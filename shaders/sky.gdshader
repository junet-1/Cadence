shader_type sky;
render_mode use_half_res_pass;
// Himmel mit Wetter (weather.gd): Verlauf Zenit/Horizont nach Sonnenstand, Sonnenscheibe und Mie-Hof, Dunst (Trockengebiete
// gelblich), ziehende Haufenwolken (clouds.gdshaderinc, gleiche Dichte wie die Wolkenschatten am Boden) und Schleierwolken,
// Regen macht alles grauer, Sterne nachts. Wolken in halber Auflösung, die Radiance-Cubemap (Umgebungslicht) rechnet sie mit.
#include "res://shaders/clouds.gdshaderinc"

global uniform float night;
uniform float haze = 0.0;  // 0 klar … 1 Wüstendunst
uniform vec3 haze_color : source_color = vec3(0.85, 0.72, 0.52);
uniform float rain = 0.0;
uniform float energy = 1.5;  // Himmelshelligkeit (im Spiel abgeglichen mit AgX und Sonne 1,3)
uniform sampler2D stars : filter_linear, repeat_enable;

vec3 to_sun() {
	return LIGHT0_ENABLED ? LIGHT0_DIRECTION : vec3(0.0, -1.0, 0.0);  // im Sky-Shader zeigt LIGHT0_DIRECTION zur Sonne
}

vec3 sky_col(vec3 d, vec3 s) {
	float se = s.y;
	float day = smoothstep(-0.18, 0.12, se);
	float gold = (1.0 - smoothstep(0.0, 0.3, se)) * smoothstep(-0.2, 0.0, se);  // Dämmerung
	// Zenit tiefblau, Horizont hell und dunstig (Sommer am Mittelmeer); Trockengebiete: warmer, staubiger Horizont
	// wenig gesättigt rechnen: AgX macht aus reinem Blau Marineblau
	vec3 zenith = mix(vec3(0.004, 0.006, 0.02), vec3(0.16, 0.38, 0.92), day);
	vec3 horizon = mix(vec3(0.01, 0.012, 0.03), vec3(0.56, 0.70, 0.88), day);
	horizon = mix(horizon, haze_color * 0.95, haze * 0.7 * day);
	zenith = mix(zenith, zenith * vec3(1.15, 1.05, 0.9), haze * day);
	horizon = mix(horizon, vec3(0.9, 0.42, 0.15), gold * 0.8 * pow(max(dot(normalize(d.xz + 1e-4), normalize(s.xz + 1e-4)), 0.0), 2.0));
	vec3 col = mix(horizon, zenith, pow(clamp(d.y, 0.0, 1.0), 0.35));
	col = d.y < 0.0 ? horizon * mix(0.9, 0.55, clamp(-d.y * 4.0, 0.0, 1.0)) : col;
	float mu = max(dot(d, s), 0.0);
	col += vec3(1.0, 0.7, 0.4) * (pow(mu, 8.0) * 0.35 + pow(mu, 64.0) * 0.6) * mix(0.4, 1.0, haze) * day;
	float grey = dot(col, vec3(0.3, 0.5, 0.2));  // bedeckt/Regen: grau, dunkler
	float dull = smoothstep(0.6, 1.0, clouds.z);
	col = mix(col, vec3(grey) * mix(1.0, 0.7, rain), dull * 0.85);
	return col * energy;
}

vec4 cloud_col(vec3 d, vec3 s, vec3 cam, vec3 skyc) {
	if (d.y <= 0.0) {
		return vec4(0.0);
	}
	float t = clouds.w / (d.y + 0.025);  // flache Schicht, am Horizont flach gestreckt
	vec2 p = cam.xz + d.xz * t;
	float dens = cloud_density(p);
	// Schleierwolken hoch oben: in Windrichtung gestreckt, auch bei klarem Himmel ein wenig
	vec2 q = (cam.xz + d.xz * (8000.0 / (d.y + 0.025)) + clouds.xy * 2.0) * vec2(0.00012, 0.00045);
	float cir = smoothstep(0.55, 0.9, c_noise(q) * 0.7 + c_noise(q * 3.1) * 0.3) * 0.25 * (1.0 - clouds.z * 0.5);
	cir *= smoothstep(0.5, 0.8, c_noise(q * vec2(0.08, 0.025) + 11.0));  // nur stellenweise: dazwischen klares Blau
	float a = max(dens, cir) * smoothstep(0.0, 0.08, d.y);
	if (a < 0.003) {
		return vec4(0.0);
	}
	// Licht: Dichte ein Stück Richtung Sonne = Selbstschatten; Rand hell (Silberrand), Unterseite dunkel
	float toward = cloud_density(p + s.xz / max(s.y, 0.12) * 220.0);
	float lit = exp(-toward * 2.0) * (1.0 - 0.45 * dens);
	vec3 sunc = LIGHT0_ENABLED ? LIGHT0_COLOR * clamp(LIGHT0_ENERGY, 0.0, 1.5) : vec3(0.0);
	float day = smoothstep(-0.18, 0.1, s.y);
	vec3 amb = mix(vec3(0.02, 0.025, 0.04), vec3(0.32, 0.36, 0.44), day) * mix(1.0, 0.55, rain);
	vec3 col = (amb + sunc * lit * 0.9 * mix(1.0, 0.45, rain)) * energy * 0.65;
	col += sunc * pow(max(dot(d, s), 0.0), 10.0) * (1.0 - dens) * 0.8;  // Silberrand gegen die Sonne
	col = mix(col, skyc, 1.0 - exp(-t / 45000.0));  // ferne Wolken verschwinden im Dunst
	return vec4(col, a);
}

void sky() {
	vec3 s = to_sun();
	vec3 skyc = sky_col(EYEDIR, s);
	if (AT_HALF_RES_PASS) {
		vec4 c = cloud_col(EYEDIR, s, POSITION, skyc);
		COLOR = c.rgb;
		ALPHA = c.a;
	} else {
		vec3 col = skyc;
		if (!AT_CUBEMAP_PASS) {
			float mu = dot(EYEDIR, s);
			vec3 sunc = LIGHT0_ENABLED ? LIGHT0_COLOR * LIGHT0_ENERGY : vec3(0.0);
			col += sunc * smoothstep(0.99955, 0.9998, mu) * 30.0;  // Sonnenscheibe (nicht ins Umgebungslicht)
			vec2 uv = vec2(atan(EYEDIR.x, EYEDIR.z) / 6.2832 + 0.5, acos(clamp(EYEDIR.y, -1.0, 1.0)) / 3.1416);
			col += texture(stars, uv).rgb * night * (1.0 - clouds.z) * step(0.0, EYEDIR.y);
			col = mix(col, HALF_RES_COLOR.rgb, HALF_RES_COLOR.a);
		} else {
			vec4 c = cloud_col(EYEDIR, s, POSITION, skyc);
			col = mix(col, c.rgb, c.a);
		}
		COLOR = col;
	}
}
