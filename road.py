"""Straßenführung für route.py: Route säubern, Linie glätten, OSM-Eigenschaften je Routenpunkt, Höhenprofil.

  clean_route     Stichstraßen entfernen (BRouter fährt zu einem Wegpunkt neben der Straße hin und zurück)
  smooth_line     Knicke aus GPX/OSM-Polylinien runden (Mindestradius), höchstens max_dev von der echten Straße weg
  match_ways      je Routenpunkt die befahrene OSM-Straße: Klasse, Breite, Belag, Markierung, Brücke, Tunnel
  road_profile    Höhen wie eine gebaute Straße; Brücken und Tunnel werden überspannt statt dem Gelände zu folgen
  junctions       Einmündungen/Kreuzungen: wo andere Fahrstraßen unsere Fahrbahn verlassen (Randlinie unterbrechen, Schilder)
Alle Funktionen arbeiten auf Punkten im Abstand STEP (route.resample) und sind deterministisch.
"""
import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import spsolve
from scipy.spatial import cKDTree

STEP = 5.0
MIN_HW = 2.2  # halbe Fahrbahnbreite mindestens: zwei Fahrer nebeneinander (±1,2 m)
# Belag -> Code: 0 glatt (Asphalt/Beton), 1 Pflaster, 2 unbefestigt. Im Mesh als UV2.y = uv2_code(code) = -1 - code,
# damit alte Pakete (UV2.y = halbe Breite > 0) im Shader weiter als Asphalt erkannt werden.
SURFACE = {"asphalt": 0, "paved": 0, "concrete": 0, "concrete:plates": 0, "concrete:lanes": 0, "chipseal": 0, "metal": 0, "wood": 0,
           "paving_stones": 1, "sett": 1, "cobblestone": 1, "unhewn_cobblestone": 1, "bricks": 1, "grass_paver": 1,
           "compacted": 2, "fine_gravel": 2, "gravel": 2, "pebblestone": 2, "unpaved": 2, "dirt": 2, "ground": 2, "earth": 2,
           "grass": 2, "sand": 2, "mud": 2, "rock": 2}
COLOR = {"asphalt": (.22, .22, .24), "concrete": (.50, .50, .48), "paving_stones": (.56, .53, .49), "sett": (.44, .42, .40),
         "cobblestone": (.44, .42, .40), "compacted": (.58, .52, .42), "fine_gravel": (.60, .55, .45), "gravel": (.55, .51, .45),
         "dirt": (.42, .34, .24), "ground": (.42, .34, .24), "earth": (.42, .34, .24), "grass": (.36, .44, .26), "sand": (.74, .66, .50)}
MAJOR = {"motorway", "trunk", "primary", "secondary"}
MINOR = {"footway", "path", "cycleway", "steps", "pedestrian", "bridleway"}  # verliert gegen eine Fahrstraße daneben (GPS-Rauschen)


def uv2_code(code):
    return -1.0 - np.asarray(code, float)  # Mittel- und Randlinien
MINOR_MARKED = {"tertiary", "unclassified"}  # nur Mittellinie (wenn breit genug)
UNPAVED_CLASS = {"track", "path", "bridleway"}


def clean_route(x, z, loop, max_len=300.0, gap=6.0):
    """Stichstraßen raus: Wo die Route umkehrt und auf sich selbst zurückläuft (Hin- und Rückweg < gap auseinander,
    zusammen bis 2×max_len), wird das Stück ausgeschnitten. Serpentinen bleiben (Äste gehen schnell auseinander),
    lange gewollte Hin-und-zurück-Strecken (Passstraße hoch und runter) auch."""
    keep = np.ones(len(x), bool)
    h = np.unwrap(np.arctan2(np.diff(z), np.diff(x)))
    turn = np.abs(np.concatenate([[0, 0], h[2:] - h[:-2], [0]]))  # Richtungsänderung über 2 Segmente am Punkt i
    for i in np.argsort(-turn):
        if turn[i] < np.radians(120):
            break
        if not keep[i]:
            continue
        k = 1
        while i - k >= 0 and i + k < len(x) and k * STEP < max_len and np.hypot(x[i - k] - x[i + k], z[i - k] - z[i + k]) < gap:
            k += 1
        if 15 <= k * STEP < max_len and i - k >= 0 and i + k < len(x):  # 15 m bis max_len, Äste haben sich getrennt
            keep[i - k + 2:i + k - 1] = False  # Anschlusspunkte bleiben, Lücke < gap
    if not loop:
        keep[[0, -1]] = True
    return x[keep], z[keep]


