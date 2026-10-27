shader_type spatial;
// Wände mit Fensterraster (nachts zufällig beleuchtet) oder Ziegeldach.
// Wand-UV: x = m entlang des Umrisses, y = m über Fundament. UV2 = (Erdgeschoss-Höhe, Traufhöhe). COLOR.a = Zufallswert pro Haus.
#include "res://shaders/noise.gdshaderinc"

global uniform float night;
uniform bool roof = false;

varying vec3 wpos;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float n = noise(wpos.xz * 1.7 + wpos.y * 0.9);
	if (roof) {
		float rows = smoothstep(0.0, 0.12, fract(wpos.y * 3.5));
		ALBEDO = COLOR.rgb * (0.7 + 0.2 * rows + 0.15 * n);
		ROUGHNESS = 0.75;
	} else {
		vec2 cell = vec2(UV.x / 3.0, (UV.y - UV2.x - 0.9) / 3.0);
		vec2 f = fract(cell);
		vec2 id = floor(cell);
		vec2 fw = fwidth(cell);
		float box = smoothstep(0.28, 0.28 + fw.x, f.x) * (1.0 - smoothstep(0.72 - fw.x, 0.72, f.x))
			* smoothstep(0.22, 0.22 + fw.y, f.y) * (1.0 - smoothstep(0.82 - fw.y, 0.82, f.y));
		float band = step(0.0, id.y) * step(UV2.x + 0.9 + (id.y + 0.85) * 3.0, UV2.y - 0.3);  // nur zwischen EG und Traufe
		float detail = clamp(1.0 - max(fw.x, fw.y) * 3.0, 0.0, 1.0);  // weit weg: Fenster ausblenden statt flimmern
		float win = box * band * detail;
		float lit = step(0.55, hash(id + COLOR.a * 97.0)) * night;
		vec3 wall = COLOR.rgb * (0.85 + 0.15 * n);
		ALBEDO = mix(wall, mix(vec3(0.02, 0.025, 0.035), vec3(0.3, 0.2, 0.1), lit), win);
		ROUGHNESS = mix(0.9, 0.08, win);
		vec3 warm = vec3(1.0, 0.62, 0.28);
		EMISSION = warm * (lit * win * 4.0 + band * (1.0 - detail) * night * 0.35);
	}
}
