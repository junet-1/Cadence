extends Node
## Entwicklermenü (F1): Freiflug-Kamera + Infopanel. Nur Ansicht: Fahrer, Physik und Trainer laufen weiter wie sonst.
## Im Flug folgen Weltgenerator und Gras der Kamera (nächster Straßenpunkt), damit man der Generierung zusehen kann.
##   WASD bewegen · E/Q hoch/runter · Shift ×5 · rechte Maustaste halten = umsehen · Mausrad = Tempo
##   T = Fahrer hierher (nächster Straßenpunkt) · H = HUD an/aus · [ ] = Uhrzeit −/+ 1 h · F1 = zurück

const UI := preload("res://ui_theme.gd")

var m: Node  # main.gd
var on := false
var cam: Camera3D
var prev: Camera3D
var speed := 40.0  # m/s
var yaw := 0.0
var pitch := 0.0
var follow_s := 0.0
var follow_t := 0.0
var panel: PanelContainer
var info: Label
var info_t := 0.0


func setup(main: Node) -> void:
	m = main
	panel = PanelContainer.new()
	panel.theme = UI.theme()
	panel.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	panel.offset_left = 20
	panel.offset_top = -110  # wächst nach oben, endet über der Workout-Leiste
	panel.offset_bottom = -110
	panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	panel.visible = false
	var v := VBoxContainer.new()
	panel.add_child(v)
	v.add_child(UI.label("DEV · FREIFLUG", 14, UI.ACCENT, 700))
	info = UI.label("", 15, UI.TEXT)
	v.add_child(info)
	v.add_child(UI.label("WASD fliegen · E/Q hoch/runter · Shift ×5 · rechte Maus umsehen\nMausrad Tempo · T Fahrer hierher · H HUD · [ ] Uhrzeit · F1 zurück", 13, UI.MUTED))
	m.ui_layer.add_child(panel)


func toggle() -> void:
	on = not on
	panel.visible = on
	if on:
		prev = m.get_viewport().get_camera_3d()
		cam = Camera3D.new()
		cam.far = 20000.0
		m.add_child(cam)
		cam.global_transform = prev.global_transform
		var e := cam.global_basis.get_euler()
		pitch = e.x
		yaw = e.y
		cam.make_current()
		follow_s = m.dist
	else:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		if prev:
			prev.make_current()
		cam.queue_free()
		cam = null
		m.ui.visible = true


func stream_s(dist: float) -> float:
	## Strecke, um die der Generator Kacheln baut: im Flug der Straßenpunkt unter der Kamera
	return follow_s if on else dist


func view_pos(rider: Vector3) -> Vector3:
	return cam.global_position if on else rider


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_F1:
		toggle()
		get_viewport().set_input_as_handled()
		return
	if not on:
		return
	if event is InputEventKey:
		if event.keycode in [KEY_W, KEY_A, KEY_S, KEY_D, KEY_Q, KEY_E, KEY_SHIFT, KEY_T, KEY_H, KEY_BRACKETLEFT, KEY_BRACKETRIGHT]:
			get_viewport().set_input_as_handled()  # sonst schaltet W auch das Wetter um
		if event.pressed and not event.echo:
			match event.keycode:
				KEY_T:
					m.dist = follow_s
					m.speed = 0.0
				KEY_H:
					m.ui.visible = not m.ui.visible
				KEY_BRACKETLEFT, KEY_BRACKETRIGHT:
					var h: float = m.cfg.get_value("world", "time_shift_hours", 0.0)
					m.cfg.set_value("world", "time_shift_hours", h + (1.0 if event.keycode == KEY_BRACKETRIGHT else -1.0))
	elif event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT:
			Input.mouse_mode = Input.MOUSE_MODE_CAPTURED if event.pressed else Input.MOUSE_MODE_VISIBLE
		elif event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
			speed = minf(speed * 1.25, 2000.0)
		elif event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			speed = maxf(speed / 1.25, 2.0)
	elif event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		yaw -= event.relative.x * 0.003
		pitch = clampf(pitch - event.relative.y * 0.003, -1.55, 1.55)


func _process(delta: float) -> void:
	if not on:
		return
	cam.basis = Basis.from_euler(Vector3(pitch, yaw, 0.0))
	var d := Vector3.ZERO
	d += -cam.basis.z * (float(Input.is_key_pressed(KEY_W)) - float(Input.is_key_pressed(KEY_S)))
	d += cam.basis.x * (float(Input.is_key_pressed(KEY_D)) - float(Input.is_key_pressed(KEY_A)))
	d += Vector3.UP * (float(Input.is_key_pressed(KEY_E)) - float(Input.is_key_pressed(KEY_Q)))
	cam.position += d.normalized() * speed * (5.0 if Input.is_key_pressed(KEY_SHIFT) else 1.0) * delta
	follow_t -= delta
	if follow_t <= 0.0:
		follow_t = 0.25
		follow_s = m.curve.get_closest_offset(cam.position)
	info_t -= delta
	if info_t <= 0.0:
		info_t = 0.2
		var road: Vector3 = m.curve.sample_baked(follow_s)
		var lines := ["Tempo %d m/s · Höhe %d m ü. M. · %d m über der Straße" % [roundi(speed), roundi(cam.position.y + m.world_meta.get("elev0", 0)), roundi(cam.position.y - road.y)],
			"Straße unter dir: km %.2f · Abstand %d m · Fahrer bei km %.2f" % [follow_s / 1000.0, roundi(Vector2(cam.position.x - road.x, cam.position.z - road.z).length()), m.dist / 1000.0],
			"Uhrzeit-Versatz %+d h · %d FPS" % [roundi(m.cfg.get_value("world", "time_shift_hours", 0.0)), Engine.get_frames_per_second()]]
		if m.world:
			lines.append(m.world.diagnostics())
		info.text = "\n".join(lines)
