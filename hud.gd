extends Control
## Fahr-HUD: Kacheln oben (Leistung 3-s-Mittel, Trittfrequenz, Tempo, Puls), Workout-Panel links (Intervall, Ziel, Restzeit,
## Fortschritt, nächstes), Strecken-Panel rechts (km, Steigung, Höhe, Zeit, Modus, Wetter mit Windpfeil), Workout-Leiste unten,
## Einblendungen (Intervallwechsel, Region), Trainer-Status unten links, Ladebildschirm bis die Welt um den Start steht (auch
## Fehler des Generators), Pausemenü (Esc). Liest nur aus main.gd, redet nie mit dem Trainer.

signal finish_requested
signal leave_requested

const UI := preload("res://ui_theme.gd")
const NAMES := {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
	"recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei"}

var m: Node  # main.gd
var p3 := 0.0  # Leistung, 3-s-Mittel (gleitend)
var tiles := {}
var wo := {}
var ride := {}
var wind_arrow: Control
var wind_rel := 0.0  # Windrichtung relativ zur Fahrtrichtung (rad, 0 = Rückenwind)
var toast: Label
var toast_t := 0.0
var chip: PanelContainer  # Trainer-Status: Punkt + Text
var chip_dot: Control
var chip_text: Label
var diag: Label
var bar: Control
var menu: Control
var loading: Control
var loading_title: Label
var loading_ctx: Label
var loading_text: Label
var loading_btns: Control
var loading_bar: ProgressBar
var loading_t := 0.0
var seg_i := -2
var region := ""


func _ready() -> void:
	theme = UI.theme()
	set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	mouse_filter = MOUSE_FILTER_IGNORE
	# Ladebildschirm: deckend, bis die Welt um den Start steht (sonst sieht man Fahrer und HUD über halb gebautem Gelände)
	loading = Control.new()
	loading.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	loading.mouse_filter = MOUSE_FILTER_STOP
	add_child(loading)
	var bg := ColorRect.new()
	bg.color = UI.BG
	bg.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	loading.add_child(bg)
	var c := _center_panel()
	c.reparent(loading)
	var lv := VBoxContainer.new()
	lv.add_theme_constant_override("separation", 10)
	lv.custom_minimum_size.x = 520
	c.get_child(0).add_child(lv)
	lv.add_child(UI.label("CADENCE", 16, UI.ACCENT, 800))
	loading_title = UI.label("Welt wird erzeugt …", 34, UI.TEXT, 700)
	lv.add_child(loading_title)
	loading_ctx = UI.label("", 18, UI.TEXT)
	lv.add_child(loading_ctx)
	loading_bar = ProgressBar.new()  # pendelt: Fortschritt ohne bekanntes Ende
	loading_bar.show_percentage = false
	loading_bar.custom_minimum_size.y = 6
	lv.add_child(loading_bar)
	loading_text = UI.label("", 16, UI.MUTED)
	loading_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	lv.add_child(loading_text)
	loading_btns = HBoxContainer.new()
	loading_btns.add_theme_constant_override("separation", 12)
	loading_btns.visible = false
	for b in [["Fahrt beenden und auswerten", func() -> void: finish_requested.emit()], ["Zum Startbildschirm (ohne Auswertung)", func() -> void: leave_requested.emit()]]:
		var btn := Button.new()
		btn.text = b[0]
		btn.pressed.connect(b[1])
		loading_btns.add_child(btn)
	lv.add_child(loading_btns)
	# Trainer-Status unten links (auch schon beim Laden: Trainer kann sich verbinden, während die Welt entsteht)
	chip = PanelContainer.new()
	chip.add_theme_stylebox_override("panel", UI.box(UI.PANEL, 16, 8, UI.LINE))
	chip.set_anchors_preset(PRESET_BOTTOM_LEFT)
	chip.offset_left = 20
	chip.offset_top = -62
	chip.offset_bottom = -20
	chip.grow_vertical = GROW_DIRECTION_BEGIN
	add_child(chip)
	var ch := HBoxContainer.new()
	ch.add_theme_constant_override("separation", 8)
	chip.add_child(ch)
	chip_dot = Control.new()
	chip_dot.custom_minimum_size = Vector2(12, 12)
	chip_dot.size_flags_vertical = SIZE_SHRINK_CENTER
	chip_dot.draw.connect(func() -> void: chip_dot.draw_circle(chip_dot.size / 2.0, 5.0, chip_dot.get_meta("c", UI.WARN)))
	ch.add_child(chip_dot)
	chip_text = UI.label("Warte auf die Trainer-Bridge …", 15, UI.TEXT)
	ch.add_child(chip_text)


