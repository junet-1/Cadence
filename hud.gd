extends Control
## Fahr-HUD: Kacheln oben (Leistung 3-s-Mittel, Trittfrequenz, Tempo, Puls), Workout-Panel links (Intervall, Ziel, Restzeit,
## Fortschritt, nächstes), Strecken-Panel rechts (km, Steigung, Höhe, Zeit, Modus, Wetter mit Windpfeil), Workout-Leiste unten,
## Einblendungen (Intervallwechsel, Region), Ladeanzeige, Pausemenü (Esc). Liest nur aus main.gd, redet nie mit dem Trainer.

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
var status: Label
var diag: Label
var peer: Label
var bar: Control
var menu: Control
var loading: Control
var loading_text: Label
var seg_i := -2
var region := ""


func _ready() -> void:
	theme = UI.theme()
	set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	mouse_filter = MOUSE_FILTER_IGNORE
	loading = _center_panel()
	var lv := VBoxContainer.new()
	lv.add_theme_constant_override("separation", 10)
	loading.get_child(0).add_child(lv)
	lv.add_child(UI.label("Welt wird erzeugt …", 34, UI.TEXT, 700))
	loading_text = UI.label("", 18, UI.MUTED)
	lv.add_child(loading_text)


func show_loading(text: String) -> void:
	loading.visible = true
	loading_text.text = text


func setup(main: Node) -> void:
	## Nach dem Laden der Welt: alle Panels bauen
	m = main
	loading.visible = false
	# Kacheln oben Mitte
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	top.set_anchors_preset(PRESET_CENTER_TOP)
	top.offset_top = 18
	top.grow_horizontal = GROW_DIRECTION_BOTH
	add_child(top)
	for k in [["power", "Leistung", "W", 240], ["cad", "Trittfrequenz", "rpm", 170], ["speed", "Tempo", "km/h", 170], ["hr", "Puls", "bpm", 150]]:
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
	for k in ["dist", "elev", "time", "mode", "region"]:
		ride[k] = _kv(rv, {"dist": "Strecke", "elev": "Höhe", "time": "Fahrzeit", "mode": "Trainer", "region": "Gegend"}[k])
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
	status = UI.label("", 15, UI.MUTED)
	status.set_anchors_preset(PRESET_BOTTOM_LEFT)
	status.offset_left = 22
	status.offset_top = -34
	add_child(status)
	peer = UI.label("", 20, Color(0.45, 0.7, 1.0), 600)
	peer.position = Vector2(24, 330)
	add_child(peer)
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
	menu = _center_panel()
	menu.mouse_filter = MOUSE_FILTER_STOP
	menu.visible = false
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 12)
	v.custom_minimum_size.x = 420
	menu.get_child(0).add_child(v)
	v.add_child(UI.label("Pause", 36, UI.TEXT, 700))
	v.add_child(UI.label("Der Trainer läuft weiter – treten oder nicht, wie du magst.", 16, UI.MUTED))
	for b in [["Weiterfahren", func() -> void: menu.visible = false],
			["Fahrt beenden und auswerten", func() -> void:
				menu.visible = false
				finish_requested.emit()],
			["Zum Startbildschirm (ohne Auswertung)", func() -> void: leave_requested.emit()]]:
		var btn := Button.new()
		btn.text = b[0]
		btn.custom_minimum_size.y = 48
		btn.pressed.connect(b[1])
		v.add_child(btn)
	v.add_child(UI.label("Tasten: W Wetter · ↑ ↓ Gang bzw. ERG ±5 % · F3 Diagnose · Esc Pause", 14, UI.MUTED))


