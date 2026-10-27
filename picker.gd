extends "res://osm_view.gd"
## Streckenwahl wie Google Maps: Klick = Wegpunkt, Rechtsklick = letzten weg, Ziehen = schieben, Mausrad/+/- = Zoom.
## 3D-Vorschau (preview.gd): gespeicherte Strecken direkt, neue über `route.py --preview` (Sekunden, ohne OSM).

const BROUTER := "https://brouter.de/brouter?lonlats=%s&profile=fastbike&alternativeidx=0&format=geojson"
const SEARCH := "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=%s"

var points: Array[Vector2] = []  # Mercator
var line := PackedVector2Array()
var routing := HTTPRequest.new()
var search_http := HTTPRequest.new()
var drag_from = null
var dragged := false

const PREVIEW_DIR := "user://preview/"

var job_pid := -1  # laufendes route.py (Bau oder Vorschau)
var job_io: FileAccess
var job_err: FileAccess
var job_log := ""
var job_label := ""
var job_done: Callable
var build_name := ""

var info: Label
var loop_box: CheckBox
var name_edit: LineEdit
var go: Button
var preview_btn: Button
var preview: PanelContainer = preload("res://preview.gd").new()


func _ready() -> void:
	super()
	set_anchors_preset(PRESET_FULL_RECT)
	zoom = 5
	center = merc(10.0, 51.0)
	add_child(routing)
	routing.request_completed.connect(_on_route)
	add_child(search_http)
	search_http.request_completed.connect(_on_search)
	_build_ui()
	preview.set_anchors_preset(PRESET_FULL_RECT)
	preview.offset_left = 412
	preview.offset_top = 16
	preview.offset_right = -16
	preview.offset_bottom = -16
	add_child(preview)


func _build_ui() -> void:
	var panel := PanelContainer.new()
	panel.position = Vector2(16, 16)
	panel.custom_minimum_size.x = 380
	add_child(panel)
	var box := VBoxContainer.new()
	panel.add_child(box)

	var back := Button.new()
	back.text = "← Workout-Welt"
	back.pressed.connect(func() -> void: get_tree().change_scene_to_file("res://start.tscn"))
	box.add_child(back)
	var search := LineEdit.new()
	search.placeholder_text = "Ort suchen … (Enter)"
	search.text_submitted.connect(func(t: String) -> void:
		search_http.cancel_request()
		search_http.request(SEARCH % t.uri_encode(), HEADERS))
	box.add_child(search)
	var help := Label.new()
	help.text = "Klick: Wegpunkt · Rechtsklick: letzten löschen\nZiehen: verschieben · Mausrad / + / −: Zoom"
	box.add_child(help)
	loop_box = CheckBox.new()
	loop_box.text = "Rundkurs (zurück zum Start)"
	loop_box.button_pressed = true
	loop_box.toggled.connect(func(_on: bool) -> void: _reroute())
	box.add_child(loop_box)
	info = Label.new()
	info.text = "Setz mindestens 2 Punkte"
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(info)
	name_edit = LineEdit.new()
	name_edit.placeholder_text = "Name der Strecke"
	box.add_child(name_edit)
	go = Button.new()
	go.text = "Strecke bauen und losfahren"
	go.disabled = true
	go.pressed.connect(_build)
	box.add_child(go)
	preview_btn = Button.new()
	preview_btn.text = "3D-Vorschau"
	preview_btn.disabled = true
	preview_btn.pressed.connect(_preview_new)
	box.add_child(preview_btn)

	box.add_child(HSeparator.new())
	var saved := Label.new()
	saved.text = "Gespeicherte Strecken"
	box.add_child(saved)
	for d in DirAccess.get_directories_at("res://routes"):
		if FileAccess.file_exists("res://routes/%s/route.pos" % d):
			var row := HBoxContainer.new()
			var b := Button.new()
			b.text = d
			b.size_flags_horizontal = SIZE_EXPAND_FILL
			b.pressed.connect(_start.bind(d))
			row.add_child(b)
			if FileAccess.file_exists("res://routes/%s/terrain.idx" % d):  # alte Pakete ohne Gelände: keine Vorschau
				var p3 := Button.new()
				p3.text = "3D"
				p3.pressed.connect(_preview_saved.bind(d))
				row.add_child(p3)
			box.add_child(row)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_zoom_at(1, event.position)
		elif event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_zoom_at(-1, event.position)
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				drag_from = event.position
				dragged = false
			else:
				if not dragged and job_pid == -1:
					points.append(from_screen(event.position))
					_reroute()
				drag_from = null
		elif event.pressed and event.button_index == MOUSE_BUTTON_RIGHT and points and job_pid == -1:
			points.pop_back()
			_reroute()
	elif event is InputEventMouseMotion and drag_from != null:
		if dragged or event.position.distance_to(drag_from) > 5:
			dragged = true
			center -= event.relative / world_px()
			queue_redraw()


func _unhandled_key_input(event: InputEvent) -> void:
	if event.is_pressed():
		match char(event.unicode):
			"+": _zoom_at(1, size / 2.0)
			"-": _zoom_at(-1, size / 2.0)


func _zoom_at(d: int, pos: Vector2) -> void:
	var m := from_screen(pos)
	set_zoom(zoom + d)
	center = m - (pos - size / 2.0) / world_px()  # Punkt unter der Maus bleibt stehen
	queue_redraw()