func update_loading(main: Node, delta: float) -> void:
	## Jeden Frame (auch vor dem Kopf der Welt): Ladebildschirm, solange die Welt nicht steht, oder Generatorfehler
	loading_t += delta
	var w: Node = main.world
	var failed: String = w.failed if w else ""
	loading.visible = w != null and (not w.world_ready or failed != "")
	_chip(main)
	if not loading.visible:
		return
	var bar := loading_bar
	bar.value = (0.5 + 0.5 * sin(loading_t * 2.5)) * 100.0
	loading_ctx.text = str(main.world_meta.get("name", ""))
	if failed != "":
		loading_title.text = "Weltgenerator gestoppt"
		loading_title.add_theme_color_override("font_color", UI.BAD)
		loading_text.text = failed + "\nDie bisherige Fahrt kannst du auswerten und speichern."
		bar.visible = false
		loading_btns.visible = true
		if main.recap == null and get_viewport().gui_get_focus_owner() == null:
			loading_btns.get_child(0).grab_focus()
		return
	if main.world_meta.is_empty():
		loading_text.text = "Generator startet … %d s" % loading_t
	else:
		loading_text.text = "Gelände um den Start: %d Kacheln gebaut · %d s" % [w.nodes.size(), loading_t]


func _chip(main: Node) -> void:
	var fresh: bool = not main.sensor_lost
	var st: String = main.status
	var c: Color = UI.GOOD if fresh else UI.WARN
	chip_text.text = (st if st.begins_with("Trainer verbunden") or st.begins_with("Fake") else "Trainer liefert Daten") if fresh \
		else (st if st != "" else "Warte auf die Trainer-Bridge …")
	if not fresh and main.data_t > 0.0:  # hatte schon Daten -> verloren
		c = UI.BAD
		chip_text.text = "Kein Signal vom Trainer" + (" · " + st if st != "" else "")
	if chip_text.text.length() > 60:
		chip_text.text = chip_text.text.left(58) + " …"
	if chip_dot.get_meta("c", Color.BLACK) != c:
		chip_dot.set_meta("c", c)
		chip_dot.queue_redraw()


