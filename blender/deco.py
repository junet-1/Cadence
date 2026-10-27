"""Deko rund um Häuser (Zäune, Mauern, Hecken, Autos, Briefkasten, Haltestelle …) und Tiere (Reh in Teilen, Hase) für Kurbel.

  blender -b -P blender/deco.py -- models/deco.glb models/animals.glb [vorschau.png]

Konvention Deko: lange Seite entlang X (Zaunfeld 2 m von x = −1 bis 1, Auto 4,2 m), Boden bei Z = 0, Mitte im Ursprung.
world.py dreht X entlang der Straße bzw. quer dazu. Tiere: Blick nach +Y (Godot −Z), Ursprung am Boden; Reh in Teilen, damit
animals.gd Beine und Kopf bewegen kann: reh_bein hängt vom Ursprung (Hüfte) nach unten, reh_kopf beginnt am Halsansatz.
Farben als Vertexfarben (linear) -> props.gdshader; Flächen flach (Low-Poly wie Häuser).
"""
import math
import random
import sys

import bpy
from mathutils import Vector

R = random.Random(7)


def lin(c):
    return Vector([x ** 2.2 for x in c])


class M:
    def __init__(self):
        self.v, self.f, self.c = [], [], []

    def _poly(self, pts, col):
        o = len(self.v)
        self.v += [Vector(p) for p in pts]
        self.c += [col] * len(pts)
        self.f.append(tuple(range(o, o + len(pts))))

    def box(self, c, s, col, yaw=0.0):
        cx, cy, cz = c
        hx, hy, hz = s[0] / 2, s[1] / 2, s[2] / 2
        cs, sn = math.cos(yaw), math.sin(yaw)
        p = lambda x, y, z: (cx + x * cs - y * sn, cy + x * sn + y * cs, cz + z)
        corners = {(i, j, k): p(i * hx, j * hy, k * hz) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)}
        for face in (((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)), ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
                     ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)), ((-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)),
                     ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)), ((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1))):
            self._poly([corners[q] for q in face], col)

    def prism(self, prof, y0, y1, col):
        """Profil [(x, z)] (konvex, gegen den Uhrzeigersinn von +Y gesehen) von y0 bis y1 extrudiert."""
        a = [(x, y0, z) for x, z in prof]
        b = [(x, y1, z) for x, z in prof]
        self._poly(a, col)
        self._poly(b[::-1], col)
        n = len(prof)
        for i in range(n):
            j = (i + 1) % n
            self._poly([a[i], b[i], b[j], a[j]], col)

    def cyl(self, c, r, h, col, axis="z", sides=8, r2=None):
        r2 = r if r2 is None else r2
        ring = lambda rr, t: [self._ax(c, axis, rr * math.cos(2 * math.pi * k / sides), rr * math.sin(2 * math.pi * k / sides), t) for k in range(sides)]
        lo, hi = ring(r, 0.0), ring(r2, h)
        self._poly(lo[::-1], col)
        self._poly(hi, col)
        for k in range(sides):
            j = (k + 1) % sides
            self._poly([lo[k], lo[j], hi[j], hi[k]], col)

    @staticmethod
    def _ax(c, axis, u, v, t):
        x, y, z = c
        return {"z": (x + u, y + v, z + t), "y": (x + u, y + t, z + v), "x": (x + t, y + u, z + v)}[axis]

    def tube(self, a, b, r0, r1, col, sides=6):
        a, b = Vector(a), Vector(b)
        d = (b - a).normalized()
        u = d.orthogonal().normalized()
        w = d.cross(u)
        ring = lambda p, r: [p + (u * math.cos(2 * math.pi * k / sides) + w * math.sin(2 * math.pi * k / sides)) * r for k in range(sides)]
        lo, hi = ring(a, r0), ring(b, r1)
        self._poly(lo[::-1], col)
        self._poly(hi, col)
        for k in range(sides):
            j = (k + 1) % sides
            self._poly([lo[k], lo[j], hi[j], hi[k]], col)

    def blob(self, c, radii, col, jag=0.2, var=0.1):
        """Verbeulter Oktaeder-Ikosaeder (Laub, Tierkörper)."""
        t = (1 + 5 ** 0.5) / 2
        vs = [Vector(p).normalized() for p in ((-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
                                                (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1))]
        fs = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
              (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        pts = [Vector(c) + Vector((p.x * radii[0], p.y * radii[1], p.z * radii[2])) * (1 + jag * (R.random() - 0.5)) for p in vs]
        for f in fs:
            self._poly([pts[i] for i in f], col * R.uniform(1 - var, 1 + var))


# --- Deko ---

def zaun(m):  # weißer Lattenzaun (Mitteleuropa)
    white = lin((.92, .92, .9))
    for z in (0.32, 0.72):
        m.box((0, 0, z), (2.0, 0.03, 0.08), white)
    for k in range(9):
        x = -0.9 + k * 0.225
        m.box((x, 0.03, 0.47), (0.08, 0.022, 0.94), white)
        m.prism([(x - 0.04, 0.94), (x + 0.04, 0.94), (x, 1.02)], 0.019, 0.041, white)


def zaun_holz(m):  # Alpen: Pfosten + zwei Rundholz-Riegel, schräg
    wood = lin((.42, .30, .19))
    m.cyl((-0.95, 0, 0), 0.06, 1.15, wood * 0.8)
    m.tube((-1.0, 0, 0.45), (1.0, 0, 0.5), 0.045, 0.045, wood)
    m.tube((-1.0, 0, 0.85), (1.0, 0, 0.92), 0.045, 0.045, wood * 1.1)


def mauer(m, col):  # Gartenmauer mit Abdeckung
    m.box((0, 0, 0.45), (2.02, 0.32, 0.9), col)
    m.box((0, 0, 0.93), (2.02, 0.38, 0.06), col * 0.8)


def hecke(m):
    g = lin((.12, .26, .10))
    for x in (-0.7, 0.0, 0.7):
        m.blob((x, 0, 0.7), (0.62, 0.42, 0.7), g * R.uniform(0.85, 1.1), jag=0.25)


def briefkasten(m):
    m.box((0, 0, 0.55), (0.06, 0.06, 1.1), lin((.25, .25, .27)))
    m.box((0, 0, 1.18), (0.36, 0.28, 0.3), lin((.18, .30, .22)))


def holzstapel(m):  # Alpen: Brennholz an der Hauswand
    wood = lin((.55, .40, .25))
    for row in range(5):
        for k in range(10):
            m.cyl((-0.9 + k * 0.2 + (row % 2) * 0.1, -0.25, 0.1 + row * 0.19), 0.095, 0.5, wood * R.uniform(0.8, 1.15), axis="y", sides=6)
    m.box((0, 0, 1.08), (2.2, 0.7, 0.05), lin((.35, .25, .18)))


def topf(m):  # Mittelmeer: Terrakottatopf mit Pflanze
    m.cyl((0, 0, 0), 0.22, 0.45, lin((.70, .38, .22)), r2=0.3, sides=10)
    m.blob((0, 0, 0.75), (0.38, 0.38, 0.4), lin((.20, .40, .14)), jag=0.4)


def bank(m):
    wood = lin((.55, .38, .22))
    for z, y in ((0.45, 0.0), (0.75, -0.22)):
        m.box((0, y, z), (1.8, 0.4 if z < 0.6 else 0.06, 0.05 if z < 0.6 else 0.35), wood)
    for x in (-0.75, 0.75):
        m.box((x, 0, 0.22), (0.06, 0.4, 0.45), lin((.2, .2, .22)))


def haltestelle(m):
    steel, glass = lin((.45, .47, .5)), lin((.55, .65, .7))
    m.cyl((1.7, 0.5, 0), 0.04, 2.5, steel)
    m.cyl((1.7, 0.44, 2.35), 0.3, 0.03, lin((.95, .80, .05)), axis="y", sides=16)  # gelbes Schild …
    m.cyl((1.7, 0.43, 2.35), 0.17, 0.035, lin((.10, .50, .25)), axis="y", sides=16)  # … mit grünem H-Kreis
    m.box((0, -0.9, 1.15), (3.0, 0.04, 2.0), glass)  # Rückwand Glas
    m.box((-1.5, -0.45, 1.15), (0.04, 0.9, 2.0), glass)
    m.box((0, -0.45, 2.2), (3.2, 1.1, 0.08), steel)
    for x in (-1.45, 1.45):
        m.box((x, -0.9, 1.1), (0.06, 0.06, 2.2), steel)
    m.box((0, -0.7, 0.45), (2.4, 0.35, 0.05), lin((.55, .38, .22)))


def auto(m, body, kombi=False):
    glass, black = lin((.10, .13, .16)), lin((.05, .05, .05))
    m.prism([(-2.1, 0.3), (2.1, 0.3), (2.12, 0.72), (1.65, 0.95), (-1.95, 1.0), (-2.12, 0.78)], -0.88, 0.88, body)
    rear = -1.95 if kombi else -1.55
    m.prism([(1.25, 0.95), (rear, 0.98), (rear + 0.1, 1.45), (0.35, 1.45)], -0.78, 0.78, glass)
    m.box(((rear + 0.1 + 0.35) / 2, 0, 1.47), (0.35 - rear - 0.1, 1.5, 0.05), body)
    for x in (-1.35, 1.35):
        for y in (-0.82, 0.82):
            m.cyl((x, y, 0.32), 0.32, 0.22 if y > 0 else -0.22, black, axis="y", sides=10)
    for y in (-0.6, 0.6):
        m.box((2.11, y, 0.68), (0.03, 0.3, 0.1), lin((.95, .95, .85)))
        m.box((-2.12, y, 0.75), (0.03, 0.3, 0.1), lin((.75, .08, .06)))


CAR_COLORS = [(.82, .82, .84), (.10, .10, .11), (.55, .07, .06), (.12, .22, .45), (.35, .37, .40), (.85, .85, .80)]

# --- Felsen (Größe ~2 m bei Skalierung 1; world.py skaliert 0,4–12 und versenkt sie im Hang) ---

def _ico(sub):
    t = (1 + 5 ** 0.5) / 2
    v = [Vector(p).normalized() for p in ((-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
                                          (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1))]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
         (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(sub):
        mid, nf = {}, []
        for a, b, c in f:
            m = []
            for p, q in ((a, b), (b, c), (c, a)):
                k = (min(p, q), max(p, q))
                if k not in mid:
                    mid[k] = len(v)
                    v.append(((v[p] + v[q]) / 2).normalized())
                m.append(mid[k])
            nf += [(a, m[0], m[2]), (b, m[1], m[0]), (c, m[2], m[1]), (m[0], m[1], m[2])]
        f = nf
    return v, f


def rock(m, seed, detail, base, radii=(1.0, 0.8, 0.65), jag=0.35, cuts=3, moss=None, bands=None, z0=-0.25):
    """Fels: Ikosaeder, Ecken radial verbeult, ein paar ebene Bruchflächen (cuts) -> kantige Facetten; Unterseite flach.
    Farbe je Fläche: Grundfarbe ± Streuung, unten dunkler, oben optional Moos; bands(z) = Schichtfarbe (Sandstein)."""
    r = random.Random(seed)
    v, f = _ico(1 if detail else 0)
    pts = []
    planes = [(Vector((r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(-0.3, 1))).normalized(), r.uniform(0.55, 0.8)) for _ in range(cuts)]
    for p in v:
        q = p * (1 + jag * (r.random() - 0.5) * (1.0 if detail else 0.6))
        for n, d in planes:  # Bruchfläche: alles jenseits der Ebene auf die Ebene drücken
            over = q.dot(n) - d
            if over > 0:
                q = q - n * over
        q = Vector((q.x * radii[0], q.y * radii[1], max(q.z, z0) * radii[2]))
        pts.append(q)
    for a, b, c in f:
        A, B, C = pts[a], pts[b], pts[c]
        n = (B - A).cross(C - A)
        n = n.normalized() if n.length > 1e-9 else Vector((0, 0, 1))
        zc = (A.z + B.z + C.z) / 3
        col = (bands(zc) if bands else base) * r.uniform(0.82, 1.12) * (0.7 + 0.3 * min(max((zc + 0.2) / 0.8, 0), 1))
        if moss is not None and n.z > 0.55:
            col = col.lerp(moss, min((n.z - 0.55) * 3.0, 0.9))
        m._poly([A, B, C], col)


# dunkler als man denkt: unter Sonne 1,6 + Himmelslicht wirkt sRGB 0,46 schon fast weiß
GRANIT, KALK, SAND, MOOS = lin((.34, .33, .32)), lin((.66, .62, .55)), lin((.62, .34, .22)), lin((.20, .30, .10))


def sand_bands(z):
    k = int((z + 1) * 4.0) % 4
    return [lin((.72, .40, .25)), lin((.80, .55, .36)), lin((.62, .33, .22)), lin((.86, .68, .50))][k]


def fels(kind, i, detail):
    s = 100 * i + {"granit": 1, "kalk": 2, "sand": 3, "moos": 4}[kind]

    def build(m):
        if kind == "granit":  # kantig, hoch: Felsnadel/Blockwerk
            rock(m, s, detail, GRANIT, radii=(1.0, 0.75, [1.1, 0.7, 1.5][i]), jag=0.45, cuts=4)
        elif kind == "kalk":  # gerundet, hell, breit
            rock(m, s, detail, KALK, radii=(1.1, 0.9, [0.7, 0.55, 0.9][i]), jag=0.2, cuts=2)
        elif kind == "sand":  # gestapelte Schichten, nach oben schmaler, flache Oberseite (Tafelfels/Felsturm)
            n = [3, 2, 4][i]
            for k in range(n):
                w = 1.1 - 0.1 * k
                col = [lin((.64, .34, .21)), lin((.74, .48, .31)), lin((.55, .29, .19)), lin((.78, .60, .43))][(k + i) % 4]
                rock(m, s + k, detail, col, radii=(w, w * 0.85, 0.5), jag=0.12, cuts=4, z0=-0.6)
                for p in range(len(m.v) - (80 if detail else 20) * 3, len(m.v)):  # Schicht k auf Höhe k·0,45 heben
                    m.v[p] = m.v[p] + Vector((0.06 * (k % 2), 0, 0.42 * k + 0.3))
        else:  # bemooster Findling
            rock(m, s, detail, GRANIT * 0.9, radii=(1.0, 0.85, [0.6, 0.45, 0.75][i]), jag=0.25, cuts=2, moss=MOOS)
    return build


ROCKS = {}
for _k in ("granit", "kalk", "sand", "moos"):
    for _i in range(3):
        ROCKS[f"fels_{_k}_{_i}"] = fels(_k, _i, True)
        ROCKS[f"fels_{_k}_{_i}_lod"] = fels(_k, _i, False)


def wand(kind, i, detail):
    """Felswand (world._cliffs setzt sie quer in steile Felshänge, 15–60 m hoch, überlappend -> Felsband): Breite X −1…1,
    Höhe Z 0…~1 (zackiger Grat, zu den Enden niedriger), Vorderseite −Y (Godot +Z, schaut hangabwärts) mit senkrechten
    Rippen und Bändern/Gesimsen, Rückseite keilförmig (steckt im Hang). Grundriss linsenförmig: Enden laufen spitz aus.
    Flächenfarbe: Rinnen dunkler, Gesimse heller, Granit mit Grasflecken auf Absätzen, Sandstein gebändert."""
    r = random.Random(1000 + 10 * i + {"granit": 1, "kalk": 2, "sand": 3}[kind])
    nx, nz = (14, 10) if detail else (6, 4)
    base = {"granit": GRANIT, "kalk": KALK, "sand": SAND}[kind]
    ribs = [r.uniform(0.0, 0.16) for _ in range(nx + 1)]
    ledges = sorted(r.uniform(0.15, 0.85) for _ in range(3))
    crest = [0.55 + 0.45 * r.random() ** 0.7 for _ in range(nx + 1)]  # zackig: Türme und Scharten
    crest = [crest[0]] + [0.25 * crest[a - 1] + 0.5 * crest[a] + 0.25 * crest[a + 1] for a in range(1, nx)] + [crest[-1]]
    if detail:  # ein, zwei Felstürme
        for a in r.sample(range(2, nx - 1), 2):
            crest[a] += r.uniform(0.15, 0.3)

    def build(m):
        F, B = [], []
        for a in range(nx + 1):
            x = -1 + 2 * a / nx
            e = (1 - abs(x) ** 2.2) ** 0.5  # Linse: Dicke -> 0 an den Enden
            top = crest[a] * (1 - 0.6 * abs(x) ** 2.5)
            fr, bk = [], []
            for k in range(nz + 1):
                z = -0.15 + (top + 0.15) * k / nz
                step = sum(0.05 for L in ledges if z > L)  # über jedem Gesims tritt die Wand etwas zurück
                d = (0.14 + ribs[a] + 0.05 * math.sin(7 * z + a) + r.uniform(-0.03, 0.03) - step) * e
                lean = 0.42 * max(z, 0)  # Wand neigt sich nach hinten (~75°) und läuft oben in den Hang
                fy = -max(d, 0.03 * e) + lean
                fr.append(Vector((x + r.uniform(-0.02, 0.02) * e, fy, z)))
                bk.append(Vector((x, fy + (0.6 - 0.5 * min(max(z, 0), 1)) * e + 0.02, z)))
            F.append(fr)
            B.append(bk)

        def col(pts):
            A, Bv, C = pts[0], pts[1], pts[2]
            n = (Bv - A).cross(C - A)
            n = n.normalized() if n.length > 1e-9 else Vector((0, 0, 1))
            zc = sum(p.z for p in pts) / len(pts)
            c = (sand_bands(zc * 3) if kind == "sand" else base) * r.uniform(0.85, 1.1)
            depth = -sum(p.y for p in pts) / len(pts)
            c = c * (0.75 + 1.4 * min(depth, 0.25))  # Rinnen (wenig Tiefe) dunkler, Rippen heller
            c = c * (0.75 + 0.25 * min(max(zc + 0.15, 0) / 0.6, 1))  # Fuß dunkler
            if abs(n.z) > 0.6 and n.z > 0:  # Absatz: heller (Kalk/Sand) bzw. Grasfleck (Granit)
                c = c.lerp(lin((.30, .36, .18)) if kind == "granit" else c * 1.25, 0.55)
            return c

        def quad(a, b, c, d, flip=False):
            for t in ((a, b, c), (a, c, d)):
                t = t[::-1] if flip else t
                m._poly(list(t), col(t))

        for a in range(nx):
            for k in range(nz):
                quad(F[a][k], F[a + 1][k], F[a + 1][k + 1], F[a][k + 1])  # Front (Normale -Y)
                quad(B[a][k], B[a][k + 1], B[a + 1][k + 1], B[a + 1][k])  # Rückseite (+Y)
            quad(F[a][nz], F[a + 1][nz], B[a + 1][nz], B[a][nz])  # Grat
    return build


for _k in ("granit", "kalk", "sand"):
    for _i in range(2):
        ROCKS[f"fels_wand_{_k}_{_i}"] = wand(_k, _i, True)
        ROCKS[f"fels_wand_{_k}_{_i}_lod"] = wand(_k, _i, False)

# --- Tiere ---


def reh_rumpf(m):
    fur, belly, white = lin((.55, .36, .20)), lin((.70, .55, .40)), lin((.92, .90, .86))
    m.blob((0, 0, 0.78), (0.17, 0.46, 0.2), fur, jag=0.08, var=0.04)
    m.blob((0, 0.05, 0.68), (0.13, 0.35, 0.1), belly, jag=0.05, var=0.02)
    m.blob((0, -0.44, 0.8), (0.11, 0.06, 0.12), white, jag=0.05, var=0.02)  # Spiegel


def reh_kopf(m):
    fur = lin((.55, .36, .20))
    m.tube((0, 0, 0), (0, 0.16, 0.34), 0.08, 0.06, fur)  # Hals vom Ansatz nach vorn-oben
    m.blob((0, 0.24, 0.38), (0.07, 0.15, 0.08), fur, jag=0.05, var=0.03)
    m.blob((0, 0.38, 0.35), (0.035, 0.03, 0.03), lin((.08, .07, .07)), jag=0.0, var=0.0)  # Äser
    for x in (-0.06, 0.06):
        m.blob((x, 0.15, 0.48), (0.025, 0.02, 0.07), fur * 0.85, jag=0.05, var=0.02)


def reh_bein(m):
    m.tube((0, 0, 0), (0, 0, -0.34), 0.045, 0.03, lin((.50, .33, .19)))
    m.tube((0, 0, -0.34), (0, -0.02, -0.62), 0.025, 0.02, lin((.40, .27, .16)))


def hase(m):
    fur = lin((.48, .40, .30))
    m.blob((0, -0.03, 0.14), (0.1, 0.17, 0.11), fur, jag=0.08, var=0.03)
    m.blob((0, -0.1, 0.12), (0.11, 0.1, 0.1), fur * 0.95, jag=0.05, var=0.02)  # Keulen
    m.blob((0, 0.14, 0.2), (0.065, 0.08, 0.065), fur, jag=0.05, var=0.03)
    for x in (-0.025, 0.025):
        m.blob((x, 0.11, 0.31), (0.018, 0.014, 0.08), fur * 0.9, jag=0.0, var=0.02)
    m.blob((0, -0.2, 0.17), (0.035, 0.03, 0.035), lin((.95, .95, .93)), jag=0.0, var=0.0)  # Blume


DECO = dict(zaun=zaun, zaun_holz=zaun_holz, mauer=lambda m: mauer(m, lin((.93, .91, .86))), mauer_lehm=lambda m: mauer(m, lin((.72, .55, .38))),
            hecke=hecke, briefkasten=briefkasten, holzstapel=holzstapel, topf=topf, bank=bank, haltestelle=haltestelle,
            **{f"auto_{i}": (lambda m, c=c, i=i: auto(m, lin(c), kombi=i % 3 == 2)) for i, c in enumerate(CAR_COLORS)}, **ROCKS)
ANIMALS = dict(reh_rumpf=reh_rumpf, reh_kopf=reh_kopf, reh_bein=reh_bein, hase=hase)


def to_object(name, m):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in m.v], [], m.f)
    me.validate()
    col = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
    for i, c in enumerate(m.c):
        col.data[i].color = (min(c.x, 1), min(c.y, 1), min(c.z, 1), 0.0)
    me.color_attributes.active_color = col
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export(builders, path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = []
    for name, build in builders.items():
        m = M()
        build(m)
        objs.append(to_object(name, m))
        print(f"{name}: {len(m.f)} Flächen")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_yup=True, export_apply=True, export_vertex_color="ACTIVE",
                              export_materials="NONE")
    return objs


def preview(objs, path):
    for i, o in enumerate(objs):
        o.location = ((i % 8) * 5, (i // 8) * 6, 0)
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (17, -16, 9)
    cam.rotation_euler = (math.radians(62), 0, 0)
    cam.data.lens = 26
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.75, 0.82, 0.9)
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = 2000, 1100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


args = sys.argv[sys.argv.index("--") + 1:]
export(ANIMALS, args[1])
objs = export(DECO, args[0])  # zuletzt: Szene enthält die Deko für die Vorschau
if len(args) > 2:
    preview(objs, args[2])
