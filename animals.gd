extends Node3D
## Wildtiere (Workout-Welt): Rehe in kleinen Gruppen und Hasen um den Fahrer, rein optisch. Jedes Tier ist ein kleiner
## Zustandsautomat: äsen (Kopf unten) -> ein paar Meter weiterziehen -> äsen … Kommt der Fahrer (oder im Freiflug die Kamera)
## näher, sichert es (Kopf hoch, schaut her) und flüchtet dann weg von ihm; ein flüchtendes Reh reißt die Gruppe mit.
## Boden: Höhe und Wuchscode aus den Gras-Karten der feinen Kacheln (main.ground_at). Nicht deterministisch, nur Kulisse.

enum { GRAZE, WALK, ALERT, FLEE }
const MAX := {"reh": 12, "hase": 14}  # im Wald mehr Rehe: größere Rudel (_spawn)
const OK_GROUND := {"reh": [200, 230, 255], "hase": [230, 255, 200]}  # Wald/trocken, Feld, Wiese (nie Ort, Wasser, kahl)

var main: Node3D
var meshes := {}
var mat: ShaderMaterial
var list: Array = []  # {kind, node, parts, state, t, dir, speed, target, phase, head}
var spawn_t := 0.0


func _ready() -> void:
	meshes = main.tree_meshes("res://models/animals.glb")
	mat = main._shader("props")


func _make(kind: String, pos: Vector3) -> Dictionary:
	var root := Node3D.new()
	add_child(root)
	root.global_position = pos
	root.rotation.y = randf() * TAU
	var a := {"kind": kind, "node": root, "state": GRAZE, "t": randf_range(2, 7), "dir": Vector3.ZERO, "speed": 0.0,
		"target": pos, "phase": randf() * TAU, "head": null, "legs": []}
	var part := func(mesh: String, at: Vector3) -> MeshInstance3D:
		var mi := MeshInstance3D.new()
		mi.mesh = meshes.get(mesh)
		mi.material_override = mat
		mi.position = at
		root.add_child(mi)
		return mi
	var s := randf_range(0.9, 1.1)
	root.scale = Vector3.ONE * s
	if kind == "reh":
		part.call("reh_rumpf", Vector3.ZERO)
		a.head = part.call("reh_kopf", Vector3(0, 0.86, -0.38))
		for p in [Vector3(-0.09, 0.64, -0.32), Vector3(0.09, 0.64, -0.32), Vector3(-0.09, 0.64, 0.33), Vector3(0.09, 0.64, 0.33)]:
			a.legs.append(part.call("reh_bein", p))
	else:
		a.body = part.call("hase", Vector3.ZERO)
	list.append(a)
	return a


func _process(delta: float) -> void:
	if main.rider == null or meshes.is_empty():
		return
	var rider: Vector3 = main.rider.global_position
	var threats := [rider]
	if main.dev.on:
		threats.append(main.dev.cam.global_position)
	var fwd: Vector3 = -main.rider.global_basis.z
	spawn_t -= delta
	if spawn_t <= 0.0:
		spawn_t = 0.25
		_spawn()
	for a in list.duplicate():
		var p: Vector3 = a.node.global_position
		var center: Vector3 = main.dev.view_pos(rider)
		if p.distance_to(center) > 450.0 or (not main.dev.on and (p - rider).dot(fwd) < -90.0):
			a.node.queue_free()
			list.erase(a)
			continue
		_think(a, threats, delta)
		_move(a, delta)


func _spawn() -> void:
	## Vor dem Fahrer (bzw. unter der Flugkamera) neben der Straße, nur auf passendem Boden; Rehe in Gruppen von 1–3
	var count := {"reh": 0, "hase": 0}
	for a in list:
		count[a.kind] += 1
	var kind := "reh" if count.reh < MAX.reh and randf() < 0.5 else "hase"
	if count[kind] >= MAX[kind]:
		return
	var s: float = main.dev.stream_s(main.dist) + (randf_range(120, 380) if kind == "reh" else randf_range(50, 220))
	var c: Vector3 = main.curve.sample_baked(fposmod(s, main.route_len))
	var t: Vector3 = main.curve.sample_baked(fposmod(s + 5.0, main.route_len)) - c
	var lat := Vector3(t.z, 0, -t.x).normalized() * (1.0 if randf() < 0.5 else -1.0)
	var pos := c + lat * (randf_range(25, 160) if kind == "reh" else randf_range(10, 60))
	var g: Vector2 = main.ground_at(pos.x, pos.z)
	if is_nan(g.x) or int(g.y) not in OK_GROUND[kind]:
		return
	for k in ((randi_range(2, 4) if int(g.y) == 200 else randi_range(1, 3)) if kind == "reh" else (randi_range(1, 2) if int(g.y) == 200 else 1)):
		var q := pos + Vector3(randf_range(-6, 6), 0, randf_range(-6, 6))
		var gq: Vector2 = main.ground_at(q.x, q.z)
		if not is_nan(gq.x) and count[kind] + k < MAX[kind]:
			_make(kind, Vector3(q.x, gq.x, q.z))


