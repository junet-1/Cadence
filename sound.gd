extends Node
## Geräusche, komplett synthetisiert (keine Audiodateien): Fahrtwind, Reifen, Freilauf-Klacken, Gischt auf nasser Straße,
## Regen, Blätterrauschen und Vögel im Wald, nachts ein Kauz. Klänge entstehen einmal beim Start (Hintergrund-Thread) als
## Schleifen bzw. Rufe; pro Frame werden nur Lautstärke und Tonhöhe gesetzt. Reine Kulisse: liest main.gd, ändert nichts.
## Lautstärke: Einstellungen (options.gd), Taste M schaltet stumm.

const RATE := 22050
const POE := 24  # Klinken-Eingriffe der Freilaufnabe je Radumdrehung -> Klickrate beim Rollen (Kalibrierknopf: 18…54)
const WHEEL := 2.1  # m Radumfang
const CLICKS := 48.0  # Klicks/s in der Freilauf-Schleife bei Tonhöhe 1
const LOOPS := ["wind", "tire", "wet", "free", "rain", "leaves"]

var main: Node3D
var loops := {}  # Name -> AudioStreamPlayer
var amp := {}  # Name -> aktuelle Lautstärke (linear, geglättet)
var birds: Array[AudioStreamPlayer3D] = []
var sounds := {}  # vom Thread: Name -> AudioStreamWAV, "calls"/"owl" -> Array
var task := -1
var forest := 0.0  # 0 offen … 1 dichter Wald (Bäume im Umkreis von 40 m)
var night := 0.0
var scan_t := 0.0
var bird_t := 3.0


func _ready() -> void:
	for n in LOOPS:
		var p := AudioStreamPlayer.new()
		add_child(p)
		loops[n] = p
		amp[n] = 0.0
	for i in 3:
		var b := AudioStreamPlayer3D.new()
		b.unit_size = 12.0
		b.max_distance = 150.0
		add_child(b)
		birds.append(b)
	task = WorkerThreadPool.add_task(func() -> void: sounds = make())


func toggle_mute() -> void:
	AudioServer.set_bus_mute(0, not AudioServer.is_bus_mute(0))


static func free_pitch(speed: float) -> float:
	## Freilauf-Schleife: Klicks/s = Radumdrehungen/s × Klinken
	return clampf(speed / WHEEL * POE / CLICKS, 0.25, 4.0)


func update(delta: float) -> void:
	if task != -1:
		if not WorkerThreadPool.is_task_completed(task):
			return
		WorkerThreadPool.wait_for_task_completion(task)
		task = -1
		for n in LOOPS:
			loops[n].stream = sounds[n]
			loops[n].volume_db = -80.0
			loops[n].play()
	var w: Node = main.weather
	var speed: float = 0.0 if main.paused() else main.speed
	var fwd: Vector3 = -main.rider.global_basis.z
	var air: float = (Vector2(fwd.x, fwd.z) * speed - w.wind * w.gust * preload("res://weather.gd").RIDER_WIND).length()  # Luft am Ohr: Fahrt + Wetterwind
	var breeze := clampf(w.wind.length() * w.gust / 14.0, 0.0, 1.2)
	var coast: bool = main.cadence < 5.0 and speed > 0.4
	_scan(delta)
	_level("wind", pow(clampf((air - 1.5) / 14.0, 0.0, 1.2), 1.5) * 0.5, 0.8 + air / 30.0, delta)
	_level("tire", pow(clampf(speed / 12.0, 0.0, 1.0), 1.2) * 0.3, clampf(0.7 + speed / 14.0, 0.5, 2.2), delta)
	_level("wet", w.wet * clampf(speed / 10.0, 0.0, 1.0) * 0.12, clampf(0.8 + speed / 20.0, 0.8, 1.6), delta)
	_level("free", clampf(speed / 3.0, 0.0, 1.0) * 0.35 if coast else 0.0, free_pitch(speed), delta, 20.0)
	_level("rain", 0.0 if w.snow else w.rain * 0.12, 1.0, delta)
	_level("leaves", forest * clampf(0.15 + breeze, 0.0, 1.2) * 0.35, 1.0, delta)
	bird_t -= delta
	if bird_t <= 0.0:
		_call(w)


func _level(n: String, a: float, pitch: float, delta: float, rate := 4.0) -> void:
	amp[n] = lerpf(amp[n], a, 1.0 - exp(-delta * rate))  # weich, sonst knackt es bei Sprüngen
	loops[n].volume_db = linear_to_db(maxf(amp[n], 1e-4))
	loops[n].pitch_scale = pitch


