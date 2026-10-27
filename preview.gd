extends PanelContainer
## 3D-Vorschau in der Streckenwahl: Gelände (Streckenpaket oder `route.py --preview`), Strecke, Start/Ziel, Höhenprofil.
## Echte Höhen und Distanzen (Überhöhung nur per Schalter, beschriftet). Nur Anzeige: kein Kontakt zur Trainer-Bridge.
## Maus im 3D-Bild: links ziehen = drehen, rechts/mitte ziehen = schieben, Rad = Zoom.

signal ride_requested

const Main := preload("res://main.gd")
const UI := preload("res://ui_theme.gd")
const STEP := 5.0  # m zwischen Routenpunkten (route.py)

var viewport := SubViewport.new()
var view := TextureRect.new()  # zeigt viewport in Bildschirmpixeln (SubViewportContainer kennt nur ganzzahlige Stretch-Faktoren)
var content: Node3D
var cam := Camera3D.new()
var marker := MeshInstance3D.new()
var profile := Profile.new()
var title := Label.new()
var stats := Label.new()
var exaggerate := CheckButton.new()

var pts := PackedVector3Array()
var bounds := AABB()
var target := Vector3.ZERO
var yaw := 0.6
var pitch := 0.7  # rad über dem Horizont
var dist := 3000.0


func _ready() -> void:
	visible = false
	add_theme_stylebox_override("panel", UI.box(UI.PANEL_SOLID, 16, 14, UI.LINE))
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 10)
	add_child(box)
	var bar := HBoxContainer.new()
	bar.add_theme_constant_override("separation", 8)
	box.add_child(bar)
	var head := VBoxContainer.new()
	head.size_flags_horizontal = SIZE_EXPAND_FILL
	head.add_theme_constant_override("separation", 0)
	bar.add_child(head)
	title.add_theme_font_size_override("font_size", 24)
	title.add_theme_font_override("font", UI.font(700))
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title)
	stats.add_theme_font_size_override("font_size", 16)
	stats.add_theme_color_override("font_color", UI.MUTED)
	stats.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(stats)
	var fit := Button.new()
	fit.text = "Einpassen"
	fit.tooltip_text = "Kamera auf die ganze Strecke"
	fit.pressed.connect(frame)
	bar.add_child(fit)
	exaggerate.text = "Höhe ×2"
	exaggerate.tooltip_text = "Nur das 3D-Bild wird überhöht; Profil und Zahlen bleiben echt"
	exaggerate.toggled.connect(func(_on: bool) -> void: _apply_scale())
	bar.add_child(exaggerate)
	var ride := UI.primary(Button.new())
	ride.text = "Losfahren"
	ride.pressed.connect(func() -> void: ride_requested.emit())
	bar.add_child(ride)
	var close := Button.new()
	close.text = "✕"
	close.tooltip_text = "Schließen (Esc)"
	close.pressed.connect(hide)
	bar.add_child(close)
	for b in bar.get_children():
		if b is Button:
			b.size_flags_vertical = SIZE_SHRINK_CENTER
			b.focus_mode = FOCUS_NONE  # Esc/Leertaste sollen nicht an einem Knopf hängen bleiben

	view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	view.stretch_mode = TextureRect.STRETCH_SCALE
	view.size_flags_vertical = SIZE_EXPAND_FILL
	view.mouse_filter = MOUSE_FILTER_STOP
	view.gui_input.connect(_view_input)
	view.resized.connect(_fit_viewport)
	box.add_child(view)
	viewport.own_world_3d = true
	viewport.msaa_3d = Viewport.MSAA_4X
	add_child(viewport)  # rendert nur, solange view die Textur zeichnet (UPDATE_WHEN_VISIBLE)
	view.texture = viewport.get_texture()
	get_viewport().size_changed.connect(_fit_viewport)
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()
	var sky := ProceduralSkyMaterial.new()
	sky.ground_horizon_color = Color(0.72, 0.78, 0.86)  # von schräg oben sieht man fast nur den "Boden" des Himmels
	sky.ground_bottom_color = Color(0.62, 0.68, 0.76)
	env.sky.sky_material = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.tonemap_mode = Environment.TONE_MAPPER_AGX
	var we := WorldEnvironment.new()
	we.environment = env
	viewport.add_child(we)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -35, 0)
	sun.light_energy = 1.3
	viewport.add_child(sun)
	viewport.add_child(cam)
	marker.visible = false
	viewport.add_child(marker)  # bleibt über Streckenwechsel hinweg (content wird neu gebaut)

	profile.custom_minimum_size.y = 150
	profile.hovered.connect(_on_hover)
	box.add_child(profile)
	var legend := HBoxContainer.new()
	legend.add_theme_constant_override("separation", 14)
	legend.add_child(UI.label("Steigung", 14, UI.MUTED, 600))
	for i in Profile.BANDS.size():
		var sw := ColorRect.new()
		sw.color = Profile.BANDS[i][1]
		sw.custom_minimum_size = Vector2(14, 14)
		sw.size_flags_vertical = SIZE_SHRINK_CENTER
		var item := HBoxContainer.new()
		item.add_theme_constant_override("separation", 5)
		item.add_child(sw)
		item.add_child(UI.label(Profile.band_name(i), 14, UI.MUTED))
		legend.add_child(item)
	var hint := UI.label("Ziehen: drehen · Rechts ziehen: schieben · Rad: Zoom · Esc: schließen", 14, UI.MUTED)
	hint.size_flags_horizontal = SIZE_EXPAND_FILL
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	legend.add_child(hint)
	box.add_child(legend)


