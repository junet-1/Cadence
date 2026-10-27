"""Rennrad + Fahrer für Cadence (rider.gd lädt models/rider.glb).

  blender -b -P blender/rider.py -- models/rider.glb [vorschau.png]

Konvention: Blender X rechts, Y vorn (Godot −Z), Z oben; Räder auf Z = 0, Rad mittig bei x = 0. Jedes Teil ist ein eigenes
Objekt mit dem Ursprung im Drehpunkt (Mesh-Daten lokal), rider.gd setzt die Lage selbst:
  frame    Rahmen, Gabel, Lenker, Sattel, Kette, Kassette (statisch, Ursprung am Boden)
  wheel    Laufrad (Felge, Reifen, Speichen), Ursprung Nabe, dreht um X; wheel_f/wheel_r teilen das Mesh
  crank    Kurbeln + Kettenblatt, Ursprung Tretlager; rechter Arm zeigt bei Drehung 0 nach oben
  body     Rumpf, Arme, Kopf, Helm (statisch)
  thigh    Oberschenkel, Ursprung Hüftgelenk, hängt nach −Z (Länge LT), Vorderseite +Y
  shin     Unterschenkel, Ursprung Knie, nach −Z (Länge LS)
  foot     Schuh + Pedal, Ursprung Pedalachse; Knöchel bei ANKLE
Maße (BB, HIP, CRANK, LT, LS, ANKLE, Naben, PEDAL_X) stehen genauso in rider.gd – beide zusammen ändern.
Farben als Vertexfarben (linear); Alpha 1 = Trikotfarbe: rider.gd multipliziert RGB mit der Kit-Farbe (Spieler orange,
Mitfahrer blau). Glatt schattiert, Kanten über 50° hart (Rohrenden, Pedal).
"""
import math
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

# --- Maße (Meter) ---
WHEEL_R = 0.335  # Reifen außen, Ø 0,67 m
HUB_R = Vector((0, -0.405, WHEEL_R))
HUB_F = Vector((0, 0.595, WHEEL_R))  # Radstand 1,0 m
BB = Vector((0, 0, 0.265))
CRANK = 0.1725
PEDAL_X = 0.115  # Fußmitte seitlich
HIP = Vector((0.085, -0.165, 1.035))  # rechtes Hüftgelenk (links gespiegelt); Sattel oben bei 0,955
LT, LS = 0.445, 0.445  # Ober-/Unterschenkel (Fahrer 1,80 m)
ANKLE = Vector((0, -0.07, 0.085))  # Knöchel relativ zur Pedalachse im Fuß-Raum


def lin(c, kit=False):
    return (c[0] ** 2.2, c[1] ** 2.2, c[2] ** 2.2, 1.0 if kit else 0.0)


KIT = (1.0, 1.0, 1.0, 1.0)  # reine Trikotfarbe
KIT_DARK = (0.55, 0.55, 0.55, 1.0)  # Trikotfarbe, dunkler (Schulterpasse, Helmband)
SKIN = lin((.80, .60, .48))
BIB = lin((.06, .06, .07))
FRAME = lin((.13, .13, .15))
TYRE = lin((.08, .08, .08))
RIM = lin((.16, .16, .17))
SILVER = lin((.62, .63, .66))
WHITE = lin((.92, .92, .92))
GLOVE = lin((.08, .08, .09))
SHOE = lin((.10, .10, .11))
TAPE = lin((.06, .06, .06))
GLASS = lin((.04, .04, .05))


