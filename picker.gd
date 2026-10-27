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
const UI := preload("res://ui_theme.gd")
const Preview := preload("res://preview.gd")
const M := preload("res://main.gd")
const Options := preload("res://options.gd")
const PANEL_W := 380.0

var job_pid := -1  # laufendes route.py (Bau oder Vorschau)
var job_io: FileAccess
var job_err: FileAccess
var job_log := ""
var job_label := ""
var job_done: Callable
var job_t0 := 0
var build_name := ""

var info: Label
var progress: ProgressBar
var log_line: Label
var cancel: Button
var loop_box: CheckBox
var name_edit: LineEdit
var go: Button
var preview_btn: Button
var preview: PanelContainer = Preview.new()


func _ready() -> void:
	super()
	set_anchors_preset(PRESET_FULL_RECT)
	theme = UI.theme()
	zoom = 5
	center = merc(10.0, 51.0)
	add_child(routing)
	routing.request_completed.connect(_on_route)
	add_child(search_http)
	search_http.request_completed.connect(_on_search)
	_build_ui()
	preview.set_anchors_preset(PRESET_FULL_RECT)
	preview.offset_left = PANEL_W + 32
	preview.offset_top = 16
	preview.offset_right = -16
	preview.offset_bottom = -16
	add_child(preview)


func _build_ui() -> void:
	var panel := PanelContainer.new()  # volle Fensterhöhe, Liste der gespeicherten Strecken scrollt im Rest
	panel.set_anchors_preset(PRESET_LEFT_WIDE)
	panel.offset_left = 16
	panel.offset_top = 16
	panel.offset_right = 16 + PANEL_W
	panel.offset_bottom = -16
	add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 10)
	panel.add_child(box)

	var back := Button.new()
	back.text = "← Zurück"
	back.flat = true
	back.alignment = HORIZONTAL_ALIGNMENT_LEFT
	back.size_flags_horizontal = SIZE_SHRINK_BEGIN
	back.add_theme_color_override("font_color", UI.MUTED)
	back.tooltip_text = "Zurück zum Startbildschirm (Esc)"
	back.pressed.connect(_back)
	box.add_child(back)
	box.add_child(UI.label("Echte Strecke", 30, UI.TEXT, 700))
	var search := LineEdit.new()
	search.placeholder_text = "Ort suchen … (Enter)"
	search.clear_button_enabled = true
	search.text_submitted.connect(func(t: String) -> void:
		search_http.cancel_request()
		search_http.request(SEARCH % t.uri_encode(), HEADERS))
	box.add_child(search)
	var help := UI.label("Klick: Wegpunkt · Rechtsklick: letzten löschen · Ziehen: verschieben · Mausrad / + / −: Zoom", 15, UI.MUTED)
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(help)
	loop_box = CheckBox.new()
	loop_box.text = "Rundkurs (zurück zum Start)"
	loop_box.button_pressed = true
	loop_box.toggled.connect(func(_on: bool) -> void: _reroute())
	box.add_child(loop_box)
	var ride := GridContainer.new()  # Wetter + Tageszeit der Fahrt
	ride.columns = 2
	ride.add_theme_constant_override("h_separation", 12)
	Options.ride_pickers(ride)
	box.add_child(ride)

	info = UI.label("Setz mindestens 2 Punkte", 18)
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(info)
	progress = ProgressBar.new()
	progress.indeterminate = true
	progress.show_percentage = false
	progress.custom_minimum_size.y = 6
	progress.visible = false
	box.add_child(progress)
	log_line = UI.label("", 14, UI.MUTED)
	log_line.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	log_line.visible = false
	box.add_child(log_line)

	name_edit = LineEdit.new()
	name_edit.placeholder_text = "Name der Strecke"
	name_edit.text_submitted.connect(func(_t: String) -> void:
		if not go.disabled:
			_build())
	box.add_child(name_edit)
	go = UI.primary(Button.new(), 19)
	go.text = "Strecke bauen und losfahren"
	go.custom_minimum_size.y = 52
	go.disabled = true
	go.pressed.connect(_build)
	box.add_child(go)
	preview_btn = Button.new()
	preview_btn.text = "3D-Vorschau"
	preview_btn.disabled = true
	preview_btn.pressed.connect(_preview_new)
	box.add_child(preview_btn)
	cancel = Button.new()
	cancel.text = "Abbrechen"
	cancel.visible = false
	cancel.pressed.connect(func() -> void:
		if job_pid != -1:
			_kill(job_pid))  # _process meldet den Abbruch
	box.add_child(cancel)

	var gap := Control.new()
	gap.custom_minimum_size.y = 6
	box.add_child(gap)
	box.add_child(UI.label("GESPEICHERTE STRECKEN", 14, UI.MUTED, 600))
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(scroll)
	var rows := VBoxContainer.new()
	rows.size_flags_horizontal = SIZE_EXPAND_FILL
	rows.add_theme_constant_override("separation", 6)
	scroll.add_child(rows)
	for d in DirAccess.get_directories_at(M.data("res://routes")):
		if not FileAccess.file_exists(M.data("res://routes/%s/route.pos" % d)):
			continue
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 6)
		var b := _compact(Button.new())
		b.text = d
		b.alignment = HORIZONTAL_ALIGNMENT_LEFT
		b.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		b.clip_text = true
		b.size_flags_horizontal = SIZE_EXPAND_FILL
		b.tooltip_text = "„%s“ fahren" % d
		b.pressed.connect(_start.bind(d))
		row.add_child(b)
		var meta = JSON.parse_string(FileAccess.get_file_as_string(M.data("res://routes/%s/meta.json" % d)))  # < 1 KB
		var km := UI.label("%.1f km" % (meta.length_m / 1000.0) if meta is Dictionary and meta.has("length_m") else "", 15, UI.MUTED)
		km.custom_minimum_size.x = 64
		km.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		row.add_child(km)
		var p3 := _compact(Button.new())
		p3.text = "3D"
		p3.custom_minimum_size.x = 56
		p3.tooltip_text = "3D-Vorschau"
		p3.disabled = not FileAccess.file_exists(M.data("res://routes/%s/terrain.idx" % d))  # alte Pakete ohne Gelände: keine Vorschau
		p3.pressed.connect(_preview_saved.bind(d))
		row.add_child(p3)
		rows.add_child(row)
	if rows.get_child_count() == 0:
		rows.add_child(UI.label("Noch keine – setz Punkte auf der Karte.", 15, UI.MUTED))


