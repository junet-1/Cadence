"""Straßennetz für route.py: alle anderen Straßen und Wege als ein zusammenhängendes Mesh aus dem OSM-Graphen.

  ways_from_osm  Overpass-Wege (mit Knoten-IDs) -> [(ids, x, z, tags)]; Tunnel und Flächen raus
  chains         Graph: Wege an geteilten Knoten teilen, über Knoten mit genau zwei Enden und gleichen Eigenschaften
                 wieder zu Ketten verbinden (keine Naht mitten in einer Straße)
  build          Höhen über den ganzen Graphen (Gelände + lift; Brücken zwischen den Widerlagern gespannt; lichte Höhe über
                 anderen Straßen mit Rampen ≤ RAMP, die auch in die Anschlussstraßen laufen), dann Bänder zwischen den
                 Kreuzungen, Kreuzungsflächen aus den Kanten der Arme (Ecken gerundet), Einmündungen in unsere Fahrbahn
Verbunden ist nur, was einen OSM-Knoten teilt. Nahe oder sich kreuzende Wege ohne gemeinsamen Knoten bleiben getrennt;
wer oben liegt, sagt layer/bridge. Rein visuell: route.pos, Steigungen und Trainer hängen nicht davon ab.
"""
import mapbox_earcut
import numpy as np
from PIL import Image, ImageDraw
from scipy import sparse
from scipy.sparse.csgraph import dijkstra
from scipy.sparse.linalg import spsolve
from scipy.spatial import ConvexHull, cKDTree

import road

SAMPLE = 2.0  # m zwischen Kettenpunkten (Geometrie); Bänder bekommen jeden zweiten
RAMP = 0.06  # Rampe zu einer Brücke: Überhöhung übers Gelände nimmt je Meter (über den Graphen) so viel ab (Fuß-/Radwege: RAMP_PATH)
RAMP_PATH = 0.12
CLEAR_PATH = 3.0  # lichte Höhe über Fuß-/Radwegen (über Fahrstraßen road.CLEARANCE)
CAP = 150.0  # m: so weit darf eine Kreuzungsfläche in einen Arm reichen (spitze Ausfahrten)
ON_ROUTE_Y = 0.03  # Stück auf unserer Fahrbahn: Höhe = unsere Straße + das (nicht gezeichnet, nur als Anschluss)
EDGE_Y = 0.04  # Einmündung an unserer Fahrbahnkante: 1 cm unter deren Oberkante (+0,05)
ROADS = road.ROADS


def is_bridge(tags):
    return tags.get("bridge", "no") not in ("no", "")


def level(tags):
    """Ebene: layer, sonst Brücke 1, sonst 0 (Tunnel kommen gar nicht erst ins Netz)."""
    try:
        return int(tags["layer"])
    except (KeyError, ValueError):
        return 1 if is_bridge(tags) else 0


def style(tags):
    """-> (halbe Breite, Farbe sRGB, Markierung, lift, Belag-Code) – alles, was zwei Wege gleich aussehen lässt."""
    cls = tags.get("highway", "").removesuffix("_link")
    _, col, mark, lift = ROADS[cls]
    surf = tags.get("surface", "")
    code = road.SURFACE.get(surf, 2 if cls in road.UNPAVED_CLASS and tags.get("tracktype") != "grade1" else 0)
    return road.street_hw(tags), tuple(road.COLOR.get(surf, col)), mark if code == 0 else 0, lift, code


def ways_from_osm(elements, proj):
    """Overpass `out geom` -> [(Knoten-IDs, x, z, tags)]. Tunnel fehlen (Straße endet am Portal). Fehlende Punkte (null in
    der Geometrie) teilen den Weg, statt die Lücke zu überbrücken. Ohne IDs (alte Daten): Koordinate als ID."""
    out = []
    for el in elements:
        t = el.get("tags", {})
        if el["type"] != "way" or t.get("highway", "").removesuffix("_link") not in ROADS or t.get("area") == "yes":
            continue
        if t.get("tunnel", "no") not in ("no", "") or t.get("covered") == "yes":
            continue  # ponytail: Straße endet am Portal (Gelände hat keine Löcher); Portal-Mesh wie road.tunnel_mesh, falls es stört
        geo = el.get("geometry") or []
        ids = el.get("nodes") or [None] * len(geo)
        if len(ids) != len(geo):
            continue
        run = []
        for nid, g in list(zip(ids, geo)) + [(None, None)]:
            if g is not None:
                run.append((nid if nid is not None else (round(g["lon"] * 1e7), round(g["lat"] * 1e7)), g["lon"], g["lat"]))
                continue
            if len(run) > 1:
                a = np.array([r[1:] for r in run])
                x, z = proj.to_xz(a[:, 0], a[:, 1])
                out.append(([r[0] for r in run], np.asarray(x, float), np.asarray(z, float), t))
            run = []
    return out


def chains(ways):
    """Graph aus [(ids, x, z, tags)] -> Ketten [dict(n0, n1, x, z, tags)]. Geteilt wird an Knoten, die mehr als ein Weg
    (oder ein Weg zweimal) benutzt; über Knoten mit genau zwei Enden und gleichem Aussehen laufen die Ketten weiter."""
    count = {}
    for ids, *_ in ways:
        for n in ids:
            count[n] = count.get(n, 0) + 1
    edges = []
    for ids, x, z, t in ways:
        cut = [0] + [k for k in range(1, len(ids) - 1) if count[ids[k]] > 1] + [len(ids) - 1]
        key = style(t) + (level(t), is_bridge(t), t.get("highway"))
        for a, b in zip(cut, cut[1:]):
            edges.append(dict(n0=ids[a], n1=ids[b], x=x[a:b + 1], z=z[a:b + 1], tags=t, key=key))
    ends = {}
    for e, ed in enumerate(edges):
        ends.setdefault(ed["n0"], []).append((e, 0))
        ends.setdefault(ed["n1"], []).append((e, 1))

    def through(n, e):  # (Kante, Ende), mit der die Kette über Knoten n weiterläuft, oder None
        a = ends[n]
        if len(a) != 2 or a[0][0] == a[1][0]:
            return None
        o = a[1] if a[0][0] == e else a[0]
        return o if edges[o[0]]["key"] == edges[e]["key"] else None

    done, out = set(), []
    for e0 in range(len(edges)):
        if e0 in done:
            continue
        done.add(e0)
        seq = [(e0, False)]  # (Kante, rückwärts)
        for fwd in (True, False):
            e, n = e0, edges[e0]["n1" if fwd else "n0"]
            while (o := through(n, e)) and o[0] not in done:
                e, end = o
                done.add(e)
                item = (e, end == 1) if fwd else (e, end == 0)
                seq.append(item) if fwd else seq.insert(0, item)
                n = edges[e]["n1" if end == 0 else "n0"]
        xs, zs = [], []
        for e, rev in seq:
            x, z = (edges[e]["x"][::-1], edges[e]["z"][::-1]) if rev else (edges[e]["x"], edges[e]["z"])
            xs.append(x if not xs else x[1:])
            zs.append(z if not zs else z[1:])
        (f, frev), (l, lrev) = seq[0], seq[-1]
        out.append(dict(n0=edges[f]["n1" if frev else "n0"], n1=edges[l]["n0" if lrev else "n1"],
                        x=np.concatenate(xs), z=np.concatenate(zs), tags=edges[e0]["tags"]))
    return out


def _prep(x, z, hw):
    """Knicke runden (Ecken zweimal um d abschneiden, Enden = Knoten bleiben fest), dann alle SAMPLE m."""
    keep = np.r_[True, np.hypot(np.diff(x), np.diff(z)) > 0.05]
    keep[-1] = True
    p = np.column_stack([x, z])[keep]
    if len(p) > 2 and np.hypot(*(p[-1] - p[-2])) <= 0.05:
        p = np.delete(p, -2, axis=0)
    d0 = hw + 1.0
    for _ in range(2):
        if len(p) < 3:
            break
        seg = np.diff(p, axis=0)
        ln = np.hypot(seg[:, 0], seg[:, 1])[:, None] + 1e-9
        a = p[1:-1] - seg[:-1] / ln[:-1] * np.minimum(d0, ln[:-1] / 2)
        b = p[1:-1] + seg[1:] / ln[1:] * np.minimum(d0, ln[1:] / 2)
        p = np.vstack([p[:1], np.stack([a, b], 1).reshape(-1, 2), p[-1:]])
        p = p[np.r_[True, np.hypot(*np.diff(p, axis=0).T) > 1e-6]]
    d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
    s = np.linspace(0, d[-1], max(2, int(np.ceil(d[-1] / SAMPLE)) + 1))
    return np.interp(s, d, p[:, 0]), np.interp(s, d, p[:, 1]), s


