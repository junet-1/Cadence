shader_type spatial;
render_mode world_vertex_coords, cull_disabled;
// Gras/Blumen um den Fahrer, komplett auf der GPU: Instanz-Nr -> Rasterzelle in Weltkoordinaten (wandert mit, rutscht nicht),
// Höhe aus der Heightmap, Dichte aus dem Alpha der Bodentextur (255 Wiese, 230 Feld, 215 Ort, 200 Wald, 160 kahl, 128 Wasser).
#include "res://shaders/noise.gdshaderinc"
#include "res://shaders/snow.gdshaderinc"

global uniform vec3 player;
global uniform float snow_line;
global uniform vec3 wind;  // x/z = Windrichtung (m/s), z-Komponente = Stärke inkl. Böe 0…1,5 (weather.gd)
global uniform float wind_t;  // stetige Phase (weather.gd), nie TIME × Stärke -> kein Zucken bei Böen
global uniform vec2 wind_scroll;  // Böenfeld, wandert mit dem Wind
uniform sampler2D ground : source_color, filter_nearest, repeat_disable;  // scharfe Klassengrenzen, kein Gras-Saum auf der Straße
uniform sampler2D heights : filter_linear, repeat_disable;
uniform vec4 rect;  // x0, z0, Breite, Tiefe
uniform vec3 grid;  // Heightmap: Punkte x, Punkte z, Rasterweite
uniform int side = 200;
uniform float spacing = 0.5;
uniform bool flowers = false;

varying vec3 tint;
varying float tip;
varying float head;
varying float dry;  // Vergilbung je Halm (_grass_mesh COLOR.g)

void vertex() {
	vec2 cell = floor(player.xz / spacing) + vec2(float(INSTANCE_ID % side), float(INSTANCE_ID / side)) - float(side / 2);
	vec2 pos = (cell + vec2(hash(cell + 7.1), hash(cell + 3.3))) * spacing;
	vec4 g = textureLod(ground, (pos - rect.xy) / rect.zw, 0.0);
	float density = g.a > 0.97 ? 1.0 : g.a > 0.87 ? 0.75 : g.a > 0.82 ? 0.4 : g.a > 0.75 ? 0.2 : 0.0;
	if (flowers) {
		density = g.a > 0.97 ? 0.12 : g.a > 0.87 ? 0.02 : 0.0;
	}
	float radius = float(side) * spacing * 0.5;
	float keep = float(hash(cell) < density) * smoothstep(radius, radius * 0.7, length(pos - player.xz));  // strikt: hash kann 0.0 sein
	float y = textureLod(heights, ((pos - rect.xy) / grid.z + 0.5) / grid.xy, 0.0).r;
	if (y > snow_line - 300.0) {  // unter Schnee kein Gras: gleiche Regel wie der terrain-Shader (Normale aus der Heightmap)
		vec2 uvh = ((pos - rect.xy) / grid.z + 0.5) / grid.xy;
		float hx = textureLod(heights, uvh + vec2(1.0 / grid.x, 0.0), 0.0).r, hz = textureLod(heights, uvh + vec2(0.0, 1.0 / grid.y), 0.0).r;
		vec3 gn = normalize(vec3(y - hx, grid.z, y - hz));
		keep *= step(snow_cover(vec3(pos.x, y, pos.y), gn, snow_line) + (hash(cell + 23.0) - 0.5) * 0.4, 0.5);
	}

	float ang = hash(cell + 11.0) * 6.2832;
	float s = (0.6 + 0.8 * hash(cell + 5.0)) * keep;
	vec3 v = VERTEX;
	mat2 rot = mat2(vec2(cos(ang), sin(ang)), vec2(-sin(ang), cos(ang)));
	v.xz = rot * v.xz;
	vec3 n = NORMAL;
	n.xz = rot * n.xz;
	v *= s;
	tip = UV.y;
	head = COLOR.a;

	// Wind mit Böen, und Ausweichen vor dem Fahrer
	float ws = wind.z;
	vec2 wd = normalize(wind.xy + vec2(1e-4, 0.0));
	float gust = noise((pos - wind_scroll) * 0.04);  // Böen laufen als Wellen mit dem Wind übers Feld
	float wave = sin(wind_t * 2.4 + dot(pos, wd) * 0.35 + hash(cell) * 2.0);
	vec2 bend = wd * (wave * (0.03 + 0.06 * ws) + gust * gust * (0.12 + 0.6 * ws) + 0.08 * ws);
	vec2 away = pos - player.xz;
	bend += normalize(away + 0.001) * smoothstep(2.5, 0.0, length(away)) * 0.6;
	v.xz += bend * tip * tip * s;
	VERTEX = vec3(pos.x, y, pos.y) + v;
	NORMAL = normalize(mix(vec3(0.0, 1.0, 0.0), n, 0.55));  // halb Halmfläche, halb oben: Büschel hat Licht- und Schattenseite

	tint = g.rgb * (0.9 + 0.4 * hash(cell + 2.0)) * COLOR.r;
	dry = COLOR.g;
	if (flowers) {
		float k = hash(cell + 19.0) * 5.0;
		vec3 c = k < 1.0 ? vec3(0.95, 0.95, 0.9) : k < 2.0 ? vec3(1.0, 0.75, 0.05) : k < 3.0 ? vec3(0.8, 0.05, 0.04) : k < 4.0 ? vec3(0.25, 0.35, 0.95) : vec3(0.55, 0.25, 0.8);
		vec3 mid = k < 2.0 && k >= 1.0 ? vec3(0.55, 0.3, 0.05) : vec3(1.0, 0.8, 0.15);  // Blütenmitte
		tint = head > 0.75 ? c : head > 0.25 ? mid : vec3(0.12, 0.3, 0.08);
	}
}

void fragment() {
	// Fuß dunkel (zwischen den Halmen kommt kaum Licht hin) -> Tiefe; einzelne Halme strohig
	vec3 c = mix(tint, tint * vec3(1.25, 1.05, 0.55), dry * 0.8);
	ALBEDO = flowers ? tint : c * mix(0.35, 1.15, smoothstep(0.0, 0.7, tip));
	ROUGHNESS = 0.85;
	BACKLIGHT = vec3(0.25);  // Gegenlicht scheint durch die Halme
}
