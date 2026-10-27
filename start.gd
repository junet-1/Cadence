extends Control
## Start: Workout wählen (oder importieren) -> Welt wählen -> Losfahren. Die Welt erzeugt world.py aus dem Workout.
## Echte Strecken (OSM-Karte, route.py) gibt es weiter über „Echte Strecke auf der Karte …“ (picker.tscn).

# Geländeprofil (world.PROFILES): Schlüssel, Anzeige
const PROFILES := [["auto", "Wie das Workout (Intervalle = Rampen)"], ["flach", "Flach"], ["wellig", "Wellig (auf und ab)"],
	["leicht", "Leichter Anstieg (~3 %)"], ["anstieg", "Anstieg (~6–7 %)"], ["panorama", "Panorama: hoch und wieder runter"]]
const BIOMES := [["reise", "Reise (Biome wechseln unterwegs)"], ["alpen", "Alpen"], ["wald", "Mischwald"], ["mittelmeer", "Mittelmeer"], ["wueste", "Wüste"]]
const Options := preload("res://options.gd")  # Einstellungen (Anzeige, Grafik, Ton), Wetter/Tageszeit, user://start.cfg
const UI := preload("res://ui_theme.gd")

var list: ItemList
var info: Label
var bar: Control
var biome: OptionButton
var profile: OptionButton
var seed_box: SpinBox
var mode: OptionButton
var mode_info: Label
var ftp: SpinBox
var sliders := {}
var workouts: Array = []  # Zusammenfassungen aus workout.py --list, Index 0 = freie Fahrt
var saved := ConfigFile.new()
var recent: VBoxContainer


