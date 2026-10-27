# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "pillow", "mapbox-earcut"]
# ///
"""Baut ein Strecken-Paket für Kurbel aus GPX oder BRouter-Wegpunkten.

  uv run route.py taunus --via "8.57,50.20|8.458,50.232|8.47,50.18"   (lon,lat; BRouter plant die Route)
  uv run route.py hausrunde --gpx ~/Downloads/runde.gpx               (z.B. Komoot-Export)

Quellen: BRouter (Routing), AWS Terrain Tiles (Höhe), Overpass/OSM (Flächen, Gebäude), ESA WorldCover (Landbedeckung, biome.py).
Ausgabe: routes/<name>/ mit Binärdateien, die main.gd direkt in Meshes lädt.
"""
import argparse, hashlib, io, json, math, sys, time, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

import mapbox_earcut, numpy as np
from PIL import Image, ImageColor, ImageDraw
from scipy import ndimage
from scipy.spatial import Delaunay, cKDTree

import biome
import network
import regions
import road
import signs
from biome import FARM, FOREST, MEADOW, NONE, PAVING, ROCK, SAND, SCRUB, SNOW, URBAN, WATER, WETLAND

UA = {"User-Agent": "kurbel/0.1 (private cycling sim)"}
CACHE = Path.home() / ".cache" / "kurbel"
ZOOM = 13  # Terrarium ~12 m/px bei 50°N
STEP = 5.0  # m zwischen Routenpunkten
TREES_UP_TO = 500.0  # m Abstand zur Straße
SINK = 0.15  # m Gelände unter der Fahrbahn (road_terrain/fit_terrain); die Asphaltkante deckt die Fuge ab

# OSM-Flächen als Landklassen (biome.py), später gemalt gewinnt. Farben/Gras/Bäume je Klasse stehen in biome.py.
DRAW_ORDER = [FARM, MEADOW, SCRUB, SAND, ROCK, WETLAND, URBAN, FOREST, SNOW, WATER]
ROADS = network.ROADS  # highway -> (halbe Breite, Farbe, Markierung, Höhe übers Gelände)
GABLE = {"house", "detached", "semidetached_house", "terrace", "yes", "residential", "farm", "farm_auxiliary", "barn", "bungalow",
         "cabin", "hut", "stable", "church", "chapel"}
GRAY = {"industrial", "commercial", "retail", "warehouse", "office", "supermarket", "garages", "garage", "parking", "school", "university"}
# Blender-Modelle (blender/houses.py): Name -> (Länge, Tiefe, Geschosse). Instanzen werden auf die Grundrissbox gestreckt.
MODELS = {"efh_sattel": (11, 9, 2), "efh_walm": (10.5, 10, 2), "siedlung": (12, 8.5, 1), "fachwerk": (11, 8, 2), "mfh": (18, 11, 3),
          "stadthaus": (12, 7, 3), "block": (30, 12, 5), "halle": (40, 24, 0), "garage": (6, 3.5, 0), "scheune": (20, 12, 0), "kirche": (24, 12, 0),
          # regionale Modelle, Auswahl über regions.STYLES
          "chalet": (12, 10, 2), "holzhaus": (10, 7, 1), "mediterran": (11, 9, 2), "flachdach": (10, 10, 2), "suburban": (13, 9, 2),
          "ranch": (16, 9, 1), "beton": (10, 8, 2), "tropenhaus": (10, 8, 1), "wellblech": (8, 6, 1), "rundhuette": (6, 6, 1),
          "ostasien": (12, 9, 2)}
TAGS = {
    "landuse": {"farmland": FARM, "orchard": FARM, "vineyard": FARM, "meadow": MEADOW, "grass": MEADOW, "village_green": MEADOW,
                "recreation_ground": MEADOW, "allotments": MEADOW, "forest": FOREST, "residential": URBAN, "commercial": URBAN,
                "industrial": URBAN, "retail": URBAN, "reservoir": WATER, "basin": WATER},
    "natural": {"wood": FOREST, "scrub": SCRUB, "grassland": MEADOW, "heath": SCRUB, "water": WATER, "wetland": WETLAND,
                "sand": SAND, "beach": SAND, "bare_rock": ROCK, "scree": ROCK, "glacier": SNOW},
    "leisure": {"park": MEADOW, "golf_course": MEADOW, "pitch": MEADOW},
}