func _via() -> String:
	var pts := points.duplicate()
	if loop_box.button_pressed:
		pts.append(points[0])
	var parts := PackedStringArray()
	for m in pts:
		var ll := lonlat(m)
		parts.append("%.5f,%.5f" % [ll.x, ll.y])
	return "|".join(parts)


func _reroute() -> void:
	line.clear()
	go.disabled = true
	preview_btn.disabled = true
	if preview.get_meta("new", false):
		preview.hide()  # Vorschau der alten Wegpunkte passt nicht mehr
	routing.cancel_request()
	queue_redraw()
	if points.size() < 2:
		info.text = "Setz mindestens 2 Punkte"
		return
	info.text = "Route wird berechnet …"
	routing.request(BROUTER % _via(), HEADERS)


func _on_route(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var text := body.get_string_from_utf8()
	var data = JSON.parse_string(text)
	if code != 200 or data is not Dictionary:
		info.text = "Keine Route: " + text.left(160)
		return
	var f = data.features[0]
	line.clear()
	for c in f.geometry.coordinates:
		line.append(merc(c[0], c[1]))
	info.text = "%.1f km · %s Höhenmeter" % [float(f.properties["track-length"]) / 1000.0, f.properties["filtered ascend"]]
	go.disabled = false
	preview_btn.disabled = false
	queue_redraw()


func _on_search(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var r = JSON.parse_string(body.get_string_from_utf8())
	if code != 200 or r is not Array or r.is_empty():
		info.text = "Ort nicht gefunden"
		return
	center = merc(float(r[0].lon), float(r[0].lat))
	set_zoom(13)
	if name_edit.text == "":
		name_edit.text = str(r[0].name)
	queue_redraw()


func _build() -> void:
	var re := RegEx.create_from_string("[^a-z0-9_-]+")
	var n := name_edit.text.to_lower().strip_edges().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
	build_name = re.sub(n, "-", true).trim_prefix("-").trim_suffix("-")
	if build_name == "":
		build_name = "strecke-" + Time.get_datetime_string_from_system().replace(":", "").replace("T", "-")
	preview.hide()
	_run([build_name, "--via", _via()], "Baue „%s“ (1–3 min)" % build_name, _start.bind(build_name))


func _preview_new() -> void:
	_run(["--via", _via(), "--preview", ProjectSettings.globalize_path(PREVIEW_DIR)], "3D-Vorschau wird berechnet", func() -> void:
		info.text = "Vorschau fertig"
		_show_preview(PREVIEW_DIR, name_edit.text if name_edit.text else "Neue Strecke", true))


func _preview_saved(route: String) -> void:
	_show_preview("res://routes/%s/" % route, route, false, route)


func _show_preview(dir: String, title: String, is_new: bool, route := "") -> void:
	for c in preview.ride_requested.get_connections():
		preview.ride_requested.disconnect(c.callable)
	preview.ride_requested.connect(_build if is_new else _start.bind(route))
	preview.set_meta("new", is_new)
	if not preview.show_route(dir, title):
		info.text = "Keine Vorschau: Paket ohne Gelände (mit route.py neu bauen)"


func _run(args: Array, label: String, done: Callable) -> void:
	## route.py im Hintergrund, Fortschritt im Infotext, done() bei Erfolg. Ein Auftrag zur Zeit.
	if job_pid != -1:
		return
	var r := OS.execute_with_pipe(preload("res://main.gd").uv(), ["run", ProjectSettings.globalize_path("res://route.py")] + args, false)
	if r.is_empty():
		info.text = "uv nicht gefunden"
		return
	job_pid = r.pid
	job_io = r.stdio
	job_err = r.stderr
	job_log = ""
	job_label = label
	job_done = done
	go.disabled = true
	preview_btn.disabled = true


func _process(_delta: float) -> void:
	if job_pid == -1:
		return
	job_log += job_io.get_as_text() + job_err.get_as_text()
	var lines := job_log.split("\n", false)
	info.text = "%s …\n%s" % [job_label, lines[-1] if lines else ""]
	if OS.is_process_running(job_pid):
		return
	var ok := OS.get_process_exit_code(job_pid) == 0
	job_pid = -1
	go.disabled = line.size() < 2
	preview_btn.disabled = line.size() < 2
	if ok:
		job_done.call()
	else:
		info.text = "Fehler:\n" + job_log.right(400)


func _exit_tree() -> void:
	if job_pid != -1:
		OS.kill(job_pid)  # Streckenwahl verlassen = halbfertigen Bau abbrechen


func _start(route: String) -> void:
	Engine.set_meta("route", route)
	if Engine.has_meta("world"):
		Engine.remove_meta("world")  # echte Strecke statt Workout-Welt
	get_tree().change_scene_to_file("res://main.tscn")


func _draw() -> void:
	draw_tiles()
	if line.size() > 1:
		var pts := PackedVector2Array()
		for m in line:
			pts.append(to_screen(m))
		draw_polyline(pts, Color(1, 0.4, 0, 0.9), 5.0, true)
	for i in points.size():
		var p := to_screen(points[i])
		draw_circle(p, 12, Color.WHITE)
		draw_circle(p, 10, Color(0.1, 0.6, 0.2) if i == 0 else Color(0.2, 0.4, 0.9))
		draw_string(ThemeDB.fallback_font, p + Vector2(-4 if i < 9 else -8, 5), str(i + 1), HORIZONTAL_ALIGNMENT_LEFT, -1, 14, Color.WHITE)