func _fit_viewport() -> void:
	## 3D-Bild in echter Bildschirmauflösung rendern (Stretch-Faktor 1.2–2.4), nicht hochskaliert
	if view.is_inside_tree():
		viewport.size = Vector2i((view.size * (get_viewport().get_final_transform() * view.get_global_transform_with_canvas()).get_scale().x).round().max(Vector2(2, 2)))


func show_route(dir: String, name: String) -> bool:
	## Streckenpaket (routes/<name>/ oder user://preview/) anzeigen. false, wenn es kein Gelände hat (alte Pakete).
	var meta = JSON.parse_string(FileAccess.get_file_as_string(dir + "meta.json"))
	var mesh := Main.load_array_mesh(dir + "terrain")
	pts = FileAccess.get_file_as_bytes(dir + "route.pos").to_vector3_array()
	if meta is not Dictionary or not meta.has("ground") or mesh == null or pts.size() < 2:
		return false
	if content:
		content.queue_free()
	content = Node3D.new()
	viewport.add_child(content)
	RenderingServer.global_shader_parameter_set("snow_line", 1e5)  # Vorschau ohne Jahreszeit; main.gd setzt sie beim Losfahren neu

	var mat := ShaderMaterial.new()
	mat.shader = load("res://shaders/terrain.gdshader")
	mat.set_shader_parameter("ground", Main.load_ground(dir))
	var g: Array = meta.ground
	mat.set_shader_parameter("rect", Vector4(g[0], g[1], g[2], g[3]))
	var terrain := MeshInstance3D.new()
	terrain.mesh = mesh
	terrain.material_override = mat
	content.add_child(terrain)

	bounds = AABB(pts[0], Vector3.ZERO)
	for p in pts:
		bounds = bounds.expand(p)
	var extent := maxf(bounds.size.x, bounds.size.z)
	var w := clampf(extent / 500.0, 4.0, 40.0)  # Bandbreite: aus jeder Zoomstufe sichtbar
	content.add_child(_ribbon(w))
	var loop := pts[0].distance_to(pts[-1]) < 60.0
	var h := clampf(extent / 25.0, 30.0, 800.0)
	content.add_child(_pole(pts[0], h, Color(0.1, 0.75, 0.25)))
	if not loop:
		content.add_child(_pole(pts[-1], h, Color(0.85, 0.1, 0.1)))
	var ball := SphereMesh.new()
	ball.radius = maxf(w * 1.6, extent / 150.0)
	ball.height = ball.radius * 2.0
	marker.mesh = ball
	marker.material_override = _flat(Color.WHITE)
	marker.visible = false

	cam.near = 1.0
	cam.far = maxf(extent * 6.0, 20000.0)
	title.text = name
	profile.setup(pts, meta)
	stats.text = profile.summary() + ("  ·  Rundkurs" if loop else "  ·  A nach B")
	_apply_scale()
	frame()
	visible = true
	return true


func frame() -> void:
	target = bounds.get_center() * Vector3(1, _k(), 1)
	var extent := maxf(bounds.size.x, bounds.size.z)
	dist = extent * 0.6 / tan(deg_to_rad(cam.fov) / 2.0) + 200.0
	pitch = 0.75
	_update_cam()


func _k() -> float:
	return 2.0 if exaggerate.button_pressed else 1.0


