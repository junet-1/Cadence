"""Biome für route.py: Landbedeckung, Klima-Näherung, Bodenfarbe, Vegetation. Reines numpy, läuft nur beim Streckenbau.

Getrennte Dimensionen statt einer Biom-Enum:
  Landbedeckung  ESA WorldCover 2021 (global, ~20 m) als Grundlage, OSM-Flächen genauer darüber (route.py)
  Klima          Jahresmittel aus Breite + Höhe (Temperaturgradient), daraus Baumgrenze, Baumarten
  Trockenheit    Anteil Sand/Busch in ~1,5 km Umkreis (aus WorldCover)
  Jahreszeit     erst zur Laufzeit: main.gd rechnet aus meta.climate die Schneegrenze (Shader-Global snow_line)
WorldCover: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021), CC BY 4.0.
"""
import struct, urllib.error, urllib.request, zlib
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

CACHE = Path.home() / ".cache" / "kurbel" / "worldcover"
WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
WC_LEVEL = 1  # Übersicht 1 = 1/6000° ≈ 18 m, passt zum 20-m-Geländeraster
UA = {"User-Agent": "cadence/0.1 (private cycling sim)"}

# Landklassen (0–5 wie früher in route.py, damit alte Logik/Pakete passen)
OPEN, FARM, MEADOW, FOREST, URBAN, WATER, SCRUB, SAND, ROCK, SNOW, WETLAND, PAVING = range(12)
NONE = 255  # "nicht gemalt" im Klassenraster
GRASSY = [OPEN, FARM, MEADOW, WETLAND]
VEG = GRASSY + [FOREST, SCRUB]

WC_CLASS = np.zeros(256, np.uint8)  # WorldCover-Code -> Landklasse, 0 = keine Daten -> OPEN
for code, cls in {10: FOREST, 20: SCRUB, 30: MEADOW, 40: FARM, 50: URBAN, 60: SAND, 70: SNOW, 80: WATER,
                  90: WETLAND, 95: FOREST, 100: MEADOW}.items():
    WC_CLASS[code] = cls

# Bodenfarbe (sRGB) je Klasse: feucht, trocken, über der Baumgrenze. Ort = Gärten + Pflaster (Gras im Ort nimmt die Farbe an)
LUSH = np.array([(.42, .58, .30), (.66, .62, .36), (.40, .62, .28), (.17, .34, .16), (.47, .50, .40), (.25, .42, .62),
                 (.30, .40, .20), (.84, .72, .52), (.50, .48, .45), (.93, .95, .98), (.30, .42, .27), (.58, .56, .53)], np.float32)
DRY = LUSH.copy()
DRY[[OPEN, FARM, MEADOW, FOREST, SCRUB, WETLAND, URBAN]] = [(.74, .64, .42), (.78, .68, .46), (.72, .63, .40), (.36, .33, .20),
                                                            (.62, .53, .38), (.45, .48, .32), (.68, .62, .52)]
ALPINE = LUSH.copy()
ALPINE[VEG] = (.47, .53, .33)  # kurzes Bergras
ALPINE[SAND] = LUSH[ROCK]  # kahle Flächen oben = Geröll statt Sand
TROPIC = LUSH.copy()  # feucht-heiß: sattes Grün, Felder = Reis/Plantagen statt Stoppeln
TROPIC[[OPEN, FARM, MEADOW, FOREST, SCRUB, WETLAND]] = [(.34, .55, .22), (.36, .56, .20), (.32, .56, .20), (.12, .28, .10),
                                                        (.24, .40, .16), (.26, .44, .22)]
# Alpha der Bodentextur = Gras-Dichte im grass-Shader (255 Wiese, 230 Feld, 215 Ort, 200 Wald, 160 kahl), 128 = Wasser
GROW = np.array([230, 230, 255, 200, 215, 128, 200, 160, 160, 160, 230, 160], np.uint8)

TREE_DENSITY = np.array([.04, .04, .04, 3, .15, 0, .1, 0, 0, 0, .1, 0], np.float32)  # Bäume je Rasterzelle
SHRUB_DENSITY = np.array([0, 0, 0, 0, 0, 0, 2, .06, .02, 0, .2, 0], np.float32)  # Büsche je Rasterzelle

