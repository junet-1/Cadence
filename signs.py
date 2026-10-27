"""Straßenausstattung für route.py: Markierungen an Kreuzungen, Schilder und Ampeln aus OSM. Reine numpy/PIL-Logik.

  lane_marks  je Routenpunkt (Mesh-Kanal CUSTOM0): Randlinie links/rechts unterbrochen (Einmündung), Mittellinie
              durchgezogen (vor Ampeln)
  furnish     alles Weitere: Querstriche (Haltlinie, Wartelinie, Zebrastreifen, Fußgängerfurt) als Dreiecke, Schilder
              (Tempo, Stopp, Vorfahrt gewähren, Ortstafel, Straßenname, Rad-/Wanderwegweiser) und Ampeln als Instanzen
  roadside    Leitpfosten und Schutzplanken außerorts (OSM barrier=guard_rail, Gefälle neben der Fahrbahn, Brückenköpfe)
  atlas       alle Schildgesichter als eine Textur, UV-Rechteck je Gesicht

Quellen: OSM-Knoten (highway=traffic_signals/stop/give_way/crossing, traffic_sign=city_limit, information=guidepost mit
direction_*), Tags der befahrenen Wege (maxspeed, name), road.junctions. Nur für unsere Fahrtrichtung (rechts, zu uns gedreht).
Koordinaten wie route.py: x Ost, z Süd, y Höhe; links = (t.z, -t.x).
"""
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

import road

STEP = road.STEP
WHITE = (.85, .85, .83)
COMPASS = {"north": 0, "northeast": 45, "east": 90, "southeast": 135, "south": 180, "southwest": 225, "west": 270, "northwest": 315}


class Route:
    """Route als Kurve: s (m ab Start) <-> Punkt, Tangente, Linksnormale; Höhe der Fahrbahnoberfläche."""

    def __init__(self, x, z, y, hw, loop):
        self.x, self.z, self.y, self.hw, self.loop = x, z, y, hw, loop
        t = np.column_stack([np.gradient(x), np.gradient(z)])
        self.t = t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-9)
        self.n = len(x)

    def at(self, s, lat=0.0):
        """Punkt(e) bei s mit Querversatz lat (links +) -> (x, y, z) auf der Fahrbahnoberfläche (+5 cm wie das Mesh)."""
        f = np.clip(np.asarray(s, float) / STEP, 0, self.n - 1)
        i = np.minimum(f.astype(int), self.n - 2)
        w = f - i
        lerp = lambda a: a[i] * (1 - w) + a[i + 1] * w
        tx, tz = lerp(self.t[:, 0]), lerp(self.t[:, 1])
        ln = np.hypot(tx, tz) + 1e-9
        return lerp(self.x) + tz / ln * lat, lerp(self.y) + 0.05, lerp(self.z) - tx / ln * lat

    def tan(self, s):
        i = int(np.clip(round(s / STEP), 0, self.n - 1))
        return self.t[i]


def lane_marks(n, junctions, solid, loop=False):
    """CUSTOM0 je Routenpunkt: x Randlinie links unterbrochen, y rechts unterbrochen, z Mittellinie durchgezogen, w frei.
    solid = [(s0, s1)] Strecken mit durchgezogener Mittellinie. Runde: Bereiche über den Start hinaus laufen am Ende weiter."""
    c0 = np.zeros((n, 4), np.float32)
    s = np.arange(n) * STEP
    wraps = (-n * STEP, 0, n * STEP) if loop else (0,)
    for col, a, b in [(0 if m["side"] > 0 else 1, m["s0"], m["s1"]) for m in junctions] + [(2, a, b) for a, b in solid]:
        for w in wraps:
            c0[(s >= a + w) & (s <= b + w), col] = 1
    return c0


def _quad(c, a, b):
    """Rechteck: Mitte c (3), halbe Kantenvektoren a, b (3) -> 4 Ecken."""
    return np.array([c - a - b, c + a - b, c + a + b, c - a + b])


def _yaw_rows(x, y, z, yaw, sx, sy, sz=1.0):
    """MultiMesh-Transform (3x4 zeilenweise): Drehung um y (lokal +Z zeigt nach (sin, 0, cos) yaw), Skalierung."""
    c, s = np.cos(yaw), np.sin(yaw)
    return [c * sx, 0, s * sz, x, 0, sy, 0, y, -s * sx, 0, c * sz, z]


def _facing(t):
    """yaw, damit die Schildvorderseite (lokal +Z) einem Fahrer in Richtung t entgegenschaut."""
    return float(np.arctan2(-t[0], -t[1]))


def _speed(v):
    v = str(v).strip()
    m = re.fullmatch(r"(\d+)( mph)?", v)
    return int(m.group(1)) if m else {"DE:urban": -50, "AT:urban": -50, "CH:urban": -50, "DE:rural": -100}.get(v)  # < 0: ohne Schild


def _dests(v):
    """'Oberursel 8.5; Kronberg 2,9 km; Limes Radweg' -> ['Oberursel 8,5 km', 'Kronberg 2,9 km'] (nur Ziele mit Entfernung)."""
    out = []
    for part in v.split(";"):
        m = re.fullmatch(r"\s*(.+?)\s+([\d.,?]+)\s*(km|m)?\s*", part)
        if m and any(ch.isdigit() for ch in m.group(2)):
            out.append(f"{m.group(1)} {m.group(2).replace('.', ',')} {m.group(3) or 'km'}")
    return out[:2]