func _ready() -> void:
	set_anchors_preset(PRESET_FULL_RECT)
	theme = UI.theme()
	migrate_user_dir()
	saved = Options.load_cfg()
	Options.apply(saved)
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
	var scroll := ScrollContainer.new()  # niedrige Fenster / „Erweitert“ offen: scrollen statt abschneiden
	scroll.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(scroll)
	var center := CenterContainer.new()
	center.size_flags_horizontal = SIZE_EXPAND_FILL
	center.size_flags_vertical = SIZE_EXPAND_FILL
	scroll.add_child(center)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 18)
	var pad := MarginContainer.new()
	for side in ["top", "bottom"]:
		pad.add_theme_constant_override("margin_" + side, 24)
	pad.add_child(box)
	center.add_child(pad)

	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 14)
	box.add_child(head)
	head.add_child(UI.label("CADENCE", 46, UI.ACCENT, 800))
	var sub := UI.label("Jedes Workout baut seine eigene Landschaft: Intervalle werden Pässe, Erholung wird Abfahrt oder Seeufer.", 18, UI.MUTED)
	sub.size_flags_vertical = SIZE_SHRINK_CENTER
	sub.size_flags_horizontal = SIZE_EXPAND_FILL
	sub.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	sub.custom_minimum_size.x = 420
	head.add_child(sub)
	var osm := Button.new()  # zweiter Modus: echte Strecken (OSM-Karte, route.py)
	osm.text = "Echte Strecke auf der Karte …"
	osm.size_flags_vertical = SIZE_SHRINK_CENTER
	osm.pressed.connect(func() -> void: get_tree().change_scene_to_file("res://picker.tscn"))
	head.add_child(osm)
	var opt := Button.new()
	opt.text = "Einstellungen"
	opt.size_flags_vertical = SIZE_SHRINK_CENTER
	opt.pressed.connect(func() -> void: add_child(Options.new()))
	head.add_child(opt)

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
	list.custom_minimum_size = Vector2(340, 300)
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
	bar.custom_minimum_size = Vector2(600, 110)
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
	profile = _option(grid, "Gelände", PROFILES.map(func(p): return p[1]), saved.get_value("start", "profile", 0))
	profile.tooltip_text = "Wie bergig die Strecke ist. Im SIM-Modus spürst du genau diese Steigungen; im ERG hält der Trainer die Zielwatt.\nAlpen: auf großen Anstiegen liegt oben Schnee, Regen fällt dort als Schnee."
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
	mode = _option(grid, "Trainer", ["ERG – Trainer hält die Zielwatt", "SIM – Steigung der Welt"], saved.get_value("start", "mode", 0))
	mode.item_selected.connect(func(_i: int) -> void: _mode_info())
	grid.add_child(Control.new())
	mode_info = UI.label("", 15, UI.MUTED)
	mode_info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	mode_info.custom_minimum_size.x = 440
	grid.add_child(mode_info)
	_label(grid, "FTP (W)")
	ftp = SpinBox.new()
	ftp.min_value = 80
	ftp.max_value = 600
	ftp.tooltip_text = "Funktionelle Schwellenleistung: alle Workout-Ziele sind Prozent davon"
	var cfg: ConfigFile = preload("res://main.gd").config()
	ftp.value = saved.get_value("start", "ftp", cfg.get_value("rider", "ftp", 250))
	grid.add_child(ftp)
	Options.ride_pickers(grid)

	var lspace := Control.new()  # Welt-Feinschliff unten links (rechts ist die Spalte sonst zu hoch für 900 px)
	lspace.size_flags_vertical = SIZE_EXPAND_FILL
	left.add_child(lspace)
	var adv := CheckButton.new()
	adv.text = "Welt-Feinschliff"
	left.add_child(adv)
	var advgrid := GridContainer.new()
	advgrid.columns = 2
	advgrid.add_theme_constant_override("h_separation", 18)
	advgrid.visible = false
	adv.toggled.connect(func(on: bool) -> void:
		advgrid.visible = on
		Options.store("start", {"adv": on}))
	left.add_child(advgrid)
	for k in [["density", "Landschaftsdichte"], ["drama", "Dramatik des Geländes"], ["variation", "Abwechslung"]]:
		_label(advgrid, k[1])
		var sl := HSlider.new()
		sl.min_value = 0.5
		sl.max_value = 1.5
		sl.step = 0.1
		sl.value = saved.get_value("start", k[0], 1.0)
		sl.custom_minimum_size = Vector2(150, 24)
		sl.size_flags_horizontal = SIZE_EXPAND_FILL
		advgrid.add_child(sl)
		sliders[k[0]] = sl
	adv.button_pressed = saved.get_value("start", "adv", false)

	var spacer := Control.new()
	spacer.size_flags_vertical = SIZE_EXPAND_FILL
	right.add_child(spacer)
	var go := UI.primary(Button.new(), 28)
	go.text = "Losfahren"
	go.custom_minimum_size.y = 64
	go.pressed.connect(_go)
	right.add_child(go)
	_load_list(saved.get_value("start", "workout", ""))
	_recent()
	go.grab_focus()


static func migrate_user_dir() -> void:
	## Umbenennung Kurbel -> Cadence: Godot legt einen neuen Benutzerordner an. Fahrten, Einstellungen, Kartenkacheln und
	## Vorschau aus dem alten Ordner einmalig herüberschieben (rename: gleiche Platte, sofort), nie etwas überschreiben.
	var new_dir := OS.get_user_data_dir()
	var old_dir := new_dir.get_base_dir() + "/Kurbel"
	if new_dir.get_file() == "Kurbel" or not DirAccess.dir_exists_absolute(old_dir):
		return
	for n: String in ["rides", "tiles", "preview", "start.cfg"]:
		var src: String = old_dir + "/" + n
		var dst: String = new_dir + "/" + n
		if DirAccess.dir_exists_absolute(dst) and DirAccess.get_files_at(dst).is_empty() and DirAccess.get_directories_at(dst).is_empty():
			DirAccess.remove_absolute(dst)  # leer angelegt (z. B. Kachel-Cache beim ersten Kartenaufruf) -> alten Inhalt nehmen
		if (DirAccess.dir_exists_absolute(src) or FileAccess.file_exists(src)) and not (DirAccess.dir_exists_absolute(dst) or FileAccess.file_exists(dst)):
			if DirAccess.rename_absolute(src, dst) == OK:
				print("Übernommen aus %s: %s" % [old_dir, n])
		elif n == "rides" and DirAccess.dir_exists_absolute(src):  # beide haben Fahrten: Datei für Datei, nichts überschreiben
			for f in DirAccess.get_files_at(src):
				if not FileAccess.file_exists(dst + "/" + f):
					DirAccess.rename_absolute(src + "/" + f, dst + "/" + f)


