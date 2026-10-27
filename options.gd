extends Control
## Einstellungen (Startbildschirm und Pausemenü): Anzeige, Grafik, Ton. Änderungen gelten erst mit „Übernehmen“ – dann
## sofort (Gras ab der nächsten Fahrt) und gespeichert in user://start.cfg; „Zurück“ verwirft sie. Danach zeigt die Statuszeile,
## was das Fenster wirklich übernommen hat. Außerdem ride_pickers(): Wetter und Tageszeit für die nächste Fahrt.

signal closed

const SAVE := "user://start.cfg"
const UI := preload("res://ui_theme.gd")
const WINDOW := [["Fenster", DisplayServer.WINDOW_MODE_WINDOWED], ["Vollbild (randlos)", DisplayServer.WINDOW_MODE_FULLSCREEN],
	["Exklusiv-Vollbild", DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN]]
const SIZES := [Vector2i(1280, 720), Vector2i(1600, 900), Vector2i(1920, 1080), Vector2i(2560, 1440), Vector2i(3840, 2160)]
const SCALES := [1.0, 0.85, 0.75, 0.67, 0.5]  # 3D-Renderauflösung (HUD bleibt scharf)
const FPS := [0, 30, 60, 120, 144, -1]  # 0 = VSync, -1 = unbegrenzt
const GRASS := [["Aus", 0], ["Niedrig", 120], ["Mittel", 220], ["Hoch", 300]]  # Gras-Raster um den Fahrer, ändert die Welt nicht
const WEATHER := ["Echtes Wetter (Auto)", "Sonnig", "Wolkig", "Regen", "Sturm"]  # = weather.gd PRESETS
const TIMES := [["Echte Uhrzeit", -1.0], ["Morgen", 8.0], ["Mittag", 12.0], ["Nachmittag", 15.5], ["Abendsonne", -2.0],
	["Nacht", 0.0]]  # Sonnenzeit am Ort der Strecke; -2 = Sonne ~6° über dem Horizont (main.gd::_set_clock)

var saved := ConfigFile.new()
var ctl := {}  # Schlüssel in [video] -> Bedienelement
var sizes: Array = []  # Fenstergrößen, die auf den Monitor passen (ZERO = maximiert)
var status: Label


static func load_cfg() -> ConfigFile:
	var c := ConfigFile.new()
	c.load(SAVE)
	return c


static func store(section: String, values: Dictionary) -> void:
	## Frisch laden, setzen, speichern: mehrere Bildschirme schreiben dieselbe Datei, keiner überschreibt die Werte der anderen
	var c := load_cfg()
	for k in values:
		c.set_value(section, k, values[k])
	c.save(SAVE)


static func apply(c: ConfigFile) -> void:
	## Anzeige + Ton sofort; Schatten/SSAO an die laufende Fahrt (main.gd::apply_settings). Ohne [video] bleibt project.godot.
	AudioServer.set_bus_volume_db(0, linear_to_db(c.get_value("video", "volume", 1.0)))
	var tree := Engine.get_main_loop() as SceneTree
	if tree.current_scene and tree.current_scene.has_method("apply_settings"):
		tree.current_scene.apply_settings(c)
	if not c.has_section("video") or OS.get_environment("MCP_BACKGROUND") == "1":  # MCP-Hintergrundtest: Fenster bleibt versteckt
		return
	var mode: int = c.get_value("video", "mode", DisplayServer.WINDOW_MODE_WINDOWED)
	var win: Vector2i = c.get_value("video", "size", Vector2i.ZERO)  # ZERO = maximiert
	var fps: int = c.get_value("video", "fps", 0)
	# Godot schaltet den Monitor nicht um: Vollbild = Monitorauflösung, schneller wird es über die 3D-Renderauflösung
	tree.root.scaling_3d_scale = c.get_value("video", "scale", 1.0)
	if mode != DisplayServer.WINDOW_MODE_WINDOWED:
		DisplayServer.window_set_mode(mode)
	elif win == Vector2i.ZERO:
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MAXIMIZED)
	else:
		DisplayServer.window_set_mode(mode)
		DisplayServer.window_set_size(win)
		DisplayServer.window_set_position(DisplayServer.screen_get_position() + (DisplayServer.screen_get_size() - win) / 2)
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_ENABLED if fps == 0 else DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = maxi(fps, 0)


