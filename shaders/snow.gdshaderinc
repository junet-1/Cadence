// Schneedecke 0…1, gleiche Regel für Gelände, Bankett und Gras (braucht noise.gdshaderinc).
// Höhe über snow_line mit großräumig welliger Grenze; Nordhänge (Normale −z) halten Schnee tiefer, Südhänge apern früher aus;
// flach hält mehr, steile Flanken nur weit über der Grenze (dort dann in Rinnen/Flecken statt als Decke).
float snow_depth(vec3 p, vec3 nrm, float line) {
	float wave = (noise(p.xz * 0.004) - 0.5) * 140.0 + (noise(p.xz * 0.05) - 0.5) * 60.0;
	return p.y - line + wave - 90.0 * nrm.z;
}

float snow_cover(vec3 p, vec3 nrm, float line) {
	float d = snow_depth(p, nrm, line);
	float deep = smoothstep(0.0, 250.0, d);
	return smoothstep(-40.0, 40.0, d) * smoothstep(0.62 - 0.17 * deep, 0.86 - 0.2 * deep, nrm.y);
}
