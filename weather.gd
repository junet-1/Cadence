extends Node
## Wetter: Bewölkung, Regen/Schnee, Wind (Richtung, Stärke, Böen). Deterministisch aus Seed + echter Uhrzeit -> zwei Fahrer
## mit demselben Seed haben dasselbe Wetter. Wirkt auf Optik (Himmel, Wolkenschatten, Nebel, Regen, nasse Straße, Gras/Bäume)
## und auf die Fahrphysik in main.gd (Luftwiderstand mit Gegen-/Rückenwind) – NIE auf den Trainerwiderstand (SIM bleibt
## reine Streckengeometrie, siehe CLAUDE.md).
## Taste W schaltet: Auto -> Sonnig -> Wolkig -> Regen -> Sturm -> Auto.

const PRESETS := ["Auto", "Sonnig", "Wolkig", "Regen", "Sturm"]
const CLOUD_H := 1600.0  # m über dem Fahrer: Haufenwolken (Himmel und Schatten am Boden rechnen mit derselben Höhe)
const RIDER_WIND := 0.75  # Wind in Fahrerhöhe statt 10 m (Bodenreibung)

var seed := 1
var preset := 0
var cover := 0.3  # Bedeckung 0…1 (geglättet zum Ziel)
var rain := 0.0  # Niederschlag 0…1
var wind := Vector2.ZERO  # m/s in 10 m Höhe, Welt x/z, Richtung = wohin die Luft strömt
var gust := 1.0  # Böenfaktor
var wet := 0.0  # Nässe der Straße 0…1 (trocknet langsam)
var heat := 0.0  # Hitzeflimmern/Luftspiegelung 0…1: Sonne hoch, klar, trocken, warm
var snow := false
var cloud_off := Vector2.ZERO
var wind_t := 0.0  # Schwingungsphase für Gras/Bäume: läuft bei Wind schneller, aber stetig (TIME × Stärke springt bei Böen)
var wind_scroll := Vector2.ZERO  # Böenfeld wandert mit dem Wind übers Land (m), ebenfalls stetig integriert
var fx: GPUParticles3D
var _t := 0.0


static func _n1(x: float, s: int) -> float:
	## Glattes 1D-Rauschen ~[-1, 1] (gehasht, ohne Zustand)
	var i := floorf(x)
	var f := x - i
	var h := func(k: float) -> float: return fposmod(sin(k * 127.1 + s * 311.7) * 43758.5453, 1.0) * 2.0 - 1.0
	var u := f * f * (3.0 - 2.0 * f)
	return lerpf(h.call(i), h.call(i + 1.0), u)


static func state(s: int, minutes: float, arid: float, p: int) -> Dictionary:
	## Zielwetter -> {cover, rain, wind (m/s), dir (rad), gusty}. minutes = Unixzeit in Minuten. arid 0…1 (Wüste fast nie Regen).
	var cover := clampf(0.45 + 0.55 * _n1(minutes / 45.0, s + 1) + 0.25 * _n1(minutes / 9.0, s + 2) - 0.55 * arid, 0.0, 1.0)
	var rain := clampf((cover - 0.72) / 0.22, 0.0, 1.0) * clampf(0.6 + _n1(minutes / 14.0, s + 3), 0.0, 1.0) * (1.0 - arid)
	var speed := maxf(0.5, 4.0 + 3.0 * _n1(minutes / 50.0, s + 4) + 3.0 * cover * cover)
	var dir := TAU * fposmod(sin(s * 12.9898) * 43758.5453, 1.0) + 1.4 * _n1(minutes / 120.0, s + 5)
	match PRESETS[p]:
		"Sonnig":
			cover = 0.12; rain = 0.0; speed = 2.5
		"Wolkig":
			cover = 0.65; rain = 0.0; speed = 5.5
		"Regen":
			cover = 0.97; rain = 0.85; speed = 7.0
		"Sturm":
			cover = 0.9; rain = 0.45; speed = 15.0
	return {"cover": cover, "rain": rain, "wind": speed, "dir": dir, "gusty": 0.25 + 0.35 * clampf(speed / 12.0, 0.0, 1.0)}


static func air_force(speed: float, head: float, rho: float, cda: float) -> float:
	## Luftwiderstand (N) bei Fahrtempo speed und Gegenwind head (m/s, negativ = Rückenwind). Rückenwind schneller als
	## das Rad schiebt (Kraft < 0).
	var v := speed + head
	return 0.5 * rho * cda * v * absf(v)


func headwind(fwd: Vector3) -> float:
	## Gegenwindanteil in Fahrerhöhe (m/s) für Fahrtrichtung fwd; negativ = Rückenwind
	return -Vector2(fwd.x, fwd.z).normalized().dot(wind * gust) * RIDER_WIND


func describe(fwd: Vector3) -> String:
	var name := "Regen" if rain > 0.15 and not snow else "Schnee" if rain > 0.15 else \
		"Bedeckt" if cover > 0.8 else "Wolkig" if cover > 0.5 else "Heiter" if cover > 0.2 else "Sonnig"
	if wind.length() > 11.0:
		name += ", stürmisch"
	var h := headwind(fwd)
	var side := Vector2(fwd.x, fwd.z).normalized().orthogonal().dot(wind * gust) * RIDER_WIND
	var w := ""
	if absf(h) >= absf(side) * 0.6:
		w = "%s %d km/h" % ["Gegenwind" if h > 0.0 else "Rückenwind", roundi(absf(h) * 3.6)]
	else:
		w = "Seitenwind %s %d km/h" % ["von rechts" if side > 0.0 else "von links", roundi(absf(side) * 3.6)]
	return "%s · %s%s" % [name, w, "" if preset == 0 else "  [W: %s]" % PRESETS[preset]]


