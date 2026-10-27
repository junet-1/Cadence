shader_type spatial;
#include "res://shaders/noise.gdshaderinc"
// Bäume (wiegen im Wind) und Laternen (COLOR.a = leuchtet nachts). Läuft auf MultiMesh, MODEL_MATRIX = Instanz.
// Blattkarten (blender/trees.py) haben UV.x in 1…2: Zweigbild aus dem Blatt-Atlas, Alpha schneidet aus; alles andere UV 0.

global uniform float night;
global uniform float snow_line;
global uniform vec3 wind;  // weather.gd: Richtung x/z, Stärke z
global uniform float wind_t;
global uniform vec2 wind_scroll;
uniform float sway = 0.0;
uniform sampler2D leaves : hint_default_white, filter_linear_mipmap_anisotropic, repeat_disable;  // models/leaves.png (main.leaf_atlas)

void vertex() {
	vec3 o = MODEL_MATRIX[3].xyz;
	float snow = smoothstep(snow_line - 40.0, snow_line + 40.0, o.y) * step(0.8, VERTEX.y) * smoothstep(0.1, 0.7, NORMAL.y);
	// Wind (weather.gd): Biegung wächst quadratisch mit der Höhe (Stamm unten steif, Krone weich), Grundneigung in
	// Windrichtung + Böen, die als Feld über das Land wandern, + langsames Schwingen je Baum; Krone flattert fein.
	// Alle Zeiten aus wind_t (stetig) statt TIME × Stärke -> kein Zucken, wenn Böen kommen und gehen.
	mat3 b = mat3(MODEL_MATRIX);
	vec3 wl = transpose(b) * vec3(wind.x, 0.0, wind.y) / max(dot(b[0], b[0]), 1e-4);
	vec2 wd = normalize(wl.xz + vec2(1e-4, 0.0));
	float ws = wind.z;
	float y = max(VERTEX.y - 1.0, 0.0);
	float bend = y * y / (y + 12.0) / 8.0 * sway;  // 10-m-Baum ~0,6, 24-m-Fichte ~2 (nicht quadratisch ins Unendliche)
	float gust = noise((o.xz - wind_scroll) * 0.025);
	float swing = sin(wind_t * 1.3 + o.x * 0.21 + o.z * 0.17);
	vec2 lean = wd * (0.15 * ws + gust * gust * 0.5 * ws + swing * (0.04 + 0.12 * ws));
	VERTEX.xz += lean * bend;
	float flutter = sin(wind_t * 7.0 + dot(VERTEX, vec3(1.3, 0.7, 1.1)) * 2.0) * (0.015 + 0.05 * ws);
	VERTEX += vec3(flutter, flutter * 0.5, -flutter) * smoothstep(2.0, 4.0, VERTEX.y) * sway;
	COLOR.rgb *= 0.75 + 0.5 * fract(sin(dot(o.xz, vec2(12.9898, 78.233))) * 43758.5453);
	COLOR.rgb = mix(COLOR.rgb, vec3(0.85, 0.88, 0.92), snow * 0.85 * (1.0 - COLOR.a));  // Schnee auf Kronen, nicht auf Laternen
}

void fragment() {
	vec3 c = COLOR.rgb;
	if (UV.x > 0.999) {
		vec2 uv = UV - vec2(1.0, 0.0);
		vec4 t = texture(leaves, uv);
		// Mip-Stufen mitteln das Alpha weg -> in der Ferne lösen sich Karten auf; mit der Stufe anheben hält die Krone dicht
		if (t.a * (1.0 + 0.4 * max(textureQueryLod(leaves, uv).x, 0.0)) < 0.5) {
			discard;
		}
		c *= t.rgb * 1.55;  // Atlas ist grau (Mittel ~0,64): Artfarbe bleibt im Mittel erhalten
	}
	ALBEDO = c;
	ROUGHNESS = 0.9;
	EMISSION = vec3(1.0, 0.75, 0.45) * COLOR.a * night * 8.0;
}
