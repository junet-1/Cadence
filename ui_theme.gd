extends RefCounted
## Gemeinsames Aussehen für Startbildschirm, Fahr-HUD und Zusammenfassung: dunkle, halbtransparente Panels mit runden Ecken,
## eine Schriftfamilie (System: Inter/Noto Sans/DejaVu, sonst Godot-Standard), Akzentfarbe, Leistungszonen-Farben.

const BG := Color(0.07, 0.08, 0.10)
const PANEL := Color(0.08, 0.09, 0.11, 0.78)
const PANEL_SOLID := Color(0.11, 0.12, 0.15)
const LINE := Color(1, 1, 1, 0.08)
const TEXT := Color(0.94, 0.95, 0.97)
const MUTED := Color(0.62, 0.66, 0.72)
const ACCENT := Color(1.0, 0.48, 0.12)  # Kurbel-Orange (Fahrerfarbe)
const GOOD := Color(0.35, 0.82, 0.45)
const WARN := Color(1.0, 0.78, 0.25)
const BAD := Color(0.95, 0.33, 0.3)
# Coggan-Zonen (Anteil FTP bis): Z1 aktive Erholung … Z6 anaerob
const ZONES := [[0.55, "Z1 Erholung", Color(0.55, 0.58, 0.64)], [0.75, "Z2 Grundlage", Color(0.26, 0.56, 0.96)],
	[0.90, "Z3 Tempo", Color(0.30, 0.78, 0.40)], [1.05, "Z4 Schwelle", Color(0.98, 0.78, 0.16)],
	[1.20, "Z5 VO2max", Color(0.96, 0.45, 0.16)], [99.0, "Z6 Anaerob", Color(0.90, 0.22, 0.25)]]

static var _theme: Theme


static func font(weight := 400) -> Font:
	var f := SystemFont.new()
	f.font_names = PackedStringArray(["Inter", "Noto Sans", "DejaVu Sans", "sans-serif"])
	f.font_weight = weight
	f.antialiasing = TextServer.FONT_ANTIALIASING_GRAY  # LCD franst auf halbtransparenten Panels aus
	f.hinting = TextServer.HINTING_LIGHT
	return f


static func box(color: Color, radius := 14, pad := 14, border := Color(0, 0, 0, 0)) -> StyleBoxFlat:
	var s := StyleBoxFlat.new()
	s.bg_color = color
	s.set_corner_radius_all(radius)
	s.set_content_margin_all(pad)
	if border.a > 0.0:
		s.set_border_width_all(1)
		s.border_color = border
	s.anti_aliasing = true
	return s


static func theme() -> Theme:
	if _theme:
		return _theme
	var t := Theme.new()
	t.default_font = font(450)
	t.default_font_size = 20
	t.set_color("font_color", "Label", TEXT)
	t.set_stylebox("panel", "PanelContainer", box(PANEL, 16, 18, LINE))
	t.set_stylebox("panel", "Panel", box(PANEL, 16, 18, LINE))
	for state in ["normal", "hover", "pressed", "focus", "disabled"]:
		var c: Color = {"normal": Color(1, 1, 1, 0.07), "hover": Color(1, 1, 1, 0.13), "pressed": Color(1, 1, 1, 0.18),
			"focus": Color(1, 1, 1, 0.07), "disabled": Color(1, 1, 1, 0.03)}[state]
		var b := box(c, 10, 10, ACCENT if state == "focus" else LINE)
		b.content_margin_left = 16
		b.content_margin_right = 16
		for cls in ["Button", "OptionButton", "CheckButton"]:
			t.set_stylebox(state, cls, b)
	t.set_color("font_color", "Button", TEXT)
	t.set_color("font_hover_color", "Button", Color.WHITE)
	t.set_color("font_focus_color", "Button", Color.WHITE)
	t.set_stylebox("panel", "ItemList", box(Color(1, 1, 1, 0.04), 12, 8, LINE))
	t.set_stylebox("selected", "ItemList", box(Color(ACCENT, 0.25), 8, 6))
	t.set_stylebox("selected_focus", "ItemList", box(Color(ACCENT, 0.32), 8, 6))
	t.set_stylebox("hovered", "ItemList", box(Color(1, 1, 1, 0.06), 8, 6))
	t.set_stylebox("focus", "ItemList", StyleBoxEmpty.new())
	t.set_constant("v_separation", "ItemList", 10)
	t.set_color("font_color", "ItemList", TEXT)
	t.set_stylebox("normal", "LineEdit", box(Color(1, 1, 1, 0.06), 10, 8, LINE))
	t.set_stylebox("focus", "LineEdit", box(Color(1, 1, 1, 0.06), 10, 8, ACCENT))
	t.set_stylebox("slider", "HSlider", box(Color(1, 1, 1, 0.12), 3, 3))
	t.set_stylebox("grabber_area", "HSlider", box(ACCENT, 3, 3))
	t.set_stylebox("grabber_area_highlight", "HSlider", box(ACCENT.lightened(0.2), 3, 3))
	t.set_stylebox("background", "ProgressBar", box(Color(1, 1, 1, 0.1), 4, 0))
	t.set_stylebox("fill", "ProgressBar", box(ACCENT, 4, 0))
	t.set_stylebox("panel", "PopupMenu", box(PANEL_SOLID, 10, 8, LINE))
	t.set_stylebox("panel", "TooltipPanel", box(PANEL_SOLID, 8, 8, LINE))
	_theme = t
	return t


static func zone(frac: float) -> Array:
	## Anteil FTP -> [Index, Name, Farbe]
	for i in ZONES.size():
		if frac < ZONES[i][0]:
			return [i, ZONES[i][1], ZONES[i][2]]
	return [5, ZONES[5][1], ZONES[5][2]]


static func label(text: String, size := 20, color := TEXT, weight := 450) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	if weight != 450:
		l.add_theme_font_override("font", font(weight))
	return l


static func hms(sec: float) -> String:
	var s := int(maxf(sec, 0.0))
	return "%d:%02d:%02d" % [s / 3600, s / 60 % 60, s % 60] if s >= 3600 else "%d:%02d" % [s / 60, s % 60]