func _scan(delta: float) -> void:
	## Alle 0,5 s: Bäume im Umkreis von 40 m zählen (main.tree_pts), Tageszeit
	scan_t -= delta
	if scan_t > 0.0:
		return
	scan_t = 0.5
	night = 1.0 - smoothstep(-0.12, 0.08, main.sun_dir().y)
	var here := Vector2(main.rider.global_position.x, main.rider.global_position.z)
	var n := 0
	for key in main.tree_pts:
		var pts: PackedVector2Array = main.tree_pts[key]
		# ponytail: erster Baum als Kachelmitte (Kacheln ≤ 256 m); echte Mitte speichern, wenn Kacheln größer werden
		if pts[0].distance_squared_to(here) > 300.0 * 300.0:
			continue
		for p in pts:
			if p.distance_squared_to(here) < 40.0 * 40.0:
				n += 1
	forest = lerpf(forest, clampf(n / 30.0, 0.0, 1.0), 0.5)


func _call(w: Node) -> void:
	## Nächster Ruf: tags Vögel (im Wald oft, im Offenen selten), nachts im Wald ab und zu ein Kauz; Regen dämpft
	var rate: float = (0.06 + 0.5 * forest) * (1.0 - night) * (1.0 - 0.8 * w.rain) + 0.04 * forest * night
	bird_t = randf_range(0.5, 2.0) / maxf(rate, 0.02)
	if sounds.is_empty() or main.paused() or randf() > rate * 2.0:
		return
	var idle := birds.filter(func(x: AudioStreamPlayer3D) -> bool: return not x.playing)
	if idle.is_empty():
		return
	var b: AudioStreamPlayer3D = idle.pick_random()
	var calls: Array = sounds.owl if night > 0.5 else sounds.calls
	b.stream = calls.pick_random()
	b.pitch_scale = randf_range(0.92, 1.08)
	b.volume_db = randf_range(-8.0, 0.0)
	var a := randf() * TAU
	b.global_position = main.rider.global_position + Vector3(cos(a), 0.0, sin(a)) * randf_range(15.0, 55.0) + Vector3.UP * randf_range(5.0, 12.0)
	b.play()


# --- Synthese (läuft im Thread, keine Szenenknoten) ---

static func make() -> Dictionary:
	var s := {
		"wind": _wav(_loop(_noise(3.0, 40.0, 500.0, 1)), true),
		"tire": _wav(_loop(_noise(2.0, 150.0, 2500.0, 2)), true),
		"wet": _wav(_loop(_noise(2.0, 800.0, 4500.0, 3)), true),
		"free": _wav(_clicks(48, 1.0), true),
		"rain": _wav(_loop(_rain(3.0)), true),
		"leaves": _wav(_loop(_gusts(_noise(4.0, 1200.0, 7000.0, 5), 4.0)), true),
		"calls": [], "owl": [_wav(_owl(), false)]}
	for v in 2:
		for kind in ["meise", "fink", "amsel", "zilpzalp"]:
			s.calls.append(_wav(_bird(kind, 10 + v), false))
	return s


static func _wav(s: PackedFloat32Array, loop: bool) -> AudioStreamWAV:
	var b := PackedByteArray()
	b.resize(s.size() * 2)
	for i in s.size():
		b.encode_s16(i * 2, int(clampf(s[i], -1.0, 1.0) * 32767.0))
	var w := AudioStreamWAV.new()
	w.format = AudioStreamWAV.FORMAT_16_BITS
	w.mix_rate = RATE
	w.data = b
	if loop:
		w.loop_mode = AudioStreamWAV.LOOP_FORWARD
		w.loop_end = s.size()
	return w


static func _noise(sec: float, lo: float, hi: float, seed: int) -> PackedFloat32Array:
	## Weißes Rauschen im Band lo…hi Hz (je zwei Tiefpässe erster Ordnung), Spitze auf 1 normiert
	var rng := RandomNumberGenerator.new()
	rng.seed = seed
	var a := 1.0 - exp(-TAU * hi / RATE)
	var b := 1.0 - exp(-TAU * lo / RATE)
	var s := PackedFloat32Array()
	s.resize(int(sec * RATE))
	var y1 := 0.0
	var y2 := 0.0
	var z1 := 0.0
	var z2 := 0.0
	var peak := 1e-6
	for i in s.size():
		y1 += a * (rng.randf() * 2.0 - 1.0 - y1)
		y2 += a * (y1 - y2)
		z1 += b * (y2 - z1)
		z2 += b * (z1 - z2)
		s[i] = y2 - z2
		peak = maxf(peak, absf(s[i]))
	for i in s.size():
		s[i] /= peak
	return s


static func _loop(s: PackedFloat32Array, fade := 4000) -> PackedFloat32Array:
	## Ende in den Anfang überblenden (gleiche Leistung) -> nahtlose Schleife, fade Samples kürzer
	var n := s.size() - fade
	for i in fade:
		var k := float(i) / fade
		s[i] = s[i] * sqrt(k) + s[n + i] * sqrt(1.0 - k)
	return s.slice(0, n)


static func _gusts(s: PackedFloat32Array, sec: float) -> PackedFloat32Array:
	## Blätter: Lautstärke schwillt in Böen an und ab (ganze Perioden je Schleife -> nahtlos)
	for i in s.size():
		var t := float(i) / RATE / sec
		s[i] *= 0.35 + 0.65 * (0.5 + 0.25 * sin(TAU * 2.0 * t) + 0.25 * sin(TAU * 5.0 * t + 1.3))
	return s