class M:
    def __init__(self):
        self.v, self.f, self.c = [], [], []

    def _ring(self, c, u, w, rx, ry, col, sides):
        o = len(self.v)
        for k in range(sides):
            a = 2 * math.pi * k / sides
            self.v.append(c + u * (rx * math.cos(a)) + w * (ry * math.sin(a)))
            self.c.append(col)
        return o

    def _bridge(self, a, b, sides):
        for k in range(sides):
            j = (k + 1) % sides
            self.f.append((a + k, a + j, b + j, b + k))

    def _cap(self, r, sides, pole=None, col=None):
        if pole is None:  # flach
            self.f.append(tuple(range(r, r + sides)))
            return
        o = len(self.v)
        self.v.append(pole)
        self.c.append(col)
        for k in range(sides):
            self.f.append((r + k, r + (k + 1) % sides, o))

    def loft(self, pts, rads, cols, sides=10, side=(1, 0, 0), caps=(True, True)):
        """Röhre durch pts; rads[i] = r oder (rx, ry) (rx entlang side), cols[i] je Ring; caps: Kuppel (True) oder flach."""
        pts = [Vector(p) for p in pts]
        rads = [r if isinstance(r, tuple) else (r, r) for r in rads]
        side = Vector(side)
        n = len(pts)
        rings = []
        for i in range(n):
            t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
            u = side - t * side.dot(t)
            u = (u if u.length > 1e-6 else t.orthogonal()).normalized()
            rings.append((pts[i], t, u, t.cross(u), rads[i], cols[i]))
        seq = []
        for end, dome in ((0, caps[0]), (n - 1, caps[1])):
            if not dome:
                continue
            c, t, u, w, (rx, ry), col = rings[end]
            s = -1 if end == 0 else 1
            depth = (rx + ry) / 2
            extra = [(c + t * (s * depth * math.sin(math.radians(p))), t, u, w, (rx * math.cos(math.radians(p)), ry * math.cos(math.radians(p))), col)
                     for p in (35, 65)]
            seq.append((end, extra, c + t * s * depth))
        all_rings = list(rings)
        pole0 = pole1 = None
        for end, extra, pole in seq:
            if end == 0:
                all_rings = extra[::-1] + all_rings
                pole0 = pole
            else:
                all_rings = all_rings + extra
                pole1 = pole
        idx = [self._ring(c, u, w, rx, ry, col, sides) for c, t, u, w, (rx, ry), col in all_rings]
        for a, b in zip(idx, idx[1:]):
            self._bridge(a, b, sides)
        self._cap(idx[0], sides, pole0, all_rings[0][5])
        self._cap(idx[-1], sides, pole1, all_rings[-1][5])

    def tube(self, a, b, r, col, sides=8, r1=None):
        self.loft([a, b], [r, r if r1 is None else r1], [col, col], sides, caps=(False, False))

    def ball(self, c, radii, col, seg=12, rings=7, lat0=-90.0, colfn=None):
        """Ellipsoid (UV-Kugel), unten bei lat0 abgeschnitten und flach geschlossen; colfn(lat) -> Farbe je Breitenring."""
        c = Vector(c)
        lats = [lat0 + (90 - lat0) * i / rings for i in range(rings)]
        idx = []
        for lat in lats:
            la = math.radians(lat)
            col = colfn(lat) if colfn else col
            o = len(self.v)
            for k in range(seg):
                a = 2 * math.pi * k / seg
                self.v.append(c + Vector((radii[0] * math.cos(la) * math.sin(a), radii[1] * math.cos(la) * math.cos(a), radii[2] * math.sin(la))))
                self.c.append(col)
            idx.append(o)
        for a, b in zip(idx, idx[1:]):
            self._bridge(a, b, seg)
        if lat0 <= -89.9:
            self._cap(idx[0], seg, c - Vector((0, 0, radii[2])), self.c[idx[0]])
        else:
            self.f.append(tuple(range(idx[0], idx[0] + seg))[::-1])
        self._cap(idx[-1], seg, c + Vector((0, 0, radii[2])), colfn(90) if colfn else col)

    def lathe(self, c, prof, col, seg=32):
        """Rotationskörper um die X-Achse: prof = geschlossene Kontur [(x, r)]."""
        c = Vector(c)
        o = len(self.v)
        for k in range(seg):
            a = 2 * math.pi * k / seg
            for x, r in prof:
                self.v.append(c + Vector((x, r * math.cos(a), r * math.sin(a))))
                self.c.append(col)
        n = len(prof)
        for k in range(seg):
            kk = (k + 1) % seg
            for i in range(n):
                j = (i + 1) % n
                self.f.append((o + k * n + i, o + k * n + j, o + kk * n + j, o + kk * n + i))

    def box(self, c, s, col, rot=Matrix.Identity(3)):
        c = Vector(c)
        o = len(self.v)
        for i in (-1, 1):
            for j in (-1, 1):
                for k in (-1, 1):
                    self.v.append(c + rot @ Vector((i * s[0] / 2, j * s[1] / 2, k * s[2] / 2)))
                    self.c.append(col)
        for f in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            self.f.append(tuple(o + q for q in f))