def furnish(route, rd, ways, junctions, nodes, ground, lift, buildings, europe=True):
    """-> dict: c0 (n×4), marks [(4×3 Ecken)], plates [(16 floats: Transform + Gesicht)], faces {key: index}, posts
    [12 floats], signals [12 floats], counts {Art: Anzahl}. rd = road.match_ways(...) (way, cls, bridge, tunnel),
    ways = [(x, z, tags)] wie für match_ways, nodes = [(x, z, tags)], ground(x, z) -> Gelände, lift = {Klasse: Höhe der
    Nebenstraße übers Gelände}, buildings = Gebäudemitten (k×2) für Ortseingang/-ausgang, europe: ungetaggte Einmündungen
    an Hauptstraßen bekommen „Vorfahrt gewähren“ (dort üblich; anderswo nur, was OSM sagt)."""
    from scipy.spatial import cKDTree
    R = route
    n = R.n
    total = (n - 1) * STEP
    skip = rd["bridge"] | rd["tunnel"]
    marks, plates, posts, signals, faces = [], [], [], [], {}
    count = {}

    def face(key):
        return faces.setdefault(key, len(faces))

    def plate(x, y, z, yaw, w, h, key):
        plates.append(_yaw_rows(x, y, z, yaw, w, h) + [face(key), 0, 0, 0])

    def post(x, z, top, r=0.04):
        y0 = float(ground(np.array([x]), np.array([z]))[0]) - 0.3
        posts.append(_yaw_rows(x, y0, z, 0.0, r / 0.04, max(top - y0, 0.5), r / 0.04))

    def right_sign(s, key, w, h, bottom=2.0, off=1.2):
        """Schild rechts neben unserer Fahrbahn bei s, zu uns gedreht, auf einem Pfosten."""
        i = int(np.clip(round(s / STEP), 0, n - 1))
        x, _, z = R.at(s, -(R.hw[i] + off))
        g = float(ground(np.array([x]), np.array([z]))[0])
        plate(x, g + bottom + h / 2, z, _facing(R.tan(s)), w, h, key)
        post(x, z, g + bottom + h)
        count[key.split(":")[0]] = count.get(key.split(":")[0], 0) + 1

    def across(s, lat0, lat1, length):
        """Querstrich auf unserer Fahrbahn bei s von lat0 bis lat1, length lang (in Fahrtrichtung); Ecken auf der Fahrbahn
        (folgt der Steigung)."""
        ss = np.array([s - length / 2, s - length / 2, s + length / 2, s + length / 2])
        x, y, z = R.at(ss, np.array([lat0, lat1, lat1, lat0]))
        marks.append(np.column_stack([x, y + 0.02, z]))

    # Knoten auf die Route abbilden: s, Querabstand
    tree = cKDTree(np.column_stack([R.x, R.z]))
    on = []
    for x, z, tags in nodes:
        d, i = tree.query([x, z])
        rel = np.array([x - R.x[i], z - R.z[i]])
        s = i * STEP + rel @ R.t[i]
        lat = rel @ np.array([R.t[i, 1], -R.t[i, 0]])
        on.append((s, lat, i, x, z, tags))

    # Ampeln auf unserer Straße: erste je 40 m (OSM hat oft eine je Richtung), Haltlinie vor der Kreuzung, Mittellinie davor
    # durchgezogen, Ampel rechts an der Haltlinie
    solid, last = [], -1e9
    sig = sorted((o for o in on if (o[5].get("highway") == "traffic_signals" or o[5].get("crossing") == "traffic_signals")
                  and abs(o[1]) < R.hw[o[2]] + 4 and not skip[o[2]]), key=lambda o: o[0])
    for s, lat, i, x, z, tags in sig:
        if s - last < 40:
            continue
        last = s
        mouths = [m["s0"] for m in junctions if s - 5 < (m["s0"] + m["s1"]) / 2 < s + 25]
        stop = min([s - 2] + [a - 1.5 for a in mouths])
        if stop < 3:
            continue
        across(stop, -(R.hw[i] - 0.3), 0.0, 0.5)
        solid.append((stop - 30, stop))
        ax, _, az = R.at(stop + 0.5, -(R.hw[i] + 0.9))
        g = float(ground(np.array([ax]), np.array([az]))[0])
        signals.append(_yaw_rows(ax, g, az, _facing(R.tan(stop)), 1, 1))
    count["ampel"] = len(signals)

    # Fußgängerüberwege: Zebrastreifen (zebra/marked), Furt an Ampeln
    for s, lat, i, x, z, tags in on:
        if tags.get("highway") != "crossing" or abs(lat) > R.hw[i] + 1 or skip[i]:
            continue
        kind = tags.get("crossing", "")
        if kind in ("zebra", "marked") or tags.get("crossing_ref") == "zebra":
            for k in np.arange(-(R.hw[i] - 0.55), R.hw[i] - 0.3, 1.0):  # 0,5 m breite Streifen längs, 0,5 m Lücke
                across(s, k - 0.25, k + 0.25, 4.0)
            count["zebra"] = count.get("zebra", 0) + 1
        elif kind == "traffic_signals":
            for ds in (-2.0, 2.0):
                for k in np.arange(-(R.hw[i] - 0.4), R.hw[i] - 0.3, 0.75):
                    across(s + ds, k - 0.25, k + 0.25, 0.12)
            count["furt"] = count.get("furt", 0) + 1

    # Einmündungen: Warte-/Haltlinie und Schild an der Nebenstraße, Straßenname an der Ecke vor der Einmündung
    rank_here = lambda i: road.RANK.get(str(rd["cls"][i]), 2)
    names_seen = {}
    for m in junctions:
        i = m["i"]
        tags, d = m["tags"], m["dir"]
        near = [o[5] for o in on if np.hypot(o[3] - m["at"][0], o[4] - m["at"][1]) < 25 and abs(o[1]) > R.hw[o[2]] + 0.5]
        kind = "stop" if any(t.get("highway") == "stop" for t in near) else \
            "give_way" if any(t.get("highway") == "give_way" for t in near) else None
        signal = any(abs(s0 - m["s0"]) < 35 for s0, *_ in sig)
        cls = tags.get("highway", "").removesuffix("_link")
        if kind is None and europe and not signal and rank_here(i) >= 3 and 2 <= road.RANK.get(cls, 0) < rank_here(i):
            kind = "give_way"  # Nebenstraße an einer Landes-/Kreisstraße: Vorfahrt gewähren (üblich, in OSM selten getaggt)
        hws = m["hw"]
        rgt = np.array([d[1], -d[0]])  # rechts für Verkehr, der auf uns zufährt (Richtung -d)
        c = m["at"] + d * 0.8
        lat0, lat1 = (0.0, hws - 0.3) if hws >= 2.6 else (-(hws - 0.3), hws - 0.3)
        # Einmündung (network.mouth) liegt an unserer Kante auf Fahrbahnhöhe und fällt dann zur Nebenstraße ab
        y = max(float(ground(c[:1], c[1:])[0]) + lift.get(cls, 0.1), float(R.at(m["i"] * STEP)[1])) + 0.025
        mid = c + rgt * (lat0 + lat1) / 2
        a3 = np.array([rgt[0], 0, rgt[1]]) * (lat1 - lat0) / 2
        b3 = np.array([d[0], 0, d[1]]) * 0.25
        if kind == "stop" or (signal and kind is None):
            marks.append(_quad(np.array([mid[0], y, mid[1]]), a3, b3))  # Haltlinie 0,5 m
        elif kind == "give_way":  # Wartelinie: 0,5 m Striche, 0,25 m Lücken
            for k in np.arange(lat0 + 0.25, lat1, 0.75):
                p = c + rgt * k
                marks.append(_quad(np.array([p[0], y, p[1]]), np.array([rgt[0], 0, rgt[1]]) * 0.25, b3))
        if kind:
            p = c + d * 1.2 + rgt * (hws + 0.8)
            g = float(ground(p[:1], p[1:])[0])
            plate(p[0], g + 2.0 + 0.35, p[1], float(np.arctan2(d[0], d[1])), 0.7, 0.7, kind)  # schaut die Nebenstraße hinunter
            post(p[0], p[1], g + 2.7)
            count[kind] = count.get(kind, 0) + 1
        name = tags.get("name", "")
        here = ways[rd["way"][i]][2].get("name", "") if rd["way"][i] >= 0 else ""
        if name and name != here and abs(names_seen.get(name, -1e9) - m["s0"]) > 80:
            names_seen[name] = m["s0"]
            right_sign(max(m["s0"] - 1.0, 0), f"street:{name}", 1.0, 0.25, bottom=2.3,
                       off=1.2 if m["side"] < 0 else -(2 * R.hw[i] + 1.2))  # an der Ecke auf der Seite der Einmündung

    # Tempo: wo sich maxspeed der befahrenen Straße ändert (≥ 150 m lang), am Start; innerorts-Regel (DE:urban) ohne Schild
    v = np.array([(_speed(ways[k][2].get("maxspeed", "")) or 0) if k >= 0 else 0 for k in rd["way"]])
    for i in range(1, n):  # Lücken mit dem letzten Wert füllen
        if v[i] == 0:
            v[i] = v[i - 1]
    shown, i = None, 0
    while i < n:
        j = i
        while j < n and v[j] == v[i]:
            j += 1
        if (j - i) * STEP >= 150 or j == n:
            if v[i] != shown and v[i] > 0 and not skip[i]:
                right_sign(i * STEP + 5, f"speed:{v[i]}", 0.6, 0.6)
            shown = v[i]
        i = j

    # Ortstafeln: Name aus OSM, Eingang oder Ausgang nach Häusern davor/dahinter (gelb mit Name / durchgestrichen)
    bt = cKDTree(buildings) if len(buildings) else None
    city = sorted((o for o in on if o[5].get("traffic_sign", "").startswith(("city_limit", "DE:310", "DE:311"))
                   and o[5].get("name") and abs(o[1]) < R.hw[o[2]] + 15), key=lambda o: o[0])
    placed = []
    for s, lat, i, x, z, tags in city:
        if any(abs(s - p) < 150 for p in placed) or not 10 < s < total - 10 or skip[i]:
            continue
        placed.append(s)

        def houses(a, b):
            if bt is None:
                return 0
            ss = np.arange(max(a, 0), min(b, total), 20.0)
            px, _, pz = R.at(ss)
            return sum(len(r) for r in bt.query_ball_point(np.column_stack([px, pz]), 60))

        entering = houses(s, s + 300) >= houses(s - 300, s)
        x0, _, z0 = R.at(s, -(R.hw[i] + 1.5))
        g = float(ground(np.array([x0]), np.array([z0]))[0])
        t = R.tan(s)
        plate(x0, g + 1.6, z0, _facing(t), 1.4, 0.7, ("city:" if entering else "cityend:") + tags["name"])
        nl = np.array([t[1], -t[0]])
        for k in (-0.55, 0.55):
            post(x0 + nl[0] * k, z0 + nl[1] * k, g + 1.95)
        count["ortstafel"] = count.get("ortstafel", 0) + 1

    # Wegweiser: Pfosten am Knoten (aus der Fahrbahn geschoben), je Richtung ein Pfeilschild (Spitze zeigt hin) mit Zielen
    for s, lat, i, x, z, tags in on:
        if tags.get("information") != "guidepost" or abs(lat) > R.hw[i] + 20 or skip[i]:
            continue
        dirs = [(COMPASS[k[10:]], _dests(v)) for k, v in tags.items() if k.startswith("direction_") and k[10:] in COMPASS]
        dirs = [(b, ds) for b, ds in dirs if ds]
        if not dirs:
            continue
        if abs(lat) < R.hw[i] + 1.0:  # Knoten auf der Straße gemappt -> an den Rand
            x, _, z = R.at(s, (1 if lat > 0 else -1) * (R.hw[i] + 1.0))
        g = float(ground(np.array([x]), np.array([z]))[0])
        bike = tags.get("bicycle") == "yes" or tags.get("guidepost") == "bicycle"
        for k, (b, ds) in enumerate(dirs[:4]):
            dv = np.array([np.sin(np.radians(b)), -np.cos(np.radians(b))])  # Nord = -z
            yaw = float(np.arctan2(-dv[1], dv[0]))  # lokal +X = dv (Spitze), lokal +Z = Vorderseite
            key = ("bike:" if bike else "hike:") + "|".join(ds)
            for side in (1, -1):  # zwei Schilder Rücken an Rücken, beide lesbar, Spitze zeigt dieselbe Richtung
                off = np.array([np.sin(yaw), np.cos(yaw)]) * 0.006 * side
                c = np.array([x, z]) + dv * 0.55 + off
                plate(c[0], g + 2.45 - k * 0.32, c[1], yaw if side > 0 else yaw + np.pi, 1.0, 0.25, key + ("" if side > 0 else ":l"))
        post(x, z, g + 2.65, 0.05)
        count["wegweiser"] = count.get("wegweiser", 0) + 1

    return dict(c0=lane_marks(n, junctions, solid, R.loop), marks=marks, plates=plates, faces=faces, posts=posts, signals=signals,
                counts=count)