func _mode_info() -> void:
	## ERG/SIM erklären; ohne Workout gibt es kein Ziel -> immer SIM (main.gd::trainer_command)
	var free: bool = _selected().get("file", "") == ""
	mode.disabled = free
	if free:
		mode_info.text = "Freie Fahrt hat keine Zielwatt: der Trainer fährt immer SIM (Steigung der Welt)."
	elif mode.selected == 0:
		mode_info.text = "Der Trainer stellt die Zielwatt ein, egal wie steil es ist; Steigungen sind nur Bild und Tempo. Schalter: Intensität ±5 %."
	else:
		mode_info.text = "Die Steigung der Welt geht an den Trainer wie draußen; die Zielwatt stehen im HUD. Schalter: virtuelle Gänge."


func _selected() -> Dictionary:
	var sel := list.get_selected_items()
	return workouts[sel[0] if sel else 0] if workouts else {}


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
	OS.execute(preload("res://main.gd").uv(), ["run", ProjectSettings.globalize_path(preload("res://main.gd").data("res://workout.py")), "--list", ProjectSettings.globalize_path(preload("res://main.gd").data("res://workouts"))], out, true)
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
		info.text = "Freie Fahrt: eine endlose Strecke mit wechselnden Landschaften."
	else:
		var names := {"warmup": "Einfahren", "endurance": "Grundlage", "tempo": "Tempo", "threshold": "Schwelle", "vo2": "VO2max",
			"recovery": "Erholung", "cooldown": "Ausfahren", "free": "Frei", "set": "Intervall-Satz"}
		info.text = "%s · %d min\nAblauf: %s" % [w.name, roundi(w.duration / 60.0), " → ".join(w.phases.map(func(p): return names.get(p, p)))]
	if mode_info:
		_mode_info()


func _import() -> void:
	var fd := FileDialog.new()
	fd.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	fd.access = FileDialog.ACCESS_FILESYSTEM
	fd.filters = PackedStringArray(["*.zwo, *.erg, *.mrc ; Workouts"])
	fd.size = Vector2i(900, 600)
	fd.file_selected.connect(func(path: String) -> void:
		var dst := ProjectSettings.globalize_path(preload("res://main.gd").data("res://workouts/")) + path.get_file()
		if DirAccess.copy_absolute(path, dst) == OK:
			_load_list(dst)
		else:
			info.text = "Kopieren fehlgeschlagen: " + path)
	add_child(fd)
	fd.popup_centered()


func _go() -> void:
	var w := _selected()
	if w.has("error"):
		return
	var opts := {"workout": w.file, "biome": BIOMES[biome.selected][0], "seed": int(seed_box.value), "profile": PROFILES[profile.selected][0],
		"mode": "erg" if mode.selected == 0 else "sim", "ftp": ftp.value}
	for k in sliders:
		opts[k] = snappedf(sliders[k].value, 0.1)
	var keep := {"biome": biome.selected, "seed": int(seed_box.value), "mode": mode.selected, "ftp": ftp.value,
		"workout": w.file.get_file(), "profile": profile.selected}
	for k in sliders:
		keep[k] = sliders[k].value
	Options.store("start", keep)
	Engine.set_meta("world", opts)
	get_tree().change_scene_to_file("res://main.tscn")