LAPSE = 0.0065  # K/m


def climate(lat, elev):
    """(Jahresmittel, wärmster Monat) in °C. ponytail: Breite+Höhe ignoriert Meer/Kontinent (±5 K); WorldClim-Raster wenn das stört."""
    lat = np.abs(lat)
    t = 29 - 0.0072 * lat ** 2 - LAPSE * elev
    return t, t + amplitude(lat)


def amplitude(lat):
    return np.clip(0.18 * np.abs(lat), 1, 15)  # halbe Jahresschwankung


def smooth(t):
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def alpine(t_warm):
    """0 unter, 1 über der Baumgrenze (wärmster Monat ~10 °C), weicher Übergang."""
    return smooth((11 - t_warm) / 3).astype(np.float32)


def aridity(cls, t_warm, cell):
    """0 feucht … 1 Wüste: Anteil Sand (Busch halb) an der Landfläche in ~1,5 km Umkreis. Geröll über der Baumgrenze zählt nicht."""
    dry = np.where(t_warm > 10, (cls == SAND) + 0.5 * (cls == SCRUB), 0).astype(np.float32)
    land = ((cls != WATER) & (cls != OPEN)).astype(np.float32)  # OPEN = keine WorldCover-Daten
    s = 1500 / cell
    frac = ndimage.gaussian_filter(dry, s) / np.maximum(ndimage.gaussian_filter(land, s), 1e-3)
    return np.clip(frac / 0.5, 0, 1).astype(np.float32)


def plant_density(land, arid, alp):
    """(Bäume, Büsche) je Rasterzelle. Trockenheit dünnt verstreute Bäume aus (beobachteten Wald kaum: Oasen), Baumgrenze alle."""
    trees = TREE_DENSITY[land] * (1 - np.where(land == FOREST, 0.3, 0.7) * arid) * (1 - alp)
    shrubs = (SHRUB_DENSITY[land] + np.isin(land, GRASSY) * 0.4 * arid) * (1 - 0.5 * alp)
    return trees, shrubs


def tree_species(t_mean, arid, forest, rng):
    """(Nadelbaum, Palme) je Baum: Nadel wo es kühl ist (im Wald 60 % bei ~9 °C), Palmen in den Tropen und in warmen Trockengebieten (Oasen)."""
    conifer = rng.random(len(t_mean)) < np.clip((15 - t_mean) / 10, .05, .95) * np.where(forest, 1, .3) * (t_mean < 22)  # Tropen: keine
    palm_p = np.maximum(np.clip((t_mean - 19) / 6, 0, .6), .9 * arid * np.clip((t_mean - 15) / 5, 0, 1))
    palm = ~conifer & (rng.random(len(t_mean)) < palm_p)
    return conifer, palm


def upsample(cls, size, scale, rng):
    """Klassenraster (Gitter) -> Texturgröße, nächster Nachbar an verrauschten Koordinaten: natürliche statt treppige Ränder."""
    h, w = cls.shape
    warp = []
    for _ in range(2):
        f = ndimage.gaussian_filter(rng.standard_normal((h, w)), 1.5)
        warp.append(np.asarray(Image.fromarray((f / (f.std() + 1e-9) * 0.7).astype(np.float32), "F").resize(size, Image.BILINEAR)))
    tw, th = size
    u = np.arange(tw) * scale
    out = np.empty((th, tw), np.uint8)
    for y0 in range(0, th, 1024):  # in Streifen, sonst ~1 GB für 4096² Pixel
        y1 = min(y0 + 1024, th)
        v = np.arange(y0, y1)[:, None] * scale
        ix = np.clip(np.rint(u[None, :] + warp[0][y0:y1]), 0, w - 1).astype(np.int32)
        iz = np.clip(np.rint(v + warp[1][y0:y1]), 0, h - 1).astype(np.int32)
        out[y0:y1] = cls[iz, ix]
    return out