def ik(top, end, l1, l2, pole):
    """Mittelgelenk (Knie/Ellbogen) zwischen top und end, Glieder l1/l2, zur Seite pole hin geknickt (wie rider.gd::_knee)."""
    d = end - top
    ln = min(max(d.length, abs(l1 - l2) + 1e-3), l1 + l2 - 1e-3)
    u = d.normalized()
    x = (l1 * l1 - l2 * l2 + ln * ln) / (2 * ln)
    y = math.sqrt(max(l1 * l1 - x * x, 0))
    p = (pole - u * pole.dot(u)).normalized()
    return top + u * x + p * y


def leg_pose(a, sx):
    """Pedal, Fußneigung, Knöchel, Knie bei Kurbelwinkel a (0 = Pedal oben, vorwärts drehend), Seite sx = ±1."""
    p = BB + Vector((sx * PEDAL_X, math.sin(a) * CRANK, math.cos(a) * CRANK))
    pitch = -math.radians(17 - 12 * math.sin(a))  # Fersenarbeit: Zehen im Druck flacher, im Zug tiefer
    ankle = p + Matrix.Rotation(pitch, 3, "X") @ ANKLE
    hip = Vector((sx * HIP.x, HIP.y, HIP.z))
    knee = ik(hip, ankle, LT, LS, Vector((sx * 0.15, 1, 0)))
    return p, pitch, ankle, hip, knee


# --- Teile ---

