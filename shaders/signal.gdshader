shader_type spatial;
// Ampeln (signs.py) auf MultiMesh. COLOR.a: 0 Gehäuse/Mast, 0.25 rot, 0.5 gelb, 0.75 grün. Umlauf 36 s
// (15 grün, 3 gelb, 15 rot, 3 rot-gelb), jede Ampel mit eigener Phase aus ihrer Position. Nur Optik, kein Halten.

varying float phase;

void vertex() {
	vec2 o = MODEL_MATRIX[3].xz;
	phase = fract(sin(dot(o, vec2(12.9898, 78.233))) * 43758.5453) * 36.0;
}

void fragment() {
	float id = COLOR.a;
	ALBEDO = COLOR.rgb;
	ROUGHNESS = 0.6;
	if (id > 0.1) {
		float t = mod(TIME + phase, 36.0);
		float red = step(18.0, t);
		float yellow = step(15.0, t) * step(t, 18.0) + step(33.0, t);
		float green = step(t, 15.0);
		float on = id < 0.4 ? red : id < 0.6 ? yellow : green;
		ALBEDO = COLOR.rgb * mix(0.08, 0.4, on);
		EMISSION = COLOR.rgb * on * 6.0;
		ROUGHNESS = 0.3;
	}
}
