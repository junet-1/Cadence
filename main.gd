extends Node3D
## Cadence: Rad-Sim. Watt vom Kickr -> Tempo auf der Strecke, Steigung -> zurück an den Kickr.

const GAME_PORT := 47800
const BRIDGE_PORT := 47801
const G := 9.81
const CRR := 0.004
const CDA := 0.32  # m², Oberlenker
const RHO := 1.2
const SENSOR_TIMEOUT := 3.0  # s ohne Messwerte vom Trainer -> Leistung/Trittfrequenz 0 (sonst fährt der letzte Wert ewig weiter)
const Options := preload("res://options.gd")
const ZONE_NAMES := {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
	"recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei"}

var cfg := config()
var bridge := PacketPeerUDP.new()
var trainer_owner := false  # nur die Instanz, die GAME_PORT besitzt (und kein automatischer Test), steuert den Trainer
var net := PacketPeerUDP.new()
var curve := Curve3D.new()
var route_len := 0.0
var is_loop := true

var power := 0.0
var cadence := 0.0
var hr := 0.0
var speed := 0.0  # m/s
var dist := 0.0
var gear := 0
var elapsed := 0.0
var status := ""
var send_timer := 0.0
var data_t := -100.0  # Zeitpunkt der letzten Messwerte vom Trainer (s seit Start)
var sensor_lost := true  # noch keine/keine frischen Messwerte; einmalig genullt, damit Werte per Skript gesetzt werden können

var peer_name := ""
var peer_dist := 0.0
var peer_seen := -100.0

var peer_cadence := 0.0
var peer_speed := 0.0  # m/s, aus aufeinanderfolgenden Paketen geschätzt (nur für die Räder des Kollegen)

var rider: Node3D
var ghost: Node3D
var ui: Control  # hud.gd: Kacheln, Workout, Strecke, Pausemenü
var ui_layer: CanvasLayer
var ride_log := preload("res://ride_log.gd").new()  # 1 Probe/s -> Zusammenfassung + TCX
var recap: Control
var recap_shown := false  # Zusammenfassung am Workout-Ende schon gezeigt
var recap_auto := false  # offene Auswertung kam automatisch am Workout-Ende: Fahrt und Aufzeichnung laufen weiter
var dev := preload("res://dev.gd").new()  # F1: Freiflug-Kamera (nur Ansicht)
var env: Environment
var sun: DirectionalLight3D
var moon: DirectionalLight3D
var headlight: SpotLight3D
var stars: Texture2D
var geo := Vector2(10.0, 50.0)  # lon, lat der Strecke -> Sonnenstand
var climate := {}  # aus meta.json (route.py/biome.py) -> Schneegrenze, Herbst, Dunst
var leaves: GPUParticles3D
var heat_quad: MeshInstance3D
var weather := preload("res://weather.gd").new()  # Wolken, Regen, Wind (Optik + Luftwiderstand, nie Trainer)
var fog_base := 0.00025  # Nebeldichte des Bioms, weather.gd verdichtet sie bei Wolken/Regen
var fog_color := Color(0.52, 0.55, 0.61)
var snow_y := 1e5  # aktuelle Schneegrenze (Welt-y): Niederschlag darüber als Schnee
var warmth := 0.0  # 0 kühl … 1 heiß-trocken: Hitzeflimmern, Wasserfarbe
var leaf_pts := {}  # Kachel/Chunk -> [Mitte (Vector2), Kronenpunkte (PackedVector3Array)] von Laubbäumen: dort fallen Blätter
var leaf_t := 0.0
var leaf_tex: Texture2D
var tree_pts := {}  # Kachel/Chunk -> Fußpunkte (x, z) aller Bäume: Waldgeräusche (sound.gd)
var sound := preload("res://sound.gd").new()  # Wind, Reifen, Freilauf, Regen, Vögel (nur Kulisse)
const LEAF_CROWN := {"buche": 12.0, "eiche": 9.0, "birke": 10.0}  # Kronenmitte über dem Fuß (m, × Baumskalierung)
# Baum- und Buscharten (blender/trees.py -> models/trees.glb); Wiegen im Wind je Art (sonst 1)
const TREES := ["buche", "eiche", "birke", "fichte", "kiefer", "olive", "pinie", "aleppo", "steineiche", "zypresse", "palme", "busch",
	"farn", "hochgras", "staude"]
const SWAY := {"palme": 1.5, "busch": 0.0, "zypresse": 0.5, "farn": 3.0, "hochgras": 6.0, "staude": 5.0}
const UNDER := {"farn": 160.0, "hochgras": 160.0, "staude": 200.0}  # Unterwuchs (world._plants): sichtbar bis, Detail bis 120 m
const TREE_DETAIL := 600.0  # m: bis hier das Detailmodell (Blattkarten), dahinter die Fernversion
var autumn := 1.0
var minimap := preload("res://minimap.gd").new()
var grass: Array[MultiMeshInstance3D] = []

# Workout-Welt (world.py über world_stream.gd) und Workout-Ausführung
var world: Node3D  # null = Streckenpaket aus routes/
var world_meta := {}
var atmo := {}  # Biom-Atmosphäre alle meta.atmo.step Meter
var workout := {}  # normalisiert (workout.py), leer = freie Fahrt
var wo_time := 0.0  # Workout-Uhr: läuft, solange getreten wird – unabhängig vom Tempo in der Welt
var erg := false  # ERG: Trainer hält die Zielleistung, Steigung nur Optik/Physik; sonst SIM (Steigung an den Trainer)
var erg_bias := 0  # ±5 % je Schaltdruck im ERG
var ftp := 250.0
var diag := false
var region_label := ""
var region_t := 0.0


func _ready() -> void:
	# Zweite Instanz (Port belegt) oder MCP-Test: nie an die Bridge senden, sonst steuern zwei Spiele denselben Trainer
	trainer_owner = bridge.bind(GAME_PORT, "127.0.0.1") == OK and OS.get_environment("MCP_BACKGROUND") != "1"
	bridge.set_dest_address("127.0.0.1", BRIDGE_PORT)
	var port: int = cfg.get_value("net", "port", 47810)
	net.bind(port)
	var peer: String = cfg.get_value("net", "peer_host", "")
	if peer != "":
		net.set_dest_address(peer, port)
	if trainer_owner:
		_start_bridge()
	else:
		status = "Trainer wird von einer anderen Cadence-Instanz gesteuert – hier nur Zuschauen"
	ftp = cfg.get_value("rider", "ftp", 250.0)
	cfg.set_value("world", "grass", Options.load_cfg().get_value("video", "grass", cfg.get_value("world", "grass", 220)))  # Einstellungen
	if Engine.has_meta("world"):
		_start_world(Engine.get_meta("world"))
	else:
		_build_world()


func _start_bridge() -> void:
	var args := ["run", ProjectSettings.globalize_path(data("res://bridge.py"))]
	if "--fake" in OS.get_cmdline_user_args() or OS.get_environment("MCP_BACKGROUND") == "1":  # automatische Tests nie an den echten Trainer
		args.append("--fake")
	if OS.create_process(uv(), args) == -1:
		status = "uv nicht gefunden – starte bridge.py manuell"


func _build_world() -> void:
	var dir := data("res://routes/%s/" % Engine.get_meta("route", cfg.get_value("route", "name", "taunus")))
	var pts := FileAccess.get_file_as_bytes(dir + "route.pos").to_vector3_array()
	if pts.is_empty():
		status = "Strecke fehlt: %s – erst route.py laufen lassen" % dir
		pts = PackedVector3Array([Vector3.ZERO, Vector3(0, 0, -1000)])
	var meta = JSON.parse_string(FileAccess.get_file_as_string(dir + "meta.json"))
	if meta is not Dictionary:
		meta = {}
	_setup_route(meta, pts)
	if climate.has("elev0"):  # echte Höhe ü. M. für HUD und TCX (alte Pakete ohne Klima: nur relative Höhe)
		world_meta = {"elev0": climate.elev0}

	var terrain := _shader("terrain")
	var ground_tex := load_ground(dir)
	if ground_tex:
		terrain.set_shader_parameter("ground", ground_tex)
	var g: Array = meta.get("ground", [0, 0, 1, 1])
	terrain.set_shader_parameter("rect", Vector4(g[0], g[1], g[2], g[3]))
	_load_mesh(dir + "terrain", terrain)
	var road := _shader("road")
	_load_mesh(dir + "road", road, false)  # Fahrbahnen werfen keinen Schatten (Streifen an der Kante bei tiefer Sonne)
	_load_mesh(dir + "streets", road, false)
	_load_mesh(dir + "structures", road)  # Brücken (Deck, Brüstung, Pfeiler) und Tunnelgalerien aus road.py, Alpha 0 = ohne Markierung
	var paint := StandardMaterial3D.new()  # Querstriche (signs.py): Haltlinien, Wartelinien, Zebrastreifen
	paint.vertex_color_use_as_albedo = true
	paint.roughness = 0.55
	_load_mesh(dir + "marks", paint)
	_signs(dir)
	_load_mesh(dir + "walls", _shader("building"))
	var roofs := _shader("building")
	roofs.set_shader_parameter("roof", true)
	_load_mesh(dir + "roofs", roofs)
	_houses(dir)

	# Props: Alpha 0 = normal, Alpha 1 = leuchtet nachts
	var pm := prop_meshes()
	_route_trees(dir, meta, pm)
	_instances(dir + "lamps.bin", pm.lamps, 0.0)
	_setup_scene()
	_vegetation(dir, meta, ground_tex)