def frame(m):
    st = Vector((0, -math.cos(math.radians(73.5)), math.sin(math.radians(73.5))))  # Sitzrohr 73,5°
    st_top = BB + st * 0.56
    ht_top, ht_bot = Vector((0, 0.385, 0.84)), Vector((0, 0.429, 0.697))
    m.lathe(BB, [(-0.035, 0.0), (-0.035, 0.022), (0.035, 0.022), (0.035, 0.0)], FRAME, seg=10)  # Tretlager
    m.loft([BB + st * 0.02, BB + st * 0.3, st_top], [0.02, 0.016, 0.016], [FRAME] * 3, caps=(False, False))
    m.tube(st_top - st * 0.01, BB + st * 0.69, 0.0135, FRAME)  # Sattelstütze
    m.tube(Vector((0, -0.145, 0.79)), ht_top + Vector((0, -0.01, -0.025)), 0.017, FRAME, r1=0.02)  # Oberrohr
    m.tube(BB + Vector((0, 0.02, 0.0)), ht_bot + Vector((0, -0.005, 0.02)), 0.024, FRAME, r1=0.021)  # Unterrohr
    m.tube(ht_bot, ht_top, 0.022, FRAME, sides=10)  # Steuerrohr
    m.tube(ht_top, ht_top + Vector((0, -0.003, 0.03)), 0.018, FRAME)  # Spacer
    for sx in (-1, 1):
        m.tube(BB + Vector((sx * 0.025, -0.02, 0.0)), HUB_R + Vector((sx * 0.065, 0, 0)), 0.012, FRAME, r1=0.008)  # Kettenstreben
        m.tube(st_top + Vector((sx * 0.015, 0.0, -0.025)), HUB_R + Vector((sx * 0.065, 0.005, 0.012)), 0.01, FRAME, r1=0.007)  # Sitzstreben
        m.loft([ht_bot + Vector((sx * 0.028, 0.0, -0.005)), Vector((sx * 0.045, 0.53, 0.48)), HUB_F + Vector((sx * 0.05, -0.002, 0))],
               [0.016, 0.012, 0.008], [FRAME] * 3, sides=8, caps=(False, False))  # Gabel
    m.box(ht_bot + Vector((0, 0.005, -0.02)), (0.075, 0.05, 0.035), FRAME)  # Gabelkrone
    # Sattel: hinten breit, Nase schmal
    sad = [(-0.335, 0.072, 0.02), (-0.30, 0.075, 0.024), (-0.23, 0.058, 0.022), (-0.15, 0.03, 0.02), (-0.07, 0.02, 0.016), (-0.055, 0.012, 0.01)]
    m.loft([(0, y + 0.025, 0.937 + 0.006 * (y + 0.2)) for y, _, _ in sad], [(rx, ry) for _, rx, ry in sad], [lin((.05, .05, .05))] * len(sad),
           sides=10, caps=(False, False))
    m.tube(BB + st * 0.69, Vector((0, -0.19, 0.925)), 0.012, SILVER)  # Sattelklemme
    # Vorbau + Lenker (Oberlenker, Bremsgriffe, Unterlenker)
    stem0, stem1 = ht_top + Vector((0, -0.003, 0.045)), Vector((0, 0.49, 0.875))
    m.tube(stem0, stem1, 0.017, FRAME, r1=0.015)
    for sx in (-1, 1):
        bar = [(0.0, 0.49, 0.875), (0.15, 0.49, 0.875), (0.19, 0.5, 0.875), (0.205, 0.54, 0.872), (0.21, 0.578, 0.858), (0.212, 0.598, 0.82),
               (0.212, 0.588, 0.785), (0.212, 0.556, 0.765), (0.212, 0.5, 0.762)]
        m.loft([(sx * x, y, z) for x, y, z in bar], [0.0125] * len(bar), [TAPE] * len(bar), sides=6, caps=(False, False))
        m.loft([(sx * 0.208, 0.555, 0.875), (sx * 0.208, 0.585, 0.883), (sx * 0.208, 0.612, 0.89), (sx * 0.208, 0.62, 0.878)],
               [(0.016, 0.02), (0.017, 0.024), (0.014, 0.02), (0.01, 0.012)], [SHOE] * 4, sides=8)  # Griffgummi
        m.loft([(sx * 0.208, 0.618, 0.87), (sx * 0.21, 0.628, 0.83), (sx * 0.212, 0.62, 0.78)], [(0.006, 0.01), (0.005, 0.009), (0.004, 0.007)],
               [SILVER] * 3, sides=6)  # Bremshebel
    # Antrieb hinten: Kassette, Schaltwerk, Kette (dreht nicht mit, sieht man nicht)
    m.lathe(HUB_R + Vector((0.045, 0, 0)), [(-0.012, 0.015), (-0.012, 0.05), (0.012, 0.033), (0.012, 0.015)], SILVER, seg=16)
    m.box(HUB_R + Vector((0.06, 0.0, -0.075)), (0.016, 0.05, 0.075), lin((.2, .2, .22)))
    chain = lin((.25, .25, .27))
    m.tube(BB + Vector((0.045, 0, 0.1)), HUB_R + Vector((0.045, 0, 0.05)), 0.004, chain, sides=4)
    m.tube(BB + Vector((0.045, 0, -0.1)), HUB_R + Vector((0.058, 0.015, -0.1)), 0.004, chain, sides=4)
    # Flasche im Halter am Unterrohr
    dt = (ht_bot - BB).normalized()
    up = Vector((1, 0, 0)).cross(dt)
    m.loft([BB + dt * 0.18 + up * 0.055, BB + dt * 0.36 + up * 0.055, BB + dt * 0.39 + up * 0.055], [0.034, 0.034, 0.015],
           [WHITE, WHITE, lin((.1, .1, .1))], sides=10, side=(0, 0, 1))