func _apply_scale() -> void:
	if content:
		content.scale = Vector3(1, _k(), 1)
	target.y = bounds.get_center().y * _k()
	_update_cam()


func _update_cam() -> void:
	cam.position = target + Vector3(sin(yaw) * cos(pitch), sin(pitch), cos(yaw) * cos(pitch)) * dist
	cam.look_at(target)


func _view_input(e: InputEvent) -> void:
	var extent := maxf(bounds.size.x, bounds.size.z)
	if e is InputEventMouseButton and e.pressed:
		if e.button_index == MOUSE_BUTTON_WHEEL_UP:
			dist = maxf(dist * 0.88, 60.0)
		elif e.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			dist = minf(dist * 1.13, extent * 4.0 + 2000.0)
		_update_cam()
	elif e is InputEventMouseMotion:
		if e.button_mask & MOUSE_BUTTON_MASK_LEFT:
			yaw -= e.relative.x * 0.006
			pitch = clampf(pitch + e.relative.y * 0.004, 0.08, 1.5)
			_update_cam()
		elif e.button_mask & (MOUSE_BUTTON_MASK_RIGHT | MOUSE_BUTTON_MASK_MIDDLE):
			var right := Vector3(cos(yaw), 0, -sin(yaw))
			var fwd := Vector3(-sin(yaw), 0, -cos(yaw))
			target += (-right * e.relative.x + fwd * e.relative.y) * dist * 0.0015
			_update_cam()


func _unhandled_key_input(e: InputEvent) -> void:
	if visible and e.is_action_pressed("ui_cancel"):
		hide()
		get_viewport().set_input_as_handled()


func _on_hover(i: int) -> void:
	marker.visible = i >= 0
	if i >= 0:
		marker.position = pts[i] * Vector3(1, _k(), 1) + Vector3.UP * marker.mesh.radius


func _ribbon(w: float) -> MeshInstance3D:
	# Strecke als Band knapp über dem Gelände (route.py hält das Gelände dort unter der Fahrbahn)
	var v := PackedVector3Array()
	var idx := PackedInt32Array()
	for i in pts.size():
		var a := pts[maxi(i - 1, 0)]
		var b := pts[mini(i + 1, pts.size() - 1)]
		var side := Vector3(b.z - a.z, 0, a.x - b.x).normalized() * w
		var lift := Vector3.UP * (w * 0.3 + 1.0)
		v.append(pts[i] - side + lift)
		v.append(pts[i] + side + lift)
		if i > 0:
			var q := 2 * i - 2
			idx.append_array([q, q + 2, q + 1, q + 1, q + 2, q + 3])
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = v
	arrays[Mesh.ARRAY_INDEX] = idx
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _flat(Color(1, 0.4, 0))
	return mi


func _pole(at: Vector3, h: float, color: Color) -> MeshInstance3D:
	var c := CylinderMesh.new()
	c.top_radius = h / 40.0
	c.bottom_radius = h / 40.0
	c.height = h
	var mi := MeshInstance3D.new()
	mi.mesh = c
	mi.position = at + Vector3.UP * h / 2.0
	mi.material_override = _flat(color)
	return mi