def _crosses(A, B):
    """Alle Schnitte zweier Polylinien (n×2, m×2) -> Segment a, Anteil ta, Segment b, Anteil tb (Arrays)."""
    p, r = A[:-1], np.diff(A, axis=0)
    q, s = B[:-1], np.diff(B, axis=0)
    den = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    qp = q[None] - p[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[None, :, 1] - qp[..., 1] * s[None, :, 0]) / den
        u = (qp[..., 0] * r[:, None, 1] - qp[..., 1] * r[:, None, 0]) / den
    ia, ib = np.nonzero((den != 0) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1))
    return ia, t[ia, ib], ib, u[ia, ib]


def _simple(P):
    """Polygon ohne Selbstschnitt (nicht benachbarte Kanten berühren sich nicht)?"""
    n = len(P)
    if n < 3:
        return False
    ia, ta, ib, tb = _crosses(np.vstack([P, P[:1]]), np.vstack([P, P[:1]]))
    gap = np.abs(ia - ib)
    return not ((gap > 1) & (gap < n - 1)).any()


def _triangulate(P):
    """Earcut -> Dreiecke oder None, wenn das Polygon kaputt ist (Fläche passt nicht, Selbstschnitt)."""
    if not _simple(P):
        return None
    tri = np.asarray(mapbox_earcut.triangulate_float64(P, np.array([len(P)], np.uint32)), np.int64).reshape(-1, 3)
    area = abs(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])) / 2
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    ta = np.abs((b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0]) / 2
    if not len(tri) or abs(ta.sum() - area) > 1e-3 * area + 1e-3:
        return None
    return tri[ta > 1e-6]  # kollineare Kantenpunkte: Earcut liefert dort Dreiecke ohne Fläche


def _dedupe(pts):
    keep = np.r_[True, np.hypot(*np.diff(pts[:, [0, 1]], axis=0).T) > 1e-3]
    pts = pts[keep]
    while len(pts) > 1 and np.hypot(*(pts[0, :2] - pts[-1, :2])) <= 1e-3:
        pts = pts[:-1]
    return pts


class Piece:
    """Gezeichnetes Stück einer Kette (Samples i0..i1, Bogenmaß a wie in der Kette -> UV läuft durch)."""

    def __init__(self, c, i0, i1):
        self.c, self.a = c, c["a"][i0:i1 + 1]
        self.x, self.z, self.y, self.g = (c[k][i0:i1 + 1] for k in ("x", "z", "h", "G"))
        self.e = {s: tuple(c[k][i0:i1 + 1] for k in (f"x{s}", f"z{s}", f"y{s}")) for s in (1, -1)}
        self.cut = [False, False]  # Ende an unserer Fahrbahn abgeschnitten
        self.set = [0.0, 0.0]  # Rücksprung je Ende (m)

    def pt(self, side, f):
        """Kantenpunkt(e) (x, z, y) bei Bogen f (Kettenmaß), side +1/-1 (+ = links der Laufrichtung in atan2(z, x))."""
        ex, ez, ey = self.e[side]
        return np.column_stack([np.interp(f, self.a, ex), np.interp(f, self.a, ez), np.interp(f, self.a, ey)])


class Arm:
    """Ein Ende eines Stücks, vom Knoten weg gesehen: Bogen o ab dem Knoten, Seite +1 zeigt zum nächsten Arm (gegen den
    Uhrzeigersinn in atan2(z, x))."""

    def __init__(self, p, end):
        self.p, self.end = p, end
        self.L = p.a[-1] - p.a[0]
        # kurzes Stück zwischen zwei Kreuzungen: beide Flächen reichen ganz hinüber (überlappen auf gleicher Höhe statt Lücke)
        # ponytail: eine Fläche für den ganzen Knoten-Cluster wäre sauberer (Polygon-Vereinigung fehlt ohne neue Abhängigkeit)
        self.cap = self.L if self.L < 15 else min(0.45 * self.L, CAP)
        self.hw = p.c["hw"]

    def f(self, o):
        return self.p.a[0] + o if self.end == 0 else self.p.a[-1] - o

    def at(self, side, o):
        return self.p.pt(side if self.end == 0 else -side, self.f(np.atleast_1d(np.asarray(o, float))))

    def centre(self, o):
        f = self.f(np.atleast_1d(np.asarray(o, float)))
        return np.column_stack([np.interp(f, self.p.a, self.p.x), np.interp(f, self.p.a, self.p.z)])

    def arcs(self, o0, o1):
        """o0, Stützstellen dazwischen, o1 (in dieser Reihenfolge)."""
        o = (self.p.a - self.p.a[0]) if self.end == 0 else (self.p.a[-1] - self.p.a)
        lo, hi = min(o0, o1), max(o0, o1)
        mid = np.sort(o[(o > lo + 1e-3) & (o < hi - 1e-3)])
        return np.r_[o0, mid if o1 >= o0 else mid[::-1], o1]

    def angle(self):
        c = self.centre([0.0, min(4.0, self.L * 0.5)])
        return float(np.arctan2(c[1, 1] - c[0, 1], c[1, 0] - c[0, 0]))


def _bezier(p0, c, p1, n=6):
    t = np.arange(1, n + 1)[:, None] / (n + 1)
    xz = (1 - t) ** 2 * p0[:2] + 2 * (1 - t) * t * c + t ** 2 * p1[:2]
    return np.column_stack([xz, (1 - t[:, 0]) * p0[2] + t[:, 0] * p1[2]])


def _round(node, p0, p1):
    """Bogen um den Knoten von p0 nach p1 (gegen den Uhrzeigersinn in atan2), Radius und Höhe linear."""
    a0, a1 = np.arctan2(p0[1] - node[1], p0[0] - node[0]), np.arctan2(p1[1] - node[1], p1[0] - node[0])
    sweep = (a1 - a0) % (2 * np.pi)
    n = int(sweep / np.radians(20))
    if n < 1:
        return np.zeros((0, 3))
    t = np.arange(1, n + 1) / (n + 1)
    r = (1 - t) * np.hypot(*(p0[:2] - node)) + t * np.hypot(*(p1[:2] - node))
    a = a0 + sweep * t
    return np.column_stack([node[0] + r * np.cos(a), node[1] + r * np.sin(a), (1 - t) * p0[2] + t * p1[2]])


def _first(A, B, oa, ob):
    """Erster Schnitt (kleinster Bogen auf beiden) zweier Kantenlinien -> (Bogen a, Bogen b, Punkt) oder None."""
    ia, ta, ib, tb = _crosses(A, B)
    if not len(ia):
        return None
    sa, sb = oa[ia] + ta * (oa[ia + 1] - oa[ia]), ob[ib] + tb * (ob[ib + 1] - ob[ib])
    k = np.argmin(sa + sb)
    return sa[k], sb[k], A[ia[k]] + ta[k] * (A[ia[k] + 1] - A[ia[k]])