func setup(main: Node) -> void:
	## Nach dem Laden der Welt: alle Panels bauen
	m = main
	# Kacheln oben Mitte
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	top.set_anchors_preset(PRESET_CENTER_TOP)
	top.offset_top = 18
	top.grow_horizontal = GROW_DIRECTION_BOTH
	add_child(top)
	for k in [["power", "Leistung", "W", 220], ["cad", "Trittfrequenz", "rpm", 150], ["speed", "Tempo", "km/h", 160], ["hr", "Puls", "bpm", 140]]:
		tiles[k[0]] = _tile(top, k[1], k[2], k[3])
	# Workout links oben
	var wp := PanelContainer.new()
	wp.position = Vector2(20, 18)
	wp.custom_minimum_size.x = 380
	add_child(wp)
	var wv := VBoxContainer.new()
	wv.add_theme_constant_override("separation", 2)
	wp.add_child(wv)
	wo.name = UI.label("", 15, UI.MUTED, 600)
	wo.target = UI.label("", 30, UI.TEXT, 700)
	wo.left = UI.label("", 56, UI.TEXT, 700)
	wo.prog = ProgressBar.new()
	wo.prog.show_percentage = false
	wo.prog.custom_minimum_size.y = 8
	wo.next = UI.label("", 17, UI.MUTED)
	wo.total = UI.label("", 15, UI.MUTED)
	for k in ["name", "target", "left", "prog", "next", "total"]:
		wv.add_child(wo[k])
	wo.panel = wp
	# Strecke rechts oben
	var rp := PanelContainer.new()
	rp.set_anchors_preset(PRESET_TOP_RIGHT)
	rp.offset_left = -330
	rp.offset_right = -20
	rp.offset_top = 18
	rp.grow_horizontal = GROW_DIRECTION_BEGIN
	add_child(rp)
	var rv := VBoxContainer.new()
	rv.add_theme_constant_override("separation", 4)
	rp.add_child(rv)
	var gh := HBoxContainer.new()
	ride.grade = UI.label("", 44, UI.TEXT, 700)
	gh.add_child(ride.grade)
	var gl := UI.label("Steigung", 15, UI.MUTED, 600)
	gl.size_flags_vertical = SIZE_SHRINK_END
	gh.add_child(gl)
	rv.add_child(gh)
	for k in ["dist", "elev", "time", "mode", "region", "peer"]:
		ride[k] = _kv(rv, {"dist": "Strecke", "elev": "Höhe", "time": "Fahrzeit", "mode": "Trainer", "region": "Gegend", "peer": "Kollege"}[k])
	var wh := HBoxContainer.new()
	wh.add_theme_constant_override("separation", 8)
	wind_arrow = Control.new()
	wind_arrow.custom_minimum_size = Vector2(30, 30)
	wind_arrow.draw.connect(_draw_wind)
	wh.add_child(wind_arrow)
	ride.weather = UI.label("", 16, UI.TEXT)
	ride.weather.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	ride.weather.custom_minimum_size.x = 240
	wh.add_child(ride.weather)
	rv.add_child(wh)
	# Workout-Leiste unten
	if not m.workout.is_empty():
		bar = preload("res://workout_bar.gd").new()
		bar.setup(m.workout)
		add_child(bar)
	else:
		wp.visible = false
	# Einblendung, Status, Kollege, Diagnose
	toast = UI.label("", 34, UI.TEXT, 700)
	toast.set_anchors_preset(PRESET_CENTER)
	toast.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	toast.grow_horizontal = GROW_DIRECTION_BOTH
	toast.offset_top = -170
	toast.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.8))
	toast.add_theme_constant_override("outline_size", 10)
	toast.modulate.a = 0.0
	add_child(toast)
	diag = UI.label("", 14, UI.TEXT)
	diag.add_theme_font_override("font", SystemFont.new())
	diag.set_anchors_preset(PRESET_BOTTOM_RIGHT)
	diag.grow_horizontal = GROW_DIRECTION_BEGIN
	diag.grow_vertical = GROW_DIRECTION_BEGIN
	diag.offset_right = -20
	diag.offset_bottom = -130
	diag.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	diag.add_theme_color_override("font_outline_color", Color.BLACK)
	diag.add_theme_constant_override("outline_size", 6)
	add_child(diag)
	_build_menu()
	move_child(loading, -1)  # Ladebildschirm über allem, Trainer-Status darüber
	move_child(chip, -1)


func _center_panel() -> Control:
	var c := CenterContainer.new()
	c.set_anchors_preset(PRESET_FULL_RECT)
	c.mouse_filter = MOUSE_FILTER_IGNORE
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", UI.box(UI.PANEL, 18, 28, UI.LINE))
	c.add_child(p)
	add_child(c)
	return c


func _tile(parent: Control, name: String, unit: String, w: int) -> Dictionary:
	var p := PanelContainer.new()
	p.custom_minimum_size.x = w
	parent.add_child(p)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", -4)
	p.add_child(v)
	v.add_child(UI.label(name.to_upper(), 13, UI.MUTED, 600))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	var val := UI.label("–", 52, UI.TEXT, 700)
	row.add_child(val)
	var u := UI.label(unit, 17, UI.MUTED)
	u.size_flags_vertical = SIZE_SHRINK_END
	u.offset_bottom = -8
	row.add_child(u)
	v.add_child(row)
	var sub := UI.label("", 15, UI.MUTED)
	v.add_child(sub)
	return {"panel": p, "value": val, "sub": sub}


func _kv(parent: Control, key: String) -> Label:
	var h := HBoxContainer.new()
	var k := UI.label(key, 17, UI.MUTED)
	k.size_flags_horizontal = SIZE_EXPAND_FILL
	h.add_child(k)
	var v := UI.label("", 17, UI.TEXT, 600)
	v.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	h.add_child(v)
	parent.add_child(h)
	return v