func toggle_menu() -> void:
	if menu:
		menu.visible = not menu.visible
		if menu.visible:
			menu.get_child(0).get_child(0).get_child(2).grab_focus()


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
	tiles.power.value.add_theme_color_override("font_color", pz[2] if p3 > 1.0 else UI.TEXT)
	if tgt > 0.0:
		var dv := roundi(p3 - tgt)
		tiles.power.sub.text = "Ziel %d W  %s" % [roundi(tgt), ("%+d" % dv) if absi(dv) >= 5 else "✓"]
		tiles.power.sub.add_theme_color_override("font_color", UI.GOOD if absi(dv) < 10 else UI.WARN if absi(dv) < 25 else UI.BAD)
	else:
		tiles.power.sub.text = "%s · %d %% FTP" % [pz[1], roundi(p3 / maxf(ftp, 1.0) * 100)]
		tiles.power.sub.add_theme_color_override("font_color", UI.MUTED)
	tiles.cad.value.text = "%d" % roundi(m.cadence)
	tiles.speed.value.text = "%.1f" % (m.speed * 3.6)
	tiles.hr.panel.visible = m.hr > 0.0
	tiles.hr.value.text = "%d" % roundi(m.hr)
	# Strecke
	var g: float = m.grade_at(m.dist)
	ride.grade.text = "%+.1f %%" % g
	ride.grade.add_theme_color_override("font_color", UI.TEXT if g < 3.0 else UI.WARN if g < 7.0 else UI.BAD if g >= 7.0 else UI.TEXT)
	if m.world:
		ride.dist.text = "%.2f km" % (m.dist / 1000.0)
	else:
		ride.dist.text = "%.2f / %.1f km" % [fposmod(m.dist, m.route_len) / 1000.0, m.route_len / 1000.0]
	ride.elev.text = "%d m ü. M." % roundi(m.curve.sample_baked(fposmod(m.dist, m.route_len)).y + m.world_meta.get("elev0", 0))
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
	# Kollege, Status, Diagnose, Einblendung
	peer.visible = m.ghost.visible
	peer.text = "%s  %+d m" % [m.peer_name, roundi(m.peer_dist - m.dist)]
	status.text = m.status
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
		wo.prog.value = 100
		wo.next.text = "Trainer jetzt im SIM-Modus (Steigung)"
		return
	for i in segs.size():
		var s: Dictionary = segs[i]
		if t < s.t0 + s.dur:
			var left: float = s.t0 + s.dur - t
			var col: Color = preload("res://workout_bar.gd").COLORS.get(s.kind, UI.TEXT)
			wo.name.text = "%s · INTERVALL %d/%d" % [NAMES.get(s.kind, s.kind).to_upper(), i + 1, segs.size()]
			wo.name.add_theme_color_override("font_color", col)
			wo.target.text = "frei fahren" if s.free else "%d W  ·  %d %% FTP" % [roundi(tgt), roundi(tgt / maxf(ftp, 1.0) * 100)]
			wo.left.text = UI.hms(left)
			wo.prog.value = (t - s.t0) / s.dur * 100.0
			if m.power <= 0.0 and m.cadence <= 0.0:
				wo.next.text = "Pause – zum Weitermachen treten"
				wo.next.add_theme_color_override("font_color", UI.WARN)
			elif i + 1 < segs.size():
				var n: Dictionary = segs[i + 1]
				var nt := "frei" if n.free else "%d W" % roundi(n.p0 * ftp * (1.0 + 0.05 * m.erg_bias))
				wo.next.text = ("Gleich: " if left <= 10.0 else "Danach: ") + "%s %s · %s" % [NAMES.get(n.kind, n.kind), nt, UI.hms(n.dur)]
				wo.next.add_theme_color_override("font_color", UI.ACCENT if left <= 10.0 else UI.MUTED)
			else:
				wo.next.text = "Letztes Intervall"
				wo.next.add_theme_color_override("font_color", UI.MUTED)
			if i != seg_i:
				if seg_i != -2:
					flash("%s · %s · %s" % [NAMES.get(s.kind, s.kind), "frei" if s.free else "%d W" % roundi(tgt), UI.hms(s.dur)], col)
				seg_i = i
			return


func _draw_wind() -> void:
	## Pfeil: wohin der Wind weht, relativ zur Fahrtrichtung (oben = mit dir = Rückenwind)
	var c := wind_arrow.size / 2.0
	var d := Vector2.UP.rotated(wind_rel)
	var col := UI.GOOD if absf(wind_rel) < PI / 3.0 else UI.BAD if absf(wind_rel) > PI * 2.0 / 3.0 else UI.WARN
	wind_arrow.draw_circle(c, 14.0, Color(1, 1, 1, 0.08))
	wind_arrow.draw_line(c - d * 10.0, c + d * 10.0, col, 3.0)
	wind_arrow.draw_colored_polygon(PackedVector2Array([c + d * 12.0, c + d * 4.0 + d.orthogonal() * 6.0, c + d * 4.0 - d.orthogonal() * 6.0]), col)