def junction(arms):
    """Kreuzungsfläche aus den Kanten der Arme -> (Punkte m×3, Dreiecke, Rücksprung je Arm, "ok" | "simple" | "hull").
    Je zwei benachbarte Arme: Innenecke = erster Schnitt der zugewandten Kanten, mit einem Bogen (corner_radius) gerundet;
    die Arme enden hinter der Ecke. Außenseite einer Kurve (> 180°): Bogen um den Knoten. Fast gerade (160–180°, oder
    bis 200° bei Breitenwechsel): gerade Verbindung (Breitenwechsel als Keil). Schneiden sich die Kanten nicht auf den Armen (Arm zu kurz, oder spitz
    und länger als CAP): Ecke gerader Kanten, gekappt – bei spitzen Winkeln reichen beide Arme dann bis CAP (Zwickel gefüllt). Polygon = Kanten der Arme bis zum Rücksprung + Ecken -> Earcut;
    kaputt -> nur Querschnitte verbinden ("simple"), sonst konvexe Hülle ("hull": deckt alles ab, pflastert Zwickel)."""
    n = len(arms)
    order = np.argsort([a.angle() for a in arms])
    A = [arms[k] for k in order]
    ang = [a.angle() for a in A]
    node = A[0].centre(0.0)[0]
    pairs, req = [], [0.5] * n
    for i in range(n):
        j = (i + 1) % n
        gap = (ang[j] - ang[i]) % (2 * np.pi) if n > 1 else 2 * np.pi
        if gap >= np.pi + 0.35 or (gap >= np.pi and abs(A[i].hw - A[j].hw) < 0.3):  # Außenseite: Bogen um den Knoten
            pairs.append(("round",))
            continue
        if gap > np.pi - 0.35:
            pairs.append(("straight",))
            t = max(1.0, 3 * abs(A[i].hw - A[j].hw))
            req[i], req[j] = max(req[i], t), max(req[j], t)
            continue
        oa, ob = A[i].arcs(0, A[i].cap), A[j].arcs(0, A[j].cap)
        hit = _first(A[i].at(1, oa)[:, :2], A[j].at(-1, ob)[:, :2], oa, ob)
        R = road.corner_radius(min(A[i].hw, A[j].hw))
        if hit is None:  # Ecke nicht auf den Armen (einer zu kurz, oder spitz und lang): Ecke gerader Kanten, gekappt
            sin, cos = np.sin(gap), np.cos(gap)
            pairs.append(("straight",))
            req[i] = max(req[i], (A[j].hw + A[i].hw * cos) / sin + R)
            req[j] = max(req[j], (A[i].hw + A[j].hw * cos) / sin + R)
            continue
        pairs.append(("corner", hit[0] + R, hit[1] + R, hit[2]))
        req[i], req[j] = max(req[i], hit[0] + R), max(req[j], hit[1] + R)
    s = [min(r, a.cap) for r, a in zip(req, A)]
    # Ecke hinter einem zu kurzen Arm (Rücksprung gekappt): gerade Verbindung statt Bogen über die Ecke hinaus
    pairs = [("straight",) if p[0] == "corner" and (p[1] > s[i] + 1e-6 or p[2] > s[(i + 1) % n] + 1e-6) else p
             for i, p in enumerate(pairs)]

    def outline(pairs):
        entry, exit_ = [0.0] * n, [0.0] * n
        for i in range(n):
            p, j = pairs[i], (i + 1) % n
            exit_[i] = s[i] if p[0] == "straight" else p[1] if p[0] == "corner" else 0.0
            entry[j] = s[j] if p[0] == "straight" else p[2] if p[0] == "corner" else 0.0
        pts = []
        for i in range(n):
            j = (i + 1) % n
            pts.append(A[i].at(-1, A[i].arcs(entry[i], s[i])))
            pts.append(A[i].at(1, A[i].arcs(s[i], exit_[i])))
            p0, p1 = pts[-1][-1], A[j].at(-1, entry[j])[0]
            if pairs[i][0] == "corner":
                pts.append(_bezier(p0, pairs[i][3], p1))
            elif pairs[i][0] == "round":
                pts.append(_round(node, p0, p1))
        P = _dedupe(np.vstack(pts))
        return P, _triangulate(P[:, :2]) if len(P) >= 3 else None

    P, tri = outline(pairs)
    mode = "ok"
    if tri is None:  # zweiter Versuch: nur gerade Verbindungen an den Rücksprüngen (Vieleck aus den Querschnitten)
        P, tri = outline([("straight",)] * n)
        mode = "simple"
    if tri is None:
        mode = "hull"  # ponytail: konvexe Hülle deckt alles ab, pflastert aber Zwickel; Cluster naher Knoten wäre sauberer
        P = P[ConvexHull(P[:, :2]).vertices]
        tri = _triangulate(P[:, :2])
    out = [0.0] * n
    for k, i in enumerate(order):
        out[i] = s[k]
    return P, tri, out, mode


class Near:
    """Unsere Strecke (signs.Route) für die Nebenstraßen: Abstand quer/längs, Ebene je Punkt."""

    def __init__(self, route, rlevel):
        self.R, self.lvl = route, rlevel
        self.L = route.n * road.STEP if route.loop else np.inf  # Runde: Bogen u läuft über den Start weiter
        self.tree = cKDTree(np.column_stack([route.x, route.z]))
        self.nl = np.column_stack([route.t[:, 1], -route.t[:, 0]])  # links wie signs.Route.at

    def rel(self, x, z):
        """-> (Index, quer links +, längs, Bogen u auf der Route)"""
        _, j = self.tree.query(np.column_stack([x, z]))
        rel = np.column_stack([x - self.R.x[j], z - self.R.z[j]])
        lat, along = (rel * self.nl[j]).sum(1), (rel * self.R.t[j]).sum(1)
        return j, lat, along, j * road.STEP + along

    def hw(self, u):
        return np.interp(u / road.STEP, np.arange(self.R.n), self.R.hw)

    def wrap(self, u):
        return u % self.L if np.isfinite(self.L) else u


def mouth(arm, near):
    """Einmündung eines an unserer Fahrbahn abgeschnittenen Arms: Kanten des Arms bis an unsere Fahrbahnkante, Ecken mit
    corner_radius gerundet, die Fläche liegt an der Kante auf unserer Höhe (EDGE_Y) und fällt zur Nebenstraße ab.
    -> (Punkte m×3, fest je Punkt, Dreiecke, Rücksprung) oder None (zu flach/kurz: Ende bleibt gerade)."""
    o = arm.arcs(0, arm.L * 0.9)
    j, lat, *_ = near.rel(*arm.centre(o).T)
    side = np.sign(lat[-1])
    if side == 0:
        return None
    hit = {}
    for s in (1, -1):
        e = arm.at(s, o)
        j, lat, along, u = near.rel(e[:, 0], e[:, 1])
        d = side * lat - near.R.hw[j]
        k = np.flatnonzero(d >= 0)
        if not len(k):
            return None
        k = k[0]
        t = 0.0 if k == 0 else -d[k - 1] / (d[k] - d[k - 1])
        oc = o[max(k - 1, 0)] + t * (o[k] - o[max(k - 1, 0)])
        hit[s] = (oc, arm.at(s, oc)[0], u[max(k - 1, 0)] + t * (u[k] - u[max(k - 1, 0)]))
    c = arm.centre([hit[1][0], hit[1][0] + 2.0])
    tv = np.diff(c, axis=0)[0]
    tv /= np.hypot(*tv) + 1e-9
    jj = near.rel(*c[:1].T)[0][0]
    if abs(near.R.t[jj, 0] * tv[1] - near.R.t[jj, 1] * tv[0]) < 0.34:  # unter 20°: läuft neben uns her, kein Mund
        return None
    R = road.corner_radius(arm.hw)
    s_ = min(max(hit[1][0], hit[-1][0]) + R, arm.L * 0.9)
    if s_ <= max(hit[1][0], hit[-1][0]) + 0.2:
        return None
    up, um = hit[1][2], hit[-1][2]
    if np.isfinite(near.L):  # Runde: kürzester Weg über die Start-/Ziel-Naht
        up = um + (up - um + near.L / 2) % near.L - near.L / 2
    if abs(up - um) > 2 * (arm.hw / 0.34 + R) + 4:
        return None  # Ecken liegen weit auseinander (Sprung zum Nachbarpunkt in einer Kehre): Ende bleibt gerade
    dlt = 1.0 if up >= um else -1.0
    uq = np.r_[np.arange(um - dlt * R, up + dlt * R, dlt * 2.0), up + dlt * R]

    def edge(u):  # Punkte auf unserer Fahrbahnkante, Höhe knapp unter der Oberkante
        u = near.wrap(u)
        x, y, z = near.R.at(u, side * near.hw(u))
        return np.column_stack([x, z, y - 0.05 + EDGE_Y])

    Q = edge(uq)
    op, om = min(hit[1][0] + R, s_), min(hit[-1][0] + R, s_)
    pts = [Q, _bezier(Q[-1], hit[1][1][:2], arm.at(1, op)[0]), arm.at(1, arm.arcs(op, s_)),
           arm.at(-1, arm.arcs(s_, om)), _bezier(arm.at(-1, om)[0], hit[-1][1][:2], Q[0])]
    fixed = np.r_[np.ones(len(Q), bool), np.zeros(sum(len(p) for p in pts[1:]), bool)]
    P = np.vstack(pts)
    keep = np.r_[True, np.hypot(*np.diff(P[:, :2], axis=0).T) > 1e-3]
    P, fixed = P[keep], fixed[keep]
    tri = _triangulate(P[:, :2])
    return None if tri is None else (P, fixed, tri, s_)


