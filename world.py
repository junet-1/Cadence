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
from scipy.ndimage import gaussian_filter1d, maximum_filter1d, minimum_filter1d
from scipy.spatial import cKDTree

import biome
import regions
import signs
import terrain
import workout
from biome import FARM, FOREST, MEADOW, PAVING, ROCK, SAND, SCRUB, WATER
from route import MODELS, instances, save_mesh
from terrain import fbm, hash01, noise1, smoothstep

VERSION = 13
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
                  mount=1100, style=dict(ridged=1), treeline=420, snow=650, cap=140, hw=2.7, village="alpen", asphalt=(.22, .22, .24),
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
    "hills": dict(grade=(0, .11, 800), curve=(1 / 250, 450), valley=(20, 280), cross=.06, relief=1.0, forest=.7, village=.3),
    "gentle": dict(grade=(.03, .008, 900), curve=(1 / 260, 520), valley=(60, 350), cross=.12, relief=1.1, forest=.8, village=.15, summit=True),
}
SCENE_NAMES = {"rolling": "Hügelland", "valley": "Flusstal", "lake": "Seeufer", "forest": "Waldstraße", "open": "Weite",
               "climb": "Anstieg", "steep": "Steilrampe", "descent": "Abfahrt", "coast": "Küstenstraße", "canyon": "Canyon",
               "hills": "Auf und ab", "gentle": "Sanfter Anstieg"}
# Geländeprofil (Startbildschirm): "auto" = Szenen aus dem Workout (Intervalle werden Rampen). Sonst bestimmt das Profil die
# Steigung und das Workout nur die Zeit: ERG hält die Zielwatt ohnehin, SIM gibt genau diese Steigungen an den Trainer.
# Anstieg/Panorama/leicht: Anteil der Zeit bergauf (Rest: Abfahrt); ohne Workout in Runden von CYCLE Sekunden.
PROFILES = {"auto": "Wie das Workout", "flach": "Flach", "wellig": "Wellig (auf und ab)", "leicht": "Leichter Anstieg",
            "anstieg": "Anstieg", "panorama": "Panorama (hoch und wieder runter)"}
UPHILL = {"leicht": ("gentle", 1.0), "anstieg": ("climb", 1.0), "panorama": ("climb", 0.55)}
FLAT = dict(open=2, valley=2, lake=1.5, coast=1, canyon=.7)
CYCLE = 3600.0
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


def _pscene(profile, it, wdur):
    """Szene aus dem Geländeprofil für einen Abschnitt (None = wie das Workout, "FLAT" = flache Szene nach Biom)."""
    if profile == "flach":
        return "FLAT"
    if profile == "wellig":
        return "hills"
    if profile not in UPHILL:
        return None
    up, share = UPHILL[profile]
    mid = (it["t0"] + it["t1"]) / 2
    if wdur > 0 and mid >= wdur:  # nach dem Workout: erst runter (im Workout war es nur bergauf), dann wie freie Fahrt
        return "descent" if it.get("first_after") else None
    tf = mid / wdur if wdur > 0 else (mid % CYCLE) / CYCLE
    return up if tf < (share if wdur > 0 else min(share, 0.6)) else "descent"


