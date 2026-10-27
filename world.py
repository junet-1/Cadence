# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "pillow", "mapbox-earcut"]
# ///
"""Prozedurale Welt aus einem Workout: keine Karten, keine Höhendaten, kein Netz. Deterministisch aus Workout + Seed.

Ebenen (grob -> fein), jede nur aus der vorigen:
  Workout (workout.py)  -> Bauplan: Phasen nach Zeit (Einfahren, Grundlage, Satz VO2 …)
  Regie (plan)          -> Regionen nach Strecke: Szene (Tal, See, Anstieg …) + Biom, Länge = Dauer × Referenztempo
  Straße (_road)        -> Mittellinie alle 5 m: Krümmung und Steigung als glatte Funktionen von s (zuerst!)
  Gelände (field)       -> Höhe/Farbe an jedem Punkt (Geologie in terrain.py): Talboden aus der Straßenhöhe + Gebirgsketten
                           + Täler der Szene (_macro) -> regionale Erosion (16 m, gecacht) -> Erosionsrinnen -> Schichtstufen
                           -> Wasser -> Straßenanschluss (Trasse, Einschnitt, Damm; auch zwischen Serpentinen)
  Kacheln (tile_*)      -> Meshes, Bäume, Häuser, Gras-Karten; nur rund um den Fahrer, im Hintergrund (stream)

Zeit vs. Strecke: das Workout läuft nach Zeit, die Welt liegt nach Strecke. Regionen werden mit dem Tempo eines
Referenzfahrers (3 W/kg) auf der geplanten Steigung ausgelegt; fährt jemand schneller/langsamer, kommt die Landschaft früher/
später – die Welt ändert sich dafür nie (beide Fahrer sehen dieselbe Welt, nichts wird umgebaut). Nach dem Workout geht die
Straße einfach weiter (gemischte Grundlagen-Szenen), sie endet praktisch nie.
Reproduzierbar: gleiches Workout (Segmente), gleicher Seed, gleiche Einstellungen (Biom, Dramatik, Dichte, Abwechslung) und
gleiche VERSION -> byte-gleiche Dateien. FTP/Gewicht/Grafik ändern die Welt nicht (Ausnahme: .erg-Dateien in Watt ohne FTP).

  uv run world.py stream --out DIR [--workout F] [--seed N] [--biome reise|alpen|wald|mittelmeer|wueste] …   (für main.gd)
  uv run world.py --test
  uv run world.py preview --workout F --seed N --png out.png    (Draufsicht zum Prüfen)
"""
import argparse, hashlib, json, math, queue, resource, sys, threading, time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import sparse
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

import biome
import regions
import signs
import terrain
import workout
from biome import FARM, FOREST, MEADOW, PAVING, ROCK, SAND, SCRUB, WATER
from route import MODELS, instances, save_mesh
from terrain import fbm, hash01, noise1, smoothstep

VERSION = 7
STEP = 5.0  # m zwischen Straßenpunkten (wie route.py)
START = 300.0  # m Straße hinter dem Startpunkt: kein Straßenende im Bild
SINK = 0.15  # Trasse unter der Fahrbahn (wie route.py)
BENCH = 3.5  # m ebene Trasse neben der Fahrbahnkante: ≥ Diagonale einer 2-m-Zelle, sonst schneidet ein Dreieck die Fahrbahn
R_MIN = 55.0  # m kleinster Kurvenradius außerhalb von Serpentinen
R_HAIR = (16.0, 22.0)  # m Radius der Kehren (Mittellinie)
HAIR_BETA = math.radians(12)  # Äste der Serpentinen weichen so weit von der Höhenlinie ab -> sie fächern auf, Hang zwischen ihnen ≤ ~40 %
ER_FULL, ER_END = 1400.0, 1900.0  # m Abstand zur Straße: bis hier volle Erosion, dahinter Makrorelief (Regionen nur im Korridor)
K_ERO = 0.01  # Erodierbarkeit (Stream-Power, je Erosionsschritt), × Biom
G_MAX = 0.13  # größte Steigung
NEAR, MID, FAR = (128.0, 2.0), (256.0, 8.0), (1024.0, 32.0)  # Kachelgröße, Rasterweite
NEAR_DIST, MID_DIST, FAR_R = 90.0, 500.0, 3200.0  # Abstand zur Straße: fein / mittel / Sichtweite fern
MARGIN = 48.0  # m Rand der Gras-Karten (≥ Gras-Radius in grass.gdshader, ~44 m)
AHEAD, BEHIND = 3000.0, 400.0  # m Welt vor/hinter dem Fahrer
NEAR_AHEAD = 1600.0  # m feine Kacheln (2 m, Bäume, Gras-Karten) voraus; dahinter dieselbe Fläche als mittlere Kachel
CHUNK = 50  # Straßenpunkte je Straßenstück (250 m)
REF_W, REF_KG = 240.0, 80.0  # Referenzfahrer für die Auslegung (3 W/kg), nicht der echte Fahrer

# --- Biome: alles, was eine Gegend ausmacht, als Daten. Paletten aus biome.py (gleiche Farben wie echte Strecken) ---
BIOMES = {
    "alpen": dict(name="Alpen", sun=(10.5, 46.6), elev0=900, palette=dict(lush=.8, alpine=.2), forest=.55,
                  trees=dict(fichte=.65, kiefer=.1, buche=.17, birke=.08), shrubs=.05, farm=.15, scrub=.0, sand=.0, rock=.35,
                  mount=1100, style=dict(ridged=1), treeline=420, snow=650, hw=2.7, village="alpen", asphalt=(.22, .22, .24),
                  scenes=dict(valley=2, lake=1.5, coast=0, canyon=0, open=.5), sky=dict(arid=0, fog=.8, warm=0),
                  geo=dict(K=1.0, talus=0.9, vexp=3.0, kehren=0.85)),
    "wald": dict(name="Mischwald", sun=(8.5, 50.2), elev0=250, palette=dict(lush=1), forest=.5,
                 trees=dict(buche=.3, eiche=.2, birke=.1, fichte=.25, kiefer=.15), shrubs=.05, farm=.5, scrub=.0, sand=.0, rock=.0,
                 mount=240, style=dict(soft=1), treeline=1e4, snow=6000, hw=3.0, village="mitteleuropa", asphalt=(.22, .22, .24),
                 scenes=dict(forest=1.5, rolling=1.5, coast=0, canyon=0), sky=dict(arid=0, fog=1, warm=0),
                 geo=dict(K=1.4, talus=0.5, vexp=1.4, kehren=0.0)),
    "mittelmeer": dict(name="Mittelmeer", sun=(6.8, 43.3), elev0=60, palette=dict(lush=.45, dry=.55), forest=.2,
                       trees=dict(aleppo=.3, pinie=.2, steineiche=.22, olive=.12, zypresse=.08, palme=.08), shrubs=.45, farm=.25, scrub=.35, sand=.0, rock=.2,
                       mount=420, style=dict(soft=.6, ridged=.4), treeline=1e4, snow=6000, hw=3.0, village="mittelmeer",
                       asphalt=(.26, .25, .25), scenes=dict(coast=3, canyon=0, lake=.5), sky=dict(arid=.35, fog=.9, warm=.5),
                       geo=dict(K=0.9, talus=0.75, vexp=1.8, kehren=0.35)),
    "wueste": dict(name="Wüste", sun=(7.6, 31.2), elev0=350, palette=dict(dry=1), forest=.0,
                   trees=dict(palme=.9, olive=.1), shrubs=.25, farm=.0, scrub=.25, sand=.75, rock=.5,
                   mount=380, style=dict(mesa=1), treeline=1e4, snow=6000, hw=3.3, village="trocken", asphalt=(.30, .29, .28),
                   scenes=dict(canyon=3, open=2, lake=.25, forest=0, coast=0, valley=.3), sky=dict(arid=1, fog=1.4, warm=.8),
                   geo=dict(K=0.5, talus=1.3, vexp=5.0, kehren=0.0)),
}
# geo: Morphologie (terrain.py): K Erodierbarkeit, talus Schüttwinkel (tan), vexp Talform (1,4 Kerbe/Mulde … 3 Trogtal … 5 Canyon),
# kehren = Wahrscheinlichkeit für Serpentinen in Anstiegen/Abfahrten
# trees: Artenmix (Modelle aus blender/trees.py, Namen = Instanzdateien); Olivenhaine kommen zusätzlich auf Feldern (_grove)
SPECIES = ["buche", "eiche", "birke", "fichte", "kiefer", "olive", "pinie", "aleppo", "steineiche", "zypresse", "palme"]
JOURNEY = ["wueste", "mittelmeer", "wald", "alpen"]  # Reise: Nachbarn in dieser Reihenfolge (keine Wüste neben Gletschern)
PALETTES = {"lush": biome.LUSH, "dry": biome.DRY, "alpine": biome.ALPINE}

# --- Szenen: Charakter eines Abschnitts. grade = (Mittel, Schwankung, Wellenlänge m), curve = (Krümmung 1/m, Wellenlänge m),
# valley = Talflanken (m, Breite m), cross = Quergefälle (Hang), relief = Hügel, forest = Waldfaktor, water = See/Fluss/Meer
SCENES = {
    "rolling": dict(grade=(0, .025, 900), curve=(1 / 320, 520), valley=(15, 300), cross=.04, relief=.7, forest=.6, village=.55),
    "valley": dict(grade=(.004, .01, 1300), curve=(1 / 380, 700), valley=(170, 320), cross=0, relief=.8, forest=.8, village=.45, water="river"),
    "lake": dict(grade=(0, .004, 1500), curve=(1 / 420, 800), valley=(50, 400), cross=.02, relief=.6, forest=.7, village=.35, water="lake"),
    "forest": dict(grade=(.008, .033, 700), curve=(1 / 150, 320), valley=(30, 250), cross=.07, relief=.8, forest=2.2, village=0),
    "open": dict(grade=(0, .012, 1600), curve=(1 / 900, 1300), valley=(0, 300), cross=.01, relief=.45, forest=.15, village=.3),
    "climb": dict(grade=(.065, .012, 650), curve=(1 / 170, 420), valley=(70, 350), cross=.22, relief=1.3, forest=.9, village=0, summit=True),
    "steep": dict(grade=(.09, .015, 450), curve=(1 / 125, 320), valley=(90, 300), cross=.33, relief=1.6, forest=.55, village=0, summit=True),
    "descent": dict(grade=(-.045, .012, 700), curve=(1 / 190, 420), valley=(70, 350), cross=.2, relief=1.2, forest=.8, village=0),
    "coast": dict(grade=(0, .018, 900), curve=(1 / 210, 450), valley=(20, 300), cross=.05, relief=1.0, forest=.4, village=.35, water="sea"),
    "canyon": dict(grade=(.004, .012, 1100), curve=(1 / 260, 560), valley=(140, 90), cross=0, relief=.6, forest=.1, village=0),
}
SCENE_NAMES = {"rolling": "Hügelland", "valley": "Flusstal", "lake": "Seeufer", "forest": "Waldstraße", "open": "Weite",
               "climb": "Anstieg", "steep": "Steilrampe", "descent": "Abfahrt", "coast": "Küstenstraße", "canyon": "Canyon"}
# Workout-Zone -> Szenen mit Gewicht (Richtwerte, keine Regel: Biome gewichten um, Seed wählt)
MAPPING = {
    "warmup": dict(rolling=3, open=1.5, lake=1, valley=1),
    "endurance": dict(valley=3, rolling=3, lake=2, forest=1.5, open=1, coast=1, canyon=1),
    "free": dict(valley=3, rolling=3, lake=2, forest=1.5, open=1, coast=1, canyon=1),
    "tempo": dict(forest=3, rolling=2, canyon=1, coast=1),
    "threshold": dict(climb=1),
    "vo2": dict(steep=1),
    "recovery": dict(descent=2, lake=1, valley=1),
    "cooldown": dict(open=2, lake=2, rolling=1, valley=1, coast=1),
}
IN_SET = {"tempo": "climb", "threshold": "climb", "vo2": "steep", "recovery": "descent", "endurance": "descent",
          "warmup": "descent", "cooldown": "descent", "free": "descent"}
WET = 0.25  # Uferform ab der Wasser gezeichnet wird
WATER_OFF = {"lake": -1.6, "river": -2.6, "sea": -24.0}  # Wasserspiegel unter der (geglätteten) Straße
NAMES = {  # Ortsnamen je Biom: Vorsilbe + Stamm + Endung, aus dem Seed
    "wald": (["Ober", "Unter", "Alt", "Neu", "Hohen", "Kirch", "Wald", "Nieder"], ["kurbel", "linden", "eich", "buch", "tann", "rod", "stein", "hasel"],
             ["bach", "hausen", "dorf", "heim", "rode", "au", "ingen", "feld"]),
    "alpen": (["St. ", "Hinter", "Vorder", "Ober", "Inner"], ["kurbel", "alp", "gams", "lärch", "zirben", "schnee", "fels", "brunn"],
              ["egg", "alm", "tal", "boden", "joch", "au", "berg"]),
    "mittelmeer": (["San ", "Santa ", "Porto ", "Monte ", "Villa "], ["Cur", "Ros", "Mar", "Ver", "Sol", "Oliv", "Pin", "Tor"],
                   ["ella", "ino", "etto", "ara", "ona", "ezza", "ano"]),
    "wueste": (["Ain ", "Bir ", "El ", "Ksar ", "Oued "], ["Kur", "Sab", "Tam", "Nef", "Dar", "Zag", "Mer"], ["ra", "ouk", "ane", "it", "out", "ia"]),
}


