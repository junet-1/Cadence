"""Bäume und Büsche für Cadence: europäischer Mischwald und Mittelmeer (Mallorca).

  blender -b -P blender/trees.py -- models/trees.glb [vorschau.png]

Schreibt auch models/leaves.png (neben der .glb): Blatt-Atlas, 2 × 2 Zweigbilder (Laub breit, Laub klein, Nadelzweig,
Kiefernbüschel), in Blender von oben gerendert, Graustufen + Alpha.
Konvention: Stammfuß im Ursprung, Z oben (glTF/Godot: Y). Farben als Vertexfarben (linear) -> props.gdshader, kein Material.
Zu jeder Art gibt es "<name>_lod" für die Ferne (wenige Dutzend Dreiecke). Äste wachsen rekursiv (zufällig, aber fest je Art).
Laub nah = Blattkarten: beidseitige Quads mit einem Zweigbild aus dem Atlas (UV.x in 1…2 markiert sie für den Shader, der
dort ausschneidet und die Vertexfarbe mit dem Bild multipliziert), um jede Astspitze, dazu dunkle Innenklumpen gegen Durchblick.
Laub fern = Klumpen aus verbeulten Ikosaedern. Laubnormalen zeigen vom Kronenmittelpunkt weg (weiches, volumiges Licht),
Farbe dunkler nach innen und unten (Umgebungsverdeckung als Vertexfarbe).
Höhen sind echte Größen bei Skalierung 1 (world.py/route.py skalieren je Baum 0,7–1,4).
"""
import math
import os
import random
import sys

import bpy
from mathutils import Vector

