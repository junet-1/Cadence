extends Node3D
## Prozedurale Welt streamen: world.py läuft als Kindprozess und erzeugt Kacheln vor dem Fahrer (eine JSON-Zeile je fertigem
## Element auf stdout), wir schicken die Fahrerposition (eine Zahl je Zeile auf stdin). Ein Loader-Thread liest die Dateien
## (und löscht sie: alles ist aus dem Seed wiederherstellbar), der Hauptthread baut daraus Meshes – mit Zeitbudget je Frame,
## damit das Bild nie hängt. Nichts hier redet mit dem Trainer.

signal meta_ready(meta: Dictionary, pts: PackedVector3Array)

const BUDGET_MS := 4.0  # je Frame höchstens so lange Kacheln einbauen

var main: Node3D  # main.gd: Materialien, Prop-Meshes, Gras
var pid := -1
var io: FileAccess
var err: FileAccess
var dir := ""
var buf := ""
var world_ready := false
var failed := ""

var nodes := {}  # Schlüssel -> Node3D
var want := {}  # Schlüssel -> laufende Nummer der gültigen Meldung (Abwurf/Neuanforderung)
var grass_maps := {}  # feine Kachel -> [Boden, Höhen, rect, grid]
var serial := 0
var inbox: Array = []  # [Nummer, Meldung] für den Loader-Thread
var done: Array = []  # [Nummer, Meldung, Daten] fertig gelesen
var mutex := Mutex.new()
var sem := Semaphore.new()
var thread := Thread.new()
var quit := false
var last_s := -1e9
var send_t := 0.0
var cur_grass := ""

var mats := {}
var props := {}  # gemeinsame Meshes (Bäume, Pfosten …)
var house_meshes := {}
var stats := {"gen_ms": 0.0, "gen_n": 0, "build_ms": 0.0, "build_n": 0, "near": 0, "mid": 0, "far": 0, "road": 0,
	"lag": 0.0, "lag_max": 0.0, "todo": 0, "ero": [0, 0, 0, 0], "rss": 0, "tris": 0}  # lag/todo/ero/rss meldet world.py


func start(opts: Dictionary) -> void:
	dir = OS.get_user_data_dir() + "/world"
	DirAccess.make_dir_recursive_absolute(dir)
	for f in DirAccess.get_files_at(dir):  # Reste einer alten Fahrt
		DirAccess.remove_absolute(dir + "/" + f)
	var args := ["run", ProjectSettings.globalize_path(main.data("res://world.py")), "stream", "--out", dir,
		"--seed", str(opts.get("seed", 1)), "--biome", str(opts.get("biome", "reise")),
		"--drama", str(opts.get("drama", 1.0)), "--density", str(opts.get("density", 1.0)), "--variation", str(opts.get("variation", 1.0)),
		"--profile", str(opts.get("profile", "auto"))]
	if opts.get("workout", "") != "":
		args += ["--workout", ProjectSettings.globalize_path(opts.workout), "--ftp", str(opts.get("ftp", 250))]
	var r := OS.execute_with_pipe(main.uv(), args, false)
	if r.is_empty():
		failed = "uv nicht gefunden – world.py kann nicht starten"
		return
	pid = r.pid
	io = r.stdio
	err = r.stderr
	thread.start(_loader)


func _exit_tree() -> void:
	quit = true
	sem.post()
	if thread.is_started():
		thread.wait_to_finish()
	if pid != -1:
		OS.kill(pid)


func update(s: float, rider: Vector3, delta: float) -> void:
	## Jeden Frame von main.gd: Position melden, Meldungen lesen, fertige Kacheln einbauen, Gras-Karten wechseln.
	if pid == -1:
		return
	send_t -= delta
	if send_t <= 0.0 and absf(s - last_s) > 10.0:
		send_t = 0.3
		last_s = s
		io.store_line("%.1f" % s)
	_read()
	_integrate()
	var key := "n%d_%d" % [floori(rider.x / 128.0), floori(rider.z / 128.0)]
	if key != cur_grass and grass_maps.has(key):
		cur_grass = key
		var g: Array = grass_maps[key]
		main.set_grass(g[0], g[1], g[2], g[3])


