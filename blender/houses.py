"""Typische deutsche und regionale Hausmodelle (Alpen, Skandinavien, Mittelmeer, Nordafrika/Nahost, Nordamerika,
Lateinamerika/Südasien, Tropen, Afrika, Ostasien) für Kurbel.

  blender -b -P blender/houses.py -- models/houses.glb [vorschau.png]

Konvention je Haus: Länge entlang X (= Firstrichtung, längere Grundrissseite), Tiefe entlang Y, Erdgeschoss bei Z=0,
Grundriss mittig im Ursprung. Zu jedem Haus gibt es "<name>_lod" ohne Details für die Ferne.
Materialnamen sind die Schnittstelle zu Godot (Wall/Roof/Glass werden dort pro Haus eingefärbt bzw. nachts beleuchtet).
"""
import math
import sys

import bmesh
import bpy
from mathutils import Vector

X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
MATS = {  # Grundfarbe (sRGB), Rauheit
    "Wall": ((.92, .90, .85), .9), "Roof": ((.55, .22, .15), .8), "Glass": ((.05, .07, .09), .05),
    "Frame": ((.95, .95, .95), .6), "Timber": ((.28, .17, .09), .8), "Door": ((.36, .21, .11), .6),
    "Plinth": ((.48, .47, .45), .9), "Metal": ((.62, .64, .67), .4), "Wood": ((.47, .32, .19), .85),
    "Shutter": ((.20, .33, .23), .7), "Stone": ((.62, .60, .56), .9), "Flat": ((.36, .36, .38), .9),
    "Thatch": ((.60, .50, .30), .95),
}


