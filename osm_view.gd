extends Control
## OSM-Kartenansicht: Kacheln laden + in user://tiles cachen, Mercator-Umrechnung. Basis für Minimap und Streckenwahl.

const TILE_URL := "https://tile.openstreetmap.org/%s/%s/%s.png"
const HEADERS := ["User-Agent: cadence/0.1 (private cycling sim)"]

var zoom := 15
var center := Vector2(0.5, 0.5)  # Mercator 0..1

var tiles := {}  # "z_x_y" -> Texture2D, null = lädt/fehlt
var queue: Array[String] = []
var pool: Array[HTTPRequest] = []  # 4 parallele Downloads, wie ein Browser


func _ready() -> void:
	clip_contents = true
	DirAccess.make_dir_recursive_absolute("user://tiles")
	get_viewport().size_changed.connect(queue_redraw)  # Fenstergröße ändert den Stretch-Faktor und damit die Kachelgröße
	for i in 4:
		var h := HTTPRequest.new()
		h.set_meta("key", "")
		h.request_completed.connect(_on_tile.bind(h))
		add_child(h)
		pool.append(h)


static func merc(lon: float, lat: float) -> Vector2:
	var r := deg_to_rad(clamp(lat, -85.0, 85.0))
	return Vector2((lon + 180.0) / 360.0, (1.0 - log(tan(r) + 1.0 / cos(r)) / PI) / 2.0)


static func lonlat(m: Vector2) -> Vector2:
	return Vector2(m.x * 360.0 - 180.0, rad_to_deg(atan(sinh(PI * (1.0 - 2.0 * m.y)))))


func phys_xf() -> Transform2D:
	## lokal -> Fensterpixel. get_screen_transform() enthält den Stretch (canvas_items) in 4.7 nicht, get_final_transform() schon.
	return get_viewport().get_final_transform() * get_global_transform_with_canvas()


func tile_px() -> float:
	## Kachelgröße in Canvas-Einheiten: 1 Kachelpixel = 1 Bildschirmpixel, auch bei Stretch (canvas_items, 4K/5K)
	return 256.0 / phys_xf().get_scale().x


func world_px() -> float:
	return tile_px() * pow(2.0, zoom)


func to_screen(m: Vector2, wp := 0.0) -> Vector2:
	## wp: world_px() einmal je Zeichnen übergeben (phys_xf je Punkt kostet bei 4000 Punkten ~1 ms)
	return (m - center) * (wp if wp > 0.0 else world_px()) + size / 2.0


func from_screen(p: Vector2) -> Vector2:
	return (p - size / 2.0) / world_px() + center


func set_zoom(z: int) -> void:
	z = clampi(z, 3, 18)
	if z == zoom:
		return
	zoom = z
	for k in queue:
		tiles.erase(k)  # alte Zoomstufe nicht mehr laden
	queue.clear()
	queue_redraw()


func draw_tiles() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), Color(0.93, 0.92, 0.88))
	var n := int(pow(2, zoom))
	var c := center * world_px()
	var tp := tile_px()
	var xf := phys_xf()
	var inv := xf.affine_inverse()
	var t0 := ((c - size / 2.0) / tp).floor()
	var t1 := ((c + size / 2.0) / tp).floor()
	for ty in range(maxi(t0.y, 0), mini(t1.y, n - 1) + 1):
		for tx in range(t0.x, t1.x + 1):
			var tex = _tile(posmod(tx, n), ty)
			if tex:  # Ecke auf ganze Bildschirmpixel, sonst filtert die GPU die Kachel unscharf
				draw_texture_rect(tex, Rect2(inv * (xf * (Vector2(tx, ty) * tp - c + size / 2.0)).round(), Vector2(tp, tp)), false)
	var font := get_theme_default_font()
	var w := font.get_string_size("© OpenStreetMap", HORIZONTAL_ALIGNMENT_LEFT, -1, 12).x
	draw_rect(Rect2(size.x - w - 10, size.y - 20, w + 10, 20), Color(1, 1, 1, 0.7))
	draw_string(font, Vector2(size.x - w - 5, size.y - 6), "© OpenStreetMap", HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color(0.2, 0.2, 0.2))


func _tile(x: int, y: int) -> Variant:
	var key := "%d_%d_%d" % [zoom, x, y]
	if tiles.has(key):
		return tiles[key]
	tiles[key] = null
	var path := "user://tiles/%s.png" % key
	if FileAccess.file_exists(path):
		tiles[key] = ImageTexture.create_from_image(Image.load_from_file(path))
	else:
		queue.append(key)
		_next()
	return tiles[key]


func _next() -> void:
	for h in pool:
		if queue.is_empty():
			return
		if h.get_meta("key") == "":
			var key: String = queue.pop_back()  # zuletzt angefragt = gerade sichtbar
			h.set_meta("key", key)
			var p := key.split("_")
			h.request(TILE_URL % [p[0], p[1], p[2]], HEADERS)


func _on_tile(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray, h: HTTPRequest) -> void:
	var key: String = h.get_meta("key")
	var img := Image.new()
	if code == 200 and img.load_png_from_buffer(body) == OK:
		img.save_png("user://tiles/%s.png" % key)
		tiles[key] = ImageTexture.create_from_image(img)
		queue_redraw()
	# ponytail: fehlgeschlagene Kachel bleibt leer bis zum Neustart; Retry wenn offline-Start ein Thema wird
	h.set_meta("key", "")
	_next()