func _setup_route(meta: Dictionary, pts: PackedVector3Array) -> void:
	for p in pts:
		curve.add_point(p)
	is_loop = meta.get("loop", pts[0].distance_to(pts[-1]) < 60.0)  # route.py entscheidet, alte Pakete: Abstand Start/Ziel
	if is_loop:
		curve.add_point(pts[0])  # Runde schließen
	curve.bake_interval = 2.0
	route_len = curve.get_baked_length()
	minimap.setup(meta, pts)
	var sun_at: Array = meta.get("sun", [meta.get("lon0", 10.0), meta.get("lat0", 50.0)])  # Workout-Welt: Sonne wie im Startbiom
	geo = Vector2(sun_at[0], sun_at[1])
	climate = meta.get("climate", {})
	snow_y = snow_line()
	RenderingServer.global_shader_parameter_set("snow_line", snow_y)
	autumn = _autumn()
	weather.seed = int(meta.get("seed", hash(str(meta.get("name", "")))))  # gleicher Seed + gleiche Uhrzeit = gleiches Wetter
	_set_clock(Engine.get_meta("ride", {}).get("time", -1.0))


func _start_world(opts: Dictionary) -> void:
	## Workout-Welt: world.py erzeugt sie im Hintergrund (world_stream.gd); bis der Kopf da ist, nur Ladeanzeige
	world = preload("res://world_stream.gd").new()
	world.main = self
	add_child(world)
	world.meta_ready.connect(_on_world_meta)
	world.start(opts)
	erg = opts.get("mode", "erg") == "erg"
	ftp = opts.get("ftp", ftp)
	_make_ui()


func _on_world_meta(meta: Dictionary, pts: PackedVector3Array) -> void:
	world_meta = meta
	workout = meta.get("workout", {}) if meta.get("workout") is Dictionary else {}
	atmo = meta.get("atmo", {})
	_setup_route(meta, pts)
	dist = meta.get("start", 0.0)
	climate = {"arid": atmo.arid[0] if atmo else 0.0}
	_setup_scene()
	_vegetation("", {}, null)
	minimap.visible = false  # zeigt echte OSM-Karten; die Workout-Welt liegt nirgends
	var animals := preload("res://animals.gd").new()  # Rehe und Hasen (nur Kulisse)
	animals.main = self
	add_child(animals)


func _setup_scene() -> void:
	env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()
	env.sky.process_mode = Sky.PROCESS_MODE_REALTIME  # Wolken ziehen -> Umgebungslicht jeden Frame neu
	env.sky.radiance_size = Sky.RADIANCE_SIZE_256
	var sky := ShaderMaterial.new()
	sky.shader = load("res://shaders/sky.gdshader")
	# Trockengebiete: Staubdunst, gelblicher Himmel am Horizont, mehr Fernnebel
	var arid: float = climate.get("arid", 0.0)
	sky.set_shader_parameter("haze", arid)
	stars = _stars()
	sky.set_shader_parameter("stars", stars)
	env.sky.sky_material = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	env.tonemap_mode = Environment.TONE_MAPPER_AGX
	env.glow_enabled = true
	env.ssao_enabled = true
	env.fog_enabled = true
	fog_base = lerp(0.00025, 0.0006, arid)
	fog_color = Color(0.52, 0.55, 0.61).lerp(Color(0.80, 0.70, 0.55), arid)
	env.fog_density = fog_base
	env.fog_light_color = fog_color
	env.fog_aerial_perspective = 0.9
	env.fog_sky_affect = 0.2
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	sun = DirectionalLight3D.new()
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 250.0
	add_child(sun)
	moon = DirectionalLight3D.new()
	moon.light_color = Color(0.6, 0.7, 1.0)
	add_child(moon)

	rider = _make_rider(Color(1, 0.45, 0.1), cfg.get_value("rider", "name", "Ich"))
	ghost = _make_rider(Color(0.2, 0.5, 1), "")  # Kollege (UDP)
	rider.get_node("Tag").visible = false  # eigener Name stünde mitten im Blick; der Text geht weiter an den Kollegen
	ghost.visible = false
	var cam := Camera3D.new()
	cam.position = Vector3(0, 2.1, 4.6)
	cam.rotation_degrees.x = -7  # Fahrer knapp unter der Bildmitte, Straße und Horizont voraus sichtbar
	cam.far = 8000
	rider.add_child(cam)
	headlight = SpotLight3D.new()
	headlight.position = Vector3(0, 1.0, -0.7)
	headlight.spot_range = 50.0
	headlight.spot_angle = 25.0
	headlight.light_energy = 6.0
	headlight.light_color = Color(1, 0.95, 0.85)
	rider.add_child(headlight)
	add_child(weather)
	add_child(weather.make_fx())
	weather.begin(Engine.get_meta("ride", {}).get("weather", 0), climate.get("arid", 0.0))
	sound.main = self
	add_child(sound)
	# Hitzeflimmern: Vollbild-Quad an der Kamera (nie weggeschnitten), nur sichtbar wenn es heiß ist
	heat_quad = MeshInstance3D.new()
	var q := QuadMesh.new()
	q.size = Vector2(2, 2)
	heat_quad.mesh = q
	heat_quad.extra_cull_margin = 16384.0
	heat_quad.material_override = _shader("heat")
	heat_quad.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	cam.add_child(heat_quad)
	# Farbe minimal kräftiger, damit Himmel, Wasser und Vegetation zusammen satt wirken (AgX entsättigt sonst stark)
	env.adjustment_enabled = true
	env.adjustment_saturation = 1.06
	env.fog_aerial_perspective = 1.0  # Ferne nimmt die Himmelsfarbe am Horizont an (Luftperspektive)
	if not climate.is_empty() and climate.has("t_sea"):
		warmth = clampf((climate.t_sea - climate.lapse * climate.elev0 - 13.0) / 6.0, 0.0, 1.0) * clampf(0.5 + 0.5 * _season(200.0), 0.0, 1.0)
	_update_sky()

	if ui == null:
		_make_ui()
	ui_layer.add_child(minimap)
	ui_layer.move_child(minimap, 0)
	ui.setup(self)
	add_child(dev)
	dev.setup(self)
	apply_settings(Options.load_cfg())


func _make_ui() -> void:
	ui_layer = CanvasLayer.new()
	add_child(ui_layer)
	ui = preload("res://hud.gd").new()
	ui_layer.add_child(ui)
	ui.finish_requested.connect(_finish)
	ui.leave_requested.connect(_leave)


func _leave() -> void:
	get_tree().change_scene_to_file("res://start.tscn" if world else "res://picker.tscn")


func _finish(at_end := false) -> void:
	## Auswertung: Kennzahlen, TCX speichern, Zusammenfassung zeigen. at_end = Workout gerade fertig (Weiterfahren möglich).
	if recap:
		return
	var name: String = world_meta.get("name", "Cadence-Fahrt") if world else cfg.get_value("route", "name", "Cadence-Fahrt")
	var st: Dictionary = ride_log.stats(ftp, workout)
	var path: String = ride_log.save(name, st)
	recap = preload("res://recap.gd").new()
	recap_auto = at_end
	ui_layer.add_child(recap)
	var when := Time.get_datetime_string_from_system(false, true).left(16)
	recap.open(ride_log, st, "Workout geschafft!" if at_end else "Deine Fahrt", "%s · %s" % [name, when], path, at_end)
	recap.closed.connect(func(finish: bool) -> void:
		recap.queue_free()
		recap = null
		if finish:
			if recap_auto:  # nach der Auswertung am Workout-Ende weitergefahren: mit allen Daten neu speichern (gleicher Name)
				ride_log.save(name, ride_log.stats(ftp, workout))
			_leave())


