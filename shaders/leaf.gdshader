shader_type spatial;
render_mode cull_disabled;
// Herbstblatt-Partikel: trudelt um zwei Achsen, Form per Ellipse ausgeschnitten, Farbe zufällig pro Blatt.
#include "res://shaders/noise.gdshaderinc"

uniform float autumn = 1.0;  // 0 Sommer (grüne Blätter) … 1 Herbst (main.gd)
varying vec3 tint;

void vertex() {
	float h = hash(vec2(float(INSTANCE_ID), 1.7));
	float t = TIME * (2.0 + 3.0 * h) + h * 40.0;
	mat3 rx = mat3(vec3(1, 0, 0), vec3(0, cos(t), sin(t)), vec3(0, -sin(t), cos(t)));
	float u = t * 0.7;
	mat3 ry = mat3(vec3(cos(u), 0, -sin(u)), vec3(0, 1, 0), vec3(sin(u), 0, cos(u)));
	VERTEX = ry * rx * VERTEX;
	NORMAL = ry * rx * NORMAL;
	float k = hash(vec2(float(INSTANCE_ID), 9.3)) * 4.0;
	tint = k < 1.0 ? vec3(0.9, 0.45, 0.05) : k < 2.0 ? vec3(0.95, 0.75, 0.1) : k < 3.0 ? vec3(0.7, 0.12, 0.05) : vec3(0.4, 0.22, 0.08);
	vec3 green = k < 2.0 ? vec3(0.18, 0.36, 0.08) : k < 3.5 ? vec3(0.32, 0.45, 0.12) : vec3(0.45, 0.42, 0.16);  // Sommer: grün, wenige welke
	tint = mix(green, tint, autumn);
}

void fragment() {
	vec2 p = (UV - 0.5) * vec2(1.0, 1.7);
	if (length(p) > 0.5) {
		discard;
	}
	float vein = 1.0 - smoothstep(0.0, 0.04, abs(UV.x - 0.5)) * 0.25;
	ALBEDO = tint * vein;
	ROUGHNESS = 0.7;
	BACKLIGHT = tint * 0.5;
}