def fetch(url, data=None, tries=4, timeout=300):
    """HTTP mit Wiederholung: Overpass und BRouter sind öfter überlastet (429/502/503/504) oder kurz weg."""
    for k in range(tries):
        req = urllib.request.Request(url, data=data and urllib.parse.urlencode(data).encode(), headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or k == tries - 1:
                raise
        except OSError:  # URLError, Timeout, Verbindung abgebrochen
            if k == tries - 1:
                raise
        print(f"Server ausgelastet, neuer Versuch in {5 * 2 ** k} s …")
        time.sleep(5 * 2 ** k)


def gpx_lonlat(xml):
    pts = [(float(p.get("lon")), float(p.get("lat"))) for p in ET.fromstring(xml).iter() if p.tag.endswith(("trkpt", "rtept"))]
    if len(pts) < 2: sys.exit("GPX enthält keine Punkte")
    return np.array(pts)


def brouter(via):
    q = urllib.parse.urlencode({"lonlats": via, "profile": "fastbike", "alternativeidx": 0, "format": "gpx"})
    return gpx_lonlat(fetch(f"https://brouter.de/brouter?{q}"))


class Proj:
    """Lokale Meter um den Routenmittelpunkt: x = Ost, z = Süd (Godot: -Z ist Norden)."""
    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.ky = 111320.0
        self.kx = 111320.0 * math.cos(math.radians(lat0))

    def to_xz(self, lon, lat): return (lon - self.lon0) * self.kx, -(lat - self.lat0) * self.ky
    def to_lonlat(self, x, z): return self.lon0 + x / self.kx, self.lat0 - z / self.ky


def mercator_px(lon, lat):
    n = 256 * 2 ** ZOOM
    lat = np.radians(lat)
    return (lon + 180) / 360 * n, (1 - np.log(np.tan(lat) + 1 / np.cos(lat)) / math.pi) / 2 * n


def bilinear(a, fx, fy):
    x0 = np.clip(np.floor(fx).astype(int), 0, a.shape[1] - 2)
    y0 = np.clip(np.floor(fy).astype(int), 0, a.shape[0] - 2)
    tx, ty = np.clip(fx - x0, 0, 1), np.clip(fy - y0, 0, 1)
    top = a[y0, x0] * (1 - tx) + a[y0, x0 + 1] * tx
    bot = a[y0 + 1, x0] * (1 - tx) + a[y0 + 1, x0 + 1] * tx
    return top * (1 - ty) + bot * ty


class Dem:
    """Mosaik aus Terrarium-Kacheln, Höhe in m = R*256 + G + B/256 - 32768."""
    def __init__(self, lon, lat):
        px, py = mercator_px(lon, lat)
        self.tx0, self.ty0 = int(px.min() // 256), int(py.min() // 256)
        tx1, ty1 = int(px.max() // 256), int(py.max() // 256)
        self.h = np.zeros(((ty1 - self.ty0 + 1) * 256, (tx1 - self.tx0 + 1) * 256), np.float32)
        (CACHE / "tiles").mkdir(parents=True, exist_ok=True)
        for ty in range(self.ty0, ty1 + 1):
            for tx in range(self.tx0, tx1 + 1):
                f = CACHE / "tiles" / f"{ZOOM}_{tx}_{ty}.png"
                if not f.exists():
                    f.write_bytes(fetch(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{tx}/{ty}.png"))
                rgb = np.asarray(Image.open(f).convert("RGB"), np.float32)
                y, x = (ty - self.ty0) * 256, (tx - self.tx0) * 256
                self.h[y:y + 256, x:x + 256] = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768

    def at(self, lon, lat):
        px, py = mercator_px(lon, lat)
        return bilinear(self.h, px - self.tx0 * 256 - 0.5, py - self.ty0 * 256 - 0.5)


def overpass(bbox, line):
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    q = f"""[out:json][timeout:240];(
      way["landuse"]({b}); relation["landuse"]({b});
      way["natural"~"^(wood|scrub|water|grassland|heath|wetland|sand|beach|bare_rock|scree|glacier)$"]({b});
      relation["natural"~"^(wood|water|wetland|sand|bare_rock|scree|glacier)$"]({b});
      way["leisure"~"^(park|golf_course|pitch)$"]({b});
      way["building"](around:300,{line});
      way["highway"](around:600,{line});
      way["amenity"="parking"](around:600,{line}); way["place"="square"](around:600,{line});
    ); out geom;"""
    return overpass_query(q, "Lade OSM-Daten (dauert bis zu 1-2 min) …")


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


def overpass_query(q, msg, tries=4, timeout=300):
    f = CACHE / "osm" / f"{hashlib.sha1(q.encode()).hexdigest()[:16]}.json"
    if not f.exists():
        print(msg)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".part")
        for k, url in enumerate(OVERPASS):  # Hauptserver einmal, dann Spiegel mit Wiederholung (gleiche Daten, gleicher Cache)
            try:
                body = fetch(url, {"data": q}, 1 if k == 0 else tries, timeout)
                break
            except OSError as e:  # HTTPError/URLError: abgelehnt, überlastet, gesperrt
                if k == len(OVERPASS) - 1:
                    raise
                print(f"{url.split('/')[2]} nicht erreichbar ({e}), versuche {OVERPASS[k + 1].split('/')[2]} …")
        tmp.write_bytes(body)
        tmp.replace(f)  # abgebrochener Download hinterlässt keinen kaputten Cache
    return json.loads(f.read_bytes())


def route_nodes(proj, rx, rz):
    """OSM-Knoten bis 40 m um die Route für signs.py: Ampeln, Stopp/Vorfahrt, Übergänge, Ortstafeln, Wegweiser."""
    lo, la = proj.to_lonlat(rx[::10], rz[::10])
    line = ",".join(f"{a:.5f},{o:.5f}" for o, a in zip(lo, la))
    q = f"""[out:json][timeout:180];(
      node["highway"~"^(traffic_signals|stop|give_way|crossing)$"](around:40,{line});
      node["traffic_sign"](around:40,{line}); node["information"="guidepost"](around:40,{line});
    ); out body;"""
    return overpass_query(q, "Lade Schilder/Ampeln …")["elements"]


def route_rails(proj, rx, rz):
    """OSM-Schutzplanken (barrier=guard_rail) bis 25 m um die Route als [(x, z)] für signs.roadside."""
    lo, la = proj.to_lonlat(rx[::10], rz[::10])
    line = ",".join(f"{a:.5f},{o:.5f}" for o, a in zip(lo, la))
    els = overpass_query(f'[out:json][timeout:180];way["barrier"="guard_rail"](around:25,{line});out geom;', "Lade Schutzplanken …",
                         tries=2, timeout=120)["elements"]
    return [proj.to_xz(np.array([p["lon"] for p in e["geometry"] if p]), np.array([p["lat"] for p in e["geometry"] if p]))
            for e in els if e.get("geometry")]


def route_ways(proj, rx, rz, quick=False):
    """Alle Straßen/Wege bis 40 m um die Route als [(x, z, tags)] – für road.match_ways, bei Bau und Vorschau gleich.
    quick: ein Versuch mit kurzem Timeout (Vorschau soll nicht minutenlang hängen)."""
    lo, la = proj.to_lonlat(rx[::10], rz[::10])
    line = ",".join(f"{a:.5f},{o:.5f}" for o, a in zip(lo, la))
    els = overpass_query(f'[out:json][timeout:180];way["highway"](around:40,{line});out geom;', "Lade Straßendaten …",
                         tries=1 if quick else 4, timeout=45 if quick else 300)["elements"]
    out = []
    for el in els:
        if len(el.get("geometry", [])) > 1:
            a = np.array([(p["lon"], p["lat"]) for p in el["geometry"]])
            out.append((*proj.to_xz(a[:, 0], a[:, 1]), el.get("tags", {})))
    return out


def rings(el):
    """Polygonringe als lon/lat-Listen. Relationen: äußere Wege an den Enden zusammensetzen."""
    if el["type"] == "way":
        return [[(p["lon"], p["lat"]) for p in el["geometry"]]], []
    parts = {"outer": [], "inner": []}
    for m in el.get("members", []):
        if m["type"] == "way" and "geometry" in m and m.get("role") in parts:
            parts[m["role"]].append([(p["lon"], p["lat"]) for p in m["geometry"]])
    return stitch(parts["outer"]), stitch(parts["inner"])


def stitch(ways):
    out, ways = [], [w for w in ways if len(w) > 1]
    while ways:
        ring = ways.pop()
        while ring[0] != ring[-1]:
            for i, w in enumerate(ways):
                if w[0] == ring[-1]: ring += w[1:]
                elif w[-1] == ring[-1]: ring += w[-2::-1]
                else: continue
                ways.pop(i); break
            else:
                break  # offener Ring (Teil außerhalb der Abfrage) -> ImageDraw schließt ihn einfach
        out.append(ring)
    return out


def normals(pos, idx):
    n = np.zeros_like(pos)
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    f = np.cross(b - a, c - a)
    for k in range(3): np.add.at(n, idx[:, k], f)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    return n


def save_mesh(out, name, pos, nrm, idx, col=None, uv=None, uv2=None, c0=None):
    # Dreiecke an den Normalen ausrichten, dann umdrehen: Godot will Front-Faces im Uhrzeigersinn
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    flip = np.einsum("ij,ij->i", np.cross(b - a, c - a), nrm[idx].sum(axis=1)) > 0
    idx = np.where(flip[:, None], idx[:, [0, 2, 1]], idx)
    pos.astype(np.float32).tofile(out / f"{name}.pos")
    nrm.astype(np.float32).tofile(out / f"{name}.nrm")
    idx.astype(np.int32).tofile(out / f"{name}.idx")
    if col is not None:
        col = np.array(col, np.float64)
        if col.shape[1] == 3: col = np.column_stack([col, np.ones(len(col))])
        col[:, :3] **= 2.2  # sRGB -> linear, die Shader nehmen COLOR direkt
        col.astype(np.float32).tofile(out / f"{name}.col")
    for arr, ext in ((uv, "uv"), (uv2, "uv2"), (c0, "c0")):  # c0: 4 floats je Vertex -> Mesh CUSTOM0
        if arr is not None: np.asarray(arr, np.float32).tofile(out / f"{name}.{ext}")


def up(n):
    n[n[:, 1] < 0] *= -1
    return n


def tag_color(tags, key, fallback):
    try:
        return tuple(v / 255 for v in ImageColor.getrgb(tags[key].strip()))[:3]
    except (KeyError, ValueError):
        return fallback


def clip_half(x, z, d, sign):
    """Polygon (mit eingefügten Firstpunkten, d=0) auf eine Seite des Firsts beschneiden."""
    keep = sign * d >= -1e-6
    return x[keep], z[keep], d[keep]


def building(tags, x, z, ground, rng, style=regions.STYLES["mitteleuropa"]):
    """Wände (mit Fenster-UVs) und Dach eines Gebäudes. Kleine Wohnhäuser bekommen ein Satteldach entlang der Längsachse,
    wo der regionale Stil keins kennt (Trockengebiete, Lateinamerika, Südasien) ein Flachdach. Farben aus dem Stil."""
    flat = not style["gable"]
    gmin, gmax = ground(x, z).min(), ground(x, z).max()
    y0 = gmin - 1.0
    kind = tags["building"]
    area = abs(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z)) / 2
    shape = tags.get("roof:shape", "")
    levels = tags.get("building:levels", "")
    levels = float(levels) if levels.replace(".", "").isdigit() else None
    gable = shape in ("gabled", "hipped", "half-hipped", "pitched") or (not flat and not shape and kind in GABLE and area < 350 and (levels or 2) <= 4)

    # Firstrichtung = längere Seite der kleinsten umschließenden Box
    c, s_, lu, lv, cu, cv = obb(x, z)
    sd = (lambda x, z: -x * s_ + z * c - cv) if lu >= lv else (lambda x, z: x * c + z * s_ - cu)
    half = max(min(lu, lv) / 2, 0.5)
    roof_h = min(half * math.tan(math.radians(40)), 7.0) if gable else 0.0

    try:
        total = float(tags.get("height", "").replace("m", "").strip())
        walls_h = max(total - roof_h, 2.5)
    except ValueError:
        walls_h = levels * 3 + 0.5 if levels else (2.8 if kind in ("garage", "garages", "shed") else 3.6 if flat and area < 150
                                                   else 6.5 if gable or flat else 8.0)
    eave = gmax + walls_h

    # Firstpunkte in den Umriss einfügen, damit Dach und Giebelwände exakt passen
    d = sd(x, z)
    X, Z, D = [], [], []
    for k in range(len(x)):
        j = (k + 1) % len(x)
        X.append(x[k]); Z.append(z[k]); D.append(d[k])
        if gable and d[k] * d[j] < 0:
            t = d[k] / (d[k] - d[j])
            X.append(x[k] + t * (x[j] - x[k])); Z.append(z[k] + t * (z[j] - z[k])); D.append(0.0)
    x, z, d = np.array(X), np.array(Z), np.array(D)
    top = eave + roof_h * (1 - np.clip(np.abs(d) / half, 0, 1))

    gray = kind in GRAY
    walls_, roofs_ = style["walls"], style["roofs"]
    wall = tag_color(tags, "building:colour", (.72, .73, .74) if gray else walls_[rng.integers(len(walls_))])
    roof = tag_color(tags, "roof:colour", roofs_[rng.integers(len(roofs_))] if gable else (.70, .66, .58) if flat else (.38, .38, .40))
    seed = rng.random()

    n = len(x)
    j = (np.arange(n) + 1) % n
    ex, ez = x[j] - x, z[j] - z
    el = np.hypot(ex, ez) + 1e-9
    ccw = np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z) > 0
    nx, nz = (ez / el, -ex / el) if ccw else (-ez / el, ex / el)
    perim = np.concatenate([[0], np.cumsum(el)])[:-1]
    wp = np.stack([np.column_stack([x, np.full(n, y0), z]), np.column_stack([x[j], np.full(n, y0), z[j]]),
                   np.column_stack([x, top, z]), np.column_stack([x[j], top[j], z[j]])], 1).reshape(-1, 3)
    wn = np.repeat(np.column_stack([nx, np.zeros(n), nz]), 4, axis=0)
    wuv = np.stack([np.column_stack([perim, np.zeros(n)]), np.column_stack([perim + el, np.zeros(n)]),
                    np.column_stack([perim, top - y0]), np.column_stack([perim + el, top[j] - y0])], 1).reshape(-1, 2)
    q = 4 * np.arange(n)
    walls = (wp, wn, np.column_stack([q, q + 1, q + 2, q + 1, q + 3, q + 2]).reshape(-1, 3),
             np.tile([*wall, seed], (4 * n, 1)), wuv, np.tile([gmax - y0, eave - y0], (4 * n, 1)))

    rp, ri = [], []
    for hx, hz, hd in ([clip_half(x, z, d, 1), clip_half(x, z, d, -1)] if gable else [(x, z, d)]):
        if len(hx) < 3: continue
        tri = np.asarray(mapbox_earcut.triangulate_float64(np.column_stack([hx, hz]), np.array([len(hx)], np.uint32))).reshape(-1, 3)
        ri.append(tri + sum(len(p) for p in rp))
        rp.append(np.column_stack([hx, eave + roof_h * (1 - np.clip(np.abs(hd) / half, 0, 1)), hz]))
    if not rp: return walls, None
    rpos, ridx = np.vstack(rp), np.vstack(ri)
    return walls, (rpos, up(normals(rpos, ridx)), ridx, np.tile(roof, (len(rpos), 1)))


def obb(x, z):
    """Kleinste umschließende Box über alle Kantenrichtungen: (cos, sin, Ausdehnung u, Ausdehnung v, Mitte u, Mitte v)."""
    best = None
    for k in range(len(x)):
        j = (k + 1) % len(x)
        a = math.atan2(z[j] - z[k], x[j] - x[k])
        c, s_ = math.cos(a), math.sin(a)
        u, v = x * c + z * s_, -x * s_ + z * c
        if best is None or np.ptp(u) * np.ptp(v) < best[0]:
            best = (np.ptp(u) * np.ptp(v), c, s_, np.ptp(u), np.ptp(v), (u.min() + u.max()) / 2, (v.min() + v.max()) / 2)
    return best[1:]


def pick_model(tags, area, short, rng, style=regions.STYLES["mitteleuropa"]):
    """Haustyp nach OSM-Tags, Fläche, Geschossen und regionalem Stil. None = prozedural bauen."""
    kind = tags["building"]
    lv = tags.get("building:levels", "")
    lv = float(lv) if lv.replace(".", "").isdigit() else 0
    if kind in ("church", "chapel", "cathedral"): return "kirche" if style["church"] else None
    if kind in ("garage", "garages", "carport", "shed"): return "garage"
    if kind in ("barn", "farm_auxiliary", "stable", "cowshed"): return "scheune" if style["barn"] else None
    if kind in ("industrial", "warehouse", "manufacture", "commercial", "retail", "supermarket", "hangar"):
        return "halle" if area > 250 else None
    if kind in ("school", "university", "hospital", "public", "civic", "office", "hotel", "kindergarten"):
        return "block" if area > 350 else style["apartments"]
    if kind in ("house", "detached", "semidetached_house", "terrace", "residential", "apartments", "yes", "farm", "bungalow", "hut"):
        if area < 40: return style.get("small", "garage")
        if lv >= 5 or area > 700: return "block"
        if lv >= 3 or area > 220: return ("stadthaus" if short < 9 else "mfh") if style["apartments"] == "mfh" else style["apartments"]
        return rng.choice(list(style["houses"]), p=list(style["houses"].values()))
    return None


def place_model(tags, x, z, ground, rng, style=regions.STYLES["mitteleuropa"]):
    """(Modellname, 20 Floats für den MultiMesh-Buffer) oder None, wenn der Grundriss nicht zu einem Modell passt."""
    c, s_, lu, lv, cu, cv = obb(x, z)
    area = abs(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z)) / 2
    if lu * lv <= 0 or area / (lu * lv) < 0.75:  # L-Formen, Höfe … -> prozedural
        return None
    long_, short = max(lu, lv), min(lu, lv)
    name = pick_model(tags, area, short, rng, style)
    if not name: return None
    L, D, floors = MODELS[name]
    sx, sz = long_ / L, short / D
    lim = (0.4, 2.5) if name in ("halle", "block", "garage") else (0.6, 1.7)
    if not (lim[0] <= sx <= lim[1] and lim[0] <= sz <= lim[1]):
        return None
    lv_ = tags.get("building:levels", "")
    sy = min(max(float(lv_) / floors, 0.67), 2.0) if floors > 1 and lv_.replace(".", "").isdigit() else 1.0
    ax, az = (c, s_) if lu >= lv else (-s_, c)
    if rng.random() < 0.5: ax, az = -ax, -az  # Vorder-/Rückseite zufällig
    ox, oz = cu * c - cv * s_, cu * s_ + cv * c
    oy = ground(x, z).max() - 0.15
    walls_, roofs_ = style["walls"], style["roofs"]
    wall = tag_color(tags, "building:colour", (.72, .73, .74) if name == "halle" else walls_[rng.integers(len(walls_))])
    roof = tag_color(tags, "roof:colour", roofs_[rng.integers(len(roofs_))])
    lin = lambda col: [v ** 2.2 for v in col]
    # Godot-MultiMesh: Transform zeilenweise (Spalten = Basisvektoren X=Länge, Y=oben, Z=Tiefe), dann Farbe, dann Custom
    row = [ax * sx, 0, -az * sz, ox, 0, sy, 0, oy, az * sx, 0, ax * sz, oz, *lin(wall), rng.random(), *lin(roof), 0]
    return name, row


def blocks_road(x, z, rtree, rhw, margin=-0.3):
    """Ragt der Grundriss (geschlossener Umriss x, z) in unsere Fahrbahn? Umriss alle 1 m gegen die Routenpunkte prüfen.
    margin < 0: Häuser direkt am Fahrbahnrand (enge Dörfer) bleiben, nur echte Überlappung fliegt raus."""
    ex, ez = np.r_[x, x[:1]], np.r_[z, z[:1]]
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(ex), np.diff(ez)))])
    s = np.linspace(0, d[-1], max(4, int(d[-1]) + 1))
    dist, j = rtree.query(np.column_stack([np.interp(s, d, ex), np.interp(s, d, ez)]))
    return bool((dist < rhw[j] + margin).any())


