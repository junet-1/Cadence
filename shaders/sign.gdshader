shader_type spatial;
render_mode cull_disabled;
// Verkehrsschilder (signs.py): Quad 1 × 1 m pro Instanz, INSTANCE_CUSTOM = UV-Rechteck im Atlas (u0, v0, u1, v1).
// Vorderseite: Schildgesicht, nachts leicht rückstrahlend; Rückseite: graues Blech in derselben Form (Alpha des Atlas).

global uniform float night;
uniform sampler2D atlas : source_color, filter_linear_mipmap, repeat_disable;

varying vec4 rect;

void vertex() {
	rect = INSTANCE_CUSTOM;
}

void fragment() {
	vec4 c = texture(atlas, mix(rect.xy, rect.zw, UV));
	if (c.a < 0.5) {
		discard;
	}
	if (FRONT_FACING) {
		ALBEDO = c.rgb;
		ROUGHNESS = 0.45;
		EMISSION = c.rgb * night * 0.35;
	} else {
		ALBEDO = vec3(0.42, 0.43, 0.45);
		ROUGHNESS = 0.6;
	}
}