func _vegetation(dir: String, meta: Dictionary, ground: Texture2D) -> void:
	# Gras + Wildblumen rund um den Fahrer (GPU, siehe grass.gdshader) und Herbstlaub als Partikel
	# Höhenkarte: heights.f32 (feines Raster, Gelände ist ein Dreiecksnetz) oder bei alten Paketen terrain.pos (= Raster)
	# Workout-Welt: Karten kommen je feiner Kachel aus world_stream.gd (set_grass), dir = ""
	var grid: Array = meta.get("heights", meta.get("grid", []))
	var side: int = cfg.get_value("world", "grass", 220)
	if side > 0 and (world or (grid.size() == 3 and ground)):
		_grass(side)
	if grid.size() == 3 and ground and side > 0:
		var h := PackedFloat32Array()
		if meta.has("heights"):
			h = FileAccess.get_file_as_bytes(dir + "heights.f32").to_float32_array()
		else:
			var pos := FileAccess.get_file_as_bytes(dir + "terrain.pos").to_vector3_array()
			h.resize(pos.size())
			for i in pos.size():
				h[i] = pos[i].y
		var heights := ImageTexture.create_from_image(Image.create_from_data(int(grid[0]), int(grid[1]), false, Image.FORMAT_RF, h.to_byte_array()))
		var g: Array = meta.ground
		set_grass(ground, heights, Vector4(g[0], g[1], g[2], g[3]), Vector3(grid[0], grid[1], grid[2]))

	# Fallende Blätter: starten in den Kronen der Laubbäume um den Fahrer (Punkte aus leaf_pts, _leaf_update), treiben mit
	# dem Wind (Schwerkraft schräg = Drift), trudeln (leaf.gdshader); mehr bei Wind und im Herbst
	leaves = GPUParticles3D.new()
	leaves.amount = 500
	leaves.lifetime = 9.0
	leaves.preprocess = 6.0
	leaves.local_coords = false  # einmal losgeflogen bleiben sie in der Welt, der Fahrer fährt durch
	leaves.visibility_aabb = AABB(Vector3(-120, -40, -120), Vector3(240, 80, 240))
	leaves.amount_ratio = 0.0
	var pm := ParticleProcessMaterial.new()
	pm.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_POINTS
	pm.direction = Vector3(0, -1, 0)
	pm.spread = 60.0
	pm.initial_velocity_min = 0.2
	pm.initial_velocity_max = 1.0
	pm.gravity = Vector3(0, -1.0, 0)
	pm.damping_min = 0.3
	pm.damping_max = 0.6
	pm.turbulence_enabled = true
	pm.turbulence_noise_strength = 2.0
	pm.turbulence_noise_scale = 3.0
	pm.turbulence_influence_min = 0.05
	pm.turbulence_influence_max = 0.2
	leaves.process_material = pm
	var quad := QuadMesh.new()
	quad.size = Vector2(0.2, 0.28)
	quad.material = _shader("leaf")
	quad.material.set_shader_parameter("autumn", autumn)
	leaves.draw_pass_1 = quad
	add_child(leaves)


func _grass(side: int) -> void:
	# Gras + Blumen als zwei MultiMeshes, Position rechnet der Shader; sichtbar erst mit Karten (set_grass)
	RenderingServer.global_shader_parameter_set("grass_r", side * 0.4 * 0.5)  # terrain.gdshader dunkelt den Boden darunter ab
	for flowers in [false, true]:
		var spacing := 0.9 if flowers else 0.4
		var n := int(side * 0.4 / spacing) if flowers else side  # gleicher Radius für beide
		var mat := _shader("grass")
		mat.set_shader_parameter("side", n)
		mat.set_shader_parameter("spacing", spacing)
		mat.set_shader_parameter("flowers", flowers)
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = _flower_mesh() if flowers else _grass_mesh()
		mm.instance_count = n * n
		var ident := PackedFloat32Array()  # Position rechnet der Shader; Transforms müssen aber Identität sein, sonst Müll
		ident.resize(n * n * 12)
		for i in n * n:
			ident[i * 12] = 1.0
			ident[i * 12 + 5] = 1.0
			ident[i * 12 + 10] = 1.0
		mm.buffer = ident
		mm.custom_aabb = AABB(Vector3(-1e5, -1e4, -1e5), Vector3(2e5, 2e4, 2e5))
		var mi := MultiMeshInstance3D.new()
		mi.multimesh = mm
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.visible = false
		add_child(mi)
		grass.append(mi)


func set_grass(ground: Texture2D, heights: Texture2D, rect: Vector4, grid: Vector3) -> void:
	for mi: MultiMeshInstance3D in grass:
		var mat: ShaderMaterial = mi.material_override
		mat.set_shader_parameter("ground", ground)
		mat.set_shader_parameter("heights", heights)
		mat.set_shader_parameter("rect", rect)
		mat.set_shader_parameter("grid", grid)
		mi.visible = true


func _grass_mesh() -> ArrayMesh:
	# Grasbüschel: 12 schmale, gebogene Halme (je 3 Abschnitte, spitz), unterschiedlich hoch und geneigt. UV.y = Höhe 0..1
	# (Wind, dunkler Fuß), COLOR.r = Helligkeit, COLOR.g = Vergilbung je Halm, Normale = Halmfläche (grass.gdshader mischt sie
	# mit "oben") -> Licht und Schatten innerhalb des Büschels statt einer flachen Farbe
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	for k in 12:
		var r := Basis(Vector3.UP, k * TAU / 12.0 + rng.randf() * 0.6)
		var hgt := rng.randf_range(0.22, 0.62)
		var w := rng.randf_range(0.009, 0.016)
		var lean := rng.randf_range(0.05, 0.25) * hgt
		var off := Vector3(rng.randf_range(-0.15, 0.15), 0, rng.randf_range(-0.15, 0.15))
		var col := Color(rng.randf_range(0.75, 1.15), rng.randf() * rng.randf(), 0, 0)
		var rows := []  # (links, rechts) je Höhe; Biegung wächst quadratisch nach oben
		for i in 4:
			var u := i / 3.0
			var c := Vector3(0, hgt * u, lean * u * u)
			var ww := w * (1.0 - u * 0.85) if i < 3 else 0.0
			rows.append([c + Vector3(-ww, 0, 0), c + Vector3(ww, 0, 0)])
		var n := (r * Vector3(0, 0.35, -1)).normalized()
		for i in 3:
			var a: Array = rows[i]
			var b: Array = rows[i + 1]
			var tri := [a[0], a[1], b[1], a[0], b[1], b[0]] if i < 2 else [a[0], a[1], b[0]]
			for p: Vector3 in tri:
				st.set_uv(Vector2(0, p.y / 0.62))
				st.set_color(col)
				st.set_normal(n)
				st.add_vertex(r * p + off)
	st.index()  # gemeinsame Vertices: der Gras-Vertexshader (Höhenkarte, Boden, Wind) läuft ~50k × je Vertex
	return st.commit()


func _flower_mesh() -> ArrayMesh:
	# Wildblume: Stiel (2 gekreuzte Streifen) mit zwei Blättern (COLOR.a 0), Blüte aus 5 schräg aufgestellten Blütenblättern
	# (COLOR.a 1 = Blütenfarbe) um eine kleine Mitte (COLOR.a 0,5). Die Blüte nickt leicht zur Seite.
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var top := Vector3(0.03, 0.4, 0.0)
	var tris := []  # [p0, p1, p2, alpha]
	for k in 2:
		var r := Basis(Vector3.UP, k * PI / 2.0)
		var s0 := r * Vector3(-0.006, 0, 0)
		var s1 := r * Vector3(0.006, 0, 0)
		tris.append([s0, s1, top + r * Vector3(0.003, 0, 0), 0.0])
		tris.append([s0, top + r * Vector3(0.003, 0, 0), top + r * Vector3(-0.003, 0, 0), 0.0])
	for k in 2:  # Blätter am Stiel
		var d := Vector3(cos(k * PI + 0.5), 0.6, sin(k * PI + 0.5)).normalized()
		var b := Vector3(0, 0.1 + 0.08 * k, 0)
		var side := d.cross(Vector3.UP).normalized() * 0.02
		tris.append([b, b + d * 0.06 + side, b + d * 0.13, 0.0])
		tris.append([b, b + d * 0.13, b + d * 0.06 - side, 0.0])
	var up := Vector3(0.25, 1, 0).normalized()  # Blüte nickt
	var ax := up.cross(Vector3(0, 0, 1)).normalized()
	var ay := up.cross(ax)
	for k in 5:
		var a0 := k * TAU / 5.0
		var dir := (ax * cos(a0) + ay * sin(a0)) + up * 0.45  # Blütenblätter schräg nach oben (Kelch)
		var sd := (ax * cos(a0 + PI / 2) + ay * sin(a0 + PI / 2)) * 0.022
		var c := top + up * 0.005
		tris.append([c + sd * 0.4, c + dir * 0.05 + sd, c + dir * 0.06, 1.0])
		tris.append([c - sd * 0.4, c + dir * 0.06, c + dir * 0.05 - sd, 1.0])
		tris.append([top + up * 0.012, top + up * 0.012 + (ax * cos(a0) + ay * sin(a0)) * 0.014, top + up * 0.012 + (ax * cos(a0 + TAU / 5) + ay * sin(a0 + TAU / 5)) * 0.014, 0.5])
	for t in tris:
		var n: Vector3 = (t[1] - t[0]).cross(t[2] - t[0]).normalized()
		for i in 3:
			st.set_uv(Vector2(0, (t[i] as Vector3).y / 0.42))
			st.set_color(Color(1, 0, 0, t[3]))
			st.set_normal(n if n.y > 0.0 else -n)
			st.add_vertex(t[i])
	return st.commit()