def smooth_line(x, z, loop, max_dev=2.5, hairpin_dev=6.0, lam=40.0):
    """Knicke runden: Whittaker-Glättung beider Koordinaten, danach höchstens max_dev von der Originallinie (bleibt auf der
    Fahrbahn). Ein 90°-Knick wird so zu einem Bogen mit ~6 m Radius. In Kehren (> 120° auf 40 m) bis hairpin_dev, sonst
    bleibt die Spitze der OSM-Linie ein 3-m-Radius, den kein Auto fahren könnte."""
    n = len(x)
    h = np.unwrap(np.arctan2(np.diff(z), np.diff(x)))
    h = np.r_[h, h[-1]]
    turn = np.abs(np.roll(h, -4) - np.roll(h, 4)) if loop else np.abs(np.r_[h[4:], [h[-1]] * 4] - np.r_[[h[0]] * 4, h[:-4]])
    lim = np.where(ndimage.maximum_filter1d((turn > np.radians(120)).astype(float), 9, mode="wrap" if loop else "nearest") > 0,
                   hairpin_dev, max_dev)
    i = np.arange(n if loop else n - 2)
    D = sparse.csr_matrix((np.tile([1., -2., 1.], len(i)), (np.repeat(i, 3), (i[:, None] + np.arange(3)).ravel() % n)), shape=(len(i), n))
    w = np.ones(n)
    if not loop:
        w[[0, -1]] = 1e6  # Start und Ziel bleiben, wo sie sind
    sx, sz = x, z
    w = np.where(lim > max_dev, 0.01, w)  # Kehren deutlich weicher (Glättung ~40 m statt ~15 m): Bogen statt Spitze
    for _ in range(4):  # wo zu weit weg: Originalpunkt stärker gewichten, neu glätten
        A = (sparse.diags(w) + lam * (D.T @ D)).tocsc()
        sx, sz = spsolve(A, w * x), spsolve(A, w * z)
        dev = np.hypot(sx - x, sz - z)
        if (dev <= lim).all():
            break
        w = np.where(dev > lim * 0.9, w * 8, w)
    dev = np.hypot(sx - x, sz - z)
    f = np.minimum(1, lim / np.maximum(dev, 1e-9))  # Rest hart begrenzen
    return x + (sx - x) * f, z + (sz - z) * f


def resample(x, z, loop):
    """Gleichmäßig STEP Meter (nach dem Ausschneiden/Glätten wieder nötig)."""
    if loop:
        x, z = np.append(x, x[0]), np.append(z, z[0])
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
    s = np.arange(0, d[-1] - (STEP / 2 if loop else 0), STEP)
    return np.interp(s, d, x), np.interp(s, d, z)


def curvature(x, z, loop):
    """Vorzeichenbehaftete Krümmung 1/m je Punkt (links positiv)."""
    if loop:
        xp, zp = np.r_[x[-1], x, x[0]], np.r_[z[-1], z, z[0]]
    else:
        xp, zp = np.r_[2 * x[0] - x[1], x, 2 * x[-1] - x[-2]], np.r_[2 * z[0] - z[1], z, 2 * z[-1] - z[-2]]
    a, b, c = np.column_stack([xp[:-2], zp[:-2]]), np.column_stack([xp[1:-1], zp[1:-1]]), np.column_stack([xp[2:], zp[2:]])
    cr = (b - a)[:, 0] * (c - b)[:, 1] - (b - a)[:, 1] * (c - b)[:, 0]
    ab, bc, ca = (np.linalg.norm(v, axis=1) for v in (b - a, c - b, a - c))
    return -2 * cr / (ab * bc * ca + 1e-9)  # z zeigt nach Süden -> Vorzeichen drehen, damit links positiv


def _mode(codes, win, loop):
    """Häufigster Wert im gleitenden Fenster (gegen Flackern an Kreuzungen); Speicher n × win², nicht n × Kategorien."""
    w = np.lib.stride_tricks.sliding_window_view(np.pad(codes, win // 2, mode="wrap" if loop else "edge"), win)
    count = (w[:, :, None] == w[:, None, :]).sum(1)
    return w[np.arange(len(w)), count.argmax(1)]


def _clean_flags(flag, loop, close=5, open_=3):
    """Lücken schließen, Einzeltreffer weg – mit Rand-Padding, sonst verlieren Start/Ende auf einer Brücke ihr Flag."""
    pad = close
    f = np.pad(flag, pad, mode="wrap" if loop else "edge")
    f = ndimage.binary_opening(ndimage.binary_closing(f, np.ones(close)), np.ones(open_))
    return f[pad:-pad]


def match_ways(x, z, ways, loop):
    """Eigenschaften je Routenpunkt aus der befahrenen OSM-Straße. ways = [(x, z, tags)] (alle highway-Wege, auch Tunnel).
    Treffer: nächster Wegabschnitt bis 15 m Abstand, ungefähr gleiche Richtung. Ohne Treffer: unklassifizierte Asphaltstraße.
    Rückgabe dict von Arrays: hw (halbe Breite), surface (Code), color (sRGB), mark (0/⅓/⅔/1), bridge, tunnel, cls."""
    n = len(x)
    out = {"hw": np.full(n, 2.8), "surface": np.zeros(n, int), "color": np.tile(COLOR["asphalt"], (n, 1)), "mark": np.zeros(n),
           "bridge": np.zeros(n, bool), "tunnel": np.zeros(n, bool), "cls": np.array(["unclassified"] * n, object),
           "way": np.full(n, -1)}
    P, Dir, Way = [], [], []
    minor = np.array([t.get("highway", "").removesuffix("_link") in MINOR for _, _, t in ways] + [False])
    for k, (wx, wz, _) in enumerate(ways):
        d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(wx), np.diff(wz)))])
        if d[-1] < 1:
            continue
        s = np.linspace(0, d[-1], max(2, int(d[-1] / 2) + 1))  # alle ~2 m, mindestens Anfang und Ende
        px, pz = np.interp(s, d, wx), np.interp(s, d, wz)
        dx, dz = np.gradient(px), np.gradient(pz)
        ln = np.hypot(dx, dz) + 1e-9
        P.append(np.column_stack([px, pz]))
        Dir.append(np.column_stack([dx / ln, dz / ln]))
        Way.append(np.full(len(px), k))
    if not P:
        return out
    P, Dir, Way = np.vstack(P), np.vstack(Dir), np.concatenate(Way)
    rdx, rdz = np.gradient(x), np.gradient(z)
    rl = np.hypot(rdx, rdz) + 1e-9
    dist, idx = cKDTree(P).query(np.column_stack([x, z]), k=12, distance_upper_bound=15.0)
    ok = np.isfinite(dist)
    idx = np.where(ok, idx, 0)
    cos = np.abs(Dir[idx, 0] * (rdx / rl)[:, None] + Dir[idx, 1] * (rdz / rl)[:, None])
    score = np.where(ok & (cos > 0.6), dist + 12 * (1 - cos) + 4 * minor[np.where(ok, Way[idx], -1)], np.inf)
    best = score.argmin(1)
    hit = np.isfinite(score[np.arange(n), best])
    way = np.where(hit, Way[idx[np.arange(n), best]], -1)
    # Flackern an Kreuzungen glätten: Weg-Index im 30-m-Fenster nach Mehrheit
    way = _mode(way, 7, loop)
    for i in range(n):
        if way[i] < 0:
            continue
        tags = ways[way[i]][2]
        cls = tags.get("highway", "unclassified").removesuffix("_link")
        out["cls"][i] = cls
        surf = tags.get("surface", "")
        if surf not in SURFACE:  # ohne Tag: nach Klasse / tracktype raten
            surf = "asphalt" if cls not in UNPAVED_CLASS or tags.get("tracktype") == "grade1" else "compacted"
        out["surface"][i] = SURFACE[surf]
        out["color"][i] = COLOR.get(surf, COLOR["asphalt"] if SURFACE[surf] == 0 else COLOR["paving_stones"] if SURFACE[surf] == 1 else COLOR["compacted"])
        out["hw"][i] = _halfwidth(tags, cls)
        wide = out["hw"][i] >= 2.8
        out["mark"][i] = 1.0 if cls in MAJOR and SURFACE[surf] == 0 else 1 / 3 if cls in MINOR_MARKED and wide and SURFACE[surf] == 0 else 0.0
        out["bridge"][i] = tags.get("bridge", "no") not in ("no", "")
        out["tunnel"][i] = tags.get("tunnel", "no") not in ("no", "") or tags.get("covered") == "yes"
    # Breite weich ändern (kein Sprung), Brücken/Tunnel ohne Ein-Punkt-Lücken
    out["way"] = way  # Index in ways je Routenpunkt (-1: keiner) -> Tags für Tempo, Namen
    out["hw"] = ndimage.gaussian_filter1d(out["hw"], 3, mode="wrap" if loop else "nearest")
    for key in ("bridge", "tunnel"):  # Lücken bis 20 m schließen, Einzeltreffer an Kreuzungen (< 15 m) weg
        out[key] = _clean_flags(out[key], loop)
    return out


