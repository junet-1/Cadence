shader_type spatial;
// Fahrbahn und Wege. UV.x = quer in m (links +), UV.y = längs in m, UV2.x = halbe Breite, UV2.y = -1 - Belag (0 glatt,
// 1 Pflaster, 2 unbefestigt; alte Pakete haben dort die halbe Breite > 0 -> glatt), COLOR.rgb = Grundfarbe,
// COLOR.a = Markierung (0 keine, ⅓ Mittellinie, ⅔ Randlinien, 1 beides).
// Bankette/Brücken: COLOR.a = 0. Muster werden in der Ferne zum Mittelwert ausgeblendet statt zu flimmern.
// CUSTOM0 (signs.lane_marks, fehlt bei alten Paketen = 0): x Randlinie links (UV.x < 0) unterbrochen (Einmündung),
// y rechts unterbrochen, z Mittellinie durchgezogen (vor Ampeln).
#include "res://shaders/noise.gdshaderinc"
#include "res://shaders/clouds.gdshaderinc"

global uniform vec3 sun_dir;
global uniform float wetness;  // Regen: dunkler Asphalt, spiegelt (Pfützen in den Fahrspuren zuerst)
global uniform float heat;  // Hitze: Luftspiegelung in der Ferne (Asphalt spiegelt den Himmel bei flachem Blick)

varying vec3 wpos;
varying vec4 lane;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	lane = CUSTOM0;
}

void fragment() {
	float u = UV.x;
	float v = UV.y;
	float hw = UV2.x;
	float surface = UV2.y < -0.5 ? floor(-1.0 - UV2.y + 0.5) : 0.0;
	float n = noise(wpos.xz * 2.0) * 0.5 + noise(wpos.xz * 12.0) * 0.3 + noise(wpos.xz * 40.0) * 0.2;
	vec2 fw = max(fwidth(vec2(u, v)), vec2(0.002));
	float inside = step(abs(u), hw);  // Fahrbahn (nicht Bankett)
	vec3 col = COLOR.rgb * (0.75 + 0.5 * n);
	float rough = 0.88 - 0.1 * n;

	if (surface < 0.5) {
		// Asphalt: Fahrspuren der Autos etwas dunkler und glatter
		float wheel = (1.0 - smoothstep(0.25, 0.6, abs(abs(u) - hw * 0.5))) * inside * step(1.6, hw);
		col *= 1.0 - 0.12 * wheel;
		rough -= 0.15 * wheel;
	} else if (surface < 1.5) {
		// Pflaster: versetzte Steinreihen, Fugen dunkel und rau, jeder Stein leicht anders
		vec2 size = vec2(0.22, 0.16);
		float row = floor(v / size.y);
		vec2 c = vec2(u / size.x + 0.5 * mod(row, 2.0), v / size.y);
		vec2 f = fract(c);
		vec2 g = min(f, 1.0 - f);
		vec2 fws = fw / size;
		float stone = smoothstep(0.06, 0.06 + fws.x * 1.5, g.x) * smoothstep(0.08, 0.08 + fws.y * 1.5, g.y);
		float detail = clamp(1.0 - max(fws.x, fws.y) * 2.5, 0.0, 1.0);
		float h = hash(floor(c) + 3.1);
		vec3 sett = COLOR.rgb * (0.8 + 0.4 * h) * (0.9 + 0.2 * n);
		vec3 grout = COLOR.rgb * 0.45;
		vec3 far = COLOR.rgb * 0.85;  // Mittelwert aus Stein und Fuge für die Ferne
		col = mix(col, mix(far, mix(grout, sett, stone), detail), inside);
		rough = mix(0.8, mix(0.95, 0.62 + 0.15 * h, stone), detail);
		col *= mix(1.0, 0.9 + 0.2 * (1.0 - length(f - 0.5)), detail * stone * inside);  // Steine zur Mitte heller: leichte Wölbung
	} else {
		// Unbefestigt: Körnung, zwei festgefahrene Spuren, schmale Wege mit Grasstreifen in der Mitte
		float grain = noise(wpos.xz * 30.0) * 0.6 + noise(wpos.xz * 90.0) * 0.4;
		float detail = clamp(1.0 - max(fw.x, fw.y) * 8.0, 0.0, 1.0);
		col *= mix(1.0, 0.8 + 0.4 * grain, detail);
		float rut = (1.0 - smoothstep(0.2, 0.45, abs(abs(u) - min(hw * 0.45, 0.8)))) * inside * step(hw, 999.0);  // Kreuzungsflächen (UV2.x = 1000): keine Spuren
		col *= 1.0 - 0.1 * rut;
		float strip = (1.0 - smoothstep(0.15, 0.35, abs(u))) * step(hw, 2.6) * inside;
		col = mix(col, vec3(0.20, 0.30, 0.11) * (0.8 + 0.4 * grain), strip * 0.8);
		float fray = smoothstep(hw - 0.5, hw, abs(u) + (noise(wpos.xz * 3.0) - 0.5) * 0.6);  // Ränder franzen aus
		col = mix(col, col * 0.75 + vec3(0.06, 0.08, 0.02), fray * inside);
		rough = 0.95 - 0.08 * rut;
	}

	// Markierung: ⅓ Mittellinie, ⅔ Randlinien, 1 beides; nur auf der Fahrbahn
	float a = COLOR.a;
	float has_center = step(0.2, a) * (step(a, 0.5) + step(0.85, a));
	float has_edge = step(0.5, a);
	float edge = 1.0 - smoothstep(0.06, 0.06 + fw.x, abs(abs(u) - (hw - 0.35)));
	float broken = step(0.5, u < 0.0 ? lane.x : lane.y);  // an Einmündungen: 1 m Strich, 1 m Lücke
	edge *= 1.0 - broken * step(0.5, fract(v / 2.0));
	float center = (1.0 - smoothstep(0.05, 0.05 + fw.x, abs(u))) * max(step(fract(v / 9.0), 0.45), step(0.5, lane.z));
	float mark = max(edge * has_edge, center * has_center) * inside * step(surface, 0.5);
	float puddle = smoothstep(0.45, 0.75, noise(wpos.xz * 0.35) + wetness * 0.4) * wetness;
	col *= 1.0 - 0.45 * wetness;
	rough = mix(rough, 0.12, max(wetness * 0.6, puddle));
	col *= 1.0 - 0.5 * cloud_shadow(wpos, sun_dir);
	// Luftspiegelung: weit weg, fast streifender Blick, flackernde Flecken -> spiegelt wie Wasser
	float graze = 1.0 - smoothstep(0.015, 0.07, dot(NORMAL, VIEW));
	float flick = smoothstep(0.35, 0.7, noise(wpos.xz * vec2(0.08, 0.08) + vec2(0.0, TIME * 0.6)) + 0.15 * sin(TIME * 7.0 + wpos.x));
	float mirage = heat * smoothstep(90.0, 300.0, length(VERTEX)) * graze * flick * inside;
	col *= 1.0 - 0.8 * mirage;
	rough = mix(rough, 0.02, mirage);
	ALBEDO = mix(col, vec3(0.8) * (1.0 - 0.3 * wetness), mark * (1.0 - mirage));
	ROUGHNESS = mix(rough, 0.55 - 0.3 * wetness, mark * (1.0 - mirage));
	SPECULAR = mix(0.5, 1.0, mirage);
}