def merge(parts):
    """Liste von (pos, nrm, idx, *extras) zu einem Mesh zusammenfassen."""
    out, o = [[] for _ in parts[0]], 0
    for p in parts:
        for k, a in enumerate(p):
            out[k].append(a + o if k == 2 else a)
        o += len(p[0])
    return [np.vstack(a) for a in out]


def instances(path, x, y, z, s):
    """MultiMesh-Buffer: 3x4 Transform zeilenweise, nur Skalierung."""
    zero = np.zeros_like(s)
    np.column_stack([s, zero, zero, x, zero, s, zero, y, zero, zero, s, z]).astype(np.float32).tofile(path)


def resample(lonlat, proj):
    x, z = proj.to_xz(lonlat[:, 0], lonlat[:, 1])
    seg = np.hypot(np.diff(x), np.diff(z))
    keep = np.concatenate([[True], seg > 0.01])  # doppelte Punkte raus, sonst bricht interp
    x, z = x[keep], z[keep]
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
    s = np.arange(0, d[-1], STEP)
    return np.interp(s, d, x), np.interp(s, d, z)


def tri_height(terrain, gx0, gz0, cell, x, z):
    """Höhe exakt auf der Dreiecksfläche des Terrain-Meshes (gleiche Diagonale wie dessen idx)."""
    H, W = terrain.shape
    fx, fz = (x - gx0) / cell, (z - gz0) / cell
    i = np.clip(np.floor(fx).astype(int), 0, W - 2)
    j = np.clip(np.floor(fz).astype(int), 0, H - 2)
    tx, tz = np.clip(fx - i, 0, 1), np.clip(fz - j, 0, 1)
    h00, h10, h01, h11 = terrain[j, i], terrain[j, i + 1], terrain[j + 1, i], terrain[j + 1, i + 1]
    return np.where(tx + tz <= 1, h00 + tx * (h10 - h00) + tz * (h01 - h00), h11 + (1 - tx) * (h01 - h11) + (1 - tz) * (h10 - h11))


def fit_terrain(dem, gx, gz, rx, rz, rh, cell, bridge=None, hw=3.0):
    """Gelände an die Straße anpassen -> (Gelände, Abstand zur Straße in m, nächster Routenpunkt).
    Nahe der Straße: gewichtetes Mittel der nächsten Routenpunkte (zwischen Serpentinen-Ästen eine Rampe statt einer Stufe),
    sanft zurück zum DEM, dahinter höchstens 45° Böschung. Brücken heben das Gelände nicht an (sonst Damm statt Tal).
    Danach exakt: jedes Dreieck unter der Fahrbahn (hw + 0,5 m) wird so weit abgesenkt, dass es SINK unter der Straße liegt –
    bündig statt Damm, am Hang ein Einschnitt von einer Rasterzelle."""
    d, k = cKDTree(np.column_stack([rx, rz])).query(np.column_stack([gx.ravel(), gz.ravel()]), k=min(24, len(rx)))
    w = 1 / (d ** 2 + 25) ** 2
    wk = w * (1.0 if bridge is None else (~bridge[k]).astype(float))
    road_h = (wk * rh[k]).sum(1) / np.maximum(wk.sum(1), 1e-30)
    t = 1 - (1 - biome.smooth((d[:, 0] - 1.5 * cell) / 80.0)) * wk.sum(1) / w.sum(1)
    out = (road_h - SINK) * (1 - t) + dem.ravel() * t
    out = np.minimum(out, (rh[k] - SINK + d).min(1)).reshape(dem.shape)  # 45° Böschung zur Straße
    # Querschnitte alle 2,5 m (Punkte + Mitten), 5 Stützstellen über die Breite; Absenken aller 4 Ecken senkt den Punkt genau um p
    n = len(rx)
    s = np.arange(0, n - 0.5, 0.5)
    x, z, y, h = (np.interp(s, np.arange(n), a) for a in (rx, rz, rh, np.broadcast_to(hw, rx.shape)))
    tx, tz = np.gradient(x), np.gradient(z)
    ln = np.hypot(tx, tz) + 1e-9
    o = np.array([-1, -.5, 0, .5, 1])[None, :] * (h + 0.5)[:, None]
    sx, sz = (x[:, None] + tz[:, None] / ln[:, None] * o).ravel(), (z[:, None] - tx[:, None] / ln[:, None] * o).ravel()
    sy = np.repeat(y - SINK, 5)
    H, W = out.shape
    for _ in range(12):
        p = tri_height(out, gx[0, 0], gz[0, 0], cell, sx, sz) - sy
        bad = p > 1e-3
        if not bad.any():
            break
        i = np.clip(np.floor((sx[bad] - gx[0, 0]) / cell).astype(int), 0, W - 2)
        j = np.clip(np.floor((sz[bad] - gz[0, 0]) / cell).astype(int), 0, H - 2)
        for di, dj in ((0, 0), (1, 0), (0, 1), (1, 1)):
            np.minimum.at(out, (j + dj, i + di), out[j + dj, i + di] - p[bad])
    return out.astype(np.float32), d[:, 0].reshape(dem.shape), k[:, 0].reshape(dem.shape)