def _halfwidth(tags, cls):
    try:
        return float(np.clip(float(tags["width"].replace("m", "").strip()) / 2, MIN_HW, 7.0))
    except (KeyError, ValueError):
        pass
    lanes = tags.get("lanes", "")
    if lanes.isdigit():
        return float(np.clip(int(lanes) * 1.6, MIN_HW, 7.0))
    return max(MIN_HW, {"motorway": 6.0, "trunk": 5.0, "primary": 4.0, "secondary": 3.5, "tertiary": 3.2, "unclassified": 2.8,
                        "residential": 2.8, "living_street": 2.5, "service": 2.3}.get(cls, MIN_HW))


# highway -> (halbe Breite, Farbe, Fahrbahnmarkierung, Höhe übers Gelände in m: wichtigere Straßen liegen oben, alle knapp drüber)
ROADS = {
    "motorway": (6.0, (.20, .20, .22), 1, .14), "trunk": (5.0, (.20, .20, .22), 1, .13), "primary": (4.0, (.22, .22, .24), 1, .12),
    "secondary": (3.5, (.23, .23, .25), 1, .11), "tertiary": (3.2, (.24, .24, .26), 1, .10), "unclassified": (2.6, (.26, .26, .27), 0, .09),
    "residential": (2.8, (.26, .26, .27), 0, .09), "living_street": (2.5, (.34, .33, .31), 0, .08), "service": (1.8, (.28, .28, .29), 0, .07),
    "pedestrian": (2.5, (.55, .52, .48), 0, .07), "track": (1.4, (.45, .38, .28), 0, .05), "cycleway": (1.0, (.40, .26, .24), 0, .06),
    "footway": (0.8, (.60, .55, .47), 0, .04), "path": (0.6, (.50, .43, .32), 0, .04), "bridleway": (0.8, (.50, .43, .32), 0, .04),
    "steps": (0.8, (.60, .60, .60), 0, .04),
}


def street_hw(tags):
    """Halbe Breite: width, sonst Fahrstreifen, sonst Klasse. Rampen (*_link) sind einstreifig, nicht so breit wie die
    Autobahn, an der sie hängen."""
    hwy = tags.get("highway", "")
    cls = hwy.removesuffix("_link")
    try:
        return float(np.clip(float(tags["width"].replace("m", "").strip()) / 2, 0.4, 12.0))
    except (KeyError, ValueError):
        pass
    lanes = tags.get("lanes", "")
    if lanes.isdigit() and int(lanes) > 0 and cls in RANK:
        one = tags.get("oneway") in ("yes", "1", "-1") or cls == "motorway" or tags.get("junction") == "roundabout"
        return float(np.clip(int(lanes) * (1.75 if one else 1.6) + (1.0 if one and cls in ("motorway", "trunk") else 0), 1.5, 12.0))
    hw = ROADS[cls][0]
    return min(hw, 3.5 if cls in ("motorway", "trunk") else 3.2) if hwy.endswith("_link") else hw



CONCRETE = (.62, .61, .58)
def corner_radius(hw):
    """Eckradius (Bordstein) an Einmündungen: network.py rundet die Ecken so, hier wird die Randlinie so weit unterbrochen."""
    return float(np.clip(hw, 1.0, 4.0))