func next_preset() -> void:
	preset = (preset + 1) % PRESETS.size()


func update(delta: float, arid: float, rider: Vector3, snow_line: float, env: Environment, sky: ShaderMaterial,
		sun: DirectionalLight3D, fog_base: float, warm: float) -> void:
	## Jeden Frame aus main.gd: Ziel aus Seed + Uhrzeit, weich nachführen, alles Sichtbare setzen.
	_t += delta
	var target := state(seed, Time.get_unix_time_from_system() / 60.0, arid, preset)
	var k := 1.0 - exp(-delta / (4.0 if preset != 0 else 30.0))  # Umschalten per Taste schnell, Wetterwechsel langsam
	cover = lerpf(cover, target.cover, k)
	rain = lerpf(rain, target.rain, k)
	var tw: Vector2 = Vector2.from_angle(float(target.dir)) * float(target.wind)
	wind = wind.lerp(tw, k)
	gust = 1.0 + target.gusty * (0.6 * _n1(_t / 4.0, seed + 7) + 0.4 * _n1(_t / 1.3, seed + 8))
	wet = clampf(wet + delta * (rain / 60.0 if rain > 0.05 else -1.0 / 300.0), 0.0, 1.0)
	snow = rider.y > snow_line - 60.0
	cloud_off -= wind * delta * 1.6  # Wolken ziehen mit dem Höhenwind (stärker als am Boden)

	RenderingServer.global_shader_parameter_set("clouds", Vector4(cloud_off.x, cloud_off.y, cover, CLOUD_H))
	var strength := clampf(wind.length() * gust / 14.0, 0.0, 1.5)
	wind_t += delta * (1.0 + 1.2 * strength)
	wind_scroll += wind * gust * delta
	RenderingServer.global_shader_parameter_set("wind", Vector3(wind.x, wind.y, strength))
	RenderingServer.global_shader_parameter_set("wind_t", wind_t)
	RenderingServer.global_shader_parameter_set("wind_scroll", wind_scroll)
	RenderingServer.global_shader_parameter_set("wetness", wet)
	var sun_up := sun.global_basis.z.y if sun.visible else 0.0  # +Z der Sonne zeigt zur Sonne (sie leuchtet entlang −Z)
	var hot := smoothstep(0.25, 0.65, sun_up) * (1.0 - smoothstep(0.35, 0.75, cover)) * (1.0 - wet) * clampf(warm, 0.0, 1.0)
	heat = lerpf(heat, hot, 1.0 - exp(-delta / 10.0))
	RenderingServer.global_shader_parameter_set("heat", heat)
	sky.set_shader_parameter("rain", rain)
	var dull := smoothstep(0.55, 1.0, cover)
	sun.light_energy *= 1.0 - 0.75 * dull  # bedeckt: kaum direkte Sonne (lokale Wolkenschatten macht der Boden-Shader)
	sun.shadow_blur = 1.0 + 4.0 * dull
	env.fog_density = fog_base * (1.0 + 2.0 * dull + 5.0 * rain)
	env.fog_light_color = env.fog_light_color.lerp(Color(0.46, 0.48, 0.52), 0.7 * dull)
	env.ambient_light_energy *= 1.0 - 0.25 * dull
	if fx:
		fx.amount_ratio = rain
		fx.global_position = rider + Vector3(wind.x, 0, wind.y) * 0.8 + Vector3.UP * 14.0  # Tropfen kommen von luv
		var pm: ParticleProcessMaterial = fx.process_material
		var fall := 1.6 if snow else 9.0
		pm.direction = Vector3(wind.x * gust / fall, -1.0, wind.y * gust / fall).normalized()
		pm.initial_velocity_min = fall * 0.9
		pm.initial_velocity_max = fall * 1.1
		pm.turbulence_enabled = snow
		var mat: StandardMaterial3D = (fx.draw_pass_1 as QuadMesh).material
		mat.albedo_color = Color(1, 1, 1, 0.9) if snow else Color(0.7, 0.75, 0.82, 0.18)
		(fx.draw_pass_1 as QuadMesh).size = Vector2(0.06, 0.06) if snow else Vector2(0.008, 0.55)


func make_fx() -> GPUParticles3D:
	## Regen-/Schneepartikel um den Fahrer (Streifen, die zur Kamera gedreht nur um die Hochachse)
	fx = GPUParticles3D.new()
	fx.amount = 9000
	fx.lifetime = 2.2
	fx.local_coords = false
	fx.amount_ratio = 0.0
	fx.visibility_aabb = AABB(Vector3(-50, -40, -50), Vector3(100, 60, 100))
	var pm := ParticleProcessMaterial.new()
	pm.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	pm.emission_box_extents = Vector3(35, 1, 35)
	pm.direction = Vector3.DOWN
	pm.spread = 3.0
	pm.gravity = Vector3.ZERO
	pm.turbulence_noise_strength = 2.0
	pm.turbulence_influence_min = 0.1
	pm.turbulence_influence_max = 0.3
	fx.process_material = pm
	var q := QuadMesh.new()
	q.size = Vector2(0.008, 0.55)
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_FIXED_Y
	mat.billboard_keep_scale = true
	mat.albedo_color = Color(0.7, 0.75, 0.82, 0.18)
	q.material = mat
	fx.draw_pass_1 = q
	return fx