static func _clicks(count: int, sec: float) -> PackedFloat32Array:
	## Freilauf: count Klinkenklicks je Schleife, leicht unregelmäßig (Takt und Stärke), Ende läuft in den Anfang über
	var rng := RandomNumberGenerator.new()
	rng.seed = 4
	var s := PackedFloat32Array()
	s.resize(int(sec * RATE))
	for c in count:
		var at := int((c + rng.randf_range(-0.12, 0.12)) * s.size() / count)
		var a := rng.randf_range(0.55, 1.0)
		for j in 100:
			var t := float(j) / RATE
			s[posmod(at + j, s.size())] += a * exp(-t / 0.0007) * (0.65 * sin(TAU * 3600.0 * t) + rng.randf_range(-0.35, 0.35))
	return s


static func _rain(sec: float) -> PackedFloat32Array:
	## Weiches Rauschen (Regen auf Laub, ohne scharfe Höhen) + leise einzelne Tropfen
	var s := _noise(sec + 4000.0 / RATE, 250.0, 3200.0, 6)
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	for i in s.size():
		s[i] *= 0.5
	for d in int(sec * 120.0):
		var at := rng.randi_range(0, s.size() - 200)
		var f := rng.randf_range(700.0, 1800.0)
		var a := rng.randf_range(0.04, 0.15)
		for j in 200:
			var t := float(j) / RATE
			s[at + j] += a * exp(-t / 0.002) * sin(TAU * f * t)
	return s


static func _tone(s: PackedFloat32Array, at: float, dur: float, f0: float, f1: float, vib := 0.0, a := 1.0) -> void:
	## Pfeifton ab at s, dur s lang, gleitet f0 -> f1 Hz (Sinus + leise Oktave, optional Vibrato), weicher Ein-/Ausklang
	var i0 := int(at * RATE)
	var n := int(dur * RATE)
	var ph := 0.0
	for j in mini(n, s.size() - i0):
		var k := float(j) / n
		ph += TAU * lerpf(f0, f1, k) * (1.0 + vib * sin(TAU * 26.0 * j / RATE)) / RATE
		s[i0 + j] += a * sqrt(sin(PI * k)) * (sin(ph) + 0.15 * sin(2.0 * ph)) * 0.6


static func _bird(kind: String, seed: int) -> PackedFloat32Array:
	## Grobe Gesangsmuster (keine echten Aufnahmen): Meise "zi-zi-bä", Fink-Triller mit Schlussschnörkel, flötende
	## Amsel, Zilpzalp-Wechselruf. seed variiert Tonhöhen und Längen.
	var rng := RandomNumberGenerator.new()
	rng.seed = seed * 31 + kind.hash()
	var s := PackedFloat32Array()
	s.resize(int(2.4 * RATE))
	var t := 0.05
	match kind:
		"meise":
			var hi := rng.randf_range(4800.0, 5600.0)
			var lo := hi * rng.randf_range(0.66, 0.75)
			for r in rng.randi_range(3, 5):
				_tone(s, t, 0.07, hi, hi * 0.97)
				_tone(s, t + 0.1, 0.11, lo, lo * 0.95)
				t += 0.3
		"fink":
			for r in rng.randi_range(8, 12):
				var f := lerpf(5200.0, 3600.0, r / 12.0) * rng.randf_range(0.97, 1.03)
				_tone(s, t, 0.045, f, f * 0.8)
				t += 0.075
			_tone(s, t + 0.05, 0.3, 2900.0, 5400.0, 0.0, 0.9)
		"amsel":
			for r in rng.randi_range(4, 6):
				var d := rng.randf_range(0.12, 0.3)
				var f := rng.randf_range(1700.0, 2800.0)
				_tone(s, t, d, f, f * rng.randf_range(0.85, 1.2), 0.015)
				t += d + rng.randf_range(0.03, 0.1)
		"zilpzalp":
			for r in rng.randi_range(5, 7):
				var f := 4300.0 if r % 2 == 0 else 3600.0
				_tone(s, t, 0.09, f * 1.03, f * 0.95)
				t += rng.randf_range(0.28, 0.36)
	return s.slice(0, mini(s.size(), int((t + 0.4) * RATE)))


static func _owl() -> PackedFloat32Array:
	## Waldkauz: "huu" … Pause … zitterndes "hu-hu-huuu"
	var s := PackedFloat32Array()
	s.resize(int(3.0 * RATE))
	_tone(s, 0.0, 0.7, 640.0, 600.0, 0.0, 0.8)
	for r in 3:
		_tone(s, 1.6 + r * 0.14, 0.12, 620.0, 600.0, 0.0, 0.6)
	_tone(s, 2.05, 0.8, 620.0, 560.0, 0.04, 0.8)
	return s