class Ground:
    """Gelände als Dreiecksnetz (grobes Raster + feine Querprofile an der Straße); at() = Höhe exakt auf der Fläche."""

    def __init__(self, pos):
        self.pos = pos.astype(np.float32).astype(np.float64)  # wie in terrain.pos -> Prüfskripte triangulieren (fast) gleich
        self.tri = Delaunay(pos[:, [0, 2]])
        idx = self.tri.simplices
        a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
        down = (b[:, 2] - a[:, 2]) * (c[:, 0] - a[:, 0]) - (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) < 0
        self.idx = np.where(down[:, None], idx[:, [0, 2, 1]], idx)  # alle gleich herum, sonst heben sich die Normalen auf
        self.lo, self.hi = pos[:, [0, 2]].min(0), pos[:, [0, 2]].max(0)

    def locate(self, x, z):
        p = np.clip(np.column_stack([x, z]), self.lo, self.hi)
        s = self.tri.find_simplex(p)
        if (s < 0).any():  # knapp außerhalb (Rundung an der Hülle): Dreieck des nächsten Punkts
            near = cKDTree(self.pos[:, [0, 2]]).query(p[s < 0])[1]
            s[s < 0] = self.tri.vertex_to_simplex[near]
        T = self.tri.transform[s]
        b = np.einsum("ijk,ik->ij", T[:, :2], p - T[:, 2])
        return self.tri.simplices[s], np.column_stack([b, 1 - b.sum(1)])

    def at(self, x, z):
        v, w = self.locate(np.ravel(x), np.ravel(z))
        return (w * self.pos[v, 1]).sum(1).reshape(np.shape(x))


def road_terrain(dem, gx, gz, rx, rz, rh, hw, bridge):
    """Gelände für den Bau -> (Ground, Abstand zur Straße je Rasterpunkt, nächster Routenpunkt je Rasterpunkt).
    Das 20-m-Raster kann einer Straße am Hang nicht folgen (Damm oder Absatz). Deshalb Querprofile alle 5 m (0,3 … 24 m neben
    der Fahrbahn) statt der Rasterpunkte nahe der Straße: unter der Fahrbahn eine ebene Trasse SINK unter der Straße, nach 4 m
    das echte Quergefälle des DEM (Hang bergauf, Böschung bergab); liegt die Straße höher/tiefer als das DEM (Rampe, Einschnitt,
    DEM-Fehler), gleicht eine Böschung 1:1,5 das aus. Brücken überspannen das Tal, darunter bleibt DEM. Gekappt: nirgends über einer Fahrbahn,
    dahinter höchstens 45°; zum Schluss jedes Dreieck unter der Fahrbahn exakt nachgesenkt."""
    cell = gx[0, 1] - gx[0, 0]
    dem_at = lambda x, z: bilinear(dem, (x - gx[0, 0]) / cell, (z - gz[0, 0]) / cell)
    tx, tz = np.gradient(rx), np.gradient(rz)
    ln = np.hypot(tx, tz) + 1e-9
    nx, nz = tz / ln, -tx / ln
    # unter der Fahrbahn alle 2,5 m Mitte + Kanten (kleine, flache Dreiecke), daneben Ringe alle 5–15 m
    s = np.arange(0, len(rx) - 0.5, 0.5)
    hx, hz, hhw, hnx, hnz = (np.interp(s, np.arange(len(rx)), a) for a in (rx, rz, hw, nx, nz))
    # Unter Brücken dieselben Punkte, aber frei (DEM-Höhe): sonst spannt Delaunay eine Fläche auf Deckhöhe von Ende zu Ende
    hy = np.where(bridge[np.round(s).astype(int)], np.nan, np.interp(s, np.arange(len(rx)), rh) - SINK)
    px, pz, des = [gx.ravel(), hx], [gz.ravel(), hz], [np.full(gx.size, np.inf), np.zeros(len(s))]
    for sgn in (-1, 1):
        px.append(hx + sgn * hnx * (hhw + 0.3))
        pz.append(hz + sgn * hnz * (hhw + 0.3))
        des.append(np.full(len(s), 0.3))
    own = np.concatenate([np.full(gx.size, np.nan), np.tile(hy, 3)])  # Trassenhöhe der eigenen Stelle (Brücke: keine)
    for o, st in ((1.5, 1), (3.3, 1), (6, 1), (10, 2), (16, 2), (24, 3)):
        k = np.arange(0, len(rx), st)
        for sgn in (-1, 1):
            px.append(rx[k] + sgn * nx[k] * (hw[k] + o))
            pz.append(rz[k] + sgn * nz[k] * (hw[k] + o))
            des.append(np.full(len(k), float(o)))
    px, pz, des = np.concatenate(px), np.concatenate(pz), np.concatenate(des)
    own = np.concatenate([own, np.full(len(px) - len(own), np.nan)])
    centre = des == 0
    # Raster ist kozirkulär -> Delaunay sonst nicht eindeutig; Rand bleibt exakt (Hülle = Rechteck)
    j = np.random.default_rng(5).uniform(-0.02, 0.02, (2, len(px)))
    inner = (px > gx[0, 0]) & (px < gx[0, -1]) & (pz > gz[0, 0]) & (pz < gz[-1, 0])
    px, pz = np.where(inner, px + j[0], px), np.where(inner, pz + j[1], pz)
    d, k = cKDTree(np.column_stack([rx, rz])).query(np.column_stack([px, pz]), k=min(24, len(rx)))
    ng = gx.size
    dist, nearest = d[:ng, 0].reshape(gx.shape), k[:ng, 0].reshape(gx.shape)
    # Abstand zur Fahrbahnkante: geplanter Ringabstand (Abstand zu Punkten statt zur Linie wäre zwischen den Punkten zu groß),
    # ein anderer Ast der Strecke kann näher sein
    edge = np.minimum(des, d[:, 0] - hw[k[:, 0]])
    keep = np.where(np.arange(len(px)) < ng, edge > 20, (edge > 0.25) | centre)  # Raster nahe der Straße raus; nichts auf fremder Fahrbahn
    sel = np.flatnonzero(keep)
    first = np.unique(np.round(np.column_stack([px[sel], pz[sel]]) * 2), axis=0, return_index=True)[1]  # doppelte (0,5 m) raus
    keep[:] = False
    keep[sel[first]] = True
    px, pz, d, k, edge, own = px[keep], pz[keep], d[keep], k[keep], edge[keep], own[keep]
    pinned = ~np.isnan(own) & (centre[keep] | (edge > 0.25))  # Trasse: genau SINK unter der eigenen Straße
    w = 1 / (d ** 2 + 25) ** 2
    wk = w * ~bridge[k]
    ground_share = wk.sum(1) / w.sum(1)  # mitten auf einer Brücke ~0: Tal bleibt Tal
    norm = np.maximum(wk.sum(1), 1e-30)
    off = rh - dem_at(rx, rz)
    # Damm/Einschnitt: Höhenunterschied Straße–DEM klingt mit einer Böschung 1:1,5 ab (~34°, steilste Stelle 45°),
    # nicht über 70 m – sonst wird aus der Brückenrampe ein Plateau
    shift = (wk * off[k]).sum(1) / norm * ground_share
    natural = dem_at(px, pz) + shift * (1 - biome.smooth(np.maximum(edge, 0) / np.maximum(6.0, 1.5 * np.abs(shift))))
    bench = (wk * rh[k]).sum(1) / norm - SINK
    b = (1 - biome.smooth((edge - 0.3) / 4.0)) * ground_share
    y = np.minimum(bench * b + natural * (1 - b), (rh[k] - SINK + np.maximum(d - hw[k] - 0.3, 0)).min(1))
    # Trasse direkt aus der eigenen Höhe: das gewichtete Mittel zieht in Kehren den anderen Ast mit hinein, die Kappung über
    # Punktabstände (statt Linie) zwischen den Punkten auf den tieferen Nachbarn
    y = np.where(pinned, own, y)
    g = Ground(np.column_stack([px, y, pz]))
    # exakt: Querschnitte alle 2,5 m, 5 Stützstellen bis 0,25 m über die Kante. Gesenkt werden nur die freien Ecken (Hang),
    # und zwar um p / Gewicht -> der Punkt liegt danach genau richtig, die Trasse daneben bleibt, wo sie ist
    x, z, sy, h, sx_, sz_ = (np.interp(s, np.arange(len(rx)), a) for a in (rx, rz, rh, hw, nx, nz))
    o = np.array([-1, -.5, 0, .5, 1])[None, :] * (h + 0.25)[:, None]
    sx, sz, sy = (x[:, None] + sx_[:, None] * o).ravel(), (z[:, None] + sz_[:, None] * o).ravel(), np.repeat(sy - SINK, 5)
    for _ in range(12):
        v, wb = g.locate(sx, sz)
        p = (wb * g.pos[v, 1]).sum(1) - sy
        bad = p > 1e-3
        if not bad.any():
            break
        v, wb, p = v[bad], wb[bad], p[bad]
        free = ~pinned[v]
        sw = (wb * free).sum(1)
        drop = np.where(sw[:, None] > 0.05, free * (p / np.maximum(sw, 0.05))[:, None], p[:, None])
        np.minimum.at(g.pos[:, 1], v.ravel(), (g.pos[v, 1] - drop).ravel())
    return g, dist, nearest