func _read() -> void:
	var e := err.get_as_text()
	if e != "":
		printerr("world.py: ", e.strip_edges())
		if "Error" in e or "Traceback" in e:
			failed = "Weltgenerator abgestürzt (Details im Terminal)"
	buf += io.get_as_text()
	var nl := buf.rfind("\n")
	if nl < 0:
		if not OS.is_process_running(pid) and failed == "":
			failed = "Weltgenerator beendet"
		return
	var lines := buf.substr(0, nl).split("\n", false)
	buf = buf.substr(nl + 1)
	for line in lines:
		var msg = JSON.parse_string(line)
		if msg is not Dictionary:
			continue
		if msg.has("meta"):
			_on_meta(msg.meta)
		elif msg.has("ready"):
			world_ready = true
			print("Welt bereit nach %d ms" % msg.ms)
		elif msg.has("drop"):
			want.erase(msg.drop)
			grass_maps.erase(msg.drop)
			main.leaf_pts.erase(msg.drop)
			main.tree_pts.erase(msg.drop)
			if nodes.has(msg.drop):
				nodes[msg.drop].queue_free()
				nodes.erase(msg.drop)
		else:
			var key: String = msg.get("tile", msg.get("road", ""))
			serial += 1
			want[key] = serial
			stats.gen_ms += msg.get("ms", 0.0)
			stats.gen_n += 1
			stats.lag = msg.get("lag", 0.0)
			stats.lag_max = maxf(stats.lag_max, stats.lag) if world_ready else 0.0  # Verzug erst nach dem Start zählen
			stats.todo = msg.get("todo", 0)
			stats.ero = msg.get("ero", stats.ero)
			stats.rss = msg.get("rss", 0)
			mutex.lock()
			inbox.append([serial, msg])
			mutex.unlock()
			sem.post()


func _on_meta(path: String) -> void:
	var meta = JSON.parse_string(FileAccess.get_file_as_string(path))
	var pts := FileAccess.get_file_as_bytes(dir + "/route.pos").to_vector3_array()
	var terrain: ShaderMaterial = main._shader("terrain")
	terrain.set_shader_parameter("vertex_ground", true)
	mats.terrain = terrain
	mats.road = main._shader("road")
	mats.water = main._shader("water")
	for kind in main.TREES:
		var m: ShaderMaterial = main._shader("props")
		m.set_shader_parameter("sway", main.SWAY.get(kind, 1.0))
		mats[kind] = m
	mats.props = main._shader("props")
	var img := Image.load_from_file(dir + "/signs.png")
	if img:
		img.generate_mipmaps()
		var sign: ShaderMaterial = main._shader("sign")
		sign.set_shader_parameter("atlas", ImageTexture.create_from_image(img))
		mats.sign = sign
	props = main.prop_meshes()
	house_meshes = main.house_meshes()
	meta_ready.emit(meta, pts)


func _loader() -> void:
	# Eigener Thread: nur Dateien lesen/dekodieren (keine Ressourcen, keine Nodes – die baut der Hauptthread)
	while true:
		sem.wait()
		if quit:
			return
		mutex.lock()
		var job = inbox.pop_front() if inbox else null
		mutex.unlock()
		if job == null:
			continue
		var msg: Dictionary = job[1]
		var base: String = dir + "/" + str(msg.get("tile", msg.get("road", "")))
		var data := {}
		data.mesh = main.read_mesh_arrays(base)
		if msg.get("water", false):
			data.water = main.read_mesh_arrays(base + ".water")
		for kind in msg.get("plants", []):
			data[kind] = main.spin(_take(base + ".%s.bin" % kind).to_float32_array())
		for mdl in msg.get("houses", []):
			data["house_" + mdl] = _take(base + ".house_%s.bin" % mdl).to_float32_array()
		for kind in msg.get("deco", []):
			data["deco_" + kind] = _take(base + ".deco_%s.bin" % kind).to_float32_array()
		for p in msg.get("props", []):
			data["prop_" + p] = _take(base + ".%s.bin" % p).to_float32_array()
		if msg.has("grass"):
			data.ground = Image.load_from_file(base + ".g.png")
			DirAccess.remove_absolute(base + ".g.png")
			var g: Array = msg.grass
			data.heights = Image.create_from_data(int(g[4]), int(g[5]), false, Image.FORMAT_RF, _take(base + ".h.f32"))
		for ext in [".pos", ".nrm", ".idx", ".col", ".uv", ".uv2", ".c0", ".water.pos", ".water.nrm", ".water.idx", ".water.col", ".water.uv"]:
			if FileAccess.file_exists(base + ext):
				DirAccess.remove_absolute(base + ext)
		mutex.lock()
		done.append([job[0], msg, data])
		mutex.unlock()


func _take(path: String) -> PackedByteArray:
	var b := FileAccess.get_file_as_bytes(path)
	DirAccess.remove_absolute(path)
	return b


func _integrate() -> void:
	var t0 := Time.get_ticks_usec()
	while Time.get_ticks_usec() - t0 < BUDGET_MS * 1000.0:
		mutex.lock()
		var job = done.pop_front() if done else null
		mutex.unlock()
		if job == null:
			return
		var msg: Dictionary = job[1]
		var key: String = msg.get("tile", msg.get("road", ""))
		if want.get(key, -1) != job[0]:
			continue  # inzwischen abgeworfen oder neu angefordert
		var t1 := Time.get_ticks_usec()
		var node := _build(msg, job[2])
		if nodes.has(key):
			nodes[key].queue_free()
		nodes[key] = node
		add_child(node)
		stats.build_ms += (Time.get_ticks_usec() - t1) / 1000.0
		stats.build_n += 1


