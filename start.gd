extends Control
## Start: Workout wählen (oder importieren) -> Welt wählen -> Losfahren. Die Welt erzeugt world.py aus dem Workout.
## Echte Strecken (OSM-Karte, route.py) gibt es weiter über „Echte Strecke auf der Karte …“ (picker.tscn).

const BIOMES := [["reise", "Reise (Biome wechseln unterwegs)"], ["alpen", "Alpen"], ["wald", "Mischwald"], ["mittelmeer", "Mittelmeer"], ["wueste", "Wüste"]]
const GRAPHICS := [["Niedrig", 120], ["Mittel", 220], ["Hoch", 300]]  # Gras-Raster (kurbel.cfg grass), ändert die Welt nicht
const SAVE := "user://start.cfg"
const UI := preload("res://ui_theme.gd")

var list: ItemList
var info: Label
var bar: Control
var biome: OptionButton
var seed_box: SpinBox
var mode: OptionButton
var ftp: SpinBox
var sliders := {}
var gfx: OptionButton
var workouts: Array = []  # Zusammenfassungen aus workout.py --list, Index 0 = freie Fahrt
var saved := ConfigFile.new()
var recent: VBoxContainer


func _ready() -> void:
	set_anchors_preset(PRESET_FULL_RECT)
	theme = UI.theme()
	saved.load(SAVE)
	var bg := TextureRect.new()  # dunkler Verlauf statt Flachfarbe
	var grad := GradientTexture2D.new()
	grad.gradient = Gradient.new()
	grad.gradient.set_color(0, Color(0.10, 0.13, 0.17))
	grad.gradient.set_color(1, Color(0.05, 0.05, 0.07))
	grad.fill_to = Vector2(0.4, 1.0)
	bg.texture = grad
	bg.stretch_mode = TextureRect.STRETCH_SCALE
	bg.set_anchors_preset(PRESET_FULL_RECT)
	add_child(bg)
	var center := CenterContainer.new()
	center.set_anchors_preset(PRESET_FULL_RECT)
	add_child(center)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 18)
	center.add_child(box)

	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 14)
	box.add_child(head)
	head.add_child(UI.label("KURBEL", 46, UI.ACCENT, 800))
	var sub := UI.label("Jedes Workout baut seine eigene Landschaft: Intervalle werden Pässe, Erholung wird Abfahrt oder Seeufer.", 18, UI.MUTED)
	sub.size_flags_vertical = SIZE_SHRINK_CENTER
	head.add_child(sub)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 18)
	box.add_child(row)
	var lp := PanelContainer.new()
	row.add_child(lp)
	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 10)
	lp.add_child(left)
	left.add_child(UI.label("WORKOUT", 14, UI.MUTED, 600))
	list = ItemList.new()
	list.custom_minimum_size = Vector2(340, 330)
	list.item_selected.connect(_on_pick)
	left.add_child(list)
	var imp := Button.new()
	imp.text = "Workout importieren (.zwo / .erg / .mrc) …"
	imp.pressed.connect(_import)
	left.add_child(imp)
	recent = VBoxContainer.new()
	recent.add_theme_constant_override("separation", 4)
	left.add_child(recent)

	var rp := PanelContainer.new()
	row.add_child(rp)
	var right := VBoxContainer.new()
	right.add_theme_constant_override("separation", 12)
	rp.add_child(right)
	bar = preload("res://workout_bar.gd").new()
	bar.hud = false
	bar.custom_minimum_size = Vector2(600, 130)
	right.add_child(bar)
	info = UI.label("", 18, UI.TEXT)
	info.custom_minimum_size.x = 600
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	right.add_child(info)

	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 18)
	grid.add_theme_constant_override("v_separation", 10)
	right.add_child(grid)
	biome = _option(grid, "Welt", BIOMES.map(func(b): return b[1]), saved.get_value("start", "biome", 0))
	_label(grid, "Seed")
	var seed_row := HBoxContainer.new()
	seed_box = SpinBox.new()
	seed_box.max_value = 999999
	seed_box.value = saved.get_value("start", "seed", 1)
	seed_box.tooltip_text = "Gleicher Seed + gleiches Workout + gleiche Welt = dieselbe Landschaft (auch für den Kollegen)"
	seed_row.add_child(seed_box)
	var dice := Button.new()
	dice.text = "Würfeln"
	dice.pressed.connect(func() -> void: seed_box.value = randi() % 100000)
	seed_row.add_child(dice)
	grid.add_child(seed_row)
	mode = _option(grid, "Trainer", ["ERG (Trainer hält die Zielwatt)", "SIM (Steigung der Welt)"], saved.get_value("start", "mode", 0))
	_label(grid, "FTP (W)")
	ftp = SpinBox.new()
	ftp.min_value = 80
	ftp.max_value = 600
	var cfg := ConfigFile.new()
	cfg.load("res://kurbel.cfg")
	ftp.value = saved.get_value("start", "ftp", cfg.get_value("rider", "ftp", 250))
	grid.add_child(ftp)

	var adv := CheckButton.new()
	adv.text = "Erweitert"
	right.add_child(adv)
	var advgrid := GridContainer.new()
	advgrid.columns = 2
	advgrid.add_theme_constant_override("h_separation", 18)
	advgrid.visible = false
	adv.toggled.connect(func(on: bool) -> void: advgrid.visible = on)
	right.add_child(advgrid)
	for k in [["density", "Landschaftsdichte"], ["drama", "Dramatik des Geländes"], ["variation", "Abwechslung"]]:
		_label(advgrid, k[1])
		var sl := HSlider.new()
		sl.min_value = 0.5
		sl.max_value = 1.5
		sl.step = 0.1
		sl.value = saved.get_value("start", k[0], 1.0)
		sl.custom_minimum_size = Vector2(280, 24)
		advgrid.add_child(sl)
		sliders[k[0]] = sl
	gfx = _option(advgrid, "Grafik (Gras)", GRAPHICS.map(func(g): return g[0]), saved.get_value("start", "gfx", 1))

	var spacer := Control.new()
	spacer.size_flags_vertical = SIZE_EXPAND_FILL
	right.add_child(spacer)
	var go := Button.new()
	go.text = "Losfahren"
	go.custom_minimum_size.y = 64
	go.add_theme_font_size_override("font_size", 28)
	go.add_theme_font_override("font", UI.font(700))
	go.add_theme_stylebox_override("normal", UI.box(UI.ACCENT, 12, 12))
	go.add_theme_stylebox_override("hover", UI.box(UI.ACCENT.lightened(0.15), 12, 12))
	go.add_theme_stylebox_override("pressed", UI.box(UI.ACCENT.darkened(0.15), 12, 12))
	go.add_theme_stylebox_override("focus", UI.box(UI.ACCENT, 12, 12, Color.WHITE))
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color"]:
		go.add_theme_color_override(c, Color.BLACK)
	go.pressed.connect(_go)
	right.add_child(go)
	var osm := Button.new()
	osm.text = "Echte Strecke auf der Karte …"
	osm.pressed.connect(func() -> void: get_tree().change_scene_to_file("res://picker.tscn"))
	right.add_child(osm)
	_load_list(saved.get_value("start", "workout", ""))
	_recent()
	go.grab_focus()


