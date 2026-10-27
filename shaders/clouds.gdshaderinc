// Wolkenschicht für den Himmel (sky.gdshader) und Wolkenschatten am Boden (terrain/road): gleiche Dichte am gleichen Ort.
// clouds = (Versatz x, Versatz z in m – wandert mit dem Wind, Bedeckung 0…1, Höhe der Schicht über dem Fahrer in m), weather.gd
global uniform vec4 clouds;

float c_hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float c_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(c_hash(i), c_hash(i + vec2(1.0, 0.0)), f.x), mix(c_hash(i + vec2(0.0, 1.0)), c_hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

float cloud_density(vec2 xz) {
	// Haufenwolken ~1–3 km, ausgefranst; Bedeckung verschiebt die Schwelle (0 = einzelne Fetzen, 1 = geschlossen)
	vec2 p = (xz + clouds.xy) * 0.00032;
	vec2 w = vec2(c_noise(p * 0.5 + 3.1), c_noise(p * 0.5 - 7.3)) * 1.6;  // verwirbelt: keine Rasterblasen
	p += w;
	float n = c_noise(p) * 0.5 + c_noise(p * 2.03 + 3.7) * 0.25 + c_noise(p * 4.1 - 1.3) * 0.15 + c_noise(p * 8.7 + 7.1) * 0.1;
	float t = mix(0.78, 0.2, clouds.z);
	return smoothstep(t, t + 0.22, n);
}

float cloud_shadow(vec3 wpos, vec3 to_sun) {
	// Schatten am Boden: Dichte dort, wo der Strahl zur Sonne die Wolkenschicht trifft
	float t = clouds.w / max(to_sun.y, 0.08);
	return cloud_density(wpos.xz + to_sun.xz * t) * smoothstep(-0.02, 0.1, to_sun.y);
}