def resize(a, size):
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize(size, Image.BILINEAR))


def tropic(t_mean):
    """0 gemäßigt … 1 tropisch (Jahresmittel 20 → 25 °C)."""
    return smooth((t_mean - 20) / 5).astype(np.float32)


def ground_rgba(cls, arid, alp, trop=None):
    """Bodentextur aus Klasse, Trockenheit, Baumgrenze, Tropenanteil (alle in Texturgröße) -> RGBA uint8 (A = Wachstumscode)."""
    out = np.empty((*cls.shape, 4), np.uint8)
    for y0 in range(0, cls.shape[0], 1024):
        c, a, k = cls[y0:y0 + 1024], arid[y0:y0 + 1024, :, None], alp[y0:y0 + 1024, :, None]
        lush = LUSH[c]
        if trop is not None:  # Tropen nur wo es feucht ist: trockene Tropen bleiben Savanne
            t = trop[y0:y0 + 1024, :, None]
            lush = lush * (1 - t) + TROPIC[c] * t
        rgb = lush * (1 - a) + DRY[c] * a
        rgb = rgb * (1 - k) + ALPINE[c] * k
        grow = GROW[c].copy()
        grassy, a2 = np.isin(c, GRASSY + [URBAN]), a[..., 0]  # Grünflächen im Ort trocknen mit aus
        grow[grassy & (a2 > .25)] = 215
        grow[grassy & (a2 > .4)] = 200
        grow[grassy & (a2 > .6)] = 160
        grow[np.isin(c, VEG) & (k[..., 0] > .5)] = 230  # über der Baumgrenze: kurze Bergwiese, auch statt Waldboden
        out[y0:y0 + 1024, :, :3] = np.clip(rgb * 255, 0, 255).astype(np.uint8)
        out[y0:y0 + 1024, :, 3] = grow
    return out


# --- ESA WorldCover: Cloud-Optimized GeoTIFF, nur die gebrauchten 1024er-Blöcke per HTTP-Range ---

def _get(url, first, last):
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={first}-{last}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _cache(f, get):
    """Datei aus dem Cache, sonst holen und atomar ablegen (abgebrochener Download hinterlässt nichts Kaputtes)."""
    if not f.exists():
        tmp = f.with_suffix(".part")
        tmp.write_bytes(get())
        tmp.replace(f)
    return f.read_bytes()


def _ifds(b):
    """TIFF-Verzeichnisse (little endian): je Ebene {256: Breite, 322: Blockgröße, 324: Offsets, 325: Längen}."""
    assert b[:4] == b"II*\x00", "kein Little-Endian-TIFF"
    out, off = [], struct.unpack_from("<I", b, 4)[0]
    while off:
        n = struct.unpack_from("<H", b, off)[0]
        tags = {}
        for k in range(n):
            tag, typ, cnt, val = struct.unpack_from("<HHII", b, off + 2 + 12 * k)
            if tag in (256, 322, 324, 325) and typ in (3, 4):
                f, size = ("H", 2) if typ == 3 else ("I", 4)
                tags[tag] = struct.unpack_from(f"<{cnt}{f}", b, off + 10 + 12 * k if size * cnt <= 4 else val)
        out.append(tags)
        off = struct.unpack_from("<I", b, off + 2 + 12 * n)[0]
    return out


def _tile_name(lon, lat):
    """3°-Kachel nach linker unterer Ecke, z.B. N48E006."""
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


