# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
"""Geologie der Workout-Welt (world.py): Makrorelief, regionale Erosion, Erosionsrinnen, Schichtstufen, Straßenanschluss.
Reines numpy/scipy, kennt weder Godot noch Workouts. Alles ist eine reine Funktion der Weltkoordinaten (+ Seed): Kacheln
hängen nicht davon ab, in welcher Reihenfolge sie entstehen.

Pipeline (grob -> fein), world.py::field ruft die Stufen in dieser Reihenfolge:
  Makro       Talboden aus der Straßenhöhe + Gebirge (mountains: Ketten entlang einer Streichrichtung) + Täler/Hänge der Szene
  Erosion     Regions: je 2-km-Region (+768 m Rand) auf 16-m-Raster: Senken füllen, Abfluss, Flusserosion (Stream-Power,
              implizit gelöst), Ablagerung in flachen Talböden, Hangrutschung (Schutthalden). Überlappende Regionen werden
              weich gemischt -> keine Nähte. Ergebnis im Speicher (LRU), nie neu gerechnet solange es gebraucht wird.
  Rinnen      gullies: hangabwärts gerichtetes Phasenrauschen 40/20/10 m, Stärke nach Hangneigung
  Schichten   terrace: Schichtstufen/Tafelberge (Wüste)
  Straße      conform: ebene Trasse, Einschnitt 1:1 darüber, Damm 1:1,5 darunter, als Kegel von jedem Straßenstück
              (min/max über alle Stücke -> stetig, auch zwischen Serpentinen)

  uv run terrain.py --test
"""
import math, sys, time
from collections import OrderedDict

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.ndimage import gaussian_filter
from scipy.sparse.linalg import spsolve_triangular


