"""Bäume und Büsche für Kurbel: europäischer Mischwald und Mittelmeer (Mallorca).

  blender -b -P blender/trees.py -- models/trees.glb [vorschau.png]

Konvention: Stammfuß im Ursprung, Z oben (glTF/Godot: Y). Farben als Vertexfarben (linear) -> props.gdshader, kein Material.
Zu jeder Art gibt es "<name>_lod" für die Ferne (wenige Dutzend Dreiecke). Äste wachsen rekursiv (zufällig, aber fest je Art),
Laub = Klumpen aus verbeulten Ikosaedern an den Astspitzen; Laubnormalen zeigen vom Kronenmittelpunkt weg (weiches, volumiges
Licht statt Facetten), Farbe dunkler nach innen und unten (Umgebungsverdeckung als Vertexfarbe).
Höhen sind echte Größen bei Skalierung 1 (world.py/route.py skalieren je Baum 0,7–1,4).
"""
import math
import random
import sys

import bpy
from mathutils import Vector

UP = Vector((0, 0, 1))


def lin(c):
    """sRGB -> linear (die Shader nehmen COLOR direkt)."""
    return Vector([x ** 2.2 for x in c])


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


ICO = {s: _ico(s) for s in (0, 1, 2)}


class Tree:
    def __init__(self, seed, detail):
        self.r = random.Random(seed)
        self.detail = detail
        self.v, self.f, self.c, self.n = [], [], [], []
        self.tips = []  # (Position, Astrichtung, Astdicke) für das Laub

    def u(self, a, b):
        return self.r.uniform(a, b)

    # -- Holz --

    def tube(self, pts, radii, col, sides=None, gnarl=0.0, bands=None):
        """Röhre entlang pts (Radius je Punkt), Ringe per Paralleltransport ausgerichtet, Spitze geschlossen."""
        sides = sides or (6 if self.detail else 3)
        o = len(self.v)
        t0 = (pts[1] - pts[0]).normalized()
        a = t0.cross(Vector((1, 0, 0)) if abs(t0.x) < 0.9 else Vector((0, 1, 0))).normalized()
        for i, p in enumerate(pts):
            t = (pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]).normalized()
            a = (a - t * a.dot(t)).normalized()
            b = t.cross(a)
            shade = 1.0 if bands is None else bands(i)
            for k in range(sides):
                ang = 2 * math.pi * k / sides
                d = a * math.cos(ang) + b * math.sin(ang)
                rr = radii[i] * (1 + gnarl * (self.r.random() - 0.5))
                self.v.append(p + d * rr)
                self.n.append(d)
                self.c.append(col * shade * self.u(0.85, 1.1))
        for i in range(len(pts) - 1):
            for k in range(sides):
                k1 = (k + 1) % sides
                self.f.append((o + i * sides + k, o + i * sides + k1, o + (i + 1) * sides + k1, o + (i + 1) * sides + k))
        tip = len(self.v)
        self.v.append(pts[-1] + (pts[-1] - pts[-2]).normalized() * radii[-1])
        self.n.append((pts[-1] - pts[-2]).normalized())
        self.c.append(col)
        last = o + (len(pts) - 1) * sides
        for k in range(sides):
            self.f.append((last + k, last + (k + 1) % sides, tip))

    def limb(self, p, d, length, r0, depth, P):
        """Ast von p in Richtung d; biegt sich (P["bend"], Richtung nach oben P["up"]), verzweigt sich bis depth = 0.
        Spitzen landen in self.tips."""
        segs = max(2, int(length / P.get("seg", 1.6))) if self.detail else 2
        pts, rad = [p], [r0]
        q, dd = p.copy(), d.normalized()
        for i in range(segs):
            dd = (dd + Vector((self.u(-1, 1), self.u(-1, 1), self.u(-1, 1))) * P["bend"] + UP * P["up"]).normalized()
            q = q + dd * (length / segs)
            pts.append(q.copy())
            rad.append(r0 * (1 - (i + 1) / segs * P.get("taper", 0.75)))
        if self.detail or depth >= P["depth"] - 1:
            self.tube(pts, rad, P["bark"], gnarl=P.get("gnarl", 0.1), sides=(5 if r0 > 0.12 else 3) if self.detail else 3)
        if depth <= 0:
            self.tips.append((pts[-1], dd, r0))
            return
        n = P["kids"] if self.detail else max(1, P["kids"] - 1)
        for j in range(n):
            t = self.u(*P.get("at", (0.45, 1.0)))
            k = min(int(t * segs), segs - 1)
            base = pts[k].lerp(pts[k + 1], t * segs - k)
            ang = self.u(*P["spread"])
            side = Vector((self.u(-1, 1), self.u(-1, 1), 0))
            side = (side - dd * side.dot(dd)).normalized() if side.length > 1e-3 else dd.orthogonal().normalized()
            nd = (dd * math.cos(math.radians(ang)) + side * math.sin(math.radians(ang))).normalized()
            self.limb(base, nd, length * self.u(*P["ratio"]), r0 * self.u(0.45, 0.65), depth - 1, P)
        if P.get("end_tip", True):
            self.tips.append((pts[-1], dd, r0))

    # -- Laub --

    def blob(self, c, radii, col, center, crown, jag=0.25, sub=None, ao=(0.68, 1.2)):
        """Laubklumpen: verbeulter Ikosaeder; Normale halb vom Kronenmittelpunkt, halb lokal; Farbe nach Lage in der Krone."""
        sub = (1 if self.detail else 0) if sub is None else sub
        verts, faces = ICO[sub]
        o = len(self.v)
        for p in verts:
            q = c + Vector((p.x * radii[0], p.y * radii[1], p.z * radii[2])) * (1 + jag * (self.r.random() - 0.5))
            rel = q - center
            g = Vector((rel.x / crown[0], rel.y / crown[1], rel.z / crown[2]))
            nrm = (g.normalized() * 0.65 + p * 0.35).normalized()
            outer = min(g.length, 1.2) / 1.2  # außen hell, innen dunkel
            top = 0.5 + 0.5 * max(min(g.z, 1), -1)
            self.v.append(q)
            self.n.append(nrm)
            self.c.append(col * (ao[0] + (ao[1] - ao[0]) * (0.55 * outer + 0.45 * top)))
        self.f += [(o + a, o + b, o + cc) for a, b, cc in faces]

    def foliage(self, col, center, crown, size, var=0.12, jag=0.3, flat=1.0, keep=1.0, fill=0):
        """Klumpen an allen Astspitzen (Größe nach Astdicke), optional Füllklumpen im Kronenellipsoid gegen Durchblick."""
        for p, d, r0 in self.tips:
            if self.r.random() > keep:
                continue
            s = size * self.u(0.75, 1.2)
            cc = col * self.u(1 - var, 1 + var)
            if not self.detail:
                self.blob(p + d * s * 0.3, (s, s, s * flat), cc, center, crown, jag)
                continue
            for _ in range(4):  # nah: vier kleine, kantige Klumpen statt einer Kugel -> ausgefranste, belaubte Silhouette
                o = Vector((self.u(-1, 1), self.u(-1, 1), self.u(-0.6, 1))) * s * 0.55
                q = s * self.u(0.45, 0.65)
                self.blob(p + d * s * 0.3 + o, (q, q, q * flat), cc * self.u(0.9, 1.1), center, crown, jag + 0.1, sub=0)
        for _ in range(fill * 3 if self.detail else max(1, fill // 3)):  # nah: viele mittlere statt weniger großer
            th, ph, rr = self.u(0, 2 * math.pi), self.u(-0.6, 0.8), self.u(0.3, 0.8)
            p = center + Vector((math.cos(th) * math.cos(ph) * crown[0], math.sin(th) * math.cos(ph) * crown[1], math.sin(ph) * crown[2])) * rr
            s = size * self.u(0.7, 1.0) if self.detail else size * 2.2
            self.blob(p, (s, s, s * flat), col * self.u(0.8, 1.0), center, crown, jag, sub=0)

    def lathe(self, prof, col, sides, center, crown, noise=0.0):
        """Rotationskörper (Zypresse): prof = [(z, r)], Spitze oben."""
        o = len(self.v)
        for z, rr in prof:
            for k in range(sides):
                ang = 2 * math.pi * k / sides + z * 0.35
                q = Vector((math.cos(ang) * rr * (1 + noise * (self.r.random() - 0.5)), math.sin(ang) * rr * (1 + noise * (self.r.random() - 0.5)), z))
                rel = q - center
                g = Vector((rel.x / crown[0], rel.y / crown[1], rel.z / crown[2]))
                self.v.append(q)
                self.n.append(g.normalized())
                self.c.append(col * (0.55 + 0.5 * min(Vector((g.x, g.y)).length, 1.0)) * self.u(0.9, 1.1))
        for i in range(len(prof) - 1):
            for k in range(sides):
                k1 = (k + 1) % sides
                self.f.append((o + i * sides + k, o + i * sides + k1, o + (i + 1) * sides + k1, o + (i + 1) * sides + k))
        tip = len(self.v)
        self.v.append(Vector((0, 0, prof[-1][0] + 0.4)))
        self.n.append(UP.copy())
        self.c.append(col)
        last = o + (len(prof) - 1) * sides
        self.f += [(last + k, last + (k + 1) % sides, tip) for k in range(sides)]

    def frond(self, base, d, length, width, col, droop):
        """Palmwedel: Mittelrippe als Bogen, Fiedern als V nach unten, beidseitig."""
        n = 9 if self.detail else 3
        side = d.cross(UP).normalized()
        rows = []
        for i in range(n + 1):
            t = i / n
            p = base + d * (length * t) + UP * (length * (0.35 * t - droop * t * t))
            w = width * math.sin(math.pi * min(t * 1.15, 1) ** 0.8)
            rows.append((p - side * w + UP * (-0.25 * w), p + UP * 0.05, p + side * w + UP * (-0.25 * w)))
        for flip in (False, True):
            o = len(self.v)
            for i, row in enumerate(rows):
                for q in row:
                    self.v.append(q)
                    self.n.append(UP * (-1 if flip else 1))
                    self.c.append(col * (0.75 + 0.35 * i / n) * (0.8 if flip else 1.0))
            for i in range(n):
                a, b = o + i * 3, o + (i + 1) * 3
                quads = [(a, a + 1, b + 1, b), (a + 1, a + 2, b + 2, b + 1)]
                for q in quads:
                    self.f.append(q[::-1] if flip else q)


def trunk(t, h, r0, lean=0.0, curve=0.0, segs=6, col=None, gnarl=0.06, taper=0.35, bands=None):
    """Stamm (Höhe h) mit Neigung und Krümmung -> Punkte, Radien (für den Kronenansatz)."""
    segs = segs if t.detail else 2
    az = t.u(0, 2 * math.pi)
    lean_v = Vector((math.cos(az), math.sin(az), 0))
    pts, rad = [], []
    for i in range(segs + 1):
        s = i / segs
        off = lean_v * (math.tan(math.radians(lean)) * h * s + curve * math.sin(math.pi * s) * h)
        pts.append(Vector((0, 0, h * s)) + off)
        rad.append(r0 * (1 - taper * s) * (1.25 if i == 0 else 1.0))
    t.tube(pts, rad, col, gnarl=gnarl, bands=bands)
    return pts, rad


# --- Arten (Mischwald) ---

def buche(t):
    bark, leaf = lin((.46, .45, .42)), lin((.24, .40, .11))
    pts, rad = trunk(t, 6.5, 0.38, lean=2, col=bark)
    P = dict(bark=bark, bend=0.12, up=0.08, depth=2, kids=2, spread=(25, 45), ratio=(0.5, 0.7), gnarl=0.05, end_tip=False)
    for k in range(5 if t.detail else 3):
        a = 2 * math.pi * k / 5 + t.u(-.3, .3)
        t.limb(pts[-1], Vector((math.cos(a) * .55, math.sin(a) * .55, 1)), t.u(6, 8), 0.2, 2, P)
    t.foliage(leaf, Vector((0, 0, 13)), (6, 6, 6.5), 2.6, fill=10)


def eiche(t):
    bark, leaf = lin((.30, .26, .21)), lin((.18, .32, .09))
    pts, rad = trunk(t, 3.6, 0.55, lean=3, curve=0.03, col=bark, gnarl=0.15)
    P = dict(bark=bark, bend=0.3, up=0.02, depth=2, kids=2, spread=(30, 60), ratio=(0.5, 0.75), gnarl=0.18, seg=1.3, end_tip=False)
    for k in range(6 if t.detail else 3):
        a = 2 * math.pi * k / 6 + t.u(-.4, .4)
        t.limb(pts[-1] + UP * t.u(-.4, .6), Vector((math.cos(a), math.sin(a), t.u(.5, 1.1))), t.u(5.5, 7.5), 0.26, 2, P)
    t.foliage(leaf, Vector((0, 0, 9.5)), (7.5, 7.5, 5), 2.5, var=0.15, jag=0.4, keep=0.92, fill=6)


def birke(t):
    bark, leaf = lin((.86, .85, .80)), lin((.38, .52, .15))
    dark = lambda i: 0.25 if t.r.random() < 0.25 else 1.0  # schwarze Rindenflecken
    pts, rad = trunk(t, 14, 0.2, lean=4, curve=0.02, segs=10, col=bark, bands=dark, taper=0.7)
    P = dict(bark=lin((.35, .30, .27)), bend=0.15, up=-0.12, depth=1, kids=3, spread=(30, 70), ratio=(0.5, 0.8), seg=1.2,
             at=(0.25, 1.0))
    for k in range(9 if t.detail else 3):
        i = 4 + k * (len(pts) - 5) // 9 if t.detail else 1 + k % 2
        a = t.u(0, 2 * math.pi)
        t.limb(pts[min(i, len(pts) - 1)], Vector((math.cos(a), math.sin(a), 1.3)), t.u(2.5, 4.5), 0.06, 1, P)
    t.tips.append((pts[-1], UP, 0.05))
    t.foliage(leaf, Vector((0, 0, 10)), (3.5, 3.5, 5.5), 1.05, var=0.18, jag=0.4, flat=1.3, fill=8)


def fichte(t):
    bark, leaf = lin((.32, .24, .18)), lin((.08, .19, .10))
    H = 24
    trunk(t, H * 0.9, 0.32, col=bark, taper=0.8)
    tiers = 18 if t.detail else 4
    n = 12 if t.detail else 5
    for i in range(tiers):
        s = i / tiers
        z = 2.2 + s * (H - 3.2) + t.u(-.2, .2)
        R = 4.4 * (1 - s) ** 0.9 + 0.35
        o = len(t.v)
        t.v.append(Vector((0, 0, z + R * 0.35 + 0.4)))  # Ansatz am Stamm
        t.n.append(UP.copy())
        t.c.append(leaf * 0.7)
        for k in range(n):  # Zweigspitzen hängen nach außen herab, abwechselnd lang/kurz -> ausgefranster Rand
            a = 2 * math.pi * (k + 0.5 * (i % 2)) / n + t.u(-.15, .15)
            rr = R * (1.0 if k % 2 == 0 else 0.7) * t.u(0.85, 1.15)
            q = Vector((math.cos(a) * rr, math.sin(a) * rr, z - R * t.u(0.35, 0.6)))
            t.v.append(q)
            t.n.append((Vector((math.cos(a), math.sin(a), 0.0)) * 0.75 + UP * 0.5).normalized())
            t.c.append(leaf * (t.u(1.05, 1.4) if k % 2 == 0 else t.u(0.8, 1.0)))
        under = len(t.v)
        t.v.append(Vector((0, 0, z - R * 0.2)))
        t.n.append(-UP)
        t.c.append(leaf * 0.4)
        for k in range(n):
            a, b = o + 1 + k, o + 1 + (k + 1) % n
            t.f += [(o, a, b), (under, b, a)]
    t.tips.clear()


def kiefer(t):
    low, high, leaf = lin((.36, .30, .26)), lin((.64, .40, .23)), lin((.15, .28, .14))
    pts, rad = trunk(t, 15, 0.32, lean=4, curve=0.03, segs=8, col=low, bands=lambda i: 1.0 if i < 5 else 1.4, taper=0.6)
    P = dict(bark=high, bend=0.3, up=0.03, depth=1, kids=2, spread=(30, 60), ratio=(0.4, 0.6), gnarl=0.15, seg=1.2)
    for k in range(6 if t.detail else 3):
        i = len(pts) - 1 - (k % 3)
        a = 2 * math.pi * k / 6 + t.u(-.5, .5)
        t.limb(pts[max(i, 1)], Vector((math.cos(a), math.sin(a), t.u(.1, .5))), t.u(2.5, 4.0), 0.12, 1, P)
    t.foliage(leaf, pts[-1] + UP * 1.0, (4, 4, 2.5), 1.6, flat=0.55, jag=0.35, fill=3)


# --- Arten (Mittelmeer / Mallorca) ---

def olive(t):
    bark, leaf = lin((.32, .29, .25)), lin((.45, .50, .36))
    P = dict(bark=bark, bend=0.35, up=0.05, depth=2, kids=2, spread=(25, 55), ratio=(0.55, 0.75), gnarl=0.3, seg=0.8, taper=0.6,
             end_tip=False)
    for k in range(2 if t.detail else 1):  # zwei verdrehte Stämmlinge aus einem knorrigen Fuß
        a = t.u(0, 2 * math.pi)
        d = Vector((math.cos(a) * .45, math.sin(a) * .45, 1))
        pts, rad = [Vector((0, 0, 0))], [0.42]
        q = Vector((0, 0, 0))
        for i in range(5 if t.detail else 2):
            ang = a + i * 0.9
            q = q + Vector((math.cos(ang) * .25, math.sin(ang) * .25, 0.45)) + d * 0.12
            pts.append(q.copy())
            rad.append(0.36 - i * 0.03)
        t.tube(pts, rad, bark, gnarl=0.45)
        for j in range(3):
            b = a + j * 2.1 + t.u(-.4, .4)
            t.limb(pts[-1], Vector((math.cos(b), math.sin(b), t.u(.5, 1.0))), t.u(2.0, 2.8), 0.15, 2, P)
    t.foliage(leaf, Vector((0, 0, 3.6)), (3.3, 3.3, 2.0), 1.15, var=0.1, jag=0.45, flat=0.75, fill=6)


def pinie(t):
    bark, leaf = lin((.48, .30, .20)), lin((.14, .29, .12))
    pts, rad = trunk(t, 9.0, 0.36, lean=7, curve=0.04, segs=7, col=bark, gnarl=0.08, taper=0.45)
    P = dict(bark=bark, bend=0.12, up=0.05, depth=1, kids=3, spread=(25, 50), ratio=(0.5, 0.7), seg=1.2)
    top = pts[-1]
    for k in range(5 if t.detail else 3):
        a = 2 * math.pi * k / 5 + t.u(-.3, .3)
        t.limb(top, Vector((math.cos(a), math.sin(a), 0.9)), t.u(3.0, 4.2), 0.18, 1, P)
    c = top + UP * 3.0
    t.foliage(leaf, c, (6.5, 6.5, 1.8), 2.0, flat=0.45, jag=0.3, fill=0)
    for k in range(12 if t.detail else 3):  # geschlossener Schirm: Ring + Mitte
        a, rr = t.u(0, 2 * math.pi), t.u(0, 4.6)
        p = c + Vector((math.cos(a) * rr, math.sin(a) * rr, 0.6 - 0.05 * rr * rr))
        s = t.u(2.0, 2.8) if t.detail else 3.2
        t.blob(p, (s, s, s * 0.42), leaf * t.u(0.85, 1.05), c, (6.5, 6.5, 1.8), 0.3)


def aleppo(t):
    bark, leaf = lin((.43, .40, .36)), lin((.30, .40, .17))
    pts, rad = trunk(t, 7.0, 0.30, lean=13, curve=0.08, segs=7, col=bark, gnarl=0.12, taper=0.5)
    P = dict(bark=bark, bend=0.35, up=0.06, depth=2, kids=2, spread=(25, 55), ratio=(0.5, 0.7), gnarl=0.15, seg=1.2)
    for k in range(4 if t.detail else 2):
        i = len(pts) - 1 - k
        a = t.u(0, 2 * math.pi)
        t.limb(pts[max(i, 1)], Vector((math.cos(a), math.sin(a), t.u(.6, 1.2))), t.u(3.0, 4.5), 0.14, 2, P)
    t.foliage(leaf, pts[-1] + UP * 2.0, (4.5, 4.5, 3), 1.25, flat=0.7, jag=0.45, keep=0.85, fill=2)


def steineiche(t):
    bark, leaf = lin((.24, .22, .20)), lin((.13, .22, .09))
    pts, rad = trunk(t, 2.2, 0.38, lean=4, col=bark, gnarl=0.18)
    P = dict(bark=bark, bend=0.25, up=0.06, depth=2, kids=2, spread=(30, 55), ratio=(0.55, 0.75), gnarl=0.2, end_tip=False)
    for k in range(5 if t.detail else 2):
        a = 2 * math.pi * k / 5 + t.u(-.4, .4)
        t.limb(pts[-1], Vector((math.cos(a), math.sin(a), 1.1)), t.u(2.8, 3.8), 0.18, 2, P)
    t.foliage(leaf, Vector((0, 0, 5.6)), (4.2, 4.2, 3.6), 1.5, var=0.12, jag=0.35, fill=6)


def zypresse(t):
    leaf = lin((.07, .16, .07))
    trunk(t, 1.0, 0.18, col=lin((.30, .24, .18)))
    H = 13.0
    rings = 16 if t.detail else 5
    prof = [(0.6 + H * i / rings, 1.15 * math.sin(math.pi * min(0.15 + i / rings * 0.95, 1.0)) ** 0.7 * (1 - 0.55 * i / rings) + 0.15)
            for i in range(rings + 1)]
    t.lathe(prof, leaf, 9 if t.detail else 5, Vector((0, 0, 5)), (1.2, 1.2, 7), noise=0.25)


def palme(t):
    """Kanarische Dattelpalme (Mallorcas Promenaden und Fincas): dicker Stamm mit Blattnarben, Krone aus gebogenen Wedeln."""
    bark, leaf = lin((.46, .36, .25)), lin((.26, .42, .12))
    pts, rad = trunk(t, 7.5, 0.6, lean=4, curve=0.03, segs=12, col=bark, taper=0.15,
                     bands=lambda i: 0.75 if i % 2 else 1.05, gnarl=0.12)
    top = pts[-1]
    t.blob(top + UP * 0.3, (0.9, 0.9, 0.8), lin((.40, .38, .18)), top, (1, 1, 1), 0.2)
    n = 30 if t.detail else 8
    for k in range(n):
        a = 2 * math.pi * k / n + t.u(-.1, .1)
        tier = k % 3  # oben steil, unten hängend und gelblicher
        d = Vector((math.cos(a), math.sin(a), 0.9 - 0.45 * tier)).normalized()
        col = leaf.lerp(lin((.50, .48, .20)), 0.25 * tier) * t.u(0.9, 1.1)
        t.frond(top + UP * 0.4, d, t.u(5.5, 6.8), 1.1, col, 0.6 + 0.25 * tier)


def busch(t):
    """Macchia-/Waldrandbusch: mehrere Klumpen, flach und breit."""
    leaf = lin((.22, .30, .13))
    for k in range(5 if t.detail else 2):
        a, rr = t.u(0, 2 * math.pi), t.u(0, 0.7)
        s = t.u(0.6, 1.0)
        t.blob(Vector((math.cos(a) * rr, math.sin(a) * rr, 0.45 + 0.2 * s)), (s, s, s * 0.8), leaf * t.u(0.8, 1.15),
               Vector((0, 0, 0.6)), (1.4, 1.4, 1.0), 0.35)


SPECIES = dict(buche=buche, eiche=eiche, birke=birke, fichte=fichte, kiefer=kiefer, olive=olive, pinie=pinie, aleppo=aleppo,
               steineiche=steineiche, zypresse=zypresse, palme=palme, busch=busch)


def to_object(name, t):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in t.v], [], t.f)
    me.validate()
    col = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
    for i, c in enumerate(t.c):
        col.data[i].color = (min(c.x, 1), min(c.y, 1), min(c.z, 1), 0.0)  # Alpha 0: leuchtet nicht (props.gdshader)
    me.color_attributes.active_color = col
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices([tuple(n) for n in t.n])
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out, preview = args[0], (args[1] if len(args) > 1 else None)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = []
    for k, (name, build) in enumerate(SPECIES.items()):
        for detail in (True, False):
            t = Tree(1000 + k, detail)
            build(t)
            objs.append(to_object(name if detail else name + "_lod", t))
            print(f"{objs[-1].name}: {len(t.f)} Flächen")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True, export_apply=True, export_vertex_color="ACTIVE",
                              export_materials="NONE")
    if preview:
        render_preview(objs, preview)


def render_preview(objs, path):
    """Alle Arten nebeneinander von der Seite (nur Detailversion), heller Hintergrund, Farben ohne Filmkurve."""
    det = [o for o in objs if not o.name.endswith("_lod")]
    for i, o in enumerate(det):
        o.location = ((i % 6) * 17, 0 if i < 6 else 400, 0)
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (42, -88, 16)
    cam.rotation_euler = (math.radians(84), 0, 0)
    cam.data.lens = 28
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.75, 0.82, 0.9)
    scene.display.shading.show_shadows = True
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = 2400, 1300
    for row in (0, 1):  # Mischwald, dann Mittelmeer
        for i, o in enumerate(objs):
            o.hide_render = o.name.endswith("_lod") or (det.index(o) // 6 != row if o in det else True)
        cam.location = (42, -88 + 400 * row, 16)
        scene.render.filepath = path.replace(".png", f"_{row}.png")
        bpy.ops.render.render(write_still=True)


main()
