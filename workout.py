# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Workouts für Cadence: einlesen, normalisieren, einordnen, Bauplan für die prozedurale Welt (world.py).

Formate: .zwo (Zwift, Anteil FTP), .erg (Minuten + Watt, FTP aus dem Kopf oder --ftp), .mrc (Minuten + % FTP).
Gemeinsame Darstellung (nur Zeit und Anteil FTP, nichts über Geometrie):
  Segment   {"t0", "dur", "p0", "p1", "kind", "free"}   Sekunden, Anteil FTP am Anfang/Ende (linear), Zone, frei = kein Ziel
  Bauplan   [{"kind", "t0", "t1", "p", "parts"}]       Phasen; Intervall-Sätze ("set") mit den Teilen (Arbeit/Pause) in parts
Der Bauplan beschreibt den Ablauf, nicht die Geometrie: world.py entscheidet, welche Landschaft dazu passt.

  uv run workout.py --list workouts/         Übersicht als JSON (Streckenwahl)
  uv run workout.py workouts/vo2_5x3.zwo     Segmente + Bauplan als JSON
  uv run workout.py --test
"""
import json, re, sys, xml.etree.ElementTree as ET
from pathlib import Path

ZONES = [(0.56, "recovery"), (0.76, "endurance"), (0.91, "tempo"), (1.06, "threshold"), (9e9, "vo2")]  # obere Grenze, Anteil FTP
ORDER = ["recovery", "warmup", "cooldown", "endurance", "free", "tempo", "threshold", "vo2"]  # Härte, für "Arbeit vs. Pause"
NAMES = {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
         "recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei", "set": "Intervalle"}


def zone(p):
    return next(name for top, name in ZONES if p < top)


def _f(el, *keys, default=0.0):
    for k in keys:
        v = el.get(k)
        if v is not None:
            return float(v)
    return default


def parse_zwo(text):
    """Zwift-Workout (XML) -> (Name, Segmente). Warmup/Cooldown/Ramp/SteadyState/IntervalsT/FreeRide/MaxEffort."""
    root = ET.fromstring(text)
    name = (root.findtext("name") or "").strip()
    segs = []
    for el in root.find("workout") if root.find("workout") is not None else []:
        tag, dur = el.tag, _f(el, "Duration")
        lo, hi = _f(el, "PowerLow", "Power"), _f(el, "PowerHigh", "Power")
        if tag in ("SteadyState", "SolidState"):
            p = _f(el, "Power", "PowerLow")
            segs.append(dict(dur=dur, p0=p, p1=_f(el, "PowerHigh", default=p) if el.get("Power") is None else p))
        elif tag == "Warmup":
            segs.append(dict(dur=dur, p0=min(lo, hi), p1=max(lo, hi), kind="warmup"))
        elif tag == "Cooldown":  # Zwift-Dateien schreiben die Richtung unterschiedlich: Ausfahren geht immer runter
            segs.append(dict(dur=dur, p0=max(lo, hi), p1=min(lo, hi), kind="cooldown"))
        elif tag == "Ramp":
            segs.append(dict(dur=dur, p0=lo, p1=hi))
        elif tag == "IntervalsT":
            on, off = _f(el, "OnPower", "PowerOnLow", "PowerOnHigh"), _f(el, "OffPower", "PowerOffLow", "PowerOffHigh")
            for _ in range(int(_f(el, "Repeat", default=1))):
                segs.append(dict(dur=_f(el, "OnDuration"), p0=on, p1=on))
                segs.append(dict(dur=_f(el, "OffDuration"), p0=off, p1=off))
        elif tag == "FreeRide":
            segs.append(dict(dur=dur, p0=0.0, p1=0.0, free=True, kind="free"))
        elif tag == "MaxEffort":
            segs.append(dict(dur=dur, p0=0.0, p1=0.0, free=True, kind="vo2"))
    return name, segs


def parse_erg(text, ftp=None):
    """ERG (Watt) / MRC (Prozent): Punkte (Minute, Wert), Segmente linear dazwischen, gleiche Minute = Sprung."""
    head = re.search(r"\[COURSE HEADER\](.*?)\[END COURSE HEADER\]", text, re.S | re.I)
    head = head.group(1) if head else ""
    name = re.search(r"^\s*(?:DESCRIPTION|FILE NAME)\s*=\s*(.+)$", head, re.M | re.I)
    pct = re.search(r"MINUTES\s+PERCENT", head, re.I) is not None
    hftp = re.search(r"^\s*FTP\s*=\s*([\d.]+)", head, re.M | re.I)
    scale = 0.01 if pct else 1.0 / float(hftp.group(1) if hftp else ftp or 250)
    data = re.search(r"\[COURSE DATA\](.*?)\[END COURSE DATA\]", text, re.S | re.I)
    pts = [tuple(map(float, ln.split()[:2])) for ln in (data.group(1) if data else "").splitlines() if len(ln.split()) >= 2]
    segs = []
    for (m0, v0), (m1, v1) in zip(pts, pts[1:]):
        if m1 > m0:
            segs.append(dict(dur=(m1 - m0) * 60, p0=v0 * scale, p1=v1 * scale))
    return (name.group(1).strip() if name else ""), segs


def load(path, ftp=None):
    """Datei -> {"name", "duration", "segments"} mit Zonen, Einfahren/Ausfahren erkannt."""
    path = Path(path)
    text = path.read_text(errors="replace")
    name, segs = parse_zwo(text) if path.suffix.lower() == ".zwo" else parse_erg(text, ftp)
    return normalize(name or path.stem.replace("_", " "), segs)


def normalize(name, segs):
    segs = [dict(s) for s in segs if s["dur"] > 0.5]
    t = 0.0
    for s in segs:
        s["t0"], t = t, t + s["dur"]
        s.setdefault("free", False)
        s.setdefault("kind", zone((s["p0"] + s["p1"]) / 2))
    # Einfahren/Ausfahren auch ohne Zwift-Tags: lockere Segmente vor der ersten und nach der letzten harten Einheit
    hard = [i for i, s in enumerate(segs) if s["kind"] in ("tempo", "threshold", "vo2")]
    if hard:
        lead = segs[:hard[0]]
        if lead and sum(s["dur"] for s in lead) <= 25 * 60:
            for s in lead:
                if s["kind"] in ("recovery", "endurance"): s["kind"] = "warmup"
        tail = segs[hard[-1] + 1:]
        if tail and sum(s["dur"] for s in tail) <= 20 * 60:
            for s in tail[-2:] if len(tail) > 2 else tail:
                if s["kind"] in ("recovery", "endurance"): s["kind"] = "cooldown"
    for s in segs:
        s["p0"], s["p1"] = round(s["p0"], 4), round(s["p1"], 4)
    return {"name": name, "duration": t, "segments": segs}


def target(w, t):
    """Ziel (Anteil FTP) zur Workout-Zeit t, None = frei/vorbei. Gleiche Logik wie main.gd::_target_frac."""
    for s in w["segments"]:
        if s["t0"] <= t < s["t0"] + s["dur"]:
            return None if s["free"] else s["p0"] + (s["p1"] - s["p0"]) * (t - s["t0"]) / s["dur"]
    return None


def blueprint(w):
    """Phasen für die Welt: gleiche Zone zusammengefasst; Wechsel aus kurzer Arbeit (≤ 12 min, ab Tempo) und Pause
    (≥ 2 Wiederholungen, gleiche Zone) wird ein Satz ("set") mit seinen Teilen – world.py baut daraus ein zusammenhängendes Gebirge."""
    phases = []
    for s in w["segments"]:
        p = (s["p0"] + s["p1"]) / 2
        if phases and phases[-1]["kind"] == s["kind"]:
            ph = phases[-1]
            ph["p"] = (ph["p"] * (ph["t1"] - ph["t0"]) + p * s["dur"]) / (ph["t1"] - ph["t0"] + s["dur"])
            ph["t1"] = s["t0"] + s["dur"]
        else:
            phases.append({"kind": s["kind"], "t0": s["t0"], "t1": s["t0"] + s["dur"], "p": p})
    # Kurze Wechsel (jeder Teil ≤ 90 s, z. B. 30/15, 40/20) als einen Block: Härte = härtester Teil, Leistung gemittelt.
    # Sonst baut world.py daraus eine Säge aus 100-m-Rampen statt eines Anstiegs je Satz.
    short = lambda ph: ph["t1"] - ph["t0"] <= 90
    merged, i = [], 0
    while i < len(phases):
        j = i
        while j + 1 < len(phases) and short(phases[j]) and short(phases[j + 1]):
            j += 1
        if j > i:
            parts = phases[i:j + 1]
            dur = parts[-1]["t1"] - parts[0]["t0"]
            merged.append({"kind": max(parts, key=lambda q: ORDER.index(q["kind"]))["kind"], "t0": parts[0]["t0"], "t1": parts[-1]["t1"],
                           "p": sum(q["p"] * (q["t1"] - q["t0"]) for q in parts) / dur})
        else:
            merged.append(phases[i])
        i = j + 1
    phases = merged
    work = lambda ph: ph["kind"] in ("tempo", "threshold", "vo2") and ph["t1"] - ph["t0"] <= 12 * 60
    rest = lambda ph: ph["kind"] in ("recovery", "endurance") and ph["t1"] - ph["t0"] <= 12 * 60
    out, i = [], 0
    while i < len(phases):
        j = i
        same = lambda k: phases[k]["kind"] == phases[i]["kind"]  # ein Satz = gleiche Art Arbeit (Schwelle ≠ VO2-Spitzen)
        while j + 1 < len(phases) and work(phases[j]) and same(j) and rest(phases[j + 1]):
            j += 2
        if i < j < len(phases) and work(phases[j]) and same(j):
            j += 1  # letzte Arbeit ohne Pause danach gehört dazu
        if (j - i) >= 3:  # mindestens Arbeit, Pause, Arbeit
            parts = phases[i:j]
            out.append({"kind": "set", "t0": parts[0]["t0"], "t1": parts[-1]["t1"], "parts": parts,
                        "p": sum(q["p"] * (q["t1"] - q["t0"]) for q in parts) / (parts[-1]["t1"] - parts[0]["t0"])})
            i = j
        else:
            out.append(phases[i])
            i += 1
    for ph in out:
        ph.setdefault("parts", [])
    return out


def summary(path):
    w = load(path)
    return {"file": str(path), "name": w["name"], "duration": w["duration"],
            "segments": [[s["t0"], s["dur"], s["p0"], s["p1"], s["kind"]] for s in w["segments"]],
            "phases": [ph["kind"] for ph in blueprint(w)]}


ZWO_TEST = """<workout_file><name>Test</name><workout>
<Warmup Duration="600" PowerLow="0.4" PowerHigh="0.7"/>
<IntervalsT Repeat="3" OnDuration="180" OffDuration="120" OnPower="1.15" OffPower="0.5"/>
<SteadyState Duration="1200" Power="0.95"/>
<FreeRide Duration="300"/>
<Cooldown Duration="300" PowerLow="0.3" PowerHigh="0.6"/>
</workout></workout_file>"""

ERG_TEST = """[COURSE HEADER]
VERSION = 2
DESCRIPTION = Schwelle
FTP = 200
MINUTES WATTS
[END COURSE HEADER]
[COURSE DATA]
0.00 100
10.00 150
10.00 200
20.00 200
20.00 100
25.00 100
[END COURSE DATA]
"""


def test():
    # Kurze Wechsel (30/15) werden je Satz ein Block: 3 Arbeitsblöcke im Satz statt 39 Rampen
    rz = """<workout_file><name>R</name><workout><SteadyState Duration="600" Power="0.6"/>