RANK = {"motorway": 7, "trunk": 6, "primary": 5, "secondary": 4, "tertiary": 3, "unclassified": 2, "residential": 2,
        "living_street": 1, "service": 1}  # Fahrstraßen, die an Einmündungen die Randlinie unterbrechen


def junctions(x, z, hw, ways, skip):
    """Wo eine andere Fahrstraße (RANK, ebenerdig) unsere Fahrbahn verlässt -> [dict] je Einmündung, Kreuzungen = zwei.
    Felder: i (Routenindex), side (+1 links, -1 rechts), s0/s1 (Lücke der Randlinie in m entlang der Route), hw (halbe Breite
    der Nebenstraße), dir (Einheitsvektor von uns weg), at (Punkt an unserer Kante), tags. skip = Brücke|Tunnel je Punkt.
    Auch die Fortsetzung der Straße, von der wir abbiegen, ist eine Einmündung. Erkannt geometrisch (robust gegen
    unsere geglättete Linie, die ≤ 2,5 m neben den OSM-Knoten liegt): Weg-Stück innerhalb unserer Fahrbahn, das danach mindestens 20° schräg weg und 4 m über die Kante geht."""
    t = np.column_stack([np.gradient(x), np.gradient(z)])
    t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
    nl = np.column_stack([t[:, 1], -t[:, 0]])  # links (wie route.py)
    tree = cKDTree(np.column_stack([x, z]))
    found = []
    for wx, wz, tags in ways:
        cls = tags.get("highway", "").removesuffix("_link")
        if cls not in RANK or tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no" or tags.get("layer", "0") != "0":
            continue
        d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(wx), np.diff(wz)))])
        if d[-1] < 6:
            continue
        s = np.arange(0, d[-1], 1.0)
        p = np.column_stack([np.interp(s, d, wx), np.interp(s, d, wz)])
        dist, j = tree.query(p)
        rel = p - np.column_stack([x[j], z[j]])
        lat, along = (rel * nl[j]).sum(1), (rel * t[j]).sum(1)
        inside = (np.abs(lat) < hw[j] - 0.3) & (np.abs(along) < 3.5) & ~skip[j]
        for b in (1, -1):  # beide Laufrichtungen auf dem Weg: Übergang drinnen -> draußen
            for e in np.flatnonzero(inside[:-1] != inside[1:]):
                e, f = (e, e + 1) if b == 1 else (e + 1, e)  # e drinnen, f draußen
                if not inside[e] or inside[f]:
                    continue
                g = f + b * 8  # 8 m weiter auf dem Weg
                if not 0 <= g < len(p) or np.abs(lat[g]) < hw[j[g]] + 4 or dist[g] < hw[j[g]] + 4:
                    continue  # bleibt neben uns (Linie schlängelt sich um die Fahrbahn) oder zu kurz
                dv = p[g] - p[f]
                dv /= np.linalg.norm(dv) + 1e-9
                i = j[f]
                sin = abs(t[i, 0] * dv[1] - t[i, 1] * dv[0])
                if sin < 0.34:  # unter 20°: läuft neben uns her
                    continue
                side = 1 if lat[f] > 0 else -1
                hws = street_hw(tags)  # wie network.py die Einmündung baut
                half = hws / sin + corner_radius(hws)  # Einmündungsbreite längs unserer Kante + Eckradius
                c = i * STEP + along[f] + (dv @ t[i]) * hw[i] / sin  # Mitte der Öffnung an unserer Kante
                found.append(dict(i=int(i), side=side, s0=c - half, s1=c + half, hw=hws, dir=dv, tags=tags,
                                  at=np.array([x[i], z[i]]) + nl[i] * side * hw[i] + t[i] * (c - i * STEP)))
    found.sort(key=lambda m: -m["hw"])
    out = []
    for m in found:  # doppelte (zwei Wege derselben Straße, Hin/Rück) zusammenfassen
        if not any(o["side"] == m["side"] and abs((o["s0"] + o["s1"]) - (m["s0"] + m["s1"])) / 2 < 8 for o in out):
            out.append(m)
    return sorted(out, key=lambda m: m["s0"])