# --- Regie: Bauplan -> Regionen entlang der Strecke ---

def ref_speed(frac, grade):
    """Tempo (m/s) des Referenzfahrers bei frac × FTP auf grade (gleiche Physik wie main.gd)."""
    P = max(frac, 0.35) * REF_W
    th = math.atan(grade)
    a = REF_KG * 9.81 * (math.sin(th) + 0.004 * math.cos(th))
    c = 0.5 * 1.2 * 0.32
    lo, hi = math.sqrt(max(-a, 0) / (3 * c)), 40.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if c * mid ** 3 + a * mid < P else (lo, mid)
    return min(max(lo, 1.5), 16.5)


def plan(w, seed, theme="reise", variation=1.0, total=None):
    """Bauplan -> Regionen [{scene, biome, kind, t0, t1, s0, s1, grade, massif, side}] lückenlos ab START.
    Lange Phasen werden in Szenen von 8–18 min (÷ variation) geteilt, Sätze zu einem Gebirge aus Anstiegen und Abfahrten."""
    rng = np.random.default_rng([seed, 11])
    items = []
    bp = workout.blueprint(w) if w else []
    for ph in bp:
        if ph["kind"] == "set":
            items += [dict(kind=q["kind"], t0=q["t0"], t1=q["t1"], p=q["p"], massif=True) for q in ph["parts"]]
        else:
            dur = ph["t1"] - ph["t0"]
            n = max(1, round(dur / (rng.uniform(8, 18) * 60 / variation)))
            items += [dict(kind=ph["kind"], t0=ph["t0"] + dur * k / n, t1=ph["t0"] + dur * (k + 1) / n, p=ph["p"], massif=False)
                      for k in range(n)]
    plan_end = len(items)
    t = items[-1]["t1"] if items else 0.0
    # Nach dem Workout (oder freie Fahrt): weiter mit lockeren Szenen, bis die Strecke lang genug ist
    while len(items) < plan_end + 400:
        d = rng.uniform(10, 18) * 60 / variation
        items.append(dict(kind="cooldown" if len(items) == plan_end and items else "free", t0=t, t1=t + d, p=0.62, massif=False))
        t += d
    # Biom je Abschnitt: fest oder als Reise (Nachbarn in JOURNEY, Gebirge zieht in die Alpen, mindestens 2 Abschnitte bleiben)
    fixed = theme if theme in BIOMES else None
    idx, dwell = int(rng.integers(1, 3)), 0
    out, s, prev, gain = [], START, None, 0.0
    for n_it, it in enumerate(items):
        if fixed:
            b = fixed
        else:
            hard = it["massif"] or it["kind"] in ("threshold", "vo2")
            if hard and idx < 3 and dwell >= 1:
                idx, dwell = idx + 1, 0
            elif not hard and dwell >= 2 and rng.random() < 0.35 * variation:
                idx, dwell = int(np.clip(idx + rng.choice([-1, 1]), 0, 3)), 0
            b = JOURNEY[idx]
            dwell += 1
        if it["massif"]:
            scene = IN_SET[it["kind"]]
        else:
            opts = {k: v * BIOMES[b]["scenes"].get(k, 1.0) for k, v in MAPPING[it["kind"]].items()}
            opts = {k: v for k, v in opts.items() if v > 0 and k != prev} or {"rolling": 1}
            scene = rng.choice(list(opts), p=np.array(list(opts.values())) / sum(opts.values()))
        sc = SCENES[scene]
        g = sc["grade"][0]
        dur = it["t1"] - it["t0"]
        p = it["p"] if it["p"] > 0 else 0.65
        if scene == "descent":  # Abfahrt gibt ungefähr die Höhe des Anstiegs davor zurück
            ln = dur * ref_speed(p, -0.04)
            for _ in range(3):
                g = -float(np.clip(gain / max(ln, 1), 0.02, 0.065))
                ln = dur * ref_speed(p, g)
            gain = max(gain + g * ln, 0)
        else:
            ln = dur * ref_speed(p, g)
            gain = gain + g * ln if g > 0.03 else gain * 0.5
        out.append(dict(scene=str(scene), biome=b, kind=it["kind"], t0=it["t0"], t1=it["t1"], s0=s, s1=s + ln, grade=g,
                        massif=it["massif"], side=int(rng.choice([-1, 1])), workout=n_it < plan_end and w is not None))
        s += ln
        prev = scene
        if total and s > total:
            break
    return out


# --- Die Welt ---

