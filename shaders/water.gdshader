shader_type spatial;
render_mode specular_schlick_ggx, cull_disabled;
// Wasser der Workout-Welt (world.py: eigene Fläche auf Spiegelhöhe, UV.x = Wassertiefe in m, < 0 am Ufer).
// Farbe nach Tiefe (flach türkis/grünlich, tief blau), Gischt an der Uferlinie, Wellen aus zwei wandernden Rauschfeldern in
// Windrichtung, Glanz/Fresnel macht die Engine (Himmel spiegelt, Sonne glitzert). warm: 0 Bergsee … 1 Mittelmeer.
#include "res://shaders/noise.gdshaderinc"

global uniform vec3 wind;
uniform float warm = 0.5;

varying vec3 wpos;
varying float depth;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	depth = UV.x;
}

vec2 waves(vec2 p, float t) {
	vec2 wd = normalize(wind.xy + vec2(1e-4, 0.0));
	float ws = 0.3 + wind.z;
	vec2 a = p * 0.35 - wd * t * 0.6 * ws;
	vec2 b = p * 0.9 + vec2(-wd.y, wd.x) * t * 0.4 - wd * t * 0.9 * ws;
	float e = 0.05;
	vec2 g1 = vec2(noise(a + vec2(e, 0.0)) - noise(a - vec2(e, 0.0)), noise(a + vec2(0.0, e)) - noise(a - vec2(0.0, e))) / (2.0 * e);
	vec2 g2 = vec2(noise(b + vec2(e, 0.0)) - noise(b - vec2(e, 0.0)), noise(b + vec2(0.0, e)) - noise(b - vec2(0.0, e))) / (2.0 * e);
	return (g1 * 0.6 + g2 * 0.4) * (0.25 + 0.5 * ws);
}

void fragment() {
	float d = max(depth, 0.0);
	vec3 shallow = mix(vec3(0.10, 0.28, 0.20), vec3(0.08, 0.55, 0.50), warm);
	vec3 deep = mix(vec3(0.008, 0.05, 0.06), vec3(0.004, 0.07, 0.20), warm);
	vec3 col = mix(shallow, deep, 1.0 - exp(-d * 0.35));
	// Gischt: schmaler, unruhiger Saum an der Uferlinie
	float n = noise(wpos.xz * 0.6 + TIME * 0.3) * 0.6 + noise(wpos.xz * 2.3 - TIME * 0.5) * 0.4;
	float foam = (1.0 - smoothstep(0.0, 0.5 + 0.4 * n, depth)) * smoothstep(0.35, 0.6, n + 0.25);
	col = mix(col, vec3(0.85, 0.9, 0.9), foam * 0.8);
	vec2 w = waves(wpos.xz, TIME);
	ALBEDO = col;
	ROUGHNESS = mix(0.03, 0.6, foam);
	SPECULAR = 0.6;
	NORMAL_MAP = normalize(vec3(-w * 0.35, 1.0)) * 0.5 + 0.5;
	NORMAL_MAP_DEPTH = 1.0;
}
