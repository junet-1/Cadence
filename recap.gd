extends Control
## Zusammenfassung am Workout-Ende und beim Beenden: Kennzahlen, Leistungskurve (Zonenfarben) mit Ziel und Puls, Bestwerte,
## Min/Max, Zeit in Zonen, Intervalle Soll/Ist, Speicherort der TCX-Datei. closed(finish): true = Fahrt beenden.

signal closed(finish: bool)

const UI := preload("res://ui_theme.gd")
const NAMES := {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
	"recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei"}

var log: RefCounted
var s := {}


func open(ride_log: RefCounted, stats: Dictionary, title: String, subtitle: String, path: String, can_continue: bool) -> void:
	log = ride_log
	s = stats
	theme = UI.theme()
	set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	mouse_filter = MOUSE_FILTER_STOP
	var dim := ColorRect.new()
	dim.color = Color(0, 0, 0, 0.6)
	dim.set_anchors_preset(PRESET_FULL_RECT)
	add_child(dim)
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", UI.box(UI.PANEL_SOLID, 20, 28, UI.LINE))
	panel.set_anchors_preset(PRESET_FULL_RECT)
	for side in ["left", "right"]:
		panel.set("offset_" + side, 70.0 if side == "left" else -70.0)
	panel.offset_top = 40
	panel.offset_bottom = -40
	add_child(panel)
	var scroll := ScrollContainer.new()  # kleine Fenster: scrollen statt abschneiden
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	panel.add_child(scroll)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 16)
	col.size_flags_horizontal = SIZE_EXPAND_FILL
	scroll.add_child(col)

	var head := HBoxContainer.new()
	col.add_child(head)
	var titles := VBoxContainer.new()
	titles.size_flags_horizontal = SIZE_EXPAND_FILL
	head.add_child(titles)
	titles.add_child(UI.label(title, 40, UI.TEXT, 700))
	titles.add_child(UI.label(subtitle, 20, UI.MUTED))
	if s.get("n", 0) == 0:
		col.add_child(UI.label("Keine Daten aufgezeichnet – erst treten, dann gibt es eine Auswertung.", 22, UI.MUTED))
		col.add_child(_buttons(path, can_continue))
		return

	var tiles := GridContainer.new()
	tiles.columns = 8
	tiles.add_theme_constant_override("h_separation", 12)
	col.add_child(tiles)
	var ftp: float = s.ftp
	for t in [["Ø Leistung", "%d" % roundi(s.power_avg), "W", UI.zone(s.power_avg / ftp)[2]],
			["Normalized Power", "%d" % roundi(s.np), "W", UI.zone(s.np / ftp)[2]],
			["FTP-Schätzung", "%d" % roundi(s.ftp_guess) if s.ftp_guess > 0 else "–", "W", UI.ACCENT],
			["Ø Trittfrequenz", "%d" % roundi(s.cad_avg), "rpm", UI.TEXT],
			["Ø Puls", "%d" % roundi(s.hr_avg) if s.hr_avg > 0 else "–", "bpm", UI.BAD],
			["Strecke", "%.1f" % (s.distance / 1000.0), "km", UI.TEXT],
			["Höhenmeter", "%d" % roundi(s.ascent), "m", UI.TEXT],
			["Belastung", "%d" % roundi(s.tss), "TSS", UI.TEXT]]:
		tiles.add_child(_tile(t[0], t[1], t[2], t[3]))

	col.add_child(UI.label("LEISTUNG (ZONENFARBE)  ·  ZIEL (WEISS)  ·  PULS (ROT)  ·  FTP (GESTRICHELT)", 13, UI.MUTED, 600))
	var chart := Control.new()
	chart.custom_minimum_size = Vector2(0, 200)
	chart.draw.connect(_draw_chart.bind(chart))
	col.add_child(chart)

	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 28)
	col.add_child(cols)
	var a := _section(cols, "Bestwerte und Spitzen")
	_rows(a, [["5 s", "%d W" % roundi(s.best[5])], ["1 min", "%d W" % roundi(s.best[60])], ["5 min", "%d W" % roundi(s.best[300])],
		["20 min", "%d W" % roundi(s.best[1200]) if s.best[1200] > 0 else "–"],
		["Leistung min / max", "%d / %d W" % [roundi(s.power_min), roundi(s.power_max)]],
		["Trittfrequenz max", "%d rpm" % roundi(s.cad_max)], ["Puls max", "%d bpm" % roundi(s.hr_max) if s.hr_max > 0 else "–"],
		["Tempo Ø / max", "%.1f / %.1f km/h" % [s.speed_avg * 3.6, s.speed_max * 3.6]],
		["Arbeit", "%d kJ · IF %.2f" % [roundi(s.kj), s.if_]], ["Zeit (fahrend)", "%s (%s)" % [UI.hms(s.duration), UI.hms(s.moving)]]])
	var note := UI.label("FTP %s, eingestellt %d W. Ohne Ausbelastung ist das nur eine untere Grenze." % [s.ftp_basis, roundi(ftp)] if s.ftp_guess > 0
		else "Für eine FTP-Schätzung braucht es mindestens 5 min am Stück.", 15, UI.MUTED)
	note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	note.custom_minimum_size.x = 360
	a.add_child(note)
	var z := _section(cols, "Zeit in Zonen (FTP %d W)" % roundi(ftp))
	var tot := 0
	for k in s.zones:
		tot += k
	for i in 6:
		z.add_child(_zone_bar(UI.ZONES[i][1], UI.ZONES[i][2], float(s.zones[i]) / maxf(tot, 1), s.zones[i]))
	var iv := _section(cols, "Intervalle: Ziel → Ist")
	var shown := 0
	for x in s.intervals:
		if shown >= 9:
			iv.add_child(UI.label("… %d weitere in der JSON-Datei" % (s.intervals.size() - shown), 15, UI.MUTED))
			break
		var pct: float = x.actual / maxf(x.target, 1.0)
		var c := UI.GOOD if absf(pct - 1.0) < 0.05 else UI.WARN if absf(pct - 1.0) < 0.12 else UI.BAD
		_rows(iv, [["%s %s" % [NAMES.get(x.kind, x.kind), UI.hms(x.dur)], "%d → %d W  %d %%" % [roundi(x.target), roundi(x.actual), roundi(pct * 100)]]], c)
		shown += 1
	if s.intervals.is_empty():
		iv.add_child(UI.label("Freie Fahrt – keine Zielvorgaben.", 17, UI.MUTED))
	col.add_child(_buttons(path, can_continue))