func _think(a: Dictionary, threats: Array, delta: float) -> void:
	var p: Vector3 = a.node.global_position
	var near: Vector3 = threats[0]
	for th: Vector3 in threats:
		if th.distance_to(p) < near.distance_to(p):
			near = th
	var d := Vector2(p.x - near.x, p.z - near.z).length()
	var deer: bool = a.kind == "reh"
	var fast := clampf(main.speed / 8.0, 0.0, 1.0)  # schnelles Rad fällt mehr auf
	var alert_r := (55.0 if deer else 20.0) * (0.6 + 0.4 * fast)
	a.t -= delta
	match a.state:
		GRAZE, WALK:
			if d < alert_r * 0.5:
				_flee(a, near)
			elif d < alert_r:
				a.state = ALERT
				a.t = randf_range(0.5, 1.5) if deer else randf_range(0.2, 0.6)
				a.speed = 0.0
				a.dir = Vector3(near.x - p.x, 0, near.z - p.z).normalized()  # Blick zum Störenfried
			elif a.state == GRAZE and a.t <= 0.0:
				a.state = WALK
				a.target = p + Vector3(randf_range(-15, 15), 0, randf_range(-15, 15))
				a.t = 12.0
			elif a.state == WALK:
				var to: Vector3 = a.target - p
				to.y = 0
				if to.length() < 0.8 or a.t <= 0.0:
					a.state = GRAZE
					a.t = randf_range(3, 9)
					a.speed = 0.0
				else:
					a.dir = to.normalized()
					a.speed = 0.9 if deer else 1.1
		ALERT:
			if a.t <= 0.0 or d < alert_r * 0.4:
				_flee(a, near)
		FLEE:
			if a.t <= 0.0 and d > (100.0 if deer else 40.0):
				a.state = WALK
				a.target = p + a.dir * 6.0
				a.t = 6.0
			elif not deer and randf() < delta * 1.5:  # Hase schlägt Haken
				a.dir = a.dir.rotated(Vector3.UP, randf_range(-0.9, 0.9))


func _flee(a: Dictionary, from: Vector3) -> void:
	var p: Vector3 = a.node.global_position
	var away := Vector3(p.x - from.x, 0, p.z - from.z).normalized().rotated(Vector3.UP, randf_range(-0.5, 0.5))
	a.state = FLEE
	a.dir = away
	a.speed = randf_range(9, 12) if a.kind == "reh" else randf_range(6, 8)
	a.t = randf_range(3, 6)
	if a.kind == "reh":  # die Gruppe flüchtet mit
		for b in list:
			if b != a and b.kind == "reh" and b.state != FLEE and b.node.global_position.distance_to(p) < 40.0:
				b.state = ALERT
				b.t = randf_range(0.1, 0.4)


func _move(a: Dictionary, delta: float) -> void:
	var root: Node3D = a.node
	var p := root.global_position
	if a.speed > 0.0:
		var nxt: Vector3 = p + a.dir * a.speed * delta
		var g: Vector2 = main.ground_at(nxt.x, nxt.z)
		if is_nan(g.x) or int(g.y) == 128:  # Kartenrand oder Wasser: umdrehen
			a.dir = -a.dir
			a.target = p
		else:
			p = Vector3(nxt.x, g.x, nxt.z)
	else:
		var g: Vector2 = main.ground_at(p.x, p.z)
		if not is_nan(g.x):
			p.y = g.x
	if a.dir != Vector3.ZERO:
		root.rotation.y = lerp_angle(root.rotation.y, atan2(-a.dir.x, -a.dir.z), 1.0 - exp(-delta * (8.0 if a.state == FLEE else 3.0)))
	var fleeing: bool = a.state == FLEE
	a.phase += delta * (a.speed * (1.6 if a.kind == "reh" else 2.6) + (0.0 if a.speed > 0.0 else 0.0))
	var bob := 0.0
	if a.kind == "reh":
		var amp := 0.9 if fleeing else 0.35 * float(a.speed > 0.0)
		for i in 4:  # Schritt: diagonale Paare; Flucht: Sprünge (vorne zusammen, hinten zusammen)
			var off: float = ([0.0, PI, PI, 0.0] if not fleeing else [0.0, 0.3, PI * 0.7, PI])[i]
			a.legs[i].rotation.x = sin(a.phase + off) * amp
		bob = absf(sin(a.phase)) * 0.28 if fleeing else 0.0
		var head_x := -1.15 if a.state == GRAZE else (0.25 if a.state == ALERT else -0.2)
		a.head.rotation.x = lerpf(a.head.rotation.x, head_x, 1.0 - exp(-delta * 4.0))
		root.rotation.x = -cos(a.phase) * 0.12 if fleeing else 0.0
	else:
		bob = absf(sin(a.phase)) * (0.3 if fleeing else 0.07) * float(a.speed > 0.0)
		a.body.rotation.x = -cos(a.phase) * 0.25 * float(a.speed > 0.0)
		a.body.position.y = 0.04 if a.state == ALERT else 0.0  # Männchen machen: kurz aufrichten
	root.global_position = p + Vector3.UP * bob