<IntervalsT Repeat="13" OnDuration="30" OffDuration="15" OnPower="1.25" OffPower="0.5"/><SteadyState Duration="180" Power="0.5"/>
<IntervalsT Repeat="13" OnDuration="30" OffDuration="15" OnPower="1.25" OffPower="0.5"/><SteadyState Duration="180" Power="0.5"/>
<IntervalsT Repeat="13" OnDuration="30" OffDuration="15" OnPower="1.25" OffPower="0.5"/></workout></workout_file>"""
    bp = blueprint(normalize(*parse_zwo(rz)))
    sets = [ph for ph in bp if ph["kind"] == "set"]
    assert len(sets) == 1 and [q["kind"] for q in sets[0]["parts"]] == ["vo2", "recovery", "vo2", "recovery", "vo2"], bp
    assert all(560 < q["t1"] - q["t0"] < 590 for q in sets[0]["parts"][::2])
    name, segs = parse_zwo(ZWO_TEST)
    w = normalize(name, segs)
    assert w["name"] == "Test" and w["duration"] == 600 + 3 * 300 + 1200 + 300 + 300
    k = [s["kind"] for s in w["segments"]]
    assert k == ["warmup", "vo2", "recovery", "vo2", "recovery", "vo2", "recovery", "threshold", "free", "cooldown"], k
    assert w["segments"][-1]["p0"] == 0.6 and w["segments"][-1]["p1"] == 0.3  # Ausfahren geht runter
    assert abs(target(w, 300) - 0.55) < 1e-9 and target(w, 600 + 10) == 1.15 and target(w, 600 + 3 * 300 + 1200 + 10) is None
    assert target(w, w["duration"] + 1) is None
    bp = blueprint(w)
    assert [p["kind"] for p in bp] == ["warmup", "set", "threshold", "free", "cooldown"], [p["kind"] for p in bp]
    assert len(bp[1]["parts"]) == 6 and bp[1]["t0"] == 600 and bp[1]["t1"] == 600 + 3 * 300  # letzte Pause gehört zum Satz
    name, segs = parse_erg(ERG_TEST)
    w = normalize(name, segs)
    assert w["name"] == "Schwelle" and w["duration"] == 25 * 60
    assert [s["kind"] for s in w["segments"]] == ["warmup", "threshold", "cooldown"]
    assert abs(w["segments"][0]["p1"] - 0.75) < 1e-9 and w["segments"][1]["p0"] == 1.0
    mrc = ERG_TEST.replace("MINUTES WATTS", "MINUTES PERCENT").replace("FTP = 200\n", "")
    assert parse_erg(mrc)[1][1]["p0"] == 2.0  # 200 % FTP
    for f in sorted((Path(__file__).parent / "workouts").glob("*.zwo")):  # mitgelieferte Workouts lesbar
        s = summary(f)
        assert s["duration"] > 20 * 60 and s["phases"], f
    print("ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--test" in a:
        test()
    elif "--list" in a:
        d = Path(a[a.index("--list") + 1])
        out = []
        for f in sorted(p for p in d.iterdir() if p.suffix.lower() in (".zwo", ".erg", ".mrc")):
            try:
                out.append(summary(f))
            except Exception as e:  # kaputte Datei: anzeigen statt alles abbrechen
                out.append({"file": str(f), "name": f.name, "error": str(e)})
        print(json.dumps(out))
    elif a:
        w = load(a[0])
        print(json.dumps({**w, "blueprint": blueprint(w)}, indent=1))
    else:
        print(__doc__)