func _tile(name: String, value: String, unit: String, color: Color) -> Control:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", UI.box(Color(1, 1, 1, 0.04), 12, 12, UI.LINE))
	p.size_flags_horizontal = SIZE_EXPAND_FILL
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 0)
	p.add_child(v)
	v.add_child(UI.label(name.to_upper(), 13, UI.MUTED, 600))
	var row := HBoxContainer.new()
	row.add_child(UI.label(value, 36, color, 700))
	var u := UI.label(unit, 16, UI.MUTED)
	u.size_flags_vertical = SIZE_SHRINK_END
	row.add_child(u)
	v.add_child(row)
	return p


func _section(parent: Control, title: String) -> VBoxContainer:
	var v := VBoxContainer.new()
	v.size_flags_horizontal = SIZE_EXPAND_FILL
	v.add_theme_constant_override("separation", 6)
	v.add_child(UI.label(title.to_upper(), 14, UI.MUTED, 600))
	parent.add_child(v)
	return v


func _rows(parent: Control, rows: Array, color := UI.TEXT) -> void:
	for r in rows:
		var h := HBoxContainer.new()
		var k := UI.label(r[0], 17, UI.MUTED)
		k.size_flags_horizontal = SIZE_EXPAND_FILL
		h.add_child(k)
		h.add_child(UI.label(r[1], 17, color, 600))
		parent.add_child(h)


func _zone_bar(name: String, color: Color, frac: float, sec: int) -> Control:
	var h := HBoxContainer.new()
	var n := UI.label(name, 16, UI.MUTED)
	n.custom_minimum_size.x = 130
	h.add_child(n)
	var bar := Control.new()
	bar.custom_minimum_size = Vector2(0, 18)
	bar.size_flags_horizontal = SIZE_EXPAND_FILL
	bar.size_flags_vertical = SIZE_SHRINK_CENTER
	bar.draw.connect(func() -> void:
		bar.draw_rect(Rect2(Vector2.ZERO, bar.size), Color(1, 1, 1, 0.06))
		bar.draw_rect(Rect2(Vector2.ZERO, Vector2(bar.size.x * frac, bar.size.y)), color))
	h.add_child(bar)
	var t := UI.label("%s" % UI.hms(sec), 16, UI.TEXT)
	t.custom_minimum_size.x = 64
	t.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	h.add_child(t)
	return h