func _compact(b: Button) -> Button:
	## Listenzeile: kleiner als die Hauptknöpfe, damit viele Strecken auf einen Blick passen
	b.add_theme_font_size_override("font_size", 17)
	for st in ["normal", "hover", "pressed", "focus", "disabled"]:
		var sb: StyleBox = UI.theme().get_stylebox(st, "Button").duplicate()
		sb.content_margin_top = 5
		sb.content_margin_bottom = 5
		sb.content_margin_left = 12
		sb.content_margin_right = 12
		b.add_theme_stylebox_override(st, sb)
	return b


func _status(text: String, color := UI.TEXT) -> void:
	info.text = text
	info.add_theme_color_override("font_color", color)


func _back() -> void:
	get_tree().change_scene_to_file("res://start.tscn")


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
	if event.is_action_pressed("ui_cancel") and not preview.visible and job_pid == -1:  # laufenden Bau nicht per Esc verwerfen
		_back()
	elif event.is_pressed():
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
		_status("Setz mindestens 2 Punkte" if points.is_empty() else "Noch einen Punkt setzen")
		return
	_status("Route wird berechnet …", UI.MUTED)
	routing.request(BROUTER % _via(), HEADERS)


func _on_route(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS:
		_status("Routing-Server (brouter.de) nicht erreichbar – Netzwerk prüfen", UI.BAD)
		return
	var text := body.get_string_from_utf8()
	var data = JSON.parse_string(text)
	if code != 200 or data is not Dictionary:
		_status("Keine Route zwischen diesen Punkten (Punkt näher an eine Straße setzen?)\n" + text.strip_edges().left(160), UI.WARN)
		return
	var f = data.features[0]
	line.clear()
	for c in f.geometry.coordinates:
		line.append(merc(c[0], c[1]))
	_status("%.1f km · %s Höhenmeter" % [float(f.properties["track-length"]) / 1000.0, f.properties["filtered ascend"]])
	go.disabled = false
	preview_btn.disabled = false
	queue_redraw()


func _on_search(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS:
		_status("Ortssuche nicht erreichbar – Netzwerk prüfen", UI.BAD)
		return
	var r = JSON.parse_string(body.get_string_from_utf8())
	if code != 200 or r is not Array or r.is_empty():
		_status("Ort nicht gefunden", UI.WARN)
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
		_status("Vorschau fertig", UI.GOOD)
		_show_preview(PREVIEW_DIR, name_edit.text if name_edit.text else "Neue Strecke", true))


func _preview_saved(route: String) -> void:
	_show_preview(M.data("res://routes/%s/" % route), route, false, route)


func _show_preview(dir: String, title: String, is_new: bool, route := "") -> void:
	for c in preview.ride_requested.get_connections():
		preview.ride_requested.disconnect(c.callable)
	preview.ride_requested.connect(_build if is_new else _start.bind(route))
	preview.set_meta("new", is_new)
	if not preview.show_route(dir, title):
		_status("Keine Vorschau: Paket ohne Gelände (mit route.py neu bauen)", UI.WARN)


func _run(args: Array, label: String, done: Callable) -> void:
	## route.py im Hintergrund (Pipe, blockiert nicht), Fortschritt im Infotext, done() bei Erfolg. Ein Auftrag zur Zeit.
	if job_pid != -1:
		return
	var r := OS.execute_with_pipe(M.uv(), ["run", ProjectSettings.globalize_path(M.data("res://route.py"))] + args, false)
	if r.is_empty():
		_status("uv nicht gefunden – Installation prüfen (~/.local/bin/uv)", UI.BAD)
		return
	job_pid = r.pid
	job_io = r.stdio
	job_err = r.stderr
	job_log = ""
	job_label = label
	job_done = done
	job_t0 = Time.get_ticks_msec()
	_status(label + " …")
	log_line.text = ""
	_busy(true)


func _busy(on: bool) -> void:
	progress.visible = on
	log_line.visible = on or log_line.text != ""
	cancel.visible = on
	go.disabled = on or line.size() < 2
	preview_btn.disabled = on or line.size() < 2


func _process(_delta: float) -> void:
	if job_pid == -1:
		return
	job_log += job_io.get_as_text() + job_err.get_as_text()
	var lines := job_log.split("\n", false)
	var secs := (Time.get_ticks_msec() - job_t0) / 1000
	info.text = "%s … %d:%02d" % [job_label, secs / 60, secs % 60]
	log_line.text = lines[-1].strip_edges().left(200) if lines else "startet …"
	if OS.is_process_running(job_pid):
		return
	var code := OS.get_process_exit_code(job_pid)
	job_pid = -1
	if code == 0:
		log_line.text = ""
		_busy(false)
		job_done.call()
		return
	# Fehler: was, die letzten Logzeilen, und ein Hinweis, wenn es nach Netzwerk aussieht
	var tail := "\n".join(lines.slice(-4))
	var net := RegEx.create_from_string("(?i)urlopen|urlerror|timed? ?out|connection|nicht erreichbar|name or service|resolution|http error|ssl|network")
	var what := job_label.get_slice(" (", 0)
	var head := "%s fehlgeschlagen (Exit-Code %d)" % [what, code]
	if code < 0 or (code != 1 and lines.is_empty()):
		head = "%s abgebrochen" % what
	if net.search(tail):
		head += "\nNetzwerk prüfen (BRouter, Overpass, Höhendaten), dann erneut versuchen"
	_status(head, UI.BAD)
	log_line.text = tail if tail else "keine Ausgabe von route.py"
	_busy(false)


func _exit_tree() -> void:
	if job_pid != -1:
		_kill(job_pid)  # Streckenwahl verlassen = halbfertigen Bau abbrechen


static func _kill(pid: int) -> void:
	## SIGTERM an uv: uv reicht es an route.py weiter. OS.kill (SIGKILL) beendet nur uv, route.py schrieb weiter ins Paket.
	OS.execute("kill", ["-TERM", str(pid)])


func _start(route: String) -> void:
	Engine.set_meta("route", route)
	if Engine.has_meta("world"):
		Engine.remove_meta("world")  # echte Strecke statt Workout-Welt
	get_tree().change_scene_to_file("res://main.tscn")


func _draw() -> void:
	draw_tiles()
	if line.size() > 1:
		var pts := PackedVector2Array()
		var wp := world_px()
		for m in line:
			pts.append(to_screen(m, wp))
		draw_polyline(pts, Color(0, 0, 0, 0.35), 9.0, true)  # dunkler Rand: Linie auch auf orangen Straßen der Karte lesbar
		draw_polyline(pts, UI.ACCENT, 5.0, true)
	var font := get_theme_default_font()
	for i in points.size():
		var p := to_screen(points[i])
		draw_circle(p, 13, Color.WHITE)
		draw_circle(p, 11, UI.GOOD if i == 0 else UI.ACCENT)
		draw_string(font, p + Vector2(-13, 5), str(i + 1), HORIZONTAL_ALIGNMENT_CENTER, 26, 14, Color.BLACK)