func _build_menu() -> void:
	menu = Control.new()
	menu.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	menu.mouse_filter = MOUSE_FILTER_STOP
	menu.visible = false
	add_child(menu)
	var dim := ColorRect.new()  # Welt abdunkeln: klar, dass gerade nichts weiterläuft
	dim.color = Color(0, 0, 0, 0.35)
	dim.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	dim.mouse_filter = MOUSE_FILTER_IGNORE
	menu.add_child(dim)
	var center := _center_panel()
	center.reparent(menu)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 12)
	v.custom_minimum_size.x = 440
	center.get_child(0).add_child(v)
	v.add_child(UI.label("Pause", 36, UI.TEXT, 700))
	var hint := UI.label("Strecke, Fahrzeit, Workout-Uhr und Aufzeichnung stehen. Der Trainer behält den aktuellen Widerstand.", 16, UI.MUTED)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(hint)
	for b in [["Weiterfahren", func() -> void: menu.visible = false],
			["Einstellungen …", func() -> void: add_child(preload("res://options.gd").new())],
			["Fahrt beenden und auswerten", func() -> void:
				menu.visible = false
				finish_requested.emit()],
			["Zum Startbildschirm (ohne Auswertung)", func() -> void: leave_requested.emit()]]:
		var btn := Button.new()
		btn.text = b[0]
		btn.custom_minimum_size.y = 48
		btn.pressed.connect(b[1])
		v.add_child(btn)
	var keys := UI.label("Tasten: W Wetter · M Ton · ↑ ↓ Gang bzw. ERG ±5 % · F3 Diagnose · F1 Freiflug · Esc Pause", 14, UI.MUTED)
	keys.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(keys)


func toggle_menu() -> void:
	if menu and not loading.visible:
		menu.visible = not menu.visible
		if menu.visible:
			menu.get_child(1).get_child(0).get_child(0).get_child(2).grab_focus()


func flash(text: String, color := UI.TEXT) -> void:
	toast.text = text
	toast.add_theme_color_override("font_color", color)
	toast_t = 4.0


func refresh(delta: float) -> void:
	if m == null:
		return
	p3 = lerpf(p3, m.power, 1.0 - exp(-delta / 1.5))
	var ftp: float = m.ftp
	var tgt: float = m.target_watts() if not m.workout.is_empty() else 0.0
	# Kacheln
	var pz: Array = UI.zone(p3 / maxf(ftp, 1.0))
	tiles.power.value.text = "%d" % roundi(p3)
	var zc: Color = pz[2] if p3 > 1.0 else UI.LINE
	if tiles.power.get("zone_c", Color.BLACK) != zc:  # Stylebox nur bei Zonenwechsel neu
		tiles.power.zone_c = zc
		var sb := UI.box(UI.PANEL, 16, 18, zc)
		sb.set_border_width_all(2 if p3 > 1.0 else 1)
		tiles.power.panel.add_theme_stylebox_override("panel", sb)
	if tgt > 0.0:
		var dv := roundi(p3 - tgt)
		tiles.power.sub.text = "Ziel %d W  %s" % [roundi(tgt), ("%+d" % dv) if absi(dv) >= 5 else "✓"]
		_col(tiles.power.sub, UI.GOOD if absi(dv) < 10 else UI.WARN if absi(dv) < 25 else UI.BAD)
	else:
		tiles.power.sub.text = "%s · %d %% FTP" % [pz[1], roundi(p3 / maxf(ftp, 1.0) * 100)]
		_col(tiles.power.sub, UI.MUTED)
	tiles.cad.value.text = "%d" % roundi(m.cadence)
	tiles.speed.value.text = "%.1f" % (m.speed * 3.6)
	tiles.hr.panel.visible = m.hr > 0.0
	tiles.hr.value.text = "%d" % roundi(m.hr)
	# Strecke
	var g: float = m.grade_at(m.dist)
	ride.grade.text = "%+.1f %%" % g
	_col(ride.grade, UI.TEXT if g < 3.0 else UI.WARN if g < 7.0 else UI.BAD)
	if m.world:
		ride.dist.text = "%.2f km" % (m.dist / 1000.0)
	else:
		ride.dist.text = "%.2f / %.1f km" % [fposmod(m.dist, m.route_len) / 1000.0, m.route_len / 1000.0]
	ride.elev.text = ("%d m ü. M." if m.world_meta.has("elev0") else "%d m") % roundi(m.curve.sample_baked(fposmod(m.dist, m.route_len)).y + m.world_meta.get("elev0", 0))
	ride.time.text = UI.hms(m.elapsed)
	ride.mode.text = ("ERG %+d %%" % (m.erg_bias * 5)) if m.erg and tgt > 0.0 else "SIM · Gang %+d" % m.gear
	var reg: String = m._region() if m.world else ""
	ride.region.text = reg
	ride.region.get_parent().visible = reg != ""
	if reg != region and reg != "":
		if region != "":
			flash(reg, UI.TEXT)
		region = reg
	var fwd: Vector3 = -m.rider.global_basis.z
	ride.weather.text = m.weather.describe(fwd)
	var w2: Vector2 = m.weather.wind
	wind_rel = Vector2(fwd.x, fwd.z).angle_to(w2) if w2.length() > 0.3 else 0.0
	wind_arrow.queue_redraw()
	# Workout
	if not m.workout.is_empty():
		_workout(m.wo_time, tgt, ftp)
		bar.t = m.wo_time
	# Kollege, Diagnose, Einblendung
	ride.peer.get_parent().visible = m.ghost.visible
	if m.ghost.visible:
		var gap: float = m.peer_dist - m.dist
		ride.peer.text = "%s · %s" % [m.peer_name, ("%d m vor dir" % roundi(gap)) if gap >= 0.5 else ("%d m hinter dir" % roundi(-gap))]
	update_loading(m, delta)
	diag.visible = m.diag and m.world != null
	if diag.visible:
		diag.text = m.world.diagnostics()
	toast_t -= delta
	toast.modulate.a = clampf(toast_t, 0.0, 1.0)