func _draw_chart(cv: Control) -> void:
	## Leistung (10-s-Mittel) als Säulen in Zonenfarbe, Ziel als weiße Stufenlinie, Puls rot, FTP gestrichelt
	var w := cv.size.x
	var h := cv.size.y
	cv.draw_rect(Rect2(Vector2.ZERO, cv.size), Color(1, 1, 1, 0.03))
	var n: int = log.size()
	var ftp: float = s.ftp
	var top := maxf(s.power_max * 1.05, ftp * 1.6)
	var cols := int(w / 3.0)
	var prev_t := Vector2(-1, -1)
	var prev_h := Vector2(-1, -1)
	for k in cols:
		var i0 := int(float(k) / cols * n)
		var i1 := maxi(int(float(k + 1) / cols * n), i0 + 1)
		var sp := 0.0
		var st := 0.0
		var sh := 0.0
		for i in range(i0, mini(i1, n)):
			sp += log.p[i]
			st += log.tgt[i]
			sh += log.hr[i]
		var m := float(i1 - i0)
		var pw := sp / m
		var x := k * 3.0
		var y := h - pw / top * h
		cv.draw_rect(Rect2(x, y, 2.4, h - y), Color(UI.zone(pw / ftp)[2], 0.9))
		var tp := Vector2(x, h - st / m / top * h)
		if st > 0.0 and prev_t.x >= 0.0:
			cv.draw_line(prev_t, tp, Color(1, 1, 1, 0.85), 2.0)
		prev_t = tp if st > 0.0 else Vector2(-1, -1)
		if sh > 0.0:
			var hp := Vector2(x, h - (sh / m - 60.0) / 140.0 * h)
			if prev_h.x >= 0.0:
				cv.draw_line(prev_h, hp, Color(UI.BAD, 0.9), 2.0)
			prev_h = hp
	var fy := h - ftp / top * h
	cv.draw_dashed_line(Vector2(0, fy), Vector2(w, fy), Color(1, 1, 1, 0.4), 1.0, 8.0)
	var f := cv.get_theme_default_font()  # gecachte Theme-Schrift (eine neue SystemFont je Zeichnen rendert Klötzchen)
	cv.draw_string(f, Vector2(8, fy - 6), "FTP %d W" % roundi(ftp), HORIZONTAL_ALIGNMENT_LEFT, -1, 14, Color(1, 1, 1, 0.6))
	for frac in [0.25, 0.5, 0.75]:
		var x: float = w * frac
		cv.draw_string(f, Vector2(x + 4, h - 6), UI.hms(n * frac), HORIZONTAL_ALIGNMENT_LEFT, -1, 13, UI.MUTED)


func _buttons(path: String, can_continue: bool) -> Control:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 12)
	var info := UI.label("Gespeichert: %s  (TCX – zu Strava, Garmin Connect oder intervals.icu hochladen)" % path if path != "" else "", 15, UI.MUTED)
	info.size_flags_horizontal = SIZE_EXPAND_FILL
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	h.add_child(info)
	if path != "":
		var open_dir := Button.new()
		open_dir.text = "Ordner öffnen"
		open_dir.pressed.connect(func() -> void: OS.shell_open(path.get_base_dir()))
		h.add_child(open_dir)
	if can_continue:
		var cont := Button.new()
		cont.text = "Weiterfahren"
		cont.pressed.connect(func() -> void: closed.emit(false))
		h.add_child(cont)
	var done := Button.new()
	done.text = "Fahrt beenden"
	done.add_theme_stylebox_override("normal", UI.box(UI.ACCENT, 10, 10))
	done.add_theme_stylebox_override("hover", UI.box(UI.ACCENT.lightened(0.15), 10, 10))
	done.add_theme_color_override("font_color", Color.BLACK)
	done.add_theme_color_override("font_hover_color", Color.BLACK)
	done.pressed.connect(func() -> void: closed.emit(true))
	h.add_child(done)
	done.call_deferred("grab_focus")
	return h