def smoothstep(a, b, x):
    t = np.clip((np.asarray(x, float) - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


# --- Rauschen: gehasht (kein Zustand, keine Periode, unabhängig von der Reihenfolge) -> jede Kachel einzeln reproduzierbar ---

def hash01(*ints, seed=0):
    with np.errstate(over="ignore"):
        k = np.uint64((seed * 0x9E3779B1 + 0x632BE59B) & 0xFFFFFFFFFFFFFFFF)
        for i in ints:
            k = k ^ (np.asarray(i).astype(np.int64).astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15))
            k = k ^ (k >> np.uint64(31))
            k = k * np.uint64(0xBF58476D1CE4E5B9)
            k = k ^ (k >> np.uint64(27))
            k = k * np.uint64(0x94D049BB133111EB)
            k = k ^ (k >> np.uint64(31))
        return (k >> np.uint64(11)).astype(np.float64) / 2.0 ** 53


_GA = np.arange(16) * (2 * np.pi / 16)
_GX, _GZ = np.cos(_GA), np.sin(_GA)


def perlin(x, z, seed):
    """2D-Gradientenrauschen, ~[-0.7, 0.7]. Gradient je Gitterpunkt aus 16 Richtungen (ein Hash, Tabelle statt cos/sin)."""
    xi, zi = np.floor(x), np.floor(z)
    fx, fz = x - xi, z - zi
    xi, zi = xi.astype(np.int64).astype(np.uint64), zi.astype(np.int64).astype(np.uint64)
    s = np.uint64((seed * 0x9E3779B1 + 0x632BE59B) & 0xFFFFFFFFFFFFFFFF)

    def g(dx, dz):
        with np.errstate(over="ignore"):
            k = ((xi + np.uint64(dx)) * np.uint64(0x9E3779B97F4A7C15)) ^ ((zi + np.uint64(dz)) * np.uint64(0xC2B2AE3D27D4EB4F)) ^ s
            k = (k ^ (k >> np.uint64(31))) * np.uint64(0xBF58476D1CE4E5B9)
            k = k ^ (k >> np.uint64(29))
        j = (k >> np.uint64(60)).astype(np.intp)
        return _GX[j] * (fx - dx) + _GZ[j] * (fz - dz)
    u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    v = fz * fz * fz * (fz * (fz * 6 - 15) + 10)
    a = g(0, 0) + u * (g(1, 0) - g(0, 0))
    b = g(0, 1) + u * (g(1, 1) - g(0, 1))
    return a + v * (b - a)


def fbm(x, z, seed, octaves=4):
    """Fraktales Rauschen ~[-1, 1] (Standardabweichung ~0,3)."""
    out, amp, f, tot = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        out = out + amp * perlin(x * f, z * f, seed + 101 * o)
        tot += amp
        amp *= 0.5
        f *= 2.03
    return out / tot * 1.6


def ridged(x, z, seed, octaves=5):
    """Grate (Hochgebirge) 0…1: gefaltetes Rauschen, feine Oktaven nur wo es schon hoch ist."""
    out, amp, f, w, tot = 0.0, 1.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        r = (1 - np.abs(perlin(x * f, z * f, seed + 101 * o)) * 1.45) ** 2
        out = out + r * amp * w
        tot += amp
        w = np.clip(r * 1.6, 0, 1)
        amp *= 0.5
        f *= 2.1
    return np.clip(out / tot * 1.25, 0, 1)


def noise1(u, seed):
    """Glattes 1D-Rauschen ~[-1, 1] entlang der Strecke."""
    i = np.floor(u)
    f = u - i
    i = i.astype(np.int64)
    g0, g1 = hash01(i, seed=seed) * 2 - 1, hash01(i + 1, seed=seed) * 2 - 1
    w = f * f * f * (f * (f * 6 - 15) + 10)
    return 2 * (g0 * f * (1 - w) + g1 * (f - 1) * w)


# --- Makrorelief ---

def mountains(x, z, seed, strike, soft, ridge, mesa):
    """Gebirgsform ~0…1 vor der Amplitude, gemischt nach Stil (Gewichte je Punkt, Summe 1).
    Ketten: gefaltetes Rauschen in Koordinaten quer/längs zur Streichrichtung (2,6 km / 5 km), großräumig verbogen ->
    lange, verbundene Grate statt einzelner Kegel; Gipfel sitzen auf den Ketten, ein Massiv-Feld hebt ganze Gebirge.
      ridge (Alpen): scharfe Grate, Gipfelfluren nur auf den Ketten
      soft (Mittelgebirge): abgerundete, verwitterte Rücken
      mesa (Wüste): Hochflächen mit Stufenrändern (Schichtstufen kommen später aus terrace)"""
    wx = x + 2600 * fbm(x / 17000, z / 17000, seed + 1, 2)
    wz = z + 2600 * fbm(x / 17000 + 5.3, z / 17000 - 2.1, seed + 2, 2)
    wx = wx + 450 * fbm(x / 3200, z / 3200, seed + 10, 2)  # Grate schlängeln sich auch im Kilometermaßstab
    wz = wz + 450 * fbm(x / 3200 - 7.7, z / 3200, seed + 11, 2)
    c, s = math.cos(strike), math.sin(strike)
    u, v = wx * c + wz * s, -wx * s + wz * c
    # Kettenrücken: gefaltetes Rauschen mit leicht gerundetem Falz (kein linealgerader Knick auf dem Grat)
    c1 = 1 - 1.45 * np.sqrt(perlin(u / 2600, v / 5000, seed + 3) ** 2 + 0.03 ** 2)
    c2 = 1 - 1.45 * np.sqrt(perlin(u / 1250, v / 2400, seed + 12) ** 2 + 0.03 ** 2)
    chain = np.clip(0.75 * c1 * c1 + 0.25 * np.clip(c1 * 1.6, 0, 1) * c2 * c2, 0, 1)
    massif = smoothstep(-0.5, 0.5, fbm(x / 11000, z / 11000, seed + 4, 2))
    dx = wx + 160 * fbm(x / 650, z / 650, seed + 5, 2)
    dz = wz + 160 * fbm(x / 650 + 3.3, z / 650 - 1.7, seed + 6, 2)
    out = np.zeros_like(x)
    if np.any(ridge > 0):
        det = ridged(dx / 1300, dz / 1300, seed + 7, 5)
        out = out + ridge * chain ** 1.5 * (0.4 + 0.6 * det)
    hb = 0.5 + 0.5 * fbm(dx / 1800, dz / 1800, seed + 8, 4) if np.any((soft > 0) | (mesa > 0)) else 0.0
    if np.any(soft > 0):
        r = 0.4 * chain + 0.6 * hb
        out = out + soft * r * r * (3 - 2 * r)  # gerundete Kuppen, keine Spitzen
    if np.any(mesa > 0):
        plateau = smoothstep(0.45, 0.52, 0.5 + 0.5 * fbm(dx / 2400, dz / 2400, seed + 9, 3))
        out = out + mesa * (plateau * (0.7 + 0.3 * chain) + 0.12 * hb)
    return out * (0.55 + 0.45 * massif)


def terrace(rel, x, z, seed, step=24.0):
    """Schichtstufen: Relief über dem Talboden in Stufen (flache Bänke, steile Stufenkanten), Schichten leicht verbogen,
    jede Schicht mit eigener Härte (Anteil Wand). Stetig (die Stufe endet genau auf der nächsten Bank)."""
    w = 5 * fbm(x / 900, z / 900, seed + 60, 2)
    t = (rel + w) / step
    k = np.floor(t)
    a = 0.35 + 0.55 * hash01(k, seed=seed + 61)  # je Schicht: harte Bank (steile Wand) oder weiche (Hang)
    q = (k + smoothstep(a, 1.0, t - k)) * step - w
    return rel + (q - rel) * smoothstep(2, 10, rel)


def gullies(x, z, gx, gz, seed, res=0.0):
    """Erosionsrinnen -> Höhenänderung (m). Streifen laufen hangabwärts (Phasenrauschen quer zur Falllinie, Zellen mit
    zufälligem Versatz, Gauß-Gewicht), drei Oktaven, jede verbiegt die Richtung der nächsten (Verästelung). Stärke nach
    Hangneigung: flaches Land bleibt glatt. res > 0: Oktaven unter 4·res weglassen (grobe Kacheln, kein Aliasing)."""
    out = np.zeros_like(x)
    gx, gz = gx.copy(), gz.copy()
    patch = smoothstep(-0.35, 0.45, fbm(x / 350, z / 350, seed + 90, 2))  # Rinnen in Feldern, nicht überall gleich
    for o, lam in enumerate((40.0, 20.0, 10.0)):
        if res and lam < 4 * res:
            break
        slope = np.hypot(gx, gz)
        amp = 0.025 * lam * smoothstep(0.06, 0.5, slope) * patch
        inv = 1 / np.maximum(slope, 1e-6)
        qx, qz = -gz * inv, gx * inv  # quer zur Falllinie
        px, pz = x / lam, z / lam
        ix, iz = np.floor(px), np.floor(pz)
        va, wt, dvx, dvz = np.zeros_like(x), np.zeros_like(x), np.zeros_like(x), np.zeros_like(x)
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                cx = ix + i + 0.25 + 0.5 * hash01(ix + i, iz + j, seed=seed + 70 + o)
                cz = iz + j + 0.25 + 0.5 * hash01(ix + i, iz + j, seed=seed + 80 + o)
                ex, ez = px - cx, pz - cz
                w = np.exp(-2.0 * (ex * ex + ez * ez))
                ph = 2 * np.pi * (ex * qx + ez * qz)
                va += np.cos(ph) * w
                sn = -np.sin(ph) * w * 2 * np.pi / lam
                dvx += sn * qx
                dvz += sn * qz
                wt += w
        out += amp * va / wt
        gx += amp * dvx / wt
        gz += amp * dvz / wt
    return out


# --- Erosion auf einem Raster ---

NB = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


def fill(h, eps=1e-3):
    """Senken füllen (Überlaufhöhe): minimaler Spannbaum über die Kanten max(h_u, h_v) mit einem virtuellen Randknoten ->
    Füllhöhe = Maximum entlang des Baumpfads zum Rand (Minimax-Pfad), + eps je Baumschritt (Gefälle auf Seeböden).
    Pfad-Maxima per Zeigerverdopplung, keine Python-Schleife über Zellen."""
    n0, n1 = h.shape
    N = n0 * n1
    hv = h.ravel()
    lo = hv.min() - 1.0
    idx = np.arange(N).reshape(n0, n1)
    rows, cols = [], []
    for a, b in ((idx[:, :-1], idx[:, 1:]), (idx[:-1], idx[1:]), (idx[:-1, :-1], idx[1:, 1:]), (idx[:-1, 1:], idx[1:, :-1])):
        rows.append(a.ravel()), cols.append(b.ravel())
    border = np.unique(np.concatenate([idx[0], idx[-1], idx[:, 0], idx[:, -1]]))
    rows.append(np.full(len(border), N)), cols.append(border)
    r, c = np.concatenate(rows), np.concatenate(cols)
    hx = np.append(hv, lo)
    w = np.maximum(hx[r], hx[c]) - lo + 1.0  # > 0 (0 hieße "keine Kante")
    T = csgraph.minimum_spanning_tree(sparse.csr_matrix((w, (r, c)), shape=(N + 1, N + 1)))
    _, pred = csgraph.breadth_first_order(T, N, directed=False)
    anc = np.where(pred < 0, N, pred)
    f = np.append(hv, -np.inf)
    step = 1
    while step < N:
        f = np.maximum(f, f[anc] + eps * step)
        anc = anc[anc]
        step *= 2
    return f[:N].reshape(n0, n1)


def receivers(h, res):
    """Steilster Abstieg (8 Nachbarn) -> Empfänger je Zelle (sich selbst am Rand/in Senken), Abstand."""
    n0, n1 = h.shape
    hp = np.pad(h, 1, mode="constant", constant_values=np.inf)
    idx = np.pad(np.arange(n0 * n1).reshape(n0, n1), 1, mode="constant", constant_values=-1)
    best = np.zeros((n0, n1))
    rec = np.arange(n0 * n1).reshape(n0, n1)
    dist = np.full((n0, n1), res)
    for dy, dx in NB:
        L = res * math.hypot(dy, dx)
        s = (h - hp[1 + dy:n0 + 1 + dy, 1 + dx:n1 + 1 + dx]) / L
        m = s > best
        best = np.where(m, s, best)
        rec = np.where(m, idx[1 + dy:n0 + 1 + dy, 1 + dx:n1 + 1 + dx], rec)
        dist = np.where(m, L, dist)
    rec[0], rec[-1], rec[:, 0], rec[:, -1] = idx[1, 1:-1], idx[-2, 1:-1], idx[1:-1, 1], idx[1:-1, -2]  # Rand = Abfluss
    return rec.ravel(), dist.ravel(), best.ravel()


def accumulate(rec, order, src):
    """Summe von src über alle Oberlieger (Einzugsgebiet): untere Dreiecksmatrix in topologischer Reihenfolge."""
    N = len(rec)
    pos = np.empty(N, np.int64)
    pos[order] = np.arange(N)
    own = rec != np.arange(N)
    M = sparse.identity(N, format="csr") - sparse.csr_matrix((own.astype(float), (pos[rec], pos)), shape=(N, N))
    out = spsolve_triangular(M.tocsr(), src[order], lower=True)
    return out[pos]


def thermal(h, talus, res, rate=0.3, iters=6):
    """Hangrutschung: Material über dem Schüttwinkel (talus = tan) rutscht zu tieferen Nachbarn -> Schutthalden."""
    n0, n1 = h.shape
    for _ in range(iters):
        hp = np.pad(h, 1, mode="edge")
        ex, tot, mx = [], 0.0, 0.0
        for dy, dx in NB:
            e = np.maximum(h - hp[1 + dy:n0 + 1 + dy, 1 + dx:n1 + 1 + dx] - talus * res * math.hypot(dy, dx), 0)
            ex.append(e)
            tot = tot + e
            mx = np.maximum(mx, e)
        move = rate * mx
        k = np.where(tot > 0, move / np.maximum(tot, 1e-12), 0)
        gain = np.zeros((n0 + 2, n1 + 2))
        for (dy, dx), e in zip(NB, ex):
            gain[1 + dy:n0 + 1 + dy, 1 + dx:n1 + 1 + dx] += e * k
        h = h - move + gain[1:-1, 1:-1]
    return h


def erode(h, res, K, talus, iters=20, kd=0.6):
    """Regionale Erosion. h: Höhen (n0×n1), K: Erodierbarkeit je Zelle (0 = fest, z. B. Straßenkorridor), talus: Schüttwinkel.
    Je Schritt: Abfluss (Einzugsfläche A), Flusserosion nach Stream-Power E = K·√A·S implizit (Braun & Willett 2013, n = 1:
    eine Dreieckslösung, stabil bei jedem Schritt), Ablagerung des mitgeführten Materials wo das Gefälle nachlässt
    (Talböden, Schwemmfächer), Hangrutschung. -> (h, Abfluss log10 A, Sediment m)."""
    n0, n1 = h.shape
    N = n0 * n1
    h = fill(h).ravel()
    Kv, area = K.ravel(), res * res
    sed = np.zeros(N)
    A = np.full(N, area)
    for it in range(iters):
        if it and it % 7 == 0:
            h = fill(h.reshape(n0, n1)).ravel()
        rec, dist, S = receivers(h.reshape(n0, n1), res)
        order = np.argsort(-h, kind="stable")  # Empfänger liegen immer tiefer -> topologische Ordnung
        A = accumulate(rec, order, np.full(N, area))
        own = rec != np.arange(N)
        F = np.where(own, Kv * np.sqrt(A) / dist, 0.0)
        # (1 + F)·h'_i − F·h'_r = h_i, von unten nach oben
        up = order[::-1]
        pos = np.empty(N, np.int64)
        pos[up] = np.arange(N)
        M = sparse.csr_matrix((np.concatenate([1 + F, -F[own]]),
                               (np.concatenate([pos, pos[own]]), np.concatenate([pos, pos[rec[own]]]))), shape=(N, N))
        hn = spsolve_triangular(M, h[up], lower=True)[pos]
        ero = np.maximum(h - hn, 0)
        # Mitgeführtes Material (mittlere Abtragung im Einzugsgebiet) bleibt liegen, wo es flach wird
        qs = accumulate(rec, order, ero * area) / A
        dep = kd * qs * smoothstep(0.15, 0.02, S) * (Kv > 0)
        hn = hn + dep
        sed += dep
        h = thermal(hn.reshape(n0, n1), talus, res, iters=2).ravel()
    return h.reshape(n0, n1), np.log10(A).reshape(n0, n1), sed.reshape(n0, n1)


class Regions:
    """Erosion in Regionen: Raster SIZE (Kern) + MARGIN (Rand, damit Randeffekte außerhalb liegen), RES Rasterweite.
    Benachbarte Kerne überlappen um ±BLEND und werden dort mit glatten Gewichten (Summe 1) gemischt -> stetig über alle
    Regionsgrenzen, und jede Region ist eine reine Funktion ihres Index (Reihenfolge egal). macro(x, z) -> (h, K, talus)."""
    SIZE, MARGIN, BLEND, RES = 2048.0, 768.0, 256.0, 16.0
    FIELDS = ("h", "dh", "flow", "sed", "curv", "gx", "gz")

    def __init__(self, macro, cap=96, iters=20):
        self.macro, self.cap, self.iters = macro, cap, iters
        self.cache = OrderedDict()
        self.pending = {}  # (I, J) -> Future eines Hilfsprozesses (world.stream rechnet voraus)
        self.ms, self.made, self.waited = 0.0, 0, 0.0

    def region(self, I, J):
        key = (I, J)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key in self.pending:
            t0 = time.time()
            reg, ms = self.pending.pop(key).result()
            self.waited += (time.time() - t0) * 1000
        else:
            reg, ms = self.compute(I, J)
        self.cache[key] = reg
        if len(self.cache) > self.cap:
            self.cache.popitem(last=False)
        self.ms += ms
        self.made += 1
        return reg

    def compute(self, I, J):
        """Eine Region rechnen (rein, darf in einem anderen Prozess laufen) -> (Felder, ms)."""
        t0 = time.time()
        S, M, R, B = self.SIZE, self.MARGIN, self.RES, self.BLEND
        n = int((S + 2 * M) / R) + 1
        ax = I * S - M + np.arange(n) * R
        az = J * S - M + np.arange(n) * R
        GX, GZ = np.meshgrid(ax, az)
        h0, K, talus = (a.reshape(n, n) for a in self.macro(GX.ravel(), GZ.ravel()))
        # Startrauschen je Weltzelle (in allen überlappenden Regionen gleich): bricht das Raster-Muster des steilsten
        # Abstiegs (sonst parallele Rinnen entlang der Rasterachsen auf glatten Hängen)
        jit = 2.0 * (hash01(np.rint(GX / R), np.rint(GZ / R), seed=977) - 0.5)
        h, flow, sed = erode(h0 + jit * (K > 0), R, K, talus, self.iters)
        h = h0 + gaussian_filter(h - h0, 1.3)  # Änderung leicht geglättet: keine Treppen aus einzelnen Rasterzellen
        lap = (np.roll(h, 1, 0) + np.roll(h, -1, 0) + np.roll(h, 1, 1) + np.roll(h, -1, 1) - 4 * h) / (R * R)
        c = slice(int((M - B) / R), int((M + S + B) / R) + 1)  # gespeichert: Kern ± BLEND
        gz, gx = np.gradient(h, R)  # als Feld gespeichert und interpoliert: stetig (die Ableitung der Bilinearen springt je Zelle)
        reg = {k: a[c, c].astype(np.float32) for k, a in zip(self.FIELDS, (h, h - h0, flow, sed, lap, gx, gz))}
        return reg, (time.time() - t0) * 1000

    def keys_near(self, px, pz, radius):
        """Regionen, deren gespeicherte Fläche (Kern ± BLEND) näher als radius an einem der Punkte liegt."""
        S, B = self.SIZE, self.BLEND
        out = {}
        for x, z in zip(px, pz):
            for I in range(int(math.floor((x - radius - B) / S)), int(math.floor((x + radius + B) / S)) + 1):
                for J in range(int(math.floor((z - radius - B) / S)), int(math.floor((z + radius + B) / S)) + 1):
                    dx = max(I * S - B - x, 0, x - (I + 1) * S - B)
                    dz = max(J * S - B - z, 0, z - (J + 1) * S - B)
                    dd = math.hypot(dx, dz)
                    if dd < radius:
                        out[(I, J)] = min(out.get((I, J), 1e18), dd)
        return out

    def sample(self, x, z):
        """Gemischte Felder an Punkten (nur aufrufen, wo Erosion gebraucht wird) -> dict FIELDS (gx, gz = Gradient von h)."""
        S, B, R = self.SIZE, self.BLEND, self.RES
        out = {k: np.zeros(len(x)) for k in self.FIELDS}
        axes = []
        for v in (x, z):
            a, b = np.floor((v - B) / S).astype(np.int64), np.floor((v + B) / S).astype(np.int64)
            wb = np.where(a == b, 0.0, smoothstep(-B, B, v - b * S))
            axes.append(((a, 1 - wb), (b, wb)))
        for Ia, wa in axes[0]:
            for Jb, wb in axes[1]:
                w = wa * wb
                live = w > 0
                if not live.any():
                    continue
                keys = Ia[live] * 1_000_003 + Jb[live]
                for kk in np.unique(keys):
                    sel = np.flatnonzero(live)[keys == kk]
                    I, J = int(Ia[sel[0]]), int(Jb[sel[0]])
                    reg = self.region(I, J)
                    u = (x[sel] - (I * S - B)) / R
                    t = (z[sel] - (J * S - B)) / R
                    m = reg["h"].shape[0]
                    i0 = np.clip(np.floor(u).astype(np.int64), 0, m - 2)
                    j0 = np.clip(np.floor(t).astype(np.int64), 0, m - 2)
                    fu, ft = u - i0, t - j0
                    ws = w[sel]
                    for k in self.FIELDS:
                        g = reg[k]
                        a00, a10, a01, a11 = g[j0, i0], g[j0, i0 + 1], g[j0 + 1, i0], g[j0 + 1, i0 + 1]
                        out[k][sel] += ws * (a00 + (a10 - a00) * fu + (a01 - a00) * ft + (a00 - a10 - a01 + a11) * fu * ft)
        return out


# --- Straßenanschluss ---

CUT, FILL, ROUND = 1.0, 1 / 1.5, 3.0  # Einschnitt 1:1, Damm 1:1,5, Ausrundung an der Böschungskante (m)


def smin(a, b, k):
    k = np.maximum(k, 1e-6)
    t = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * t - k * t * (1 - t)


def conform(px, pz, N, tree, x, z, h, hw, sink, bench, reach=80.0, k=64):
    """Gelände N an die Straße anschließen. tree/x/z/h/hw: Straßenpunkte (Strecken a -> a+1). Je Straßenstück ein Kegel:
    eben auf hw + bench (sink unter der Fahrbahn), darüber höchstens Einschnitt CUT, darunter mindestens Damm FILL;
    Gelände = clamp(N, max aller Dammkegel, min aller Einschnittkegel), weich verrundet abseits der Trasse.
    Ein Einschnittkegel gewinnt im Zweifel (nie Gelände über einer Fahrbahn). Kegel klingen bis reach aus -> stetig."""
    H = N.copy()
    n = len(x)
    dd, ii = tree.query(np.column_stack([px, pz]), k=k, distance_upper_bound=reach)
    near = np.isfinite(dd[:, 0])
    if not near.any():
        return H, np.full(len(px), np.inf)
    ii, ok = ii[near], np.isfinite(dd[near])
    a = np.clip(np.where(ok, ii, 0), 0, n - 2)
    b = a + 1
    qx, qz = px[near, None], pz[near, None]
    ex, ez = x[b] - x[a], z[b] - z[a]
    ur = ((qx - x[a]) * ex + (qz - z[a]) * ez) / (ex * ex + ez * ez + 1e-12)
    u = np.clip(ur, 0, 1)
    dseg = np.hypot(qx - x[a] - u * ex, qz - z[a] - u * ez)
    # Höhe über das Stückende hinaus mit dessen Steigung weiter (sonst beansprucht am Knoten das vorige Stück seine
    # Endhöhe als flache Trasse und das Gelände liegt bergauf unter der Fahrbahn)
    base = h[a] + np.clip(ur, -1, 2) * (h[b] - h[a]) - sink
    e = np.maximum(dseg - (hw[a] + u * (hw[b] - hw[a])) - bench, 0)
    es = np.where(e < ROUND, e * e / (2 * ROUND), e - ROUND / 2)
    far = np.where(ok, 1e4 * smoothstep(0.75 * reach, reach, dseg), 1e4)
    hi = (base + CUT * es + far).min(1)
    lo = (base - FILL * es - far).max(1)
    emin = np.where(ok, e, np.inf).min(1)
    kk = 1.5 * smoothstep(0, 4, emin)
    n0 = N[near]
    up = -smin(-n0, -lo, kk)  # weiches Maximum: nie unter dem Damm
    H[near] = smin(up, hi, kk)
    out = np.full(len(px), np.inf)
    out[near] = emin
    return H, out


# --- Selbsttest ---

def test():
    from scipy.spatial import cKDTree
    rng = np.random.default_rng(3)
    n = 96
    X, Z = np.meshgrid(np.arange(n) * 16.0, np.arange(n) * 16.0)
    # Hang mit Rauschen und einer geschlossenen Senke
    h0 = 0.25 * X + 40 * fbm(X / 300, Z / 300, 5, 4) - 30 * np.exp(-((X - 700) ** 2 + (Z - 700) ** 2) / 120 ** 2)
    f = fill(h0)
    assert (f >= h0 - 1e-9).all()
    rec, dist, S = receivers(f, 16.0)
    inner = np.zeros((n, n), bool)
    inner[1:-1, 1:-1] = True
    assert (rec[inner.ravel()] != np.flatnonzero(inner)).all(), "gefülltes Gelände hat keine Senken"
    K = np.full((n, n), 0.004)
    h1, flow, sed = erode(h0, 16.0, K, np.full((n, n), 0.8), iters=12)
    h2, _, _ = erode(h0, 16.0, K, np.full((n, n), 0.8), iters=12)
    assert np.array_equal(h1, h2), "Erosion deterministisch"
    assert flow.max() > np.log10(16 * 16 * 200), "Abfluss sammelt sich in Rinnen"
    assert (h1[1:-1, 1:-1] <= f[1:-1, 1:-1] + 5).all() and h1.mean() < f.mean(), "Erosion trägt ab"
    assert sed.max() > 0
    # Hangrutschung: nichts steiler als der Schüttwinkel bleibt übrig (Stufe 40 m)
    st = np.where(X > 600, 40.0, 0.0)
    th = thermal(st, np.full((n, n), 0.7), 16.0, iters=200)
    assert np.abs(np.diff(th, axis=1)).max() / 16 < 0.7 * 1.6
    # Regionen: stetig über die Grenze (Mischzone), Reihenfolge egal
    macro = lambda x, z: (0.1 * x + 60 * fbm(x / 500, z / 500, 9, 4), np.full(len(x), 0.003), np.full(len(x), 0.8))
    Regions.SIZE, Regions.MARGIN, Regions.BLEND = 512.0, 256.0, 96.0
    try:
        R1, R2 = Regions(macro, iters=8), Regions(macro, iters=8)
        xs = np.linspace(200, 900, 1400)
        zs = np.full_like(xs, 300.0)
        a = R1.sample(xs, zs)["h"]
        R2.sample(xs[::-1], zs)
        b = R2.sample(xs, zs)["h"]
        assert np.array_equal(a, b), "Regionen unabhängig von der Reihenfolge"
        assert np.abs(np.diff(a)).max() < 2.0, np.abs(np.diff(a)).max()  # 0,5-m-Schritte: keine Sprünge
    finally:
        Regions.SIZE, Regions.MARGIN, Regions.BLEND = 2048.0, 768.0, 256.0
    # Straßenanschluss: Trasse eben, nie Gelände über der Fahrbahn, Böschungen begrenzt, zwei Kehren-Äste stetig
    s = np.arange(0, 200, 5.0)
    rx = np.concatenate([s, s[::-1]])
    rz = np.concatenate([np.zeros_like(s), np.full_like(s, 80.0)])
    rh = np.arange(len(rx)) * 5 * 0.06
    hw = np.full(len(rx), 3.0)
    tr = cKDTree(np.column_stack([rx, rz]))
    gx, gz = np.meshgrid(np.arange(-50, 250, 1.0), np.arange(-60, 140, 1.0))
    N = 0.3 * (gz - 40) + 12 + 6 * fbm(gx / 80, gz / 80, 1, 3)  # Hang, in den die Kehren gelegt sind
    H, emin = conform(gx.ravel(), gz.ravel(), N.ravel(), tr, rx, rz, rh, hw, 0.15, 3.5)
    H = H.reshape(gx.shape)
    _, j = tr.query(np.column_stack([gx.ravel(), gz.ravel()]))
    dmin = np.hypot(gx.ravel() - rx[j], gz.ravel() - rz[j]).reshape(gx.shape)
    on = (dmin < 3.0) & (gx > 10) & (gx < 190)
    assert np.abs(H[on] - (rh[j].reshape(gx.shape)[on] - 0.15)).max() < 0.5
    assert np.abs(np.diff(H, axis=0)).max() < 1.3 and np.abs(np.diff(H, axis=1)).max() < 1.3, "stetig, Böschung ≤ 1:1"
    # Rinnen: stetig, auf flachem Land null
    xs = np.linspace(0, 300, 3001)
    g = gullies(xs, np.zeros_like(xs), np.full_like(xs, 0.4), np.full_like(xs, 0.1), 3)
    assert np.abs(np.diff(g)).max() < 0.5 and np.abs(g).max() > 0.5
    assert np.abs(gullies(xs, xs, np.zeros_like(xs), np.zeros_like(xs), 3)).max() < 1e-9
    print("ok")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