def wheel(m):
    m.lathe((0, 0, 0), [(0, WHEEL_R), (0.011, WHEEL_R - 0.004), (0.0135, WHEEL_R - 0.0125), (0, WHEEL_R - 0.02),
                        (-0.0135, WHEEL_R - 0.0125), (-0.011, WHEEL_R - 0.004)], TYRE, seg=32)  # Reifen
    m.lathe((0, 0, 0), [(-0.011, 0.314), (0.011, 0.314), (0.0085, 0.272), (-0.0085, 0.272)], RIM, seg=32)  # Hochprofilfelge
    m.lathe((0, 0, 0), [(-0.05, 0.0), (-0.05, 0.012), (-0.03, 0.022), (0.03, 0.022), (0.05, 0.012), (0.05, 0.0)], SILVER, seg=8)  # Nabe
    for k in range(16):
        a = 2 * math.pi * k / 16
        sx = 1 if k % 2 else -1
        d = Vector((0, math.cos(a), math.sin(a)))
        m.tube(Vector((sx * 0.028, 0, 0)) + d * 0.02, d * 0.274, 0.0016, lin((.35, .35, .37)), sides=3)


def crank(m):
    m.tube((-0.075, 0, 0), (0.075, 0, 0), 0.012, SILVER, sides=8)
    m.lathe((0.045, 0, 0), [(-0.002, 0.085), (0.002, 0.085), (0.002, 0.106), (-0.002, 0.106)], lin((.18, .18, .2)), seg=28)  # Kettenblatt
    for k in range(4):  # Spider
        a = 2 * math.pi * k / 4 + 0.4
        m.tube((0.045, 0, 0), (0.045, 0.08 * math.cos(a), 0.08 * math.sin(a)), 0.008, FRAME, sides=4)
    for sx, sz in ((1, 1), (-1, -1)):
        m.loft([(sx * 0.07, 0, 0), (sx * 0.08, 0, sz * 0.09), (sx * 0.085, 0, sz * CRANK)], [(0.008, 0.02), (0.008, 0.016), (0.008, 0.014)],
               [FRAME] * 3, sides=8, caps=(False, False))


def body(m):
    tilt = math.radians(40)  # Oberkörper an den Bremsgriffen, entspannt
    t = Vector((0, math.cos(tilt), math.sin(tilt)))
    nup = Vector((0, -math.sin(tilt), math.cos(tilt)))  # Rücken-Normale
    pc = Vector((0, HIP.y, HIP.z))
    # Rumpf: (Abstand entlang t, Rückenwölbung, Breite rx, Tiefe ry, Farbe); Saum der Hose doppelt -> harte Kante
    rows = [(-0.05, -0.02, 0.135, 0.095, BIB), (0.04, 0.0, 0.15, 0.108, BIB), (0.1, 0.005, 0.145, 0.103, BIB), (0.1, 0.005, 0.145, 0.103, KIT),
            (0.2, 0.015, 0.142, 0.1, KIT), (0.33, 0.03, 0.165, 0.115, KIT), (0.44, 0.03, 0.18, 0.108, KIT_DARK), (0.52, 0.01, 0.165, 0.085, KIT_DARK)]
    m.loft([pc + t * d + nup * b for d, b, _, _, _ in rows], [(rx, ry) for _, _, rx, ry, _ in rows], [c for *_, c in rows], sides=12)
    sc = pc + t * 0.5 + nup * 0.0
    # Hals + Kopf (blickt nach vorn) + Helm + Brille
    head = sc + Vector((0, 0.125, 0.18))
    m.tube(sc + nup * 0.02, head + Vector((0, -0.02, -0.05)), 0.05, SKIN, sides=10)
    m.ball(head, (0.074, 0.095, 0.105), SKIN, seg=12, rings=8)
    hc = head + Vector((0, -0.012, 0.03))
    m.ball(hc, (0.102, 0.142, 0.1), WHITE, seg=14, rings=6, lat0=-12.0, colfn=lambda lat: KIT_DARK if lat < 8 else (KIT if lat > 70 else WHITE))
    m.loft([head + Vector((-0.07, 0.06, 0.0)), head + Vector((-0.045, 0.092, 0.005)), head + Vector((0, 0.1, 0.006)),
            head + Vector((0.045, 0.092, 0.005)), head + Vector((0.07, 0.06, 0.0))], [(0.012, 0.022)] * 5, [GLASS] * 5, sides=6,
           side=(0, 0, 1))  # Brille
    # Arme: Schulter -> Ellbogen (nach außen/unten geknickt) -> Handgelenk am Bremsgriff
    for sx in (-1, 1):
        sh = sc + Vector((sx * 0.165, 0, 0)) - nup * 0.03
        wr = Vector((sx * 0.2, 0.535, 0.905))
        el = ik(sh, wr, 0.3, 0.27, Vector((sx * 0.6, -0.3, -1)))
        m.ball(sh, (0.052, 0.052, 0.052), KIT_DARK, seg=8, rings=5)
        sl = sh.lerp(el, 0.6)
        m.loft([sh, sl, sl, el], [0.05, 0.045, 0.043, 0.04], [KIT, KIT, SKIN, SKIN], sides=10, caps=(False, True))
        m.loft([el, el.lerp(wr, 0.4), wr], [0.04, 0.036, 0.026], [SKIN] * 3, sides=10, caps=(True, False))
        m.loft([wr - (wr - el).normalized() * 0.01, Vector((sx * 0.207, 0.57, 0.9)), Vector((sx * 0.21, 0.6, 0.895))],
               [(0.03, 0.022), (0.035, 0.03), (0.022, 0.02)], [GLOVE] * 3, sides=8, side=(0, 0, 1))  # Hand um den Griff


