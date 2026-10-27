shader_type spatial;
// Boden: Biom-Farbe (Textur bei echten Strecken, Vertexfarbe in der Workout-Welt) + Materialschichten aus world.py/terrain.py:
// UV = (Nässe aus dem Abfluss, Sediment/Schwemmland), UV2 = (Fels freigelegt, Geröll/Schutthalde). Echte Strecken haben kein UV
// -> 0: dort wie bisher Fels nur an steilen Hängen. Fels triplanar mit Schichtbändern, großräumige Helligkeit gegen Wiederholung,
// Schnee über snow_line (flach, nicht in Wänden), Wasser (Alpha < 1) spiegelt.
#include "res://shaders/noise.gdshaderinc"
#include "res://shaders/clouds.gdshaderinc"

global uniform float snow_line;
global uniform vec3 sun_dir;  // zur Sonne (Wolkenschatten)
global uniform float wetness;  // Regen: Boden dunkler, glänzt etwas  // Welt-y, main.gd rechnet sie aus Klima + Monat

uniform sampler2D ground : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform vec4 rect;  // x0, z0, Breite, Tiefe in m
uniform bool vertex_ground = false;  // Workout-Welt (world.py): Bodenfarbe + Wuchscode als Vertexfarbe statt Textur

varying vec3 wpos;
varying vec3 wnrm;
varying vec4 vcol;
varying vec4 mat;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	wnrm = (MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz;
	vcol = COLOR;
	mat = vec4(UV, UV2);
}

float fbm3(vec2 p) {
	return noise(p) * 0.5 + noise(p * 2.03 + 7.1) * 0.3 + noise(p * 4.1 - 3.7) * 0.2;
}

float tri(vec3 p, vec3 n, float f) {
	// triplanar: Wände bekommen Struktur aus ihrer eigenen Ebene statt gestreckter Draufsicht-Muster
	vec3 w = pow(abs(n), vec3(4.0));
	w /= w.x + w.y + w.z;
	return fbm3(p.zy * f) * w.x + fbm3(p.xz * f) * w.y + fbm3(p.xy * f) * w.z;
}

void fragment() {
	vec4 g = vertex_ground ? vcol : texture(ground, (wpos.xz - rect.xy) / rect.zw);
	vec3 nrm = normalize(wnrm);
	float n = noise(wpos.xz * 0.05) * 0.45 + noise(wpos.xz * 0.4) * 0.35 + noise(wpos.xz * 2.5) * 0.2;
	float macro = noise(wpos.xz * 0.0041) * 0.6 + noise(wpos.xz * 0.013 + 3.3) * 0.4;
	vec3 col = g.rgb * (0.7 + 0.6 * n) * (0.86 + 0.28 * macro);

	// Abflussrinnen dunkler/satter, Schwemmland heller (Kies, Sand)
	col *= 1.0 - 0.3 * mat.x;
	float sed = clamp(mat.y * (0.6 + 0.8 * noise(wpos.xz * 0.21)), 0.0, 1.0);
	vec3 gravel = mix(vec3(0.27, 0.25, 0.22), g.rgb, 0.5) * (0.75 + 0.5 * noise(wpos.xz * 1.7));
	col = mix(col, mix(gravel, g.rgb * 1.1, 0.35), sed * 0.55);
	// Schutthalden: grauer Grus mit Brocken
	float scree = smoothstep(0.25, 0.8, mat.w + (noise(wpos.xz * 0.15) - 0.5) * 0.5);
	float speck = noise(wpos.xz * 3.1) * 0.6 + noise(wpos.xz * 9.0) * 0.4;
	col = mix(col, vec3(0.24, 0.23, 0.21) * (0.6 + 0.7 * speck), scree * 0.85);
	// Fels: steil oder freigelegt; Farbe vom Biom (Vertexfarbe = Fels der Gegend, z. B. Sandstein), sonst Granitgrau
	float rock = max(smoothstep(0.82, 0.62, nrm.y), smoothstep(0.35, 0.75, mat.z + (n - 0.5) * 0.3));
	float t = tri(wpos, nrm, 0.13);
	float band = noise(vec2(wpos.y * 0.35 + noise(wpos.xz * 0.008) * 5.0, 0.5));  // Schichtbänder, leicht verbogen
	vec3 rockc = mix(vec3(0.17, 0.16, 0.145), g.rgb, smoothstep(0.3, 0.8, mat.z));
	float ribs = tri(wpos, nrm, 0.021);  // Rippen und Flecken im 50-m-Maßstab: Wände bleiben auch in der Ferne gegliedert
	col = mix(col, rockc * (0.65 + 0.6 * t) * (0.55 + 0.75 * ribs) * (0.85 + 0.3 * band), rock);

	float water = 1.0 - smoothstep(0.52, 0.58, g.a);  // Alpha 128 = Wasser, darüber = Vegetationsklassen
	if (vertex_ground) {  // Workout-Welt: Wasser ist eine eigene Fläche (water.gdshader) -> hier nur der Grund (Sand/Kies)
		col = mix(col, vec3(0.32, 0.29, 0.22) * (0.8 + 0.4 * n), water);
		water = 0.0;
	}
	float snow = smoothstep(snow_line - 40.0, snow_line + 40.0, wpos.y + (n - 0.5) * 120.0) * smoothstep(0.6, 0.85, nrm.y);
	col = mix(col, vec3(0.88, 0.9, 0.94) * (0.92 + 0.08 * n), snow * (1.0 - water));
	float tt = TIME * 0.5;
	vec2 rip = vec2(noise(wpos.xz * 0.8 + tt) - noise(wpos.xz * 0.8 - tt + 3.7), noise(wpos.xz * 1.1 + vec2(tt, -tt)) - noise(wpos.zx * 1.1 - tt));
	col *= 1.0 - 0.28 * wetness * (1.0 - snow);
	col *= 1.0 - 0.55 * cloud_shadow(wpos, sun_dir);  // ziehende Wolkenschatten (gleiche Wolken wie am Himmel)
	ALBEDO = mix(col, vec3(0.02, 0.05, 0.08), water);
	ROUGHNESS = mix(mix(0.95, 0.8, rock) - 0.15 * mat.x - 0.3 * wetness, 0.04, water);
	SPECULAR = mix(0.2, 0.9, water);
	NORMAL_MAP = mix(vec3(0.5, 0.5, 1.0), normalize(vec3(rip * 0.35, 1.0)) * 0.5 + 0.5, water);
}