func _shader(name: String) -> ShaderMaterial:
	var m := ShaderMaterial.new()
	m.shader = load("res://shaders/%s.gdshader" % name)
	if name == "props":
		m.set_shader_parameter("leaves", leaf_atlas())
	return m


func leaf_atlas() -> Texture2D:
	## Blatt-Atlas für die Blattkarten (blender/trees.py schreibt ihn neben trees.glb): wie die Modelle zur Laufzeit von der
	## Platte, Mipmaps selbst. Fehlt er, bleibt der Shader-Standard (weiß, deckend) – dann nur mit alter trees.glb sinnvoll.
	if leaf_tex == null:
		var img := Image.load_from_file(ProjectSettings.globalize_path(data("res://models/leaves.png")))
		if img:
			img.generate_mipmaps()
			leaf_tex = ImageTexture.create_from_image(img)
	return leaf_tex


static func config() -> ConfigFile:
	## cadence.cfg; ältere Installationen haben noch kurbel.cfg (vor der Umbenennung) -> die gilt dann weiter
	var c := ConfigFile.new()
	if c.load(data("res://cadence.cfg")) != OK:
		c.load(data("res://kurbel.cfg"))
	elif FileAccess.file_exists(data("res://kurbel.cfg")):
		push_warning("cadence.cfg gilt, kurbel.cfg wird ignoriert – eigene Werte (difficulty, mass, peer_host …) bitte übertragen")
	return c


static func data(p: String) -> String:
	## Dateien auf der Platte (Python, routes/, models/, workouts/, cadence.cfg): im Editor res://, im Export neben der ausführbaren Datei
	if OS.has_feature("template"):
		return OS.get_executable_path().get_base_dir().path_join(p.trim_prefix("res://"))
	return p


static func uv() -> String:
	# Vom Desktop gestartet fehlt ~/.local/bin oft im PATH (steht nur in der Shell-Konfiguration): dort zuerst suchen
	var home := OS.get_environment("HOME")
	for p in [home + "/.local/bin/uv", home + "/.cargo/bin/uv"]:
		if FileAccess.file_exists(p):
			return p
	return "uv"


static func load_ground(dir: String) -> ImageTexture:
	# Bodentextur eines Streckenpakets (auch von preview.gd genutzt)
	var img := Image.load_from_file(ProjectSettings.globalize_path(dir + "ground.png"))
	if not img:
		return null
	# S3TC füllt sonst auf ein Vielfaches von 4 auf -> Textur um bis zu 3 Texel verschoben, Gras auf der Straße
	img.resize(ceili(img.get_width() / 4.0) * 4, ceili(img.get_height() / 4.0) * 4, Image.INTERPOLATE_NEAREST)
	img.generate_mipmaps()
	img.compress(Image.COMPRESS_S3TC)
	return ImageTexture.create_from_image(img)


static func load_array_mesh(base: String) -> ArrayMesh:
	# Mesh aus den Binärdateien von route.py/world.py (.pos/.nrm/.idx, optional .col/.uv/.uv2/.c0); null wenn es fehlt
	return mesh_from_arrays(read_mesh_arrays(base))


static func mesh_from_arrays(data: Array) -> ArrayMesh:
	if data.is_empty():
		return null
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, data[0], [], {}, data[1])
	return mesh


static func read_mesh_arrays(base: String) -> Array:
	# Nur Dateien lesen (darf in einem Thread laufen) -> [arrays, flags] oder [] wenn es fehlt
	var idx := FileAccess.get_file_as_bytes(base + ".idx").to_int32_array()
	if idx.is_empty():
		return []
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = FileAccess.get_file_as_bytes(base + ".pos").to_vector3_array()
	arrays[Mesh.ARRAY_NORMAL] = FileAccess.get_file_as_bytes(base + ".nrm").to_vector3_array()
	arrays[Mesh.ARRAY_INDEX] = idx
	var col := FileAccess.get_file_as_bytes(base + ".col")
	if col:
		arrays[Mesh.ARRAY_COLOR] = col.to_color_array()
	var uv := FileAccess.get_file_as_bytes(base + ".uv")
	if uv:
		arrays[Mesh.ARRAY_TEX_UV] = uv.to_vector2_array()
	var uv2 := FileAccess.get_file_as_bytes(base + ".uv2")
	if uv2:
		arrays[Mesh.ARRAY_TEX_UV2] = uv2.to_vector2_array()
	var flags := 0
	var c0 := FileAccess.get_file_as_bytes(base + ".c0")  # 4 floats je Vertex (Fahrbahnmarkierung an Kreuzungen)
	if c0:
		arrays[Mesh.ARRAY_CUSTOM0] = c0.to_float32_array()
		flags = Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT
	return [arrays, flags]


func _load_mesh(base: String, mat: Material, shadow := true) -> void:
	var mesh := load_array_mesh(base)
	if not mesh:
		return
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = mat
	if not shadow:
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)


func house_meshes() -> Dictionary:
	# Blender-Modelle (models/houses.glb): Name -> Mesh mit house-Shader je Teil; "<name>_lod" = Fernversion
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(ProjectSettings.globalize_path(data("res://models/houses.glb")), state) != OK:
		return {}
	var scene := doc.generate_scene(state)
	var meshes := {}
	for mi: MeshInstance3D in scene.find_children("*", "MeshInstance3D", true, false):
		var mesh: Mesh = mi.mesh
		for i in mesh.get_surface_count():
			var part: int = ["Wall", "Roof", "Glass"].find(mesh.surface_get_material(i).resource_name)
			if part >= 0:
				var m := _shader("house")
				m.set_shader_parameter("part", part)
				mesh.surface_set_material(i, m)
		meshes[str(mi.name)] = mesh
	scene.free()
	return meshes


func _route_trees(dir: String, meta: Dictionary, pm: Dictionary) -> PackedFloat32Array:
	## Echte Strecken: route.py schreibt Nadel/Laub/Palme/Busch; die Art wählen wir hier nach Klima (mild = Mittelmeer)
	## und Zufall je Baum (Hash der Position) -> alte Pakete bekommen die neuen Bäume ohne Neubau. -> Laubbäume (Herbstlaub)
	var cl: Dictionary = meta.get("climate", {})
	var warm: bool = float(cl.get("t_sea", 10.0)) - float(cl.get("lapse", 0.0065)) * float(cl.get("elev0", 0.0)) > 14.5
	var mix := {
		"trees_conifer": [["aleppo", 0.65], ["pinie", 1.0]] if warm else [["fichte", 0.7], ["kiefer", 1.0]],
		"trees_broad": [["steineiche", 0.45], ["olive", 0.85], ["zypresse", 1.0]] if warm else [["buche", 0.45], ["eiche", 0.8], ["birke", 1.0]],
		"trees_palm": [["palme", 1.0]], "shrubs": [["busch", 1.0]]}
	var broad := PackedFloat32Array()
	for file: String in mix:
		var buf := spin(FileAccess.get_file_as_bytes(dir + file + ".bin").to_float32_array())
		if file == "trees_broad":
			broad = buf
		var parts := {}  # Art -> 150-m-Kachel -> Instanzen (Frustum-Culling und Detail/Fern je Kachel)
		for i in range(0, buf.size(), 12):
			var h := fposmod(sin(buf[i + 3] * 4.898 + buf[i + 11] * 7.23) * 9631.17, 1.0)
			var kind: String = mix[file][-1][0]
			for e in mix[file]:
				if h < e[1]:
					kind = e[0]
					break
			var key := Vector2i(floori(buf[i + 3] / 150.0), floori(buf[i + 11] / 150.0))
			if not parts.has(kind):
				parts[kind] = {}
			if not parts[kind].has(key):
				parts[kind][key] = PackedFloat32Array()
			parts[kind][key].append_array(buf.slice(i, i + 12))
		for kind: String in parts:
			var mat := _shader("props")
			mat.set_shader_parameter("sway", SWAY.get(kind, 1.0))
			for key: Vector2i in parts[kind]:
				plant_multimesh(self, pm, kind, parts[kind][key], mat, 0.0)
				add_leaf_trees("c%d_%d" % [key.x, key.y], kind, parts[kind][key])
	return broad