def ground_grid(lonlat, margin, points, quick=False):
    """Strecke, Geländeraster und Höhen – gemeinsam für Bau und Vorschau: gleiches DEM (z13) und road_profile, also exakt
    dieselben Steigungen. points = Rasterpunkte entlang der längeren Seite (ungefähr)."""
    proj = Proj(lonlat[:, 0].mean(), lonlat[:, 1].mean())
    rx, rz = resample(lonlat, proj)
    if len(rx) < 40:
        sys.exit(f"Strecke zu kurz ({len(rx) * STEP:.0f} m, mindestens 200 m)")
    loop = math.hypot(rx[0] - rx[-1], rz[0] - rz[-1]) < 60 and len(rx) * STEP > 500  # kurze A-nach-B sind keine Runde
    n0 = len(rx)
    rx, rz = road.clean_route(rx, rz, loop)  # Stichstraßen an Wegpunkten
    cut = (n0 - len(rx)) * STEP
    ox, oz = rx, rz
    rx, rz = road.resample(*road.smooth_line(rx, rz, loop), loop)  # Knicke runden, ≤ 2,5 m neben der echten Straße
    print(f"Strecke {len(rx) * STEP / 1000:.1f} km, {'Runde' if loop else 'A nach B'}" + (f", {cut:.0f} m Stichstraßen entfernt" if cut > 20 else ""))
    try:
        ways = route_ways(proj, rx, rz, quick)
    except Exception as e:  # ohne Straßendaten: Standardstraße, keine Brücken
        print(f"Straßendaten nicht verfügbar ({e})" + (" – Vorschau-Höhen können vom Bau abweichen" if quick else ""))
        ways = []
    rd = road.match_ways(rx, rz, ways, loop)
    od = np.r_[0, np.cumsum(np.hypot(np.diff(ox), np.diff(oz)))]  # Abstand zur echten Linie (Kehren bis 6 m) -> network.build
    s = np.arange(0, od[-1] + 0.5, 0.5)
    rd["dev"] = cKDTree(np.column_stack([np.interp(s, od, ox), np.interp(s, od, oz)])).query(np.column_stack([rx, rz]))[0]
    gx0, gz0 = rx.min() - margin, rz.min() - margin
    cell = max(20.0, (max(np.ptp(rx), np.ptp(rz)) + 2 * margin) / points)
    W = int((rx.max() + margin - gx0) / cell) + 1
    H = int((rz.max() + margin - gz0) / cell) + 1
    gx, gz = np.meshgrid(gx0 + np.arange(W) * cell, gz0 + np.arange(H) * cell)
    glon, glat = proj.to_lonlat(gx, gz)
    print(f"Gelände {W}x{H} Punkte, {cell:.0f} m Raster")
    dem = Dem(glon, glat)
    terrain = dem.at(glon, glat)
    floor = np.full(len(rx), np.nan)  # Brücke über einer anderen Straße: Deck mindestens road.CLEARANCE darüber
    cross = road.crossings(rx, rz, ways, rd["bridge"])
    for i, cx_, cz_ in cross:
        floor[i] = dem.at(*proj.to_lonlat(np.array([cx_]), np.array([cz_])))[0] + road.CLEARANCE
    rh = road.road_profile(lambda x, z: dem.at(*proj.to_lonlat(x, z)), rx, rz, loop, span=rd["bridge"] | rd["tunnel"], floor=floor)
    base = rh.min()
    k = np.abs(road.curvature(rx, rz, loop))
    surf = np.bincount(rd["surface"], minlength=3) / len(rx)
    print(f"Straße: min. Radius {1 / max(k.max(), 1e-9):.0f} m, Belag glatt {surf[0]:.0%} / Pflaster {surf[1]:.0%} / unbefestigt {surf[2]:.0%}"
          f", Brücken {rd['bridge'].sum() * STEP:.0f} m (über {len(cross)} Straßen), Tunnel {rd['tunnel'].sum() * STEP:.0f} m")
    return proj, rx, rz, loop, gx0, gz0, cell, W, H, gx, gz, glon, glat, terrain - base, rh - base, base, rd, ways


def write_meta(out, name, proj, rx, loop, gx0, gz0, W, H, cell, base, arid, heights=None):
    extra = {"heights": heights} if heights else {}  # heights.f32: Punkte x, Punkte z, Abstand (sonst terrain.pos = Raster)
    (out / "meta.json").write_text(json.dumps({"name": name, "loop": bool(loop), "length_m": len(rx) * STEP, **extra,
                                               "lon0": proj.lon0, "lat0": proj.lat0, "kx": proj.kx, "ky": proj.ky,
                                               "ground": [gx0, gz0, (W - 1) * cell, (H - 1) * cell], "grid": [W, H, cell],
                                               # Klima für die Schneegrenze zur Laufzeit (main.gd): Monatsmittel = t_sea ∓ amp·cos(Jahreszeit)
                                               "climate": {"elev0": float(base), "lapse": biome.LAPSE, "south": bool(proj.lat0 < 0),
                                                           "t_sea": float(biome.climate(proj.lat0, 0)[0]), "amp": float(biome.amplitude(proj.lat0)),
                                                           "arid": float(arid)}}))  # Dunst am Himmel


def preview(lonlat, out):
    """Schnelle 3D-Vorschau für die Streckenwahl (Sekunden statt Minuten): grobes Gelände mit Biomfarben aus WorldCover,
    kein OSM. Dateien wie ein Streckenpaket (terrain.*, route.pos, ground.png, meta.json), Höhen identisch zum Bau."""
    proj, rx, rz, loop, gx0, gz0, cell, W, H, gx, gz, glon, glat, terrain, rh, base, rd, _ = ground_grid(lonlat, 800.0, 250, quick=True)
    terrain, _, _ = fit_terrain(terrain, gx, gz, rx, rz, rh, cell, rd["bridge"])
    try:
        wc = biome.WorldCover(glon, glat, int(np.clip(np.floor(np.log2(cell / 9.3)), 1, 6))).at(glon, glat)
    except Exception as e:  # optionale Quelle
        print(f"WorldCover nicht verfügbar ({e})")
        wc = np.zeros((H, W), np.uint8)
    cls = biome.WC_CLASS[wc]
    t_mean, t_warm = biome.climate(glat, terrain + base)
    arid, alp = biome.aridity(cls, t_warm, cell), biome.alpine(t_warm)
    size = (4 * W, 4 * H)
    px_ = biome.ground_rgba(biome.upsample(cls, size, (W - 1) / (size[0] - 1), np.random.default_rng(2)), biome.resize(arid, size),
                            biome.resize(alp, size), biome.resize(biome.tropic(t_mean), size))
    Image.fromarray(px_).save(out / "ground.png")
    pos = np.column_stack([gx.ravel(), terrain.ravel(), gz.ravel()])
    i = (np.arange(H - 1)[:, None] * W + np.arange(W - 1)[None, :]).ravel()
    idx = np.column_stack([i, i + W, i + 1, i + 1, i + W, i + W + 1]).reshape(-1, 3)
    save_mesh(out, "terrain", pos, normals(pos, idx), idx)
    np.column_stack([rx, rh, rz]).astype(np.float32).tofile(out / "route.pos")
    ix = np.clip(np.round((rx - gx0) / cell).astype(int), 0, W - 1)
    iz = np.clip(np.round((rz - gz0) / cell).astype(int), 0, H - 1)
    write_meta(out, "vorschau", proj, rx, loop, gx0, gz0, W, H, cell, base, arid[iz, ix].mean())
    print(f"Fertig: {out}")