def own_key(tags):
    """Dieselbe Straße wie unsere (Klasse, Name, Nummer) – nur damit wird unsere geglättete Linie weiter als ±hw gesucht."""
    return tags.get("highway"), tags.get("name"), tags.get("ref")


def build(ways, ground, route=None, rlevel=None, gcolor=None, rdev=None, rkey=None):
    """Netz-Mesh aus [(ids, x, z, tags)]. ground(x, z) -> Gelände; route = signs.Route unserer Strecke (oder None),
    rlevel = Ebene je Routenpunkt (Brücke 1, Tunnel -1, sonst 0); gcolor(x, z) -> sRGB der Böschungen;
    rdev = Abstand unserer geglätteten Linie zur echten (m), rkey = own_key der befahrenen Straße je Routenpunkt (oder None).
    -> dict(mesh=(pos, nrm, idx, col, uv, uv2) oder None, structures=[Brücken-Meshes], stats)"""
    C = []
    for c in chains(ways):
        hw, col, mark, lift, code = style(c["tags"])
        x, z, a = _prep(c["x"], c["z"], hw)
        if a[-1] < 0.5:
            continue
        tx, tz = np.gradient(x), np.gradient(z)
        ln = np.hypot(tx, tz) + 1e-9
        tx, tz = tx / ln, tz / ln
        k = np.gradient(np.unwrap(np.arctan2(tz, tx)), a) if len(a) > 2 else np.zeros(len(a))
        lim = 0.85 / np.maximum(np.abs(k), 1e-6)  # Innenseite enger Kurven nicht falten
        cls = c["tags"].get("highway", "").removesuffix("_link")
        C.append(dict(c, x=x, z=z, a=a, tx=tx, tz=tz, k=k, hw=hw, col=col, mark=mark, lift=lift, code=code, lvl=level(c["tags"]),
                      bridge=is_bridge(c["tags"]), rank=road.RANK.get(cls, 0), clear=road.CLEARANCE if cls in road.RANK else CLEAR_PATH,
                      op=np.where(k > 0, np.minimum(hw, lim), hw), om=np.where(k < 0, np.minimum(hw, lim), hw)))
    stats = dict(chains=len(C), junctions=0, simple=0, fallback=0, mouths=0, crossings=0, clearance_min=np.inf)
    if not C:
        return dict(mesh=None, structures=[], stats=stats, tris=None)

    # --- alle Kettenpunkte; Kettenenden sind die Knoten (gemeinsame Variable -> gleiche Höhe an jeder Kreuzung)
    nodes = {}
    for c in C:
        for n in (c["n0"], c["n1"]):
            nodes.setdefault(n, len(nodes))
    off = np.cumsum([0] + [len(c["a"]) for c in C])
    nS, nN = off[-1], len(nodes)
    X, Z = np.concatenate([c["x"] for c in C]), np.concatenate([c["z"] for c in C])
    TX, TZ = np.concatenate([c["tx"] for c in C]), np.concatenate([c["tz"] for c in C])
    cid = np.repeat(np.arange(len(C)), np.diff(off))
    vid = np.empty(nS, np.int64)
    nxt = nN
    for i, c in enumerate(C):
        m = len(c["a"])
        vid[off[i]] = nodes[c["n0"]]
        vid[off[i] + m - 1] = nodes[c["n1"]]
        vid[off[i] + 1:off[i] + m - 1] = nxt + np.arange(m - 2)
        nxt += m - 2
    nV = nxt
    first = np.zeros(nS, bool)
    first[off[:-1]] = True
    k1 = np.flatnonzero(~first)
    e0, e1 = vid[k1 - 1], vid[k1]
    w = np.maximum(np.hypot(X[k1] - X[k1 - 1], Z[k1] - Z[k1 - 1]), 1e-3)
    NP = np.zeros((nN, 2))
    ends_ = np.r_[off[:-1], off[1:] - 1]
    NP[vid[ends_]] = np.column_stack([X[ends_], Z[ends_]])
    lift = np.array([c["lift"] for c in C])[cid]
    G = ground(X, Z)
    tgt = G + lift

    # --- unsere Strecke: was darauf liegt, zeichnet road.pos; ebenerdige Wege enden an der Kante (Einmündung)
    on = np.zeros(nS, bool)
    floor_src = []  # (Kettenpunkt-Indizes, Höhe, Rampe): lichte Höhe über unserer Strecke
    if route is not None:
        near = Near(route, rlevel)
        j, lat, along, _ = near.rel(X, Z)
        cos = np.abs(TX * route.t[j, 0] + TZ * route.t[j, 1])
        lvl = np.array([c["lvl"] for c in C])[cid]
        cover = (np.abs(lat) < route.hw[j] + 1.0) & (np.abs(along) < road.STEP) & (cos > 0.7)
        if rdev is not None:  # unsere eigene Straße: in Kehren liegt die echte Linie bis 6 m außerhalb der geglätteten
            ids = {}
            ck = np.array([ids.setdefault(own_key(c["tags"]), len(ids)) for c in C])[cid]
            rk = np.array([-1 if k is None else ids.get(k, -2) for k in rkey])[j]
            cover |= (ck == rk) & (np.abs(lat) < route.hw[j] + 1.0 + rdev[j]) & (np.abs(along) < road.STEP) & (cos > 0.5)
        inside = (np.abs(lat) < route.hw[j]) & (np.abs(along) < 0.75 * road.STEP) & (lvl == rlevel[j])
        on = cover | inside
        tgt = np.where(on, route.y[j] + ON_ROUTE_Y, tgt)
        over = (lvl > rlevel[j]) & (np.abs(lat) < route.hw[j] + 1.5) & (np.abs(along) < road.STEP) & (cos < 0.7)
        over &= tgt > route.y[j] - road.CLEARANCE  # weit unter uns (Kehren, Hang): Gelände trennt schon
        if over.any():
            path = np.array([c["rank"] == 0 for c in C])[cid]
            for sel, ramp in ((over & ~path, RAMP), (over & path, RAMP_PATH)):
                if sel.any():
                    floor_src.append((np.flatnonzero(sel), route.y[j[sel]] + road.CLEARANCE, ramp))
            stats["crossings"] += len(np.unique(cid[over]))

    # --- Kreuzungen ohne gemeinsamen Knoten: obere Ebene bekommt lichte Höhe über der unteren
    tree = cKDTree(np.column_stack([X, Z]))
    cross = []  # (obere Kette, Punkt, untere Kette, Bogen auf der unteren)
    for u, cu in enumerate(C):
        if cu["lvl"] < 1:
            continue
        P = np.column_stack([cu["x"], cu["z"]])
        cand = np.unique(np.concatenate(tree.query_ball_point(P, SAMPLE * 1.2)).astype(np.int64))
        for o in np.unique(cid[cand]):
            co = C[o]
            if o == u or co["lvl"] >= cu["lvl"]:
                continue
            ks = cand[cid[cand] == o] - off[o]
            k0, k1 = max(ks.min() - 2, 0), min(ks.max() + 3, len(co["a"]))
            Q = np.column_stack([co["x"][k0:k1], co["z"][k0:k1]])
            box = np.flatnonzero(((P >= Q.min(0) - 2.5) & (P <= Q.max(0) + 2.5)).all(1))
            p0, p1 = max(box.min() - 1, 0), min(box.max() + 2, len(P))  # nur der Teil der oberen Kette über Q
            ia, ta, ib, tb = _crosses(P[p0:p1], Q)
            ia = ia + p0
            shared = [nodes[n] for n in {cu["n0"], cu["n1"]} & {co["n0"], co["n1"]}]
            for q in range(len(ia)):
                pt = P[ia[q]] + ta[q] * (P[ia[q] + 1] - P[ia[q]])
                if any(np.hypot(*(pt - NP[v])) < 1.5 for v in shared):
                    continue  # am gemeinsamen Knoten: verbunden, keine Kreuzung
                if any(cu_ == u and co_ == o and np.hypot(*(pt - p_)) < 1.0 for cu_, p_, co_, _ in cross[-4:]):
                    continue  # Schnitt genau auf einem Stützpunkt: zweimal gefunden
                cross.append((u, pt, o, co["a"][k0 + ib[q]] + tb[q] * (co["a"][k0 + ib[q] + 1] - co["a"][k0 + ib[q]])))
    stats["crossings"] += len(cross)

    # --- Höhen: Gelände + lift, Rampen zur lichten Höhe über den Graphen (≤ RAMP), Brücken zwischen den Widerlagern
    hv = np.full(nV, -np.inf)
    np.maximum.at(hv, vid, tgt)
    onv = np.zeros(nV, bool)
    np.logical_or.at(onv, vid, on)
    brs = np.array([c["bridge"] for c in C])[cid]
    allb = np.ones(nV, bool)
    np.logical_and.at(allb, vid, brs)
    deg = np.bincount(vid, minlength=nV)
    unknown = allb & ~onv & ~((np.arange(nV) < nN) & (deg == 1))
    W = sparse.coo_matrix((1 / w, (e0, e1)), shape=(nV, nV)).tocsr()
    W = W + W.T
    Lap = sparse.diags(np.asarray(W.sum(1)).ravel()) - W
    free = sparse.diags((~onv).astype(float))
    Wf = free @ W @ free  # Kegel laufen nicht über unsere Straße (deren Höhe steht fest) in Seitenstraßen
    base_t = hv.copy()
    h = base_t.copy()
    for _ in range(2):  # zweiter Durchgang: untere Brücken haben jetzt ihre Höhe (gestapelte Ebenen)
        # Bezug unten: ebenerdige Straße = Gelände (nicht die eigene Rampe, sonst schaukeln sich verbundene Rampen hoch)
        ref = np.where(allb, h, base_t)
        src = list(floor_src)
        for u, pt, o, fo in cross:
            co, cu = C[o], C[u]
            hl = np.interp(fo, co["a"], ref[vid[off[o]:off[o + 1]]])
            k = np.flatnonzero(np.hypot(cu["x"] - pt[0], cu["z"] - pt[1]) < co["hw"] + 1.5) + off[u]
            k = k[~on[k]]  # unsere eigene Brücke: Höhe kommt aus road_profile
            if not len(k) or base_t[vid[k]].max() < hl - co["clear"]:
                continue  # Gelände trennt schon (Berg: layer ist nur örtliche Reihenfolge), nichts anheben
            src.append((k, np.full(len(k), hl + co["clear"]), RAMP if cu["rank"] else RAMP_PATH))
        floor = np.full(nV, -np.inf)
        # Kegel relativ zum Gelände: die Überhöhung an der Quelle nimmt um ramp je Meter ab, über alle Anschlüsse (am Hang
        # folgt die Rampe dem Hang; absolut gerechnet hinge eine Bergstraße bergab bald Hunderte Meter in der Luft)
        for ramp in {r for *_, r in src}:
            Ev = np.full(nV, -np.inf)  # je Punkt die größte Überhöhung (COO würde doppelte Quellen addieren)
            sv = np.concatenate([vid[s] for s, _, r in src if r == ramp])
            np.maximum.at(Ev, sv, np.concatenate([v for _, v, r in src if r == ramp]) - base_t[sv])
            sv = np.flatnonzero(Ev > 0)
            if not len(sv):
                continue
            E = Ev[sv]
            top = E.max()
            Gs = sparse.coo_matrix(((top - E) / ramp + 1e-6, (np.full(len(sv), nV), sv)), shape=(nV + 1, nV + 1)).tocsr()
            Wg = sparse.bmat([[Wf, None], [None, sparse.csr_matrix((1, 1))]]).tocsr() + Gs
            D = dijkstra(Wg, directed=False, indices=nV, limit=top / ramp)[:nV]
            floor = np.maximum(floor, base_t + top - ramp * D)
        base = np.where(onv, base_t, np.maximum(base_t, floor))
        h = base.copy()
        if unknown.any():
            u_ = np.flatnonzero(unknown)
            kb = np.flatnonzero(~unknown)
            A = Lap[u_][:, u_] + sparse.identity(len(u_)) * 1e-6
            rhs = -(Lap[u_][:, kb] @ base[kb]) + 1e-6 * base[u_]
            h[u_] = np.maximum(spsolve(A.tocsc(), rhs), base[u_])
    for u, pt, o, fo in cross:
        hl = np.interp(fo, C[o]["a"], h[vid[off[o]:off[o + 1]]])
        ku = np.hypot(C[u]["x"] - pt[0], C[u]["z"] - pt[1]).argmin() + off[u]
        hu = h[vid[ku]]
        if on[ku] or abs(hu - hl) > 50:  # unsere Straße oben / Gelände trennt weit: keine Netz-Kreuzung
            continue
        stats["clearance_min"] = min(stats["clearance_min"], hu - hl)
        if hu - hl < C[o]["clear"] - 0.1:
            stats.setdefault("low_clear", []).append((pt, hu - hl, C[u]["tags"], C[o]["tags"]))

    up_ = h[vid] - (G + lift) > 0.2
    stats["raised"] = float(up_.mean())  # Anteil der Netzpunkte > 20 cm über dem Gelände (Rampen zu Brücken)
    # --- Kanten je Kettenpunkt: am Boden folgt der Querschnitt dem Hang, erhöht (Rampe, Brücke) liegt er eben
    hs = h[vid]
    for i, c in enumerate(C):
        sl = slice(off[i], off[i + 1])
        c["h"], c["G"] = hs[sl], G[sl]
        nx, nz = -c["tz"], c["tx"]
        for s, o in ((1, c["op"]), (-1, c["om"])):
            c[f"x{s}"], c[f"z{s}"] = c["x"] + s * nx * o, c["z"] + s * nz * o
    EX = np.concatenate([np.r_[c["x1"], c["x-1"]] for c in C])
    EZ = np.concatenate([np.r_[c["z1"], c["z-1"]] for c in C])
    GE = ground(EX, EZ)
    k = 0
    for c in C:
        m = len(c["a"])
        fl = 0.0 if c["bridge"] else np.clip(1 - (c["h"] - c["G"] - c["lift"]) / 0.5, 0, 1)
        c["y1"] = c["h"] + (GE[k:k + m] - c["G"]) * fl
        c["y-1"] = c["h"] + (GE[k + m:k + 2 * m] - c["G"]) * fl
        k += 2 * m

    # --- Stücke: was nicht auf unserer Strecke liegt; ein abgeschnittenes Ende behält den letzten Punkt auf der Fahrbahn
    pieces, arms = [], {}
    for i, c in enumerate(C):
        keep = ~on[off[i]:off[i + 1]]
        lab = np.flatnonzero(np.diff(np.r_[0, keep.astype(int), 0]))
        for r0, r1 in zip(lab[::2], lab[1::2] - 1):
            i0, i1 = max(r0 - 1, 0), min(r1 + 1, len(keep) - 1)
            if i1 - i0 < 1:
                continue
            p = Piece(c, i0, i1)
            p.cut = [r0 > 0, r1 < len(keep) - 1]
            pieces.append(p)
            for end, n in ((0, c["n0"]), (1, c["n1"])):
                if not p.cut[end]:
                    arms.setdefault(n, []).append(Arm(p, end))

    V, Col, UV, UV2, I, fixed, kind = [], [], [], [], [], [], []

    def emit(P3, tri, col, mark, code, centre, hw=1000.0, fx=None, k=0):
        o = sum(len(v) for v in V)
        V.append(P3[:, [0, 2, 1]])
        Col.append(np.tile([*col, mark], (len(P3), 1)))
        UV.append(P3[:, :2] - centre if hw == 1000.0 else centre)
        UV2.append(np.tile([hw, road.uv2_code(code)], (len(P3), 1)))
        I.append(tri + o)
        fixed.append(np.zeros(len(P3), bool) if fx is None else fx)
        kind.append(np.full(len(P3), k))

    def best(ps):
        c = max((p.c for p in ps), key=lambda c: (c["rank"], c["hw"]))
        return c["col"], c["code"]

    for n, al in arms.items():
        if len(al) < 2:
            continue
        P, tri, s, mode = junction(al)
        stats["junctions"] += 1
        stats["simple"] += mode == "simple"
        stats["fallback"] += mode == "hull"
        if mode == "hull":
            stats.setdefault("fallback_at", []).append(al[0].centre(0.0)[0])
        for arm, sv in zip(al, s):
            arm.p.set[arm.end] = sv
        if tri is not None:
            col, code = best([a.p for a in al])
            emit(P, tri, col, 0, code, al[0].centre(0.0)[0], k=2)
    if route is not None:
        for p in pieces:
            for end in (0, 1):
                if p.cut[end] and (m := mouth(Arm(p, end), near)):
                    P, fx, tri, s = m
                    p.set[end] = s
                    stats["mouths"] += 1
                    emit(P, tri, p.c["col"], 0, p.c["code"], P[0, :2], fx=fx, k=3)

    # --- Bänder zwischen den Rücksprüngen (+ Böschung, wo die Straße über dem Gelände liegt)
    structures, skirts = [], []
    for p in pieces:
        c = p.c
        f0, f1 = p.a[0] + p.set[0], p.a[-1] - p.set[1]
        if f1 - f0 < 0.05:
            continue
        ks = np.abs(c["k"][np.searchsorted(c["a"], p.a[0]):][:len(p.a)])
        sel = (p.a > f0 + 0.3) & (p.a < f1 - 0.3) & ((np.arange(len(p.a)) % 2 == 1) | (ks > 1 / 60))  # Kurven: alle 2 m
        mid = p.a[sel]
        f = np.r_[f0, mid, f1]
        L, R = p.pt(1, f), p.pt(-1, f)
        m = len(f)
        S = np.stack([L, R], 1).reshape(-1, 3)
        q = 2 * np.arange(m - 1)
        tri = np.column_stack([q, q + 2, q + 1, q + 1, q + 2, q + 3]).reshape(-1, 3)
        uv = np.column_stack([np.tile([c["hw"], -c["hw"]], m), np.repeat(f, 2)])
        emit(S, tri, c["col"], c["mark"], c["code"], uv, hw=c["hw"])
        if c["bridge"]:
            structures.append(road.bridge_mesh(p.x, p.y, p.z, np.full(len(p.x), c["hw"]), np.ones(len(p.x), bool), ground))
            continue
        el = np.interp(f, p.a, p.y) - np.interp(f, p.a, p.g) - c["lift"]
        run = el > 0.2
        run = run | np.r_[run[1:], False] | np.r_[False, run[:-1]]  # ein Abschnitt Übergang davor und danach
        if run.sum() < 2:
            continue
        for side, E in ((1, L), (-1, R)):
            nxz = np.column_stack([-np.interp(f, c["a"], c["tz"]), np.interp(f, c["a"], c["tx"])]) * side
            dist = np.clip(1.5 * np.maximum(el, 0) + 0.3, 0.3, 15.0)
            skirts.append((c, side, f, E, E[:, :2] + nxz * dist[:, None], run))
    # Böschungen zuletzt: nur wo sie nicht auf eine andere Fahrbahn fallen (Raster 1 m aus allen Fahrbahndreiecken)
    if skirts:
        sp = np.vstack([np.vstack([E[:, :2], ft]) for _, _, _, E, ft, _ in skirts])  # Raster nur so groß wie die Böschungen
        lo = sp.min(0) - 20
        size = tuple((sp.max(0) + 20 - lo).astype(int) + 1)
        mask = Image.new("L", size, 0)
        md = ImageDraw.Draw(mask)
        o = 0
        for P3, tri in zip(V, I):
            for t in (P3[tri - o][:, :, [0, 2]] - lo):
                md.polygon([tuple(v) for v in t], fill=255)
            o += len(P3)
        mask = np.asarray(mask)
        on_road = lambda q: mask[np.clip(q[:, 1] - lo[1], 0, size[1] - 1).astype(int), np.clip(q[:, 0] - lo[0], 0, size[0] - 1).astype(int)] > 0
    for c, side, f, E, foot, run in skirts:
        # fällt die Böschung auf eine andere Fahrbahn: kürzer (½, ¼), zuletzt fast senkrecht – nie Lücken (Sägezahn)
        dv, scale = foot - E[:, :2], np.full(len(f), 0.02)
        un = dv / (np.linalg.norm(dv, axis=1, keepdims=True) + 1e-9)
        probe = lambda d: E[:, :2] + un * np.maximum(d, 1.5)[:, None]  # ≥ 1,5 m: die eigene Kante im Raster nicht mitzählen
        for k in (0.25, 0.5, 1.0):
            d = np.linalg.norm(dv, axis=1) * k
            scale = np.where(~on_road(probe(d)) & ~on_road(probe(d / 2)), k, scale)
        foot = E[:, :2] + dv * scale[:, None]
        Fp = np.column_stack([foot, ground(foot[:, 0], foot[:, 1]) - 0.05])
        for r0, r1 in zip(*[np.flatnonzero(np.diff(np.r_[0, run.astype(int), 0]))[k::2] for k in (0, 1)]):
            if r1 - r0 < 2:
                continue
            Sk = np.stack([E[r0:r1], Fp[r0:r1]], 1).reshape(-1, 3)
            q = 2 * np.arange(r1 - r0 - 1)
            gc = gcolor(foot[r0:r1, 0], foot[r0:r1, 1]) if gcolor else np.tile([.33, .38, .22], (r1 - r0, 1))
            o = sum(len(v) for v in V)
            V.append(Sk[:, [0, 2, 1]])
            Col.append(np.column_stack([np.repeat(gc, 2, axis=0), np.zeros(len(Sk))]))
            UV.append(np.column_stack([np.tile([side * c["hw"], side * (c["hw"] + 5)], r1 - r0), np.repeat(f[r0:r1], 2)]))
            UV2.append(np.tile([c["hw"], road.uv2_code(0)], (len(Sk), 1)))
            I.append(np.column_stack([q, q + 2, q + 1, q + 1, q + 2, q + 3]).reshape(-1, 3) + o)
            fixed.append(np.zeros(len(Sk), bool))  # Oberkante hebt sich mit der Fahrbahn, sonst bliebe deren Kante unter dem Gelände
            kind.append(np.full(len(Sk), 1))
    if not V:
        return dict(mesh=None, structures=[m for m in structures if m], stats=stats, tris=None)
    pos, col, uv, uv2, idx = np.vstack(V), np.vstack(Col), np.vstack(UV), np.vstack(UV2), np.vstack(I)
    fixed, kind = np.concatenate(fixed), np.concatenate(kind)

    # --- gleiche Stelle = gleiche Höhe (Kanten von Band und Kreuzungsfläche sind dieselben Punkte); dann nirgends Gelände
    # über der Fahrbahn: Dreiecke prüfen (Mitte, Kantenmitten), zu tiefe Ecken anheben – je Stelle, also überall gleich
    key = np.unique(np.round(pos[:, [0, 2]] * 1000).astype(np.int64), axis=0, return_inverse=True)[1].ravel()
    nk = key.max() + 1
    yk = np.full(nk, -np.inf)
    np.maximum.at(yk, key, pos[:, 1])
    pos[:, 1] = yk[key]
    lock = np.zeros(nk, bool)
    np.logical_or.at(lock, key, fixed)
    road_t = kind[idx[:, 0]] != 1  # Fahrbahn (Band 0, Kreuzung 2, Einmündung 3), nicht Böschung (1)
    for _ in range(6):
        a, b, c3 = pos[idx[road_t, 0]], pos[idx[road_t, 1]], pos[idx[road_t, 2]]
        probe = [(a + b + c3) / 3, (a + b) / 2, (b + c3) / 2, (a + c3) / 2]
        need = np.max([ground(p[:, 0], p[:, 2]) + 0.02 - p[:, 1] for p in probe], axis=0)
        bad = need > 1e-3
        if not bad.any():
            break
        up_ = np.zeros(nk)
        for col_ in range(3):
            np.maximum.at(up_, key[idx[road_t, col_][bad]], need[bad])
        up_[lock] = 0
        pos[:, 1] += up_[key]
    stats["poke"] = float(np.max([ground(p[:, 0], p[:, 2]) - p[:, 1] for p in
                                  [(pos[idx[road_t, 0]] + pos[idx[road_t, 1]] + pos[idx[road_t, 2]]) / 3]]))
    f_ = np.cross(pos[idx[:, 1]] - pos[idx[:, 0]], pos[idx[:, 2]] - pos[idx[:, 0]])
    f_ *= np.where(f_[:, 1:2] < 0, -1, 1)
    nk_ = np.zeros((nk, 3))
    for col_ in range(3):
        np.add.at(nk_, key[idx[:, col_]], f_)
    nrm = nk_[key] / (np.linalg.norm(nk_[key], axis=1, keepdims=True) + 1e-9)
    nrm[nrm[:, 1] < 0] *= -1
    return dict(mesh=(pos, nrm, idx, col, uv, uv2), structures=[m for m in structures if m], stats=stats,
                tris=pos[idx][road_t], kind=kind)