def plan(w, seed, theme="reise", variation=1.0, total=None, profile="auto"):
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
        items.append(dict(kind="cooldown" if len(items) == plan_end and items else "free", t0=t, t1=t + d, p=0.62, massif=False,
                          first_after=len(items) == plan_end))
        t += d
    wdur = items[plan_end - 1]["t1"] if w and plan_end else 0.0
    ps = [_pscene(profile, it, wdur) for it in items]
    # Biom je Abschnitt: fest oder als Reise (Nachbarn in JOURNEY, Gebirge zieht in die Alpen, mindestens 2 Abschnitte bleiben)
    fixed = theme if theme in BIOMES else None
    idx, dwell = int(rng.integers(1, 3)), 0
    out, s, prev, gain = [], START, None, 0.0
    for n_it, it in enumerate(items):
        if fixed:
            b = fixed
        else:
            hard = ps[n_it] in ("climb", "steep", "gentle") if profile != "auto" else it["massif"] or it["kind"] in ("threshold", "vo2")
            if hard and idx < 3 and dwell >= 1:
                idx, dwell = idx + 1, 0
            elif not hard and dwell >= 2 and rng.random() < 0.35 * variation:
                idx, dwell = int(np.clip(idx + rng.choice([-1, 1]), 0, 3)), 0
            b = JOURNEY[idx]
            dwell += 1
        if ps[n_it] == "FLAT":
            opts = {k: v * BIOMES[b]["scenes"].get(k, 1.0) for k, v in FLAT.items()}
            opts = {k: v for k, v in opts.items() if v > 0 and k != prev} or {"open": 1}
            scene = rng.choice(list(opts), p=np.array(list(opts.values())) / sum(opts.values()))
        elif ps[n_it]:
            scene = ps[n_it]
        elif it["massif"] and profile == "auto":
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
            run = dur  # Profil: Höhe über die ganze Abfahrt verteilen (mehrere Abschnitte hintereinander), nicht im ersten
            if profile != "auto":
                for j in range(n_it + 1, len(items)):
                    if ps[j] != "descent":
                        break
                    run += items[j]["t1"] - items[j]["t0"]
            ln = dur * ref_speed(p, -0.04)
            for _ in range(3):
                g = -float(np.clip(gain * dur / run / max(ln, 1), 0.02, 0.065))
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
    def __init__(self, w=None, seed=1, theme="reise", drama=1.0, density=1.0, variation=1.0, profile="auto", length=None):
        self.w, self.seed, self.theme = w, int(seed), theme
        self.profile = profile if profile in PROFILES else "auto"
        self.drama, self.density, self.variation = float(drama), float(density), float(variation)
        dur_s = w["duration"] if w else 0.0
        plan_len = sum(r["s1"] - r["s0"] for r in plan(w, seed, theme, variation, total=None, profile=self.profile)
                       if r["workout"]) if w else 0.0
        # Strecke: Workout-Länge × 2 + 20 km (Stärkere fahren weiter, danach geht es weiter), freie Fahrt 150 km
        self.length = length or (max(2 * plan_len + 20000, 40000) if w else 150000)
        self.regions = plan(w, seed, theme, variation, total=self.length, profile=self.profile)
        self.plan_len, self.duration = plan_len, dur_s
        self._road()
        self._coarse()
        self._places()
        self._sides()
        self.key = hashlib.sha1(json.dumps([VERSION, self.seed, theme, self.profile, round(drama, 3), round(density, 3), round(variation, 3),
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
        # Quergefälle: in Kurven zur Innenseite (κ > 0 = Rechtskurve -> linke Kante höher), |κ|·2,5 (R 55 m -> 4,5 %), höchstens
        # 4 % (tiefe Kante bleibt 8 cm über der Trasse), über ~20 m ein- und ausgerampt. Nur Optik: route.pos (Mittellinie) und
        # damit Steigung/Trainer unverändert. Kantenhöhe: edge_y.
        self.bank = gaussian_filter1d(np.clip(self.kappa * 2.5, -0.04, 0.04), 20 / STEP, mode="nearest")
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

    def edge_y(self, i, side, off):
        """Höhe der Fahrbahnoberfläche off m neben der Mittellinie bei Punkt i auf Seite side (+1 = links, +lx), mit Quergefälle."""
        return self.h[i] + 0.05 + self.bank[i] * off * side

    def _hairpins(self, n):
        """Serpentinen: in Anstiegen/Abfahrten, wo das Biom sie hat (Alpen), ein Zickzack quer zur Falllinie: Einfahrkurve
        (R_MIN) um 90° − β, Äste abwechselnd ±(90° − β) zur Fahrtrichtung, Kehren um 180° − 2β (Radius R_HAIR), Ausfahrkurve
        zurück. Die Äste fächern auf (Abstand wächst mit der Entfernung von der Kehre wie ihr Höhenunterschied) -> der Hang
        zwischen ihnen bleibt bei ~30–40 %. Steigung bleibt die geplante (nur die Linie ändert sich). -> Krümmung je Punkt
        (NaN = normale Straße); self.blocks = [(i0, i1)]."""
        prog = np.full(n, np.nan)
        self.blocks = []
        rng = np.random.default_rng([self.seed, 31])
        for ri, r in enumerate(self.regions):
            roll, R, leg_t, side = rng.random(), rng.uniform(*R_HAIR), rng.uniform(150, 240), rng.choice([-1, 1])
            mid = min(int((r["s0"] + r["s1"]) / 2 / STEP), n - 1)
            p = self.bw[mid] @ np.array([BIOMES[b]["geo"]["kehren"] for b in BIOMES])
            if r["scene"] not in ("steep", "climb", "descent") or roll >= p * min(self.drama, 1.2) or abs(r["grade"]) < 0.045:
                continue
            # Gipfel frei von Serpentinen (deren Fläche ist ein ebener Hang -> oben ein Plateau ohne Aussicht): Kehren enden 450 m
            # vor dem Ende eines Anstiegs und beginnen 450 m nach dem Anfang einer Abfahrt; das letzte Stück läuft über den Grat
            summit = lambda k: 0 <= k < len(self.regions) and SCENES[self.regions[k]["scene"]].get("summit")

            def big(k):  # Anstieg, der bei k endet, ist lang (> 2,5 km am Stück): oben Aussicht statt Kehren
                k0 = k
                while summit(k0 - 1):
                    k0 -= 1
                return summit(k) and self.regions[k]["s1"] - self.regions[k0]["s0"] > 2500
            top_gap = 450 if big(ri) and not summit(ri + 1) else 60
            s0 = math.ceil((r["s0"] + (450 if r["scene"] == "descent" and big(ri - 1) else 60)) / STEP) * STEP
            s1 = min(r["s1"], self.length - 300) - top_gap
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
            # Unregelmäßig wie gebaut: Astlängen ×0,6–1,4 (Summe gleich), je Ast eigener Winkel zur Höhenlinie (±5°, Äste fächern
            # weiter auf: Öffnung an der Kehre ≥ 14°), je Kehre eigener Radius, Äste mit leichtem S-Schwung (netto 0).
            # Ein-/Ausfahrt und Kehren heben die Winkel genau auf -> danach dieselbe Richtung wie vorher.
            wl = rng.uniform(0.6, 1.4, nl)
            a = (np.pi / 2 - HAIR_BETA) + np.radians(rng.uniform(-3, 3, nl))  # Abweichung jedes Astes von der Fahrtrichtung
            Rs = rng.uniform(*R_HAIR, nl - 1)
            arcs = [Rs[j] * (a[j] + a[j + 1]) for j in range(nl - 1)]
            ent0, ent1 = R_MIN * a[0], R_MIN * a[-1]
            legs = wl / wl.sum() * (s1 - s0 - ent0 - ent1 - sum(arcs))  # Äste nehmen den Rest: Gesamtlänge exakt
            if legs.min() < 60:
                continue
            segs = [(ent0, side / R_MIN)]
            for j in range(nl):
                k = rng.uniform(-1, 1) / 900.0  # S-Schwung: erste Hälfte +k, zweite −k (stärker rückt Äste zu nah zusammen)
                segs += [(legs[j] / 2, k), (legs[j] / 2, -k)]
                if j < nl - 1:
                    segs.append((arcs[j], -side * (-1) ** j / Rs[j]))
            segs.append((ent1, -side * (-1) ** (nl - 1) / R_MIN))
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
        # Anstiege (> ~150 m in ±8 km) führen AUF einen Berg, nicht durch ein Tal zwischen höheren: cw = Anstiegsgewicht
        # (Talseite offen, _macro), pk = Gipfelnähe (oberste 60 % des Anstiegs): Berge/Talflanken ringsum kleiner, das Gelände
        # fällt von der Straße weg (Grat/Kuppe mit Rundblick). Nur Gelände – das Höhenprofil der Straße bleibt.
        top, low = maximum_filter1d(self.h, 3201, mode="nearest"), minimum_filter1d(self.h, 3201, mode="nearest")
        gain = top - low
        cw = gaussian_filter1d(smoothstep(120, 220, gain), 300 / STEP, mode="nearest")
        pk = gaussian_filter1d(cw * smoothstep(top - 0.6 * gain, top - 0.05 * gain, self.h), 150 / STEP, mode="nearest")
        P["cw"], P["pk"], P["gain"] = cw, pk, gain * cw
        massif = massif * (1 - 0.85 * pk)
        P["valley"] = P["valley"] * (1 - pk)
        P["forest"] = P["forest"] * (1 - 0.8 * pk)  # oben Almwiese statt Wald: der Blick bleibt frei
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
        # Serpentinen liegen auf einem Hang: Ausgleichsebene durch ihre Punkte + Abweichung der Äste davon (glatt zwischen den
        # Ästen gemischt) -> jeder Ast liegt auf dem Hang, nur das Bankett wird eingeschnitten. Als eigene Fläche je Block
        # (_floor), mit geometrischem Gewicht um das Rechteck; früher sagte jede Stütze ihre Ebene bis 150 m weit voraus und
        # params() mischte die Vorhersagen -> Knicklinien strahlenförmig von den Serpentinen (Talboden bis 147 % steil).
        self.hblocks = []
        hs = gaussian_filter1d(self.h, 20 / STEP, mode="nearest")
        for (i0, i1), (c0, vt, half) in zip(self.blocks, self.boxes):
            j = np.arange(max(i0 - 40, 0), min(i1 + 40, n))
            X = np.column_stack([np.ones(len(j)), self.x[j] - c0[0], self.z[j] - c0[1]])
            c = np.linalg.lstsq(X, self.h[j], rcond=None)[0]
            P["h"][j] += (hs[j] - P["h"][j]) * P["hp"][j]  # Stützen im Block: eigene Höhe (passt zur Blockfläche)
            self.hblocks.append((c, cKDTree(np.column_stack([self.x[j], self.z[j]])), self.h[j] - X @ c))
        self._drains(P)
        self.pnames = list(P)
        sel = np.arange(0, n, 8)
        self.P = np.column_stack([P[k][sel] for k in self.pnames])
        self.ctree = cKDTree(np.column_stack([self.x[sel], self.z[sel]]))
        self.Pfull = P  # je Straßenpunkt (Atmosphäre, Tests)

    def _drains(self, P):
        """Seitentäler an Senken der Straße: wo der geglättete Talboden ein Tiefpunkt mit ≥ 10 m Gegenanstieg zu beiden Seiten
        ist, wäre er rundum von Talflanken umschlossen (geschlossene Senke: die Erosion füllte sie früher zu einer ebenen
        Fläche mit Wänden). Ein Tal quer zur Straße zur Talseite hin (2,5 % Gefälle) gibt dem Wasser einen Ausgang, wie ein
        Bach unter der Straße durch. Endet vor anderen Straßenteilen. -> self.drains = [(x0, z0, ex, ez, Länge, h0)]"""
        self.drains = []
        n, h = self.n, P["h"]
        win = int(1500 / STEP)
        lo = minimum_filter1d(h, 2 * win + 1, mode="nearest")
        wet = np.maximum.reduce([P["lake"], P["sea"], P["river"]])
        cand = np.flatnonzero((h <= lo + 1e-9) & (np.arange(n) * STEP > START + 200) & (np.arange(n) * STEP < self.length - 2000))
        last = -1e9
        for i in cand:
            if i * STEP - last < 1000 or wet[i] > 0.05 or P["village"][i] > 0.3:
                continue
            if min(h[max(i - win, 0):i].max(), h[i:i + win].max()) - h[i] < 10:
                continue  # Straße fällt nach einer Seite weiter ab: Wasser fließt entlang der Straße
            side = -np.sign(P["cross"][i]) if abs(P["cross"][i]) > 0.01 else (1.0 if hash01(i, seed=self.seed + 77) < .5 else -1.0)
            ex, ez = self.lx[i] * side, self.lz[i] * side
            u = np.arange(40, 3500, 20.0)
            dd, jj = self.tree.query(np.column_stack([self.x[i] + ex * u, self.z[i] + ez * u]))
            hit = (dd < 120) & (np.abs(jj - i) * STEP > 400)
            L = float(u[np.argmax(hit)] - 120 if hit.any() else u[-1])
            if L >= 300:
                self.drains.append((self.x[i], self.z[i], ex, ez, L, float(h[i]) - 1.5))
                last = i * STEP

    def _carve(self, px, pz, N):
        """Seitentäler (_drains) ins Makrorelief: das Relief über der Talsohle (fällt mit 2,5 %) wird zur Talachse hin
        gestaucht (Gauß quer, Breite wächst talabwärts, Achse mäandert) -> Talform aus dem vorhandenen Gelände, Grate werden
        zu Scharten; keine ebenen Flanken. Zum Talschluss und jenseits der Straße klingt es aus."""
        for n_, (x0, z0, ex, ez, L, h0) in enumerate(self.drains):
            if (px.max() < min(x0, x0 + ex * L) - 1400 or px.min() > max(x0, x0 + ex * L) + 1400
                    or pz.max() < min(z0, z0 + ez * L) - 1400 or pz.min() > max(z0, z0 + ez * L) + 1400):
                continue
            u = (px - x0) * ex + (pz - z0) * ez
            v = (px - x0) * -ez + (pz - z0) * ex
            m = (u > -100) & (u < L + 400) & (np.abs(v) < 1400)
            if not m.any():
                continue
            um, vm = u[m], v[m]
            vm = vm + 90 * smoothstep(0, 300, um) * fbm(um / 600, n_ * 3.7, self.seed + 79, 2)  # Mäander, an der Straße gerade
            w = 110 + 0.05 * np.maximum(um, 0)
            g = 0.97 * np.exp(-(vm / w) ** 2) * (1 - smoothstep(L - 300, L + 300, um)) * smoothstep(-80, -10, um)
            floor = h0 - 0.025 * np.maximum(um, 0)
            N[m] = N[m] - np.maximum(N[m] - floor, 0) * g
        return N

    def _floor(self, px, pz, Ph):
        """Talboden: Straßenhöhe (params) und in Serpentinen die Hangfläche ihres Blocks (Ebene, im Rechteck festgehalten ->
        außen keine Fortsetzung bergab/bergauf, + Abweichung der Äste, glatt gemischt). -> (Boden, Gewicht Serpentine 0…1,
        Abstand zum nächsten Serpentinen-Rechteck)"""
        floor, wb, dbox = Ph.copy(), np.zeros(len(px)), np.full(len(px), np.inf)
        for (c0, vt, half), (c, tree, res) in zip(self.boxes, self.hblocks):
            loc = (np.column_stack([px, pz]) - c0) @ vt.T
            db = np.hypot(*np.maximum(np.abs(loc) - half, 0).T)
            dbox = np.minimum(dbox, db)
            w = 1 - smoothstep(0, 200, db)
            m = w > 0
            if not m.any():
                continue
            q = (np.clip(loc[m], -half, half)) @ vt  # nächster Punkt im Rechteck (Weltachsen, relativ zu c0)
            plane = c[0] + q[:, 0] * c[1] + q[:, 1] * c[2]
            # 64 Nachbarn (~ mehrere Äste): mit nur einem Ast sprang die Abweichung an der Mittellinie zwischen zwei Ästen
            d, k = tree.query(np.column_stack([px[m], pz[m]]), k=min(65, len(res)))
            sig = 30 + 0.3 * d[:, :1]
            g = np.exp(-(d / sig) ** 2)
            g = np.maximum(g[:, :-1] - g[:, -1:], 0)  # wer aus der Auswahl fällt, hat schon Gewicht 0 (stetig)
            r = (g * res[k[:, :-1]]).sum(1) / np.maximum(g.sum(1), 1e-12) * (1 - smoothstep(30, 150, db[m]))
            floor[m] += (plane + r - floor[m]) * w[m]
            wb[m] = np.maximum(wb[m], w[m])
        return floor, wb, dbox

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

    # Nebenstraßen: nur Kulisse (der Fahrer bleibt auf der Hauptstraße, route.pos bleibt unverändert)

    def _sides(self):
        """Nebenstraßen aus dem Seed: Einmündungen links/rechts (im Mittel alle ~1,5 km, im Ort öfter, am Berg seltener),
        manchmal eine Kreuzung (beide Seiten), manchmal eine Gabelung nach 100–400 m, ab und zu eine Brücke über die
        Hauptstraße (der Fahrer fährt darunter durch). Sie biegen in 55–125° ab, schwingen in Bögen weg, folgen dem Gelände
        (≤ 9 %), kommen der Hauptstraße nie wieder näher als 70 m, enden nach 0,6–2,5 km außer Sicht. Keine in Serpentinen-
        Blöcken, nicht ins Wasser, nicht durch Häuser. -> self.sides = [dict(x, z, h, hw, surf, mark, s, side, ang, bridge)]
        Unter der Hauptstraße hindurch geht nicht: das Gelände ist ein Höhenfeld (kein Loch unter einer Fahrbahn)."""
        rng = np.random.default_rng([self.seed, 31])
        self.sides = []
        P = self.Pfull
        bad = np.zeros(self.n, bool)
        for i0, i1 in self.blocks:
            bad[max(i0 - 80, 0):i1 + 80] = True
        bad |= np.abs(self.kappa) > 1 / 160  # enge Kurven
        wet = np.maximum.reduce([P["lake"], P["sea"], P["river"]]) > 0.05
        hx = np.array([[h["x"], h["z"]] for h in self.houses]) if self.houses else np.zeros((0, 2))
        htree = cKDTree(hx) if len(hx) else None
        occ = []  # Punkte fertiger Nebenstraßen (Abstand zueinander)

        def clear(x, z, own=None):
            """Punkte, die frei sind: weit genug von Hauptstraße, Häusern, anderen Nebenstraßen."""
            ok = self.tree.query(np.column_stack([x, z]))[0] > 70
            if htree is not None:
                ok &= htree.query(np.column_stack([x, z]))[0] > 20  # Hausmitte: Ecke ~8 m, Straße + Bankett ~3,5 m
            if occ:
                ok &= cKDTree(np.vstack(occ)).query(np.column_stack([x, z]))[0] > 45
            return ok

        def trace(x0, z0, th, length, skip):
            """10-m-Schritte, Krümmung als geglättetes Rauschen (R ≥ ~180 m); Ende, sobald ein Punkt nach den ersten skip
            Metern nicht frei ist."""
            m = max(int(length / 10), 8)
            k = gaussian_filter1d(rng.normal(0, 1, m + 40), 5)[20:20 + m]
            k = k / (np.abs(k).max() + 1e-9) / rng.uniform(180, 420)
            ths = th + np.concatenate([[0], np.cumsum(k[:-1] * 10)])
            x = x0 + np.concatenate([[0], np.cumsum(np.cos(ths[:-1]) * 10)])
            z = z0 + np.concatenate([[0], np.cumsum(np.sin(ths[:-1]) * 10)])
            ok = clear(x, z)
            ok[: int(skip / 10)] = True
            end = int(np.argmin(ok)) if not ok.all() else m
            return x[:end], z[:end]

        def profile(x, z, h0, cone=None):
            """Höhen: natürliches Gelände (Makrorelief, ohne Erosion), am Anfang auf h0, ≤ 9 % je Schritt, geglättet.
            cone = (Index, Höhe): Brücke – dort mindestens diese Höhe, Rampen 7 %."""
            hn = gaussian_filter1d(self._macro(x, z)["N"], 3, mode="nearest")
            u = np.arange(len(x)) * 10.0
            h = hn * smoothstep(0, 60, u) + h0 * (1 - smoothstep(0, 60, u))
            ramp = 0.07 * np.maximum(np.abs(u - cone[0] * 10.0) - 14, 0) if cone is not None else None
            if cone is not None:  # Brücke: Deck über der Hauptstraße, Rampen höchstens 7 % hinauf und hinab
                h = np.clip(h, cone[1] - ramp, cone[1] + ramp)
            for _ in range(2):  # Steigung begrenzen, vorwärts und rückwärts (Anfang bleibt fest)
                for j in range(1, len(h)):
                    h[j] = np.clip(h[j], h[j - 1] - 0.9, h[j - 1] + 0.9)
                h[1:] = gaussian_filter1d(h, 1.5, mode="nearest")[1:]
                if cone is not None:
                    h = np.clip(h, cone[1] - ramp, cone[1] + ramp)
            h[0] = h0 if cone is None else h[0]
            return h

        def add(x, z, h, s_, side, ang, bridge=False, parent=None):
            vil_ = P["village"][int(np.clip(s_ / STEP, 0, self.n - 1))]
            surf = 2 if (rng.random() < 0.3 and not bridge and parent is None and vil_ < 0.2) else 0  # Feldwege, nicht im Ort
            hw = float(rng.uniform(2.0, 2.4) if surf else rng.uniform(2.3, 2.8))
            if parent is not None:
                hw, surf = min(hw, parent[0]["hw"]), parent[0]["surf"]
            if not bridge:  # endet, wo das Längsprofil (≤ 9 %) dem Gelände nicht mehr folgen kann: sonst bis 400 m tiefer Graben
                off = np.abs(h - self._macro(x, z)["N"]) > 10.0  # mit senkrechten Wänden (ein Tunnel wäre nötig)
                off[:8] = False
                if off.any():
                    if np.argmax(off) < 12:
                        return None  # kürzer als 120 m: gar nicht erst anlegen
                    x, z, h = x[:np.argmax(off)], z[:np.argmax(off)], h[:np.argmax(off)]
            r = dict(x=x, z=z, h=h, hw=hw, surf=surf, mark=1 / 3 if hw >= 2.6 and not surf else 0.0, s=s_, side=side, ang=ang,
                     bridge=bridge, parent=parent)
            self.sides.append(r)
            occ.append(np.column_stack([x, z]))
            return r

        def mouth(i, side):
            """Einmündungspunkt an der Fahrbahnkante (Höhe = Hauptstraße dort, bündig)."""
            off = (self.hw[i] - 0.1) * side  # 10 cm auf den Asphalt (1 cm höher, s. _side_mesh): keine Naht zum Bankett
            # Höhe wie die Fahrbahnoberfläche dort (mit Quergefälle) − 5 cm: Nebenstraßen-h ist wie h der Hauptstraße "Trasse + 5 cm"
            return self.x[i] + self.lx[i] * off, self.z[i] + self.lz[i] * off, float(self.edge_y(i, side, abs(off))) - 0.05

        s = START + rng.uniform(300, 1200)
        while s < self.length - 2500:
            i = int(s / STEP)
            vil = P["village"][i]
            gap = rng.uniform(800, 2400) / (1 + 1.5 * vil) * (1.6 if abs(self.grade[i]) > 0.05 else 1.0)
            if bad[max(i - 20, 0):i + 20].any():
                s += 150
                continue
            th0 = self.theta[i]
            water = wet[max(i - 40, 0):i + 40].any()
            wside = -float(np.sign(P["wside"][i]) or 1)
            roll = rng.random()
            if roll < 0.12 and not water and vil < 0.2 and abs(self.grade[i]) < 0.03:  # Brücke über die Hauptstraße
                ang = rng.uniform(65, 90) * np.pi / 180 * rng.choice([-1, 1])
                cx, cz = self.x[i], self.z[i]
                d = th0 + ang
                L1, L2 = rng.uniform(500, 1400), rng.uniform(500, 1400)
                # gerades Stück ±150 m durch den Kreuzungspunkt, dann frei weiter; Linie von Seite A nach Seite B
                xa, za = trace(cx, cz, d + np.pi, L1, 160)
                xb, zb = trace(cx, cz, d, L2, 160)
                if len(xa) > 30 and len(xb) > 30:
                    x, z = np.r_[xa[::-1], xb[1:]], np.r_[za[::-1], zb[1:]]
                    jc = len(xa) - 1
                    win = slice(max(i - 6, 0), i + 7)
                    deck = float(self.h[win].max() + 7.0)  # Unterkante ≥ 6,1 m über der Fahrbahn
                    hn0 = self._macro(x[jc - 15:jc + 16], z[jc - 15:jc + 16])["N"]
                    if hn0.min() > self.h[i] - 6 and hn0.max() < deck + 4:  # Gelände passt zu Damm und Rampen (kein Tal, kein Einschnitt)
                        h = profile(x, z, self._macro(x[:1], z[:1])["N"][0], cone=(jc, deck))
                        add(x, z, h, s, 0, float(ang), bridge=True)
                        s += gap
                        continue
            sides = [wside] if water else ([1, -1] if roll < 0.2 else [int(rng.choice([-1, 1]))])  # 13 %: Kreuzung
            ang = rng.uniform(55, 125) * np.pi / 180
            for sd in sides:
                mx, mz, mh = mouth(i, sd)
                th = th0 - sd * ang  # links (+lx) liegt bei th0 − 90°
                x, z = trace(mx, mz, th, rng.uniform(600, 2500), 70)
                if len(x) < 12:
                    continue
                r = add(x, z, profile(x, z, mh), s, sd, float(ang))
                if r is None:
                    continue
                x, z = r["x"], r["z"]
                if rng.random() < 0.25 and len(x) > 45:  # Gabelung
                    j = int(rng.uniform(10, min(40, len(x) - 5)))
                    tx, tz = x[j + 1] - x[j], z[j + 1] - z[j]
                    tb = math.atan2(tz, tx) + rng.choice([-1, 1]) * rng.uniform(40, 70) * np.pi / 180
                    lft = np.array([tz, -tx]) / 10.0  # links der Nebenstraße
                    sgn = 1 if (math.cos(tb) * lft[0] + math.sin(tb) * lft[1]) > 0 else -1
                    bx, bz = x[j] + lft[0] * sgn * (r["hw"] - 0.1), z[j] + lft[1] * sgn * (r["hw"] - 0.1)
                    occ.pop()  # Elternstraße selbst nicht als Hindernis am Abzweig
                    xb, zb = trace(bx, bz, tb, rng.uniform(400, 1500), 50)
                    occ.append(np.column_stack([x, z]))
                    if len(xb) > 10:
                        add(xb, zb, profile(xb, zb, r["h"][j]), s, sd, float(abs(tb - math.atan2(tz, tx))), parent=(r, j))
            s += gap
        # Häuser-Deko und Einfahrten nicht auf Nebenstraßen
        if self.sides:
            self._side_index()
            if self.deco:
                dd, hw_ = self.side_dist(np.array([d[1] for d in self.deco]), np.array([d[2] for d in self.deco]))
                self.deco = [d for d, ok in zip(self.deco, dd > hw_ + 1.5) if ok]

    def _side_index(self):
        """Alle Nebenstraßen als eine Punktreihe für terrain.conform: nach jeder Straße ein Trennpunkt mit hw = −1e4
        (das Stück zur nächsten Straße zählt dann nicht)."""
        xs, zs, hs, ws, rid = [], [], [], [], []
        for k, r in enumerate(self.sides):
            n = len(r["x"])
            xs += [r["x"], r["x"][-1:]]
            zs += [r["z"], r["z"][-1:]]
            hs += [r["h"], r["h"][-1:]]
            w_ = np.full(n, r["hw"])
            if not r["bridge"]:
                w_[0] += self._flare(r)  # Mündungstrichter: breitere Trasse, kein Gras/Baum in den ausgerundeten Ecken
            ws += [w_, [-1e4]]
            rid += [np.full(n, k), [-1]]
        self.side10 = tuple(np.concatenate(a).astype(float) for a in (xs, zs, hs, ws))
        self.side_rid = np.concatenate(rid)
        self.stree = cKDTree(np.column_stack(self.side10[:2]))

    @staticmethod
    def _flare(r):
        """Eckradius der Mündung (m): Asphalt 6, Feldweg 4."""
        return 4.0 if r["surf"] else 6.0

    def side_dist(self, px, pz):
        """Abstand zur nächsten Nebenstraße (Mittellinie, je Stück genau) -> (Abstand, halbe Breite); ohne: (inf, 0)."""
        if not self.sides:
            return np.full(len(px), np.inf), np.zeros(len(px))
        x, z, _, hw = self.side10
        _, ii = self.stree.query(np.column_stack([px, pz]), k=3)
        best, bhw = np.full(len(px), np.inf), np.zeros(len(px))
        for c in range(3):
            for a in (np.maximum(ii[:, c] - 1, 0), np.minimum(ii[:, c], len(x) - 2)):
                b = a + 1
                okseg = (self.side_rid[a] == self.side_rid[b]) & (self.side_rid[a] >= 0)
                ex, ez = x[b] - x[a], z[b] - z[a]
                u = np.clip(((px - x[a]) * ex + (pz - z[a]) * ez) / (ex * ex + ez * ez + 1e-12), 0, 1)
                d = np.where(okseg, np.hypot(px - x[a] - u * ex, pz - z[a] - u * ez), np.inf)
                better = d < best
                best, bhw = np.where(better, d, best), np.where(better, hw[a], bhw)
        return best, bhw

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
        # Serpentinen: Abstand zur ganzen Serpentinen-Fläche statt zum einzelnen Ast -> innen ein durchgehender Hang (_floor),
        # die Äste werden nur als Bankett hineingeschnitten; außen Täler/Berge wie sonst. Ohne das gräbt sich jeder Ast sein
        # eigenes Tal und zwischen den Ästen bleiben Grate stehen (Waschbrett). Gewicht q rein geometrisch (Abstand zum
        # Rechteck): das über params gemischte P["hp"] knickte an den Grenzen der Stützen-Nachbarschaften.
        floor, q, db = self._floor(px, pz, P["h"])
        P["h"] = floor
        dh = d
        if (q > 0).any():
            df = df * (1 - q) + np.minimum(db, df) * q
            dh = d * (1 - q) + np.maximum(np.minimum(db, d), 70.0) * q  # Hügelwellen auch innen (halbe Stärke), quer über die Äste
        dv = df * (1 + 0.3 * fbm(px / 1300, pz / 1300, sd + 33, 2))
        valley = P["valley"] * (1 - np.exp(-(dv / np.maximum(P["vwidth"], 30)) ** P["vexp"])) * calm
        # Hang quer zur Straße, klingt nach ~600 m ab (sonst springt er, wo zwei ferne Straßenteile gleich nah sind)
        cross = P["cross"] * 140 * np.tanh(t / 140) * np.exp(-(t / 600) ** 2) * calm * (1 - q) * (1 - 0.7 * P["pk"])
        hills = 22 * P["relief"] * self.drama * calm * fbm(wx / 380, wz / 380, sd + 20, 4) * smoothstep(hw + 6, hw + 130, dh) * (1 - 0.8 * P["pk"])
        mount = P["mount"] * self.drama * shape * smoothstep(110, 900, dv) * open_side * calm
        # Anstieg: Talflanken und Gebirge ringsum kleiner (stetig über params gemischt – eine Seitenregel nach dem Vorzeichen
        # von t sprang, wo der nächste Straßenpunkt wechselt: Stufen bis 19 m); am Gipfel fällt das Gelände gleich hinter dem
        # Bankett steil weg (~40 %, höchstens 80 % des Anstiegs) -> Grat/Kuppe, Blick über das Land
        valley = valley * (1 - 0.5 * P["cw"])
        mount = mount * (1 - 0.5 * P["cw"])
        xf = np.maximum(df - hw - 6, 0)
        fall = P["pk"] * np.minimum(0.42 * xf * xf / (xf + 15), 0.8 * P["gain"])  # nach ~30 m schon ~40 % Gefälle
        N = self._carve(px, pz, P["h"] + valley + cross + hills + mount - fall)
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
            sl = np.hypot(E["gx"], E["gz"])
            if res > NEAR[1]:
                # grobe Kacheln: Stufen über die Höhe einer Rasterzelle mitteln (Kastenfilter), sonst schneidet die Stufenkante
                # die Dreiecke schräg -> Sägezähne am Hang (Wüste, mittlere/ferne Kacheln). Feine Kacheln unverändert.
                wd = 3.0 * res * np.minimum(sl, 1.5)
                tr = sum(terrain.terrace(rel + o * wd, px, pz, sd) - o * wd for o in np.linspace(-0.5, 0.5, 6)) / 6
            else:
                tr = terrain.terrace(rel, px, pz, sd)
            N = N + P["st_mesa"] * smoothstep(0.2, 0.5, sl) * (tr - rel)
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
        if self.sides:  # Nebenstraßen zuerst, die Hauptstraße gewinnt (ihre Trasse und Einschnitte bleiben unverändert)
            N, _ = terrain.conform(px, pz, N, self.stree, *self.side10, SINK, 1.5)
        H, _ = terrain.conform(px, pz, N, self.rtree, *self.road10, SINK, BENCH)
        if self.sides:  # ab hier: Abstand = zur nächsten Fahrbahnkante (Haupt- oder Nebenstraße) + hw -> alle Abstandsregeln gelten
            ds, dhw = self.side_dist(px, pz)
            d = hw + np.minimum(d - hw, ds - dhw)
        out = dict(h=H, s=s, t=t, d=d, hw=hw, wl=Wl, wm=np.maximum.reduce([lake, sea, river]), P=P,
                   slope=np.hypot(E["gx"], E["gz"]), E=E)
        if not color:
            return out
        # Bodenklassen: Wald als große Flecken (Dichte aus Biom × Szene × Einstellung), offenes Land Wiese/Feld/Busch/Sand
        dens = np.clip(P["forest_b"] * P["forest"] * self.density, 0, 1)
        f = fbm(px / 420, pz / 420, sd + 40, 4) + 0.35 * fbm(px / 90, pz / 90, sd + 41, 2)
        above = H - (P["h"] + P["treeline"])  # über der Baumgrenze (Alpen)
        # Baumgrenze als Saum statt Höhenlinie: Südhänge (fallen nach +z) höher, Nordhänge tiefer, wellig; der Wald lichtet
        # sich über ~200 m in Gruppen auf (Schwelle steigt). Steile Flanken ohne Wald (dort stehen auch keine Bäume, _plants).
        tl = above - 70 * np.clip(-E["gz"], -1, 1) + 45 * fbm(px / 700, pz / 700, sd + 45, 2)
        dens = dens * smoothstep(20, -180, tl) * (1 - smoothstep(0.55, 0.8, np.hypot(E["gx"], E["gz"])))
        forest = (f > 0.55 - 1.25 * dens) & (dens > 0.01) & (d > hw + 6) & (vill < 0.5)
        cls = np.full(len(px), MEADOW, np.uint8)
        cls[(fbm(px / 380, pz / 380, sd + 42, 3) > 0.45 - 1.2 * P["farm"]) & (d > hw + 4)] = FARM
        cls[fbm(px / 260, pz / 260, sd + 43, 3) > 0.55 - 1.3 * P["scrub"]] = SCRUB
        cls[fbm(px / 260, pz / 260, sd + 44, 3) > 0.6 - 1.6 * P["sand"]] = SAND
        # Fels: steil (Erosionsraster), konvexe Grate, hoch über der Baumgrenze; Mittelgebirge bleibt meist bewachsen.
        # Schutt: mittelsteile, hohle Hänge unter Felsen im Gebirge. Nässe/Sediment aus dem Abfluss der Erosion.
        sl, curv = out["slope"], E["curv"]
        rockx = (np.clip(smoothstep(0.55, 0.95, sl) + 0.6 * smoothstep(0.002, 0.012, -curv) * smoothstep(0.25, 0.5, sl)
                        + smoothstep(60, 220, tl) + P["st_mesa"] * smoothstep(0.3, 0.6, sl), 0, 1)
                 * (1 - 0.6 * P["st_soft"]) * (1 - vill) * (d > hw + 3))  # Wüste: jede Wand ist Sandstein
        scree = (smoothstep(0.3, 0.55, sl) * (1 - smoothstep(0.8, 1.0, sl)) * smoothstep(0.0, 0.006, curv)
                 * np.clip(P["st_ridged"] + 0.5 * P["st_mesa"] + 0.3 * P["rock"], 0, 1) * (1 - vill) * (d > hw + 3))
        wet = smoothstep(np.log10(16 * 16 * 40), np.log10(16 * 16 * 4000), np.where(E["flow"] > 0, E["flow"], 0)) * (d > hw + 4)
        sed = smoothstep(0.3, 3.0, E["sed"])
        rock = (rockx > 0.55) | (tl > 160) | ((P["st_mesa"] > .5) & (M["mount"] > 0.45 * P["mount"]))
        cls[rock] = ROCK
        cls[forest] = FOREST
        under = (H < Wl - 0.15) & (out["wm"] > WET)
        cls[under] = WATER
        pal = sum(P["pal_" + k][:, None] * PALETTES[k][cls] for k in PALETTES)
        alp = smoothstep(-60, 60, tl)[:, None]
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
        # Straßenrand als Saum statt Kante: Kiesbankett, dann lückiges, kurzes Gras, nach ~3 m volle Wiese; Rand ausgefranst
        de = d - hw + 0.5 * fbm(px / 3, pz / 3, sd + 48, 2)
        grow = np.where(de < 0.7, biome.GROW[PAVING], np.where(de < 1.4, np.minimum(grow, 200), np.where(
            de < 2.2, np.minimum(grow, 215), np.where(de < 3.0, np.minimum(grow, 230), grow))))
        rgb = rgb + (np.array([.45, .42, .37]) - rgb) * (0.6 * (1 - smoothstep(0.4, 1.4, de)))[:, None]
        rgb = rgb + (np.array([.50, .50, .33]) - rgb) * (0.2 * (1 - smoothstep(1.4, 3.5, de)) * smoothstep(0.4, 1.4, de))[:, None]
        out.update(cls=cls, rgb=np.clip(rgb, 0, 1), grow=grow, forest=forest, above=above, tl=tl, vill=vill,
                   mat=np.column_stack([wet, sed, rockx, scree]))
        return out

    # Kacheln

    def items(self, s_rider):
        """Alles, was um s_rider geladen sein soll -> {key: Priorität}. Nur aus der Straßengeometrie, also deterministisch.
        Fein/mittel entscheidet der Abstand zum Straßenstück im Fenster um den Fahrer, nicht zur nächsten Straße überhaupt:
        sonst bekam eine Kachel, deren Mitte näher an einem 2 km entfernten Straßenteil liegt, nur den 32-m-Block – auch
        wenn der Fahrer gerade darin fuhr (Fahrer im Hang vergraben, VO2/Reise Seed 7 km 10,6)."""
        lo, hi = max(s_rider - BEHIND, 0), min(s_rider + AHEAD, self.length)
        i = np.arange(int(lo / STEP), int(hi / STEP) + 1)
        px, pz, ds = self.x[i], self.z[i], i * STEP - s_rider
        out = {}
        for k in range(int(lo / STEP / CHUNK), min(int(hi / STEP / CHUNK) + 1, (self.n - 1) // CHUNK + 1)):
            out[f"r{k}"] = max(k * CHUNK * STEP - s_rider, 0) * 0.5
        win = (cKDTree(np.column_stack([px, pz])), px, pz, ds)
        nw = (ds > -BEHIND - 200) & (ds < NEAR_AHEAD)
        near = (cKDTree(np.column_stack([px[nw], pz[nw]])), px[nw], pz[nw], ds[nw])
        B, R = FAR[0], FAR_R
        cand = set()
        for x0, z0 in zip(px[::24], pz[::24]):
            for I in range(int(math.floor((x0 - R) / B)), int(math.floor((x0 + R) / B)) + 1):
                for J in range(int(math.floor((z0 - R) / B)), int(math.floor((z0 + R) / B)) + 1):
                    cand.add((I, J))
        cand = sorted(cand)
        c = np.array([((I + .5) * B, (J + .5) * B) for I, J in cand])
        dr, j = win[0].query(c)
        for (I, J), cc, dw, jj in zip(cand, c, dr, j):
            if dw - B * 0.7072 > R:
                continue
            # fein/mittel nur nah am Straßenstück um den Fahrer; sonst ein grober Block
            if self.rect_dist(cc, B / 2, MID_DIST, win) >= MID_DIST:
                out[f"f{I}_{J}"] = (abs(ds[jj]) if ds[jj] > 0 else 3 * -ds[jj]) + 800
                continue
            for mi in range(4):
                for mj in range(4):
                    self._mid(I * 4 + mi, J * 4 + mj, out, win, near)
        return out

    def rect_dist(self, c, half, limit, pts=None):
        """Abstand Quadrat (Mitte c, halbe Kante) <-> Straßenpunkte (alle oder pts = (Baum, x, z, …)), exakt bis limit."""
        tree, x, z = pts[:3] if pts is not None else (self.tree, self.x, self.z)
        if not len(x):
            return limit
        idx = tree.query_ball_point(c, half * 1.4143 + limit)
        if not idx:
            return limit
        idx = np.array(idx)
        dx = np.maximum(np.abs(x[idx] - c[0]) - half, 0)
        dz = np.maximum(np.abs(z[idx] - c[1]) - half, 0)
        return float(np.hypot(dx, dz).min())

    def _mid(self, i, j, out, win, near):
        pr = lambda c, w: (lambda d: abs(d) if d > 0 else 3 * -d)(w[3][w[0].query(c)[1]])  # Priorität: Fahrstrecke bis dahin
        T = MID[0]
        c = np.array([(i + .5) * T, (j + .5) * T])
        if self.rect_dist(c, T / 2, NEAR_DIST, near) >= NEAR_DIST:
            out[f"m{i}_{j}"] = pr(c, win) + 250
            return
        for a in range(2):
            for b in range(2):
                ni, nj = i * 2 + a, j * 2 + b
                out[f"n{ni}_{nj}"] = pr(np.array([(ni + .5) * NEAR[0], (nj + .5) * NEAR[0]]), near)

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
        Hg = (self._far_edge(GX.ravel(), GZ.ravel(), F["h"], F["d"]) if lod is MID else F["h"]).reshape(m, m)
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

    def _far_edge(self, px, pz, h, d):
        """Mittlere Kacheln an 1024-m-Linien (nur dort grenzen ferne Kacheln an: die liegen ≥ MID_DIST von der Straße, feine
        ≤ NEAR_DIST): in einem 32-m-Band zur Kante hin auf das 32-m-Netz der fernen Kachel überblenden (Geomorphing), weg von
        Straßen. Sonst klaffte dort bis 12 m Lücke, und die senkrechte Schürze der fernen Kachel stand als Wand in der
        Landschaft. Bäume/Felsen der Kachel nehmen dieselbe Höhe. -> h"""
        e = np.minimum.reduce([np.mod(px, FAR[0]), FAR[0] - np.mod(px, FAR[0]), np.mod(pz, FAR[0]), FAR[0] - np.mod(pz, FAR[0])])
        w = np.clip(1 - e / FAR[1], 0, 1) * smoothstep(250, 450, d)
        sel = w > 0
        if not sel.any():
            return h
        h = h.copy()
        h[sel] += (mesh_h(self, px[sel], pz[sel], FAR[1]) - h[sel]) * w[sel]
        return h

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
        p_tree = np.maximum(p_tree * smoothstep(140, 0, F["tl"]), 0.55 * oasis * (cls != WATER))  # einzelne Bäume bis ~140 m über den Wald
        # steil (dort malt terrain.gdshader Fels, Normale < ~0,82), Fels oder Geröll: keine Bäume
        steep = (F["slope"] > 0.65) | (F["mat"][:, 2] > 0.4) | (F["mat"][:, 3] > 0.5)
        ok = ~steep & (F["d"] > F["hw"] + 5) & (cls != WATER) & (cls != ROCK) & ((F["h"] > F["wl"] + 0.4) | (F["wm"] < WET * 0.5)) & (F["vill"] < 0.3)
        grove = ok & self._grove(px, pz, P, cls)
        tree = ok & ~grove & (r < p_tree)
        # Art je Baum nach dem Mix der Gegend (Oase: Dattelpalmen)
        w = np.column_stack([P["tr_" + s] + (oasis if s == "palme" else 0) for s in SPECIES])
        cum = np.cumsum(w / np.maximum(w.sum(1, keepdims=True), 1e-9), 1)
        species = np.minimum((hash01(ci, cj, seed=sd + 4)[:, None] > cum).sum(1), len(SPECIES) - 1)
        # im Schnee (ab ~80 m unter der Schneegrenze der nächsten Straßenstelle, wie das Rauschband der Shader) nur Fichten/Kiefern
        snowy = F["h"] > self.snow_line()[self.tree.query(np.column_stack([px, pz]))[1]] - 80
        if snowy.any():
            conifer = np.where(hash01(ci, cj, seed=sd + 9) < 0.75, SPECIES.index("fichte"), SPECIES.index("kiefer"))
            broad = ~np.isin(species, [SPECIES.index(k) for k in ("fichte", "kiefer", "aleppo", "pinie", "zypresse")])
            species = np.where(snowy & broad, conifer, species)
        scale = (0.7 + 0.7 * hash01(ci, cj, seed=sd + 5)) * (1.0 if lod is NEAR else 1.15)
        y = (F["h"] if lod is NEAR else self._far_edge(px, pz, F["h"], F["d"])) - 0.25
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
        if lod is NEAR:
            self._under(key, x0, z0, size, out, msg)

    def _under(self, key, x0, z0, size, out, msg):
        """Unterwuchs (nur feine Kacheln, sichtbar bis ~160 m): Farne im Wald, hohes Gras und Stauden am Waldrand, auf
        Lichtungen und im Saum der Straße (2,5–7 m neben der Fahrbahn) -> der Wald wirkt dicht, die Straße läuft weich ins
        Grün. Nur in grünen Gegenden (Palette lush), nicht im Schnee, nicht auf Fels/Geröll/steil, nicht im Ort."""
        sp = 2.6
        ci, cj = np.meshgrid(np.arange(math.ceil(x0 / sp), math.ceil((x0 + size) / sp)), np.arange(math.ceil(z0 / sp), math.ceil((z0 + size) / sp)))
        ci, cj = ci.ravel(), cj.ravel()
        sd = self.seed + 1300
        px = (ci + hash01(ci, cj, seed=sd + 1)) * sp
        pz = (cj + hash01(ci, cj, seed=sd + 2)) * sp
        F = self.field(px, pz, res=NEAR[1])
        P, cls, de = F["P"], F["cls"], F["d"] - F["hw"]
        lush = np.clip(1 - 1.4 * P["pal_dry"], 0, 1) * np.clip(self.density, 0, 1.5)
        woods = np.clip(P["forest_b"] * P["forest"] * 1.5, 0, 1)  # Waldgegend: Saum der Straße wie ein Waldweg
        snowy = F["h"] > self.snow_line()[self.tree.query(np.column_stack([px, pz]))[1]] - 80
        ok = ((de > 2.5) & (cls != WATER) & (cls != ROCK) & (F["slope"] < 0.9) & (F["mat"][:, 3] < 0.5) & (F["vill"] < 0.3)
              & ~snowy & ((F["h"] > F["wl"] + 0.3) | (F["wm"] < WET * 0.5)))
        forest, open_ = cls == FOREST, np.isin(cls, [MEADOW, FARM])
        verge = (de < 7.0) * woods
        patch = smoothstep(-0.3, 0.5, fbm(px / 35, pz / 35, sd + 5, 2))  # Farne in Teppichen (lichte, feuchte Stellen)
        p = {"farn": (0.08 + 0.6 * patch) * forest + 0.10 * verge,
             "hochgras": 0.06 * forest + 0.04 * open_ + 0.30 * verge,
             "staude": 0.02 * forest + 0.012 * open_ + 0.06 * verge}
        r, k = hash01(ci, cj, seed=sd + 3), hash01(ci, cj, seed=sd + 4)
        lo = 0.0
        for kind, pk in p.items():  # eine Pflanze je Zelle: Arten teilen sich den Würfelwurf
            hi = lo + pk * lush
            sel = ok & (r >= lo) & (r < hi)
            lo = hi
            if sel.any():
                instances(out / f"{key}.{kind}.bin", px[sel], F["h"][sel] - 0.05, pz[sel], (0.75 + 0.5 * k[sel]) * (1.25 if kind == "farn" else 1.0))
                msg.setdefault("plants", []).append(kind)

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
        rows = self._cliffs(x0, z0, size, res)
        # Art je Fels nach Biom-Gewichten (Übergänge mischen sich)
        w = np.column_stack([P["st_ridged"] + 0.2 * P["rock"] * (1 - P["desert"]), P["pal_dry"] * (1 - P["desert"]) * (1 - P["st_ridged"]),
                             P["desert"] + P["st_mesa"] * 0.5, P["st_soft"] * (1 - P["pal_dry"])]) + 1e-6
        cum = np.cumsum(w / w.sum(1, keepdims=True), 1)
        kind = (hash01(ci, cj, seed=sd + 5)[:, None] > cum).sum(1).clip(0, 3)
        var = (hash01(ci, cj, seed=sd + 6) * 3).astype(int).clip(0, 2)
        names = ["granit", "kalk", "sand", "moos"]
        yaw = hash01(ci, cj, seed=sd + 7) * 2 * np.pi
        stretch = [0.8 + 0.5 * hash01(ci, cj, seed=sd + 8 + q) for q in range(3)]
        hgt = F["h"] if lod is NEAR else self._far_edge(px, pz, F["h"], F["d"])
        y = hgt - scale * (0.25 + 0.6 * sl / 1.5)  # im Hang versenkt: keine schwebende Unterkante
        for i in np.flatnonzero(ok):
            s = scale[i]
            rows.setdefault(f"fels_{names[kind[i]]}_{var[i]}", []).append(
                signs._yaw_rows(px[i], y[i], pz[i], yaw[i], s * stretch[0][i], s * stretch[1][i], s * stretch[2][i]))
        if not rows:
            return
        for kd, rr in rows.items():
            np.array(rr, np.float32).tofile(out / f"{key}.deco_{kd}.bin")
        msg.setdefault("deco", []).extend(sorted(rows))

    def _cliffs(self, x0, z0, size, res):
        """Felswände in steilem Fels: breite, flache Wandstücke (blender/deco.py `wand`, 15–60 m hoch) quer zum Hang, Front
        hangabwärts, Fuß im Hang versenkt; im 24-m-Raster überlappend -> durchgehende Felsbänder statt einzelner Brocken.
        Raster und Seeds hängen nicht von der LOD ab: feine und mittlere Kacheln setzen dieselben Wände. -> {Modell: [Zeilen]}"""
        sp = 24.0
        ci, cj = np.meshgrid(np.arange(math.ceil(x0 / sp), math.ceil((x0 + size) / sp)), np.arange(math.ceil(z0 / sp), math.ceil((z0 + size) / sp)))
        ci, cj = ci.ravel(), cj.ravel()
        sd = self.seed + 1100
        px = (ci + hash01(ci, cj, seed=sd + 1)) * sp
        pz = (cj + hash01(ci, cj, seed=sd + 2)) * sp
        F = self.field(px, pz, res=res)
        sl = F["slope"]
        # nur in Bändern: Höhenstufen alle ~70 m (wellig über die Fläche), dazwischen Schutt/Gras wie in echten Wänden
        # VERSION 12: nur in wirklich steilem Fels, schmalere Bänder, lückig – in Reihen über ganze Hänge wirkten die Platten wie
        # gestapelte Blöcke (aus der Ferne dunkle Linsen in Zeilen)
        band = np.mod(F["h"] / 70.0 + 0.8 * fbm(px / 400, pz / 400, self.seed + 47, 2), 1.0) < 0.18
        ok = ((F["mat"][:, 2] > 0.5) & (sl > 1.15) & band & (hash01(ci, cj, seed=sd + 3) < 0.45) & (F["vill"] < 0.3)
              & (F["cls"] != WATER) & (F["d"] > F["hw"] + 14))
        rows = {}
        if not ok.any():
            return rows
        i = np.flatnonzero(ok)
        px, pz = px[i], pz[i]
        k = np.clip(0.5 + 1.2 * fbm(px / 180, pz / 180, self.seed + 48, 2), 0, 1)  # Höhe ändert sich weich: Band statt Klötze
        e = 3.0  # Hangrichtung aus Höhendifferenzen (nur für die Kandidaten)
        h = self.field(np.r_[px + e, px - e, px, px], np.r_[pz, pz, pz + e, pz - e], color=False, res=res)["h"].reshape(4, -1)
        gx, gz = (h[0] - h[1]) / (2 * e), (h[2] - h[3]) / (2 * e)
        yaw = np.arctan2(-gx, -gz)  # lokal +Z (Front) zeigt hangabwärts
        hgt = (25 + 35 * k) * np.clip((sl[i] - 0.6) / 0.6, 0.6, 1.0)
        half = np.minimum(hgt * (0.9 + 0.6 * hash01(ci[i], cj[i], seed=sd + 5)), F["d"][i] - F["hw"][i] - 8)  # Enden weg von der Straße
        P = F["P"]
        w = np.column_stack([P["st_ridged"] + 0.2 * P["rock"] * (1 - P["desert"]) + P["st_soft"] * (1 - P["pal_dry"]),
                             P["pal_dry"] * (1 - P["desert"]) * (1 - P["st_ridged"]), P["desert"] + P["st_mesa"] * 0.5])[i] + 1e-6
        cum = np.cumsum(w / w.sum(1, keepdims=True), 1)
        kind = (hash01(ci[i], cj[i], seed=sd + 6)[:, None] > cum).sum(1).clip(0, 2)
        var = (hash01(ci[i], cj[i], seed=sd + 7) * 2).astype(int).clip(0, 1)
        y = F["h"][i] - 0.35 * hgt  # tief versenkt: nur der obere Teil steht als Felsstufe aus dem Hang
        for n in np.flatnonzero(half >= 6):
            rows.setdefault(f"fels_wand_{['granit', 'kalk', 'sand'][kind[n]]}_{var[n]}", []).append(
                signs._yaw_rows(px[n], y[n], pz[n], yaw[n], half[n], hgt[n], 0.3 * hgt[n]))
        return rows

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
        # Bankett als eigener Streifen (Fahrbahnkante doppelt): Asphalt endet scharf, statt über 0,35 m in die Bodenfarbe zu verlaufen
        off = np.column_stack([sh, hw, hw, -hw, -hw, -sh])
        bk = self.bank[sel]
        top, low = np.zeros(nr) + .05, np.full(nr, -.2)  # Außenkante unter die Trasse
        yo = np.column_stack([low + bk * sh, top + bk * hw, top + bk * hw, top - bk * hw, top - bk * hw, low - bk * sh])
        pos = np.stack([np.column_stack([x + lx * off[:, c], h + yo[:, c], z + lz * off[:, c]]) for c in range(6)], 1).reshape(-1, 3)
        # Normale analytisch je Punkt (gleich an der Grenze zum nächsten Stück -> keine Lichtkante)
        g = self.grade[sel]
        tan = np.column_stack([np.cos(self.theta[sel]), g, np.sin(self.theta[sel])])
        left = np.column_stack([lx, bk, lz])
        nrm = np.cross(left, tan)
        nrm *= np.sign(nrm[:, 1:2])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.repeat(nrm, 6, axis=0)
        q = (np.arange(nr - 1)[:, None] * 6 + np.array([0, 2, 4])[None, :]).ravel()  # Bankett links, Fahrbahn, Bankett rechts
        idx = np.column_stack([q, q + 6, q + 1, q + 1, q + 6, q + 7]).reshape(-1, 3)
        col = np.zeros((nr, 6, 4))
        col[:, 2:4, :3] = self.asphalt[sel][:, None]
        col[:, 2:4, 3] = np.where(hw >= 2.85, 1.0, 1 / 3)[:, None]  # breite Straßen Mittel- und Randlinien, schmale nur Mitte
        F = self.field(np.r_[x + lx * sh, x - lx * sh], np.r_[z + lz * sh, z - lz * sh])
        col[:, 0:2, :3], col[:, 4:6, :3] = F["rgb"][:nr, None], F["rgb"][nr:, None]
        uv = np.column_stack([-off.ravel(), np.repeat(sel * STEP, 6)])
        uv2 = np.repeat(np.column_stack([hw, np.full(nr, -1.0)]), 6, axis=0)
        # Einmündungen: Randlinie der Hauptstraße dort unterbrochen (road.gdshader CUSTOM0 x = links, y = rechts)
        c0 = np.zeros((nr, 6, 4))
        mine = [r for r in self.sides if a * STEP <= r["s"] < b * STEP]
        for r in mine:
            if not r["bridge"] and r["parent"] is None:
                half = r["hw"] / max(math.sin(r["ang"]), 0.5) + 6
                c0[np.abs(sel * STEP - r["s"]) < half, :, 0 if r["side"] > 0 else 1] = 1
        parts = [(pos, nrm, idx, col.reshape(-1, 4), uv, uv2, c0.reshape(-1, 4))] + [self._side_mesh(r) for r in mine]
        o, P_ = 0, []
        for pp in parts:
            P_.append((pp[0], pp[1], pp[2] + o) + pp[3:])
            o += len(pp[0])
        pos, nrm, idx, col, uv, uv2, c0 = (np.concatenate([pp[c] for pp in P_]) for c in range(7))
        save_mesh(out, f"r{k}", pos, nrm, idx, col=col, uv=uv, uv2=uv2, c0=c0 if mine else None)
        msg = {"road": f"r{k}"}
        self._furniture(k, a, b, out, msg)
        return msg

    def _side_mesh(self, r):
        """Fahrbahn einer Nebenstraße wie die Hauptstraße (6 Punkte je Querschnitt: Bankett, Fahrbahn, Bankett); der erste
        Querschnitt liegt auf der Kante der Straße, von der sie abzweigt (bündig, ohne Lücke/Überlappung). Brücken: Deck-
        Seiten, Brüstungen, Pfeiler neben der Hauptstraße (Beton, Straßen-Shader ohne Markierung).
        -> (pos, nrm, idx, col, uv, uv2, c0)"""
        x, z, h, hw = r["x"], r["z"], r["h"], r["hw"]
        if r["bridge"]:  # Brücke fein (2,5 m): Spannweite und Pfeiler genau neben der Hauptstraße
            su = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
            u = np.arange(0, su[-1], 2.5)
            x, z, h = np.interp(u, su, x), np.interp(u, su, z), np.interp(u, su, h)
        n = len(x)
        tx, tz = np.gradient(x), np.gradient(z)
        L = np.hypot(tx, tz) + 1e-9
        tx, tz = tx / L, tz / L
        lx, lz = tz, -tx
        sh = hw + 0.35
        off = np.array([sh, hw, hw, -hw, -hw, -sh])
        yo = np.array([-.2, .05, .05, .05, .05, -.2])
        pos = np.stack([np.column_stack([x + lx * o, h + y, z + lz * o]) for o, y in zip(off, yo)], 1)
        if not r["bridge"]:  # Mündung: ersten Querschnitt entlang der Richtung auf die Kantenlinie schieben
            if r["parent"] is None:
                i = int(r["s"] / STEP)
                ex, ez = math.cos(self.theta[i]), math.sin(self.theta[i])
            else:
                pr, j = r["parent"]
                ex, ez = pr["x"][j + 1] - pr["x"][j], pr["z"][j + 1] - pr["z"][j]
            cr = lambda ax, az, bx, bz: ax * bz - az * bx
            den = cr(ex, ez, tx[0], tz[0])
            if abs(den) > 0.2:
                lam = -cr(ex, ez, lx[0] * off, lz[0] * off) / den
                pos[0, :, 0] += tx[0] * lam
                pos[0, :, 2] += tz[0] * lam
                pos[0, 1:5, 1] = h[0] + .06  # Mündung eben, 1 cm über der Fahrbahn, auf die sie 10 cm hinaufreicht
                pos[0, [0, 5], 1] = h[0] - .2  # Bankett bleibt unten (der Eckfächer liegt darüber)
        fill = self._mouth_fill(r, pos, tx, tz, h, hw) if not r["bridge"] else None
        pos = pos.reshape(-1, 3)
        g = np.gradient(h) / np.maximum(np.hypot(np.gradient(x), np.gradient(z)), 1e-6)
        nrm = np.cross(np.column_stack([lx, np.zeros(n), lz]), np.column_stack([tx, g, tz]))
        nrm *= np.sign(nrm[:, 1:2])
        nrm = np.repeat(nrm / np.linalg.norm(nrm, axis=1, keepdims=True), 6, axis=0)
        q = (np.arange(n - 1)[:, None] * 6 + np.array([0, 2, 4])[None, :]).ravel()
        idx = np.column_stack([q, q + 6, q + 1, q + 1, q + 6, q + 7]).reshape(-1, 3)
        col = np.zeros((n, 6, 4))
        i0 = int(np.clip(r["s"] / STEP, 0, self.n - 1))
        col[:, 2:4, :3] = (.47, .43, .35) if r["surf"] else self.asphalt[i0] * 1.08
        col[:, 2:4, 3] = r["mark"]
        st = 16 if r["bridge"] else 4  # Bankettfarbe nur alle ~40 m aus dem Gelände (teuer), dazwischen gehalten
        e = np.arange(0, n, st)
        F = self.field(np.r_[x[e] + lx[e] * sh, x[e] - lx[e] * sh], np.r_[z[e] + lz[e] * sh, z[e] - lz[e] * sh])
        m_ = np.minimum(np.arange(n) // st, len(e) - 1)
        col[:, 0:2, :3], col[:, 4:6, :3] = F["rgb"][:len(e)][m_, None], F["rgb"][len(e):][m_, None]
        su = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
        uv = np.column_stack([np.tile(-off, n), np.repeat(su, 6)])
        uv2 = np.tile([hw, -1.0 - r["surf"]], (n * 6, 1))
        parts = [(pos, nrm, idx, col.reshape(-1, 4), uv, uv2)]
        if fill is not None:
            fv, fi = fill
            fc = np.tile(np.r_[col[0, 2, :3], 0.0], (len(fv), 1))  # Farbe der Fahrbahn, keine Markierung
            fu = np.column_stack([np.full(len(fv), hw), np.zeros(len(fv))])  # |u| = hw: kein Grasstreifen (Feldweg)
            parts.append((fv, np.tile([0, 1.0, 0], (len(fv), 1)), fi, fc, fu, np.tile([hw + 20, -1.0 - r["surf"]], (len(fv), 1))))
        if r["bridge"]:
            parts.append(self._bridge_parts(r, x, z, h, lx, lz, tx, tz, sh))
        o, out = 0, []
        for pp in parts:
            out.append((pp[0], pp[1], pp[2] + o) + pp[3:])
            o += len(pp[0])
        res = [np.concatenate([pp[c] for pp in out]) for c in range(6)]
        return tuple(res) + (np.zeros((len(res[0]), 4)),)

    def _mouth_fill(self, r, pos, tx, tz, h, hw):
        """Ausgerundete Ecken der Mündung: je Seite ein Fächer von der Ecke (Kante Hauptstraße × Kante Nebenstraße) über
        einen Bogen (quadratische Bézierkurve, Kontrollpunkt = Ecke) von R entlang der Hauptkante bis R in die Nebenstraße.
        Höhe am Bogen: Hauptfahrbahn dort bzw. Nebenstraße dort (+5 cm wie die Fahrbahnen). -> (Punkte, Dreiecke)"""
        R = self._flare(r)
        if r["parent"] is None:
            i = int(r["s"] / STEP)
            ex, ez = math.cos(self.theta[i]), math.sin(self.theta[i])
            def surf_y(px, pz):  # Hauptfahrbahn an der Kante (mit Quergefälle) + 1 cm
                s_, t_, _, _ = self.nearest(px, pz)
                j_ = np.clip((s_ / STEP).astype(int), 0, self.n - 1)
                return self.edge_y(j_, np.sign(t_), np.abs(t_)) + .01
        else:
            pr, j = r["parent"]
            ex, ez = pr["x"][j + 1] - pr["x"][j], pr["z"][j + 1] - pr["z"][j]
            L = math.hypot(ex, ez)
            ex, ez = ex / L, ez / L
            u0 = np.concatenate([[0], np.cumsum(np.hypot(np.diff(pr["x"]), np.diff(pr["z"])))])
            surf_y = lambda px, pz: np.interp(u0[j] + (px - pr["x"][j]) * ex + (pz - pr["z"][j]) * ez, u0, pr["h"]) + .06
        c0 = np.array([r["x"][0], r["z"][0]])
        hs = np.interp(R, [0, 10], [h[0], h[1]]) + .05  # Nebenstraße R weiter
        V, I = [], []
        for c in (1, 4):  # linke / rechte Asphaltkante
            C = np.array([pos[0, c, 0], pos[0, c, 2]])
            away = np.sign((C - c0) @ np.array([ex, ez])) or 1.0
            A = C + np.array([ex, ez]) * away * R / max(abs(tx[0] * ez - tz[0] * ex), 0.4)  # spitzer Winkel: länger
            B = C + np.array([tx[0], tz[0]]) * R
            t = np.linspace(0, 1, 9)[:, None]
            arc = (1 - t) ** 2 * A + 2 * t * (1 - t) * C + t ** 2 * B
            ya = surf_y(np.array([A[0]]), np.array([A[1]]))[0]
            ys = (1 - t[:, 0]) ** 2 * ya + 2 * t[:, 0] * (1 - t[:, 0]) * (h[0] + .06) + t[:, 0] ** 2 * hs
            o = len(V)
            V.append([C[0], h[0] + .06, C[1]])
            V += [[p[0], y, p[1]] for p, y in zip(arc, ys)]
            I += [[o, o + k, o + k + 1] for k in range(1, 9)]
        return np.array(V), np.array(I)

    def _bridge_parts(self, r, x, z, h, lx, lz, tx, tz, sh):
        """Beton der Brücke über die Hauptstraße: Deckseiten (0,9 m), Untersicht, Brüstungen (1 m) über die Spannweite,
        zwei Pfeiler neben der Hauptstraße. Spannweite = wo die Hauptstraßen-Böschung (1:1) unter dem Deck liegt."""
        _, t, hr, hwm = self.nearest(x, z)
        span = np.abs(t) < hwm + BENCH + 9
        if not span.any():
            return tuple(np.zeros((0, 3)) for _ in range(2)) + (np.zeros((0, 3), int), np.zeros((0, 4)), np.zeros((0, 2)), np.zeros((0, 2)))
        j0, j1 = int(np.argmax(span)), int(len(span) - np.argmax(span[::-1]) - 1)
        V, N_, I = [], [], []

        def quad(a, b, c, d, nrm):
            o = len(V)
            V.extend([a, b, c, d])
            N_.extend([nrm] * 4)
            I.extend([[o, o + 1, o + 2], [o, o + 2, o + 3]])
        up = np.array([0, 1.0, 0])
        for j in range(max(j0 - 2, 0), min(j1 + 2, len(x) - 1)):
            for sd in (1, -1):
                P0 = lambda jj, w, y: np.array([x[jj] + lx[jj] * w * sd, h[jj] + y, z[jj] + lz[jj] * w * sd])
                side_n = np.array([lx[j] * sd, 0, lz[j] * sd])
                if j0 <= j < j1:  # Deckseite
                    quad(P0(j, sh, .05), P0(j + 1, sh, .05), P0(j + 1, sh, -.9), P0(j, sh, -.9), side_n)
                # Brüstung: außen, innen, oben
                for w, nn in ((sh, side_n), (sh - 0.3, -side_n)):
                    quad(P0(j, w, .05), P0(j + 1, w, .05), P0(j + 1, w, 1.05), P0(j, w, 1.05), nn)
                quad(P0(j, sh, 1.05), P0(j + 1, sh, 1.05), P0(j + 1, sh - 0.3, 1.05), P0(j, sh - 0.3, 1.05), up)
            if j0 <= j < j1:  # Untersicht
                Pu = lambda jj, sd: np.array([x[jj] + lx[jj] * sh * sd, h[jj] - .9, z[jj] + lz[jj] * sh * sd])
                quad(Pu(j, 1), Pu(j + 1, 1), Pu(j + 1, -1), Pu(j, -1), -up)
        for side in (1, -1):  # Pfeiler 2,2 m neben der Fahrbahnkante (Lage zwischen den Punkten interpoliert)
            dd = t - side * (hwm + 2.2)
            ks = np.flatnonzero((np.sign(dd[:-1]) != np.sign(dd[1:])) & span[:-1])
            if not len(ks):
                continue
            jj = int(ks[np.argmin(np.abs(t[ks]))])
            f = dd[jj] / (dd[jj] - dd[jj + 1])
            cx, cz = x[jj] + f * (x[jj + 1] - x[jj]), z[jj] + f * (z[jj + 1] - z[jj])
            top = h[jj] + f * (h[jj + 1] - h[jj]) - 0.9
            bot = hr[jj] - 0.6
            ax, az, bx, bz = tx[jj] * 0.5, tz[jj] * 0.5, lx[jj] * sh * 0.8, lz[jj] * sh * 0.8
            cs = [np.array([cx + sa * ax + sb * bx, 0, cz + sa * az + sb * bz]) for sa, sb in ((1, 1), (-1, 1), (-1, -1), (1, -1))]
            for e in range(4):
                p, q_ = cs[e], cs[(e + 1) % 4]
                mid = (p + q_) / 2 - np.array([cx, 0, cz])
                nn = mid / (np.linalg.norm(mid) + 1e-9)
                quad(p + up * bot, q_ + up * bot, q_ + up * top, p + up * top, nn)
        V, N_, I = np.array(V), np.array(N_), np.array(I)
        col = np.tile([.6, .59, .56, 0.0], (len(V), 1))
        return V, N_, I, col, np.zeros((len(V), 2)), np.tile([1.0, -1.0], (len(V), 1))

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
        mouths = [(r["s"], r["side"]) for r in self.sides if not r["bridge"] and r["parent"] is None]
        for s in np.arange(math.ceil(s0 / 50) * 50, s1, 50.0):
            if not inside(s):
                for sd in (1, -1):
                    if any(abs(s - ms) < 18 and msd == sd for ms, msd in mouths):
                        continue
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

    def snow_line(self):
        """Schneegrenze (Welt-y) je Straßenpunkt: Biom-Abstand über dem Gelände; dazu Gipfel im Schnee: wo ein großer Anstieg
        (> 300 m in ±8 km) in einem Schnee-Biom liegt, beginnt der Schnee cap m unter seinem Gipfel (höchster Punkt in ±8 km)
        -> nur das oberste Stück weiß, Regen fällt dort als Schnee (weather.gd), oben nur Nadelbäume (_plants)."""
        if getattr(self, "_snow", None) is None:
            snow = self.Pfull["h"] + self.bw @ np.array([BIOMES[b]["snow"] for b in BIOMES])
            top, low = maximum_filter1d(self.h, 3201, mode="nearest"), minimum_filter1d(self.h, 3201, mode="nearest")
            capw = self.bw @ np.array([1.0 if BIOMES[b].get("cap") else 0.0 for b in BIOMES])
            cap = np.where((top - low > 300) & (capw > 0.5), top - BIOMES["alpen"]["cap"], np.inf)
            self._snow = np.minimum(snow, cap)
        return self._snow

    def write_meta(self, out):
        self.atlas.save(out / "signs.png")
        np.column_stack([self.x, self.h, self.z]).astype(np.float32).tofile(out / "route.pos")
        sel = np.arange(0, self.n, 50)  # Atmosphäre alle 250 m, main.gd interpoliert nach der Fahrerposition
        sky = lambda k: self.bw[sel] @ np.array([BIOMES[b]["sky"][k] for b in BIOMES])
        snow = self.snow_line()[sel]
        start = self.regions[0]["biome"]
        meta = {"name": (self.w["name"] if self.w else "Freie Fahrt") + " · " + ("Reise" if self.theme not in BIOMES else BIOMES[self.theme]["name"]),
                "world": self.key, "version": VERSION, "seed": self.seed, "loop": False, "length_m": float(self.length),
                "start": START, "sun": list(BIOMES[start]["sun"]), "elev0": self.elev0, "procedural": True,
                "atmo": {"step": 250.0, "arid": sky("arid").round(3).tolist(), "fog": (sky("fog") * (1 - 0.45 * self.Pfull["pk"][sel])).round(3).tolist(),
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
    return (w, a.seed, theme, a.drama, a.density, a.variation, a.profile)


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
        # ponytail: in Serpentinen-Blöcken bis 0,3 m Spalt unter der Bankettkante (die ebene Trasse eines nahen, tieferen Astes
        # oder Kurventeils reicht darunter; seit die Äste unregelmäßig sind auch auf einem Ast); schmalere Trasse dort, falls
        # es im Bild auffällt
        tol = np.where((np.abs(A.kappa[i]) > 1 / R_MIN + 1e-6) | inb[i], 0.3, 0.1)
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
        ra = np.fromfile(d1 / "r8.pos", np.float32).reshape(-1, 3)[-6:]
        rb = np.fromfile(d1 / "r9.pos", np.float32).reshape(-1, 3)[:6]
        assert np.array_equal(ra, rb)
        # Feine Kachel: Gelände stetig (keine Stufen > 1,5 m zwischen 2-m-Nachbarn abseits steiler Flanken)
        p = pa.reshape(65, 65, 3)
        assert np.abs(np.diff(p[:, :, 1], axis=0)).max() < 4 and np.abs(np.diff(p[:, :, 1], axis=1)).max() < 4
    # Gelände an Serpentinen (VERSION 12): keine Knicklinien/Sprünge im Talboden zwischen den Ästen (Querlinien in 0,5 m),
    # keine aufgefüllten Senken (Erosion hebt nirgends um mehr als Ablagerung), Quergefälle begrenzt
    i0, i1 = A.blocks[1]
    k = (i0 + i1) // 2
    a_, t_ = np.meshgrid(np.linspace(-200, 200, 9), np.arange(-250, 250, 0.5), indexing="ij")
    dx, dz = math.cos(A.theta[k]), math.sin(A.theta[k])
    F = A.field((A.x[k] + dx * a_ + A.lx[k] * t_).ravel(), (A.z[k] + dz * a_ + A.lz[k] * t_).ravel(), color=False)
    assert np.abs(np.diff(F["h"].reshape(a_.shape), axis=1)).max() < 1.2, np.abs(np.diff(F["h"].reshape(a_.shape), axis=1)).max()
    assert F["E"]["dh"].max() < 40, F["E"]["dh"].max()
    assert np.abs(A.bank).max() <= 0.04 + 1e-9 and np.abs(A.bank[np.abs(A.kappa) < 1e-4]).max() < 0.02
    # Ferne/mittlere Kachel an einer 1024-m-Linie fern der Straße: gleiche Kante (keine Schürzenwand)
    e = np.arange(0, 1024, 8.0)
    xs, zs = np.full_like(e, (math.floor(A.x[k] / FAR[0]) + 1) * FAR[0]), math.floor(A.z[k] / FAR[0]) * FAR[0] + e
    far_ = A.field(xs, zs, color=False)["d"] > 460
    if far_.any():
        assert np.abs(mesh_h(A, xs[far_], zs[far_], MID[1]) - mesh_h(A, xs[far_], zs[far_], FAR[1])).max() < 0.05
    # Stream-Fenster: Pause (gleiches s), Sprung zurück auf den Start, schnelles Fahren -> gleiche Elemente je s
    assert A.items(5000.0) == A.items(5000.0)
    w0, w1 = set(A.items(START)), set(A.items(START + 1500))
    assert "r0" in w0 and "r0" not in w1 and any(k[0] == "n" for k in w1)
    # Der Fahrer steht immer auf einer feinen Kachel (auch wo die Kachelmitte näher an einem fernen Straßenteil liegt)
    for sr in np.arange(START, START + A.plan_len, 120.0):
        k = int(sr / STEP)
        assert f"n{math.floor(A.x[k] / NEAR[0])}_{math.floor(A.z[k] / NEAR[0])}" in A.items(sr), sr
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
    # Geländeprofil: flach bleibt flach, Anstieg steigt durchgehend, Panorama hat den Gipfel vor der Mitte der Strecke und kommt
    # wieder herunter, wellig geht auf und ab ohne Höhengewinn; Gipfel großer Alpen-Anstiege liegen im Schnee (Atmosphäre)
    for prof, chk in [("flach", lambda h, g: np.percentile(np.abs(g), 95) < 0.015),
                      ("anstieg", lambda h, g: np.median(g) > 0.05 and h[-1] > 500),
                      ("panorama", lambda h, g: 0.1 < np.argmax(h) / len(h) < 0.5 and h[-1] < 0.3 * h.max()),
                      ("wellig", lambda h, g: g.max() > 0.03 and g.min() < -0.03 and abs(h[-1]) < 60)]:
        Pw = World(w, 7, "alpen", profile=prof)
        i0 = int(START / STEP)
        hh, gg = Pw.h[i0:i0 + int(Pw.plan_len / STEP)], Pw.grade[i0:i0 + int(Pw.plan_len / STEP)]
        assert chk(hh, gg), (prof, hh.max(), hh[-1], np.percentile(gg, [5, 50, 95]))
        if prof == "panorama":
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                sn = np.array(Pw.write_meta(Path(td))["atmo"]["snow"])
            top = int(np.argmax(Pw.h) / 50)
            assert sn[top] < Pw.h[np.argmax(Pw.h)] - 100, "Gipfel im Schnee"
            # Gipfel mit Rundblick: im Ring 700/1100 m liegt das Gelände fast überall tiefer als die Straße oben
            it_ = int(np.argmax(Pw.h))
            ang_ = np.linspace(0, 2 * np.pi, 32, endpoint=False)
            for rr in (700.0, 1100.0):
                Fr_ = Pw.field(Pw.x[it_] + rr * np.cos(ang_), Pw.z[it_] + rr * np.sin(ang_), color=False, res=8.0)
                assert np.mean(Fr_["h"] < Pw.h[it_]) >= 0.7, ("Gipfel zwischen höheren Bergen", rr, np.mean(Fr_["h"] < Pw.h[it_]))
            mid = int(np.argmax(Pw.h) * 0.3)
            assert sn[mid // 50] > Pw.h[mid] + 100, "Schnee nur oben, nicht schon im unteren Teil"
    assert World(w, 7, "alpen", profile="flach").key != World(w, 7, "alpen").key
    # Nebenstraßen: vorhanden, Brücken, Abstand zur Hauptstraße, bündige Mündung, Steigung, Durchfahrtshöhe, Gelände nicht darüber
    G = World(workout.load(Path(__file__).parent / "workouts" / "grundlage_60.zwo"), 3, "wald")
    assert len(G.sides) > 15 and any(r["bridge"] for r in G.sides), len(G.sides)
    for r in G.sides:
        seg = np.hypot(np.diff(r["x"]), np.diff(r["z"]))
        assert (np.abs(np.diff(r["h"])) / seg).max() < 0.115, "Nebenstraße zu steil"
        if r["bridge"]:
            _, t, hr, hwm = G.nearest(r["x"], r["z"])
            over = np.abs(t) < hwm + 0.5
            assert over.any() and (r["h"][over] - 0.9 - hr[over]).min() >= 5.5, "Durchfahrtshöhe unter der Brücke"
            m = G._side_mesh(r)
            foot = m[0][(m[3][:, 3] == 0) & (m[5][:, 0] == 1.0)]
            foot = foot[foot[:, 1] < foot[:, 1].max() - 3]
            _, tf, _, hf = G.nearest(foot[:, 0], foot[:, 2])
            assert len(foot) == 16 and (np.abs(tf) - hf).min() > 1.0, "Pfeiler neben, nicht auf der Fahrbahn"
            continue
        assert G.tree.query(np.column_stack([r["x"][10:], r["z"][10:]]))[0].min() > 69, "Nebenstraße kommt zurück"
        if r["parent"] is None:
            i_ = int(r["s"] / STEP)
            assert abs(r["h"][0] + 0.05 - G.edge_y(i_, r["side"], G.hw[i_] - 0.1)) < 1e-6, "Mündung bündig mit der Fahrbahnkante"
        k = slice(8, 30, 3)
        Fs = G.field(r["x"][k], r["z"][k], color=False, res=2.0)
        assert (Fs["h"] <= r["h"][k] - SINK + 0.05).all(), ("Gelände über der Nebenstraße", (Fs["h"] - r["h"][k]).max())
    Fr = World(None, 1, "mittelmeer")
    assert Fr.length >= 150000 and Fr.regions[-1]["s1"] >= Fr.length - 1
    print("ok")


def mesh_h(world, px, pz, res):
    """Höhe des Kachel-Meshes mit Rasterweite res an Punkten (gleiche Dreiecksteilung wie tile()): was man sieht, nicht field."""
    c, r = np.floor(px / res), np.floor(pz / res)
    fx, fz = px / res - c, pz / res - r
    qx, qz = np.r_[c, c + 1, c, c + 1] * res, np.r_[r, r, r + 1, r + 1] * res
    F = world.field(qx, qz, color=False, res=res)
    h = (world._far_edge(qx, qz, F["h"], F["d"]) if res == MID[1] else F["h"]).reshape(4, -1)
    h00, h01, h10, h11 = h  # 01 = eine Spalte weiter (x), 10 = eine Zeile weiter (z)
    lo = h00 + fx * (h01 - h00) + fz * (h10 - h00)
    hi = h11 + (1 - fx) * (h10 - h11) + (1 - fz) * (h01 - h11)
    return np.where(fx + fz <= 1, lo, hi)


def diag(world, s_list):
    """Messwerte gegen Geometriefehler (uv run world.py diag …): Gelände (Neigung, Stufen, Auffüllung), Straße (Steigung,
    Anschluss ans Gelände, Einschnitt/Damm), Streaming (Lücken an LOD-Grenzen = sichtbare Schürzen, Straße auf groben
    Kacheln, Zeiten). Ausgabe: eine Zeile je Messung, Grenzwerte in Klammern."""
    W = world
    say = lambda k, v: print(f"  {k:34s} {v}")
    i = np.arange(int(START / STEP), W.n - int(2000 / STEP))
    g10 = np.diff(W.h[::2]) / 10
    print("Straße")
    say("Steigung max / p99 (≤ G_MAX)", f"{np.abs(g10).max() * 100:.1f} % / {np.percentile(np.abs(g10), 99) * 100:.1f} %  ({G_MAX * 100:.0f} %)")
    say("Steigungsänderung je 10 m max", f"{np.abs(np.diff(g10)).max() * 100:.2f} %-Pkt")
    pairs = W.tree.query_pairs(12.0, output_type="ndarray")
    say("Straße kreuzt sich selbst (< 12 m)", int((np.abs(pairs[:, 0] - pairs[:, 1]) * STEP > 60).sum()) if len(pairs) else 0)
    j = i[::4]
    N = W._macro(W.x[j], W.z[j])["N"]
    cf = W.h[j] - N
    say("Straße − Gelände p1/p50/p99 (Damm +)", " / ".join(f"{v:.1f}" for v in np.percentile(cf, [1, 50, 99])) + " m")
    gap, bur = [], []
    for o in (-1.0, 0.0, 1.0):
        F = W.field(W.x[j] + W.lx[j] * o * (W.hw[j] + 0.35), W.z[j] + W.lz[j] * o * (W.hw[j] + 0.35), color=False)
        bur.append((F["h"] - (W.h[j] - SINK)).max())
        gap.append((W.h[j] - SINK - F["h"]).max())
    say("Gelände über Trasse / Spalt max", f"{max(bur):.2f} / {max(gap):.2f} m")
    print("Gelände (je 1,2 km Quadrat um die Straße, 4 m)")
    for s in s_list:
        k = int(np.clip(s / STEP, 0, W.n - 1))
        ax = W.x[k] - 600 + np.arange(300) * 4.0
        az = W.z[k] - 600 + np.arange(300) * 4.0
        GX, GZ = np.meshgrid(ax, az)
        F = W.field(GX.ravel(), GZ.ravel(), color=False, res=4.0)
        H = F["h"].reshape(GX.shape)
        gz, gx = np.gradient(H, 4.0)
        S = np.hypot(gx, gz)[F["d"].reshape(GX.shape) > F["hw"].reshape(GX.shape) + 20]
        # Sprung: größter Höhenunterschied auf 0,5 m entlang 7 Querlinien über 600 m (Unstetigkeit statt steiler Flanke)
        a_, t_ = np.meshgrid(np.linspace(-150, 150, 7), np.linspace(-300, 300, 1201), indexing="ij")
        dx, dz = math.cos(W.theta[k]), math.sin(W.theta[k])
        hh = W.field((W.x[k] + dx * a_ + W.lx[k] * t_).ravel(), (W.z[k] + dz * a_ + W.lz[k] * t_).ravel(), color=False)["h"]
        step = np.abs(np.diff(hh.reshape(a_.shape), axis=1)).max()
        say(f"km {s / 1000:5.1f}: Neigung p50/p90/p99/max", f"{np.percentile(S, 50):.2f} / {np.percentile(S, 90):.2f} / {np.percentile(S, 99):.2f} / "
            f"{S.max():.2f}   > 1,5: {100 * (S > 1.5).mean():.1f} %   Sprung/0,5 m {step:.2f} m   Erosion dh {F['E']['dh'].min():.0f}…{F['E']['dh'].max():.0f} m")
    print("Streaming (Kacheln wie im Spiel)")
    rs = {"n": NEAR[1], "m": MID[1], "f": FAR[1]}
    sz = {"n": NEAR[0], "m": MID[0], "f": FAR[0]}
    for s in s_list:
        want = W.items(s)
        tiles = {k for k in want if k[0] in "nmf"}

        def lod_at(x, z):
            out = np.full(len(x), "", object)
            for c in "nmf":
                keys = np.array([f"{c}{a}_{b}" for a, b in zip(np.floor(x / sz[c]).astype(int), np.floor(z / sz[c]).astype(int))])
                out = np.where((out == "") & np.isin(keys, list(tiles)), c, out)
            return out
        gaps = {}
        for key in tiles:
            c = key[0]
            a, b = map(int, key[1:].split("_"))
            e = np.arange(0, sz[c], NEAR[1]) + 0.0
            x0, z0 = a * sz[c], b * sz[c]
            for ex, ez, ox, oz in ((x0 + e, np.full_like(e, z0), 0, -1), (x0 + e, np.full_like(e, z0 + sz[c]), 0, 1),
                                   (np.full_like(e, x0), z0 + e, -1, 0), (np.full_like(e, x0 + sz[c]), z0 + e, 1, 0)):
                nb = lod_at(ex + ox * 0.5, ez + oz * 0.5)
                for c2 in set(nb) - {"", c}:
                    if rs[c2] >= rs[c]:
                        continue  # jede Grenze einmal, von der groben Seite
                    m = nb == c2
                    d_ = np.abs(mesh_h(W, ex[m], ez[m], rs[c]) - mesh_h(W, ex[m], ez[m], rs[c2]))
                    gaps.setdefault(c + c2, []).append(d_)
        txt = "  ".join(f"{k}: p99 {np.percentile(np.concatenate(v), 99):.1f} max {np.concatenate(v).max():.1f} m" for k, v in sorted(gaps.items()))
        say(f"km {s / 1000:5.1f}: LOD-Grenzen (Schürze)", txt or "keine")
        ii = np.arange(int(s / STEP), int(min(s + AHEAD, W.length - 1) / STEP), 2)
        lod = lod_at(W.x[ii], W.z[ii])
        txt = []
        for c in "nmf":
            m = lod == c
            if m.any():
                d_ = mesh_h(W, W.x[ii][m], W.z[ii][m], rs[c]) - (W.h[ii][m] + 0.05)
                txt.append(f"{c}: {m.sum()} Pkt, Gelände über Fahrbahn max {d_.max():.2f}, darunter max {-d_.min():.2f} m")
        say(f"km {s / 1000:5.1f}: Straße auf Kacheln", "; ".join(txt))
    t0 = time.time()
    k = sorted((p, k) for k, p in W.items(s_list[0]).items())
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for c in "nmf":
            key = next((kk for _, kk in k if kk[0] == c), None)
            if key:
                t0 = time.time()
                W.build(key, Path(td))
                say(f"Kachel {key} bauen", f"{(time.time() - t0) * 1000:.0f} ms")
    say("Erosionsregionen", f"{W.ero.made}, Ø {W.ero.ms / max(W.ero.made, 1):.0f} ms")


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
    ap.add_argument("cmd", nargs="?", choices=["stream", "preview", "info", "diag"])
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--workout")
    ap.add_argument("--ftp", type=float, default=None, help="nur für .erg-Dateien ohne FTP im Kopf")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--biome", default="reise")
    ap.add_argument("--drama", type=float, default=1.0)
    ap.add_argument("--density", type=float, default=1.0)
    ap.add_argument("--variation", type=float, default=1.0)
    ap.add_argument("--profile", default="auto", choices=list(PROFILES))
    ap.add_argument("--png")
    ap.add_argument("--km", help="diag: Stellen in km, z. B. 2,6,10")
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
    elif a.cmd == "diag":
        wd = from_args(a)
        diag(wd, [float(v) * 1000 for v in (a.km or "2,6,10").split(",")])
    elif a.cmd == "info":
        wd = from_args(a)
        for r in wd.regions[:40]:
            print(f"{r['s0'] / 1000:6.2f}–{r['s1'] / 1000:6.2f} km  {r['t0'] / 60:5.1f}–{r['t1'] / 60:5.1f} min  {r['kind']:9s} {r['scene']:8s} {r['biome']}")
        print(f"Workout {wd.plan_len / 1000:.1f} km, Strecke {wd.length / 1000:.0f} km, {len(wd.villages)} Orte, {len(wd.summits)} Pässe, Welt {wd.key}")
    else:
        ap.print_help()