class World:
    def __init__(self, w=None, seed=1, theme="reise", drama=1.0, density=1.0, variation=1.0, length=None):
        self.w, self.seed, self.theme = w, int(seed), theme
        self.drama, self.density, self.variation = float(drama), float(density), float(variation)
        dur_s = w["duration"] if w else 0.0
        plan_len = sum(r["s1"] - r["s0"] for r in plan(w, seed, theme, variation, total=None)
                       if r["workout"]) if w else 0.0
        # Strecke: Workout-Länge × 2 + 20 km (Stärkere fahren weiter, danach geht es weiter), freie Fahrt 150 km
        self.length = length or (max(2 * plan_len + 20000, 40000) if w else 150000)
        self.regions = plan(w, seed, theme, variation, total=self.length)
        self.plan_len, self.duration = plan_len, dur_s
        self._road()
        self._coarse()
        self._places()
        self.key = hashlib.sha1(json.dumps([VERSION, self.seed, theme, round(drama, 3), round(density, 3), round(variation, 3),
                                            [[s["dur"], s["p0"], s["p1"], s["free"]] for s in w["segments"]] if w else None,
                                            round(self.length)]).encode()).hexdigest()[:10]

    # Straße zuerst: Mittellinie + Höhe als glatte Funktionen von s

    def _per_sample(self, n, getter, sigma):
        a = np.zeros(n)
        for r in self.regions:
            i0, i1 = int(r["s0"] / STEP), int(r["s1"] / STEP) + 1
            a[i0:i1] = getter(r)
        a[:int(self.regions[0]["s0"] / STEP)] = getter(self.regions[0])
        last = int(self.regions[-1]["s1"] / STEP) + 1
        a[last:] = getter(self.regions[-1])
        return gaussian_filter1d(a, sigma / STEP, mode="nearest") if sigma else a

    def _road(self):
        n = int(self.length / STEP) + 1
        self.n = n
        s = np.arange(n) * STEP
        self.s = s
        sc = lambda key, k=None: (lambda r: SCENES[r["scene"]][key] if k is None else SCENES[r["scene"]][key][k])
        # Biomgewichte je Punkt (langer Übergang: Reise zwischen Gegenden, kein Umschalten)
        self.bw = np.column_stack([self._per_sample(n, lambda r, b=b: float(r["biome"] == b), 900) for b in BIOMES])
        bget = lambda f: self.bw @ np.array([f(BIOMES[b]) for b in BIOMES])
        # Horizontal wie eine trassierte Straße: Gerade -> Übergangsbogen (Klothoide, Krümmung linear) -> Kreisbogen ->
        # Übergangsbogen -> Gerade. Szene gibt Bogenradius (curve[0] = typische Krümmung) und Länge der Geraden (curve[1]);
        # Richtung jedes Bogens zieht meist zur Hauptrichtung zurück (Straße kommt nie zurück), manchmal folgt direkt ein
        # Gegenbogen (S-Kurve). Serpentinen (_hairpins) ersetzen das Programm abschnittsweise.
        curv = self._per_sample(n, sc("curve", 0), 150) * (0.7 + 0.3 * self.drama)
        cwl = self._per_sample(n, sc("curve", 1), 150)
        theta0 = hash01(1, seed=self.seed) * 2 * np.pi
        mean = theta0 + 0.6 * noise1(s / 14000.0, self.seed + 5)
        prog = self._hairpins(n)
        rng = np.random.default_rng([self.seed, 41])
        th = np.empty(n)
        th[0] = mean[0]
        k = np.empty(n)
        lim = 1 / R_MIN
        queue, qi, last = np.zeros(0), 0, 0.0

        def element(i, dev, last):
            """Gerade + Bogen ab Punkt i -> (Krümmung je Punkt, Vorzeichen des Bogens)."""
            R = max(R_MIN, 1.0 / max(curv[i], 1e-5) * rng.uniform(0.7, 2.2))
            big = R < 260 and rng.random() < 0.3  # enge Gegend: ab und zu eine richtige Kehre im Tal (bis 95°)
            phi = np.radians(rng.uniform(40, 95) if big else rng.uniform(8, 50) * (0.7 + 0.3 * self.drama))
            reverse = last != 0 and rng.random() < 0.25
            straight = rng.uniform(15, 60) if reverse else cwl[i] * rng.uniform(0.35, 1.7)
            if reverse:
                sgn = -last
            else:
                back = 0.5 + 0.45 * np.tanh(abs(dev) / 0.35)
                sgn = -np.sign(dev or 1.0) if rng.random() < back else np.sign(dev or 1.0)
            kap = sgn / R
            lt = min(np.clip(R / 6, 25, 100), phi * R)  # Übergangslänge; Drehung = |kap| · (Bogen + Übergang)
            arc = phi * R - lt
            ns, nt, na = int(straight / STEP), max(int(lt / STEP), 1), int(arc / STEP)
            ramp = (np.arange(nt) + 0.5) / nt
            return np.concatenate([np.zeros(ns), ramp * kap, np.full(na, kap), ramp[::-1] * kap]), sgn

        for i in range(n - 1):  # ponytail: Python-Schleife, ~30 ms für 150 km; vektorisieren falls je nötig
            dev = th[i] - mean[i]
            if prog[i] == prog[i]:  # Serpentine: festes Kurvenprogramm (kehrt am Ende zur Richtung vom Anfang zurück)
                k[i] = prog[i]
                queue, qi, last = np.zeros(0), 0, 0.0
            else:
                if qi >= len(queue):
                    queue, last = element(i, dev, last)
                    qi = 0
                # kleine Rückstellung gegen Drift (R ≥ ~3 km auf Geraden, unsichtbar), stark nur bei großer Abweichung
                k[i] = min(max(queue[qi] - dev / 1500.0 - dev ** 3 / 110.0, -lim), lim)
                qi += 1
            th[i + 1] = th[i] + STEP * k[i]
        k[-1] = k[-2]
        self.theta, self.kappa = th, k
        self.x = np.concatenate([[0], np.cumsum(np.cos(th[:-1]) * STEP)])
        self.z = np.concatenate([[0], np.cumsum(np.sin(th[:-1]) * STEP)])
        self.x -= self.x[int(START / STEP)]
        self.z -= self.z[int(START / STEP)]
        # Vertikal: Steigung der Szene + Wellen, begrenzt und zweimal geglättet (keine Knicke, keine Sprünge)
        gm = self._per_sample(n, lambda r: r["grade"], 60)
        gamp = self._per_sample(n, sc("grade", 1), 150) * (0.6 + 0.4 * self.drama)
        gwl = self._per_sample(n, sc("grade", 2), 150)
        g = gm + gamp * noise1(np.cumsum(STEP / gwl), self.seed + 6)
        g = gaussian_filter1d(np.clip(g, -G_MAX, G_MAX), 40 / STEP, mode="nearest")
        self.grade = g
        h = np.concatenate([[0], np.cumsum((g[:-1] + g[1:]) / 2 * STEP)])
        self.h = h - h[int(START / STEP)]
        self.hw = gaussian_filter1d(bget(lambda b: b["hw"]) * self._per_sample(n, lambda r: 0.92 if r["scene"] in ("steep", "climb") else 1.0, 0),
                                    150 / STEP, mode="nearest")
        dx, dz = np.cos(th), np.sin(th)
        self.lx, self.lz = dz, -dx  # links (wie route.py: −z = Norden)
        # Serpentinen als Fläche: gedrehtes Rechteck (Hauptachsen der Punkte + 25 m) um alle Äste eines Blocks
        self.boxes = []
        for i0, i1 in self.blocks:
            P_ = np.column_stack([self.x[i0:i1], self.z[i0:i1]])
            c = P_.mean(0)
            _, _, vt = np.linalg.svd(P_ - c, full_matrices=False)
            loc = (P_ - c) @ vt.T
            self.boxes.append((c, vt, np.abs(loc).max(0) + 25.0))
        self.tree = cKDTree(np.column_stack([self.x, self.z]))
        # Straßenanschluss: Stücke alle 10 m (Sehnenfehler in Kehren < 0,8 m, die Trasse ist 3,5 m breiter als die Fahrbahn)
        self.road10 = (self.x[::2], self.z[::2], self.h[::2], self.hw[::2])
        self.rtree = cKDTree(np.column_stack(self.road10[:2]))
        self.strike = float(hash01(2, seed=self.seed) * np.pi)  # Streichrichtung der Gebirgsketten
        self.ero = terrain.Regions(self._ero_macro)
        self.asphalt = self.bw @ np.array([BIOMES[b]["asphalt"] for b in BIOMES])
        # Höhe über dem Meer für Schilder: Startbiom + Straßenhöhe
        self.elev0 = max(BIOMES[r["biome"]]["elev0"] for r in self.regions[:12])  # Reise in die Alpen: Tal schon hoch

    def _hairpins(self, n):
        """Serpentinen: in Anstiegen/Abfahrten, wo das Biom sie hat (Alpen), ein Zickzack quer zur Falllinie: Einfahrkurve
        (R_MIN) um 90° − β, Äste abwechselnd ±(90° − β) zur Fahrtrichtung, Kehren um 180° − 2β (Radius R_HAIR), Ausfahrkurve
        zurück. Die Äste fächern auf (Abstand wächst mit der Entfernung von der Kehre wie ihr Höhenunterschied) -> der Hang
        zwischen ihnen bleibt bei ~30–40 %. Steigung bleibt die geplante (nur die Linie ändert sich). -> Krümmung je Punkt
        (NaN = normale Straße); self.blocks = [(i0, i1)]."""
        prog = np.full(n, np.nan)
        self.blocks = []
        rng = np.random.default_rng([self.seed, 31])
        for r in self.regions:
            roll, R, leg_t, side = rng.random(), rng.uniform(*R_HAIR), rng.uniform(150, 240), rng.choice([-1, 1])
            mid = min(int((r["s0"] + r["s1"]) / 2 / STEP), n - 1)
            p = self.bw[mid] @ np.array([BIOMES[b]["geo"]["kehren"] for b in BIOMES])
            if r["scene"] not in ("steep", "climb", "descent") or roll >= p * min(self.drama, 1.2):
                continue
            s0, s1 = math.ceil((r["s0"] + 60) / STEP) * STEP, min(r["s1"], self.length - 300) - 60
            turn, ent = np.pi - 2 * HAIR_BETA, R_MIN * (np.pi / 2 - HAIR_BETA)
            arc = R * turn
            avail = s1 - s0 - 2 * ent
            nl = max(2, round((avail + arc) / (leg_t + arc)))
            leg = (avail - (nl - 1) * arc) / nl
            if leg < 80:
                nl -= 1
                leg = (avail - (nl - 1) * arc) / nl
            if nl < 2 or leg < 80:
                continue
            segs = [(ent, side / R_MIN)]
            for j in range(nl):
                segs.append((leg, 0.0))
                if j < nl - 1:
                    segs.append((arc, -side * (-1) ** j / R))
            segs.append((ent, -side * (-1) ** (nl - 1) / R_MIN))
            # Richtung als stückweise lineare Funktion von s; je Punkt die mittlere Krümmung -> Gesamtdrehung exakt
            L = np.cumsum([0] + [a for a, _ in segs])
            ang = np.cumsum([0] + [a * c for a, c in segs])
            i0, i1 = int(round(s0 / STEP)), int((s0 + L[-1]) / STEP)
            u = np.arange(i0, i1 + 1) * STEP - s0
            A = np.interp(u, L, ang)
            prog[i0:i1] = np.diff(A) / STEP
            self.blocks.append((i0, i1))
        return prog

    def _coarse(self):
        """Grobe Stützpunkte alle 40 m mit geglätteter Höhe und allen Landschaftsparametern: das Gelände abseits der Straße
        mischt sie per inverser Distanz -> überall stetig, auch innen in Kurven, wo der nächste Straßenpunkt springt."""
        n = self.n
        sc = lambda key: (lambda r: SCENES[r["scene"]].get(key, 0))
        water = lambda kind: (lambda r: float(SCENES[r["scene"]].get("water") == kind))
        P = {}
        P["h"] = gaussian_filter1d(self.h, 150 / STEP, mode="nearest")  # Talboden (Kuppen der Straße kaum auf Damm)
        P["valley"] = self._per_sample(n, lambda r: sc("valley")(r)[0], 250) * self.drama
        P["vwidth"] = self._per_sample(n, lambda r: sc("valley")(r)[1], 250)
        # Hang: bergauf auf der Seite r["side"]; an Wasser umgekehrt (Ufer fällt zum Wasser hin ab)
        P["cross"] = self._per_sample(n, lambda r: sc("cross")(r) * r["side"] * (-1 if SCENES[r["scene"]].get("water") else 1), 250) * self.drama
        P["relief"] = self._per_sample(n, sc("relief"), 250)
        P["forest"] = self._per_sample(n, sc("forest"), 300)
        massif = self._per_sample(n, lambda r: 1.35 if r["massif"] or r["scene"] in ("climb", "steep", "descent") else 0.75, 600)
        for kind in WATER_OFF:  # Wasser nur im Inneren der Region (Ränder: Gelände wieder über dem Spiegel)
            m = np.zeros(n)
            for r in self.regions:
                if SCENES[r["scene"]].get("water") == kind and r["s1"] - r["s0"] > 900:
                    m[int((r["s0"] + 350) / STEP):int((r["s1"] - 350) / STEP)] = 1
            P[kind] = gaussian_filter1d(m, 150 / STEP, mode="nearest")
        P["wside"] = self._per_sample(n, lambda r: r["side"], 0)
        P["woff"] = self._per_sample(n, lambda r: WATER_OFF.get(SCENES[r["scene"]].get("water"), -3.0), 300)
        vm = np.zeros(n)
        for v in getattr(self, "villages", []):
            vm[int((v["s0"] - 60) / STEP):int((v["s1"] + 60) / STEP)] = 1
        P["village"] = gaussian_filter1d(vm, 60 / STEP, mode="nearest")
        bw = self.bw
        P["mount"] = (bw @ np.array([BIOMES[b]["mount"] for b in BIOMES])) * massif
        for key in ("forest_b", "farm", "scrub", "sand", "rock", "shrubs", "treeline"):
            P[key] = bw @ np.array([BIOMES[b][key.removesuffix("_b") if key == "forest_b" else key] for b in BIOMES])
        for st in ("soft", "ridged", "mesa"):
            P["st_" + st] = bw @ np.array([BIOMES[b]["style"].get(st, 0) for b in BIOMES])
        for pal in PALETTES:
            P["pal_" + pal] = bw @ np.array([BIOMES[b]["palette"].get(pal, 0) for b in BIOMES])
        for sp in SPECIES:
            P["tr_" + sp] = bw @ np.array([BIOMES[b]["trees"].get(sp, 0.0) for b in BIOMES])
        P["desert"] = bw[:, list(BIOMES).index("wueste")]
        for key in ("K", "talus", "vexp"):
            P[key] = bw @ np.array([BIOMES[b]["geo"][key] for b in BIOMES])
        hp = np.zeros(n)
        for i0, i1 in self.blocks:
            hp[i0:i1] = 1
        P["hp"] = gaussian_filter1d(hp, 40 / STEP, mode="nearest")  # in Serpentinen kein Quergefälle (Seite wechselt je Ast)
        # Serpentinen liegen in einer Hangebene (Ausgleichsebene durch ihre Punkte): Talboden = Ebene, params() mischt die
        # Ebenen der Nachbarpunkte mit ihrem Gefälle (sonst mittelt der Talboden die Äste weg -> Dämme und Gräben)
        self.hgrad = np.zeros((n, 2))
        for i0, i1 in self.blocks:
            j = np.arange(max(i0 - 40, 0), min(i1 + 40, n))
            X = np.column_stack([np.ones(len(j)), self.x[j] - self.x[j].mean(), self.z[j] - self.z[j].mean()])
            c = np.linalg.lstsq(X, self.h[j], rcond=None)[0]
            P["h"][j] += (X @ c - P["h"][j]) * P["hp"][j]
            self.hgrad[j] = c[1:] * P["hp"][j, None]
        self.pnames = list(P)
        sel = np.arange(0, n, 8)
        self.P = np.column_stack([P[k][sel] for k in self.pnames])
        self.ctree = cKDTree(np.column_stack([self.x[sel], self.z[sel]]))
        self.cx, self.cz, self.cgrad = self.x[sel], self.z[sel], self.hgrad[sel]
        self.Pfull = P  # je Straßenpunkt (Atmosphäre, Tests)

    def _places(self):
        """Orte (Häuser beidseits der Straße, Kirche, Ortstafeln) und Pässe (Kapelle, Passschild). Global, aus dem Seed."""
        rng = np.random.default_rng([self.seed, 21])
        self.villages, self.summits, used = [], [], set()

        def name(b):
            pre, mid, suf = NAMES[b]
            for _ in range(20):
                p_ = pre[rng.integers(len(pre))] if rng.random() < .6 else ""
                m_ = mid[rng.integers(len(mid))] + suf[rng.integers(len(suf))]
                nm = p_ + (m_.capitalize() if not p_ or p_.endswith(" ") else m_)
                if nm not in used:
                    used.add(nm)
                    return nm
            return nm
        for r in self.regions:
            sc = SCENES[r["scene"]]
            ln = r["s1"] - r["s0"]
            # Orte: je angefangene 4 km eine Chance (lange Grundlage-Abschnitte bekommen mehrere)
            for part in range(int(ln // 4000) + 1):
                a0, a1 = r["s0"] + part * 4000, min(r["s0"] + (part + 1) * 4000, r["s1"])
                if sc.get("village", 0) and a1 - a0 > 1000 and rng.random() < min(sc["village"] * 1.5 * min(self.density, 1.5), 0.9):
                    vl = float(rng.uniform(220, 480))
                    c = float(rng.uniform(a0 + 300 + vl / 2, max(a1 - 300 - vl / 2, a0 + 301 + vl / 2)))
                    self.villages.append(dict(s0=c - vl / 2, s1=c + vl / 2, name=name(r["biome"]), biome=r["biome"]))
            if sc.get("summit") and r["s1"] < self.length - 500:
                nxt = next((q for q in self.regions if q["s0"] >= r["s1"] - 1), None)
                if nxt is None or not SCENES[nxt["scene"]].get("summit"):  # Kuppe erst am Ende eines Anstiegs
                    i = int(r["s1"] / STEP)
                    self.summits.append(dict(s=r["s1"], name=name(r["biome"]), elev=int(round(self.h[i] + self.elev0, -1)), biome=r["biome"]))
        self._coarse()  # Ortsmaske ins Gelände (eben, keine Hügel zwischen den Häusern)
        # Häuser: alle 16–28 m je Seite, 7–30 m neben dem Rand, Längsseite zur Straße
        self.houses = []
        for v in self.villages:
            style = regions.STYLES[BIOMES[v["biome"]]["village"]]
            models, weights = list(style["houses"]), list(style["houses"].values())
            church = style["church"]
            for side in (1, -1):
                s = v["s0"] + rng.uniform(0, 15)
                while s < v["s1"]:
                    mdl = "kirche" if church and abs(s - (v["s0"] + v["s1"]) / 2) < 12 and side == 1 else str(rng.choice(models, p=weights))
                    L, D, _ = MODELS[mdl]
                    sc_ = rng.uniform(0.9, 1.1) * (0.8 if mdl == "kirche" else 1.0)
                    off = 7 + D * sc_ / 2 + rng.uniform(0, 12) + (6 if mdl == "kirche" else 0)
                    self.houses.append(dict(s=s, side=side, off=off, model=mdl, scale=sc_, wall=rng.integers(1 << 30), roof=rng.integers(1 << 30),
                                            var=rng.random(), flip=rng.random() < .5, style=BIOMES[v["biome"]]["village"]))
                    s += L * sc_ + rng.uniform(6, 16)
        for sm in self.summits:  # Kapelle am Pass (wo Kirchen zur Gegend passen)
            if regions.STYLES[BIOMES[sm["biome"]]["village"]]["church"]:
                self.houses.append(dict(s=sm["s"] + 25, side=1, off=28, model="kirche", scale=0.55, wall=rng.integers(1 << 30),
                                        roof=rng.integers(1 << 30), var=rng.random(), flip=False, style=BIOMES[sm["biome"]]["village"]))
        for hs in self.houses:
            i = int(np.clip(hs["s"] / STEP, 0, self.n - 1))
            hw = self.hw[i] + hs["off"]
            hs["x"], hs["z"] = self.x[i] + self.lx[i] * hw * hs["side"], self.z[i] + self.lz[i] * hw * hs["side"]
            hs["ax"], hs["az"] = math.cos(self.theta[i]), math.sin(self.theta[i])
        # Häuser, die in eine andere Stelle der Straße ragen (Kurve), weglassen
        if self.houses:
            hx = np.array([[h["x"], h["z"]] for h in self.houses])
            d, j = self.tree.query(hx)
            keep = d > self.hw[j] + np.array([MODELS[h["model"]][1] * h["scale"] / 2 + 4 for h in self.houses])
            self.houses = [h for h, k in zip(self.houses, keep) if k]
        self._deco(rng)
        # Schildergesichter: ein Atlas für die ganze Welt
        self.faces = {}
        for v in self.villages:
            self.faces.setdefault("city:" + v["name"], len(self.faces))
            self.faces.setdefault("cityend:" + v["name"], len(self.faces))
        for sm in self.summits:
            self.faces.setdefault(f"street:{sm['name']} {sm['elev']} m", len(self.faces))
        self.atlas, self.rects = signs.atlas(self.faces) if self.faces else (Image.new("RGBA", (4, 4)), np.zeros((0, 4), np.float32))

    def _deco(self, rng):
        """Rund um die Häuser: Einfriedung zur Straße (Zaun/Mauer/Hecke je Region, Lücke an der Einfahrt), Einfahrt (im Boden,
        field), Auto in der Einfahrt, Briefkasten, Büsche, Gartenbaum, regionale Kleinigkeiten; Autos am Straßenrand und eine
        Haltestelle je Ort. -> self.deco = [(Art, x, z, yaw, Skalierung)], self.drive (Punkte der Einfahrten, kd-Baum).
        Rein optisch, nie auf der Fahrbahn (alles ab Fahrbahnkante + 0,3 m)."""
        style_set = {"mitteleuropa": dict(fence=[("zaun", .45), ("hecke", .35), (None, .2)], trees=[("birke", .5), ("eiche", .35)],
                                          extra=[]),
                     "alpen": dict(fence=[("zaun_holz", .7), (None, .3)], trees=[("fichte", .45), ("birke", .3)], extra=["holzstapel"]),
                     "mittelmeer": dict(fence=[("mauer", .7), ("hecke", .1), (None, .2)], trees=[("olive", .5), ("zypresse", .3), ("palme", .2)],
                                        extra=["topf", "topf"]),
                     "trocken": dict(fence=[("mauer_lehm", .75), (None, .25)], trees=[("palme", .7), ("olive", .2)], extra=["topf"])}
        self.deco, drive = [], []
        pick = lambda opts: opts[int(np.searchsorted(np.cumsum([w for _, w in opts]) / sum(w for _, w in opts), rng.random()))][0]

        def at(s, off, side):
            """Punkt off m neben der Mittellinie bei s -> (x, z, Tangentenwinkel yaw für X entlang der Straße)."""
            f = float(np.clip(s / STEP, 0, self.n - 1.001))  # zwischen den 5-m-Punkten interpolieren (Zaunfelder sind 2 m)
            i, u = int(f), f - int(f)
            lerp = lambda a: a[i] + (a[i + 1] - a[i]) * u
            x, z = lerp(self.x) + lerp(self.lx) * off * side, lerp(self.z) + lerp(self.lz) * off * side
            th = lerp(self.theta)
            return x, z, float(np.arctan2(-math.sin(th), math.cos(th)))
        lamp = lambda s, side: abs(s - round(s / 35) * 35) < 4 and (1 if int(round(s / 35)) % 2 else -1) == side
        for hs in self.houses:
            if hs["model"] == "kirche":
                continue
            st = style_set.get(hs["style"], style_set["mitteleuropa"])
            L, D, _ = MODELS[hs["model"]]
            L, D = L * hs["scale"], D * hs["scale"]
            i = int(np.clip(hs["s"] / STEP, 0, self.n - 1))
            hw, side, front = self.hw[i], hs["side"], self.hw[i] + hs["off"] - D / 2  # Hausfront (Abstand zur Mittellinie)
            if front - hw < 5:
                continue
            dside = 1 if rng.random() < .5 else -1
            sd = hs["s"] + dside * (L / 2 + 2.2)  # Einfahrt neben dem Haus
            for k in range(int((front + D * 0.6 - hw) / 1.0)):
                drive.append(at(sd, hw + 0.3 + k, side)[:2])
            if rng.random() < .7:  # Auto in der Einfahrt, Front zur Straße oder zum Haus
                x, z, yaw = at(sd, hw + rng.uniform(4.0, max(4.5, front - hw - 1)), side)
                self.deco.append((f"auto_{rng.integers(6)}", x, z, yaw + np.pi / 2 * (1 if rng.random() < .5 else -1), 1.0))
            fence = pick(st["fence"])
            if fence:
                off = hw + 2.6
                a, b = hs["s"] - L / 2 - 4, hs["s"] + L / 2 + 4
                for s in np.arange(a, b, 2.0):
                    if abs(s + 1 - sd) < 2.6:
                        continue  # Lücke für die Einfahrt
                    x, z, yaw = at(s + 1, off, side)
                    self.deco.append((fence, x, z, yaw, 1.0))
                x, z, yaw = at(sd + 2.0 * -dside, hw + 2.2, side)
                self.deco.append(("briefkasten", x, z, yaw, 1.0))
            for _ in range(rng.integers(1, 4)):  # Büsche vor dem Haus
                x, z, _y = at(hs["s"] + rng.uniform(-L / 2, L / 2), front - rng.uniform(1.0, 2.0), side)
                self.deco.append(("busch", x, z, rng.uniform(0, 6.3), rng.uniform(0.7, 1.2)))
            if rng.random() < .6 and front - hw > 8:  # Gartenbaum im Vorgarten
                x, z, _y = at(hs["s"] - dside * rng.uniform(0, L / 2), rng.uniform(hw + 4.5, front - 3), side)
                self.deco.append((pick(st["trees"]), x, z, rng.uniform(0, 6.3), rng.uniform(0.45, 0.7)))
            for e in st["extra"]:
                if rng.random() < .6:
                    x, z, yaw = at(hs["s"] + rng.uniform(-L / 3, L / 3), front - 0.6, side)
                    self.deco.append((e, x, z, yaw, 1.0))
        # Autos am Straßenrand (halb auf dem Bankett, parallel), nicht vor Laternen und Einfahrten
        dr = np.array(drive) if drive else np.zeros((0, 2))
        dtree = cKDTree(dr) if len(dr) else None
        for v in self.villages:
            for side in (1, -1):
                s = v["s0"] + rng.uniform(10, 40)
                while s < v["s1"]:
                    i = int(np.clip(s / STEP, 0, self.n - 1))
                    x, z, yaw = at(s, self.hw[i] + 1.25, side)
                    free = dtree is None or dtree.query([x, z])[0] > 4.5
                    if rng.random() < .35 and free and not lamp(s, side):
                        self.deco.append((f"auto_{rng.integers(6)}", x, z, yaw + (np.pi if side < 0 else 0), 1.0))
                    s += rng.uniform(12, 45)
            sm = (v["s0"] + v["s1"]) / 2 + rng.uniform(-40, 40)  # Haltestelle, Rückwand zur Landschaft
            side = 1 if rng.random() < .5 else -1
            i = int(np.clip(sm / STEP, 0, self.n - 1))
            if not lamp(sm, side):
                x, z, yaw = at(sm, self.hw[i] + 2.1, side)
                self.deco.append(("haltestelle", x, z, yaw + (np.pi if side < 0 else 0), 1.0))
        if self.deco:  # nichts in eine andere Stelle der Straße (Kurven): Mitte ≥ Fahrbahnkante + 0,9 m
            dd, jj = self.tree.query(np.array([[d[1], d[2]] for d in self.deco]))
            self.deco = [d for d, ok in zip(self.deco, dd > self.hw[jj] + 0.9) if ok]
        self.drive = cKDTree(dr) if len(dr) else None

    # Gelände: Höhe, Farbe, Bewuchs an beliebigen Punkten (vektorisiert)

    def nearest(self, px, pz):
        """Nächster Punkt auf der Mittellinie -> (s, seitlicher Abstand t (links +), Straßenhöhe, halbe Breite)."""
        n = self.n
        _, i = self.tree.query(np.column_stack([px, pz]))
        best = None
        for a in (np.maximum(i - 1, 0), np.minimum(i, n - 2)):
            b = a + 1
            ex, ez = self.x[b] - self.x[a], self.z[b] - self.z[a]
            L2 = ex * ex + ez * ez + 1e-12
            u = np.clip(((px - self.x[a]) * ex + (pz - self.z[a]) * ez) / L2, 0, 1)
            d = np.hypot(px - self.x[a] - u * ex, pz - self.z[a] - u * ez)
            side = np.where((px - self.x[a]) * ez - (pz - self.z[a]) * ex >= 0, 1.0, -1.0)
            cand = (d, (a + u) * STEP, side * d, self.h[a] + u * (self.h[b] - self.h[a]), self.hw[a] + u * (self.hw[b] - self.hw[a]))
            best = cand if best is None else tuple(np.where(cand[0] < best[0], c, o) for c, o in zip(cand, best))
        return best[1], best[2], best[3], best[4]

    def params(self, px, pz):
        # Kern über (Abstand − nächster Abstand) mit Breite 60 m + 4 % des Abstands: nah an der Straße die nächsten Stützen,
        # weit weg breiter (sonst sind alle Nachbarn fast gleich weit und kleinste Abstandsunterschiede entscheiden -> Kanten).
        # 48 Nachbarn, Gewicht minus das des 49. -> wer aus der Auswahl fällt, hat schon Gewicht 0 (stetig)
        d, k = self.ctree.query(np.column_stack([px, pz]), k=49)
        # Bezug = glattes Minimum der Abstände (das harte Minimum knickt an den Voronoi-Kanten der Stützen -> Streifen)
        dref = d[:, :1] - 20.0 * np.log(np.exp(-(d - d[:, :1]) / 20.0).sum(1, keepdims=True))
        u = np.clip((d - dref) / (60.0 + 0.04 * dref), 0, 1)
        w = (1 - u * u) ** 2
        w = w[:, :48] - w[:, 48:]
        k = k[:, :48]
        w /= np.maximum(w.sum(1, keepdims=True), 1e-300)
        Wm = sparse.csr_matrix((w.ravel(), (np.repeat(np.arange(len(px)), w.shape[1]), k.ravel())), shape=(len(px), len(self.P)))
        V = Wm @ self.P  # dünn besetzt: nie n × 48 × Parameter im Speicher
        out = {name: V[:, j] for j, name in enumerate(self.pnames)}
        # glatter Straßenabstand für weite Terme: Softmin korrigiert um die Stützendichte (gerade Straße: ≈ echter Abstand)
        ds = dref[:, 0]
        out["dsm"] = ds + 20.0 * np.log(np.maximum(1, np.sqrt(2 * np.pi * np.maximum(ds, 1) * 20.0) / 40.0))
        # Talboden mit Gefälle (Serpentinen): jede Stütze sagt ihre Ebene am Punkt voraus, höchstens 150 m weit
        g, ox, oz = self.cgrad[k], px[:, None] - self.cx[k], pz[:, None] - self.cz[k]
        lim = 1 / np.sqrt(1 + (ox * ox + oz * oz) / 150 ** 2)  # glatt gesättigt
        out["h"] = out["h"] + np.einsum("nk,nk->n", w, (g[..., 0] * ox + g[..., 1] * oz) * lim)
        return out

    def _macro(self, px, pz):
        """Makrorelief vor der Erosion: Talboden (Straßenhöhe, gemittelt) + Gebirgsketten (terrain.mountains, Stil je Biom)
        + Talflanken (Form je Biom: Kerbe/Mulde, Trog, Canyon; Breite schwankt) + Hang + Hügel. Ohne Wasser, Rinnen und
        Straßenanschluss. Auch Grundlage der Erosionsregionen (dort auf 16 m)."""
        s, t, hr, hw = self.nearest(px, pz)
        P = self.params(px, pz)
        d = np.abs(t)
        sd = self.seed
        vill = P["village"] * (1 - smoothstep(80, 220, d))
        calm = 1 - 0.85 * vill
        # Wasser: Seite und Form je Art; Berge auf der Wasserseite flach
        ws = t * np.sign(P["wside"] + 1e-9)
        wn = fbm(px / 650, pz / 650, sd + 9, 3)  # Ufer mit Buchten und Landzungen statt parallel zur Straße
        open_side = 1 - np.maximum(P["lake"] * smoothstep(0, 60, ws) * (1 - smoothstep(700, 1200, ws)), P["sea"] * smoothstep(0, 60, ws))
        wx, wz = px + 90 * fbm(px / 700, pz / 700, sd + 7, 2), pz + 90 * fbm(px / 700 + 9.1, pz / 700, sd + 8, 2)  # verwirbelt
        shape = terrain.mountains(px, pz, sd + 30, self.strike, P["st_soft"], P["st_ridged"], P["st_mesa"])
        # Abstand für Talflanken/Gebirge: nah exakt, weiter weg glatt (der exakte knickt auf der Mittelachse zwischen
        # Straßenteilen, z. B. strahlenförmig zwischen Serpentinenästen); Talbreite schwankt: kein Kanal entlang der Straße
        df = d + (P["dsm"] - d) * smoothstep(10, 120, d)
        # Serpentinen: Abstand zur ganzen Serpentinen-Fläche statt zum einzelnen Ast -> innen ein durchgehender Hang (Ebene aus
        # _coarse + Wellen), die Äste werden nur als Bankett hineingeschnitten; außen Täler/Berge wie sonst. Ohne das gräbt
        # sich jeder Ast sein eigenes Tal und zwischen den Ästen bleiben Grate stehen (Waschbrett).
        q = np.clip(P["hp"], 0, 1)
        dh = d
        if self.boxes and (q > 0.01).any():
            m = q > 0.01
            db = np.full(m.sum(), np.inf)
            for c, vt, half in self.boxes:
                loc = (np.column_stack([px[m], pz[m]]) - c) @ vt.T
                db = np.minimum(db, np.hypot(*np.maximum(np.abs(loc) - half, 0).T))
            db = np.where(np.isfinite(db), db, d[m])
            df, dh = df.copy(), d.copy()
            df[m] = df[m] * (1 - q[m]) + db * q[m]
            dh[m] = d[m] * (1 - q[m]) + np.maximum(db, 70.0) * q[m]  # Hügelwellen auch innen (halbe Stärke), quer über die Äste
        dv = df * (1 + 0.3 * fbm(px / 1300, pz / 1300, sd + 33, 2))
        valley = P["valley"] * (1 - np.exp(-(dv / np.maximum(P["vwidth"], 30)) ** P["vexp"])) * calm
        # Hang quer zur Straße, klingt nach ~600 m ab (sonst springt er, wo zwei ferne Straßenteile gleich nah sind)
        cross = P["cross"] * 140 * np.tanh(t / 140) * np.exp(-(t / 600) ** 2) * calm * (1 - P["hp"])
        hills = 22 * P["relief"] * self.drama * calm * fbm(wx / 380, wz / 380, sd + 20, 4) * smoothstep(hw + 6, hw + 130, dh)
        mount = P["mount"] * self.drama * shape * smoothstep(110, 900, dv) * open_side * calm
        N = P["h"] + valley + cross + hills + mount
        return dict(N=N, s=s, t=t, hw=hw, d=d, P=P, vill=vill, ws=ws, wn=wn, mount=mount)

    def _ero_macro(self, px, pz):
        """Für terrain.Regions: Makrohöhe, Erodierbarkeit (Straßenkorridor und Orte fest: Seitenbäche münden auf Straßenhöhe,
        die Talsohle unter der Straße bleibt), Schüttwinkel."""
        M = self._macro(px, pz)
        K = K_ERO * M["P"]["K"] * smoothstep(M["hw"] + 10, M["hw"] + 45, M["d"]) * (1 - 0.9 * M["vill"])
        return M["N"], K, M["P"]["talus"]

    def field(self, px, pz, color=True, res=0.0):
        """Gelände an beliebigen Punkten. res = Rasterweite der Kachel (feine Rinnen-Oktaven darunter weglassen); Bäume und
        Häuser einer Kachel nehmen dieselbe res, damit sie auf deren Fläche stehen."""
        px, pz = np.ravel(px).astype(float), np.ravel(pz).astype(float)
        M = self._macro(px, pz)
        s, t, hw, d, P, vill, ws, wn = (M[k] for k in ("s", "t", "hw", "d", "P", "vill", "ws", "wn"))
        sd = self.seed
        # Erosion (regional, nur im Korridor um die Straße; dahinter klingt sie aus)
        fade = 1 - smoothstep(ER_FULL, ER_END, d)
        E = {k: np.zeros(len(px)) for k in terrain.Regions.FIELDS}
        act = fade > 0
        if act.any():
            for k, v in self.ero.sample(px[act], pz[act]).items():
                E[k][act] = v * fade[act]
        N = M["N"] + E["dh"] * smoothstep(hw + 8, hw + 40, d)
        if P["st_mesa"].max() > 0:  # Schichtstufen (Wüste): Bänke und Stufenkanten an den Wänden, Hochflächen bleiben glatt
            rel = N - P["h"]
            N = N + P["st_mesa"] * smoothstep(0.2, 0.5, np.hypot(E["gx"], E["gz"])) * (terrain.terrace(rel, px, pz, sd) - rel)
        # Rinnen nach den Stufen: sie schneiden durch die Bänke (vorher würden die Stufen sie zu Sägezähnen machen)
        free = smoothstep(hw + BENCH, hw + 30, d) * (1 - vill)
        N = N + terrain.gullies(px, pz, E["gx"], E["gz"], sd + 50, res) * free
        N = N + 0.8 * fbm(px / 40, pz / 40, sd + 21, 3) * smoothstep(hw + BENCH, hw + 25, d)
        Wl = P["h"] + P["woff"]
        ws_ = ws - 70 * np.maximum(wn, -0.4) * smoothstep(hw + 20, hw + 120, ws)
        reach = lambda m: np.clip(m * 1.8 - 0.35 + 0.6 * wn, 0, 1)  # Region klingt unregelmäßig aus (keine geraden Enden)
        lake = reach(P["lake"]) * smoothstep(hw + 9, hw + 45, ws_) * (1 - smoothstep(520, 820, ws_))
        sea = reach(P["sea"]) * smoothstep(hw + 30, hw + 110, ws_)
        river = P["river"] * np.exp(-((ws - hw - 34) / 11) ** 2)
        # In Wasser-Regionen liegt das Land über dem Spiegel; Wasser nur, wo die Uferform gräbt -> keine geraden Wasserkanten
        N = N + np.clip(np.maximum.reduce([P["lake"], P["sea"], P["river"]]) * 10, 0, 1) * np.maximum(0, Wl + 1.2 - N)
        for shp, depth in ((lake, 7.0), (sea, 40.0), (river, 3.5)):  # graben bis unter den Wasserspiegel, weich
            N = N - np.clip(shp * 1.6, 0, 1) * np.maximum(0, N - (Wl - depth * np.clip(shp, 0, 1)))
        # Straßenanschluss: Trasse, Einschnitt 1:1, Damm 1:1,5 von jedem Straßenstück (auch dem Ast über/unter einer Kehre)
        H, _ = terrain.conform(px, pz, N, self.rtree, *self.road10, SINK, BENCH)
        out = dict(h=H, s=s, t=t, d=d, hw=hw, wl=Wl, wm=np.maximum.reduce([lake, sea, river]), P=P,
                   slope=np.hypot(E["gx"], E["gz"]), E=E)
        if not color:
            return out
        # Bodenklassen: Wald als große Flecken (Dichte aus Biom × Szene × Einstellung), offenes Land Wiese/Feld/Busch/Sand
        dens = np.clip(P["forest_b"] * P["forest"] * self.density, 0, 1)
        f = fbm(px / 420, pz / 420, sd + 40, 4) + 0.35 * fbm(px / 90, pz / 90, sd + 41, 2)
        above = H - (P["h"] + P["treeline"])  # über der Baumgrenze (Alpen)
        forest = (f > 0.55 - 1.25 * dens) & (above < 0) & (d > hw + 6) & (vill < 0.5)
        cls = np.full(len(px), MEADOW, np.uint8)
        cls[(fbm(px / 380, pz / 380, sd + 42, 3) > 0.45 - 1.2 * P["farm"]) & (d > hw + 4)] = FARM
        cls[fbm(px / 260, pz / 260, sd + 43, 3) > 0.55 - 1.3 * P["scrub"]] = SCRUB
        cls[fbm(px / 260, pz / 260, sd + 44, 3) > 0.6 - 1.6 * P["sand"]] = SAND
        # Fels: steil (Erosionsraster), konvexe Grate, hoch über der Baumgrenze; Mittelgebirge bleibt meist bewachsen.
        # Schutt: mittelsteile, hohle Hänge unter Felsen im Gebirge. Nässe/Sediment aus dem Abfluss der Erosion.
        sl, curv = out["slope"], E["curv"]
        rockx = (np.clip(smoothstep(0.55, 0.95, sl) + 0.6 * smoothstep(0.002, 0.012, -curv) * smoothstep(0.25, 0.5, sl)
                        + smoothstep(60, 220, above) + P["st_mesa"] * smoothstep(0.3, 0.6, sl), 0, 1)
                 * (1 - 0.6 * P["st_soft"]) * (1 - vill) * (d > hw + 3))  # Wüste: jede Wand ist Sandstein
        scree = (smoothstep(0.3, 0.55, sl) * (1 - smoothstep(0.8, 1.0, sl)) * smoothstep(0.0, 0.006, curv)
                 * np.clip(P["st_ridged"] + 0.5 * P["st_mesa"] + 0.3 * P["rock"], 0, 1) * (1 - vill) * (d > hw + 3))
        wet = smoothstep(np.log10(16 * 16 * 40), np.log10(16 * 16 * 4000), np.where(E["flow"] > 0, E["flow"], 0)) * (d > hw + 4)
        sed = smoothstep(0.3, 3.0, E["sed"])
        rock = (rockx > 0.55) | (above > 160) | ((P["st_mesa"] > .5) & (M["mount"] > 0.45 * P["mount"]))
        cls[rock] = ROCK
        cls[forest] = FOREST
        under = (H < Wl - 0.15) & (out["wm"] > WET)
        cls[under] = WATER
        pal = sum(P["pal_" + k][:, None] * PALETTES[k][cls] for k in PALETTES)
        alp = smoothstep(-60, 60, above)[:, None]
        rgb = pal * (1 - alp) + biome.ALPINE[cls] * alp
        red = (cls == ROCK) * P["desert"]  # Wüstenfels rötlich (Sandstein) statt Granitgrau
        rgb = rgb * (1 - red[:, None]) + np.array([.64, .43, .31]) * red[:, None]
        grow = biome.GROW[cls].astype(np.float64)
        dry = P["pal_dry"]
        grassy = np.isin(cls, biome.GRASSY)
        grow = np.where(grassy & (dry > .25), 215, grow)
        grow = np.where(grassy & (dry > .4), 200, grow)
        grow = np.where(grassy & (dry > .6), 160, grow)
        rgb = rgb + (np.array([.47, .45, .42]) - rgb) * (0.75 * scree * (cls != FOREST))[:, None]  # Geröll grau
        grove = self._grove(px, pz, P, cls) & (d > hw + 4)
        rgb[grove] = rgb[grove] * 0.35 + np.array([.62, .42, .30]) * 0.65  # Olivenhain: rote Erde (terra rossa)
        grow = np.where(scree > 0.5, np.minimum(grow, 160), grow)
        grow = np.where(grove, 200, grow)  # zwischen den Oliven nur wenig Gras
        if self.drive is not None:  # Einfahrten: Pflaster/Kies, kein Gras
            dd, _ = self.drive.query(np.column_stack([px, pz]), distance_upper_bound=2.0)
            dw = 1 - smoothstep(1.2, 1.6, dd)
            rgb = rgb + (np.array([.42, .40, .37]) - rgb) * (0.9 * dw)[:, None]
            grow = np.where(dw > 0.5, biome.GROW[PAVING], grow)
        grow = np.where(d < hw + 2.0, biome.GROW[PAVING], grow)  # Bankett: kein Gras auf/an der Fahrbahn
        out.update(cls=cls, rgb=np.clip(rgb, 0, 1), grow=grow, forest=forest, above=above, vill=vill,
                   mat=np.column_stack([wet, sed, rockx, scree]))
        return out

    # Kacheln

    def items(self, s_rider):
        """Alles, was um s_rider geladen sein soll -> {key: Priorität}. Nur aus der Straßengeometrie, also deterministisch."""
        lo, hi = max(s_rider - BEHIND, 0), min(s_rider + AHEAD, self.length)
        i = np.arange(int(lo / STEP), int(hi / STEP) + 1, 6)
        px, pz = self.x[i], self.z[i]
        out = {}
        for k in range(int(lo / STEP / CHUNK), min(int(hi / STEP / CHUNK) + 1, (self.n - 1) // CHUNK + 1)):
            out[f"r{k}"] = max(k * CHUNK * STEP - s_rider, 0) * 0.5
        B, R = FAR[0], FAR_R
        cand = set()
        for x0, z0 in zip(px[::4], pz[::4]):
            for I in range(int(math.floor((x0 - R) / B)), int(math.floor((x0 + R) / B)) + 1):
                for J in range(int(math.floor((z0 - R) / B)), int(math.floor((z0 + R) / B)) + 1):
                    cand.add((I, J))
        cand = sorted(cand)
        c = np.array([((I + .5) * B, (J + .5) * B) for I, J in cand])
        dr = self._dist_window(c, px, pz)
        _, j = self.tree.query(c)
        for (I, J), cc, dw, jj in zip(cand, c, dr, j):
            if dw - B * 0.7072 > R:
                continue
            ds = jj * STEP - s_rider
            # fein/mittel nur nah an der Straße und nicht weit vor/hinter dem Fahrer; sonst ein grober Block
            if self.rect_dist(cc, B / 2, MID_DIST) >= MID_DIST or not -BEHIND - 700 < ds < AHEAD + 300:
                out[f"f{I}_{J}"] = (abs(ds) if ds > 0 else 3 * -ds) + 800
                continue
            for mi in range(4):
                for mj in range(4):
                    self._mid(I * 4 + mi, J * 4 + mj, s_rider, out)
        return out

    def _dist_window(self, c, px, pz):
        return cKDTree(np.column_stack([px, pz])).query(c)[0]

    def rect_dist(self, c, half, limit):
        """Abstand Quadrat (Mitte c, halbe Kante) <-> Straßenpunkte, exakt bis limit (darüber: limit)."""
        idx = self.tree.query_ball_point(c, half * 1.4143 + limit)
        if not idx:
            return limit
        idx = np.array(idx)
        dx = np.maximum(np.abs(self.x[idx] - c[0]) - half, 0)
        dz = np.maximum(np.abs(self.z[idx] - c[1]) - half, 0)
        return float(np.hypot(dx, dz).min())

    def _mid(self, i, j, s_rider, out):
        T = MID[0]
        c = np.array([(i + .5) * T, (j + .5) * T])
        _, jj = self.tree.query(c)
        ds = jj * STEP - s_rider
        pr = abs(ds) if ds > 0 else 3 * -ds
        if self.rect_dist(c, T / 2, NEAR_DIST) >= NEAR_DIST or not -BEHIND - 200 < ds < NEAR_AHEAD:
            out[f"m{i}_{j}"] = pr + 250
            return
        for a in range(2):
            for b in range(2):
                ni, nj = i * 2 + a, j * 2 + b
                cc = np.array([[(ni + .5) * NEAR[0], (nj + .5) * NEAR[0]]])
                d2, j2 = self.tree.query(cc)
                ds2 = j2[0] * STEP - s_rider
                out[f"n{ni}_{nj}"] = abs(ds2) if ds2 > 0 else 3 * -ds2

    def build(self, key, out):
        """Ein Element (Straßenstück r…, Kachel n/m/f…) als Dateien nach out -> Meldung für main.gd (dict)."""
        if key[0] == "r":
            return self.road_chunk(int(key[1:]), out)
        lod = {"n": NEAR, "m": MID, "f": FAR}[key[0]]
        i, j = map(int, key[1:].split("_"))
        return self.tile(key, i, j, lod, out)

    def tile(self, key, i, j, lod, out):
        size, res = lod
        n = int(size / res)
        pad = int(MARGIN / res) if lod is NEAR else 1  # feine Kacheln: Gras-Karten bis MARGIN über den Rand
        x0, z0 = i * size, j * size
        ax = x0 + (np.arange(-pad, n + pad + 1)) * res
        az = z0 + (np.arange(-pad, n + pad + 1)) * res
        GX, GZ = np.meshgrid(ax, az)
        F = self.field(GX, GZ, res=res)
        m = len(ax)
        Hg = F["h"].reshape(m, m)
        # Normalen aus zentralen Differenzen des (größeren) Rasters: an Kachelgrenzen identisch zum Nachbarn
        nx = -(Hg[1:-1, 2:] - Hg[1:-1, :-2]) / (2 * res)
        nz = -(Hg[2:, 1:-1] - Hg[:-2, 1:-1]) / (2 * res)
        nrm = np.stack([nx, np.ones_like(nx), nz], -1)
        nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
        sl = slice(pad, pad + n + 1)
        sl1 = slice(pad - 1, pad + n)
        h = Hg[sl, sl]
        nr = nrm[sl1, sl1]
        rgb = F["rgb"].reshape(m, m, 3)[sl, sl]
        grow = F["grow"].reshape(m, m)[sl, sl]
        X, Z = GX[sl, sl], GZ[sl, sl]
        pos = np.stack([X, h, Z], -1).reshape(-1, 3)
        col = np.concatenate([rgb.reshape(-1, 3), (grow.reshape(-1, 1) / 255.0)], 1)
        mat = F["mat"].reshape(m, m, 4)[sl, sl].reshape(-1, 4)  # UV = Nässe, Sediment; UV2 = Fels, Schutt (terrain.gdshader)
        nrm_ = nr.reshape(-1, 3)
        q = (np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]).ravel()
        idx = np.column_stack([q, q + n + 1, q + 1, q + 1, q + n + 1, q + n + 2]).reshape(-1, 3)
        # Schürzen an allen Kanten: verdecken Ritzen zu Nachbarn mit anderer Auflösung
        drop = {NEAR: 1.5, MID: 5.0, FAR: 40.0}[lod]
        P_, N_, C_, I_, M_ = [pos], [nrm_], [col], [idx], [mat]
        o = len(pos)
        for e in ([k for k in range(n + 1)], [k * (n + 1) + n for k in range(n + 1)], [n * (n + 1) + k for k in range(n + 1)][::-1],
                  [k * (n + 1) for k in range(n + 1)][::-1]):
            e = np.array(e)
            top = pos[e]
            P_.append(top - [0, drop, 0]); N_.append(nrm_[e]); C_.append(col[e]); M_.append(mat[e])
            a, b = e[:-1], e[1:]
            c, dd = o + np.arange(len(e) - 1), o + np.arange(1, len(e))
            I_.append(np.column_stack([a, c, b, b, c, dd]).reshape(-1, 3))
            o += len(e)
        M_ = np.vstack(M_)
        save_mesh(out, key, np.vstack(P_), np.vstack(N_), np.vstack(I_), col=np.vstack(C_), uv=M_[:, :2], uv2=M_[:, 2:])
        msg = {"tile": key, "lod": "near" if lod is NEAR else "mid" if lod is MID else "far"}
        # Wasser: Fläche auf Spiegelhöhe, nur Dreiecke wo Gelände darunter liegt
        wl, wm = F["wl"].reshape(m, m)[sl, sl].ravel(), F["wm"].reshape(m, m)[sl, sl].ravel()
        wet = (pos[:, 1] < wl) & (wm > WET)
        tri = wet[idx].any(1) & (wm[idx] > WET * 0.5).all(1)
        if tri.any():
            wp = np.column_stack([pos[:, 0], wl, pos[:, 2]])
            used, inv = np.unique(idx[tri], return_inverse=True)
            # UV.x = Wassertiefe (Spiegel − Gelände; < 0 am Ufer): water.gdshader färbt danach (türkis -> tiefblau, Gischt)
            save_mesh(out, key + ".water", wp[used], np.tile([0, 1, 0], (len(used), 1)), inv.reshape(-1, 3),
                      col=np.tile([.25, .32, .35, 0.5], (len(used), 1)), uv=np.column_stack([wl[used] - pos[used, 1], np.zeros(len(used))]))
            msg["water"] = True
        if lod is FAR:
            return msg
        # Gras-Karten (nur fein): Bodenfarbe + Wuchscode, Höhen; Rand MARGIN -> Gras um den Fahrer liegt ganz darin
        if lod is NEAR:
            rgba = np.concatenate([F["rgb"].reshape(m, m, 3), F["grow"].reshape(m, m, 1) / 255.0], -1)
            # sRGB: Bodentextur hat source_color, Farben hier sind sRGB wie biome.ground_rgba
            Image.fromarray(np.clip(rgba * 255 + 0.5, 0, 255).astype(np.uint8)).save(out / f"{key}.g.png")
            Hg.astype(np.float32).tofile(out / f"{key}.h.f32")
            msg["grass"] = [float(ax[0]), float(az[0]), m * res, m * res, m, m, res]
        self._plants(key, x0, z0, size, lod, out, msg)
        self._houses(key, x0, z0, size, out, msg, res)
        self._rocks(key, x0, z0, size, lod, out, msg, res)
        return msg

    def _grove(self, px, pz, P, cls):
        """Olivenhain: Felder dort, wo Oliven zur Gegend gehören (Mittelmeer), in großen Flecken -> Reihen statt Acker."""
        return (cls == FARM) & (P["tr_olive"] > 0.05) & (fbm(px / 260, pz / 260, self.seed + 46, 2) > 0.35 - 3 * P["tr_olive"])

    def _plants(self, key, x0, z0, size, lod, out, msg):
        sp = 7.0 if lod is NEAR else 13.0
        # Zelle gehört der Kachel, in der ihre Ecke liegt -> lückenlos, keine doppelten Bäume an Kachelgrenzen
        ci, cj = np.meshgrid(np.arange(math.ceil(x0 / sp), math.ceil((x0 + size) / sp)), np.arange(math.ceil(z0 / sp), math.ceil((z0 + size) / sp)))
        ci, cj = ci.ravel(), cj.ravel()
        sd = self.seed + (0 if lod is NEAR else 500)
        px = (ci + hash01(ci, cj, seed=sd + 1)) * sp
        pz = (cj + hash01(ci, cj, seed=sd + 2)) * sp
        F = self.field(px, pz, res=NEAR[1] if lod is NEAR else MID[1])
        P, cls = F["P"], F["cls"]
        r = hash01(ci, cj, seed=sd + 3)
        dens = np.clip(self.density, 0, 2)
        oasis = P["desert"] * np.clip(F["wm"] * 4, 0, 1) * (F["d"] < 260)
        p_tree = np.where(F["forest"], 0.85, np.where(cls == MEADOW, 0.035, np.where(cls == FARM, 0.012, 0.0))) * min(dens, 1.2)
        p_tree = np.maximum(p_tree, 0.55 * oasis * (cls != WATER))
        # steil (dort malt terrain.gdshader Fels, Normale < ~0,82), Fels oder Geröll: keine Bäume
        steep = (F["slope"] > 0.65) | (F["mat"][:, 2] > 0.4) | (F["mat"][:, 3] > 0.5)
        ok = ~steep & (F["d"] > F["hw"] + 5) & (cls != WATER) & (cls != ROCK) & ((F["h"] > F["wl"] + 0.4) | (F["wm"] < WET * 0.5)) & (F["vill"] < 0.3)
        grove = ok & self._grove(px, pz, P, cls)
        tree = ok & ~grove & (r < p_tree)
        # Art je Baum nach dem Mix der Gegend (Oase: Dattelpalmen)
        w = np.column_stack([P["tr_" + s] + (oasis if s == "palme" else 0) for s in SPECIES])
        cum = np.cumsum(w / np.maximum(w.sum(1, keepdims=True), 1e-9), 1)
        species = np.minimum((hash01(ci, cj, seed=sd + 4)[:, None] > cum).sum(1), len(SPECIES) - 1)
        scale = (0.7 + 0.7 * hash01(ci, cj, seed=sd + 5)) * (1.0 if lod is NEAR else 1.15)
        y = F["h"] - 0.25
        # Hain: Bäume in Reihen auf der Zellmitte (Abstand sp), Höhe dort neu
        g = grove & (r < 0.88 * min(dens, 1.2))
        gx, gz = (ci[g] + 0.5) * sp, (cj[g] + 0.5) * sp
        gy = self.field(gx, gz, color=False, res=NEAR[1] if lod is NEAR else MID[1])["h"] - 0.25 if g.any() else gx
        gs = 0.75 + 0.3 * hash01(ci[g], cj[g], seed=sd + 8)
        for n, s in enumerate(SPECIES):
            sel = tree & (species == n)
            X, Y, Z, S = px[sel], y[sel], pz[sel], scale[sel]
            if s == "olive":
                X, Y, Z, S = np.r_[X, gx], np.r_[Y, gy], np.r_[Z, gz], np.r_[S, gs]
            if len(X):
                instances(out / f"{key}.{s}.bin", X, Y, Z, S)
                msg.setdefault("plants", []).append(s)
        p_sh = np.where(cls == SCRUB, 0.5, np.where(cls == SAND, 0.06, np.where(cls == MEADOW, 0.02, 0.0))) * P["shrubs"] * 2 * dens
        sh = ok & ~tree & ~grove & (hash01(ci, cj, seed=sd + 6) < p_sh)
        if sh.any():
            instances(out / f"{key}.busch.bin", px[sh], y[sh], pz[sh], 0.5 + 0.8 * hash01(ci[sh], cj[sh], seed=sd + 7))
            msg.setdefault("plants", []).append("busch")

    def _rocks(self, key, x0, z0, size, lod, out, msg, res):
        """Felsen dort, wo das Gelände Fels/Geröll ist (Materialmasken aus field): große Felsnasen in Steilwänden, Blöcke am
        Hang, kleines Geröll auf Schutthalden, vereinzelte Findlinge in Wald und Wiese. Art nach Biom-Mix (Alpen Granit,
        Mittelmeer Kalk, Wüste Sandstein, Mittelgebirge bemooste Findlinge). Mittlere Kacheln nur große Felsen.
        Zelle gehört der Kachel, in der ihre Ecke liegt (wie _plants) -> keine doppelten Felsen an Kachelgrenzen."""
        sp = 8.0 if lod is NEAR else 16.0
        ci, cj = np.meshgrid(np.arange(math.ceil(x0 / sp), math.ceil((x0 + size) / sp)), np.arange(math.ceil(z0 / sp), math.ceil((z0 + size) / sp)))
        ci, cj = ci.ravel(), cj.ravel()
        sd = self.seed + (700 if lod is NEAR else 900)
        px = (ci + hash01(ci, cj, seed=sd + 1)) * sp
        pz = (cj + hash01(ci, cj, seed=sd + 2)) * sp
        F = self.field(px, pz, res=res)
        P, cls, sl = F["P"], F["cls"], np.minimum(F["slope"], 1.5)
        rockx, scree = F["mat"][:, 2], F["mat"][:, 3]
        r = hash01(ci, cj, seed=sd + 3)
        k = hash01(ci, cj, seed=sd + 4)
        big = rockx > 0.6
        scale = np.where(big, 2.5 + 5.0 * k, np.where(scree > 0.5, 0.3 + 0.6 * k, 0.8 + 1.4 * k))
        boulder = 0.012 * np.isin(cls, [FOREST, MEADOW, FARM]) * np.clip(P["st_soft"] + P["st_ridged"], 0, 1)
        p = np.clip(0.45 * rockx ** 2 + 0.35 * scree + boulder, 0, 0.7) * (1.0 if lod is NEAR else 2.0)
        if lod is not NEAR:
            scale = np.where(scale >= 2.0, scale, 0)
        ok = ((r < p) & (scale > 0) & (F["d"] > F["hw"] + 4 + scale) & (F["vill"] < 0.3) & (cls != WATER)
              & ((F["h"] > F["wl"] + 0.3) | (F["wm"] < WET * 0.5)))
        if not ok.any():
            return
        # Art je Fels nach Biom-Gewichten (Übergänge mischen sich)
        w = np.column_stack([P["st_ridged"] + 0.2 * P["rock"] * (1 - P["desert"]), P["pal_dry"] * (1 - P["desert"]) * (1 - P["st_ridged"]),
                             P["desert"] + P["st_mesa"] * 0.5, P["st_soft"] * (1 - P["pal_dry"])]) + 1e-6
        cum = np.cumsum(w / w.sum(1, keepdims=True), 1)
        kind = (hash01(ci, cj, seed=sd + 5)[:, None] > cum).sum(1).clip(0, 3)
        var = (hash01(ci, cj, seed=sd + 6) * 3).astype(int).clip(0, 2)
        names = ["granit", "kalk", "sand", "moos"]
        yaw = hash01(ci, cj, seed=sd + 7) * 2 * np.pi
        stretch = [0.8 + 0.5 * hash01(ci, cj, seed=sd + 8 + q) for q in range(3)]
        y = F["h"] - scale * (0.25 + 0.6 * sl / 1.5)  # im Hang versenkt: keine schwebende Unterkante
        rows = {}
        for i in np.flatnonzero(ok):
            s = scale[i]
            rows.setdefault(f"fels_{names[kind[i]]}_{var[i]}", []).append(
                signs._yaw_rows(px[i], y[i], pz[i], yaw[i], s * stretch[0][i], s * stretch[1][i], s * stretch[2][i]))
        for kd, rr in rows.items():
            np.array(rr, np.float32).tofile(out / f"{key}.deco_{kd}.bin")
        msg.setdefault("deco", []).extend(sorted(rows))

    def _houses(self, key, x0, z0, size, out, msg, res):
        mine = [h for h in self.houses if x0 <= h["x"] < x0 + size and z0 <= h["z"] < z0 + size]
        rows = {}
        for hs in mine:
            L, D, _ = MODELS[hs["model"]]
            sx = sz = hs["scale"]
            ax, az = hs["ax"], hs["az"]
            if hs["flip"]:
                ax, az = -ax, -az
            cx = np.array([-.5, .5, .5, -.5]) * L * sx
            cz = np.array([-.5, -.5, .5, .5]) * D * sz
            fx = hs["x"] + cx * ax - cz * az
            fz = hs["z"] + cx * az + cz * ax
            F = self.field(fx, fz, color=False, res=res)
            if ((F["h"] < F["wl"] + 0.5) & (F["wm"] > WET * 0.5)).any():
                continue  # nicht ins Wasser bauen (Ufer)
            oy = float(F["h"].max()) - 0.15
            style = regions.STYLES[hs["style"]]
            wall = style["walls"][hs["wall"] % len(style["walls"])]
            roof = style["roofs"][hs["roof"] % len(style["roofs"])]
            lin = lambda c: [v ** 2.2 for v in c]
            rows.setdefault(hs["model"], []).append([ax * sx, 0, -az * sz, hs["x"], 0, sx, 0, oy, az * sx, 0, ax * sz, hs["z"],
                                                     *lin(wall), hs["var"], *lin(roof), 0])
        for mdl, r in rows.items():
            np.array(r, np.float32).tofile(out / f"{key}.house_{mdl}.bin")
        if rows:
            msg["houses"] = sorted(rows)
        mine = [d for d in self.deco if x0 <= d[1] < x0 + size and z0 <= d[2] < z0 + size]
        if mine:
            h = self.field(np.array([d[1] for d in mine]), np.array([d[2] for d in mine]), color=False, res=res)["h"]
            kinds = {}
            for d, y in zip(mine, h):
                kinds.setdefault(d[0], []).append(signs._yaw_rows(d[1], y - 0.04, d[2], d[3], d[4], d[4], d[4]))
            for kind, r in kinds.items():
                np.array(r, np.float32).tofile(out / f"{key}.deco_{kind}.bin")
            msg.setdefault("deco", []).extend(sorted(kinds))

    def road_chunk(self, k, out):
        a, b = k * CHUNK, min((k + 1) * CHUNK, self.n - 1)
        sel = np.arange(a, b + 1)
        nr = len(sel)
        x, z, h, hw = self.x[sel], self.z[sel], self.h[sel], self.hw[sel]
        lx, lz = self.lx[sel], self.lz[sel]
        sh = hw + 0.35
        off = np.column_stack([sh, hw, -hw, -sh])
        yo = np.column_stack([np.full(nr, -.2), np.zeros(nr) + .05, np.zeros(nr) + .05, np.full(nr, -.2)])  # Kante unter die Trasse
        pos = np.stack([np.column_stack([x + lx * off[:, c], h + yo[:, c], z + lz * off[:, c]]) for c in range(4)], 1).reshape(-1, 3)
        # Normale analytisch je Punkt (gleich an der Grenze zum nächsten Stück -> keine Lichtkante)
        g = self.grade[sel]
        tan = np.column_stack([np.cos(self.theta[sel]), g, np.sin(self.theta[sel])])
        left = np.column_stack([lx, np.zeros(nr), lz])
        nrm = np.cross(left, tan)
        nrm *= np.sign(nrm[:, 1:2])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.repeat(nrm, 4, axis=0)
        q = (np.arange(nr - 1)[:, None] * 4 + np.arange(3)[None, :]).ravel()
        idx = np.column_stack([q, q + 4, q + 1, q + 1, q + 4, q + 5]).reshape(-1, 3)
        col = np.zeros((nr, 4, 4))
        col[:, 1:3, :3] = self.asphalt[sel][:, None]
        col[:, 1:3, 3] = np.where(hw >= 2.85, 1.0, 1 / 3)[:, None]  # breite Straßen Mittel- und Randlinien, schmale nur Mitte
        F = self.field(np.r_[x + lx * sh, x - lx * sh], np.r_[z + lz * sh, z - lz * sh])
        col[:, 0, :3], col[:, 3, :3] = F["rgb"][:nr], F["rgb"][nr:]
        uv = np.column_stack([-off.ravel(), np.repeat(sel * STEP, 4)])
        uv2 = np.repeat(np.column_stack([hw, np.full(nr, -1.0)]), 4, axis=0)
        save_mesh(out, f"r{k}", pos, nrm, idx, col=col.reshape(-1, 4), uv=uv, uv2=uv2)
        msg = {"road": f"r{k}"}
        self._furniture(k, a, b, out, msg)
        return msg

    def _furniture(self, k, a, b, out, msg):
        """Leitpfosten (außerorts alle 50 m beidseits), Laternen im Ort, Ortstafeln, Passschilder – für dieses Straßenstück."""
        s0, s1 = a * STEP, b * STEP
        inside = lambda s: any(v["s0"] - 30 <= s <= v["s1"] + 30 for v in self.villages)
        posts, delin, lamps, plates = [], [], [], []

        def at(s, off):
            i = int(np.clip(round(s / STEP), 0, self.n - 1))
            return self.x[i] + self.lx[i] * off, self.h[i] - SINK, self.z[i] + self.lz[i] * off, i

        def sign(s, key, w, hh, bottom):
            x, y, z, i = at(s, -(self.hw[int(s / STEP)] + 1.4))
            yaw = signs._facing((math.cos(self.theta[i]), math.sin(self.theta[i])))
            plates.append(signs._yaw_rows(x, y + bottom + hh / 2, z, yaw, w, hh) + [self.faces[key], 0, 0, 0])
            posts.append(signs._yaw_rows(x, y - 0.3, z, 0.0, 1, bottom + hh + 0.3, 1))
        for s in np.arange(math.ceil(s0 / 50) * 50, s1, 50.0):
            if not inside(s):
                for sd in (1, -1):
                    x, y, z, i = at(s, sd * (self.hw[int(s / STEP)] + 0.9))
                    delin.append(signs._yaw_rows(x, y, z, float(np.arctan2(math.cos(self.theta[i]), math.sin(self.theta[i]))), 1, 1, 1))
        for v in self.villages:
            for s in np.arange(math.ceil((v["s0"]) / 35) * 35, v["s1"], 35.0):
                if s0 <= s < s1:
                    sd = 1 if int(s / 35) % 2 else -1
                    x, y, z, _ = at(s, sd * (self.hw[int(s / STEP)] + 1.2))
                    lamps.append([1, 0, 0, x, 0, 1, 0, y + SINK, 0, 0, 1, z])
            for s, key in ((v["s0"] - 20, "city:"), (v["s1"] + 20, "cityend:")):
                if s0 <= s < s1:
                    sign(s, key + v["name"], 1.4, 0.7, 1.25)
        for sm in self.summits:
            if s0 <= sm["s"] + 8 < s1:
                sign(sm["s"] + 8, f"street:{sm['name']} {sm['elev']} m", 2.4, 0.6, 1.6)
        for name, arr, w in (("posts", posts, 12), ("delineators", delin, 12), ("lamps", lamps, 12)):
            if arr:
                np.array(arr, np.float32).tofile(out / f"r{k}.{name}.bin")
                msg.setdefault("props", []).append(name)
        if plates:
            pl = np.array(plates, np.float32)
            pl[:, 12:16] = self.rects[pl[:, 12].astype(int)]
            pl.tofile(out / f"r{k}.plates.bin")
            msg.setdefault("props", []).append("plates")

    # Welt-Kopf: Strecke, Regionen, Atmosphäre, Workout, Schilder-Atlas

    def write_meta(self, out):
        self.atlas.save(out / "signs.png")
        np.column_stack([self.x, self.h, self.z]).astype(np.float32).tofile(out / "route.pos")
        sel = np.arange(0, self.n, 50)  # Atmosphäre alle 250 m, main.gd interpoliert nach der Fahrerposition
        sky = lambda k: self.bw[sel] @ np.array([BIOMES[b]["sky"][k] for b in BIOMES])
        snow = self.Pfull["h"][sel] + self.bw[sel] @ np.array([BIOMES[b]["snow"] for b in BIOMES])
        start = self.regions[0]["biome"]
        meta = {"name": (self.w["name"] if self.w else "Freie Fahrt") + " · " + ("Reise" if self.theme not in BIOMES else BIOMES[self.theme]["name"]),
                "world": self.key, "version": VERSION, "seed": self.seed, "loop": False, "length_m": float(self.length),
                "start": START, "sun": list(BIOMES[start]["sun"]), "elev0": self.elev0, "procedural": True,
                "atmo": {"step": 250.0, "arid": sky("arid").round(3).tolist(), "fog": sky("fog").round(3).tolist(),
                         "warm": sky("warm").round(3).tolist(), "snow": snow.round(1).tolist()},
                "regions": [{**{k: r[k] for k in ("scene", "biome", "kind", "t0", "t1", "s0", "s1", "massif")},
                             "label": f"{SCENE_NAMES[r['scene']]} · {BIOMES[r['biome']]['name']}"} for r in self.regions],
                "villages": [{k: v[k] for k in ("s0", "s1", "name")} for v in self.villages],
                "summits": self.summits, "plan_len": self.plan_len,
                "workout": self.w}
        (out / "meta.json").write_text(json.dumps(meta))
        return meta


# --- Stream: main.gd schickt die Fahrerposition (eine Zahl je Zeile auf stdin), wir antworten mit JSON-Zeilen auf stdout ---

_W = None  # Welt im Hilfsprozess


def _worker(args):
    global _W
    _W = World(*args)
    import os
    parent = os.getppid()

    def watch():  # Elternprozess hart beendet (kill -9) -> nicht als Waise weiterlaufen
        while os.getppid() == parent:
            time.sleep(2)
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def _region_job(key):
    return _W.ero.compute(*key)


def prefetch(world, pool, s):
    """Erosionsregionen für das Fenster um s in Hilfsprozessen vorausrechnen (nächste zuerst). Das Ergebnis ist eine
    reine Funktion der Region -> gleiche Bytes wie ohne Hilfsprozesse."""
    lo, hi = max(s - BEHIND, 0), min(s + AHEAD, world.length)
    i = np.arange(int(lo / STEP), int(hi / STEP) + 1, 20)
    need = world.ero.keys_near(world.x[i], world.z[i], ER_END)
    ix, iz = world.x[int(min(s, world.length) / STEP)], world.z[int(min(s, world.length) / STEP)]
    S = terrain.Regions.SIZE
    for key, f in list(world.ero.pending.items()):  # fertige übernehmen (LRU begrenzt den Speicher, auch für ungenutzte)
        if f.done():
            world.ero.region(*key)
    for key in sorted(need, key=lambda k: math.hypot((k[0] + .5) * S - ix, (k[1] + .5) * S - iz)):
        if key not in world.ero.cache and key not in world.ero.pending:
            world.ero.pending[key] = pool.submit(_region_job, key)


def stream(world, out, args=None):
    out.mkdir(parents=True, exist_ok=True)
    say = lambda **kw: print(json.dumps(kw), flush=True)
    t0 = time.time()
    world.write_meta(out)
    say(meta=str(out / "meta.json"), ms=round((time.time() - t0) * 1000))
    inbox = queue.Queue()

    def reader():
        for line in sys.stdin:
            inbox.put(line.strip())
        inbox.put(None)  # Spiel weg -> beenden
    threading.Thread(target=reader, daemon=True).start()
    s, sent, ready, want, last = START, set(), False, {}, None
    pool, first = None, {}
    if args is not None:  # Erosionsregionen parallel (eigene Prozesse: kein GIL, kein geteilter Zustand)
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        n = max(1, min(8, (mp.cpu_count() or 2) - 2))
        pool = ProcessPoolExecutor(n, mp_context=mp.get_context("spawn"), initializer=_worker, initargs=(args,))
    while True:
        try:
            msg = inbox.get(timeout=0.02 if want else 0.5)
            while True:
                if msg is None:
                    return
                if msg:
                    s = float(msg)
                msg = inbox.get_nowait()
        except queue.Empty:
            pass
        if last is None or abs(s - last) > 25:
            last = s
            want = world.items(s)
            now = time.time()
            first = {k: first.get(k, now) for k in want}
            if pool:
                prefetch(world, pool, s)
            for k in sorted(sent - set(want)):
                sent.discard(k)
                say(drop=k)
        todo = sorted((p, k) for k, p in want.items() if k not in sent)
        if not todo:
            want = {}
            continue
        key = todo[0][1]
        t1 = time.time()
        msg = world.build(key, out)
        msg["ms"] = round((time.time() - t1) * 1000, 1)
        # Messwerte für die Diagnose in main.gd: Wartezeit seit Anforderung, offene Elemente, Erosion, Speicher
        msg["lag"] = round((time.time() - first.get(key, t1)) * 1000)
        msg["todo"] = len(todo) - 1
        msg["ero"] = [world.ero.made, len(world.ero.pending), round(world.ero.ms / max(world.ero.made, 1)), len(world.ero.cache)]
        msg["rss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
        sent.add(key)
        say(**msg)
        if not ready and all(p > 900 for p, k in todo[1:] if k not in sent):
            ready = True
            say(ready=True, ms=round((time.time() - t0) * 1000))


def world_args(a):
    w = workout.load(a.workout, a.ftp) if a.workout else None
    theme = {"reise": "reise", "alpen": "alpen", "wald": "wald", "mittelmeer": "mittelmeer", "wueste": "wueste"}.get(a.biome, "reise")
    return (w, a.seed, theme, a.drama, a.density, a.variation)


def from_args(a):
    return World(*world_args(a))


# --- Selbsttest ---

def test():
    w = workout.load(Path(__file__).parent / "workouts" / "vo2_5x3.zwo")
    A = World(w, 7, "reise")
    # Regie: lückenlos, Sätze als Anstieg/Abfahrt, Workout-Strecke plausibel (58 min ~ 25–40 km)
    for r0, r1 in zip(A.regions, A.regions[1:]):
        assert abs(r0["s1"] - r1["s0"]) < 1e-6
    sc = [r["scene"] for r in A.regions if r["massif"]]
    assert sc[:4] == ["steep", "descent", "steep", "descent"], sc
    assert 20000 < A.plan_len < 40000, A.plan_len
    # Straße: Radius, Steigung, Steigungsänderung, nie nah an sich selbst (Gelände braucht eindeutige Nähe)
    inb = np.zeros(A.n, bool)
    blk = np.full(A.n, -1)
    for b, (i0, i1) in enumerate(A.blocks):
        inb[i0:i1], blk[i0:i1] = True, b
    assert A.blocks and np.abs(A.kappa[inb]).max() > 1 / R_HAIR[1] - 1e-3, "Serpentinen in den alpinen Rampen"
    assert np.abs(A.kappa[~inb]).max() <= 1 / R_MIN + 1e-9 and np.abs(A.kappa).max() <= 1 / R_HAIR[0] + 1e-9
    g10 = np.diff(A.h[::2]) / 10
    assert np.abs(g10).max() <= G_MAX + 0.005, np.abs(g10).max()
    assert np.abs(np.diff(g10)).max() < 0.012, np.abs(np.diff(g10)).max()  # Kuppen/Wannen weich
    pairs = cKDTree(np.column_stack([A.x, A.z])).query_pairs(250.0, output_type="ndarray")
    other = (blk[pairs[:, 0]] != blk[pairs[:, 1]]) | (blk[pairs[:, 0]] < 0)  # Äste derselben Serpentine dürfen sich nahe sein
    assert (np.abs(pairs[other, 0] - pairs[other, 1]) * STEP < 1200).all(), "Straße kommt sich selbst zu nahe"
    dev = np.abs(np.degrees(A.theta - (A.theta[0] + 0.6 * noise1(A.s / 14000.0, A.seed + 5))))
    assert dev[~inb].max() < 80, dev[~inb].max()
    # Gelände: nirgends über der Fahrbahn (Mitte, Kanten, Bankett), nirgends schwebt sie (auch in Kehren), Wasser darunter
    i = np.arange(0, int(32000 / STEP), 3)
    for o in (-1.0, -0.5, 0, 0.5, 1.0):
        F = A.field(A.x[i] + A.lx[i] * o * (A.hw[i] + 0.35), A.z[i] + A.lz[i] * o * (A.hw[i] + 0.35), color=False)
        assert (F["h"] <= A.h[i] - SINK + 0.03).all(), (F["h"] - A.h[i]).max()  # Fahrbahn liegt bei h + 0,05
        # ponytail: Innenkante enger Kehren bis 0,3 m Spalt (die ebene Trasse eines 6 m entfernten, tieferen Kurventeils
        # reicht darunter); schmalere Trasse dort, falls es im Bild auffällt
        tol = np.where(np.abs(A.kappa[i]) > 1 / R_MIN + 1e-6, 0.3, 0.1)
        assert (F["h"] >= A.h[i] - SINK - tol).all(), (A.h[i] - F["h"] - tol).max()
        assert ((F["wl"] < A.h[i] - 1.0) | (F["wm"] < WET)).all()
    # Deko an Häusern: Einfriedung, Autos, Einfahrten; nichts auf einer Fahrbahn
    kinds = {d[0] for d in A.deco}
    assert kinds & {"zaun", "hecke", "zaun_holz", "mauer"} and any(k.startswith("auto_") for k in kinds) and A.drive is not None
    dd, jj = A.tree.query(np.array([[d[1], d[2]] for d in A.deco]))
    assert (dd > A.hw[jj] + 0.9).all()
    # Wasser kommt in Seen vor, Dörfer und Pässe gibt es
    assert A.summits and any(SCENES[r["scene"]].get("water") for r in A.regions)
    # Determinismus: gleiche Eingaben -> gleiche Bytes; anderer Seed -> andere Straße
    import tempfile
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        d1, d2 = Path(d1), Path(d2)
        B = World(w, 7, "reise")
        assert A.key == B.key
        s0 = START + 2000
        keys = sorted(A.items(s0), key=A.items(s0).get)
        near = [k for k in keys if k[0] == "n"][:3] + [k for k in keys if k[0] == "m"][:2] + [k for k in keys if k[0] == "f"][:1] + ["r8"]
        for k in near:
            A.build(k, d1)
            B.build(k, d2)
        A.write_meta(d1); B.write_meta(d2)
        for f in sorted(d1.iterdir()):
            assert f.read_bytes() == (d2 / f.name).read_bytes(), f.name
        C = World(w, 8, "reise")
        assert C.key != A.key and np.hypot(C.x[2000] - A.x[2000], C.z[2000] - A.z[2000]) > 50
        # Kachelgrenzen: Nachbarkacheln haben an der gemeinsamen Kante exakt gleiche Höhen und Normalen
        k0 = next(k for k in near if k[0] == "n")
        i0, j0 = map(int, k0[1:].split("_"))
        A.build(f"n{i0 + 1}_{j0}", d1)
        pa = np.fromfile(d1 / f"{k0}.pos", np.float32).reshape(-1, 3)[:65 * 65]
        pb = np.fromfile(d1 / f"n{i0 + 1}_{j0}.pos", np.float32).reshape(-1, 3)[:65 * 65]
        na = np.fromfile(d1 / f"{k0}.nrm", np.float32).reshape(-1, 3)[:65 * 65]
        nb = np.fromfile(d1 / f"n{i0 + 1}_{j0}.nrm", np.float32).reshape(-1, 3)[:65 * 65]
        ea, eb = np.arange(65) * 65 + 64, np.arange(65) * 65
        assert np.array_equal(pa[ea], pb[eb]) and np.array_equal(na[ea], nb[eb])
        # Straßenstücke: gemeinsame Querreihe identisch (keine Naht)
        A.build("r9", d1)
        ra = np.fromfile(d1 / "r8.pos", np.float32).reshape(-1, 3)[-4:]
        rb = np.fromfile(d1 / "r9.pos", np.float32).reshape(-1, 3)[:4]
        assert np.array_equal(ra, rb)
        # Feine Kachel: Gelände stetig (keine Stufen > 1,5 m zwischen 2-m-Nachbarn abseits steiler Flanken)
        p = pa.reshape(65, 65, 3)
        assert np.abs(np.diff(p[:, :, 1], axis=0)).max() < 4 and np.abs(np.diff(p[:, :, 1], axis=1)).max() < 4
    # Stream-Fenster: Pause (gleiches s), Sprung zurück auf den Start, schnelles Fahren -> gleiche Elemente je s
    assert A.items(5000.0) == A.items(5000.0)
    w0, w1 = set(A.items(START)), set(A.items(START + 1500))
    assert "r0" in w0 and "r0" not in w1 and any(k[0] == "n" for k in w1)
    # Szenario A: Grundlage 60 min -> keine Anstiegs-Szenen im Workout, sanfte Steigungen
    E = World(workout.load(Path(__file__).parent / "workouts" / "grundlage_60.zwo"), 3, "wald")
    wr = [r for r in E.regions if r["workout"]]
    assert not any(r["scene"] in ("climb", "steep") for r in wr) and len({r["scene"] for r in wr}) >= 3
    i = np.arange(int(START / STEP), int((START + E.plan_len) / STEP))
    assert np.percentile(np.abs(E.grade[i]), 95) < 0.05, np.percentile(np.abs(E.grade[i]), 95)
    # Szenario C: gemischt -> mehrere Regionen/Biome der Reise, Anstieg für Schwelle
    M = World(workout.load(Path(__file__).parent / "workouts" / "gemischt_45.zwo"), 5, "reise")
    mr = [r for r in M.regions if r["workout"]]
    assert any(r["scene"] == "climb" for r in mr) and any(r["scene"] == "steep" for r in mr)
    # Freie Fahrt: lang, ohne Workout
    Fr = World(None, 1, "mittelmeer")
    assert Fr.length >= 150000 and Fr.regions[-1]["s1"] >= Fr.length - 1
    print("ok")


def preview_png(world, path, s0=START, s1=None, px=2.0):
    """Draufsicht (Schummerung + Bodenfarbe) zum schnellen Prüfen ohne Godot."""
    s1 = s1 or min(s0 + 6000, world.length)
    i = np.arange(int(s0 / STEP), int(s1 / STEP))
    x0, x1 = world.x[i].min() - 600, world.x[i].max() + 600
    z0, z1 = world.z[i].min() - 600, world.z[i].max() + 600
    W, H = int((x1 - x0) / px), int((z1 - z0) / px)
    img = np.zeros((H, W, 3))
    for r0 in range(0, H, 256):
        GX, GZ = np.meshgrid(x0 + np.arange(W) * px, z0 + (np.arange(r0, min(r0 + 256, H))) * px)
        F = world.field(GX, GZ)
        h = F["h"].reshape(GX.shape)
        sh = np.clip(0.75 + (np.gradient(h, axis=1) - np.gradient(h, axis=0)) / px * 0.6, 0.3, 1.3)
        c = F["rgb"].reshape(*GX.shape, 3) * sh[..., None]
        wet = (F["h"] < F["wl"]) & (F["wm"] > WET)
        c.reshape(-1, 3)[wet] = (0.15, 0.3, 0.5)
        img[r0:r0 + 256] = c
    rr = ((world.z[i] - z0) / px).astype(int), ((world.x[i] - x0) / px).astype(int)
    img[np.clip(rr[0], 0, H - 1), np.clip(rr[1], 0, W - 1)] = (0.1, 0.1, 0.1)
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(path)


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", choices=["stream", "preview", "info"])
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--workout")
    ap.add_argument("--ftp", type=float, default=None, help="nur für .erg-Dateien ohne FTP im Kopf")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--biome", default="reise")
    ap.add_argument("--drama", type=float, default=1.0)
    ap.add_argument("--density", type=float, default=1.0)
    ap.add_argument("--variation", type=float, default=1.0)
    ap.add_argument("--png")
    ap.add_argument("--s0", type=float, default=START)
    a = ap.parse_args()
    if a.test:
        test()
    elif a.cmd == "stream":
        stream(from_args(a), Path(a.out), world_args(a))
    elif a.cmd == "preview":
        wd = from_args(a)
        preview_png(wd, a.png or "world.png", a.s0)
        print(f"{wd.key}: {len(wd.regions)} Regionen, Workout {wd.plan_len / 1000:.1f} km, Strecke {wd.length / 1000:.0f} km")
    elif a.cmd == "info":
        wd = from_args(a)
        for r in wd.regions[:40]:
            print(f"{r['s0'] / 1000:6.2f}–{r['s1'] / 1000:6.2f} km  {r['t0'] / 60:5.1f}–{r['t1'] / 60:5.1f} min  {r['kind']:9s} {r['scene']:8s} {r['biome']}")
        print(f"Workout {wd.plan_len / 1000:.1f} km, Strecke {wd.length / 1000:.0f} km, {len(wd.villages)} Orte, {len(wd.summits)} Pässe, Welt {wd.key}")
    else:
        ap.print_help()