class WorldCover:
    """Mosaik der WorldCover-Codes für ein lon/lat-Rechteck. Blöcke werden in ~/.cache/kurbel/worldcover gecacht."""
    def __init__(self, lon, lat, level=WC_LEVEL):
        self.px = 3 / (36000 >> level)  # Grad pro Pixel
        self.c0, self.r0 = int((lon.min() + 180) / self.px), int((90 - lat.max()) / self.px)
        c1, r1 = int((lon.max() + 180) / self.px) + 1, int((90 - lat.min()) / self.px) + 1
        self.a = np.zeros((r1 - self.r0, c1 - self.c0), np.uint8)
        CACHE.mkdir(parents=True, exist_ok=True)
        n = 36000 >> level  # Pixel je 3°-Kachel
        for tlat in range(int(np.floor(lat.min() / 3)) * 3, int(np.floor(lat.max() / 3)) * 3 + 3, 3):
            for tlon in range(int(np.floor(lon.min() / 3)) * 3, int(np.floor(lon.max() / 3)) * 3 + 3, 3):
                tr0, tc0 = round((90 - tlat - 3) / self.px), round((tlon + 180) / self.px)  # globale Pixel der Kachelecke
                self._tile(_tile_name(tlon, tlat), level, tr0, tc0)

    def _tile(self, name, level, tr0, tc0):
        url = WC_URL.format(name)

        def head():
            try:
                return _get(url, 0, 65535)
            except urllib.error.HTTPError as e:
                if e.code != 404: raise
                return b""  # keine Kachel = reines Meer/außerhalb -> bleibt 0

        hdr = _cache(CACHE / f"{name}.hdr", head)
        if not hdr:
            return
        ifd = _ifds(hdr)[level]
        n, bs = ifd[256][0], ifd[322][0]  # Kachelbreite in Pixeln, Blockgröße
        across = -(-n // bs)
        # nur Blöcke, die das Mosaik schneiden
        r_lo, r_hi = max(self.r0 - tr0, 0), min(self.r0 + self.a.shape[0] - tr0, n)
        c_lo, c_hi = max(self.c0 - tc0, 0), min(self.c0 + self.a.shape[1] - tc0, n)
        for br in range(r_lo // bs, (r_hi - 1) // bs + 1 if r_hi > r_lo else 0):
            for bc in range(c_lo // bs, (c_hi - 1) // bs + 1 if c_hi > c_lo else 0):
                i = br * across + bc
                data = _cache(CACHE / f"{name}_{level}_{i}.bin", lambda: _get(url, ifd[324][i], ifd[324][i] + ifd[325][i] - 1))
                block = np.frombuffer(zlib.decompress(data), np.uint8).reshape(bs, bs)
                # Randblöcke sind aufgefüllt: Füllung abschneiden, sonst überschreibt sie die Nachbarkachel
                self._paste(block[:n - br * bs, :n - bc * bs], tr0 + br * bs, tc0 + bc * bs)

    def _paste(self, block, gr, gc):
        r, c = gr - self.r0, gc - self.c0
        y0, x0 = max(r, 0), max(c, 0)
        y1, x1 = min(r + block.shape[0], self.a.shape[0]), min(c + block.shape[1], self.a.shape[1])
        if y1 > y0 and x1 > x0:
            self.a[y0:y1, x0:x1] = block[y0 - r:y1 - r, x0 - c:x1 - c]

    def at(self, lon, lat):
        c = np.clip(((lon + 180) / self.px).astype(int) - self.c0, 0, self.a.shape[1] - 1)
        r = np.clip(((90 - lat) / self.px).astype(int) - self.r0, 0, self.a.shape[0] - 1)
        return self.a[r, c]


def test():
    rng = lambda: np.random.default_rng(5)
    g = (40, 40)
    flat = lambda v: np.full(g, v, np.float32)

    def scene(wc_codes, lat, elev):
        cls = WC_CLASS[wc_codes]
        t, tw = climate(flat(lat), flat(elev))
        a, k = aridity(cls, tw, 20.0), alpine(tw)
        rgba = ground_rgba(cls, a, k)
        trees, shrubs = plant_density(cls, a, k)
        return cls, t, a, k, rgba, trees, shrubs

    # Wüste (Tozeur, 34°N): Sand, voll trocken, sandfarben, kein Gras, fast keine Bäume, Büsche, Palmen möglich
    wc = np.full(g, 60, np.uint8); wc[::7, ::7] = 20
    cls, t, a, k, rgba, trees, shrubs = scene(wc, 34, 50)
    assert (cls[1:, 1:] == SAND).mean() > .9 and a.min() > .9 and k.max() == 0
    r, gg, b = rgba[5, 5, :3].astype(int)
    assert r > gg > b and r > 180 and rgba[5, 5, 3] == 160, rgba[5, 5]
    assert trees.max() < .05 and shrubs.sum() > 1
    con, palm = tree_species(np.full(1000, 26.0), np.zeros(1000), np.ones(1000, bool), rng())
    assert palm.mean() > .4 and con.sum() == 0  # tropisch: Palmen, keine Nadelbäume
    assert tree_species(np.full(1000, 20.0), np.ones(1000), np.ones(1000, bool), rng())[1].mean() > .7  # Oase: Dattelpalmen
    assert np.allclose(plant_density(np.full(g, FOREST), np.ones(g, np.float32), np.zeros(g, np.float32))[0], 2.1)  # Oasenwald bleibt

    # Gemäßigter Wald (Taunus, 50°N, 400 m): Wald, feucht, grün, dicht, ~60 % Nadel
    cls, t, a, k, rgba, trees, shrubs = scene(np.full(g, 10, np.uint8), 50.2, 400)
    assert (cls == FOREST).all() and a.max() == 0 and k.max() == 0 and trees.min() == 3
    r, gg, b = rgba[5, 5, :3].astype(int)
    assert gg > r and gg > b and rgba[5, 5, 3] == 200
    con, palm = tree_species(np.full(4000, t[0, 0]), np.zeros(4000), np.ones(4000, bool), rng())
    assert palm.sum() == 0
    con = con.mean()
    assert .5 < con < .8, con

    # Alpin (Stilfser Joch, 46.5°N, 2700 m): über der Baumgrenze, keine Bäume, Geröll grau statt Sand
    wc = np.full(g, 30, np.uint8); wc[:, 20:] = 60
    cls, t, a, k, rgba, trees, shrubs = scene(wc, 46.5, 2700)
    assert k.min() > .99 and trees.max() == 0 and a.max() == 0  # Geröll oben macht nicht "trocken"
    rock = rgba[5, 30, :3].astype(int)
    assert abs(rock[0] - rock[2]) < 20, rock  # grau
    assert rgba[5, 5, 3] == 230  # Bergwiese hat Gras

    # Großstadt: bebaut, Ort-Wachstumscode, vereinzelt Bäume
    cls, t, a, k, rgba, trees, shrubs = scene(np.full(g, 50, np.uint8), 48.1, 520)
    assert (cls == URBAN).all() and rgba[5, 5, 3] == 215 and np.allclose(trees, .15)
    assert ground_rgba(np.full(g, URBAN, np.uint8), np.ones(g, np.float32), np.zeros(g, np.float32))[5, 5, 3] == 160  # Wüstenstadt

    # Agrarland gemischt: Felder/Wiesen/Waldstücke, feucht
    wc = np.full(g, 40, np.uint8); wc[:20, :20] = 30; wc[30:, 30:] = 10
    cls, t, a, k, rgba, trees, shrubs = scene(wc, 51, 120)
    assert set(np.unique(cls)) == {FARM, MEADOW, FOREST} and a.max() < .05 and rgba[25, 5, 3] == 230

    # Keine WorldCover-Daten (offline/Meer): alles OPEN mit der alten Standardfarbe, nichts trocken
    cls, t, a, k, rgba, trees, shrubs = scene(np.zeros(g, np.uint8), 50, 200)
    assert (cls == OPEN).all() and a.max() == 0 and (rgba[..., 3] == 230).all()
    assert tuple(rgba[0, 0, :3]) == tuple(np.clip(LUSH[OPEN] * 255, 0, 255).astype(np.uint8))

    # Tropen (Bali, 26 °C): Felder grün statt Stoppelgelb; trockene Tropen (Savanne) bleiben gelb
    farm = np.full(g, FARM, np.uint8)
    wet = ground_rgba(farm, np.zeros(g, np.float32), np.zeros(g, np.float32), tropic(flat(26)))[5, 5, :3].astype(int)
    dry = ground_rgba(farm, np.ones(g, np.float32), np.zeros(g, np.float32), tropic(flat(26)))[5, 5, :3].astype(int)
    assert wet[1] > wet[0] + 40 and dry[0] > dry[1] > dry[2], (wet, dry)

    # Deterministisch: gleiche Eingabe + Seed -> gleiche Pixel; Ränder verrauscht, aber nur Klassen aus der Quelle
    src = np.zeros(g, np.uint8); src[:, 20:] = FOREST
    up1, up2 = upsample(src, (400, 400), 0.1, rng()), upsample(src, (400, 400), 0.1, rng())
    assert (up1 == up2).all() and set(np.unique(up1)) == {OPEN, FOREST}
    edge = up1[:, 180:220]
    assert 0 < (edge == FOREST).mean() < 1 and len({tuple(r) for r in edge}) > 10  # kein gerader Strich

    # TIFF-Parser an einer Minidatei: Breite 2048, Block 1024, 4 Offsets/Längen
    ifd_at = 8
    entries = [(256, 3, 1, 2048), (322, 3, 1, 1024), (324, 4, 4, 100), (325, 4, 4, 116)]
    b = bytearray(b"II*\x00" + struct.pack("<I", ifd_at) + struct.pack("<H", len(entries)))
    for e in entries: b += struct.pack("<HHII", *e)
    b += struct.pack("<I", 0)
    b += bytes(100 - len(b)) + struct.pack("<4I", 1, 2, 3, 4) + struct.pack("<4I", 5, 6, 7, 8)
    ifd = _ifds(bytes(b))[0]
    assert ifd[256] == (2048,) and ifd[322] == (1024,) and ifd[324] == (1, 2, 3, 4) and ifd[325] == (5, 6, 7, 8)
    assert _tile_name(6, 48) == "N48E006" and _tile_name(-78, -12) == "S12W078"
    # Einfügen eines Blocks am Mosaikrand wird korrekt beschnitten
    wcv = WorldCover.__new__(WorldCover)
    wcv.r0, wcv.c0, wcv.a = 10, 10, np.zeros((4, 4), np.uint8)
    wcv._paste(np.arange(36, dtype=np.uint8).reshape(6, 6), 8, 8)
    assert (wcv.a[:4, :4] == np.arange(36).reshape(6, 6)[2:6, 2:6]).all()
    # Ganze Kachel offline: 1500 px breit, 1024er-Blöcke -> Randblöcke aufgefüllt; Füllung darf die Nachbarkachel nicht überschreiben
    global CACHE, _get
    import tempfile
    blocks = [zlib.compress(np.full((1024, 1024), 10 * (k + 1), np.uint8).tobytes()) for k in range(4)]
    off = [70000 + sum(map(len, blocks[:k])) for k in range(4)]
    hdr = bytearray(b"II*\x00" + struct.pack("<IH", 8, 4))
    for e in [(256, 3, 1, 1500), (322, 3, 1, 1024), (324, 4, 4, 100), (325, 4, 4, 116)]: hdr += struct.pack("<HHII", *e)
    hdr += struct.pack("<I", 0) + bytes(100 - 62) + struct.pack("<4I", *off) + struct.pack("<4I", *map(len, blocks))
    blob = bytes(hdr).ljust(70000, b"\0") + b"".join(blocks)
    saved = CACHE, _get
    CACHE, _get = Path(tempfile.mkdtemp()), lambda url, a, b: blob[a:b + 1]
    try:
        wcv.r0, wcv.c0, wcv.a = 0, 0, np.full((3000, 1500), 99, np.uint8)  # zwei Kacheln übereinander, untere schon "gefüllt"
        wcv._tile("N00E000", 0, 0, 0)
        assert wcv.a[0, 0] == 10 and wcv.a[0, 1499] == 20 and wcv.a[1499, 0] == 30 and wcv.a[1499, 1499] == 40
        assert (wcv.a[1500:] == 99).all()  # Füllung der oberen Kachel landet nicht in der unteren
        assert len(list(CACHE.glob("*.part"))) == 0 and len(list(CACHE.glob("*.bin"))) == 4
    finally:
        CACHE, _get = saved
    print("biome ok")