func add_leaf_trees(key: String, kind: String, buf: PackedFloat32Array) -> void:
	## Kronenpunkte einer Kachel/eines Chunks merken (je Laubbaum 3 Punkte in der Krone, fest aus der Position gehasht);
	## Fußpunkte aller Bäume (ohne Büsche/Unterwuchs) für sound.gd
	if kind != "busch" and not UNDER.has(kind) and not buf.is_empty():
		var feet: PackedVector2Array = tree_pts.get(key, PackedVector2Array())
		for i in range(0, buf.size(), 12):
			feet.append(Vector2(buf[i + 3], buf[i + 11]))
		tree_pts[key] = feet
	if not LEAF_CROWN.has(kind) or buf.is_empty():
		return
	var pts: PackedVector3Array = leaf_pts[key][1] if leaf_pts.has(key) else PackedVector3Array()
	var c := Vector2.ZERO
	for i in range(0, buf.size(), 12):
		var s := Vector2(buf[i], buf[i + 2]).length()
		var base := Vector3(buf[i + 3], buf[i + 7], buf[i + 11])
		c += Vector2(base.x, base.z)
		for k in 3:
			var h := Vector3(fposmod(sin(base.x * 1.7 + k * 3.1) * 4373.1, 1.0), fposmod(sin(base.z * 2.3 + k * 1.7) * 2311.7, 1.0),
				fposmod(sin(base.x + base.z * 1.3 + k) * 9187.3, 1.0)) - Vector3(0.5, 0.5, 0.5)
			pts.append(base + Vector3(h.x * 6.0, LEAF_CROWN[kind] + h.y * 4.0, h.z * 6.0) * s)
	c /= maxf(buf.size() / 12.0, 1.0)
	leaf_pts[key] = [c if not leaf_pts.has(key) else (leaf_pts[key][0] + c) * 0.5, pts]


func ground_at(x: float, z: float) -> Vector2:
	## Workout-Welt: (Bodenhöhe, Wuchscode aus dem Alpha der Bodenkarte) aus den Gras-Karten der feinen Kacheln;
	## x = NAN, wo keine geladen ist (echte Strecken: immer NAN -> dort keine Tiere)
	if world == null:
		return Vector2(NAN, 0)
	var e: Array = world.grass_maps.get("n%d_%d" % [floori(x / 128.0), floori(z / 128.0)], [])
	if e.size() < 6:
		return Vector2(NAN, 0)
	var r: Vector4 = e[2]
	var res: float = e[3].z
	var gi: Image = e[4]
	var hi: Image = e[5]
	var u := (x - r.x) / res
	var v := (z - r.y) / res
	var i := clampi(floori(u), 0, hi.get_width() - 2)
	var j := clampi(floori(v), 0, hi.get_height() - 2)
	var fu := clampf(u - i, 0.0, 1.0)
	var fv := clampf(v - j, 0.0, 1.0)
	var h := lerpf(lerpf(hi.get_pixel(i, j).r, hi.get_pixel(i + 1, j).r, fu), lerpf(hi.get_pixel(i, j + 1).r, hi.get_pixel(i + 1, j + 1).r, fu), fv)
	return Vector2(h, roundf(gi.get_pixel(clampi(roundi(u), 0, gi.get_width() - 1), clampi(roundi(v), 0, gi.get_height() - 1)).a * 255.0))


func _leaf_update(delta: float) -> void:
	## Alle 0,5 s: Kronenpunkte im Umkreis von 70 m als Emissionspunkte; Menge nach Wind und Jahreszeit; Drift mit dem Wind
	var pm: ParticleProcessMaterial = leaves.process_material
	var w: Vector2 = weather.wind * weather.gust
	pm.gravity = Vector3(w.x * 0.35, -1.0, w.y * 0.35)  # Wind treibt die Blätter (wie eine schräge Schwerkraft)
	leaf_t -= delta
	if leaf_t > 0.0:
		return
	leaf_t = 0.5
	var here := Vector2(rider.global_position.x, rider.global_position.z)
	var near := PackedVector3Array()
	for key in leaf_pts:
		var e: Array = leaf_pts[key]
		if (e[0] as Vector2).distance_to(here) > 260.0:
			continue
		for p: Vector3 in e[1]:
			if Vector2(p.x, p.z).distance_squared_to(here) < 70.0 * 70.0:
				near.append(p)
	if near.is_empty():
		leaves.amount_ratio = 0.0
		return
	var step := maxi(1, near.size() / 512)
	var img := Image.create(ceili(float(near.size()) / step), 1, false, Image.FORMAT_RGBF)
	leaves.global_position = rider.global_position  # Emissionspunkte sind relativ zum Partikelknoten
	for i in img.get_width():
		var p := near[i * step] - leaves.global_position
		img.set_pixel(i, 0, Color(p.x, p.y, p.z))
	pm.emission_point_texture = ImageTexture.create_from_image(img)
	pm.emission_point_count = img.get_width()
	var many := clampf(img.get_width() / 120.0, 0.15, 1.0)  # dichter Wald: mehr
	leaves.amount_ratio = clampf(0.05 + 0.6 * weather.wind.length() * weather.gust / 14.0 + 0.6 * autumn, 0.0, 1.0) * many


static func spin(buf: PackedFloat32Array) -> PackedFloat32Array:
	## Jede Instanz um eine feste, zufällige Hochachse drehen (Hash der Position; Puffer hat nur Skalierung) -> Nachbarn
	## sehen verschieden aus. Darf im Loader-Thread laufen.
	for i in range(0, buf.size(), 12):
		var s := buf[i + 5]
		var a := fposmod(sin(buf[i + 3] * 12.9898 + buf[i + 11] * 78.233) * 43758.5453, 1.0) * TAU
		buf[i] = s * cos(a)
		buf[i + 2] = s * sin(a)
		buf[i + 8] = -s * sin(a)
		buf[i + 10] = s * cos(a)
	return buf


func plant_multimesh(parent: Node, pm: Dictionary, kind: String, buf: PackedFloat32Array, mat: Material, end: float) -> void:
	## Pflanzen einer Kachel: Detailmodell bis TREE_DETAIL (Büsche 240 m, Unterwuchs 120 m), Fernversion dahinter bis end (0 = unbegrenzt)
	if buf.is_empty() or not pm.has(kind):
		return
	var near := 240.0 if kind == "busch" else 120.0 if UNDER.has(kind) else TREE_DETAIL
	if pm.has(kind + "_lod"):
		_mm(parent, pm[kind], buf, mat, 0.0, near)
		_mm(parent, pm[kind + "_lod"], buf, mat, near, end)
	else:
		_mm(parent, pm[kind], buf, mat, 0.0, end)


func _mm(parent: Node, mesh: Mesh, buf: PackedFloat32Array, mat: Material, begin: float, end: float) -> void:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = buf.size() / 12
	mm.buffer = buf
	var mi := MultiMeshInstance3D.new()
	mi.multimesh = mm
	mi.material_override = mat
	mi.visibility_range_begin = begin
	mi.visibility_range_end = end
	if end > 0.0:
		mi.visibility_range_end_margin = end * 0.1
		mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	parent.add_child(mi)


func tree_meshes(path := "res://models/trees.glb") -> Dictionary:
	# Blender-Modelle (trees.glb, deco.glb, animals.glb): Name -> Mesh (Vertexfarben, props-Shader), "<art>_lod" = Fernversion
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(ProjectSettings.globalize_path(data(path)), state) != OK:
		return {}
	var scene := doc.generate_scene(state)
	var meshes := {}
	for mi: MeshInstance3D in scene.find_children("*", "MeshInstance3D", true, false):
		# neu aus den Arrays: ohne die automatisch erzeugten Mesh-LODs des Imports (die zeigen schon auf 50 m eine grobe
		# Stufe); Detail/Fern regeln wir selbst über die Sichtweite (plant_multimesh)
		var am := ArrayMesh.new()
		am.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, mi.mesh.surface_get_arrays(0))
		meshes[str(mi.name)] = am
	scene.free()
	return meshes


func prop_meshes() -> Dictionary:
	# Bäume, Büsche, Laternen, Pfosten als je ein Mesh mit Vertexfarben (Alpha 1 = leuchtet nachts)
	var pm := _primitive_props()
	var tm := tree_meshes()
	pm.merge(tree_meshes("res://models/deco.glb"))  # Zäune, Autos, Haltestelle … (blender/deco.py)
	for kind: String in TREES:  # ohne trees.glb: einfache Formen je Typ
		var fallback: String = {"fichte": "conifer", "kiefer": "conifer", "aleppo": "conifer", "pinie": "conifer", "zypresse": "conifer",
			"palme": "palm", "busch": "shrubs", "farn": "shrubs", "hochgras": "shrubs", "staude": "shrubs"}.get(kind, "broad")
		pm[kind] = tm.get(kind, pm[fallback])
		if tm.has(kind + "_lod"):
			pm[kind + "_lod"] = tm[kind + "_lod"]
	return pm


