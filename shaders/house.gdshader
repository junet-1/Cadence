shader_type spatial;
// Blender-Häuser als MultiMesh. Instanzfarbe = Wandfarbe + Zufallswert (a), INSTANCE_CUSTOM = Dachfarbe.
// Glas: UV.x = Fensternummer -> nachts zufällig beleuchtet.
#include "res://shaders/noise.gdshaderinc"

global uniform float night;
uniform int part = 0;  // 0 Wand, 1 Dach, 2 Glas

varying vec4 inst_color;
varying vec4 inst_custom;
varying vec3 wpos;

void vertex() {
	inst_color = COLOR;
	inst_custom = INSTANCE_CUSTOM;
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float n = noise(wpos.xz * 1.7 + wpos.y * 0.9);
	if (part == 0) {
		ALBEDO = inst_color.rgb * (0.86 + 0.14 * n);
		ROUGHNESS = 0.9;
	} else if (part == 1) {
		float rows = smoothstep(0.0, 0.12, fract(wpos.y * 3.5));
		ALBEDO = inst_custom.rgb * (0.7 + 0.2 * rows + 0.15 * n);
		ROUGHNESS = 0.75;
	} else {
		float lit = step(0.55, hash(vec2(UV.x * 1.37, inst_color.a * 97.0))) * night;
		ALBEDO = mix(vec3(0.02, 0.025, 0.035), vec3(0.3, 0.2, 0.1), lit);
		ROUGHNESS = 0.05;
		SPECULAR = 0.9;
		EMISSION = vec3(1.0, 0.62, 0.28) * lit * 4.0;
	}
}