func _build(msg: Dictionary, data: Dictionary) -> Node3D:
	var root := Node3D.new()
	var is_road := msg.has("road")
	var lod: String = "road" if is_road else msg.lod
	var mesh: ArrayMesh = main.mesh_from_arrays(data.mesh)
	if mesh:
		var mi := MeshInstance3D.new()
		mi.mesh = mesh
		mi.material_override = mats.road if is_road else mats.terrain
		if lod == "far" or is_road:  # Fahrbahn liegt 0,2 m über der Trasse: ihr Schatten wäre bei tiefer Sonne ein blaugrauer Streifen an der Kante
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		root.add_child(mi)
	if data.has("water"):
		var w := MeshInstance3D.new()
		w.mesh = main.mesh_from_arrays(data.water)
		w.material_override = mats.water
		w.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		root.add_child(w)
	for kind in main.TREES:
		if data.has(kind):
			# nah Detailmodell, dahinter Fernversion; Bäume bis 1,5 km, Büsche bis 400 m
			main.plant_multimesh(root, props, kind, data[kind], mats[kind], main.UNDER.get(kind, 400.0 if kind == "busch" else 1500.0))
			if not is_road and lod == "near":
				main.add_leaf_trees(msg.tile, kind, data[kind])
	for k in data:
		if k.begins_with("house_"):
			var mdl: String = k.substr(6)
			if house_meshes.has(mdl):
				var c := Vector3(data[k][3], 0, data[k][11])
				main._house_chunk(house_meshes[mdl], data[k], c, 0.0, 700.0, root)
				main._house_chunk(house_meshes[mdl + "_lod"], data[k], c, 700.0, 0.0, root)
	for k in data:
		if k.begins_with("deco_"):  # Deko an Häusern (world._deco): Drehung kommt mit, Bäume wiegen wie Bäume
			var kind: String = k.substr(5)
			if props.has(kind):
				var far: bool = kind in main.TREES or kind.begins_with("fels")  # Bäume und Felsen sieht man weit
				main.plant_multimesh(root, props, kind, data[k], mats[kind] if mats.has(kind) else mats.props, 1500.0 if far else 450.0)
	for p in ["posts", "delineators", "lamps"]:
		if data.has("prop_" + p):
			_multimesh(root, props[p], data["prop_" + p], mats.props, 600.0)
	if data.has("prop_plates") and mats.has("sign"):
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = QuadMesh.new()
		mm.instance_count = data.prop_plates.size() / 16
		mm.buffer = data.prop_plates
		var mi := MultiMeshInstance3D.new()
		mi.multimesh = mm
		mi.material_override = mats.sign
		root.add_child(mi)
	if data.has("ground") and data.ground:
		var g: Array = msg.grass
		grass_maps[msg.tile] = [ImageTexture.create_from_image(data.ground), ImageTexture.create_from_image(data.heights),
			Vector4(g[0], g[1], g[2], g[3]), Vector3(g[4], g[5], g[6]), data.ground, data.heights]  # Bilder: main.ground_at (Tiere)
		if msg.tile == cur_grass:
			cur_grass = ""  # neu einsetzen
	stats[lod] = stats.get(lod, 0) + 1
	return root


func _multimesh(root: Node3D, mesh: Mesh, buf: PackedFloat32Array, mat: Material, range_end: float) -> void:
	if buf.is_empty():
		return
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = buf.size() / 12
	mm.buffer = buf
	var mi := MultiMeshInstance3D.new()
	mi.multimesh = mm
	mi.material_override = mat
	mi.visibility_range_end = range_end
	mi.visibility_range_end_margin = range_end * 0.1
	mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	root.add_child(mi)


func diagnostics() -> String:
	var counts := {"near": 0, "mid": 0, "far": 0, "road": 0}
	for k: String in nodes:
		var c := k[0]
		counts[{"n": "near", "m": "mid", "f": "far", "r": "road"}[c]] += 1
	mutex.lock()
	var queued := inbox.size() + done.size()
	mutex.unlock()
	return "Welt: %d fein · %d mittel · %d fern · %d Straße · %d in Arbeit · %d offen | erzeugen Ø %.0f ms · einbauen Ø %.1f ms\nErosion: %d Regionen (Ø %d ms, %d im Voraus, %d im Speicher) · Verzug %.1f s (max %.1f s) · Generator %d MB\nFPS %d · %d Draw Calls · %.1f M Dreiecke · VRAM %.0f MB · RAM %.0f MB" % [
		counts.near, counts.mid, counts.far, counts.road, queued, stats.todo,
		stats.gen_ms / maxf(stats.gen_n, 1), stats.build_ms / maxf(stats.build_n, 1),
		stats.ero[0], stats.ero[2], stats.ero[1], stats.ero[3], stats.lag / 1000.0, stats.lag_max / 1000.0, stats.rss,
		Performance.get_monitor(Performance.TIME_FPS), Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME) / 1e6,
		Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1e6, Performance.get_monitor(Performance.MEMORY_STATIC) / 1e6]