def build(name, lonlat, out):
    proj, rx, rz, loop, gx0, gz0, cell, W, H, gx, gz, glon, glat, terrain, rh, base, rd, rways = ground_grid(lonlat, 1500.0, 600)
    rhw, br = rd["hw"], rd["bridge"]  # halbe Fahrbahnbreite je Routenpunkt, Brücke

    # Gelände an die Straße anpassen: nahe Punkte auf Straßenhöhe, sanft zurück zum DEM
    ix = np.clip(np.round((rx - gx0) / cell).astype(int), 0, W - 1)
    iz = np.clip(np.round((rz - gz0) / cell).astype(int), 0, H - 1)
    # ponytail: 20-m-Raster -> am Hang ein Einschnitt von einer Zelle neben der Straße; feineres Raster entlang der Straße wenn das stört
    t0 = time.time()
    gnd, dist, nearest = road_terrain(terrain, gx, gz, rx, rz, rh, rhw, br)
    terrain = gnd.at(gx, gz)  # Rasterwerte (Klima, Häuser) von der feinen Fläche
    print(f"Gelände: {len(gnd.pos)} Punkte, {len(gnd.idx)} Dreiecke ({time.time() - t0:.0f} s)")

    # OSM: Landnutzung als Grid-Raster (Bäume) und als feine Bodentextur (Shader)
    lo, la = proj.to_lonlat(np.array([gx0, gx0 + W * cell]), np.array([gz0 + H * cell, gz0]))
    coarse = np.column_stack(proj.to_lonlat(rx, rz))[:: int(100 / STEP)]
    line = ",".join(f"{la_:.5f},{lo_:.5f}" for lo_, la_ in coarse)
    elements = overpass((la[0], lo[0], la[1], lo[1]), line)["elements"]

    rng = np.random.default_rng(1)
    ext = ((W - 1) * cell, (H - 1) * cell)
    res = max(2.0, max(ext) / 4096)  # m pro Texel
    size = (int(ext[0] / res) + 1, int(ext[1] / res) + 1)

    # Biom-Grundlage: WorldCover überall, wo OSM nichts sagt; Klima aus Breite + echter Höhe
    try:
        wc = biome.WorldCover(glon, glat).at(glon, glat)
    except Exception as e:  # optionale Quelle: offline/kaputte Kachel -> nur OSM wie früher, Bau nie abbrechen
        print(f"WorldCover nicht verfügbar ({e}), nur OSM-Flächen")
        wc = np.zeros((H, W), np.uint8)
    base_cls = biome.WC_CLASS[wc]
    t_mean, t_warm = biome.climate(glat, terrain + base)
    arid, alp = biome.aridity(base_cls, t_warm, cell), biome.alpine(t_warm)
    print("Landbedeckung: " + ", ".join(f"{n} {v:.0%}" for n, v in zip(
        ("offen", "Feld", "Wiese", "Wald", "Ort", "Wasser", "Busch", "Sand/kahl", "Fels", "Eis", "Feucht"),
        np.bincount(base_cls.ravel(), minlength=11) / base_cls.size) if v >= .01)
          + f" · trocken {arid.mean():.2f}, alpin {alp.mean():.2f}, Jahresmittel {t_mean.mean():.0f} °C")

    land = Image.new("L", (W, H), NONE)
    tex = Image.new("L", size, NONE)  # Landklasse je Texel, Farbe kommt danach aus biome.ground_rgba
    ld, td = ImageDraw.Draw(land), ImageDraw.Draw(tex)

    def to_px(ring, scale):
        a = np.array(ring)
        x, z = proj.to_xz(a[:, 0], a[:, 1])
        return list(zip((x - gx0) / scale, (z - gz0) / scale))

    by_class = {k: [] for k in DRAW_ORDER}
    paving = []
    for el in elements:
        tags = el.get("tags", {})
        cls = next((TAGS[k][tags[k]] for k in TAGS if tags.get(k) in TAGS[k]), None)
        if cls: by_class[cls].append(el)
        if tags.get("amenity") == "parking" or tags.get("place") == "square" or (tags.get("highway") and tags.get("area") == "yes"):
            paving.append(el)
    for cls in DRAW_ORDER + [PAVING]:
        for el in by_class.get(cls, paving if cls == PAVING else []):
            outer, inner = rings(el)
            for ring, fill in [(r, cls) for r in outer] + [(r, NONE) for r in inner]:  # Innenringe: wieder WorldCover
                if len(ring) > 2:
                    if cls != PAVING: ld.polygon(to_px(ring, cell), fill=fill)
                    td.polygon(to_px(ring, res), fill=fill)
    land = np.asarray(land)
    land = np.where(land == NONE, base_cls, land)
    cls_px = np.asarray(tex)
    cls_px = np.where(cls_px == NONE, biome.upsample(base_cls, size, res / cell, np.random.default_rng(2)), cls_px)
    px_ = biome.ground_rgba(cls_px, biome.resize(arid, size), biome.resize(alp, size), biome.resize(biome.tropic(t_mean), size))

    # Alle anderen Straßen und Wege als ein Netz (network.py): OSM-Graph, Kreuzungsflächen, Einmündungen in unsere Fahrbahn,
    # Brücken mit lichter Höhe. Stücke auf unserer Strecke zeichnet road.pos selbst.
    t0 = time.time()
    R = signs.Route(rx, rz, rh, rhw, loop)
    gcol = lambda x, z: px_[np.clip(((z - gz0) / res).astype(int), 0, size[1] - 1), np.clip(((x - gx0) / res).astype(int), 0, size[0] - 1), :3] / 255
    net = network.build(network.ways_from_osm(elements, proj), gnd.at, R, np.where(br, 1, np.where(rd["tunnel"], -1, 0)), gcol,
                        rd["dev"], [network.own_key(rways[w][2]) if w >= 0 else None for w in rd["way"]])
    st = net["stats"]
    print(f"Straßennetz: {st['chains']} Ketten, {st['junctions']} Kreuzungen ({st['simple']} vereinfacht, {st['fallback']} konvexe Hülle), {st['mouths']} Einmündungen, "
          f"{st['crossings']} Über-/Unterführungen (lichte Höhe min {st['clearance_min']:.1f} m, {st.get('raised', 0):.0%} des Netzes auf Rampen), Gelände über Netz max {st.get('poke', 0):+.2f} m"
          f" ({time.time() - t0:.0f} s)")
    # Kein Gras auf Straßen, Wegen und in Häusern
    bare = Image.new("L", tex.size, 0)
    bd = ImageDraw.Draw(bare)
    for i in range(len(rx) - 1):  # Fahrbahn + 1 m Rand (Gras bis an die Kante, nicht drauf), Breite je Abschnitt
        bd.line([((rx[i] - gx0) / res, (rz[i] - gz0) / res), ((rx[i + 1] - gx0) / res, (rz[i + 1] - gz0) / res)],
                fill=255, width=int(2 * (rhw[i] + 1.0) / res) + 2)
    if net["tris"] is not None:  # genau die gezeichneten Flächen (auch Kreuzungsecken), nicht nur die Mittellinien
        for t in (net["tris"][:, :, [0, 2]] - (gx0, gz0)) / res:
            bd.polygon([tuple(v) for v in t], fill=255)
    for el in elements:
        if el["type"] == "way" and "building" in el.get("tags", {}) and len(el["geometry"]) > 2:
            bd.polygon(to_px([(p["lon"], p["lat"]) for p in el["geometry"]], res), fill=255)
    bare = np.asarray(bare)
    px_[(bare > 0) & (px_[..., 3] > biome.GROW[WATER]), 3] = biome.GROW[PAVING]
    Image.fromarray(px_).save(out / "ground.png")
    del cls_px

    save_mesh(out, "terrain", gnd.pos, normals(gnd.pos, gnd.idx), gnd.idx)
    # Höhenkarte fürs Gras (main.gd): fein genug für die Querprofile an der Straße, höchstens 2048²
    # ponytail: float32 bis 16 MB; als 16 bit (RG8 + Bilinear im Shader) halb so groß, falls Pakete zu groß werden
    hres = max(2.0, max(ext) / 2047)
    hgx, hgz = np.meshgrid(gx0 + np.arange(int(ext[0] / hres) + 1) * hres, gz0 + np.arange(int(ext[1] / hres) + 1) * hres)
    gnd.at(hgx, hgz).astype(np.float32).tofile(out / "heights.f32")
    hgrid = [hgx.shape[1], hgx.shape[0], hres]
    del hgx, hgz

    ground = gnd.at

    poke = ground(rx, rz) - rh
    print(f"Gelände über der Fahrbahn: max {poke.max():+.2f} m")  # Kontrolle, soll < 0 sein

    # Kreuzungen, Markierungen, Schilder, Ampeln (signs.py) aus OSM-Knoten + Tags der Wege
    try:
        nodes = [(*proj.to_xz(np.array([e["lon"]]), np.array([e["lat"]])), e.get("tags", {})) for e in route_nodes(proj, rx, rz)]
        nodes = [(float(x[0]), float(z[0]), tg) for x, z, tg in nodes]
    except Exception as e:  # optionale Quelle: ohne Knoten nur Einmündungen und Tempo aus den Wegen
        print(f"Schilder-Daten nicht verfügbar ({e})")
        nodes = []
    try:
        rails = route_rails(proj, rx, rz)
    except Exception as e:  # optionale Quelle: Schutzplanken dann nur aus Gefälle und Brückenköpfen
        print(f"Schutzplanken-Daten nicht verfügbar ({e})")
        rails = []
    junc = road.junctions(rx, rz, rhw, rways, br | rd["tunnel"])
    bpts = np.array([np.mean([(p["lon"], p["lat"]) for p in el["geometry"]], axis=0) for el in elements
                     if el["type"] == "way" and "building" in el.get("tags", {}) and el.get("geometry")])
    bpts = np.column_stack(proj.to_xz(bpts[:, 0], bpts[:, 1])) if len(bpts) else np.zeros((0, 2))
    furn = signs.furnish(R, rd, rways, junc, nodes, ground, {c: v[3] for c, v in ROADS.items()}, bpts,
                         europe=-25 <= proj.lon0 <= 45 and 35 <= proj.lat0 <= 72)
    print(f"{len(junc)} Einmündungen, " + ", ".join(f"{v} {k}" for k, v in sorted(furn["counts"].items())))
    rs = signs.roadside(R, rd, junc, ground, ~ndimage.binary_dilation(land[iz, ix] == URBAN, np.ones(5)), rails)
    furn["posts"] += rs["posts"]
    print(f"{rs['counts']['leitpfosten']} Leitpfosten, {rs['counts']['schutzplanke_m']} m Schutzplanke "
          f"({len(rails)} OSM-Schutzplanken in der Nähe)")

    # Unsere Straße: Breite, Belag, Markierung aus OSM (road.match_ways); bündig im Gelände (road_terrain), auf Brücken Bordstein
    nr = len(rx)
    dx, dz = np.gradient(rx), np.gradient(rz)
    ln = np.hypot(dx, dz) + 1e-9
    lx, lz = dz / ln, -dx / ln  # links (−z = Norden)
    shoulder = np.where(br, rhw + 0.5, rhw + 0.35)  # Brücke: Bordstein bis an die Brüstung; sonst nur die Fuge (SINK) zudecken
    off = np.column_stack([shoulder, rhw, -rhw, -shoulder])  # Versatz nach links
    kap = road.curvature(rx, rz, loop)
    lim = 0.85 / np.maximum(np.abs(kap), 1e-6)[:, None]
    off = np.where(off * kap[:, None] > 0, np.sign(off) * np.minimum(np.abs(off), lim), off)  # Innenseite enger Kurven nicht falten
    n = 4
    yo = np.column_stack([np.full(nr, .2), np.full(nr, .05), np.full(nr, .05), np.full(nr, .2)])
    for c in (0, n - 1):  # Asphaltkante: von der Fahrbahn knapp unter die Geländeoberfläche, nie über die Fahrbahn
        g = ground(rx + lx * off[:, c], rz + lz * off[:, c]) - rh
        yo[:, c] = np.where(br, .2, np.minimum(g - 0.05, 0.0))
    rpos = np.stack([np.column_stack([rx + lx * off[:, c], rh + yo[:, c], rz + lz * off[:, c]]) for c in range(n)], axis=1).reshape(-1, 3)
    q = (np.arange(nr - 1)[:, None] * n + np.arange(n - 1)[None, :]).ravel()
    ridx = np.column_stack([q, q + n, q + 1, q + 1, q + n, q + n + 1]).reshape(-1, 3)
    if loop:
        q = (nr - 1) * n + np.arange(n - 1)
        ridx = np.vstack([ridx, np.column_stack([q, q % n, q + 1, q + 1, q % n, q % n + 1]).reshape(-1, 3)])
    col = np.zeros((nr, n, 4))
    col[:, 1:3, :3] = rd["color"][:, None]
    col[:, 1:3, 3] = rd["mark"][:, None]
    for c in (0, n - 1):  # Außenkante in Bodenfarbe (Sand, Wiese, Ort …), auf Brücken Beton
        u = np.clip(((rx + lx * off[:, c] - gx0) / res).astype(int), 0, px_.shape[1] - 1)
        v = np.clip(((rz + lz * off[:, c] - gz0) / res).astype(int), 0, px_.shape[0] - 1)
        col[:, c, :3] = np.where(br[:, None], road.CONCRETE, px_[v, u, :3] / 255)
    del px_
    save_mesh(out, "road", rpos, up(normals(rpos, ridx)), ridx, col=col.reshape(-1, 4),
              uv=np.column_stack([-off.ravel(), np.repeat(np.arange(nr) * STEP, n)]),
              uv2=np.repeat(np.column_stack([rhw, road.uv2_code(rd["surface"])]), n, axis=0), c0=np.repeat(furn["c0"], n, axis=0))
    structures = [road.bridge_mesh(rx, rh, rz, rhw, br, ground), road.tunnel_mesh(rx, rh, rz, rhw, rd["tunnel"]), rs["rail"]]
    if furn["marks"]:  # Querstriche: Haltlinien, Wartelinien, Zebrastreifen
        q = np.array(furn["marks"]).reshape(-1, 3)
        k = np.arange(len(furn["marks"]))[:, None] * 4
        mi = np.column_stack([k, k + 1, k + 2, k, k + 2, k + 3]).reshape(-1, 3)
        save_mesh(out, "marks", q, np.tile([0, 1, 0], (len(q), 1)), mi, col=np.tile(signs.WHITE, (len(q), 1)))
    if furn["plates"]:
        img, rects = signs.atlas(furn["faces"])
        img.save(out / "signs.png")
        pl = np.array(furn["plates"], np.float32)
        pl[:, 12:16] = rects[pl[:, 12].astype(int)]  # Instanz-Custom = UV-Rechteck im Atlas
        pl.tofile(out / "sign_plates.bin")
    for name_, arr in (("sign_posts", furn["posts"]), ("signals", furn["signals"]), ("delineators", rs["delineators"])):
        if arr:
            np.array(arr, np.float32).tofile(out / f"{name_}.bin")
    np.column_stack([rx, rh, rz]).astype(np.float32).tofile(out / "route.pos")

    if net["mesh"]:
        save_mesh(out, "streets", *net["mesh"][:3], col=net["mesh"][3], uv=net["mesh"][4], uv2=net["mesh"][5])
    structures += net["structures"]
    rtree = cKDTree(np.column_stack([rx, rz]))
    structures = [m for m in structures if m]
    if structures:
        save_mesh(out, "structures", *merge(structures))

    # Gebäude: passendes Blender-Modell, sonst prozedural aus dem Grundriss
    walls, roofs, placed, styles, blocked = [], [], {k: [] for k in MODELS}, {}, 0
    for el in elements:
        tags = el.get("tags", {})
        if "building" not in tags or el["type"] != "way" or len(el["geometry"]) < 4:
            continue
        a = np.array([(p["lon"], p["lat"]) for p in el["geometry"]])[:-1]
        x, z = proj.to_xz(a[:, 0], a[:, 1])
        if blocks_road(x, z, rtree, rhw):  # Durchfahrt (tunnel=building_passage) oder ungenauer Grundriss: Straße bleibt frei
            blocked += 1
            continue
        ci, cj = np.clip(int((z.mean() - gz0) / cell + .5), 0, H - 1), np.clip(int((x.mean() - gx0) / cell + .5), 0, W - 1)
        sname = regions.region(proj.lon0, proj.lat0, t_mean[ci, cj], arid[ci, cj])  # Klima je Haus: Bergdorf vs. Tal
        styles[sname] = styles.get(sname, 0) + 1
        m = place_model(tags, x, z, ground, rng, regions.STYLES[sname])
        if m:
            placed[m[0]].append(m[1])
            continue
        w, r = building(tags, x, z, ground, rng, regions.STYLES[sname])
        walls.append(w)
        if r: roofs.append(r)
    for name, rows in placed.items():
        np.array(rows, np.float32).reshape(-1, 20).tofile(out / f"house_{name}.bin")
    print("Baustil: " + ", ".join(f"{k} {v}" for k, v in styles.items()) + " · Modelle: " + ", ".join(f"{k} {len(v)}" for k, v in placed.items() if v))
    if walls:
        p, nr, i_, c, u, u2 = merge(walls)
        save_mesh(out, "walls", p, nr, i_, col=c, uv=u, uv2=u2)
        p, nr, i_, c = merge(roofs)
        save_mesh(out, "roofs", p, nr, i_, col=c)
    print(f"{len(walls)} Gebäude prozedural, {blocked} auf der Fahrbahn weggelassen")

    # Vegetation: Dichte nach Landklasse, Trockenheit und Baumgrenze (biome.py), Art nach Klima. Nie auf Straßen/in Häusern.
    near = (dist > rhw[nearest] + 8) & (dist < TREES_UP_TO)

    def scatter(density):
        per_cell = np.where(near, np.floor(density).astype(int) + (rng.random(density.shape) < density % 1), 0)
        cz_, cx_ = np.nonzero(per_cell)
        reps = per_cell[cz_, cx_]
        cz_, cx_ = np.repeat(cz_, reps), np.repeat(cx_, reps)
        x = gx0 + (cx_ + rng.uniform(-.5, .5, len(cx_))) * cell
        z = gz0 + (cz_ + rng.uniform(-.5, .5, len(cz_))) * cell
        free = bare[np.clip(((z - gz0) / res).astype(int), 0, bare.shape[0] - 1), np.clip(((x - gx0) / res).astype(int), 0, bare.shape[1] - 1)] == 0
        return x[free], z[free], cz_[free], cx_[free]

    tree_d, shrub_d = biome.plant_density(land, arid, alp)
    tx, tz, cz_, cx_ = scatter(tree_d)
    s = rng.uniform(.7, 1.4, len(tx))
    conifer, palm = biome.tree_species(t_mean[cz_, cx_], arid[cz_, cx_], land[cz_, cx_] == FOREST, rng)
    for kind, sel in (("conifer", conifer), ("palm", palm), ("broad", ~conifer & ~palm)):
        instances(out / f"trees_{kind}.bin", tx[sel], ground(tx[sel], tz[sel]), tz[sel], s[sel])
    bx, bz, _, _ = scatter(shrub_d)
    instances(out / "shrubs.bin", bx, ground(bx, bz), bz, rng.uniform(.5, 1.3, len(bx)))
    print(f"{len(tx)} Bäume ({conifer.sum()} Nadel, {palm.sum()} Palmen), {len(bx)} Büsche")

    # Laternen im Ort, abwechselnd links/rechts alle 35 m
    k = np.arange(0, len(rx), int(35 / STEP))
    k = k[(land[iz[k], ix[k]] == URBAN) & (rd["surface"][k] != 2) & ~rd["tunnel"][k]]
    side = np.where(np.arange(len(k)) % 2 == 0, 1, -1) * (rhw[k] + 1.0)
    instances(out / "lamps.bin", rx[k] + lx[k] * side, rh[k] + np.where(br[k], .2, 0), rz[k] + lz[k] * side, np.ones(len(k)))
    print(f"{len(k)} Laternen")

    write_meta(out, name, proj, rx, loop, gx0, gz0, W, H, cell, base, arid[iz, ix].mean(), hgrid)
    print(f"Fertig: {out}")