def thigh(m):
    rows = [(0.0, 0.06, 0.07, BIB), (-0.12, 0.075, 0.08, BIB), (-0.26, 0.065, 0.07, BIB), (-0.26, 0.065, 0.07, SKIN), (-0.37, 0.058, 0.06, SKIN),
            (-LT, 0.052, 0.054, SKIN)]
    m.loft([(0, 0.005 if z > -0.3 else 0, z) for z, *_ in rows], [(rx, ry) for _, rx, ry, _ in rows], [c for *_, c in rows], sides=10)


def shin(m):
    rows = [(0.0, 0.05, 0.055, 0.0, SKIN), (-0.12, 0.052, 0.062, -0.012, SKIN), (-0.24, 0.042, 0.048, -0.006, SKIN),
            (-LS + 0.11, 0.032, 0.035, 0.0, SKIN), (-LS + 0.11, 0.032, 0.035, 0.0, WHITE), (-LS, 0.032, 0.036, -0.005, WHITE)]
    m.loft([(0, dy, z) for z, _, _, dy, _ in rows], [(rx, ry) for _, rx, ry, _, _ in rows], [c for *_, c in rows], sides=10)


def foot(m):
    m.box((0, 0, -0.004), (0.07, 0.085, 0.016), lin((.12, .12, .13)))  # Pedal (Klickpedal unter der Platte)
    sole = [(-0.165, 0.035, 0.05, 0.05), (-0.13, 0.042, 0.058, 0.055), (-0.04, 0.046, 0.044, 0.04), (0.05, 0.043, 0.034, 0.03),
            (0.11, 0.032, 0.022, 0.02), (0.13, 0.015, 0.014, 0.018)]
    m.loft([(0, y, 0.008 + zc) for y, _, _, zc in sole], [(rx, ry) for _, rx, ry, _ in sole], [SHOE] * len(sole), sides=8, side=(1, 0, 0))
    m.ball(ANKLE + Vector((0, -0.005, -0.025)), (0.045, 0.06, 0.05), SHOE, seg=8, rings=5)  # Schaft/Ferse bis zum Knöchel


PARTS = dict(frame=frame, wheel=wheel, crank=crank, body=body, thigh=thigh, shin=shin, foot=foot)


def to_mesh(name, m):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in m.v], [], m.f)
    me.validate()
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    col = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
    for i, c in enumerate(m.c):
        col.data[i].color = c
    me.color_attributes.active_color = col
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(50))
    return me


def place(name, me, loc=(0, 0, 0)):
    obj = bpy.data.objects.new(name, me)
    obj.location = loc
    bpy.context.scene.collection.objects.link(obj)
    return obj