func _flat(color: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	m.albedo_color = color
	return m


class Profile extends Control:
	## Höhenprofil: Fläche nach Steigung eingefärbt (über 100 m), Maus zeigt km/Höhe/Steigung und den Punkt in 3D.
	signal hovered(i: int)  # Index in route.pos, -1 = keiner

	const LEFT := 56.0
	const BOTTOM := 20.0
	var h := PackedFloat32Array()  # echte Höhe ü. NN je Punkt (ohne Klimadaten: relativ)
	var grade := PackedFloat32Array()  # % über ±50 m
	var hmin := 0.0
	var hmax := 1.0
	var absolute := true
	var hover := -1

	func setup(pts: PackedVector3Array, meta: Dictionary) -> void:
		var climate: Dictionary = meta.get("climate", {})
		absolute = climate.has("elev0")
		var elev0: float = climate.get("elev0", 0.0)
		var n := pts.size()
		h.resize(n)
		grade.resize(n)
		for i in n:
			h[i] = pts[i].y + elev0
		for i in n:
			var a := maxi(i - 10, 0)
			var b := mini(i + 10, n - 1)
			grade[i] = (h[b] - h[a]) / maxf((b - a) * STEP, 1.0) * 100.0
		hmin = Array(h).min()
		hmax = maxf(Array(h).max(), hmin + 10.0)
		hover = -1
		queue_redraw()

	func summary() -> String:
		var gain := 0.0
		var steepest := 0.0
		for i in range(1, h.size()):
			gain += maxf(h[i] - h[i - 1], 0.0)
		for i in range(1, h.size() - 1):
			steepest = maxf(steepest, absf(h[i + 1] - h[i - 1]) / (2.0 * STEP) * 100.0)  # wie grade_at() im Spiel
		return "%.1f km  ·  %d hm bergauf  ·  max %.1f %%  ·  %d–%d m%s" % [
			(h.size() - 1) * STEP / 1000.0, gain, steepest, hmin, hmax, " ü. NN" if absolute else " (relativ)"]

	func _x(i: int) -> float:
		return LEFT + float(i) / maxf(h.size() - 1, 1) * (size.x - LEFT - 8.0)

	func _y(v: float) -> float:
		return 6.0 + (1.0 - (v - hmin) / (hmax - hmin)) * (size.y - BOTTOM - 12.0)

	# Steigungsbänder [bis %, Farbe] – Profil und Legende lesen dieselbe Tabelle
	const BANDS := [[-2.0, Color(0.35, 0.55, 0.85)], [2.0, Color(0.55, 0.75, 0.45)], [4.0, Color(0.85, 0.85, 0.3)],
		[6.0, Color(0.95, 0.65, 0.2)], [9.0, Color(0.9, 0.3, 0.15)], [INF, Color(0.55, 0.08, 0.1)]]

	static func band(g: float) -> Color:
		for b in BANDS:
			if g < b[0]:
				return b[1]
		return BANDS[-1][1]

	static func band_name(i: int) -> String:
		if i == 0:
			return "< %d %%" % BANDS[0][0]
		if i == BANDS.size() - 1:
			return "> %d %%" % BANDS[i - 1][0]
		return "%d–%d %%" % [BANDS[i - 1][0], BANDS[i][0]]

	func _draw() -> void:
		draw_style_box(UI.box(Color(0, 0, 0, 0.25), 10, 0), Rect2(Vector2.ZERO, size))
		if h.size() < 2:
			return
		var font := get_theme_default_font()
		var base := size.y - BOTTOM
		var segs := mini(h.size() - 1, int(size.x - LEFT))
		for s in segs:
			var i0 := int(float(s) / segs * (h.size() - 1))
			var i1 := int(float(s + 1) / segs * (h.size() - 1))
			var x0 := _x(i0)
			var x1 := _x(i1)
			draw_colored_polygon(PackedVector2Array([Vector2(x0, base), Vector2(x0, _y(h[i0])), Vector2(x1, _y(h[i1])), Vector2(x1, base)]),
				band(grade[(i0 + i1) / 2]))
		for v in [hmin, hmax]:
			draw_string(font, Vector2(4, _y(v) + 5), "%d m" % v, HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color(0.85, 0.85, 0.85))
		var km := (h.size() - 1) * STEP / 1000.0
		var tick := 1.0 if km <= 12 else 5.0 if km <= 60 else 10.0
		var t := 0.0
		while t <= km:
			var x := _x(int(t * 1000.0 / STEP))
			draw_line(Vector2(x, base), Vector2(x, base + 4), Color(0.7, 0.7, 0.7))
			draw_string(font, Vector2(x - 8, size.y - 3), "%d" % t, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color(0.8, 0.8, 0.8))
			t += tick
		if hover >= 0:
			var x := _x(hover)
			draw_line(Vector2(x, 0), Vector2(x, base), Color.WHITE, 1.5)
			var text := "km %.2f  ·  %d m  ·  %+.1f %%" % [hover * STEP / 1000.0, h[hover], grade[hover]]
			draw_string(font, Vector2(minf(x + 6, size.x - 200), 18), text, HORIZONTAL_ALIGNMENT_LEFT, -1, 14, Color.WHITE)

	func _gui_input(e: InputEvent) -> void:
		if e is InputEventMouseMotion and h.size() > 1:
			hover = clampi(roundi((e.position.x - LEFT) / (size.x - LEFT - 8.0) * (h.size() - 1)), 0, h.size() - 1)
			hovered.emit(hover)
			queue_redraw()

	func _notification(what: int) -> void:
		if what == NOTIFICATION_MOUSE_EXIT and hover >= 0:
			hover = -1
			hovered.emit(-1)
			queue_redraw()
