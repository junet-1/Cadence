extends Node3D
## Fahrer auf dem Rennrad (Spieler und Mitfahrer). Modell aus models/rider.glb (blender/rider.py), ohne die Datei einfache
## Grundformen. Blick nach −Z, Räder auf y = 0, Rad mittig bei x = 0. Beine folgen den Pedalen per 2-Knochen-IK
## (Hüfte fest, Knie nach vorn geknickt), Fuß mit leichter Fersenarbeit; Oberkörper ruhig (lange Fahrten, kein Wippen).
##   var r := preload("res://rider.gd").new(); add_child(r); r.setup(Color(1, 0.45, 0.1), "Ich")
##   jedes Bild: r.pedal(cadence, speed, delta)
## Trikotfarbe: Vertexfarben mit Alpha 1 werden beim setup mit der Kit-Farbe multipliziert (eigenes Mesh je Fahrer).

# Maße wie in blender/rider.py (dort Blender-Achsen: x, y = vorn, z = oben -> hier x, z, −y). Beide zusammen ändern.
const WHEEL_R := 0.335
const HUB_F := Vector3(0, 0.335, -0.595)
const HUB_R := Vector3(0, 0.335, 0.405)
const BB := Vector3(0, 0.265, 0)
const CRANK := 0.1725
const PEDAL_X := 0.115
const HIP := Vector3(0.085, 1.035, 0.165)  # rechts, links gespiegelt
const THIGH := 0.445
const SHIN := 0.445
const ANKLE := Vector3(0, 0.085, 0.07)  # Knöchel relativ zur Pedalachse im Fuß-Raum

var crank := 0.0  # Kurbelwinkel rechts, 0 = Pedal oben
var parts := {}  # frame, body, wheel_f, wheel_r, crank, thigh_r/l, shin_r/l, foot_r/l -> Node3D (Drehpunkt)
var _base := {}  # Teilname -> Mesh-Arrays aus dem glb (Vertexfarben ungefärbt)
var _kit_mats: Array[StandardMaterial3D] = []  # nur Ersatzmodell: Materialien in Trikotfarbe


func setup(color: Color, label: String) -> void:
	if parts.is_empty():
		_build()
	if _base.is_empty():
		for m in _kit_mats:
			m.albedo_color = color
	else:
		_recolor(color)
	get_node("Tag").text = label
	pedal(0.0, 0.0, 0.0)


func pedal(rpm: float, speed: float, delta: float) -> void:
	## rpm: Trittfrequenz (0 = Beine stehen), speed: m/s für die Raddrehung, delta: Bildzeit
	crank = fposmod(crank + rpm / 60.0 * TAU * delta, TAU)
	parts.crank.rotation.x = -crank
	var spin := fposmod(parts.wheel_r.rotation.x - speed / WHEEL_R * delta, TAU)
	parts.wheel_r.rotation.x = spin
	parts.wheel_f.rotation.x = spin
	_leg("r", 1.0, crank)
	_leg("l", -1.0, crank + PI)


func _leg(side: String, sx: float, a: float) -> void:
	var foot: Node3D = parts["foot_" + side]
	foot.position = BB + Vector3(sx * PEDAL_X, cos(a) * CRANK, -sin(a) * CRANK)
	foot.rotation.x = -deg_to_rad(17.0 - 12.0 * sin(a))  # Zehen im Druck flacher, im Zug tiefer
	var ankle := foot.position + foot.basis * ANKLE
	var hip := Vector3(sx * HIP.x, HIP.y, HIP.z)
	var knee := knee_at(hip, ankle, Vector3(sx * 0.15, 0, -1))
	_bone(parts["thigh_" + side], hip, knee)
	_bone(parts["shin_" + side], knee, ankle)


static func knee_at(top: Vector3, end: Vector3, pole: Vector3) -> Vector3:
	## Mittelgelenk zwischen top (Hüfte) und end (Knöchel), zur Seite pole geknickt (wie blender/rider.py::ik)
	var d := end - top
	var l := clampf(d.length(), absf(THIGH - SHIN) + 0.001, THIGH + SHIN - 0.001)
	var u := d.normalized()
	var x := (THIGH * THIGH - SHIN * SHIN + l * l) / (2.0 * l)
	return top + u * x + (pole - u * pole.dot(u)).normalized() * sqrt(maxf(THIGH * THIGH - x * x, 0.0))


func _bone(node: Node3D, top: Vector3, end: Vector3) -> void:
	# Knochen-Mesh hängt vom Ursprung nach −Y, Vorderseite −Z; x bleibt quer zum Rad
	var y := (top - end).normalized()
	var z := Vector3.RIGHT.cross(y).normalized()
	node.basis = Basis(y.cross(z), y, z)
	node.position = top


func _build(glb := true) -> void:
	var at := {"frame": Vector3.ZERO, "body": Vector3.ZERO, "wheel_f": HUB_F, "wheel_r": HUB_R, "crank": BB}
	for side in ["r", "l"]:
		for p in ["thigh", "shin", "foot"]:
			at[p + "_" + side] = Vector3.ZERO
	for n: String in at:
		var pivot := Node3D.new()
		pivot.name = n
		pivot.position = at[n]
		add_child(pivot)
		parts[n] = pivot
	if not (glb and _load_glb()):
		_fallback()
	var tag := Label3D.new()
	tag.name = "Tag"
	tag.position.y = 1.95  # ~0,3 m über dem Helm
	tag.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	tag.font_size = 64
	tag.outline_size = 14
	tag.outline_modulate = Color(0, 0, 0, 0.75)
	add_child(tag)