UP = Vector((0, 0, 1))
BROAD, SMALL, NEEDLE, TUFT = range(4)  # Kacheln im Blatt-Atlas (Spalte = k % 2, Zeile = k // 2, Zeile 0 oben)


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
        self.uv = {}  # Vertex -> (u, v) im glTF-Sinn (v nach unten), nur Blattkarten (u + 1)
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

    def card(self, p, up, size, tile, col, center, crown, side=None, width=1.0):
        """Blattkarte: Zweigbild (Atlas-Kachel) als beidseitiges Quad. Fuß bei p, Zweig zeigt entlang up; side = Breitenrichtung
        (sonst zufällig um up gedreht). Normale vom Kronenmittelpunkt weg, Farbe nach Lage in der Krone wie bei blob()."""
        up = up.normalized()
        if side is None:
            side = up.orthogonal().normalized()
            ang = self.u(0, 2 * math.pi)
            side = (side * math.cos(ang) + up.cross(side) * math.sin(ang)).normalized()
        side = (side - up * side.dot(up)).normalized()
        u0, v0 = 1.0 + (tile % 2) * 0.5, (tile // 2) * 0.5
        corners = []
        for x, y in ((0, 0), (1, 0), (1, 1), (0, 1)):
            q = p + side * ((x - 0.5) * size * width) + up * ((y - 0.05) * size)
            corners.append((q, (u0 + x * 0.5, v0 + (1 - y) * 0.5)))
        rel = p + up * size * 0.5 - center
        g = Vector((rel.x / crown[0], rel.y / crown[1], rel.z / crown[2]))
        nrm = g.normalized() if g.length > 1e-3 else UP.copy()
        shade = 0.62 + 0.55 * (0.55 * min(g.length, 1.2) / 1.2 + 0.45 * (0.5 + 0.5 * max(min(g.z, 1), -1)))
        for flip in (False, True):
            o = len(self.v)
            for q, uv in corners:
                self.uv[len(self.v)] = uv
                self.v.append(q)
                self.n.append(nrm)
                self.c.append(col * shade * self.u(0.92, 1.08))
            self.f.append((o + 3, o + 2, o + 1, o) if flip else (o, o + 1, o + 2, o + 3))

    def leaves(self, tile, col, center, crown, size, per_tip=7, var=0.12, spread=0.6, core=0.55, up_bias=0.35, keep=1.0):
        """Nah: Blattkarten um jede Astspitze (zur Spitze hin und nach außen gerichtet) + ein dunkler Innenklumpen je Spitze,
        etwas zur Kronenmitte gezogen -> geschlossene Krone ohne schwebende Teile. Fern: wie foliage() (Klumpen)."""
        for p, d, r0 in self.tips:
            if self.r.random() > keep:
                continue
            cc = col * self.u(1 - var, 1 + var)
            out = (p - center)
            out = Vector((out.x / crown[0], out.y / crown[1], out.z / crown[2]))
            out = out.normalized() if out.length > 1e-3 else UP.copy()
            if core > 0:
                q = size * core * self.u(0.7, 0.9)
                self.blob(p.lerp(center, 0.25), (q, q, q * 0.8), cc * 0.8, center, crown, 0.35, sub=0, ao=(0.6, 0.95))
            for _ in range(per_tip):
                o = Vector((self.u(-1, 1), self.u(-1, 1), self.u(-0.7, 1))) * size * spread
                up = (d * 0.8 + out * 0.6 + UP * up_bias + Vector((self.u(-1, 1), self.u(-1, 1), self.u(-1, 1))) * 0.55)
                self.card(p + o - up.normalized() * size * 0.3, up, size * self.u(0.8, 1.15), tile, cc * self.u(0.9, 1.1), center, crown)

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
# Nah (t.detail): eine Verzweigungsstufe mehr, Blattkarten (Tree.leaves); fern: weniger Äste, Laubklumpen (Tree.foliage).

def buche(t):
    bark, leaf = lin((.46, .45, .42)), lin((.26, .42, .12))
    pts, rad = trunk(t, 6.5, 0.38, lean=2, col=bark)
    d = 3 if t.detail else 2
    P = dict(bark=bark, bend=0.12, up=0.08, depth=d, kids=2, spread=(25, 45), ratio=(0.55, 0.72) if t.detail else (0.5, 0.7),
             gnarl=0.05, end_tip=False)
    for k in range(6 if t.detail else 3):
        a = 2 * math.pi * k / 6 + t.u(-.3, .3)
        t.limb(pts[-1] + UP * t.u(-1.0, 0.5), Vector((math.cos(a) * .6, math.sin(a) * .6, 1)), t.u(5.5, 7) if t.detail else t.u(6, 8), 0.2, d, P)
    if t.detail:
        t.leaves(BROAD, leaf, Vector((0, 0, 13)), (6, 6, 6.5), 1.7, per_tip=6)
    else:
        t.foliage(leaf, Vector((0, 0, 13)), (6, 6, 6.5), 2.6, fill=10)


def eiche(t):
    bark, leaf = lin((.30, .26, .21)), lin((.19, .33, .09))
    pts, rad = trunk(t, 3.6, 0.55, lean=3, curve=0.03, col=bark, gnarl=0.15)
    d = 3 if t.detail else 2
    P = dict(bark=bark, bend=0.3, up=0.02, depth=d, kids=2, spread=(30, 60), ratio=(0.55, 0.75) if t.detail else (0.5, 0.75),
             gnarl=0.18, seg=1.3, end_tip=False)
    for k in range(6 if t.detail else 3):
        a = 2 * math.pi * k / 6 + t.u(-.4, .4)
        t.limb(pts[-1] + UP * t.u(-.4, .6), Vector((math.cos(a), math.sin(a), t.u(.5, 1.1))), t.u(4.5, 6.0) if t.detail else t.u(5.5, 7.5), 0.26, d, P)
    if t.detail:
        t.leaves(BROAD, leaf, Vector((0, 0, 9.5)), (7.5, 7.5, 5), 1.6, per_tip=6, var=0.15)
    else:
        t.foliage(leaf, Vector((0, 0, 9.5)), (7.5, 7.5, 5), 2.5, var=0.15, jag=0.4, keep=0.92, fill=6)


def birke(t):
    bark, leaf = lin((.86, .85, .80)), lin((.40, .54, .16))
    dark = lambda i: 0.25 if t.r.random() < 0.25 else 1.0  # schwarze Rindenflecken
    pts, rad = trunk(t, 14, 0.2, lean=4, curve=0.02, segs=10, col=bark, bands=dark, taper=0.7)
    P = dict(bark=lin((.35, .30, .27)), bend=0.15, up=-0.12, depth=2 if t.detail else 1, kids=3 if t.detail else 3,
             spread=(30, 70), ratio=(0.5, 0.75), seg=1.2, at=(0.25, 1.0))
    for k in range(9 if t.detail else 3):
        i = 4 + k * (len(pts) - 5) // 9 if t.detail else 1 + k % 2
        a = t.u(0, 2 * math.pi)
        t.limb(pts[min(i, len(pts) - 1)], Vector((math.cos(a), math.sin(a), 1.3)), t.u(2.5, 4.5), 0.06, P["depth"], P)
    t.tips.append((pts[-1], UP, 0.05))
    if t.detail:  # hängende Zweige: Karten zeigen nach unten-außen
        t.leaves(SMALL, leaf, Vector((0, 0, 10)), (3.5, 3.5, 5.5), 1.1, per_tip=3, var=0.18, up_bias=-0.5, core=0.4)
    else:
        t.foliage(leaf, Vector((0, 0, 10)), (3.5, 3.5, 5.5), 1.05, var=0.18, jag=0.4, flat=1.3, fill=8)


def _spruce_lod(t, leaf, H):
    """Fichte fern: Etagen aus Kegelschirmen mit hängenden, abwechselnd langen Zweigspitzen."""
    tiers, n = 4, 5
    for i in range(tiers):
        s = i / tiers
        z = 2.2 + s * (H - 3.2) + t.u(-.2, .2)
        R = 4.4 * (1 - s) ** 0.9 + 0.35
        o = len(t.v)
        t.v.append(Vector((0, 0, z + R * 0.35 + 0.4)))
        t.n.append(UP.copy())
        t.c.append(leaf * 0.7)
        for k in range(n):
            a = 2 * math.pi * (k + 0.5 * (i % 2)) / n + t.u(-.15, .15)
            rr = R * (1.0 if k % 2 == 0 else 0.7) * t.u(0.85, 1.15)
            t.v.append(Vector((math.cos(a) * rr, math.sin(a) * rr, z - R * t.u(0.35, 0.6))))
            t.n.append((Vector((math.cos(a), math.sin(a), 0.0)) * 0.75 + UP * 0.5).normalized())
            t.c.append(leaf * (t.u(1.05, 1.4) if k % 2 == 0 else t.u(0.8, 1.0)))
        under = len(t.v)
        t.v.append(Vector((0, 0, z - R * 0.2)))
        t.n.append(-UP)
        t.c.append(leaf * 0.4)
        for k in range(n):
            a, b = o + 1 + k, o + 1 + (k + 1) % n
            t.f += [(o, a, b), (under, b, a)]


def fichte(t):
    bark, leaf = lin((.32, .24, .18)), lin((.09, .21, .11))
    H = 24
    trunk(t, H * 0.9, 0.32, col=bark, taper=0.8)
    if not t.detail:
        _spruce_lod(t, leaf, H)
        return
    center, crown = Vector((0, 0, H * 0.45)), (4.5, 4.5, H * 0.55)
    # dunkler, schlanker Kern gegen Durchblick (man sieht ihn nur zwischen den Zweigen)
    t.lathe([(2.0 + (H - 2.5) * i / 8, 2.0 * (1 - i / 8) ** 1.1 + 0.15) for i in range(9)], leaf * 0.35, 8, center, crown, noise=0.35)
    z = 2.0
    while z < H - 0.6:
        s = (z - 2.0) / (H - 2.0)
        R = 4.6 * (1 - s) ** 0.9 + 0.5
        n = 5 if s < 0.7 else 4
        for k in range(n):  # Quirl: Äste waagrecht hinaus, außen herabhängend, flache Nadelzweige entlang
            a = 2 * math.pi * k / n + t.u(-0.3, 0.3) + z
            dirh = Vector((math.cos(a), math.sin(a), 0))
            L = R * t.u(0.8, 1.1)
            p0 = Vector((0, 0, z))
            pts = [p0, p0 + dirh * L * 0.5 + UP * L * 0.05, p0 + dirh * L + UP * (-L * t.u(0.15, 0.3))]
            t.tube(pts, [0.06 * (1 - s) + 0.025, 0.03, 0.015], bark, sides=3)
            side = dirh.cross(UP).normalized()
            m = max(2, int(L / 0.8))
            for j in range(m):
                u = (j + 0.3) / m
                q = pts[0].lerp(pts[1], u * 2) if u < 0.5 else pts[1].lerp(pts[2], u * 2 - 1)
                fw = (dirh + UP * (0.1 - 0.5 * u) + Vector((t.u(-.25, .25), t.u(-.25, .25), t.u(-.15, .15)))).normalized()
                sz = t.u(1.5, 2.1) * (0.65 + 0.5 * (1 - s))
                cc = leaf * t.u(0.85, 1.2)
                # gekreuzt: ein Zweig leicht schräg liegend (von oben/unten sichtbar), einer hochkant (von der Seite, also
                # von der Straße aus) – flach allein sieht man von der Seite nur Kanten und kahle Äste
                t.card(q - fw * 0.25, fw, sz, NEEDLE, cc, center, crown, side=side + UP * t.u(-0.5, 0.5))
                t.card(q - fw * 0.25, fw, sz * 0.9, NEEDLE, cc * 0.9, center, crown, side=UP + side * t.u(-0.4, 0.4))
                if j == m - 1:  # Astende: Zweigvorhang hängt herab (typische Fichtensilhouette)
                    t.card(q, (fw - UP * 1.2).normalized(), sz * 0.9, NEEDLE, leaf * t.u(0.8, 1.0), center, crown, side=side)
        z += t.u(0.95, 1.2)
    t.card(Vector((0, 0, H - 1.6)), UP, 1.8, NEEDLE, leaf, center, crown)  # Wipfel
    t.card(Vector((0, 0, H - 1.6)), UP, 1.8, NEEDLE, leaf, center, crown, side=Vector((0, 1, 0)))
    t.tips.clear()


def kiefer(t):
    low, high, leaf = lin((.36, .30, .26)), lin((.64, .40, .23)), lin((.16, .30, .15))
    pts, rad = trunk(t, 15, 0.32, lean=4, curve=0.03, segs=8, col=low, bands=lambda i: 1.0 if i < 5 else 1.4, taper=0.6)
    d = 2 if t.detail else 1
    P = dict(bark=high, bend=0.3, up=0.05, depth=d, kids=2 if t.detail else 2, spread=(30, 60), ratio=(0.45, 0.65), gnarl=0.15, seg=1.2)
    for k in range(7 if t.detail else 3):
        i = len(pts) - 1 - (k % 3)
        a = 2 * math.pi * k / 7 + t.u(-.5, .5)
        t.limb(pts[max(i, 1)], Vector((math.cos(a), math.sin(a), t.u(.1, .5))), t.u(2.8, 4.2), 0.12, d, P)
    if t.detail:
        t.leaves(TUFT, leaf, pts[-1] + UP * 1.0, (4, 4, 2.5), 1.3, per_tip=4, up_bias=0.8, core=0.6)
    else:
        t.foliage(leaf, pts[-1] + UP * 1.0, (4, 4, 2.5), 1.6, flat=0.55, jag=0.35, fill=3)


# --- Arten (Mittelmeer / Mallorca) ---

def olive(t):
    bark, leaf = lin((.32, .29, .25)), lin((.47, .52, .38))
    d = 3 if t.detail else 2
    P = dict(bark=bark, bend=0.35, up=0.05, depth=d, kids=2, spread=(25, 55), ratio=(0.6, 0.78) if t.detail else (0.55, 0.75),
             gnarl=0.3, seg=0.8, taper=0.6, end_tip=False)
    for k in range(2 if t.detail else 1):  # zwei verdrehte Stämmlinge aus einem knorrigen Fuß
        a = t.u(0, 2 * math.pi)
        dd = Vector((math.cos(a) * .45, math.sin(a) * .45, 1))
        pts, rad = [Vector((0, 0, 0))], [0.42]
        q = Vector((0, 0, 0))
        for i in range(5 if t.detail else 2):
            ang = a + i * 0.9
            q = q + Vector((math.cos(ang) * .25, math.sin(ang) * .25, 0.45)) + dd * 0.12
            pts.append(q.copy())
            rad.append(0.36 - i * 0.03)
        t.tube(pts, rad, bark, gnarl=0.45)
        for j in range(3):
            b = a + j * 2.1 + t.u(-.4, .4)
            t.limb(pts[-1], Vector((math.cos(b), math.sin(b), t.u(.5, 1.0))), t.u(1.7, 2.3) if t.detail else t.u(2.0, 2.8), 0.15, d, P)
    if t.detail:
        t.leaves(SMALL, leaf, Vector((0, 0, 3.6)), (3.3, 3.3, 2.0), 0.95, per_tip=4, var=0.1, up_bias=0.2)
    else:
        t.foliage(leaf, Vector((0, 0, 3.6)), (3.3, 3.3, 2.0), 1.15, var=0.1, jag=0.45, flat=0.75, fill=6)


def pinie(t):
    bark, leaf = lin((.48, .30, .20)), lin((.15, .31, .13))
    pts, rad = trunk(t, 9.0, 0.36, lean=7, curve=0.04, segs=7, col=bark, gnarl=0.08, taper=0.45)
    d = 2 if t.detail else 1
    P = dict(bark=bark, bend=0.12, up=0.05, depth=d, kids=3, spread=(25, 50), ratio=(0.5, 0.65) if t.detail else (0.5, 0.7), seg=1.2)
    top = pts[-1]
    for k in range(5 if t.detail else 3):
        a = 2 * math.pi * k / 5 + t.u(-.3, .3)
        t.limb(top, Vector((math.cos(a), math.sin(a), 0.9)), t.u(3.0, 4.2), 0.18, d, P)
    c = top + UP * 3.0
    if t.detail:  # flacher, dunkler Schirm als Kern, Nadelbüschel obenauf und am Rand
        for k in range(9):
            a, rr = t.u(0, 2 * math.pi), t.u(0, 4.0)
            p = c + Vector((math.cos(a) * rr, math.sin(a) * rr, 0.3 - 0.05 * rr * rr))
            s = t.u(1.8, 2.4)
            t.blob(p, (s, s, s * 0.35), leaf * 0.55, c, (6.5, 6.5, 1.8), 0.3, sub=0, ao=(0.5, 0.9))
        t.leaves(TUFT, leaf, c, (6.5, 6.5, 1.8), 1.4, per_tip=5, up_bias=1.2, core=0)
    else:
        t.foliage(leaf, c, (6.5, 6.5, 1.8), 2.0, flat=0.45, jag=0.3, fill=0)
        for k in range(3):
            a, rr = t.u(0, 2 * math.pi), t.u(0, 4.6)
            p = c + Vector((math.cos(a) * rr, math.sin(a) * rr, 0.6 - 0.05 * rr * rr))
            t.blob(p, (3.2, 3.2, 3.2 * 0.42), leaf * t.u(0.85, 1.05), c, (6.5, 6.5, 1.8), 0.3)


def aleppo(t):
    bark, leaf = lin((.43, .40, .36)), lin((.31, .41, .18))
    pts, rad = trunk(t, 7.0, 0.30, lean=13, curve=0.08, segs=7, col=bark, gnarl=0.12, taper=0.5)
    d = 3 if t.detail else 2
    P = dict(bark=bark, bend=0.35, up=0.06, depth=d, kids=2, spread=(25, 55), ratio=(0.55, 0.72) if t.detail else (0.5, 0.7), gnarl=0.15, seg=1.2)
    for k in range(4 if t.detail else 2):
        i = len(pts) - 1 - k
        a = t.u(0, 2 * math.pi)
        t.limb(pts[max(i, 1)], Vector((math.cos(a), math.sin(a), t.u(.6, 1.2))), t.u(2.6, 3.8) if t.detail else t.u(3.0, 4.5), 0.14, d, P)
    if t.detail:
        t.leaves(TUFT, leaf, pts[-1] + UP * 2.0, (4.5, 4.5, 3), 1.0, per_tip=3, up_bias=0.6, core=0.5, keep=0.9)
    else:
        t.foliage(leaf, pts[-1] + UP * 2.0, (4.5, 4.5, 3), 1.25, flat=0.7, jag=0.45, keep=0.85, fill=2)


def steineiche(t):
    bark, leaf = lin((.24, .22, .20)), lin((.14, .23, .09))
    pts, rad = trunk(t, 2.2, 0.38, lean=4, col=bark, gnarl=0.18)
    d = 3 if t.detail else 2
    P = dict(bark=bark, bend=0.25, up=0.06, depth=d, kids=2, spread=(30, 55), ratio=(0.55, 0.75), gnarl=0.2, end_tip=False)
    for k in range(5 if t.detail else 2):
        a = 2 * math.pi * k / 5 + t.u(-.4, .4)
        t.limb(pts[-1], Vector((math.cos(a), math.sin(a), 1.1)), t.u(2.4, 3.2) if t.detail else t.u(2.8, 3.8), 0.18, d, P)
    if t.detail:
        t.leaves(BROAD, leaf, Vector((0, 0, 5.6)), (4.2, 4.2, 3.6), 1.1, per_tip=5)
    else:
        t.foliage(leaf, Vector((0, 0, 5.6)), (4.2, 4.2, 3.6), 1.5, var=0.12, jag=0.35, fill=6)


def zypresse(t):
    leaf = lin((.08, .18, .08))
    trunk(t, 1.0, 0.18, col=lin((.30, .24, .18)))
    H = 13.0
    rings = 16 if t.detail else 5
    prof = [(0.6 + H * i / rings, 1.15 * math.sin(math.pi * min(0.15 + i / rings * 0.95, 1.0)) ** 0.7 * (1 - 0.55 * i / rings) + 0.15)
            for i in range(rings + 1)]
    center, crown = Vector((0, 0, 5)), (1.2, 1.2, 7)
    if not t.detail:
        t.lathe(prof, leaf, 5, center, crown, noise=0.25)
        return
    t.lathe([(z, r * 0.8) for z, r in prof], leaf * 0.6, 9, center, crown, noise=0.25)  # Kern etwas kleiner und dunkler
    for k in range(110):  # aufrechte Nadelzweige rundum, folgen dem Umriss -> fransige Säule
        z = t.u(0.8, H)
        i = min(int((z - 0.6) / H * rings), rings - 1)
        r = prof[i][1] * 0.85
        a = t.u(0, 2 * math.pi)
        out = Vector((math.cos(a), math.sin(a), 0))
        t.card(Vector((0, 0, z)) + out * r * t.u(0.7, 1.0), (UP * 1.6 + out * 0.5).normalized(), t.u(0.9, 1.3), NEEDLE,
               leaf * t.u(0.85, 1.15), center, crown, side=out.cross(UP))


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
    """Macchia-/Waldrandbusch: mehrere Klumpen, flach und breit; nah mit Blattkarten außen."""
    leaf = lin((.22, .30, .13))
    for k in range(5 if t.detail else 2):
        a, rr = t.u(0, 2 * math.pi), t.u(0, 0.7)
        s = t.u(0.6, 1.0) * (0.8 if t.detail else 1.0)
        p = Vector((math.cos(a) * rr, math.sin(a) * rr, 0.45 + 0.2 * s))
        t.blob(p, (s, s, s * 0.8), leaf * (t.u(0.5, 0.7) if t.detail else t.u(0.8, 1.15)), Vector((0, 0, 0.6)), (1.4, 1.4, 1.0), 0.35)
        if t.detail:
            t.tips.append((p + Vector((math.cos(a), math.sin(a), 0.6)).normalized() * s * 0.7, Vector((math.cos(a), math.sin(a), 0.8)), 0.02))
    if t.detail:
        t.leaves(BROAD, leaf, Vector((0, 0, 0.6)), (1.4, 1.4, 1.0), 0.75, per_tip=5, spread=0.7, core=0)


def blade(t, base, d, length, width, col, droop):
    """Grashalm/Blatt: schmaler Streifen, bogig überhängend, beidseitig, zur Spitze schmal und heller."""
    n = 4 if t.detail else 2
    side = d.cross(UP).normalized() if abs(d.z) < 0.99 else Vector((1, 0, 0))
    for flip in (False, True):
        o = len(t.v)
        for i in range(n + 1):
            u = i / n
            p = base + d * (length * u) + UP * (length * (0.9 * u - droop * u * u))
            w = width * (1 - 0.85 * u)
            for q in (p - side * w, p + side * w):
                t.v.append(q)
                t.n.append((UP * 0.7 + side.cross(d) * 0.3).normalized() * (-1 if flip else 1))
                t.c.append(col * (0.55 + 0.6 * u))
        for i in range(n):
            a, b = o + 2 * i, o + 2 * i + 2
            q = (a, a + 1, b + 1, b)
            t.f.append(q[::-1] if flip else q)


def farn(t):
    """Wurmfarn (Waldboden): Trichter aus 8–11 überhängenden Wedeln, ~0,9 m."""
    for k in range(t.r.randint(8, 11) if t.detail else 4):
        a = 2 * math.pi * k / 10 + t.u(-0.3, 0.3)
        d = Vector((math.cos(a), math.sin(a), t.u(0.9, 1.5))).normalized()
        t.frond(Vector((0, 0, 0.05)), d, t.u(0.75, 1.05), t.u(0.14, 0.2), lin((.20, .36, .12)) * t.u(0.85, 1.15), t.u(0.55, 0.8))


def hochgras(t):
    """Hohes Gras / Seggen (Waldrand, Lichtungen, Wegrand): Horst aus 0,7–1,3 m langen, überhängenden Halmen, ein paar Rispen."""
    for k in range(26 if t.detail else 8):
        a, r = t.u(0, 2 * math.pi), t.u(0, 0.18)
        d = Vector((math.cos(a) * t.u(0.15, 0.5), math.sin(a) * t.u(0.15, 0.5), 1)).normalized()
        col = lin((.42, .50, .22)).lerp(lin((.62, .58, .34)), t.u(0, 0.6))
        blade(t, Vector((math.cos(a) * r, math.sin(a) * r, 0)), d, t.u(0.7, 1.3), t.u(0.018, 0.03), col, t.u(0.35, 0.7))
    if t.detail:
        for k in range(5):
            a = t.u(0, 2 * math.pi)
            top = Vector((math.cos(a) * 0.25, math.sin(a) * 0.25, t.u(1.1, 1.45)))
            t.tube([Vector((0, 0, 0)), top * 0.5, top], [0.006, 0.005, 0.004], lin((.55, .55, .30)), sides=3)
            t.blob(top + UP * 0.08, (0.035, 0.035, 0.12), lin((.62, .55, .38)), top, (0.1, 0.1, 0.2), 0.3, sub=0)


def staude(t):
    """Hohe Staude (Weidenröschen/Fingerhut am Waldrand und auf Lichtungen): Stängel 1,2–1,7 m, Blätter, rosa Blütenähre."""
    for k in range(3 if t.detail else 2):
        a, r = t.u(0, 2 * math.pi), t.u(0, 0.12)
        h = t.u(1.2, 1.7)
        b0 = Vector((math.cos(a) * r, math.sin(a) * r, 0))
        top = b0 + Vector((t.u(-0.1, 0.1), t.u(-0.1, 0.1), h))
        t.tube([b0, b0.lerp(top, 0.5), top], [0.012, 0.009, 0.005], lin((.30, .38, .16)), sides=3 if t.detail else 3)
        for j in range(6 if t.detail else 2):  # Blätter entlang des Stängels, unten länger
            u = 0.12 + 0.12 * j
            aa = t.u(0, 2 * math.pi)
            blade(t, b0.lerp(top, u), Vector((math.cos(aa), math.sin(aa), 0.5)).normalized(), 0.28 * (1.2 - u), 0.03,
                  lin((.24, .38, .13)), 0.35)
        pink = lin((.78, .30, .55)) if t.r.random() < 0.7 else lin((.70, .45, .80))
        for j in range(7 if t.detail else 2):  # Ähre: kleine Blütenklumpen, oben kleiner
            u = j / 7
            t.blob(b0.lerp(top, 0.72 + 0.28 * u), (0.05 * (1 - 0.5 * u),) * 2 + (0.045,), pink * t.u(0.85, 1.1), top, (0.1, 0.1, 0.3), 0.3,
                   sub=0, ao=(0.85, 1.1))


SPECIES = dict(buche=buche, eiche=eiche, birke=birke, fichte=fichte, kiefer=kiefer, olive=olive, pinie=pinie, aleppo=aleppo,
               steineiche=steineiche, zypresse=zypresse, palme=palme, busch=busch, farn=farn, hochgras=hochgras, staude=staude)


def to_object(name, t):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in t.v], [], t.f)
    me.validate()
    col = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
    for i, c in enumerate(t.c):
        col.data[i].color = (min(c.x, 1), min(c.y, 1), min(c.z, 1), 0.0)  # Alpha 0: leuchtet nicht (props.gdshader)
    me.color_attributes.active_color = col
    if t.uv:  # Blattkarten: Atlas-UV (glTF-v nach unten -> Blender-v nach oben), alles andere (0, 0) = keine Karte
        uv = me.uv_layers.new(name="UV")
        for lp in me.loops:
            u, v = t.uv.get(lp.vertex_index, (0.0, 1.0))
            uv.data[lp.index].uv = (u, 1.0 - v)
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices([tuple(n) for n in t.n])
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out, preview = args[0], (args[1] if len(args) > 1 else None)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    render_atlas(os.path.join(os.path.dirname(os.path.abspath(out)), "leaves.png"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = []
    for k, (name, build) in enumerate(SPECIES.items()):
        for detail in (True, False):
            t = Tree(1000 + k, detail)
            build(t)
            objs.append(to_object(name if detail else name + "_lod", t))
            print(f"{objs[-1].name}: {len(t.f)} Flächen, {len(t.uv) // 8} Blattkarten")
            assert all(1.0 <= u <= 2.0 and 0.0 <= v <= 1.0 for u, v in t.uv.values()), name  # Shader erkennt Karten an u ≥ 1
            assert detail or not t.uv, name  # Fernversionen ohne Karten (Mip-Stufen würden sie auflösen)
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True, export_apply=True, export_vertex_color="ACTIVE",
                              export_materials="NONE")
    if preview:
        render_preview(objs, preview)


class Sketch:
    """2D-Zeichnung für den Atlas: Polygone in der Ebene (z = Zeichenreihenfolge), Graustufe je Vertex."""
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.v, self.f, self.c = [], [], []
        self.z = 0.0

    def poly(self, pts, cols):
        self.z += 1e-4
        o = len(self.v)
        self.v += [Vector((p.x, p.y, self.z)) for p in pts]
        self.c += cols
        return o

    def strip(self, path, widths, col0, col1):
        """Band entlang path (Liste 2D-Punkte), Breite je Punkt, Farbe col0 -> col1."""
        left, right, cols = [], [], []
        for i, p in enumerate(path):
            d = (path[min(i + 1, len(path) - 1)] - path[max(i - 1, 0)]).normalized()
            n = Vector((-d.y, d.x, 0))
            left.append(p + n * widths[i])
            right.append(p - n * widths[i])
            cols.append(col0 * (1 - i / (len(path) - 1)) + col1 * (i / (len(path) - 1)))
        o = self.poly(left + right, cols + cols)
        k = len(path)
        self.f += [(o + i, o + i + 1, o + k + i + 1, o + k + i) for i in range(k - 1)]

    def leaf(self, base, d, length, width, g, pointed=0.8):
        """Blatt: Stiel, Spreite (spitz zulaufend), dunklere Mittelrippe."""
        d = d.normalized()
        stalk = length * 0.12
        self.strip([base, base + d * stalk], [width * 0.06, width * 0.05], g * 0.55, g * 0.6)
        b = base + d * stalk
        path = [b + d * length * i / 8 for i in range(9)]
        ws = [width * max(math.sin(math.pi * (i / 8) ** pointed), 0.0) ** 0.85 + 1e-4 for i in range(9)]
        self.strip(path, ws, g * 0.72, g)
        self.strip(path[:8], [width * 0.07] * 8, g * 0.6, g * 0.75)

    def needle(self, base, d, length, width, g):
        self.strip([base, base + d.normalized() * length], [width, width * 0.15], g * 0.7, g)

    def twig(self, path, r0, r1, g=0.34):
        self.strip(path, [r0 + (r1 - r0) * i / (len(path) - 1) for i in range(len(path))], Vector((g, g, g)), Vector((g, g, g)) * 1.1)


def _curve(a, b, bend, n=8):
    side = Vector((-(b - a).y, (b - a).x, 0))
    return [a.lerp(b, i / n) + side * bend * math.sin(math.pi * i / n) for i in range(n + 1)]


def render_atlas(png):
    """Blatt-Atlas 1024², 2 × 2 Zweigbilder, Fuß unten Mitte jeder Kachel: breites Laub (Buche/Eiche), kleines Laub
    (Birke/Olive), flacher Nadelzweig (Fichte/Zypresse), Kiefernbüschel. Graustufen (Mittel ~0,8), Farbe kommt von der Art."""
    sk = Sketch(7)
    r = sk.r
    grey = lambda lo, hi: Vector((1, 1, 1)) * r.uniform(lo, hi)

    def tile_xy(k, x, y):  # Kachel-Koordinaten (0…1, y nach oben) -> Zeichenebene (Kachel 0 oben links)
        return Vector(((k % 2) + x, (1 - k // 2) + y, 0))

    def branchy(k, twigs, leaf_fn, spacing):
        for a, b, bend, r0 in twigs:
            path = [tile_xy(k, p.x, p.y) for p in _curve(Vector(a + (0,)), Vector(b + (0,)), bend)]
            sk.twig(path, r0, r0 * 0.3)
            L = (path[-1] - path[0]).length
            n = int(L / spacing)
            for i in range(1, n + 1):
                t = i / (n + 0.5)
                j = min(int(t * 8), 7)
                p = path[j].lerp(path[j + 1], t * 8 - j)
                d = (path[j + 1] - path[j]).normalized()
                leaf_fn(p, d, i % 2 * 2 - 1, 1 - 0.45 * t)

    # 0: breites Laub – Hauptzweig + Seitenzweige, wechselständige ovale Blätter
    def broad(p, d, side, s):
        ang = math.radians(r.uniform(35, 60)) * side
        dd = Vector((d.x * math.cos(ang) - d.y * math.sin(ang), d.x * math.sin(ang) + d.y * math.cos(ang), 0))
        sk.leaf(p, dd, 0.17 * s * r.uniform(0.85, 1.15), 0.065 * s * r.uniform(0.85, 1.15), grey(0.62, 1.0))
    branchy(BROAD, [((0.5, 0.04), (0.47, 0.8), 0.06, 0.012), ((0.5, 0.25), (0.2, 0.62), -0.08, 0.008),
                    ((0.49, 0.32), (0.8, 0.66), 0.08, 0.008), ((0.48, 0.55), (0.28, 0.85), -0.05, 0.006),
                    ((0.48, 0.6), (0.7, 0.88), 0.05, 0.006)], broad, 0.065)
    for k in range(3):  # Endknospen-Blätter
        sk.leaf(tile_xy(BROAD, 0.47, 0.8), Vector((r.uniform(-0.6, 0.6), 1, 0)), 0.15, 0.06, grey(0.7, 1.0))

    # 1: kleines Laub – feine, leicht hängende Zweige, kleine eiförmige Blätter
    def small(p, d, side, s):
        ang = math.radians(r.uniform(40, 75)) * side
        dd = Vector((d.x * math.cos(ang) - d.y * math.sin(ang), d.x * math.sin(ang) + d.y * math.cos(ang), 0))
        sk.leaf(p, dd, 0.085 * s * r.uniform(0.85, 1.2), 0.04 * s * r.uniform(0.8, 1.2), grey(0.6, 1.0), pointed=1.1)
    branchy(SMALL, [((0.5, 0.04), (0.5, 0.92), 0.04, 0.008), ((0.5, 0.2), (0.15, 0.7), -0.1, 0.005),
                    ((0.5, 0.25), (0.85, 0.72), 0.1, 0.005), ((0.5, 0.45), (0.25, 0.9), -0.06, 0.004),
                    ((0.5, 0.5), (0.78, 0.92), 0.06, 0.004), ((0.5, 0.15), (0.1, 0.38), 0.05, 0.004),
                    ((0.5, 0.15), (0.9, 0.4), -0.05, 0.004)], small, 0.04)

    # 2: Nadelzweig (Fichte): gefiederter Zweig, kurze Nadeln beidseitig dicht
    def needles(p, d, side, s):
        for sd in (-1, 1):
            ang = math.radians(r.uniform(50, 75)) * sd
            dd = Vector((d.x * math.cos(ang) - d.y * math.sin(ang), d.x * math.sin(ang) + d.y * math.cos(ang), 0))
            sk.needle(p, dd, 0.065 * s * r.uniform(0.8, 1.2), 0.0028, grey(0.5, 1.0))
    twigs = [((0.5, 0.04), (0.5, 0.94), 0.03, 0.01)]
    for i in range(7):
        y = 0.14 + i * 0.11
        for sd in (-1, 1):
            L = 0.36 * (1 - 0.6 * i / 7)
            twigs.append(((0.5, y), (0.5 + sd * L * 0.85, y + L * 0.55), sd * 0.04, 0.005))
    branchy(NEEDLE, twigs, needles, 0.008)

    # 3: Kiefernbüschel: wenige Triebe, an den Enden lange Nadeln im Fächer
    for a, b in (((0.5, 0.04), (0.5, 0.55)), ((0.5, 0.2), (0.22, 0.5)), ((0.5, 0.22), (0.78, 0.48)), ((0.5, 0.35), (0.35, 0.62)), ((0.5, 0.38), (0.66, 0.64))):
        path = [tile_xy(TUFT, p.x, p.y) for p in _curve(Vector(a + (0,)), Vector(b + (0,)), r.uniform(-0.05, 0.05))]
        sk.twig(path, 0.011, 0.007, g=0.3)
        d = (path[-1] - path[-3]).normalized()
        for j in range(6):  # dichte Nadelbüschel entlang des letzten Stücks, an der Spitze ein voller Fächer
            p = path[-1 - j]
            for _ in range(30 if j == 0 else 12):
                ang = math.radians(r.uniform(-65, 65) if j == 0 else r.choice((-1, 1)) * r.uniform(25, 75))
                dd = Vector((d.x * math.cos(ang) - d.y * math.sin(ang), d.x * math.sin(ang) + d.y * math.cos(ang), 0))
                sk.needle(p, dd, r.uniform(0.14, 0.22) * (1.0 if j == 0 else 0.85), 0.003, grey(0.5, 1.0))

    me = bpy.data.meshes.new("atlas")
    me.from_pydata([tuple(v) for v in sk.v], [], sk.f)
    col = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
    for i, c in enumerate(sk.c):
        col.data[i].color = (c.x ** 2.2, c.y ** 2.2, c.z ** 2.2, 1.0)
    me.color_attributes.active_color = col
    obj = bpy.data.objects.new("atlas", me)
    scene = bpy.context.scene
    scene.collection.objects.link(obj)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.0
    cam.location = (1, 1, 5)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "VERTEX"
    scene.display.render_aa = "16"
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x = scene.render.resolution_y = 1024
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = png
    bpy.ops.render.render(write_still=True)
    # Farbe unter transparenten Pixeln = mittleres Blattgrau: sonst färben Mip-Stufen die Ränder dunkel
    img = bpy.data.images.load(png)
    px = list(img.pixels)
    solid = [i for i in range(0, len(px), 4) if px[i + 3] > 0.5]
    mean = [sum(px[i + k] for i in solid) / len(solid) for k in range(3)]
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if a < 1.0:
            for k in range(3):
                px[i + k] = px[i + k] * a + mean[k] * (1 - a) if a > 0 else mean[k]
    img.pixels = px
    img.save()
    print(f"Atlas: {png}, Blattgrau {mean[0]:.2f}, Deckung {len(solid) / (len(px) / 4):.2f}")


def render_preview(objs, path):
    """Alle Arten nebeneinander (Detailversion), EEVEE mit Blatt-Atlas wie im Spiel (Karten ausgeschnitten, Farbe × Bild × 1,55),
    Sonne von links oben. Zwei Bilder: Mischwald, Mittelmeer."""
    atlas = bpy.data.images.load(os.path.join(os.path.dirname(os.path.abspath(path)), "leaves.png"))
    mat = bpy.data.materials.new("vorschau")
    mat.use_nodes = True
    nt = mat.node_tree
    nd = nt.nodes
    bsdf = nd["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.9
    col = nd.new("ShaderNodeVertexColor")
    col.layer_name = "Color"
    uvn = nd.new("ShaderNodeUVMap")
    sep = nd.new("ShaderNodeSeparateXYZ")
    isc = nd.new("ShaderNodeMath")
    isc.operation = "GREATER_THAN"
    isc.inputs[1].default_value = 0.999
    tex = nd.new("ShaderNodeTexImage")
    tex.image = atlas
    tex.extension = "REPEAT"
    tex.interpolation = "Closest"
    scl = nd.new("ShaderNodeMix")
    scl.data_type = "RGBA"
    scl.blend_type = "MULTIPLY"
    mixc = nd.new("ShaderNodeMix")
    mixc.data_type = "RGBA"
    cut = nd.new("ShaderNodeMath")
    cut.operation = "GREATER_THAN"
    cut.inputs[1].default_value = 0.5
    mixa = nd.new("ShaderNodeMix")
    nt.links.new(uvn.outputs["UV"], sep.inputs[0])
    nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
    nt.links.new(sep.outputs["X"], isc.inputs[0])
    nt.links.new(isc.outputs[0], mixc.inputs["Factor"])
    mixc.inputs[6].default_value = (1, 1, 1, 1)
    nt.links.new(tex.outputs["Color"], mixc.inputs[7])
    scl.inputs["Factor"].default_value = 1.0
    nt.links.new(col.outputs["Color"], scl.inputs[6])
    nt.links.new(mixc.outputs[2], scl.inputs[7])
    nt.links.new(scl.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(isc.outputs[0], mixa.inputs["Factor"])
    mixa.inputs[2].default_value = 1.0
    nt.links.new(tex.outputs["Alpha"], cut.inputs[0])
    nt.links.new(cut.outputs[0], mixa.inputs[3])
    nt.links.new(mixa.outputs[0], bsdf.inputs["Alpha"])
    det = [o for o in objs if not o.name.endswith("_lod")]
    for i, o in enumerate(det):
        o.location = ((i % 6) * 17, 0 if i < 6 else 400, 0)
        o.data.materials.append(mat)
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.rotation_euler = (math.radians(84), 0, 0)
    cam.data.lens = 28
    scene.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 4.0
    sun.rotation_euler = (math.radians(50), math.radians(-30), math.radians(-20))
    scene.collection.objects.link(sun)
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0.45, 0.55, 0.7)
    scene.render.engine = "BLENDER_EEVEE"
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = 2400, 1300
    for row in (0, 1):  # Mischwald, dann Mittelmeer
        for o in objs:
            o.hide_render = o.name.endswith("_lod") or (det.index(o) // 6 != row if o in det else True)
        cam.location = (42, -88 + 400 * row, 16)
        scene.render.filepath = path.replace(".png", f"_{row}.png")
        bpy.ops.render.render(write_still=True)


main()