RURAL = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"}  # außerorts mit Leitpfosten/Schutzplanken
POST_MAX = 50.0  # m Leitpfosten-Abstand auf der Geraden (DE: Regelabstand 50 m, beidseitig gegenüber)
POST_MIN = 10.0  # m in engen Kurven
DROP = 3.0  # m Gefälle neben der Fahrbahn (bis 8 m vom Rand), ab dem eine Schutzplanke steht
# Schutzplanke (W-Profil) von unten nach oben: (Abstand hinter der Vorderkante, Höhe über der Fahrbahn); Vorderkante hw + RAIL
RAIL, RAIL_PROFILE = 0.6, [(0.06, 0.44), (0.0, 0.51), (0.08, 0.595), (0.0, 0.68), (0.06, 0.75)]
STEEL = (0.62, 0.64, 0.66)


def _runs(mask, close, min_len, loop):
    """Lücken bis close Punkte schließen, Stücke unter min_len Punkten weg (Runde: über den Start hinweg)."""
    pad = max(close, min_len)
    f = np.pad(mask, pad, mode="wrap" if loop else "edge")
    f = ndimage.binary_opening(ndimage.binary_closing(f, np.ones(close)), np.ones(min_len))
    return f[pad:-pad]


def roadside(R, rd, junctions, ground, rural, rails=()):
    """Leitpfosten und Schutzplanken an unserer Strecke (nur außerorts, Klassen RURAL, befestigt, nicht auf Brücken/in Tunneln).
    Schutzplanke: OSM barrier=guard_rail (rails = [(x, z)]), sonst wo es neben der Fahrbahn > DROP tief runtergeht, und je
    30 m vor/nach Brücken; an Einmündungen offen, Enden ins Gelände abgesenkt. Leitpfosten: beidseitig gegenüber, POST_MAX
    auf der Geraden, in Kurven enger, nicht vor einer Schutzplanke und nicht in Einmündungen.
    -> dict(delineators=[12 floats], posts=[12 floats, wie sign_posts], rail=(pos, nrm, idx, col) oder None, counts)"""
    from scipy.spatial import cKDTree
    n, s = R.n, np.arange(R.n) * STEP
    k = ndimage.gaussian_filter1d(road.curvature(R.x, R.z, R.loop), 2, mode="wrap" if R.loop else "nearest")
    nl = np.column_stack([R.t[:, 1], -R.t[:, 0]])  # links
    cls = np.array([str(c).removesuffix("_link") for c in rd["cls"]])
    ok = rural & np.isin(cls, list(RURAL)) & (rd["surface"] != 2) & ~rd["tunnel"]
    gap = np.zeros((n, 2), bool)  # Einmündung je Seite (0 links, 1 rechts), ±3 m
    for m in junctions:
        gap[(s > m["s0"] - 3) & (s < m["s1"] + 3), 0 if m["side"] > 0 else 1] = True

    need = np.zeros((n, 2), bool)
    for c, side in enumerate((1, -1)):
        lat = side * (R.hw[:, None] + np.array([1.5, 3.0, 5.0, 8.0]))
        g = ground(R.x[:, None] + nl[:, :1] * lat, R.z[:, None] + nl[:, 1:] * lat)
        need[:, c] = R.y - g.min(1) > DROP
    need |= (ndimage.binary_dilation(rd["bridge"], np.ones(13)) & ~rd["bridge"])[:, None]  # Brückenköpfe ±30 m
    tree = cKDTree(np.column_stack([R.x, R.z]))
    for wx, wz in rails:
        d = np.r_[0, np.cumsum(np.hypot(np.diff(wx), np.diff(wz)))]
        if d[-1] < 1:
            continue
        q = np.linspace(0, d[-1], int(d[-1] / 2) + 2)
        px, pz = np.interp(q, d, wx), np.interp(q, d, wz)
        tw = np.column_stack([np.gradient(px), np.gradient(pz)])
        tw /= np.linalg.norm(tw, axis=1, keepdims=True) + 1e-9
        _, j = tree.query(np.column_stack([px, pz]))
        lat = ((np.column_stack([px, pz]) - np.column_stack([R.x[j], R.z[j]])) * nl[j]).sum(1)
        hit = (np.abs(lat) > R.hw[j] + 0.2) & (np.abs(lat) < R.hw[j] + 5) & (np.abs((tw * R.t[j]).sum(1)) > 0.8)
        need[j[hit], np.where(lat[hit] > 0, 0, 1)] = True
    for c in range(2):
        need[:, c] = _runs(need[:, c] & ok, 6, 8, R.loop) & ok & ~rd["bridge"] & ~gap[:, c]  # Lücken ≤ 25 m zu, Stücke ≥ 40 m
        need[:, c] = _runs(need[:, c], 1, 3, R.loop)  # Reste an Einmündungen < 15 m weg

    # Schutzplanken: W-Profil vorn + Rückseite, Pfosten alle 2 m
    P, N, I, posts = [], [], [], []

    def strip(A, B, nrm):  # zwei Kanten (m×3) -> Dreiecke, Umlaufsinn so, dass die Seite nrm sichtbar ist
        o, m = sum(len(p) for p in P), len(A)
        P.extend([A, B]); N.extend([nrm, nrm])
        q = o + np.arange(m - 1)
        tri = np.column_stack([q, q + m, q + 1, q + 1, q + m, q + m + 1]).reshape(-1, 3)
        V = np.vstack([A, B])
        cr = np.cross(V[tri[:, 1] - o] - V[tri[:, 0] - o], V[tri[:, 2] - o] - V[tri[:, 0] - o])
        flip = (cr * np.vstack([nrm, nrm])[tri[:, 0] - o]).sum(1) > 0  # sichtbar: Kreuzprodukt gegen die Normale (wie road.pos)
        tri[flip] = tri[flip][:, [0, 2, 1]]
        I.append(tri)

    length = 0.0
    for c, side in enumerate((1, -1)):
        lab, cnt = ndimage.label(need[:, c])  # ponytail: Runde – Planke über die Start-/Ziel-Naht wird zwei Stücke mit Absenkung
        for r in range(1, cnt + 1):
            sel = np.flatnonzero(lab == r)
            if len(sel) < 2:
                continue
            m = len(sel)
            f = np.ones(m)
            for e, nb in ((0, sel[0] - 1), (m - 1, sel[-1] + 1)):  # Enden auf 10 m ins Gelände absenken, außer an Brücken
                if not (0 <= nb < n and rd["bridge"][nb]):
                    f = np.minimum(f, np.abs(np.arange(m) - e) / 2.0)
            f = np.clip(f, 0, 1)
            L = R.hw[sel] + RAIL
            inner = side * k[sel] > 0
            L = np.where(inner, np.minimum(L, 0.85 / np.maximum(np.abs(k[sel]), 1e-6)), L)  # Innenseite enger Kurven
            o2 = side * nl[sel]
            pt = lambda p, y: np.column_stack([R.x[sel] + o2[:, 0] * (L + p), R.y[sel] - 0.15 + (y + 0.15) * f, R.z[sel] + o2[:, 1] * (L + p)])
            for (p1, y1), (p2, y2) in zip(RAIL_PROFILE, RAIL_PROFILE[1:]):
                dp, dy = p2 - p1, y2 - y1
                ln = np.hypot(dp, dy)
                nrm = np.column_stack([o2[:, 0] * -dy / ln, np.full(m, dp / ln), o2[:, 1] * -dy / ln])  # zur Fahrbahn
                strip(pt(p1, y1), pt(p2, y2), nrm)
                strip(pt(p1 + 0.006, y1), pt(p2 + 0.006, y2), -nrm)  # Rückseite (Blech, von der anderen Talseite sichtbar)
            for q in np.arange(s[sel[0]], s[sel[-1]] + 0.01, 2.0):  # Pfosten hinter dem Holm
                i = int(np.clip(round(q / STEP), sel[0], sel[-1]))
                fi = f[i - sel[0]]
                if fi < 0.5:
                    continue
                x, y, z = R.at(q, side * (L[i - sel[0]] + 0.14))
                y0 = float(ground(np.array([x]), np.array([z]))[0]) - 0.3
                posts.append(_yaw_rows(x, y0, z, 0.0, 1.25, max(y - 0.05 + 0.7 * fi - y0, 0.3), 1.25))
            length += (m - 1) * STEP

    # Leitpfosten: beidseitig gegenüber, Abstand nach Kurvenradius
    dl, q = [], 5.0
    total = (n - 1) * STEP
    while q < total - 5:
        i = int(round(q / STEP))
        if ok[i] and not rd["bridge"][i]:
            for c, side in enumerate((1, -1)):
                if not need[i, c] and not gap[i, c]:
                    x, _, z = R.at(q, side * (R.hw[i] + 0.7))
                    g = float(ground(np.array([x]), np.array([z]))[0])
                    dl.append(_yaw_rows(x, g - 0.2, z, _facing(R.tan(q)), 1.0, 1.0))
        q += float(np.clip(0.25 / max(abs(k[i]), 1e-6), POST_MIN, POST_MAX))  # ponytail: R/4 nachempfunden, keine RPS-Tabelle
    rail = None
    if P:
        pos = np.vstack(P)
        rail = pos, np.vstack(N), np.vstack(I), np.tile([*STEEL, 0], (len(pos), 1))  # Alpha 0: keine Fahrbahnmarkierung
    return dict(delineators=dl, posts=posts, rail=rail, counts={"leitpfosten": len(dl), "schutzplanke_m": int(length)})


