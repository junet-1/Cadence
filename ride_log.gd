extends RefCounted
## Fahrtaufzeichnung: eine Probe je Sekunde -> Kennzahlen für die Zusammenfassung (recap.gd) und Export als TCX (Strava,
## Garmin Connect, intervals.icu, TrainingPeaks lesen Leistung/Trittfrequenz/Puls daraus). Ohne GPS = Indoor-Fahrt.

var start_unix := 0.0
var p := PackedFloat32Array()  # Leistung W
var c := PackedFloat32Array()  # Trittfrequenz rpm
var hr := PackedFloat32Array()  # Puls bpm (0 = kein Sensor)
var v := PackedFloat32Array()  # Tempo m/s
var d := PackedFloat32Array()  # Strecke m
var alt := PackedFloat32Array()  # Höhe m ü. M.
var tgt := PackedFloat32Array()  # Zielleistung W (0 = frei)
var wt := PackedFloat32Array()  # Workout-Zeit s
var _acc := 0.0


func add(delta: float, power: float, cadence: float, heart: float, speed: float, dist: float, height: float, target: float, wo_t: float) -> void:
	if start_unix == 0.0:
		start_unix = Time.get_unix_time_from_system()
	_acc += delta
	while _acc >= 1.0:
		_acc -= 1.0
		p.append(power)
		c.append(cadence)
		hr.append(heart)
		v.append(speed)
		d.append(dist)
		alt.append(height)
		tgt.append(target)
		wt.append(wo_t)


func size() -> int:
	return p.size()


static func best(a: PackedFloat32Array, n: int) -> float:
	## Höchster Mittelwert über n aufeinanderfolgende Sekunden (0 wenn kürzer)
	if a.size() < n or n <= 0:
		return 0.0
	var s := 0.0
	for i in n:
		s += a[i]
	var m := s
	for i in range(n, a.size()):
		s += a[i] - a[i - n]
		m = maxf(m, s)
	return m / n


static func normalized(a: PackedFloat32Array) -> float:
	## Normalized Power: 30-s-Mittel, vierte Potenz gemittelt, vierte Wurzel
	if a.size() < 30:
		return 0.0
	var s := 0.0
	for i in 30:
		s += a[i]
	var q := 0.0
	var n := 0
	for i in range(30, a.size() + 1):
		q += pow(s / 30.0, 4.0)
		n += 1
		if i < a.size():
			s += a[i] - a[i - 30]
	return pow(q / n, 0.25)


static func ftp_guess(a: PackedFloat32Array) -> Array:
	## FTP-Schätzung -> [Watt, Grundlage]: 95 % der besten 20 min, sonst 90 % der besten 8 min, sonst 82 % der besten 5 min
	## (gängige Faustregeln; ohne Ausbelastung nur eine untere Grenze). Die beste der vorhandenen Schätzungen zählt.
	var out := [0.0, ""]
	for r in [[1200, 0.95, "beste 20 min × 0,95"], [480, 0.90, "beste 8 min × 0,90"], [300, 0.82, "beste 5 min × 0,82"]]:
		var w: float = best(a, r[0]) * r[1]
		if w > out[0]:
			out = [w, r[2]]
	return out


func stats(ftp: float, workout: Dictionary) -> Dictionary:
	var n := p.size()
	var s := {"n": n, "ftp": ftp}
	if n == 0:
		return s
	var moving := 0
	var pw := 0.0
	var pn := 0
	var pmin := 1e9
	var cs := 0.0
	var cn := 0
	var hs := 0.0
	var hn := 0
	var asc := 0.0
	var a0 := alt[0]
	var zones := [0, 0, 0, 0, 0, 0]
	for i in n:
		moving += int(v[i] > 0.5)
		if p[i] > 0.0:
			pw += p[i]
			pn += 1
			pmin = minf(pmin, p[i])
			zones[preload("res://ui_theme.gd").zone(p[i] / maxf(ftp, 1.0))[0]] += 1
		if c[i] > 0.0:
			cs += c[i]
			cn += 1
		if hr[i] > 0.0:
			hs += hr[i]
			hn += 1
		if alt[i] - a0 > 1.0:  # Höhenmeter mit 1 m Schwelle (kein Rauschen)
			asc += alt[i] - a0
			a0 = alt[i]
		elif alt[i] < a0:
			a0 = alt[i]
	var total := 0.0
	for x in p:
		total += x
	s.duration = n
	s.moving = moving
	s.distance = d[n - 1] - d[0]
	s.ascent = asc
	s.speed_avg = s.distance / maxf(moving, 1)
	s.speed_max = Array(v).max()
	s.power_avg = total / n
	s.power_avg_pedal = pw / maxf(pn, 1)
	s.power_max = Array(p).max()
	s.power_min = pmin if pn > 0 else 0.0
	s.np = normalized(p)
	s.if_ = s.np / maxf(ftp, 1.0)
	s.tss = n / 3600.0 * s.if_ * s.if_ * 100.0
	s.kj = total / 1000.0
	s.cad_avg = cs / maxf(cn, 1)
	s.cad_max = Array(c).max()
	s.hr_avg = hs / maxf(hn, 1)
	s.hr_max = Array(hr).max()
	s.best = {5: best(p, 5), 60: best(p, 60), 300: best(p, 300), 1200: best(p, 1200)}
	var g := ftp_guess(p)
	if "rampentest" in str(workout.get("name", "")).to_lower():  # Stufentest: 75 % der besten Minute (TrainerRoad/Zwift)
		g = [best(p, 60) * 0.75, "Rampentest: beste 1 min × 0,75"]
	s.ftp_guess = g[0]
	s.ftp_basis = g[1]
	s.zones = zones
	# Intervalle: Ziel vs. Ist je Workout-Abschnitt (nach Workout-Zeit, nicht Fahrzeit)
	var iv := []
	for seg in workout.get("segments", []):
		if seg.free or seg.dur < 20.0:
			continue
		var sp := 0.0
		var st := 0.0
		var k := 0
		for i in n:
			if wt[i] >= seg.t0 and wt[i] < seg.t0 + seg.dur:
				sp += p[i]
				st += tgt[i]
				k += 1
		if k > 0:
			iv.append({"kind": seg.kind, "t0": seg.t0, "dur": seg.dur, "target": st / k, "actual": sp / k})
	s.intervals = iv
	return s