func _recent() -> void:
	## Letzte Fahrten aus user://rides/*.json (ride_log.gd): Datum, Dauer, Ø Leistung, TSS; Klick öffnet den Ordner
	if not DirAccess.dir_exists_absolute("user://rides"):
		return
	var files := Array(DirAccess.get_files_at("user://rides")).filter(func(f: String) -> bool: return f.ends_with(".json"))
	files.sort()
	files.reverse()
	if files.is_empty():
		return
	recent.add_child(UI.label("LETZTE FAHRTEN", 14, UI.MUTED, 600))
	for f: String in files.slice(0, 4):
		var st = JSON.parse_string(FileAccess.get_file_as_string("user://rides/" + f))
		if st is not Dictionary or not st.has("duration"):
			continue
		var b := Button.new()
		b.flat = true
		b.alignment = HORIZONTAL_ALIGNMENT_LEFT
		b.text = "%s   %s · Ø %d W · %d TSS" % [f.left(10), UI.hms(st.duration), roundi(st.power_avg), roundi(st.tss)]
		b.add_theme_font_size_override("font_size", 16)
		b.pressed.connect(func() -> void: OS.shell_open(ProjectSettings.globalize_path("user://rides")))
		recent.add_child(b)


func _label(parent: Control, text: String) -> void:
	var l := UI.label(text, 18, UI.MUTED)
	l.size_flags_vertical = SIZE_SHRINK_CENTER
	parent.add_child(l)