# Schrift mit Umlauten: erste vorhandene (Linux, macOS, Windows); Pillows eingebaute kann kein ä/ö/ü/ß
FONTS = ["DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/noto/NotoSans-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
         "/usr/share/fonts/liberation/LiberationSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf"]


def _font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default(size)  # ponytail: ohne Systemschrift fehlen Umlaute; Schrift ins Repo legen, falls das auffällt


def _fit(draw, text, box_w, size, stroke=0):
    while size > 10:
        f = _font(size)
        if draw.textlength(text, font=f) + 2 * stroke <= box_w:
            return f
        size -= 2
    return _font(10)


def _lines(draw, text, box_w, size, min_size):
    """Text einzeilig oder (wenn er sonst kleiner als min_size würde) an der mittleren Lücke zweizeilig -> (Zeilen, Schrift)."""
    f = _fit(draw, text, box_w, size)
    if f.size >= min_size or " " not in text and "-" not in text:
        return [text], f
    cuts = [k for k, ch in enumerate(text) if ch in " -"]
    k = min(cuts, key=lambda k: abs(k - len(text) / 2))
    lines = [text[:k + (text[k] == "-")].strip(), text[k + 1:].strip()]
    return lines, min((_fit(draw, ln, box_w, int(size * 0.75)) for ln in lines), key=lambda f: f.size)