def bridge_mesh(x, y, z, hw, bridge, ground):
    """Brücken: Querschnitt (Brüstungen + Deckplatte, Fahrbahn macht das Straßen-Mesh) entlang der Route, Pfeiler alle
    ~30 m bis ins Gelände. -> (pos, nrm, idx, col) oder None. Normalen analytisch nach außen, Farbe Beton."""
    labels, count = ndimage.label(bridge)
    P, N, I = [], [], []

    def quads(a, b, na, nb):  # zwei parallele Kanten (je m Punkte) -> Dreiecke
        o = sum(len(v) for v in P)
        m = len(a)
        P.extend([a, b]); N.extend([na, nb])
        q = o + np.arange(m - 1)
        I.append(np.column_stack([q, q + m, q + 1, q + 1, q + m, q + m + 1]).reshape(-1, 3))

    for run in range(1, count + 1):
        sel = np.nonzero(labels == run)[0]
        sel = np.arange(max(sel[0] - 1, 0), min(sel[-1] + 2, len(x)))  # bis auf die Widerlager
        if len(sel) < 2:
            continue
        cx, cy, cz, h = x[sel], y[sel], z[sel], hw[sel]
        dx, dz = np.gradient(cx), np.gradient(cz)
        ln = np.hypot(dx, dz) + 1e-9
        lx, lz = dz / ln, -dx / ln  # links
        a, b = h + 0.5, h + 0.8
        sec = [(a, .2), (a, 1.15), (b, 1.15), (b, -1.2), (-b, -1.2), (-b, 1.15), (-a, 1.15), (-a, .2)]  # (Versatz links, Höhe)
        for (o1, y1), (o2, y2) in zip(sec, sec[1:]):
            do, dy = np.broadcast_to(o2 - o1, cx.shape), np.full(cx.shape, y2 - y1)
            nl = np.hypot(do, dy) + 1e-9
            no, ny = -dy / nl, do / nl  # außen bei dieser Umlaufrichtung
            nrm = np.column_stack([no * lx, ny, no * lz])
            v = lambda oo, yy: np.column_stack([cx + lx * oo, cy + yy, cz + lz * oo])
            quads(v(o1, y1), v(o2, y2), nrm, nrm)
        for j in range(3, len(sel) - 3, 6):  # Pfeiler, wo es unter der Brücke tief genug runtergeht
            top, foot = cy[j] - 1.2, float(ground(cx[j:j + 1], cz[j:j + 1])[0]) - 1.0
            if top - foot < 2.5:
                continue
            fx, fz = -lz[j], lx[j]  # vorwärts
            for (ox, oz, nx, nz) in ((.8, 0, 1, 0), (-.8, 0, -1, 0), (0, .6, 0, 1), (0, -.6, 0, -1)):
                # Seitenfläche: Mitte (ox längs, oz quer), Normale (nx längs, nz quer)
                c = np.array([cx[j] + fx * ox + lx[j] * oz, cz[j] + fz * ox + lz[j] * oz])
                t = np.array([lx[j], lz[j]]) * .6 if ox else np.array([fx, fz]) * .8  # halbe Breite entlang der Fläche
                nw = np.array([fx * nx + lx[j] * nz, 0, fz * nx + lz[j] * nz])
                e1 = np.array([[c[0] - t[0], top, c[1] - t[1]], [c[0] - t[0], foot, c[1] - t[1]]])
                e2 = np.array([[c[0] + t[0], top, c[1] + t[1]], [c[0] + t[0], foot, c[1] + t[1]]])
                quads(e1, e2, np.tile(nw, (2, 1)), np.tile(nw, (2, 1)))
        for e, inward in ((1, 1), (len(sel) - 2, -1)):  # Widerlager: Betonwand am Brückenende, Deck bis unter das Tal-Gelände
            j = int(np.clip(e + 2 * inward, 0, len(sel) - 1))  # ~10 m in der Brücke liegt das Gelände schon unten
            top, foot = cy[e] - 1.2, float(ground(cx[j:j + 1], cz[j:j + 1])[0]) - 1.0
            if top - foot < 1.5 or len(sel) < 4:
                continue
            nw = np.array([-lz[e], 0, lx[e]]) * inward  # zur Brückenmitte (ins Tal)
            l, r = np.array([cx[e] + lx[e] * b[e], cz[e] + lz[e] * b[e]]), np.array([cx[e] - lx[e] * b[e], cz[e] - lz[e] * b[e]])
            quads(np.array([[l[0], top, l[1]], [l[0], foot, l[1]]]), np.array([[r[0], top, r[1]], [r[0], foot, r[1]]]),
                  np.tile(nw, (2, 1)), np.tile(nw, (2, 1)))
    if not P:
        return None
    pos = np.vstack(P)
    return pos, np.vstack(N), np.vstack(I), np.tile([*CONCRETE, 0], (len(pos), 1))  # Alpha 0 = keine Fahrbahnmarkierung


CLEARANCE = 5.5  # m lichte Höhe über einer anderen Straße


def crossings(x, z, ways, on):
    """Wo eine andere Straße unter unserer Brücke durchgeht: [(Routenindex, x, z)] (on = unsere Brückenpunkte)."""
    sel = np.nonzero(on)[0]
    if not len(sel):
        return []
    tree = cKDTree(np.column_stack([x[sel], z[sel]]))
    rdx, rdz = np.gradient(x), np.gradient(z)
    out = {}
    for wx, wz, tags in ways:
        if tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no":
            continue  # Brücke neben/über uns oder Tunnel darunter: keine Kreuzung auf Geländehöhe
        d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(wx), np.diff(wz)))])
        s = np.linspace(0, d[-1], max(2, int(d[-1] / 2) + 1))
        px, pz = np.interp(s, d, wx), np.interp(s, d, wz)
        dist, j = tree.query(np.column_stack([px, pz]), distance_upper_bound=4.0)
        gx, gz = np.gradient(px), np.gradient(pz)
        for k in np.nonzero(np.isfinite(dist))[0]:
            i = sel[j[k]]
            cos = abs(gx[k] * rdx[i] + gz[k] * rdz[i]) / (np.hypot(gx[k], gz[k]) * np.hypot(rdx[i], rdz[i]) + 1e-9)
            if cos >= 0.8:  # parallel: keine Kreuzung
                continue
            # wirklich drunter durch: ±10 m entlang des Wegs liegen Punkte auf beiden Seiten unserer Linie
            # (eine Seitenstraße, die am Brückenkopf endet, kreuzt nicht)
            nb = slice(max(k - 5, 0), k + 6)
            side = (px[nb] - x[i]) * rdz[i] - (pz[nb] - z[i]) * rdx[i]
            if side.min() < -1.0 and side.max() > 1.0:
                out.setdefault(i, (px[k], pz[k]))
    return [(i, *xz) for i, xz in sorted(out.items())]