func tcx(name: String) -> String:
	## Garmin Training Center XML: eine Runde, ein Trackpoint je Sekunde, Leistung als ns3:Watts
	var iso := func(t: float) -> String: return Time.get_datetime_string_from_unix_time(int(t)) + "Z"
	var n := p.size()
	var kj := 0.0
	for x in p:
		kj += x / 1000.0
	var out := PackedStringArray()
	out.append('<?xml version="1.0" encoding="UTF-8"?>')
	out.append('<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">')
	out.append('<Activities><Activity Sport="Biking"><Id>%s</Id>' % iso.call(start_unix))
	out.append('<Lap StartTime="%s"><TotalTimeSeconds>%d</TotalTimeSeconds><DistanceMeters>%.1f</DistanceMeters><Calories>%d</Calories><Intensity>Active</Intensity><TriggerMethod>Manual</TriggerMethod><Track>'
		% [iso.call(start_unix), n, (d[n - 1] - d[0]) if n else 0.0, roundi(kj)])
	for i in n:
		var hb := "<HeartRateBpm><Value>%d</Value></HeartRateBpm>" % roundi(hr[i]) if hr[i] > 0.0 else ""
		out.append("<Trackpoint><Time>%s</Time><AltitudeMeters>%.1f</AltitudeMeters><DistanceMeters>%.1f</DistanceMeters>%s<Cadence>%d</Cadence><Extensions><ns3:TPX><ns3:Speed>%.2f</ns3:Speed><ns3:Watts>%d</ns3:Watts></ns3:TPX></Extensions></Trackpoint>"
			% [iso.call(start_unix + i), alt[i], d[i] - d[0], hb, roundi(c[i]), v[i], roundi(p[i])])
	out.append("</Track></Lap><Notes>%s</Notes></Activity></Activities></TrainingCenterDatabase>" % name.xml_escape())
	return "\n".join(out)


func save(name: String, stats_: Dictionary) -> String:
	## user://rides/<Datum>_<Name>.tcx (+ .json mit den Kennzahlen) -> Pfad der TCX-Datei ("" wenn nichts aufgezeichnet)
	if p.is_empty():
		return ""
	DirAccess.make_dir_recursive_absolute("user://rides")
	var local := int(start_unix) + int(Time.get_time_zone_from_system().get("bias", 0)) * 60  # Dateiname in Ortszeit
	var stamp := Time.get_datetime_string_from_unix_time(local).left(16).replace(":", "-").replace("T", "_")
	var re := RegEx.create_from_string("[^A-Za-z0-9-]+")
	var ascii := name
	for k in [["ä", "ae"], ["ö", "oe"], ["ü", "ue"], ["Ä", "Ae"], ["Ö", "Oe"], ["Ü", "Ue"], ["ß", "ss"], ["×", "x"], ["ø", "o"], ["é", "e"]]:
		ascii = ascii.replace(k[0], k[1])  # „Wüste“ -> Wueste statt W_ste
	var base := "user://rides/%s_%s" % [stamp, re.sub(ascii, "_", true).strip_edges().trim_suffix("_").left(40)]
	var f := FileAccess.open(base + ".tcx", FileAccess.WRITE)
	if f == null:
		return ""
	f.store_string(tcx(name))
	f.close()
	var j := FileAccess.open(base + ".json", FileAccess.WRITE)
	if j:
		j.store_string(JSON.stringify(stats_, "  "))
	return ProjectSettings.globalize_path(base + ".tcx")