def _write(d, lines, f, W, H, fill):
    for k, ln in enumerate(lines):
        d.text((W / 2, H * (k + 0.5) / len(lines)), ln, font=f, fill=fill, anchor="mm", stroke_width=1, stroke_fill=fill)


def draw_face(key, scale=256):
    """Ein Schildgesicht als RGBA-Bild (transparent außerhalb der Form). Höhe = scale px."""
    kind, _, text = key.partition(":")
    H = scale
    if kind in ("speed", "stop", "give_way"):
        im = Image.new("RGBA", (H, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if kind == "speed":
            d.ellipse([4, 4, H - 4, H - 4], fill=(245, 245, 240, 255))
            d.ellipse([4, 4, H - 4, H - 4], outline=(200, 20, 30, 255), width=H // 9)
            f = _fit(d, text, H * 0.56, int(H * 0.46))
            d.text((H / 2, H / 2), text, font=f, fill=(15, 15, 15, 255), anchor="mm", stroke_width=3, stroke_fill=(15, 15, 15, 255))
        elif kind == "stop":
            r = H / 2 - 4
            pts = [(H / 2 + r * np.cos(np.radians(22.5 + 45 * k)), H / 2 + r * np.sin(np.radians(22.5 + 45 * k))) for k in range(8)]
            d.polygon(pts, fill=(245, 245, 240, 255))
            r2 = r - H * 0.04
            d.polygon([(H / 2 + r2 * np.cos(np.radians(22.5 + 45 * k)), H / 2 + r2 * np.sin(np.radians(22.5 + 45 * k))) for k in range(8)],
                      fill=(200, 20, 30, 255))
            f = _fit(d, "STOP", H * 0.75, int(H * 0.3))
            d.text((H / 2, H / 2), "STOP", font=f, fill=(250, 250, 250, 255), anchor="mm", stroke_width=2, stroke_fill=(250, 250, 250, 255))
        else:
            d.polygon([(4, 10), (H - 4, 10), (H / 2, H - 6)], fill=(200, 20, 30, 255))
            d.polygon([(H * 0.2, H * 0.16), (H * 0.8, H * 0.16), (H / 2, H * 0.72)], fill=(245, 245, 240, 255))
        return im
    Hh = H if kind in ("city", "cityend") else int(H * 0.75)  # Text-Schilder (1 × 0,25 m) etwas gröber: Atlasgröße
    W = 2 * Hh if kind in ("city", "cityend") else 4 * Hh
    im = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if kind in ("city", "cityend"):
        d.rounded_rectangle([2, 2, W - 2, Hh - 2], radius=12, fill=(250, 200, 20, 255), outline=(20, 20, 20, 255), width=6)
        d.rounded_rectangle([14, 14, W - 14, Hh - 14], radius=8, outline=(20, 20, 20, 255), width=4)
        lines, f = _lines(d, text, W - 50, int(Hh * 0.36), int(Hh * 0.24))
        _write(d, lines, f, W, Hh, (15, 15, 15, 255))
        if kind == "cityend":
            d.line([(24, Hh - 24), (W - 24, 24)], fill=(200, 20, 30, 255), width=int(Hh * 0.09))
    elif kind == "street":
        d.rectangle([2, 2, W - 2, Hh - 2], fill=(245, 245, 240, 255), outline=(20, 20, 20, 255), width=8)
        f = _fit(d, text, W - 50, int(Hh * 0.55))
        _write(d, [text], f, W, Hh, (15, 15, 15, 255))
    else:  # bike/hike: Pfeilschild, Spitze rechts (":l" -> links), bis zwei Ziele untereinander
        left = text.endswith(":l")
        lines = text.removesuffix(":l").split("|")
        tip = Hh * 0.45
        pts = [(2, 2), (W - tip, 2), (W - 2, Hh / 2), (W - tip, Hh - 2), (2, Hh - 2)]
        if left:
            pts = [(W - x, y) for x, y in pts]
        ink = (200, 25, 35, 255) if kind == "bike" else (30, 90, 45, 255)
        d.polygon(pts, fill=(245, 245, 240, 255), outline=ink, width=6)
        x0, x1 = (tip + 12, W - 24) if left else (24, W - tip - 12)
        f = min((_fit(d, ln, x1 - x0, int(Hh * (0.38 if len(lines) > 1 else 0.6))) for ln in lines), key=lambda f: f.size)
        for k, ln in enumerate(lines):
            y = Hh * ((k + 0.5) / len(lines))
            d.text((x0, y), ln, font=f, fill=ink, anchor="lm", stroke_width=1, stroke_fill=ink)
    return im


def atlas(faces, width=2048, scale=128, pad=8):
    """Alle Gesichter in eine Textur (Regale nach Höhe, pad px Rand gegen Ausbluten der Mipmaps)
    -> (Image, rects[index] = (u0, v0, u1, v1)). Taunus 36 km: ~2048 × 3000 px, als S3TC ~6 MB."""
    keys = sorted(faces, key=faces.get)
    ims = [draw_face(k, scale) for k in keys]
    order = sorted(range(len(ims)), key=lambda k: -ims[k].height)
    x = y = row = 0
    pos = [None] * len(ims)
    for k in order:
        im = ims[k]
        if x + im.width + pad > width:
            x, y, row = 0, y + row, 0
        pos[k] = (x + pad // 2, y + pad // 2)
        x += im.width + pad
        row = max(row, im.height + pad)
    H = max(y + row, 4)
    H += -H % 4  # S3TC: Vielfaches von 4
    out = Image.new("RGBA", (width, H), (0, 0, 0, 0))
    rects = np.zeros((len(ims), 4), np.float32)
    for k, (im, (px, py)) in enumerate(zip(ims, pos)):
        out.paste(im, (px, py))
        rects[k] = [(px + 1) / width, (py + 1) / H, (px + im.width - 1) / width, (py + im.height - 1) / H]
    return out, rects


def test():
    assert _speed("50") == 50 and _speed("30 mph") == 30 and _speed("DE:urban") == -50 and _speed("walk") is None
    assert _dests("Oberursel 8.5; Kronberg 2,9 km; Limes Radweg") == ["Oberursel 8,5 km", "Kronberg 2,9 km"]
    assert _dests("Feldbergkastell 800 m") == ["Feldbergkastell 800 m"] and _dests("Limeserlebnispfad") == []
    # gerade Straße nach Osten, 600 m: Ampel bei 300 m, Einmündung links bei 310 m, Zebrastreifen bei 100 m,
    # Ortstafel bei 450 m (Häuser dahinter), Wegweiser mit Ziel nach Norden, Tempo 70 -> 50 ab 200 m
    x = np.arange(0, 600, STEP)
    z, y, hw = np.zeros_like(x), np.zeros_like(x), np.full(len(x), 3.0)
    R = Route(x, z, y, hw, False)
    ways = [(np.array([0.0, 200]), np.array([0.0, 0]), {"highway": "secondary", "maxspeed": "70", "name": "Hauptstraße"}),
            (np.array([200.0, 600]), np.array([0.0, 0]), {"highway": "secondary", "maxspeed": "50", "name": "Hauptstraße"}),
            (np.array([310.0, 310]), np.array([0.0, -60]), {"highway": "residential", "name": "Gartenweg"}),
            (np.array([520.0, 520]), np.array([0.0, 60]), {"highway": "residential", "name": "Am Hang"})]
    rd = dict(way=np.where(x < 200, 0, 1), cls=np.array(["secondary"] * len(x), object), bridge=np.zeros(len(x), bool),
              tunnel=np.zeros(len(x), bool))
    junc = road.junctions(x, z, hw, ways, rd["bridge"])
    nodes = [(300.0, 1.0, {"highway": "traffic_signals"}), (100.0, 0.0, {"highway": "crossing", "crossing": "zebra"}),
             (450.0, 5.0, {"traffic_sign": "city_limit", "name": "Königstein"}),
             (150.0, -6.0, {"information": "guidepost", "bicycle": "yes", "direction_north": "Feldberg 6.9; Limes Radweg"})]
    houses = np.column_stack([np.arange(460, 600, 10.0), np.full(14, 30.0)])
    f = furnish(R, rd, ways, junc, nodes, lambda x, z: np.zeros_like(np.asarray(x, float)), {"residential": 0.09}, houses)
    keys = set(f["faces"])
    assert {"speed:70", "speed:50", "city:Königstein", "street:Gartenweg", "street:Am Hang", "bike:Feldberg 6,9 km",
            "give_way"} <= keys, keys
    assert f["counts"]["ampel"] == 1 and f["counts"]["zebra"] == 1 and len(f["signals"]) == 1
    s = np.arange(len(x)) * STEP
    assert f["c0"][(s > 306) & (s < 314), 0].all() and f["c0"][(s > 516) & (s < 524), 1].all()  # Randlinie offen
    assert not f["c0"][(s > 330) & (s < 500), :2].any()
    stop = 298  # Haltlinie 2 m vor der Ampel (die vor der Einmündung steht), Mittellinie 30 m davor durchgezogen
    assert f["c0"][(s > stop - 28) & (s < stop - 2), 2].all() and not f["c0"][s > stop + 3, 2].any()
    sx = [p[3] for p in f["plates"]]
    assert any(abs(v - 200 - 5) < 1 for v in sx)  # Tempo 50 bei der Änderung
    # Wegweiser nach Norden: Spitze (lokal +X) zeigt nach -z
    g = [p for p in f["plates"] if f["faces"]["bike:Feldberg 6,9 km"] == p[12]][0]
    assert abs(g[0]) < 1e-6 and g[8] < 0, g[:12]  # X-Achse = (0, 0, -1) * Breite
    # Runde: Einmündung über den Start hinweg -> auch am Ende der Runde offen; ohne OSM-Wege kein Absturz
    c = lane_marks(10, [dict(side=1, s0=-6.0, s1=4.0)], [], loop=True)
    assert c[[0, 9], 0].all() and not c[1:9, 0].any()
    assert (road.match_ways(x, z, [], False)["way"] == -1).all()
    im, rects = atlas(f["faces"])
    assert im.width == 2048 and len(rects) == len(f["faces"]) and (rects[:, 2] > rects[:, 0]).all()
    # Leitpfosten/Schutzplanken: rechts (z > 0) Abhang bei 100–250 m, OSM-Planke links 380–460 m, Brücke 540–560 m,
    # Einmündungen links 310 m, rechts 520 m (Planke offen), ab 580 m Ort
    rd.update(surface=np.zeros(len(x), int), bridge=(x >= 540) & (x < 560))
    hang = lambda gx, gz: np.where((gx > 100) & (gx < 250) & (gz > 3.5), -6.0, 0.0)
    rs = roadside(R, rd, junc, hang, x < 580, [(np.array([380.0, 460]), np.array([-4.5, -4.5]))])
    pos, nrm, idx = rs["rail"][:3]
    right, left = pos[:, 2] > 0, pos[:, 2] < 0
    assert pos[right & (pos[:, 0] > 120) & (pos[:, 0] < 230), 1].max() > 0.7  # volle Höhe am Abhang
    assert not (left & (pos[:, 0] > 100) & (pos[:, 0] < 250)).any()  # Bergseite frei
    assert (left & (pos[:, 0] > 390) & (pos[:, 0] < 450)).any() and (right & (pos[:, 0] > 560) & (pos[:, 0] < 580)).any()
    assert not (pos[:, 0] > 540).any() or not ((pos[:, 0] > 541) & (pos[:, 0] < 559)).any()  # Brücke hat die Brüstung
    assert np.isclose(pos[right & (pos[:, 0] < 120), 1].min(), -0.15)  # Ende ins Gelände abgesenkt
    tri = pos[idx]
    face = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert ((face * nrm[idx[:, 0]]).sum(1) <= 1e-9).all()  # Umlaufsinn passt zur Normalen (beide Seiten sichtbar)
    d = np.array(rs["delineators"])[:, [3, 11]]
    assert not ((d[:, 1] > 0) & (d[:, 0] > 100) & (d[:, 0] < 250)).any()  # vor der Planke keine Leitpfosten
    assert not ((np.abs(d[:, 0] - 310) < 5) & (d[:, 1] < 0)).any() and not (d[:, 0] > 575).any()  # Einmündung, Ort
    lft = np.sort(d[d[:, 1] < 0, 0])
    assert np.allclose(np.diff(lft[lft < 300]), POST_MAX)  # Gerade: 50 m
    t = np.arange(0, np.pi, STEP / 40.0)  # Kurve R = 40 m: enger
    C = Route(40 * np.cos(t), 40 * np.sin(t), np.zeros_like(t), np.full(len(t), 3.0), False)
    rc = dict(cls=np.array(["tertiary"] * len(t), object), surface=np.zeros(len(t), int), bridge=np.zeros(len(t), bool),
              tunnel=np.zeros(len(t), bool))
    rs = roadside(C, rc, [], lambda gx, gz: np.zeros_like(gx), np.ones(len(t), bool))
    assert rs["rail"] is None and len(rs["delineators"]) >= 2 * int(np.pi * 40 / POST_MIN) - 2, len(rs["delineators"])
    print("signs ok")