# --- Prüfungen (Selbsttest und Bau-Statistik) ---------------------------------------------------------------------

def coverage(pos, idx, pts):
    """Wie viele Dreiecke decken jeden Punkt (k×2, xz) ab?"""
    cnt = np.zeros(len(pts), int)
    T = pos[idx][:, :, [0, 2]]
    tree = cKDTree(pts)
    for t in T:
        lo, hi = t.min(0), t.max(0)
        cand = tree.query_ball_point((lo + hi) / 2, np.hypot(*(hi - lo)) / 2 + 1e-6)
        if not cand:
            continue
        p = pts[cand]
        v0, v1, v2 = t[1] - t[0], t[2] - t[0], p - t[0]
        d = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(d) < 1e-12:
            continue
        b1 = (v2[:, 0] * v1[1] - v2[:, 1] * v1[0]) / d
        b2 = (v0[0] * v2[:, 1] - v0[1] * v2[:, 0]) / d
        cnt[np.array(cand)[(b1 >= -1e-9) & (b2 >= -1e-9) & (b1 + b2 <= 1 + 1e-9)]] += 1
    return cnt


def _expect(lines, step=0.25, margin=0.15):
    """Rasterpunkte, die sicher auf einer Fahrbahn liegen: lines = [(Polylinie k×2, halbe Breite, Enden frei?)]."""
    out = []
    for P, hw, *_ in lines:
        for a, b in zip(P[:-1], P[1:]):
            d = b - a
            L = np.hypot(*d)
            t = d / L
            n = np.array([-t[1], t[0]])
            s, q = np.meshgrid(np.arange(margin, L - margin, step), np.arange(-(hw - margin), hw - margin + 1e-9, step))
            out.append(a + s.ravel()[:, None] * t + q.ravel()[:, None] * n)
    return np.vstack(out)