static func ride_pickers(grid: GridContainer) -> void:
	## Wetter + Tageszeit (je Label + Auswahl, zwei Spalten). Wirkt über Engine-Meta "ride" auf die nächste Fahrt, gemerkt in [ride].
	var c := load_cfg()
	var pick := {}
	var publish := func() -> void:
		var r := {"weather": pick.weather.selected, "time": TIMES[pick.time.selected][1]}
		Engine.set_meta("ride", r)
		store("ride", {"weather": r.weather, "time": pick.time.selected})
	for k in [["weather", "Wetter", WEATHER], ["time", "Tageszeit", TIMES.map(func(t): return t[0])]]:
		var l := UI.label(k[1], 18, UI.MUTED)
		l.size_flags_vertical = SIZE_SHRINK_CENTER
		grid.add_child(l)
		var o := OptionButton.new()
		for it in k[2]:
			o.add_item(it)
		o.select(clampi(c.get_value("ride", k[0], 0), 0, k[2].size() - 1))
		o.size_flags_horizontal = SIZE_EXPAND_FILL
		o.item_selected.connect(func(_i: int) -> void: publish.call())
		grid.add_child(o)
		pick[k[0]] = o
	pick.weather.tooltip_text = "Auto: echtes, wechselndes Wetter (gleich für beide Fahrer). Unterwegs schaltet Taste W um."
	pick.time.tooltip_text = "Sonnenstand am Ort der Strecke; die Uhr läuft ab da weiter. Abendsonne richtet sich nach der Jahreszeit."
	Engine.set_meta("ride", {"weather": pick.weather.selected, "time": TIMES[pick.time.selected][1]})


func _ready() -> void:
	set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	mouse_filter = MOUSE_FILTER_STOP
	saved = load_cfg()
	var dim := ColorRect.new()
	dim.color = Color(0, 0, 0, 0.5)
	dim.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	add_child(dim)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	add_child(center)
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", UI.box(UI.PANEL_SOLID, 18, 28, UI.LINE))
	center.add_child(p)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 12)
	v.custom_minimum_size.x = 560
	p.add_child(v)
	v.add_child(UI.label("Einstellungen", 34, UI.TEXT, 700))

	var screen := DisplayServer.screen_get_size()
	sizes = [Vector2i.ZERO] + SIZES.filter(func(r: Vector2i) -> bool: return r.x <= screen.x and r.y <= screen.y and r != screen)
	var hz := roundi(DisplayServer.screen_get_refresh_rate())
	var g := _section(v, "ANZEIGE")
	_option(g, "mode", "Modus", WINDOW.map(func(w): return w[0]), WINDOW.map(func(w): return w[1]).find(saved.get_value("video", "mode", 0)))
	_option(g, "size", "Fenstergröße", sizes.map(func(r: Vector2i) -> String:
		return "Maximiert" if r == Vector2i.ZERO else "%d × %d" % [r.x, r.y]), sizes.find(saved.get_value("video", "size", Vector2i.ZERO)))
	_option(g, "scale", "3D-Auflösung", SCALES.map(func(s: float) -> String: return "%d %%" % roundi(s * 100.0)),
		SCALES.find(saved.get_value("video", "scale", 1.0)))
	ctl.scale.tooltip_text = "Anteil der Bildschirmpixel, mit dem die 3D-Welt gerechnet wird. Weniger = schneller, HUD bleibt scharf.\nDer Monitor selbst läuft immer in seiner Auflösung."
	_option(g, "fps", "Bildrate", FPS.map(func(f: int) -> String:
		return ("VSync (%d Hz)" % hz if hz > 0 else "VSync") if f == 0 else "Unbegrenzt" if f < 0 else "max. %d FPS" % f),
		FPS.find(saved.get_value("video", "fps", 0)))
	ctl.mode.item_selected.connect(func(_i: int) -> void: _sync())

	g = _section(v, "GRAFIK")
	_option(g, "grass", "Gras", GRASS.map(func(x): return x[0]), GRASS.map(func(x): return x[1]).find(saved.get_value("video", "grass", 220)))
	ctl.grass.tooltip_text = "Gras-Büschel um den Fahrer. Gilt ab der nächsten Fahrt."
	_check(g, "shadows", "Schatten", saved.get_value("video", "shadows", true))
	_check(g, "ssao", "Umgebungsverdeckung (SSAO)", saved.get_value("video", "ssao", true))

	g = _section(v, "TON")
	g.add_child(UI.label("Lautstärke", 18, UI.MUTED))
	var vol := HSlider.new()
	vol.max_value = 1.0
	vol.step = 0.05
	vol.value = saved.get_value("video", "volume", 1.0)
	vol.custom_minimum_size = Vector2(240, 24)
	vol.size_flags_horizontal = SIZE_EXPAND_FILL
	g.add_child(vol)
	ctl.volume = vol

	status = UI.label("", 15, UI.MUTED)
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(status)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	row.alignment = BoxContainer.ALIGNMENT_END
	v.add_child(row)
	var back := Button.new()
	back.text = "Zurück"
	back.custom_minimum_size = Vector2(140, 48)
	back.pressed.connect(_close)
	row.add_child(back)
	var ok := UI.primary(Button.new(), 20)
	ok.text = "Übernehmen"
	ok.custom_minimum_size = Vector2(180, 48)
	ok.pressed.connect(_apply)
	row.add_child(ok)
	_sync()
	ok.grab_focus()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		accept_event()
		_close()