class House:
    def __init__(self, detail):
        self.detail = detail
        self.v, self.f, self.m, self.uv = [], [], [], []
        self.win = 0  # laufende Fensternummer -> UV.x, damit Godot nachts einzelne Fenster zufällig einschaltet

    def prism(self, mat, pts, d, uv=(0, 0)):
        """Geschlossener Körper: ebenes Polygon pts, um Vektor d verschoben."""
        o = len(self.v)
        n = len(pts)
        self.v += [Vector(p) for p in pts] + [Vector(p) + d for p in pts]
        faces = [list(range(o, o + n)), list(range(o + 2 * n - 1, o + n - 1, -1))]
        faces += [[o + k, o + (k + 1) % n, o + n + (k + 1) % n, o + n + k] for k in range(n)]
        for f in faces:
            self.f.append(f)
            self.m.append(mat)
            self.uv.append([uv] * len(f))

    def obox(self, mat, c, a, b, size, uv=(0, 0)):
        """Quader um Mittelpunkt c mit Achsen a, b, a×b und Kantenlängen size."""
        c, a, b = Vector(c), Vector(a).normalized(), Vector(b).normalized()
        e = a.cross(b)
        sa, sb, se = size
        base = c - e * se / 2
        pts = [base + a * x * sa / 2 + b * y * sb / 2 for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        self.prism(mat, pts, e * se, uv)

    def box(self, mat, x0, x1, y0, y1, z0, z1):
        self.obox(mat, ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), X, Y, (x1 - x0, y1 - y0, z1 - z0))

    def pyramid(self, mat, cx, cy, z0, w, h):
        o = len(self.v)
        self.v += [Vector((cx + x * w / 2, cy + y * w / 2, z0)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        self.v.append(Vector((cx, cy, z0 + h)))
        for f in ([o, o + 3, o + 2, o + 1], [o, o + 1, o + 4], [o + 1, o + 2, o + 4], [o + 2, o + 3, o + 4], [o + 3, o, o + 4]):
            self.f.append(f)
            self.m.append(mat)
            self.uv.append([(0, 0)] * len(f))

    # --- Dächer -------------------------------------------------------------------------------------------------
    def gable_roof(self, L, D, z0, pitch, over=0.5, t=0.22, mat="Roof", wall="Wall"):
        tan = math.tan(math.radians(pitch))
        z1 = z0 + D / 2 * tan
        ze = z0 - over * tan
        for s in (-1, 1):
            pts = [(-L / 2 - over, s * (D / 2 + over), ze), (L / 2 + over, s * (D / 2 + over), ze), (L / 2 + over, 0, z1 + t * 0.6), (-L / 2 - over, 0, z1 + t * 0.6)]
            self.prism(mat, pts, -Z * t)
        for x in (-L / 2, L / 2 - 0.3):  # Giebeldreiecke
            self.prism(wall, [(x, -D / 2, z0), (x, D / 2, z0), (x, 0, z1)], X * 0.3)
        return z1

    def hip_roof(self, L, D, z0, pitch, over=0.5, t=0.22):
        tan = math.tan(math.radians(pitch))
        hx, hy = L / 2 + over, D / 2 + over
        z1 = z0 + D / 2 * tan
        ze = z0 - over * tan
        r = max(L / 2 - D / 2, 0.05)
        top = z1 + t * 0.6
        planes = [
            [(-hx, -hy, ze), (hx, -hy, ze), (r, 0, top), (-r, 0, top)],
            [(hx, hy, ze), (-hx, hy, ze), (-r, 0, top), (r, 0, top)],
            [(hx, -hy, ze), (hx, hy, ze), (r, 0, top)],
            [(-hx, hy, ze), (-hx, -hy, ze), (-r, 0, top)],
        ]
        for p in planes:
            self.prism("Roof", p, -Z * t)
        return z1

    def flat_roof(self, L, D, z0, parapet=0.6, mat="Plinth"):
        self.box("Flat", -L / 2, L / 2, -D / 2, D / 2, z0, z0 + 0.25)
        for s in (-1, 1):  # Attika
            self.box(mat, -L / 2, L / 2, s * D / 2 - 0.15, s * D / 2 + 0.15, z0, z0 + parapet)
            self.box(mat, s * L / 2 - 0.15, s * L / 2 + 0.15, -D / 2, D / 2, z0, z0 + parapet)

    # --- Fassade ------------------------------------------------------------------------------------------------
    def window(self, p0, t, n, u, z, w, h, shutters=False, sill=True, mat_frame="Frame"):
        """Fenster auf der Wandebene durch p0 (Normale n, Tangente t), Mitte u entlang t, Unterkante z."""
        if not self.detail:
            return
        c = Vector(p0) + t * u + Z * (z + h / 2)
        self.win += 1
        self.obox("Glass", c + n * 0.02, t, Z, (w, h, 0.04), uv=(self.win, 0))
        for dz in (-h / 2 - 0.04, h / 2 + 0.04):  # Rahmen oben/unten
            self.obox(mat_frame, c + Z * dz + n * 0.05, t, Z, (w + 0.16, 0.08, 0.1))
        for du in (-w / 2 - 0.04, w / 2 + 0.04):
            self.obox(mat_frame, c + t * du + n * 0.05, t, Z, (0.08, h, 0.1))
        if h > 1.0:  # Fensterkreuz
            self.obox(mat_frame, c + n * 0.05, t, Z, (0.05, h, 0.06))
        if sill:
            self.obox("Stone", c - Z * (h / 2 + 0.1) + n * 0.1, t, Z, (w + 0.25, 0.06, 0.2))
        if shutters:
            for s in (-1, 1):
                self.obox("Shutter", c + t * s * (w * 0.75 + 0.12) + n * 0.06, t, Z, (w / 2, h + 0.05, 0.05))

    def door(self, p0, t, n, u, w=1.1, h=2.2):
        if not self.detail:
            return
        c = Vector(p0) + t * u + Z * (h / 2 + 0.1)
        self.obox("Door", c + n * 0.03, t, Z, (w, h, 0.06))
        self.obox("Frame", c + Z * (h / 2 + 0.05) + n * 0.06, t, Z, (w + 0.2, 0.1, 0.1))
        self.obox("Roof", c + Z * (h / 2 + 0.35) + n * 0.5, t, Z, (w + 0.8, 0.12, 1.0))  # Vordach
        self.obox("Stone", Vector(p0) + t * u + Z * 0.05 + n * 0.5, t, Z, (w + 0.6, 0.12, 1.0))  # Stufe

    def facade(self, L, D, floors, fh, spacing=2.7, w=1.2, h=1.35, door_side="front", shutters=False, sides=True, skip=()):
        """Fensterreihen auf allen Seiten, Tür vorne in der Mitte. Gibt die Fensterpositionen pro Seite zurück."""
        walls = {"front": (Vector((0, -D / 2, 0)), X, -Y, L), "back": (Vector((0, D / 2, 0)), -X, Y, L),
                 "left": (Vector((-L / 2, 0, 0)), -Y, -X, D), "right": (Vector((L / 2, 0, 0)), Y, X, D)}
        used = {}
        for side, (p0, t, n, length) in walls.items():
            if side in skip or (not sides and side in ("left", "right")):
                continue
            k = max(1, int((length - 1.0) / spacing))
            us = [(-length / 2 + (i + 0.5) * length / k) for i in range(k)]
            used[side] = us
            for f in range(floors):
                for i, u in enumerate(us):
                    if f == 0 and side == door_side and i == k // 2:
                        self.door(p0, t, n, u)
                        continue
                    self.window(p0, t, n, u, f * fh + 0.9, w, h, shutters)
        return used

    def fachwerk(self, L, D, floors, fh, used):
        """Fachwerk: Schwellen/Rähme pro Geschoss, Ständer zwischen den Fenstern, Streben an den Ecken."""
        if not self.detail:
            return
        walls = {"front": (Vector((0, -D / 2, 0)), X, -Y, L), "back": (Vector((0, D / 2, 0)), -X, Y, L),
                 "left": (Vector((-L / 2, 0, 0)), -Y, -X, D), "right": (Vector((L / 2, 0, 0)), Y, X, D)}
        for side, (p0, t, n, length) in walls.items():
            p0 = p0 + n * 0.04
            for f in range(floors + 1):
                self.obox("Timber", p0 + Z * (f * fh + 0.1), t, Z, (length, 0.2, 0.08))
            posts = [-length / 2 + 0.1, length / 2 - 0.1]
            for u in used.get(side, []):
                posts += [u - 0.75, u + 0.75]
            for f in range(floors):
                z0 = f * fh + 0.2
                for u in posts:
                    self.obox("Timber", p0 + t * u + Z * (z0 + fh / 2 - 0.1), t, Z, (0.18, fh - 0.2, 0.08))
                for s in (-1, 1):  # Streben an den Ecken
                    a = Vector((-s * 1.1, 0, fh - 0.4))
                    start = p0 + t * (s * (length / 2 - 0.2)) + Z * z0
                    d = t * a.x + Z * a.z
                    self.obox("Timber", start + d / 2, d, n.cross(d), (d.length, 0.16, 0.08))

    def chimney(self, x, y, z0, z1):
        if self.detail:
            self.box("Stone", x - 0.35, x + 0.35, y - 0.35, y + 0.35, z0, z1)
            self.box("Plinth", x - 0.42, x + 0.42, y - 0.42, y + 0.42, z1, z1 + 0.12)

    def walls(self, L, D, z1, mat="Wall", plinth=0.5):
        self.box(mat, -L / 2, L / 2, -D / 2, D / 2, 0, z1)
        self.box("Plinth", -L / 2 - 0.05, L / 2 + 0.05, -D / 2 - 0.05, D / 2 + 0.05, -3.0, plinth)  # Sockel bis ins Gelände


# --- Haustypen ----------------------------------------------------------------------------------------------------

def efh_sattel(h):
    L, D, fh = 11, 9, 2.8
    h.walls(L, D, 2 * fh + 0.3)
    h.facade(L, D, 2, fh, shutters=True)
    z1 = h.gable_roof(L, D, 2 * fh + 0.3, 40)
    h.chimney(L / 4, D / 5, z1 - 2, z1 + 0.8)


def efh_walm(h):
    L, D, fh = 10.5, 10, 2.8
    h.walls(L, D, 2 * fh + 0.3)
    h.facade(L, D, 2, fh)
    z1 = h.hip_roof(L, D, 2 * fh + 0.3, 30)
    h.chimney(-L / 5, D / 6, z1 - 1.5, z1 + 0.6)


def siedlung(h):
    """Siedlungshaus: 1 Vollgeschoss, steiles Dach mit zwei Schleppgauben."""
    L, D, fh = 12, 8.5, 2.9
    h.walls(L, D, fh + 0.4)
    h.facade(L, D, 1, fh)
    for u in (-0.2, 0.2):  # Giebelfenster im Dachgeschoss
        h.window(Vector((-L / 2, 0, 0)), -Y, -X, u * D, fh + 1.0, 0.9, 1.1)
        h.window(Vector((L / 2, 0, 0)), Y, X, u * D, fh + 1.0, 0.9, 1.1)
    z0, pitch = fh + 0.4, 48
    z1 = h.gable_roof(L, D, z0, pitch)
    tan = math.tan(math.radians(pitch))
    if h.detail:
        for xc in (-L / 4, L / 4):
            yf = -D / 2 + 1.5
            zf = z0 + 1.5 * tan
            w = 2.4
            h.box("Wall", xc - w / 2, xc + w / 2, yf, -0.6, zf - 0.6, zf + 1.4)
            h.prism("Roof", [(xc - w / 2 - 0.25, yf - 0.3, zf + 1.4), (xc + w / 2 + 0.25, yf - 0.3, zf + 1.4), (xc + w / 2 + 0.25, -0.4, zf + 1.75),
                             (xc - w / 2 - 0.25, -0.4, zf + 1.75)], Z * 0.15)
            h.window(Vector((xc, yf, 0)), X, -Y, 0, zf - 0.2, 1.4, 1.1, sill=False)
    h.chimney(L / 3, D / 5, z1 - 2, z1 + 0.8)


def fachwerk(h):
    L, D, fh = 11, 8, 2.7
    h.walls(L, D, 2 * fh + 0.3, plinth=0.7)
    used = h.facade(L, D, 2, fh, spacing=2.4, w=0.9, h=1.2)
    h.fachwerk(L, D, 2, fh, used)
    z1 = h.gable_roof(L, D, 2 * fh + 0.3, 52, over=0.35)
    h.chimney(-L / 4, -D / 6, z1 - 2, z1 + 0.7)


def mfh(h):
    """Mehrfamilienhaus, 3 Geschosse, Balkone hinten."""
    L, D, fh = 18, 11, 2.9
    h.walls(L, D, 3 * fh + 0.3)
    h.facade(L, D, 3, fh, spacing=2.5, skip=("back",))
    if h.detail:
        for f in range(3):
            for u in (-L / 3, 0, L / 3):
                z = f * fh + 0.3
                h.obox("Glass", (u, D / 2 + 0.02, z + 1.15), X, Z, (1.0, 2.1, 0.04), uv=(h.win + 1, 0))
                h.win += 1
                h.window(Vector((0, D / 2, 0)), -X, Y, -u + 1.6, z + 0.6, 1.2, 1.4)
                if f > 0:
                    h.box("Plinth", u - 1.8, u + 1.8, D / 2, D / 2 + 1.5, z - 0.15, z)
                    h.box("Metal", u - 1.8, u + 1.8, D / 2 + 1.45, D / 2 + 1.5, z, z + 1.0)
                    for s in (-1, 1):
                        h.box("Metal", u + s * 1.8 - 0.03, u + s * 1.8 + 0.03, D / 2, D / 2 + 1.5, z, z + 1.0)
    z1 = h.gable_roof(L, D, 3 * fh + 0.3, 35)
    for x in (-L / 4, L / 4):
        h.chimney(x, 0.8, z1 - 1.5, z1 + 0.8)


def stadthaus(h):
    """Schmales Stadthaus, Giebel zur Straße (First entlang der langen Achse)."""
    L, D, fh = 12, 7, 3.0
    h.walls(L, D, 3 * fh + 0.3)
    h.facade(L, D, 3, fh, spacing=2.3, w=1.0, h=1.5, door_side="left")
    for u in (-1.2, 1.2):
        h.window(Vector((-L / 2, 0, 0)), -Y, -X, u, 3 * fh + 0.8, 0.9, 1.2)
    h.gable_roof(L, D, 3 * fh + 0.3, 50, over=0.3)


def block(h):
    """Wohnblock, 5 Geschosse, Flachdach, Balkonreihen."""
    L, D, fh = 30, 12, 2.8
    h.walls(L, D, 5 * fh + 0.3)
    h.facade(L, D, 5, fh, spacing=3.0, skip=("back",))
    if h.detail:
        for f in range(5):
            z = f * fh + 0.3
            for i in range(5):
                u = -L / 2 + 3 + i * 6
                h.window(Vector((0, D / 2, 0)), -X, Y, -u, z + 0.6, 1.4, 1.4)
                if f > 0:
                    h.box("Plinth", u - 2.0, u + 2.0, D / 2, D / 2 + 1.6, z - 0.15, z)
                    h.box("Wall", u - 2.0, u + 2.0, D / 2 + 1.5, D / 2 + 1.6, z, z + 1.0)
    h.flat_roof(L, D, 5 * fh + 0.3)


def halle(h):
    """Gewerbehalle: Blechwände, flaches Satteldach, Fensterband, Rolltore."""
    L, D = 40, 24
    h.walls(L, D, 7.5, mat="Metal", plinth=0.3)
    if h.detail:
        for side, p0, t, n, length in (("front", Vector((0, -D / 2, 0)), X, -Y, L), ("back", Vector((0, D / 2, 0)), -X, Y, L)):
            for i in range(int(length / 4)):
                h.window(p0, t, n, -length / 2 + 2 + i * 4, 5.2, 3.2, 1.2, sill=False, mat_frame="Metal")
            for u in (-length / 4, length / 4):
                h.obox("Door", p0 + t * u + Z * 2.5 + n * 0.03, t, Z, (5, 5, 0.06))
    h.gable_roof(L, D, 7.5, 8, over=0.3, mat="Metal", wall="Metal")


def garage(h):
    L, D = 6, 3.5
    h.walls(L, D, 2.6, plinth=0.15)
    if h.detail:
        h.obox("Metal", (0, -D / 2 - 0.03, 1.1), X, Z, (2.5, 2.1, 0.06))
    h.box("Flat", -L / 2 - 0.15, L / 2 + 0.15, -D / 2 - 0.15, D / 2 + 0.15, 2.6, 2.8)


def scheune(h):
    L, D = 20, 12
    h.walls(L, D, 4.5, mat="Wood", plinth=0.4)
    if h.detail:
        h.obox("Door", (0, -D / 2 - 0.03, 2.0), X, Z, (4.0, 4.0, 0.06))
        for i in range(-3, 4):  # Bretter
            h.box("Timber", i * 2.8 - 0.08, i * 2.8 + 0.08, -D / 2 - 0.05, -D / 2, 0.4, 4.5)
            h.box("Timber", i * 2.8 - 0.08, i * 2.8 + 0.08, D / 2, D / 2 + 0.05, 0.4, 4.5)
    h.gable_roof(L, D, 4.5, 45, wall="Wood")


def kirche(h):
    """Dorfkirche: Schiff mit steilem Dach, Turm mit Spitzhelm am Westende."""
    L, D = 24, 12
    h.walls(L, D, 9, mat="Wall")
    for side, p0, t, n, length in (("front", Vector((0, -D / 2, 0)), X, -Y, L), ("back", Vector((0, D / 2, 0)), -X, Y, L)):
        for i in range(4):
            h.window(p0, t, n, -length / 2 + 4 + i * 5, 2.5, 1.4, 4.5, sill=False, mat_frame="Stone")
    h.gable_roof(L, D, 9, 52, over=0.4)
    tx = -L / 2 - 2.5
    h.box("Wall", tx - 3, tx + 3, -3, 3, -3, 26)
    h.box("Stone", tx - 3.1, tx + 3.1, -3.1, 3.1, 25.5, 26.3)
    h.pyramid("Metal", tx, 0, 26.3, 6.2, 14)
    h.door(Vector((tx - 3, 0, 0)), -Y, -X, 0, 1.6, 3.0)
    for p0, t, n in ((Vector((tx, -3, 0)), X, -Y), (Vector((tx, 3, 0)), -X, Y), (Vector((tx - 3, 0, 0)), -Y, -X)):
        h.window(p0, t, n, 0, 21, 1.2, 2.5, sill=False, mat_frame="Stone")


# --- Regionale Haustypen ------------------------------------------------------------------------------------------

def wand(L, D, side):
    """Wandebene (Punkt, Tangente, Normale) einer Seite, wie in House.facade."""
    return {"front": (Vector((0, -D / 2, 0)), X, -Y), "back": (Vector((0, D / 2, 0)), -X, Y),
            "left": (Vector((-L / 2, 0, 0)), -Y, -X), "right": (Vector((L / 2, 0, 0)), Y, X)}[side]


def fenster(h, L, D, side, us, z, w=1.2, hh=1.35, **kw):
    """Fensterreihe an einer Seite, u wie in House.window (entlang der Wandtangente)."""
    p0, t, n = wand(L, D, side)
    for u in us:
        h.window(p0, t, n, u, z, w, hh, **kw)


def tuer(h, p0, t, n, u=0.0, w=1.0, hh=2.1, z=0.0, mat="Door"):
    """Tür ohne Vordach und Stufe (unter Veranden, an Hütten); mat="Metal" für Garagentore."""
    if not h.detail:
        return
    c = Vector(p0) + t * u + Z * (z + hh / 2)
    h.obox(mat, c + n * 0.03, t, Z, (w, hh, 0.06))
    h.obox("Frame", c + Z * (hh / 2 + 0.05) + n * 0.06, t, Z, (w + 0.2, 0.1, 0.1))


def gelaender(h, p, q, z, mat, hh=1.0, staebe=False):
    """Brüstung von p nach q (Boden auf Höhe z): Handlauf plus Brett bzw. Stäbe."""
    p, q = Vector(p), Vector(q)
    d = q - p
    c = (p + q) / 2 + Z * z
    h.obox(mat, c + Z * (hh - 0.04), d, Z, (d.length + 0.08, 0.08, 0.08))
    if not staebe:
        h.obox(mat, c + Z * (hh / 2 + 0.05), d, Z, (d.length, hh - 0.3, 0.05))
        return
    k = max(2, int(d.length / 0.5) + 1)
    for i in range(k):
        h.obox(mat, p + d * (i / (k - 1)) + Z * (z + hh / 2), d, Z, (0.04, hh, 0.04))


def veranda(h, x0, x1, y0, depth, zf, zr, posts="Frame", deck="Wood", roof=True):
    """Veranda vor der Wand y=y0 (nach -Y): Boden (Oberkante zf), Pfosten, flaches Pultdach mit Oberkante zr an der Wand.
    Ohne Dach (roof=False) reichen die Pfosten bis zr (z. B. unter den Hausdach-Überstand)."""
    if not h.detail:
        return
    y1, s = y0 - depth, 0.1  # Dachgefälle
    h.box(deck, x0, x1, y1, y0, zf - 0.2 if zf > 0.6 else -0.5, zf)
    yp = y1 + 0.15
    top = zr
    if roof:
        ye = y1 - 0.3
        h.prism("Roof", [(x0 - 0.3, ye, zr - s * (y0 - ye)), (x1 + 0.3, ye, zr - s * (y0 - ye)), (x1 + 0.3, y0, zr), (x0 - 0.3, y0, zr)], -Z * 0.12)
        top = zr - s * (y0 - yp) - 0.12
    k = max(2, round((x1 - x0) / 2.5) + 1)
    for i in range(k):
        x = x0 + 0.15 + i * (x1 - x0 - 0.3) / (k - 1)
        h.box(posts, x - 0.07, x + 0.07, yp - 0.07, yp + 0.07, zf, top)


def versetzt(h, dx, build):
    """Baut mit build() und verschiebt alle neuen Punkte um dx entlang X (die Dachfunktionen bauen mittig)."""
    o = len(h.v)
    r = build()
    for v in h.v[o:]:
        v.x += dx
    return r


def chalet(h):
    """Alpenchalet: gemauertes Erdgeschoss, Holz-Obergeschoss, flaches Satteldach mit weitem Überstand, Balkon vorne."""
    L, D, fh = 12, 10, 2.8
    zt = 2 * fh + 0.3
    h.walls(L, D, fh)
    h.box("Wood", -L / 2, L / 2, -D / 2, D / 2, fh, zt)
    h.facade(L, D, 2, fh, w=1.0, h=1.2, shutters=True)
    z1 = h.gable_roof(L, D, zt, 25, over=1.5, wall="Wood")
    if h.detail:
        yb = -D / 2 - 1.4
        h.box("Wood", -L / 2, L / 2, yb, -D / 2, fh - 0.05, fh + 0.15)
        gelaender(h, (-L / 2, yb + 0.05, 0), (L / 2, yb + 0.05, 0), fh + 0.15, "Wood")
        for x in (-L / 2 + 0.05, L / 2 - 0.05):
            gelaender(h, (x, yb, 0), (x, -D / 2, 0), fh + 0.15, "Wood")
    h.chimney(L / 4, D / 5, z1 - 1.5, z1 + 0.6)


def holzhaus(h):
    """Skandinavisches/russisches Holzhaus: Wand in Hausfarbe (Falunrot/Gelb/Weiß), weiße Eckbretter und Rahmen, Giebelfenster."""
    L, D, fh = 10, 7, 2.7
    zt = fh + 0.3
    h.walls(L, D, zt, plinth=0.4)
    h.facade(L, D, 1, fh, spacing=2.4, w=1.0, h=1.2)
    for side in ("left", "right"):  # Dachboden
        fenster(h, L, D, side, (0,), zt + 0.5, 0.9, 1.0, sill=False)
    z1 = h.gable_roof(L, D, zt, 40)
    if h.detail:
        for sx in (-1, 1):  # Eckbretter
            for sy in (-1, 1):
                x, y = sx * L / 2, sy * D / 2
                h.box("Frame", x - 0.12, x + 0.12, y - 0.12, y + 0.12, 0.4, zt)
    h.chimney(L / 5, 0.6, z1 - 1.5, z1 + 0.7)


def mediterran(h):
    """Südeuropa: flaches Walmdach mit kleinem Überstand, Fensterläden, kleiner Balkon über der Tür."""
    L, D, fh = 11, 9, 2.9
    zt = 2 * fh + 0.3
    h.walls(L, D, zt, plinth=0.3)
    h.facade(L, D, 2, fh, w=1.0, h=1.4, shutters=True)
    h.hip_roof(L, D, zt, 22, over=0.3)
    if h.detail:
        yb, w = -D / 2 - 1.0, 1.3
        h.box("Stone", -w, w, yb, -D / 2, fh - 0.05, fh + 0.1)
        gelaender(h, (-w, yb + 0.03, 0), (w, yb + 0.03, 0), fh + 0.1, "Metal", staebe=True)
        for x in (-w + 0.03, w - 0.03):
            gelaender(h, (x, yb, 0), (x, -D / 2, 0), fh + 0.1, "Metal", staebe=True)


def flachdach(h):
    """Nordafrika/Nahost/Indien: Kubus mit Flachdach und Brüstung in Wandfarbe, kleine Fenster, Treppenhaus-Aufbau."""
    L, D, fh = 10, 10, 3.0
    zt = 2 * fh + 0.2
    h.walls(L, D, zt, plinth=0.2)
    h.facade(L, D, 2, fh, spacing=3.0, w=0.7, h=0.9)
    h.flat_roof(L, D, zt, parapet=0.9, mat="Wall")
    x0, y0 = L / 2 - 3.2, D / 2 - 2.8  # Treppenhaus auf dem Dach (auch in der Fernversion: Silhouette)
    h.box("Wall", x0, L / 2 - 0.4, y0, D / 2 - 0.4, zt + 0.25, zt + 2.6)
    h.box("Flat", x0 - 0.1, L / 2 - 0.3, y0 - 0.1, D / 2 - 0.3, zt + 2.6, zt + 2.75)
    tuer(h, (x0 + 1.4, y0, 0), X, -Y, 0, 0.9, 2.0, z=zt + 0.25)


def suburban(h):
    """Nordamerika: Satteldach 30°, eingeschossige Garage im Grundriss am rechten Ende, Veranda mit Pfosten vorne."""
    L, D, fh, G = 13, 9, 2.8, 4.0
    xm, xg = -G / 2, L / 2 - G / 2  # Mitte Wohnteil / Garage
    zt = 2 * fh + 0.3
    h.walls(L, D, fh, plinth=0.3)
    h.box("Wall", -L / 2, L / 2 - G, -D / 2, D / 2, fh, zt)
    z1 = versetzt(h, xm, lambda: h.gable_roof(L - G, D, zt, 30, over=0.4))
    versetzt(h, xg, lambda: h.gable_roof(G, D, fh, 30, over=0.3))
    fenster(h, L, D, "front", (-5, -2, 1), fh + 0.9)
    fenster(h, L, D, "front", (-5, 0.8), 0.9)
    fenster(h, L, D, "back", (5, 2, -1), fh + 0.9)
    fenster(h, L, D, "back", (5, 1.5), 0.9)
    fenster(h, L, D, "back", (-xg,), 1.2, 0.8, 0.8, sill=False)
    for z in (0.9, fh + 0.9):
        fenster(h, L, D, "left", (-2, 2), z)
    fenster(h, L, D, "right", (0,), 1.2, 0.8, 0.8, sill=False)
    tuer(h, *wand(L, D, "front"), -2.2)
    tuer(h, *wand(L, D, "front"), xg, 2.8, 2.2, mat="Metal")  # Garagentor
    veranda(h, -4.0, 1.6, -D / 2, 2.0, 0.35, fh)
    h.chimney(-L / 2 + 1.0, 0.8, z1 - 1.5, z1 + 0.6)


def ranch(h):
    """Bungalow (Nordamerika/Australien): eingeschossig, flaches Walmdach, Veranda auf dünnen Pfosten über die Front."""
    L, D, zt, over = 16, 9, 3.0, 0.6
    h.walls(L, D, zt, plinth=0.3)
    h.facade(L, D, 1, 2.7, spacing=3.0, door_side="back", skip=("front",))
    fenster(h, L, D, "front", (-6.4, -3.2, 3.2, 6.4), 0.9)
    tuer(h, *wand(L, D, "front"), 0.0)
    h.hip_roof(L, D, zt, 22, over=over)
    eave = zt - over * math.tan(math.radians(22)) - 0.22  # Unterkante der Traufe; das Verandadach muss darunter bleiben
    veranda(h, -L / 2 + 0.5, L / 2 - 0.5, -D / 2, 2.4, 0.2, eave + 0.1 * over - 0.03)


def tropenhaus(h):
    """Südostasien/Ozeanien: auf Pfählen (1,2 m), Walmdach 32° mit weitem Überstand, Veranda mit Treppe vorne."""
    L, D, fh, zf, over, pitch, t = 10, 8, 2.7, 1.2, 1.2, 32, 0.22
    zt = zf + fh
    for x in (-L / 2 + 0.2, -L / 6, L / 6, L / 2 - 0.2):  # Pfähle bis ins Gelände (statt Sockel)
        for y in (-D / 2 + 0.2, 0, D / 2 - 0.2):
            h.box("Timber", x - 0.13, x + 0.13, y - 0.13, y + 0.13, -3.0, zf - 0.25)
    h.box("Wood", -L / 2 - 0.1, L / 2 + 0.1, -D / 2 - 0.1, D / 2 + 0.1, zf - 0.25, zf)
    h.box("Wall", -L / 2, L / 2, -D / 2, D / 2, zf, zt)
    fenster(h, L, D, "front", (-3.3, 3.3), zf + 0.9, 1.1, 1.2, shutters=True, sill=False)
    fenster(h, L, D, "back", (-3, 0, 3), zf + 0.9, 1.1, 1.2, shutters=True, sill=False)
    for side in ("left", "right"):
        fenster(h, L, D, side, (-1.8, 1.8), zf + 0.9, 1.1, 1.2, shutters=True, sill=False)
    tuer(h, *wand(L, D, "front"), 0.0, z=zf)
    z1 = h.hip_roof(L, D, zt, pitch, over=over, t=t)
    if h.detail:
        tan = math.tan(math.radians(pitch))
        hy, ze = D / 2 + over, zt - over * tan
        y1 = -D / 2 - 1.2
        yp = y1 + 0.15  # Pfosten bis an die Unterseite des Dachüberstands
        top = ze + (yp + hy) / hy * (z1 + 0.6 * t - ze) - t
        veranda(h, -3, 3, -D / 2, 1.2, zf, top, posts="Timber", roof=False)
        for x in (-2.9, 2.9):
            h.box("Timber", x - 0.1, x + 0.1, y1 + 0.05, y1 + 0.25, -3.0, zf - 0.2)
        gelaender(h, (-3, y1 + 0.03, 0), (-0.8, y1 + 0.03, 0), zf, "Wood", hh=0.9)
        gelaender(h, (0.8, y1 + 0.03, 0), (3, y1 + 0.03, 0), zf, "Wood", hh=0.9)
        for i in (1, 2, 3):  # Treppe
            h.box("Wood", -0.8, 0.8, y1 - i * 0.3, y1 - (i - 1) * 0.3, -1.0, zf - i * 0.3)


def wellblech(h):
    """Ländliches Afrika südlich der Sahara: schlichtes niedriges Haus, flaches Blechsatteldach (Roof, in Godot blechgrau)."""
    L, D, fh = 8, 6, 2.5
    h.walls(L, D, fh, plinth=0.25)
    fenster(h, L, D, "front", (-2.4, 2.4), 0.9, 0.8, 0.8, sill=False, mat_frame="Wood")
    fenster(h, L, D, "back", (0,), 0.9, 0.8, 0.8, sill=False, mat_frame="Wood")
    tuer(h, *wand(L, D, "front"), 0.0, 0.9, 2.0)
    h.gable_roof(L, D, fh, 12, over=0.4, t=0.08)


def rundhuette(h):
    """Rundhütte: zwölfeckige Lehmwand (Wall), Kegeldach aus Stroh (Thatch) mit tief gezogenem Überstand."""
    r, zt, over, pitch = 3.0, 2.2, 0.6, 40
    tan = math.tan(math.radians(pitch))
    ze = zt - over * tan

    def ring(rr, z):  # Ecken bei 15° + k·30°, damit eine Wandfläche genau nach -Y (vorne) zeigt
        return [(rr * math.cos(math.radians(15 + 30 * k)), rr * math.sin(math.radians(15 + 30 * k)), z) for k in range(12)]

    h.prism("Plinth", ring(r + 0.05, -3.0), Z * 3.25)
    h.prism("Wall", ring(r, 0.0), Z * zt)
    # Dach: Kegelmantel, flacher geneigte Untersicht bis in die Wand, Deckel innen
    o = len(h.v)
    h.v += [Vector(p) for p in ring(r + over, ze) + ring(r - 0.2, ze + 0.3)] + [Vector((0, 0, zt + r * tan))]
    faces = [[o + k, o + (k + 1) % 12, o + 24] for k in range(12)]
    faces += [[o + (k + 1) % 12, o + k, o + 12 + k, o + 12 + (k + 1) % 12] for k in range(12)]
    faces.append([o + 12 + k for k in range(12)])
    for f in faces:
        h.f.append(f)
        h.m.append("Thatch")
        h.uv.append([(0, 0)] * len(f))
    a = r * math.cos(math.radians(15))  # Abstand Mitte -> Wandfläche
    tuer(h, (0, -a, 0), X, -Y, 0, 0.9, 1.75)
    h.window(Vector((a, 0, 0)), Y, X, 0, 0.9, 0.5, 0.5, sill=False, mat_frame="Wood")
    h.window(Vector((-a, 0, 0)), -Y, -X, 0, 0.9, 0.5, 0.5, sill=False, mat_frame="Wood")


def ostasien(h):
    """China/Japan/Korea: Walmdach 25° mit weitem Überstand, umlaufendes Vordach-Band zwischen den Geschossen."""
    L, D, fh = 12, 9, 2.9
    zt = 2 * fh + 0.2
    h.walls(L, D, zt, plinth=0.4)
    h.facade(L, D, 2, fh, spacing=2.4, w=1.3, h=1.2)
    h.hip_roof(L, D, zt, 25, over=0.9)
    if h.detail:
        a = math.radians(20)
        for side, length in (("front", L), ("back", L), ("left", D), ("right", D)):
            p0, t, n = wand(L, D, side)
            b = n * math.cos(a) - Z * math.sin(a)  # Hangrichtung des Bandes
            h.obox("Roof", p0 + n * 0.38 + Z * (fh + 0.15), t, b, (length + 1.5, 0.8, 0.1))


def beton(h):
    """Städtisch (Lateinamerika/Südasien/Afrika): Betonbau, Flachdach mit niedriger Brüstung, Bewehrungseisen, Balkon vorne."""
    L, D, fh = 10, 8, 2.9
    zt = 2 * fh + 0.2
    h.walls(L, D, zt, plinth=0.2)
    h.facade(L, D, 2, fh, spacing=2.6, w=1.1, h=1.2)
    h.flat_roof(L, D, zt, parapet=0.35)
    if h.detail:
        h.box("Plinth", -L / 2 - 0.06, L / 2 + 0.06, -D / 2 - 0.06, D / 2 + 0.06, fh, fh + 0.2)  # Deckenrand
        yb, w = -D / 2 - 1.1, 1.3
        h.box("Plinth", -w, w, yb, -D / 2, fh, fh + 0.18)
        gelaender(h, (-w, yb + 0.03, 0), (w, yb + 0.03, 0), fh + 0.18, "Metal", staebe=True)
        for x in (-w + 0.03, w - 0.03):
            gelaender(h, (x, yb, 0), (x, -D / 2, 0), fh + 0.18, "Metal", staebe=True)
        for x in (-L / 2 + 0.2, 0, L / 2 - 0.2):  # Bewehrung über den Stützen (für das nächste Geschoss)
            for y in (-D / 2 + 0.2, 0, D / 2 - 0.2):
                for dx, dy in ((-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)):
                    h.box("Metal", x + dx - 0.02, x + dx + 0.02, y + dy - 0.02, y + dy + 0.02, zt + 0.25, zt + 1.1)


TYPES = [efh_sattel, efh_walm, siedlung, fachwerk, mfh, stadthaus, block, halle, garage, scheune, kirche,
         chalet, holzhaus, mediterran, flachdach, suburban, ranch, beton, tropenhaus, wellblech, rundhuette, ostasien]


def materials():
    out = {}
    for name, (rgb, rough) in MATS.items():
        m = bpy.data.materials.new(name)
        bsdf = m.node_tree.nodes["Principled BSDF"] if m.node_tree else None
        if bsdf is None:
            m.use_nodes = True
            bsdf = m.node_tree.nodes["Principled BSDF"]
        lin = [c ** 2.2 for c in rgb]
        bsdf.inputs["Base Color"].default_value = (*lin, 1)
        bsdf.inputs["Roughness"].default_value = rough
        m.diffuse_color = (*lin, 1)
        out[name] = m
    return out


def to_object(name, h, mats):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in h.v], [], h.f)
    names = sorted(set(h.m))
    for n in names:
        me.materials.append(mats[n])
    for poly, mat in zip(me.polygons, h.m):
        poly.material_index = names.index(mat)
    uv = me.uv_layers.new(name="UVMap")
    for poly, uvs in zip(me.polygons, h.uv):
        for li, val in zip(poly.loop_indices, uvs):
            uv.data[li].uv = val
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out, preview = args[0], (args[1] if len(args) > 1 else None)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mats = materials()
    objs = []
    for build in TYPES:
        for detail in (True, False):
            h = House(detail)
            build(h)
            objs.append(to_object(build.__name__ + ("" if detail else "_lod"), h, mats))
            print(f"{objs[-1].name}: {len(h.f)} Flächen")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True, export_apply=True)
    if preview:
        render_preview([o for o in objs if not o.name.endswith("_lod")], preview)


def render_preview(objs, path):
    """6 Spalten, schräg von vorne, damit man Fassaden und Dächer sieht."""
    for i, o in enumerate(objs):
        o.location = ((i % 6) * 42, (i // 6) * 42, 3.0)
    for o in bpy.data.objects:
        o.hide_render = o.name.endswith("_lod")
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (105, -125, 150)
    cam.rotation_euler = (math.radians(50), 0, 0)
    cam.data.lens = 29
    scene.camera = cam
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 2400, 1600
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


main()