func _primitive_props() -> Dictionary:
	var trunk := Color(0.30, 0.20, 0.12, 0)
	return {
		"conifer": _combine([[_cyl(0.25, 0.25, 2.0), Vector3(0, 1, 0), trunk], [_cyl(0.0, 2.2, 8.0), Vector3(0, 6, 0), Color(0.09, 0.24, 0.12, 0)]]),
		"broad": _combine([[_cyl(0.3, 0.3, 3.0), Vector3(0, 1.5, 0), trunk], [_sphere(3.0), Vector3(0, 5.5, 0), Color(0.22, 0.42, 0.15, 0)]]),
		"palm": _combine([[_cyl(0.16, 0.24, 7.0), Vector3(0, 3.5, 0), Color(0.42, 0.33, 0.22, 0)],
			[_cyl(0.25, 3.2, 1.6), Vector3(0, 7.0, 0), Color(0.20, 0.42, 0.14, 0)]]),  # Wedel als hängender Schirm
		"shrubs": _combine([[_sphere(0.9), Vector3(0, 0.5, 0), Color(0.30, 0.34, 0.17, 0)]]),
		"lamps": _combine([[_cyl(0.07, 0.09, 6.0), Vector3(0, 3, 0), Color(0.2, 0.2, 0.22, 0)], [_sphere(0.3), Vector3(0, 6.1, 0), Color(1, 0.9, 0.7, 1)]]),
		"posts": _combine([[_cyl(0.04, 0.04, 1.0), Vector3(0, 0.5, 0), Color(0.55, 0.56, 0.58, 0)]]),
		# Leitpfosten (signs.roadside): weiß, schwarzes Band, Rückstrahler vorn und hinten (lokal ±Z = längs der Straße)
		"delineators": _combine([[_box(0.1, 1.2, 0.12), Vector3(0, 0.6, 0), Color(0.93, 0.93, 0.9, 0)],
			[_box(0.11, 0.25, 0.125), Vector3(0, 1.0, 0), Color(0.05, 0.05, 0.05, 0)],
			[_box(0.05, 0.13, 0.13), Vector3(0, 1.0, 0), Color(1.0, 0.95, 0.85, 0.1)]]),
	}


func _houses(dir: String) -> void:
	# Blender-Modelle auf die Grundrisse aus route.py; je Haus Detail- und Fernversion
	var meshes := house_meshes()
	for name: String in meshes:
		if name.ends_with("_lod"):
			continue
		var buf := FileAccess.get_file_as_bytes(dir + "house_%s.bin" % name).to_float32_array()
		# 300-m-Kacheln: Frustum-Culling pro Kachel, Details nur bis 700 m
		var chunks := {}
		for i in range(0, buf.size(), 20):
			var key := Vector2i(floori(buf[i + 3] / 300.0), floori(buf[i + 11] / 300.0))
			if not chunks.has(key):
				chunks[key] = PackedFloat32Array()
			chunks[key].append_array(buf.slice(i, i + 20))
		for key: Vector2i in chunks:
			var center := Vector3(key.x * 300.0 + 150.0, 0, key.y * 300.0 + 150.0)
			_house_chunk(meshes[name], chunks[key], center, 0.0, 700.0)
			_house_chunk(meshes[name + "_lod"], chunks[key], center, 700.0, 0.0)


func _house_chunk(mesh: Mesh, buf: PackedFloat32Array, center: Vector3, begin: float, end: float, parent: Node = null) -> void:
	buf = buf.duplicate()
	for i in range(0, buf.size(), 20):  # relativ zur Kachelmitte, damit die Sichtweite pro Kachel stimmt
		buf[i + 3] -= center.x
		buf[i + 11] -= center.z
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = buf.size() / 20
	mm.buffer = buf
	var mi := MultiMeshInstance3D.new()
	mi.multimesh = mm
	mi.position = center
	mi.visibility_range_begin = begin
	mi.visibility_range_end = end
	(parent if parent else self).add_child(mi)


func _signs(dir: String) -> void:
	# Schilder aus signs.py: Tafeln (Atlas signs.png, Instanz-Custom = UV-Rechteck), Pfosten, Ampeln
	var img := Image.load_from_file(ProjectSettings.globalize_path(dir + "signs.png"))
	var buf := FileAccess.get_file_as_bytes(dir + "sign_plates.bin").to_float32_array()
	if img and not buf.is_empty():
		img.generate_mipmaps()
		img.compress(Image.COMPRESS_S3TC)  # Atlas kann groß werden (viele Straßennamen): 4× weniger VRAM
		var mat := _shader("sign")
		mat.set_shader_parameter("atlas", ImageTexture.create_from_image(img))
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		var quad := QuadMesh.new()  # 1 × 1 m, Vorderseite +Z; Größe steckt in der Instanz
		mm.mesh = quad
		mm.instance_count = buf.size() / 16
		mm.buffer = buf
		var mi := MultiMeshInstance3D.new()
		mi.multimesh = mm
		mi.material_override = mat
		add_child(mi)
	var pm := prop_meshes()
	_instances(dir + "sign_posts.bin", pm.posts, 0.0)
	_instances(dir + "delineators.bin", pm.delineators, 0.0)
	# Ampel: Mast, Gehäuse, drei Lampen nach vorn (+Z); Alpha kodiert die Lampe für signal.gdshader
	var dark := Color(0.12, 0.12, 0.13, 0)
	var lamps := _combine([[_cyl(0.06, 0.07, 3.0), Vector3(0, 1.5, 0), Color(0.45, 0.46, 0.48, 0)],
		[_box(0.32, 0.95, 0.24), Vector3(0, 3.3, 0), dark],
		[_sphere(0.1), Vector3(0, 3.6, 0.1), Color(1.0, 0.08, 0.05, 0.25)],
		[_sphere(0.1), Vector3(0, 3.3, 0.1), Color(1.0, 0.65, 0.05, 0.5)],
		[_sphere(0.1), Vector3(0, 3.0, 0.1), Color(0.1, 1.0, 0.35, 0.75)]])
	var sig := FileAccess.get_file_as_bytes(dir + "signals.bin").to_float32_array()
	if not sig.is_empty():
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = lamps
		mm.instance_count = sig.size() / 12
		mm.buffer = sig
		var mi := MultiMeshInstance3D.new()
		mi.multimesh = mm
		mi.material_override = _shader("signal")
		add_child(mi)


func _instances(file: String, mesh: Mesh, sway: float) -> PackedFloat32Array:
	var buf := FileAccess.get_file_as_bytes(file).to_float32_array()
	if buf.is_empty():
		return buf
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = buf.size() / 12
	mm.buffer = buf
	var mi := MultiMeshInstance3D.new()
	mi.multimesh = mm
	var mat := _shader("props")
	mat.set_shader_parameter("sway", sway)
	mi.material_override = mat
	add_child(mi)
	return buf


func _combine(parts: Array) -> ArrayMesh:
	# [[PrimitiveMesh, Versatz, Farbe (sRGB, Alpha = leuchtet)], …] -> ein Mesh mit Vertexfarben
	var v := PackedVector3Array()
	var n := PackedVector3Array()
	var c := PackedColorArray()
	var idx := PackedInt32Array()
	for p in parts:
		var a: Array = p[0].get_mesh_arrays()
		var base := v.size()
		for x in a[Mesh.ARRAY_VERTEX]:
			v.append(x + p[1])
			c.append(p[2].srgb_to_linear())
		n.append_array(a[Mesh.ARRAY_NORMAL])
		for i in a[Mesh.ARRAY_INDEX]:
			idx.append(i + base)
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = v
	arrays[Mesh.ARRAY_NORMAL] = n
	arrays[Mesh.ARRAY_COLOR] = c
	arrays[Mesh.ARRAY_INDEX] = idx
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


func _cyl(top: float, bottom: float, h: float) -> CylinderMesh:
	var m := CylinderMesh.new()
	m.top_radius = top
	m.bottom_radius = bottom
	m.height = h
	m.radial_segments = 7
	m.rings = 0
	return m


func _sphere(r: float) -> SphereMesh:
	var m := SphereMesh.new()
	m.radius = r
	m.height = r * 1.7
	m.radial_segments = 8
	m.rings = 4
	return m


func _stars() -> ImageTexture:
	var img := Image.create(2048, 1024, false, Image.FORMAT_RGB8)
	var rng := RandomNumberGenerator.new()
	for i in 5000:
		var b := pow(rng.randf(), 4.0)
		img.set_pixel(rng.randi() % 2048, rng.randi() % 1024, Color(b, b, b))
	return ImageTexture.create_from_image(img)