func _load_glb() -> bool:
	# wie main.gd::tree_meshes: zur Laufzeit lesen, ohne Import-LODs
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(ProjectSettings.globalize_path(preload("res://main.gd").data("res://models/rider.glb")), state) != OK:
		return false
	var scene := doc.generate_scene(state)
	var mat := StandardMaterial3D.new()
	mat.vertex_color_use_as_albedo = true
	mat.roughness = 0.8  # Stoff, Carbon matt: kein Glanzfleck auf der Hose
	for mi: MeshInstance3D in scene.find_children("*", "MeshInstance3D", true, false):
		var n := str(mi.name)
		if parts.has(n):
			_base[n] = mi.mesh.surface_get_arrays(0)
			var inst := MeshInstance3D.new()
			inst.material_override = mat
			parts[n].add_child(inst)
	scene.free()
	if _base.size() != parts.size():  # unvollständige Datei: lieber Ersatzmodell
		for n: String in parts:
			for c in parts[n].get_children():
				c.free()
		_base.clear()
		return false
	return true


func _recolor(color: Color) -> void:
	var k := color.srgb_to_linear()
	for n: String in _base:
		var arr: Array = _base[n].duplicate()
		var cols: PackedColorArray = arr[Mesh.ARRAY_COLOR].duplicate()
		for i in cols.size():
			var c := cols[i]
			cols[i] = Color(c.r * k.r, c.g * k.g, c.b * k.b) if c.a > 0.5 else Color(c.r, c.g, c.b)
		arr[Mesh.ARRAY_COLOR] = cols
		var am := ArrayMesh.new()
		am.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		(parts[n].get_child(0) as MeshInstance3D).mesh = am


func _fallback() -> void:
	# ponytail: Grundformen ohne rider.glb (nur damit nichts bricht); Detail kommt aus blender/rider.py
	var dark := Color(0.08, 0.08, 0.09)
	var skin := Color(0.8, 0.6, 0.48)
	for w in ["wheel_f", "wheel_r"]:
		var t := TorusMesh.new()
		t.inner_radius = WHEEL_R - 0.03
		t.outer_radius = WHEEL_R
		_prim(w, t, Vector3.ZERO, Vector3(0, 0, 90), dark)
	_prim("frame", _box(0.04, 0.04, 0.55), Vector3(0, 0.81, 0.11), Vector3(-2, 0, 0), dark)  # Oberrohr
	_prim("frame", _box(0.045, 0.045, 0.62), Vector3(0, 0.49, -0.21), Vector3(-48, 0, 0), dark)  # Unterrohr
	_prim("frame", _box(0.035, 0.68, 0.035), Vector3(0, 0.6, 0.09), Vector3(16.5, 0, 0), dark)  # Sitzrohr
	_prim("frame", _box(0.03, 0.03, 0.45), Vector3(0, 0.3, 0.2), Vector3(-10, 0, 0), dark)  # Kettenstrebe
	_prim("frame", _box(0.03, 0.6, 0.03), Vector3(0, 0.6, -0.5), Vector3(-20, 0, 0), dark)  # Gabel
	_prim("frame", _box(0.42, 0.03, 0.03), Vector3(0, 0.875, -0.5), Vector3.ZERO, dark)  # Lenker
	_prim("frame", _box(0.12, 0.03, 0.26), Vector3(0, 0.94, 0.17), Vector3.ZERO, dark)  # Sattel
	_prim("crank", _box(0.02, CRANK, 0.03), Vector3(0.08, CRANK / 2, 0), Vector3.ZERO, dark)
	_prim("crank", _box(0.02, CRANK, 0.03), Vector3(-0.08, -CRANK / 2, 0), Vector3.ZERO, dark)
	_prim("body", _capsule(0.13, 0.62), Vector3(0, 1.2, -0.04), Vector3(-52, 0, 0), Color.WHITE, true)  # Oberkörper
	_prim("body", _capsule(0.045, 0.6), Vector3(0.18, 1.13, -0.39), Vector3(32, 0, 0), skin)  # Arme
	_prim("body", _capsule(0.045, 0.6), Vector3(-0.18, 1.13, -0.39), Vector3(32, 0, 0), skin)
	_prim("body", _sphere(0.11), Vector3(0, 1.55, -0.38), Vector3.ZERO, Color(0.95, 0.95, 0.95))  # Helm
	for side in ["r", "l"]:
		_prim("thigh_" + side, _capsule(0.07, THIGH + 0.14), Vector3(0, -THIGH / 2, 0), Vector3.ZERO, dark)
		_prim("shin_" + side, _capsule(0.05, SHIN + 0.1), Vector3(0, -SHIN / 2, 0), Vector3.ZERO, skin)
		_prim("foot_" + side, _box(0.08, 0.07, 0.26), Vector3(0, 0.04, 0.02), Vector3.ZERO, dark)


func _prim(part: String, mesh: Mesh, pos: Vector3, rot: Vector3, color: Color, kit := false) -> void:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.position = pos
	mi.rotation_degrees = rot
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	mi.material_override = m
	if kit:
		_kit_mats.append(m)
	parts[part].add_child(mi)


static func _box(x: float, y: float, z: float) -> BoxMesh:
	var m := BoxMesh.new()
	m.size = Vector3(x, y, z)
	return m


static func _capsule(r: float, h: float) -> CapsuleMesh:
	var m := CapsuleMesh.new()
	m.radius = r
	m.height = h
	return m


static func _sphere(r: float) -> SphereMesh:
	var m := SphereMesh.new()
	m.radius = r
	m.height = 2 * r
	return m
