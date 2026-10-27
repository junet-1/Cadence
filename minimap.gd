extends "res://osm_view.gd"
## Minimap: wo man in echt wäre. +/- zoomt.

const SIZE := 360.0

var meta := {}
var route := PackedVector2Array()  # Mercator 0..1, einmal berechnet
var ahead := Vector2()
var peer := Vector2()
var peer_on := false


func setup(m: Dictionary, pts: PackedVector3Array) -> void:
	meta = m
	visible = meta.has("lon0")  # alte Pakete ohne Geo-Referenz: keine Karte
	if not visible:
		return  # auch Workout-Welten: liegen nirgends auf der Erde
	for p in pts:
		route.append(_merc(p))


func _ready() -> void:
	super()
	anchor_left = 1
	anchor_top = 1
	anchor_right = 1
	anchor_bottom = 1
	offset_left = -SIZE - 16
	offset_top = -SIZE - 16
	offset_right = -16
	offset_bottom = -16
	mouse_filter = MOUSE_FILTER_IGNORE


func update(me3: Vector3, ahead3: Vector3, peer3: Vector3, peer_visible: bool) -> void:
	if not visible:
		return
	center = _merc(me3)
	ahead = _merc(ahead3)
	peer = _merc(peer3)
	peer_on = peer_visible
	queue_redraw()


func _merc(p: Vector3) -> Vector2:
	return merc(meta.lon0 + p.x / meta.kx, meta.lat0 - p.z / meta.ky)


func _unhandled_key_input(event: InputEvent) -> void:
	if event.is_pressed():
		match char(event.unicode):
			"+": set_zoom(zoom + 1)
			"-": set_zoom(zoom - 1)


func _draw() -> void:
	draw_tiles()
	var step := maxi(1, route.size() / 2000)
	var line := PackedVector2Array()
	for i in range(0, route.size(), step):
		line.append(to_screen(route[i]))
	draw_polyline(line, Color(1, 0.4, 0, 0.85), 4.0, true)

	if peer_on:
		draw_circle(to_screen(peer), 7, Color.WHITE)
		draw_circle(to_screen(peer), 5, Color(0.2, 0.5, 1))
	var c := size / 2.0
	var dir := (ahead - center).normalized()
	var side := Vector2(-dir.y, dir.x)
	draw_colored_polygon(PackedVector2Array([c + dir * 13, c - dir * 8 + side * 8, c - dir * 8 - side * 8]), Color.WHITE)
	draw_colored_polygon(PackedVector2Array([c + dir * 10, c - dir * 6 + side * 6, c - dir * 6 - side * 6]), Color(1, 0.4, 0.1))
	draw_string(ThemeDB.fallback_font, Vector2(size.x - 110, size.y - 6), "Zoom %d (+/-)" % zoom, HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color(0.2, 0.2, 0.2))
	draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.6), false, 2.0)