def _check(net, lines, overlap_ok=False, label=""):
    pos, nrm, idx, col, uv, uv2 = net["mesh"]
    assert np.isfinite(pos).all() and np.isfinite(nrm).all() and idx.max() < len(pos), label
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    assert area.min() > 1e-6, (label, "entartetes Dreieck", area.min())
    ev = _expect(lines)
    cnt = coverage(pos, idx, ev)
    assert (cnt > 0).all(), (label, "Loch", ev[cnt == 0][:5], (cnt == 0).sum())
    if not overlap_ok:
        assert (cnt <= 1).mean() > 0.995, (label, "Überlappung", (cnt > 1).mean())
    # keine Spitzen: jeder Punkt nah an einer Mittellinie (halbe Breite + Eckradius + Rundung)
    P = np.vstack([np.column_stack([np.linspace(p[k, 0], p[k + 1, 0], 50), np.linspace(p[k, 1], p[k + 1, 1], 50)])
                   for p, *_ in lines for k in range(len(p) - 1)])
    d = cKDTree(P).query(pos[:, [0, 2]])[0]
    assert d.max() < max(hw for _, hw, *_ in lines) + 6, (label, "Spitze", d.max())
    # keine Stufen: gleiche Stelle -> gleiche Höhe, keine steilen Dreieckskanten auf ebenem Grund
    key = np.unique(np.round(pos[:, [0, 2]] * 1000).astype(np.int64), axis=0, return_inverse=True)[1].ravel()
    lo, hi = np.full(key.max() + 1, np.inf), np.full(key.max() + 1, -np.inf)
    np.minimum.at(lo, key, pos[:, 1])
    np.maximum.at(hi, key, pos[:, 1])
    assert (hi - lo).max() < 1e-6, (label, "Stufe", (hi - lo).max())
    return cnt