func _workout(t: float, tgt: float, ftp: float) -> void:
	var segs: Array = m.workout.segments
	var total: float = m.workout.duration
	wo.total.text = "%s / %s" % [UI.hms(t), UI.hms(total)]
	if t >= total:
		wo.name.text = "WORKOUT GESCHAFFT"
		wo.target.text = "Frei weiterfahren"
		wo.left.text = UI.hms(t - total)
		_col(wo.left, UI.GOOD)
		wo.prog.value = 100
		wo.next.text = "Trainer jetzt im SIM-Modus (Steigung)"
		return
	for i in segs.size():
		var s: Dictionary = segs[i]
		if t < s.t0 + s.dur:
			var left: float = s.t0 + s.dur - t
			var col: Color = preload("res://workout_bar.gd").color(s.p0, s.p1, s.free)
			wo.name.text = "%s · INTERVALL %d/%d" % [NAMES.get(s.kind, s.kind).to_upper(), i + 1, segs.size()]
			_col(wo.name, col)
			wo.target.text = "frei fahren" if s.free else "%d W  ·  %d %% FTP" % [roundi(tgt), roundi(tgt / maxf(ftp, 1.0) * 100)]
			wo.left.text = UI.hms(left)
			_col(wo.left, UI.ACCENT if left <= 10.0 else UI.TEXT)  # Wechsel steht an
			wo.prog.value = (t - s.t0) / s.dur * 100.0
			if m.power <= 0.0 and m.cadence <= 0.0:
				wo.next.text = "Pause – zum Weitermachen treten"
				_col(wo.next, UI.WARN)
			elif i + 1 < segs.size():
				var n: Dictionary = segs[i + 1]
				var nt := "frei" if n.free else "%d W" % roundi(n.p0 * ftp * (1.0 + 0.05 * m.erg_bias))
				wo.next.text = ("Gleich: " if left <= 10.0 else "Danach: ") + "%s %s · %s" % [NAMES.get(n.kind, n.kind), nt, UI.hms(n.dur)]
				_col(wo.next, UI.ACCENT if left <= 10.0 else UI.MUTED)
			else:
				wo.next.text = "Letztes Intervall"
				_col(wo.next, UI.MUTED)
			if i != seg_i:
				if seg_i != -2:
					flash("%s · %s · %s" % [NAMES.get(s.kind, s.kind), "frei" if s.free else "%d W" % roundi(tgt), UI.hms(s.dur)], col)
				seg_i = i
			return


func _col(l: Label, c: Color) -> void:
	## Schriftfarbe nur bei Änderung setzen (ein Override je Frame lässt das Label seinen Text neu formen)
	if l.get_meta("c", Color(0, 0, 0, 0)) != c:
		l.set_meta("c", c)
		l.add_theme_color_override("font_color", c)


func _draw_wind() -> void:
	## Pfeil: wohin der Wind weht, relativ zur Fahrtrichtung (oben = mit dir = Rückenwind)
	var c := wind_arrow.size / 2.0
	var d := Vector2.UP.rotated(wind_rel)
	var col := UI.GOOD if absf(wind_rel) < PI / 3.0 else UI.BAD if absf(wind_rel) > PI * 2.0 / 3.0 else UI.WARN
	wind_arrow.draw_circle(c, 14.0, Color(1, 1, 1, 0.08))
	wind_arrow.draw_line(c - d * 10.0, c + d * 10.0, col, 3.0)
	wind_arrow.draw_colored_polygon(PackedVector2Array([c + d * 12.0, c + d * 4.0 + d.orthogonal() * 6.0, c + d * 4.0 - d.orthogonal() * 6.0]), col)