def tunnel_mesh(x, y, z, hw, tunnel):
    """Tunnel als Galerie: Wände + Dach (innen und außen), Portale an den Enden. Liegt im Geländeeinschnitt, den
    route.fit_terrain an der Straße ohnehin macht. -> (pos, nrm, idx, col) oder None."""
    labels, count = ndimage.label(tunnel)
    P, N, I = [], [], []

    def add(a, b, n):
        o = sum(len(v) for v in P)
        m = len(a)
        P.extend([a, b]); N.extend([n, n])
        q = o + np.arange(m - 1)
        I.append(np.column_stack([q, q + m, q + 1, q + 1, q + m, q + m + 1]).reshape(-1, 3))

    for run in range(1, count + 1):
        sel = np.nonzero(labels == run)[0]
        cx, cy, cz, h = x[sel], y[sel], z[sel], hw[sel]
        if len(sel) < 2:
            continue
        dx, dz = np.gradient(cx), np.gradient(cz)
        ln = np.hypot(dx, dz) + 1e-9
        lx, lz, fx, fz = dz / ln, -dx / ln, dx / ln, dz / ln
        wo, wi = h + 1.4, h + 1.0
        outer = [(-wo, -.5), (-wo, 4.6), (0 * h, 6.4), (wo, 4.6), (wo, -.5)]  # rechts -> links, außen
        inner = [(-wi, 0), (-wi, 4.3), (0 * h, 5.9), (wi, 4.3), (wi, 0)]
        v = lambda o, yy: np.column_stack([cx + lx * o, cy + yy, cz + lz * o])
        for ring, sign in ((outer, 1), (inner[::-1], 1)):  # innen rückwärts -> Normalen zeigen in den Tunnel
            for (o1, y1), (o2, y2) in zip(ring, ring[1:]):
                do, dy = o2 - o1, np.full(cx.shape, y2 - y1)
                nl = np.hypot(do, dy) + 1e-9
                no, ny = -dy / nl * sign, do / nl * sign
                add(v(o1, y1), v(o2, y2), np.column_stack([no * lx, ny, no * lz]))
        for end, nf in ((0, -1.0), (-1, 1.0)):  # Portal: Ring zwischen außen und innen
            for (o1, y1), (o2, y2), (i1, j1), (i2, j2) in zip(outer, outer[1:], inner, inner[1:]):
                e = slice(end, end + 1) if end == 0 else slice(-1, None)
                pa = np.vstack([v(o1, y1)[e], v(i1, j1)[e]])
                pb = np.vstack([v(o2, y2)[e], v(i2, j2)[e]])
                nrm = np.tile([fx[e][0] * nf, 0, fz[e][0] * nf], (2, 1))
                add(pa, pb, nrm)
    if not P:
        return None
    pos = np.vstack(P)
    return pos, np.vstack(N), np.vstack(I), np.tile([*CONCRETE, 0], (len(pos), 1))


def road_profile(at, x, z, loop, span=None, floor=None, lam=1e5, lat_err=8.0):
    """Höhenprofil wie eine gebaute Straße: DEM entlang der Route, Whittaker-geglättet (Strafe auf Steigungsänderung).
    Am Steilhang ist das DEM unsicher (Lage ±lat_err m × Querneigung = Meter Höhenfehler, Serpentinen verschwimmen),
    dort zählt der Messwert weniger. span = Brücken/Tunnel (bool je Punkt): dort zählt das DEM gar nicht, die Straße
    überspannt das Tal bzw. geht unter dem Berg durch. floor = Mindesthöhe je Punkt (NaN = keine), z.B. Brücke über einer
    anderen Straße: das Profil steigt mit weichen Rampen darauf. Gesamthöhe und lange echte Rampen bleiben. at(x, z) -> DEM."""
    raw = at(x, z)
    dx, dz = np.gradient(x), np.gradient(z)
    ln = np.hypot(dx, dz) + 1e-9
    px, pz = dz / ln * 6, -dx / ln * 6
    cross = np.abs(at(x + px, z + pz) - at(x - px, z - pz)) / 12
    w = 1 / (1 + lat_err * cross) ** 2
    if span is not None:
        w = np.where(span, 1e-9, w)
    n = len(raw)
    i = np.arange(n if loop else n - 2)  # Runde: zweite Differenzen über den Start hinweg
    D = sparse.csr_matrix((np.tile([1., -2., 1.], len(i)), (np.repeat(i, 3), (i[:, None] + np.arange(3)).ravel() % n)), shape=(len(i), n))
    DD = lam * (D.T @ D)
    h = spsolve((sparse.diags(w) + DD).tocsc(), w * raw)
    for _ in range(4 if floor is not None else 0):  # zu tief über einer Kreuzung: dort festhalten, neu lösen
        low = np.isfinite(floor) & (h < floor - 0.1)
        if not low.any():
            break
        w, raw = np.where(low, 1e3, w), np.where(low, floor + 0.2, raw)
        h = spsolve((sparse.diags(w) + DD).tocsc(), w * raw)
    return h


def test_junctions():
    # Gerade Straße nach Osten: Einmündung nach Norden (links) bei 100 m, Kreuzung bei 200 m, eigene Straße, paralleler Weg,
    # Brücke darüber -> 3 Öffnungen, Randlinie ±(2,8 + Eckradius 2,8) m um die Mitte
    x = np.arange(0, 400, STEP)
    z = np.zeros_like(x)
    hw = np.full(len(x), 3.0)
    ways = [(np.array([100.0, 100]), np.array([0.0, -50]), {"highway": "residential"}),
            (np.array([200.0, 200]), np.array([-50.0, 50]), {"highway": "residential"}),
            (np.array([0.0, 400]), np.array([0.0, 0]), {"highway": "secondary"}),
            (np.array([0.0, 400]), np.array([6.0, 6]), {"highway": "service"}),
            (np.array([300.0, 300]), np.array([-50.0, 50]), {"highway": "primary", "bridge": "yes"})]
    j = junctions(x, z, hw, ways, np.zeros(len(x), bool))
    got = [(round((m["s0"] + m["s1"]) / 2), m["side"]) for m in j]
    assert got == [(100, 1), (200, -1), (200, 1)] or got == [(100, 1), (200, 1), (200, -1)], got
    assert all(abs(m["s1"] - m["s0"] - 11.2) < 0.5 for m in j) and np.allclose(j[0]["dir"], [0, -1], atol=0.05)
    # schräg (45°) -> längere Öffnung; auf einer Brücke/in einem Tunnel keine Einmündungen
    j = junctions(x, z, hw, [(np.array([100.0, 150]), np.array([0.0, 50]), {"highway": "residential"})], np.zeros(len(x), bool))
    assert len(j) == 1 and j[0]["side"] == -1 and abs(j[0]["s1"] - j[0]["s0"] - 2 * (2.8 / np.sqrt(.5) + 2.8)) < 0.5
    assert not junctions(x, z, hw, ways[:2], np.ones(len(x), bool))