def test():
    p = Proj(8.5, 50.2)
    x, z = p.to_xz(8.51, 50.21)
    assert abs(x - 711.6) < 1 and abs(z + 1113.2) < 1, (x, z)
    assert np.allclose(p.to_lonlat(x, z), (8.51, 50.21))
    assert stitch([[(0, 0), (1, 0)], [(1, 1), (1, 0)], [(1, 1), (0, 0)]]) == [[(1, 1), (0, 0), (1, 0), (1, 1)]]
    a = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert bilinear(a, np.array([1.5]), np.array([2.0]))[0] == 9.5
    # Gelände an Serpentinen: zwei Äste 30 m auseinander, 15 m Höhenunterschied, 60-%-Hang -> nirgends über der Fahrbahn
    cell = 20.0
    gx, gz = np.meshgrid(np.arange(-200, 201, cell), np.arange(-200, 201, cell))
    leg = np.arange(-150, 150, STEP)
    rx = np.concatenate([leg, leg[::-1]])
    rz = np.concatenate([np.full_like(leg, -15.0), np.full_like(leg, 15.0)])
    rh = np.concatenate([leg * 0.05, 15 - leg[::-1] * 0.05 + 15])  # unten 0…15 m, oben 15…30 m
    dem = -0.6 * gz + 10
    terr, dist, _ = fit_terrain(dem, gx, gz, rx, rz, rh, cell)
    for o in (-3.5, -2, 0, 2, 3.5):  # Fahrbahnbreite + Kante
        poke = tri_height(terr, -200, -200, cell, rx, rz + o) - rh
        assert (poke <= -SINK + 2e-3).all(), poke.max()
    assert abs(dist[10, 10] - 15) < 1e-6 and np.isclose(terr[0, 0], dem[0, 0])  # weit weg: unverändertes DEM

    # Brücke über ein 40-m-Tal: Gelände darunter bleibt Tal (kein Damm), neben der Brücke wie gehabt an der Straße
    gx, gz = np.meshgrid(np.arange(-200, 201, cell), np.arange(-200, 201, cell))
    rx = np.arange(-200, 201, STEP)
    rz, rh = np.zeros_like(rx), np.zeros_like(rx)
    dem = np.where(np.abs(gx) < 60, -40.0, 0.0)
    terr, _, _ = fit_terrain(dem, gx, gz, rx, rz, rh, cell, np.abs(rx) < 70)
    assert terr[10, 10] < -39 and abs(terr[10, 0] + SINK) < 0.01  # Mitte Tal, Rand bündig unter der Straße
    # Straße an einer gleichmäßigen 10-%-Rampe auf ebenem Querprofil: Gelände bündig (SINK), kein Damm wie früher (0,8–3 m)
    rx = np.arange(-200, 201, STEP)
    rz, rh = np.zeros_like(rx), rx * 0.1
    terr, _, _ = fit_terrain(gx * 0.1, gx, gz, rx, rz, rh, cell)
    gap = rh - tri_height(terr, -200, -200, cell, rx, rz + 3)
    assert (gap >= SINK - 2e-3).all() and (gap[20:-20] < SINK + 0.05).all(), (gap.min(), gap.max())  # Enden: einseitiges Mittel
    # Feines Gelände (road_terrain): Straße quer im 40-%-Hang -> Trasse bündig, Hang bergauf/bergab bleibt Hang (kein Damm/Absatz)
    rx = np.arange(-200, 201, STEP)
    rz, rh, hw = np.zeros_like(rx), np.zeros_like(rx), np.full_like(rx, 3.0)
    g, _, _ = road_terrain(-0.4 * gz, gx, gz, rx, rz, rh, hw, np.zeros(len(rx), bool))
    mid = np.abs(rx) < 120
    for o in (-3.25, -1.5, 0, 1.5, 3.25):
        y = g.at(rx, rz + o)
        assert (y <= -SINK + 2e-3).all() and (y[mid] > -SINK - 0.05).all(), (o, y.min(), y.max())  # bündig, ohne Stufe
    assert (g.at(rx[mid], np.full(mid.sum(), -15.0)) > 3.5).all()  # bergauf (Norden) Hang statt 20-m-Absatz auf Straßenhöhe
    assert (g.at(rx[mid], np.full(mid.sum(), 15.0)) < -3.5).all()  # bergab Böschung statt Damm
    # Brückenrampe: Straße 6 m über flachem Land -> Damm mit Böschung, 15 m neben der Kante wieder Wiese (kein Plateau)
    g, _, _ = road_terrain(np.zeros_like(gx), gx, gz, rx, rz, np.full_like(rx, 6.0), hw, np.zeros(len(rx), bool))
    assert (g.at(rx[mid], np.full(mid.sum(), 4.5)) > 5.0).all() and (np.abs(g.at(rx[mid], np.full(mid.sum(), 18.0))) < 0.3).all()
    # Brücke über ein 40-m-Tal (road_terrain): unter dem Deck bleibt Tal, keine Fläche auf Deckhöhe von Ende zu Ende
    dem = np.where(np.abs(gx) < 60, -40.0, 0.0)
    br = np.abs(rx) < 70
    g, _, _ = road_terrain(dem, gx, gz, rx, rz, np.zeros_like(rx), hw, br)
    assert (g.at(rx[np.abs(rx) < 40], rz[np.abs(rx) < 40]) < -39).all() and abs(g.at(np.array([-150.0]), np.array([0.0]))[0] + SINK) < 0.01
    # Serpentine wie oben: nirgends über der Fahrbahn
    leg = np.arange(-150, 150, STEP)
    rx = np.concatenate([leg, leg[::-1]])
    rz = np.concatenate([np.full_like(leg, -15.0), np.full_like(leg, 15.0)])
    rh = np.concatenate([leg * 0.05, 15 - leg[::-1] * 0.05 + 15])
    g, _, _ = road_terrain(-0.6 * gz + 10, gx, gz, rx, rz, rh, np.full_like(rx, 3.0), np.zeros(len(rx), bool))
    ok = np.abs(np.arange(len(rx)) - len(leg) + 0.5) > 3  # Sprung zwischen den Ästen hat keine Querrichtung
    for o in (-3.25, 0, 3.25):
        assert (g.at(rx, rz + o) <= rh - SINK + 2e-3)[ok].all()
    road.test()
    network.test()

    # Satteldach: Rechteck 10x6 -> First bei z=3, Dachfirst über der Traufe, alle Dachnormalen nach oben
    tags = {"building": "house"}
    x, z = np.array([0., 10, 10, 0]), np.array([0., 0, 6, 6])
    walls, roof = building(tags, x, z, lambda x, z: np.zeros_like(x), np.random.default_rng(0))
    rp, rn, ri, rc = roof
    eave = 6.5
    assert abs(rp[:, 1].max() - (eave + 3 * math.tan(math.radians(40)))) < 1e-6
    assert np.isclose(rp[np.isclose(rp[:, 2], 3), 1], rp[:, 1].max()).all()  # Firstpunkte bei z=3
    assert (rn[:, 1] > 0).all() and len(walls[0]) == 6 * 4  # 4 Ecken + 2 Firstpunkte -> 6 Wände
    # Trockengebiet: Flachdach (Dach = Traufhöhe), eingeschossig, helle Wand
    arid_style = regions.STYLES["trocken"]
    walls, roof = building(tags, x, z, lambda x, z: np.zeros_like(x), np.random.default_rng(0), arid_style)
    assert np.allclose(roof[0][:, 1], 3.6) and len(walls[0]) == 4 * 4 and tuple(walls[3][0, :3]) in arid_style["walls"]
    # Regionale Modelle: Bali-Haus -> Tropenhaus/Beton, Kirche nur wo es passt, kleine Hütte in Afrika
    sea = regions.STYLES["suedostasien"]
    assert pick_model({"building": "house"}, 90, 8, np.random.default_rng(1), sea) in sea["houses"]
    assert pick_model({"building": "church"}, 300, 12, np.random.default_rng(1), sea) is None
    assert pick_model({"building": "hut"}, 25, 5, np.random.default_rng(1), regions.STYLES["afrika"]) == "rundhuette"
    regions.test(MODELS)
    # Gebäude auf der Fahrbahn (Durchfahrt): wird erkannt; Haus 5 m neben der Straße bleibt
    rt = cKDTree(np.column_stack([np.arange(0, 100, STEP), np.zeros(20)]))
    box = lambda z0: (np.array([40., 50, 50, 40]), np.array([z0, z0, z0 + 8, z0 + 8]))
    assert blocks_road(*box(-4), rt, np.full(20, 3.0)) and not blocks_road(*box(3.0), rt, np.full(20, 3.0))  # bündig am Rand bleibt
    # Modellplatzierung: 11x9-Rechteck -> efh-Typ, Länge entlang X, unverzerrt
    sq = (np.array([0., 11, 11, 0]), np.array([0., 0, 9, 9]))
    name, row = place_model({"building": "house"}, *sq, lambda x, z: np.zeros_like(x), np.random.default_rng(3))
    assert name in ("efh_sattel", "efh_walm", "siedlung", "fachwerk") and len(row) == 20
    L, D, _ = MODELS[name]
    assert np.isclose(abs(row[0]), 11 / L) and np.isclose(abs(row[10]), 9 / D) and np.isclose(row[3], 5.5) and np.isclose(row[11], 4.5)
    assert place_model({"building": "house"}, np.array([0., 20, 20, 2, 2, 0]), np.array([0., 0, 2, 2, 20, 20]),
                       lambda x, z: np.zeros_like(x), np.random.default_rng(3)) is None  # L-Form -> prozedural
    signs.test()
    biome.test()
    import terrain, workout, world  # Workout-Welt (world importiert route -> erst hier)
    terrain.test()
    workout.test()
    world.test()
    print("ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # Fortschritt live in der Streckenwahl
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--gpx")
    ap.add_argument("--via", help='"lon,lat|lon,lat|…"')
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--preview", metavar="ORDNER", help="nur schnelle 3D-Vorschau (ohne OSM) in diesen Ordner")
    a = ap.parse_args()
    lonlat = lambda: gpx_lonlat(Path(a.gpx).expanduser().read_bytes()) if a.gpx else brouter(a.via)
    if a.test:
        test()
    elif a.preview and (a.gpx or a.via):
        out = Path(a.preview)
        out.mkdir(parents=True, exist_ok=True)
        preview(lonlat(), out)
    elif not a.name or not (a.gpx or a.via):
        ap.error("Name und --gpx oder --via angeben")
    else:
        out = Path(__file__).parent / "routes" / a.name
        out.mkdir(parents=True, exist_ok=True)
        build(a.name, lonlat(), out)