def pose(objs, a):
    """Beine wie rider.gd bei Kurbelwinkel a (nur Blender-Datei/Vorschau; rider.gd rechnet zur Laufzeit selbst)."""
    objs["crank"].rotation_euler = (-a, 0, 0)
    for sx, side, aa in ((1, "r", a), (-1, "l", a + math.pi)):
        p, pitch, ankle, hip, knee = leg_pose(aa, sx)
        objs["foot_" + side].location = p
        objs["foot_" + side].rotation_euler = (pitch, 0, 0)
        for bone, top, end in (("thigh_", hip, knee), ("shin_", knee, ankle)):
            z = (top - end).normalized()
            y = z.cross(Vector((1, 0, 0)) - z * z.x).normalized()
            x = y.cross(z)
            mat = Matrix((x, y, z)).transposed().to_4x4()
            mat.translation = top
            objs[bone + side].matrix_world = mat


def test():
    """Beinlänge passt zum Rad: Kniewinkel unten ~145° (nicht durchgestreckt), oben ≥ 65°; Knöchel immer erreichbar."""
    angles = []
    for k in range(36):
        p, pitch, ankle, hip, knee = leg_pose(2 * math.pi * k / 36, 1)
        assert (ankle - hip).length < LT + LS - 0.01, ("Bein zu kurz", k)
        assert abs((knee - hip).length - LT) < 1e-6 and abs((ankle - knee).length - LS) < 1e-6
        angles.append(math.degrees((hip - knee).angle(ankle - knee)))
    print(f"Kniewinkel {min(angles):.0f}°…{max(angles):.0f}°")
    assert 62 < min(angles) < 80 and 135 < max(angles) < 155, angles


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out, preview = args[0], (args[1] if len(args) > 1 else None)
    test()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    meshes, tris = {}, 0
    for name, build in PARTS.items():
        m = M()
        build(m)
        meshes[name] = to_mesh(name, m)
        n = sum(len(p.vertices) - 2 for p in meshes[name].polygons)
        tris += n * (2 if name in ("wheel", "thigh", "shin", "foot") else 1)
        print(f"{name}: {n} Dreiecke")
    print(f"gesamt (Räder und Beine doppelt): {tris} Dreiecke")
    objs = {"frame": place("frame", meshes["frame"]), "body": place("body", meshes["body"]),
            "wheel_f": place("wheel_f", meshes["wheel"], HUB_F), "wheel_r": place("wheel_r", meshes["wheel"], HUB_R),
            "crank": place("crank", meshes["crank"], BB)}
    for side in "lr":
        for part in ("thigh", "shin", "foot"):
            objs[f"{part}_{side}"] = place(f"{part}_{side}", meshes[part])
    pose(objs, math.radians(60))
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True, export_apply=True, export_vertex_color="ACTIVE",
                              export_materials="NONE")
    if preview:
        render_preview(meshes, objs, preview)


def render_preview(meshes, objs, path):
    """Seitenansicht und schräg von hinten (Fahrkamera), Trikot orange eingefärbt."""
    kit = Vector((1.0, 0.45, 0.1))
    for me in meshes.values():
        col = me.color_attributes["Color"]
        for d in col.data:
            c = d.color
            if c[3] > 0.5:
                d.color = (c[0] * kit.x ** 2.2, c[1] * kit.y ** 2.2, c[2] * kit.z ** 2.2, 1)
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.75, 0.82, 0.9)
    scene.display.shading.show_shadows = True
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    for suffix, loc, rot, lens, a in (("", (4.2, 0.1, 0.85), (90, 0, 90), 50, 60), ("_hinten", (1.4, -4.2, 2.0), (73, 0, 18), 50, 150)):
        pose(objs, math.radians(a))
        cam.location = loc
        cam.rotation_euler = [math.radians(r) for r in rot]
        cam.data.lens = lens
        scene.render.filepath = path.replace(".png", f"{suffix}.png")
        bpy.ops.render.render(write_still=True)


main()