func _close() -> void:
	closed.emit()
	queue_free()


func values() -> Dictionary:
	## Stand der Bedienelemente als [video]-Werte
	return {"mode": WINDOW[ctl.mode.selected][1], "size": sizes[ctl.size.selected], "scale": SCALES[ctl.scale.selected],
		"fps": FPS[ctl.fps.selected], "grass": GRASS[ctl.grass.selected][1], "shadows": ctl.shadows.button_pressed,
		"ssao": ctl.ssao.button_pressed, "volume": ctl.volume.value}


func _apply() -> void:
	store("video", values())
	apply(load_cfg())
	status.text = "Wird übernommen …"
	for i in 3:  # der Fenstermanager braucht ein paar Frames
		await get_tree().process_frame
	var win := DisplayServer.window_get_size()
	var mode: int = DisplayServer.window_get_mode()
	var names := {DisplayServer.WINDOW_MODE_WINDOWED: "Fenster", DisplayServer.WINDOW_MODE_MAXIMIZED: "Fenster (maximiert)",
		DisplayServer.WINDOW_MODE_FULLSCREEN: "Vollbild", DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN: "Exklusiv-Vollbild"}
	var s := get_tree().root.scaling_3d_scale
	status.text = "Übernommen: %s %d × %d · 3D %d × %d · %s" % [names.get(mode, "?"), win.x, win.y, roundi(win.x * s), roundi(win.y * s),
		ctl.fps.get_item_text(ctl.fps.selected)]
	status.add_theme_color_override("font_color", UI.GOOD)


func _sync() -> void:
	## Fenstergröße gibt es nur im Fenstermodus
	ctl.size.disabled = ctl.mode.selected != 0
	ctl.size.tooltip_text = "Im Vollbild gilt die Monitorauflösung – schneller wird es über die 3D-Auflösung." if ctl.size.disabled else ""


func _section(parent: Control, title: String) -> GridContainer:
	parent.add_child(UI.label(title, 14, UI.MUTED, 600))
	var g := GridContainer.new()
	g.columns = 2
	g.add_theme_constant_override("h_separation", 18)
	g.add_theme_constant_override("v_separation", 8)
	parent.add_child(g)
	return g


func _option(g: GridContainer, key: String, text: String, items: Array, sel: int) -> void:
	var l := UI.label(text, 18, UI.MUTED)
	l.size_flags_vertical = SIZE_SHRINK_CENTER
	g.add_child(l)
	var o := OptionButton.new()
	for it in items:
		o.add_item(it)
	o.select(clampi(sel, 0, items.size() - 1))
	o.size_flags_horizontal = SIZE_EXPAND_FILL
	g.add_child(o)
	ctl[key] = o


func _check(g: GridContainer, key: String, text: String, on: bool) -> void:
	var l := UI.label(text, 18, UI.MUTED)
	l.size_flags_vertical = SIZE_SHRINK_CENTER
	g.add_child(l)
	var c := CheckButton.new()
	c.button_pressed = on
	g.add_child(c)
	ctl[key] = c