def test():
    test_junctions()
    # Stichstraße an einem Wegpunkt: 200 m geradeaus, 60 m rechts rein und zurück, weiter -> Stich weg, Rest bleibt
    a = np.column_stack([np.arange(0, 200, STEP), np.zeros(40)])
    spur = np.column_stack([np.full(12, 200.0), np.arange(0, 60, STEP)])
    b = np.column_stack([np.arange(200, 400, STEP), np.zeros(40)])
    p = np.vstack([a, spur, spur[::-1][1:], b[1:]])
    cx, cz = clean_route(p[:, 0], p[:, 1], False)
    assert np.abs(cz).max() < 1e-9 and cx[0] == 0 and cx[-1] == 395, (np.abs(cz).max(), cx[-1])
    # Serpentine (Äste 25 m auseinander) bleibt, lange gewollte Hin-und-zurück-Strecke (2 km) bleibt
    leg = np.arange(0, 300, STEP)
    hx = np.concatenate([leg, 300 + 12.5 * np.sin(np.linspace(0, np.pi, 7)), leg[::-1]])
    hz = np.concatenate([np.zeros_like(leg), 12.5 - 12.5 * np.cos(np.linspace(0, np.pi, 7)), np.full_like(leg, 25.0)])
    assert len(clean_route(hx, hz, False)[0]) == len(hx)
    long_ = np.arange(0, 2000, STEP)
    assert len(clean_route(np.r_[long_, long_[::-1][1:]], np.zeros(2 * len(long_) - 1), False)[0]) == 2 * len(long_) - 1

    # 90°-Knick: danach Radius ≥ 5 m, nie mehr als 2,5 m von der Straße weg, Endpunkte fest
    x = np.r_[np.arange(0, 100, STEP), np.full(20, 100.0)]
    z = np.r_[np.zeros(20), np.arange(0, 100, STEP)]
    sx, sz = smooth_line(x, z, False)
    sx, sz = resample(sx, sz, False)
    k = np.abs(curvature(sx, sz, False))
    assert 1 / k.max() >= 5, 1 / k.max()
    seg = np.column_stack([x, z])
    assert cKDTree(seg).query(np.column_stack([sx, sz]))[0].max() <= 2.6 + STEP / 2
    assert np.hypot(sx[0], sz[0]) < 1e-6 and np.hypot(sx[-1] - 100, sz[-1] - 95) < STEP

    # Kehre aus OSM-Knoten (Äste 20 m auseinander, spitze Wende): danach Radius ≥ 6 m, höchstens 6 m neben der Linie
    leg = np.arange(0, 200, STEP)
    x = np.r_[leg, 200, leg[::-1]]
    z = np.r_[np.zeros_like(leg), 10, np.full_like(leg, 20.0)]
    sx, sz = resample(*smooth_line(x, z, False), False)
    assert 1 / np.abs(curvature(sx, sz, False)).max() >= 6, 1 / np.abs(curvature(sx, sz, False)).max()
    assert cKDTree(np.column_stack([x, z])).query(np.column_stack([sx, sz]))[0].max() <= 6.1 + STEP / 2

    # OSM-Zuordnung: Route auf einer Hauptstraße mit Brücke in der Mitte, Querstraße kreuzt -> nur Hauptstraße zählt
    rx = np.arange(0, 400, STEP)
    rz = np.full_like(rx, 1.0)
    ways = [(np.array([0., 150]), np.zeros(2), {"highway": "secondary"}),
            (np.array([150., 250]), np.zeros(2), {"highway": "secondary", "bridge": "yes"}),
            (np.array([250., 400]), np.zeros(2), {"highway": "secondary", "surface": "sett"}),
            (np.full(2, 100.0), np.array([-200., 200]), {"highway": "track"})]
    m = match_ways(rx, rz, ways, False)
    assert (m["cls"] == "secondary").all() and m["mark"][:25].min() == 1.0 and np.isclose(m["hw"][10], 3.5)
    assert m["bridge"][34:46].all() and not m["bridge"][:25].any() and not m["bridge"][55:].any()
    assert (m["surface"][60:] == 1).all() and m["mark"][70] == 0  # Kopfsteinpflaster: Belag 1, keine Markierung
    assert (uv2_code([0, 1, 2]) < 0).all() and np.allclose(-1 - uv2_code([0, 1, 2]), [0, 1, 2])
    m = match_ways(rx, rz, [(np.array([0., 400]), np.zeros(2), {"highway": "track"})], False)
    assert (m["surface"] == 2).all() and (m["hw"] >= MIN_HW).all() and m["mark"].max() == 0  # Waldweg: unbefestigt, schmal
    assert (match_ways(rx, rz, [], False)["hw"] == 2.8).all()  # keine OSM-Daten: Standardstraße

    # Höhenprofil: Hangquerung mit DEM-Rauschen wird glatt, echte 20-%-Rampe in der Falllinie bleibt
    s = np.arange(0, 3000, STEP)
    hill = lambda x, z: 0.6 * x + 0.08 * z + 4 * np.sin(z / 40)  # Straße quer zum 60-%-Hang (x), DEM wackelt ±4 m über ~250 m
    h = road_profile(hill, np.zeros_like(s), s, False)
    g = np.diff(h) / STEP * 100
    assert np.abs(g[40:-40] - 8).max() < 2 and abs(h[-1] - h[0] - 0.08 * s[-1]) < 15, (np.abs(g).max(), h[-1] - h[0])
    h = road_profile(lambda x, z: 0.2 * z + 0.3 * np.sin(z / 9), np.zeros_like(s), s, False)  # flacher Querhang, echte Rampe
    assert (np.diff(h)[40:-40] / STEP > 0.18).all()
    ring = np.linspace(0, 2 * np.pi, 600, endpoint=False)
    h = road_profile(lambda x, z: 10 * np.sin(np.arctan2(z, x)), 500 * np.cos(ring), 500 * np.sin(ring), True)
    assert abs(h[0] - h[-1]) < 0.5  # Runde ohne Sprung am Start
    # Brücke über ein 40 m tiefes Tal und Tunnel unter einem 80-m-Berg: Profil bleibt eben statt Delle/Buckel
    s = np.arange(0, 2000, STEP)
    terrain = lambda x, z: np.where(np.abs(z - 600) < 100, -40.0, 0) + np.where(np.abs(z - 1400) < 150, 80.0, 0)
    span = (np.abs(s - 600) < 110) | (np.abs(s - 1400) < 160)
    h = road_profile(terrain, np.zeros_like(s), s, False, span=span)
    assert np.abs(h).max() < 1.0, np.abs(h).max()
    # Brückengeometrie: 100-m-Brücke 30 m über dem Tal -> Brüstung/Deck vorhanden, Normalen Einheitslänge, Pfeiler bis zum Boden
    s = np.arange(0, 300, STEP)
    br = (s > 100) & (s < 200)
    mesh = bridge_mesh(s, np.zeros_like(s), np.zeros_like(s), np.full_like(s, 3.0), br, lambda x, z: np.full_like(x, -30.0))
    pos, nrm, idx, col = mesh
    assert np.allclose(np.linalg.norm(nrm, axis=1), 1) and idx.max() < len(pos)
    assert pos[:, 1].min() < -29 and pos[:, 1].max() > 1.1  # Pfeiler bis ins Tal, Brüstung über der Fahrbahn
    assert np.isclose(np.abs(pos[pos[:, 1] > 1, 2]).max(), 3.8)  # Brüstung außen bei halber Breite + 0,8 m
    walls = (pos[:, 1] < -29) & (np.abs(pos[:, 2]) > 3.7)  # Widerlager: volle Breite bis ins Tal, an beiden Enden
    assert {round(v) for v in pos[walls, 0]} == {105, 195}, sorted({round(v) for v in pos[walls, 0]})
    assert bridge_mesh(s, s * 0, s * 0, s * 0 + 3, s < 0, lambda x, z: x) is None
    # Brücke über eine Straße auf Geländehöhe: Deck steigt auf ≥ 5,5 m, Rampen ≤ 10 %
    s = np.arange(0, 1000, STEP)
    br = np.abs(s - 500) < 15
    cr = crossings(s, np.zeros_like(s), [(np.full(2, 500.0), np.array([-50., 50]), {"highway": "primary"})], br)
    assert len(cr) == 1 and abs(s[cr[0][0]] - 500) <= STEP
    floor = np.full_like(s, np.nan)
    floor[cr[0][0]] = CLEARANCE
    h = road_profile(lambda x, z: np.zeros_like(x), s, np.zeros_like(s), False, span=br, floor=floor)
    assert h[cr[0][0]] >= CLEARANCE - 0.15 and np.abs(np.diff(h) / STEP).max() < 0.10, (h.max(), np.abs(np.diff(h)).max() / STEP)
    assert crossings(s, np.zeros_like(s), [(s, np.full_like(s, 1.0), {"highway": "footway"})], br) == []  # parallel: keine Kreuzung
    assert crossings(s, np.zeros_like(s), [(np.full(2, 500.0), np.array([-50., -1.0]), {"highway": "service"})], br) == []  # endet am Brückenkopf
    # Route beginnt mitten auf einer Brücke: Flag bleibt am Rand (sonst Rampe zum Talboden), auch bei einer Runde
    flag = np.r_[np.ones(10, bool), np.zeros(30, bool)]
    assert _clean_flags(flag, False)[:10].all() and _clean_flags(flag, True)[:10].all() and not _clean_flags(flag, False)[12:].any()
    # Fußweg direkt neben der Straße gewinnt nicht gegen die Straße (GPS-Track liegt dazwischen)
    m = match_ways(rx, np.full_like(rx, 2.5), [(np.array([0., 400]), np.zeros(2), {"highway": "secondary"}),
                                                (np.array([0., 400]), np.full(2, 5.0), {"highway": "footway"})], False)
    assert (m["cls"] == "secondary").all()
    # Tunnel: Galerie mit Normalen, Dach über der Fahrbahn, nur im Tunnelbereich
    tn = (s > 300) & (s < 600)
    pos, nrm, idx, col = tunnel_mesh(s, np.zeros_like(s), np.zeros_like(s), np.full_like(s, 3.0), tn)
    assert np.allclose(np.linalg.norm(nrm, axis=1), 1) and pos[:, 1].max() > 6 and 295 < pos[:, 0].min() and pos[:, 0].max() < 605
    print("road ok")