func sun_dir() -> Vector3:
	# Sonnenstand aus Ort + echter Uhrzeit (vereinfachte NOAA-Formel, ~0.5° genau). Godot: -Z = Nord, +X = Ost.
	var shift: float = cfg.get_value("world", "time_shift_hours", 0.0)
	var d := (Time.get_unix_time_from_system() + shift * 3600.0) / 86400.0 - 10957.5  # Tage seit J2000
	var g := deg_to_rad(357.529 + 0.98560028 * d)
	var l := deg_to_rad(280.459 + 0.98564736 * d + 1.915 * sin(g) + 0.020 * sin(2.0 * g))
	var e := deg_to_rad(23.439 - 0.00000036 * d)
	var ra := atan2(cos(e) * sin(l), cos(l))
	var dec := asin(sin(e) * sin(l))
	var ha := deg_to_rad(fposmod(280.46061837 + 360.98564736629 * d + geo.x, 360.0)) - ra
	var lat := deg_to_rad(geo.y)
	var alt := asin(sin(lat) * sin(dec) + cos(lat) * cos(dec) * cos(ha))
	var az := atan2(-sin(ha), tan(dec) * cos(lat) - sin(lat) * cos(ha))
	return Vector3(sin(az) * cos(alt), sin(alt), -cos(az) * cos(alt))


func _set_clock(hour: float) -> void:
	## Tageszeit der Fahrt (Startbildschirm/Karte) als Sonnenzeit am Ort der Strecke -> time_shift_hours (nur diese Fahrt); die
	## Uhr läuft danach normal weiter. -1 = echte Uhrzeit (+ Versatz aus cadence.cfg), -2 = Abendsonne: ~6° über dem Horizont.
	if hour == -1.0:
		return
	var now := fposmod(Time.get_unix_time_from_system() / 3600.0 + geo.x / 15.0, 24.0)
	if hour == -2.0:
		hour = 12.0
		while hour < 22.0:
			cfg.set_value("world", "time_shift_hours", fposmod(hour - now + 12.0, 24.0) - 12.0)
			if sun_dir().y < sin(deg_to_rad(6.0)):
				break
			hour += 0.1
	cfg.set_value("world", "time_shift_hours", fposmod(hour - now + 12.0, 24.0) - 12.0)


func apply_settings(c: ConfigFile) -> void:
	## Grafik aus dem Einstellungsmenü (options.gd), auch mitten in der Fahrt
	if sun:
		sun.shadow_enabled = c.get_value("video", "shadows", true)
	if env:
		env.ssao_enabled = c.get_value("video", "ssao", true)


func snow_line() -> float:
	# Höhe (Welt-y), ab der das Monatsmittel unter 0 °C liegt -> Schnee. Klima aus route.py, Monat aus cadence.cfg oder echtem Datum.
	# ponytail: 0-°C-Monatsmittel als Schneegrenze, kein Schneefall-/Niederschlagsmodell; reicht für Winter im Mittelgebirge, Sommer in den Alpen
	if climate.is_empty():
		return 1e5  # alte Pakete: nie Schnee
	return (climate.t_sea - climate.amp * _season(15.0)) / climate.lapse - climate.elev0


func _season(peak_doy: float) -> float:
	# 1 am Tag peak_doy (Nordhalbkugel), -1 ein halbes Jahr später; Südhalbkugel um ein halbes Jahr versetzt. Monat aus cadence.cfg oder Datum
	var month: int = cfg.get_value("world", "month", 0)
	var doy := (month - 0.5) * 30.44 if month > 0 else fposmod(Time.get_unix_time_from_system() / 86400.0 - 10957.5, 365.25)
	return cos(TAU * (doy - peak_doy) / 365.25) * (-1.0 if climate.get("south", false) else 1.0)


func _autumn() -> float:
	# Herbstlaub: ±2 Monate um Ende Oktober, nur wo es Jahreszeiten gibt (Tropen: Laubbäume immergrün). Alte Pakete: immer
	if climate.is_empty():
		return 1.0
	return clampf((_season(293.0) - 0.4) / 0.4, 0.0, 1.0) * clampf((climate.amp - 3.0) / 3.0, 0.0, 1.0)


func _update_sky() -> void:
	var d := sun_dir()
	RenderingServer.global_shader_parameter_set("sun_dir", d)  # Wolkenschatten am Boden
	sun.basis = Basis.looking_at(-d)
	var day := smoothstep(-0.12, 0.08, d.y)
	var night := 1.0 - day
	RenderingServer.global_shader_parameter_set("night", night)
	sun.light_energy = smoothstep(-0.04, 0.05, d.y) * 1.6  # kräftige Sonne, wenig Füll-Licht -> klare Schatten
	sun.light_color = Color(1.0, 0.55, 0.3).lerp(Color(1.0, 0.97, 0.92), smoothstep(0.0, 0.35, d.y))
	sun.visible = d.y > -0.05
	# ponytail: Mond grob gegenüber der Sonne; echte Mondbahn wenn jemand nachts den Mond sucht
	moon.basis = Basis.looking_at(-Vector3(-d.x, max(0.35, -d.y), -d.z).normalized())
	moon.light_energy = 0.12 * night
	moon.visible = night > 0.05
	# Umgebungslicht: tags vom Himmel, nachts/in der Dämmerung ein blaues Grundlicht, sonst wird alles schwarz
	# Dämmerung: kein aufgeblasener Himmel mehr (der hellte auch Spiegelungen und Dunst auf -> blaue Welt, Straße wie Wasser,
	# weißes Gras); stattdessen etwas mehr neutrales Umgebungslicht
	var dusk := day * (1.0 - smoothstep(0.0, 0.2, d.y))  # Sonne knapp am Horizont
	env.ambient_light_color = Color(0.4, 0.45, 0.65).lerp(Color(0.52, 0.5, 0.5), day)  # nachts blau, tags/Dämmerung neutral
	env.ambient_light_sky_contribution = smoothstep(0.0, 0.25, d.y)
	env.ambient_light_energy = lerp(0.35, 0.7, day) * (1.0 + 0.45 * dusk)
	headlight.visible = night > 0.4


func _make_rider(color: Color, label: String) -> Node3D:
	## Rennrad + Fahrer aus models/rider.glb (blender/rider.py), Beine per IK an den Pedalen; rider.gd baut ohne glb eine einfache Version
	var r: Node3D = preload("res://rider.gd").new()
	add_child(r)
	r.setup(color, label)
	return r


func _box(x: float, y: float, z: float) -> BoxMesh:
	var m := BoxMesh.new()
	m.size = Vector3(x, y, z)
	return m


func grade_at(d: float) -> float:
	# A nach B: am Ende nicht über den Neustart bei km 0 hinweg messen (sonst kurz −10 % am Kickr)
	var p := fposmod(d, route_len)
	var lo := p - 5.0 if is_loop else maxf(p - 5.0, 0.0)
	var hi := p + 5.0 if is_loop else minf(p + 5.0, route_len)
	var a := curve.sample_baked(fposmod(hi, route_len) if is_loop else hi)
	var b := curve.sample_baked(fposmod(lo, route_len) if is_loop else lo)
	return (a.y - b.y) / maxf(hi - lo, 1.0) * 100.0


func _place(node: Node3D, d: float, lane: float, delta := 0.0) -> void:
	var t := curve.sample_baked_with_rotation(fposmod(d, route_len))
	t.origin += t.basis.x * lane
	t.basis = Basis.looking_at(-t.basis.z.slide(Vector3.UP).normalized())  # aufrecht, nicht mitkippen
	if delta > 0.0 and node.position.distance_to(t.origin) < 20.0:
		# Blickrichtung weich nachführen (Kehren aus 5-m-Punkten sind eckig, die Kamera hängt am Fahrer); nur Optik,
		# Position und Steigung bleiben exakt auf der Strecke. Nach Sprüngen (Neustart, Teleport) sofort.
		t.basis = node.basis.slerp(t.basis, 1.0 - exp(-delta * 4.0)).orthonormalized()
	node.transform = t


