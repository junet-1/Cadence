shader_type spatial;
render_mode unshaded, fog_disabled, depth_test_disabled, cull_disabled;
// Hitzeflimmern (Vollbild-Quad vor der Kamera): in der Ferne knapp über dem Boden (Sichtstrahl fast waagrecht) wabert das Bild
// nach oben. Stärke = global heat (weather.gd: Sonne hoch, klar, trocken, warm). Wo nichts flimmert ALPHA 0 -> Regen,
// Partikel usw. bleiben unberührt.
#include "res://shaders/noise.gdshaderinc"

global uniform float heat;
uniform sampler2D screen : hint_screen_texture, filter_linear_mipmap;
uniform sampler2D depth_tex : hint_depth_texture, filter_nearest;

void vertex() {
	POSITION = vec4(VERTEX.xy, 1.0, 1.0);
}

void fragment() {
	float z = texture(depth_tex, SCREEN_UV).x;
	vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, z, 1.0);
	view.xyz /= view.w;
	vec3 dir = normalize((INV_VIEW_MATRIX * vec4(view.xyz, 0.0)).xyz);
	float dist = length(view.xyz);
	float amt = heat * smoothstep(70.0, 450.0, dist) * (1.0 - smoothstep(0.0, 0.07, abs(dir.y)));
	vec2 p = SCREEN_UV * vec2(90.0, 18.0) + vec2(0.0, TIME * 2.2);
	vec2 off = vec2(noise(p) - 0.5, noise(p * 1.7 + 9.1) - 0.5) * vec2(0.0025, 0.004) * amt;
	ALBEDO = textureLod(screen, SCREEN_UV + off, 0.0).rgb;
	ALPHA = smoothstep(0.0, 0.05, amt);
}