func _option(parent: Control, text: String, items: Array, sel: int) -> OptionButton:
	_label(parent, text)
	var o := OptionButton.new()
	for it in items:
		o.add_item(it)
	o.select(clampi(sel, 0, items.size() - 1))
	parent.add_child(o)
	return o


func _load_list(select_file: String) -> void:
	# Zusammenfassungen von workout.py (einziger Parser, Python): Name, Dauer, Segmente, Phasen
	var out := []
	OS.execute(preload("res://main.gd").uv(), ["run", ProjectSettings.globalize_path("res://workout.py"), "--list", ProjectSettings.globalize_path("res://workouts")], out, true)
	var parsed = JSON.parse_string(out[0] if out else "")
	workouts = [{"name": "Freie Fahrt (ohne Workout)", "file": "", "duration": 0, "segments": [], "phases": []}]
	if parsed is Array:
		workouts += parsed
	else:
		info.text = "workout.py --list fehlgeschlagen:\n" + (out[0] if out else "keine Ausgabe")
	list.clear()
	var sel := 0
	for i in workouts.size():
		var w: Dictionary = workouts[i]
		var mins := roundi(w.duration / 60.0)
		var named: bool = ("%d min" % mins) in w.name or ("%d h" % (mins / 60)) in w.name
		var dur := "  ·  %d min" % mins if w.duration > 0 and not named else ""
		list.add_item(w.name + dur if not w.has("error") else w.name + " (kaputt)")
		if select_file != "" and w.file.get_file() == select_file.get_file():
			sel = i
	list.select(sel)
	_on_pick(sel)


func _on_pick(i: int) -> void:
	var w: Dictionary = workouts[i]
	bar.setup(w)
	if w.has("error"):
		info.text = "Datei nicht lesbar: " + w.error
	elif w.file == "":
		info.text = "Freie Fahrt: eine endlose Strecke mit wechselnden Landschaften, Trainer im SIM-Modus."
	else:
		var names := {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
			"recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei", "set": "Intervall-Satz"}
		info.text = "%s · %d min\nAblauf: %s" % [w.name, roundi(w.duration / 60.0), " → ".join(w.phases.map(func(p): return names.get(p, p)))]


func _import() -> void:
	var fd := FileDialog.new()
	fd.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	fd.access = FileDialog.ACCESS_FILESYSTEM
	fd.filters = PackedStringArray(["*.zwo, *.erg, *.mrc ; Workouts"])
	fd.size = Vector2i(900, 600)
	fd.file_selected.connect(func(path: String) -> void:
		var dst := ProjectSettings.globalize_path("res://workouts/") + path.get_file()
		if DirAccess.copy_absolute(path, dst) == OK:
			_load_list(dst)
		else:
			info.text = "Kopieren fehlgeschlagen: " + path)
	add_child(fd)
	fd.popup_centered()


func _go() -> void:
	var sel := list.get_selected_items()
	var w: Dictionary = workouts[sel[0] if sel else 0]
	if w.has("error"):
		return
	var opts := {"workout": w.file, "biome": BIOMES[biome.selected][0], "seed": int(seed_box.value),
		"mode": "erg" if mode.selected == 0 else "sim", "ftp": ftp.value, "grass": GRAPHICS[gfx.selected][1]}
	for k in sliders:
		opts[k] = snappedf(sliders[k].value, 0.1)
	for k in ["biome", "seed", "mode", "ftp", "gfx", "workout"]:
		saved.set_value("start", k, {"biome": biome.selected, "seed": int(seed_box.value), "mode": mode.selected, "ftp": ftp.value,
			"gfx": gfx.selected, "workout": w.file.get_file()}[k])
	for k in sliders:
		saved.set_value("start", k, sliders[k].value)
	saved.save(SAVE)
	Engine.set_meta("world", opts)
	get_tree().change_scene_to_file("res://main.tscn")