func _process(delta: float) -> void:
	_read_bridge()
	_read_net()
	if world:
		world.update(dev.stream_s(dist), dev.view_pos(rider.global_position) if rider else Vector3.ZERO, delta)
	if not rider:  # Workout-Welt: Kopf noch nicht da; Bridge trotzdem füttern (Watchdog)
		_send_trainer(delta)
		ui.update_loading(self, delta)
		return

	# Fahrphysik: Antrieb = P/v, Widerstand = Rollen + Hangabtrieb + Luft. Wartet, bis die Welt um den Start steht.
	_check_sensor()
	if (world == null or world.world_ready) and not paused():
		var theta := atan(grade_at(dist) / 100.0)
		var m: float = cfg.get_value("rider", "mass", 89.0)
		# Luft mit Gegen-/Rückenwind (weather.gd). Nur Fahrphysik: der Trainer bekommt weiter nur die Steigung (SIM)
		var resist := m * G * (sin(theta) + CRR * cos(theta)) + weather.air_force(speed, weather.headwind(-rider.global_basis.z), RHO, CDA)
		speed = max(0.0, speed + (power / max(speed, 1.0) - resist) / m * delta)
		dist += speed * delta
		if speed > 0.1:
			elapsed += delta
		wo_time = advance_workout(wo_time, delta, power, cadence)
		if speed > 0.1 or power > 0.0 or ride_log.size() > 0:  # Aufzeichnung ab dem ersten Tritt
			ride_log.add(delta, power, cadence, hr, speed, dist, curve.sample_baked(fposmod(dist, route_len)).y + world_meta.get("elev0", 0),
				target_watts() if not workout.is_empty() else 0.0, wo_time)
		if not workout.is_empty() and not recap_shown and wo_time >= workout.duration:
			recap_shown = true
			_finish(true)

	_place(rider, dist, 1.2, delta)
	ghost.visible = Time.get_ticks_msec() / 1000.0 - peer_seen < 5.0
	if ghost.visible:
		_place(ghost, peer_dist, -1.2, delta)
	rider.pedal(cadence if speed > 0.3 and not paused() else 0.0, 0.0 if paused() else speed, delta)
	if ghost.visible:
		ghost.pedal(peer_cadence, peer_speed, delta)
	_update_sky()
	if not atmo.is_empty():
		_atmosphere()
	env.fog_density = fog_base
	env.fog_light_color = fog_color
	weather.update(delta, climate.get("arid", 0.0), rider.global_position, snow_y,
		env, env.sky.sky_material, sun, fog_base, warmth)
	heat_quad.visible = weather.heat > 0.02
	_leaf_update(delta)
	sound.update(delta)
	RenderingServer.global_shader_parameter_set("player", dev.view_pos(rider.global_position))  # Gras um die (Flug-)Kamera
	minimap.update(rider.position, curve.sample_baked(fposmod(dist + 30.0, route_len)), ghost.position, ghost.visible)
	_send_trainer(delta)
	ui.refresh(delta)


func paused() -> bool:
	## Pausemenü oder Auswertung offen: Strecke, Fahrzeit, Workout-Uhr und Aufzeichnung stehen. Der Trainer bekommt weiter
	## denselben Befehl wie vorher (Widerstand unverändert) – Pause ändert nie die Trainersteuerung.
	return (recap != null and not recap_auto) or (ui != null and ui.menu != null and ui.menu.visible) \
		or (world != null and world.failed != "")


func _check_sensor() -> void:
	if not sensor_lost and Time.get_ticks_msec() / 1000.0 - data_t > SENSOR_TIMEOUT:
		sensor_lost = true
		power = 0.0
		cadence = 0.0
		hr = 0.0
		if ui:
			ui.flash("Kein Signal vom Trainer", preload("res://ui_theme.gd").BAD)


func _send_trainer(delta: float) -> void:
	# Genau ein Steuerbefehl je Paket: ERG (Watt) oder SIM (Steigung), nie beides
	send_timer -= delta
	if send_timer > 0.0:
		return
	send_timer = 0.2
	var cmd := trainer_command(erg, target_watts(), trainer_grade() if rider else 0.0)
	if trainer_owner:
		bridge.put_packet(JSON.stringify(cmd).to_utf8_buffer())
	if rider and cfg.get_value("net", "peer_host", "") != "":
		net.put_packet(JSON.stringify({"n": rider.get_node("Tag").text, "d": dist, "c": cadence}).to_utf8_buffer())


static func trainer_command(erg_on: bool, watts: float, grade: float) -> Dictionary:
	# ERG nur mit Ziel (Workout läuft, Abschnitt nicht frei); sonst SIM mit der Steigung der Strecke
	if erg_on and watts > 0.0:
		return {"erg": roundi(watts)}
	return {"grade": grade}


static func advance_workout(t: float, delta: float, watts: float, rpm: float) -> float:
	# Workout-Zeit läuft, solange getreten wird (Pause = stehen bleiben); Tempo und Strecke spielen keine Rolle
	return t + delta if watts > 0.0 or rpm > 0.0 else t


static func target_frac(w: Dictionary, t: float) -> float:
	# Ziel als Anteil FTP zur Workout-Zeit t, 0 = frei/kein Workout/vorbei. Gleiche Logik wie workout.py::target
	for s in w.get("segments", []):
		if t >= s.t0 and t < s.t0 + s.dur:
			return 0.0 if s.free else s.p0 + (s.p1 - s.p0) * (t - s.t0) / s.dur
	return 0.0


func target_watts() -> float:
	return target_frac(workout, wo_time) * ftp * (1.0 + 0.05 * erg_bias)


func _shift(d: int) -> void:
	# Schalten: im ERG mit Ziel = Intensität ±5 %, sonst virtueller Gang; in der Pause nichts
	if paused():
		return
	if erg and target_watts() > 0.0:
		erg_bias = clampi(erg_bias + d, -10, 10)
	else:
		gear += d


func _atmosphere() -> void:
	# Biom-Übergänge: Dunst, Nebel, Schneegrenze, Sonnenwärme weich nach der Position auf der Strecke
	var k := clampf(dist / atmo.step, 0.0, atmo.arid.size() - 1.001)
	var i := int(k)
	var f := k - i
	var at := func(key: String) -> float: return lerpf(atmo[key][i], atmo[key][i + 1], f)
	var arid: float = at.call("arid")
	climate.arid = arid  # Wetter: Wüste fast ohne Regen
	(env.sky.sky_material as ShaderMaterial).set_shader_parameter("haze", arid)
	fog_base = lerp(0.00032, 0.0007, arid) * at.call("fog")
	warmth = clampf(at.call("warm") * 1.2 + arid * 0.4, 0.0, 1.0)
	if world and world.mats.has("water"):
		world.mats.water.set_shader_parameter("warm", clampf(at.call("warm") * 1.6, 0.0, 1.0))
	fog_color = Color(0.52, 0.55, 0.61).lerp(Color(0.80, 0.70, 0.55), arid)
	sun.light_color = sun.light_color.lerp(Color(1.0, 0.86, 0.68), 0.35 * at.call("warm"))
	snow_y = at.call("snow")
	RenderingServer.global_shader_parameter_set("snow_line", snow_y)


func trainer_grade() -> float:
	# ponytail: virtueller Gang = Steigungs-Offset; echtes Übersetzungsmodell wenn sich das falsch anfühlt
	var diff: float = cfg.get_value("rider", "difficulty", 0.5)
	var step: float = cfg.get_value("rider", "gear_step", 1.0)
	return clamp(grade_at(dist) * diff + gear * step, -10.0, 20.0)


func _read_bridge() -> void:
	while bridge.get_available_packet_count() > 0:
		var msg = JSON.parse_string(bridge.get_packet().get_string_from_utf8())
		if msg is not Dictionary:
			continue
		if msg.has("power") or msg.has("cadence"):
			data_t = Time.get_ticks_msec() / 1000.0
			sensor_lost = false
		power = msg.get("power", power)
		cadence = msg.get("cadence", cadence)
		hr = msg.get("hr", hr)
		status = msg.get("status", status)
		match msg.get("button", ""):
			"up": _shift(1)
			"down": _shift(-1)


func _read_net() -> void:
	while net.get_available_packet_count() > 0:
		var msg = JSON.parse_string(net.get_packet().get_string_from_utf8())
		if msg is Dictionary and msg.has("d"):
			var now := Time.get_ticks_msec() / 1000.0
			if now - peer_seen < 2.0 and now > peer_seen:
				peer_speed = lerpf(peer_speed, maxf(float(msg.d) - peer_dist, 0.0) / (now - peer_seen), 0.3)
			peer_dist = msg.d
			peer_name = str(msg.get("n", "Kollege"))
			peer_cadence = msg.get("c", 0.0)
			ghost.get_node("Tag").text = peer_name
			peer_seen = Time.get_ticks_msec() / 1000.0


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		if recap:
			recap.closed.emit(false)
		elif ui:
			ui.toggle_menu()
	elif event.is_action_pressed("ui_up"):
		_shift(1)
	elif event.is_action_pressed("ui_down"):
		_shift(-1)
	elif event is InputEventKey and event.pressed and event.keycode == KEY_F3:
		diag = not diag
	elif event is InputEventKey and event.pressed and event.keycode == KEY_W:
		weather.next_preset()
	elif event is InputEventKey and event.pressed and event.keycode == KEY_M:
		sound.toggle_mute()


func _region() -> String:
	region_t -= get_process_delta_time()
	if region_t <= 0.0:
		region_t = 0.5
		for r in world_meta.get("regions", []):
			if dist < r.s1:
				region_label = r.label
				break
	return region_label