def test():
    flat = lambda x, z: np.zeros_like(np.asarray(x, float))
    W = lambda ids, pts, **t: (ids, np.array(pts, float)[:, 0], np.array(pts, float)[:, 1], {"highway": "residential", **t})
    # 1 Gerade aus zwei Wegen: eine Kette, keine Kreuzung, kein Loch, keine Überlappung
    ways = [W([1, 2], [(0, 0), (50, 0)]), W([2, 3], [(50, 0), (100, 0)])]
    assert len(chains(ways)) == 1
    net = build(ways, flat)
    assert net["stats"]["junctions"] == 0
    _check(net, [(np.array([[0, 0], [100, 0]]), 2.8)], label="gerade")
    # 2–4 T-, X-, Fünffach-Kreuzung, 5 spitzer Winkel (20°): eine Fläche, alles abgedeckt, keine Überlappung, keine Spitzen
    for label, dirs in (("T", [0, 90, 180]), ("X", [0, 90, 180, 270]), ("5", [0, 72, 144, 216, 288]), ("spitz", [0, 20, 180])):
        ws = [W([0, k + 1], [(0, 0), (60 * np.cos(np.radians(a)), 60 * np.sin(np.radians(a)))]) for k, a in enumerate(dirs)]
        net = build(ws, flat)
        assert net["stats"]["junctions"] == 1 and net["stats"]["fallback"] == 0, (label, net["stats"])
        _check(net, [(np.array([w[1], w[2]]).T, 2.8) for w in ws], label=label)
    # Kurve über einen Knoten mit Grad 2 (verschiedene Straßen): Außenseite rund, Innenseite ohne Falte
    ws = [W([1, 2], [(-60, 0), (0, 0)]), W([2, 3], [(0, 0), (40, 40)], highway="tertiary")]
    net = build(ws, flat)
    assert net["stats"]["fallback"] == 0
    _check(net, [(np.array([[-60, 0], [0, 0]]), 2.8), (np.array([[0, 0], [40, 40]]), 3.2)], label="Knick")
    # 14 Breitenwechsel gerade durch (primary -> residential): Keil statt Stufe
    ws = [W([1, 2], [(-60, 0), (0, 0)], highway="primary"), W([2, 3], [(0, 0), (60, 0)])]
    net = build(ws, flat)
    _check(net, [(np.array([[-60, 0], [0, 0]]), 2.8), (np.array([[0, 0], [60, 0]]), 2.8)], label="Breite")
    # 7/8 Autobahn mit Aus- und Einfahrt (6°, dann Bogen weg): Rampe schmaler als die Fahrbahn, Zwickel gefüllt
    mw = dict(highway="motorway", oneway="yes", lanes="2")
    th = np.radians(-6)  # Rampe verlässt die Autobahn mit 6° und biegt mit 400 m Radius weiter ab
    phi = th + np.pi / 2 - np.radians(np.linspace(0, 40, 25))
    ramp = 400 * np.column_stack([np.cos(th - np.pi / 2) + np.cos(phi), np.sin(th - np.pi / 2) + np.sin(phi)])
    ws = [W([1, 2], [(-300, 0), (0, 0)], **mw), W([2, 3], [(0, 0), (300, 0)], **mw),
          W([2] + list(range(10, 10 + len(ramp) - 1)), ramp, highway="motorway_link", oneway="yes")]
    assert road.street_hw(ws[2][3]) < road.street_hw(mw) and road.street_hw({"highway": "motorway_link"}) == 3.5
    net = build(ws, flat)
    assert net["stats"]["junctions"] == 1 and net["stats"]["fallback"] == 0, net["stats"]
    _check(net, [(np.array([[-300, 0], [300, 0]]), road.street_hw(mw)), (ramp, road.street_hw(ws[2][3]))], label="Ausfahrt")
    # 9 Kreisverkehr: Ring (15 m) mit vier Zufahrten, Ring über Grad-2-Knoten hinweg eine Kette je Abschnitt
    ring = [(15 * np.cos(t), 15 * np.sin(t)) for t in np.radians(np.arange(0, 360, 7.5))]
    rid = list(range(100, 148))
    ws = [W(rid + [rid[0]], ring + [ring[0]], junction="roundabout", oneway="yes")]
    for k, a in enumerate((0, 90, 180, 270)):
        i = a * 48 // 360
        ws.append(W([rid[i], 200 + k], [ring[i], (60 * np.cos(np.radians(a)), 60 * np.sin(np.radians(a)))]))
    net = build(ws, flat)
    assert net["stats"]["junctions"] == 4 and net["stats"]["fallback"] == 0, net["stats"]
    _check(net, [(np.array(ring + [ring[0]]), 2.8)] + [(np.array([w[1], w[2]]).T, 2.8) for w in ws[1:]], overlap_ok=True,
           label="Kreisel")
    # 10 Brücke über eine Straße ohne gemeinsamen Knoten: keine Kreuzung, lichte Höhe, Rampen in den Anschlussstraßen ≤ RAMP
    ws = [W([1, 2], [(-400, 0), (-60, 0)], highway="secondary"), W([2, 3], [(-60, 0), (60, 0)], highway="secondary", bridge="yes"),
          W([3, 4], [(60, 0), (400, 0)], highway="secondary"), W([5, 6], [(0, -200), (0, 200)])]
    net = build(ws, flat)
    st = net["stats"]
    assert st["junctions"] == 2 and st["crossings"] == 1 and st["clearance_min"] >= road.CLEARANCE - 0.01, st
    pos = net["mesh"][0][net["kind"] != 1]  # Fahrbahn, ohne Böschungen
    sec = pos[(np.abs(pos[:, 2]) < 4) & (np.abs(pos[:, 0]) > 5)]
    sec = sec[np.argsort(sec[:, 0])]
    g = np.abs(np.diff(sec[:, 1]) / np.maximum(np.diff(sec[:, 0]), 1e-3))[np.diff(sec[:, 0]) > 1]
    assert g.max() < RAMP + 0.01 and sec[:, 1].max() > 5.5, (g.max(), sec[:, 1].max())
    assert net["structures"]
    low = pos[(np.abs(pos[:, 0]) < 2.9) & (np.abs(pos[:, 2]) > 6)]  # die untere Straße bleibt unten
    assert np.abs(low[:, 1] - ROADS["residential"][3]).max() < 0.03
    # 11 Tunnel unter einer Straße: nicht im Netz, keine Kreuzung, die obere Straße läuft durch
    els = [{"type": "way", "nodes": [1, 2], "geometry": [{"lon": 0, "lat": -1}, {"lon": 0, "lat": 1}], "tags": {"highway": "primary", "tunnel": "yes"}},
           {"type": "way", "nodes": [3, 4], "geometry": [{"lon": -1, "lat": 0}, {"lon": 1, "lat": 0}], "tags": {"highway": "residential"}}]
    proj = type("P", (), {"to_xz": staticmethod(lambda lon, lat: (lon * 100.0, lat * 100.0))})
    ws = ways_from_osm(els, proj)
    assert len(ws) == 1 and build(ws, flat)["stats"]["junctions"] == 0
    # 12/15 Datenrand: fehlende Punkte (null) teilen den Weg, ohne die Lücke zu überbrücken; offenes Ende bleibt gerade
    els = [{"type": "way", "nodes": [1, 2, 3, 4], "tags": {"highway": "residential"},
            "geometry": [{"lon": 0, "lat": 0}, {"lon": 0.5, "lat": 0}, None, {"lon": 2, "lat": 0}]}]
    ws = ways_from_osm(els, proj)
    assert len(ws) == 1 and ws[0][0] == [1, 2]
    # nah, aber nicht verbunden: zwei parallele Straßen 4 m auseinander -> zwei Bänder, keine Kreuzungsfläche
    net = build([W([1, 2], [(0, 0), (100, 0)]), W([3, 4], [(0, 4), (100, 4)])], flat)
    assert net["stats"]["junctions"] == 0 and net["stats"]["chains"] == 2
    # 13 unebenes Gelände: überall knapp über dem Boden, nirgends darunter, keine Stufen
    hills = lambda x, z: 6 * np.sin(np.asarray(x) / 23.0) + 4 * np.cos(np.asarray(z) / 17.0)
    ws = [W([0, k + 1], [(0, 0), (80 * np.cos(np.radians(a)), 80 * np.sin(np.radians(a)))]) for k, a in enumerate((0, 100, 210))]
    net = build(ws, hills)
    pos, _, idx = net["mesh"][:3]
    ev = _expect([(np.array([w[1], w[2]]).T, 2.8) for w in ws])
    _check(net, [(np.array([w[1], w[2]]).T, 2.8) for w in ws], label="Hügel")
    T = pos[idx]
    cen = T.mean(1)
    gap = cen[:, 1] - hills(cen[:, 0], cen[:, 2])
    assert gap.min() > 0 and np.median(gap) < 0.2, (gap.min(), np.median(gap))
    del ev
    # 16 Einmündung in unsere Strecke (Knoten auf unserer Fahrbahn): Mund bis an unsere Kante, Ecken gerundet, kein Spalt
    import signs
    rx = np.arange(-200, 201, road.STEP)
    R = signs.Route(rx, np.zeros_like(rx), np.zeros_like(rx), np.full(len(rx), 3.0), False)
    ws = [W([1, 2], [(-200, 0.5), (0, 0.5)], highway="secondary"), W([2, 3], [(0, 0.5), (200, 0.5)], highway="secondary"),
          W([2, 4], [(0, 0.5), (0, 80)]), W([5, 6], [(60, -80), (60, 80)], highway="footway")]
    net = build(ws, flat, R, np.zeros(len(rx), int))
    st = net["stats"]
    assert st["mouths"] == 3 and st["junctions"] == 0, st  # Seitenstraße + Fußweg (beide Seiten); unsere Straße selbst nicht
    pos, _, idx = net["mesh"][:3]
    assert np.abs(pos[:, 2]).min() > 2.99  # nichts auf unserer Fahrbahn
    zone = np.column_stack([np.repeat(np.arange(-2.4, 2.41, 0.2), 10), np.tile(np.linspace(3.05, 6, 10), 25)])
    assert (coverage(pos, idx, zone) > 0).all()  # vom Fahrbahnrand bis zur Seitenstraße lückenlos
    corner = np.array([[-4.5, 3.1], [4.5, 3.1]])  # Eckausrundung neben der 5,6 m breiten Einmündung
    assert (coverage(pos, idx, corner) > 0).all()
    at_edge = pos[np.abs(np.abs(pos[:, 2]) - 3.0) < 0.005, 1]
    assert np.allclose(at_edge, EDGE_Y, atol=1e-6)  # bündig mit unserer Kante
    # Runde: Seitenstraße genau an der Start-/Ziel-Naht -> normale Einmündung, nicht die ganze Runde entlang
    t = np.arange(0, 2 * np.pi, road.STEP / 48.0)
    R = signs.Route(48 * np.cos(t), 48 * np.sin(t), np.zeros_like(t), np.full(len(t), 3.0), True)
    net = build([W([1, 2], [(48, 0), (120, 0)])], flat, R, np.zeros(len(t), int))
    pos = net["mesh"][0]
    assert net["stats"]["mouths"] == 1 and np.ptp(pos[:, 2]) < 25 and pos[:, 0].min() > 40, (net["stats"], np.ptp(pos[:, 2]))
    # Kehre: unsere Linie 5,5 m neben der echten -> dieselbe Straße nicht doppelt zeichnen, eine fremde daneben schon
    rx = np.arange(-200, 201, road.STEP)
    R = signs.Route(rx, np.zeros_like(rx), np.zeros_like(rx), np.full(len(rx), 3.0), False)
    ws = [W([1, 2], [(-100, 5.5), (100, 5.5)], highway="secondary", name="A"), W([3, 4], [(-100, -5.5), (100, -5.5)], highway="secondary", name="B")]
    pos = build(ws, flat, R, np.zeros(len(rx), int), rdev=np.full(len(rx), 6.0), rkey=[("secondary", "A", None)] * len(rx))["mesh"][0]
    assert pos[:, 2].max() < 0 and pos[:, 2].min() < -7, (pos[:, 2].min(), pos[:, 2].max())
    print("network ok")
