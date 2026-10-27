extends Control
## Workout-Profil als Balken (Höhe = Anteil FTP, Farbe = Leistungszone), Strich = aktuelle Workout-Zeit. HUD unten und Startbildschirm.

const UI := preload("res://ui_theme.gd")
const FREE := Color(0.6, 0.6, 0.6)


static func color(p0: float, p1: float, free: bool) -> Color:
	## Farbe eines Abschnitts = Leistungszone seines mittleren Ziels (gleiche Farben wie HUD und Auswertung)
	return FREE if free else UI.zone((p0 + p1) * 0.5)[2]


var segs: Array = []  # [t0, dur, p0, p1, kind, frei]
var duration := 1.0
var t := -1.0:
	set(v):
		if absf(v - t) > 0.5:
			t = v
			queue_redraw()
var hud := true


func setup(w: Dictionary) -> void:
	## w = normalisiertes Workout (main.gd, meta.workout) oder Zusammenfassung aus workout.py --list (start.gd)
	segs.clear()
	for s in w.get("segments", []):
		if s is Dictionary:
			segs.append([s.t0, s.dur, s.p0, s.p1, s.kind, s.free])
		else:
			segs.append([s[0], s[1], s[2], s[3], s[4], s[4] == "free"])
	duration = maxf(w.get("duration", 1.0), 1.0)
	queue_redraw()


func _ready() -> void:
	mouse_filter = MOUSE_FILTER_IGNORE
	if hud:
		anchor_left = 0.25
		anchor_right = 0.75
		anchor_top = 1.0
		anchor_bottom = 1.0
		offset_top = -92
		offset_bottom = -20


func _draw() -> void:
	draw_style_box(UI.box(UI.PANEL, 10, 0), Rect2(Vector2.ZERO, size))
	if segs.is_empty():  # freie Fahrt: kein Profil
		draw_string(get_theme_default_font(), Vector2(0, size.y * 0.5 + 7), "Ohne Intervalle – fahr, wie du magst",
			HORIZONTAL_ALIGNMENT_CENTER, size.x, 18, UI.MUTED)
		return
	var top := 1.6  # 160 % FTP füllt die Höhe
	for s in segs:
		var x0: float = s[0] / duration * size.x
		var x1: float = (s[0] + s[1]) / duration * size.x
		var h0: float = (0.5 if s[5] else s[2]) / top * size.y
		var h1: float = (0.5 if s[5] else s[3]) / top * size.y
		var c := color(s[2], s[3], s[5])
		draw_colored_polygon(PackedVector2Array([Vector2(x0, size.y), Vector2(x0, size.y - h0), Vector2(x1, size.y - h1), Vector2(x1, size.y)]), c)
	var ftp_y := size.y - 1.0 / top * size.y
	draw_dashed_line(Vector2(0, ftp_y), Vector2(size.x, ftp_y), Color(1, 1, 1, 0.5), 1.0, 6.0)
	if t >= 0.0:
		var x := clampf(t / duration, 0.0, 1.0) * size.x
		draw_rect(Rect2(Vector2.ZERO, Vector2(x, size.y)), Color(0, 0, 0, 0.35))
		draw_line(Vector2(x, 0), Vector2(x, size.y), Color.WHITE, 3.0)
