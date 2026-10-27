extends SceneTree
## Selbsttest der Workout-Ausführung in main.gd (ohne Fenster, ohne Trainer):
##   godot --headless --path . -s res://selftest.gd

const Main := preload("res://main.gd")


func _init() -> void:
	var w := {"duration": 600.0, "segments": [
		{"t0": 0.0, "dur": 300.0, "p0": 0.5, "p1": 0.7, "kind": "warmup", "free": false},
		{"t0": 300.0, "dur": 200.0, "p0": 1.2, "p1": 1.2, "kind": "vo2", "free": false},
		{"t0": 500.0, "dur": 100.0, "p0": 0.0, "p1": 0.0, "kind": "free", "free": true}]}
	# Ziel: linear im Einfahren, konstant im Intervall, frei/vorbei = 0
	assert(is_equal_approx(Main.target_frac(w, 150.0), 0.6))
	assert(is_equal_approx(Main.target_frac(w, 350.0), 1.2))
	assert(Main.target_frac(w, 550.0) == 0.0 and Main.target_frac(w, 700.0) == 0.0 and Main.target_frac({}, 10.0) == 0.0)
	# Genau ein Steuerbefehl: ERG nur mit Ziel, sonst SIM – nie beides
	assert(Main.trainer_command(true, 288.4, 5.0) == {"erg": 288})
	assert(Main.trainer_command(true, 0.0, 5.0) == {"grade": 5.0})
	assert(Main.trainer_command(false, 288.0, -2.0) == {"grade": -2.0})
	for erg_on in [true, false]:
		for watts in [0.0, 150.0]:
			assert(Main.trainer_command(erg_on, watts, 1.0).size() == 1)
	# Workout-Uhr: läuft beim Treten, steht in der Pause; Tempo/Strecke gehen nicht ein (kein Parameter)
	var t := 0.0
	for i in 600:  # 10 s treten, 60 fps
		t = Main.advance_workout(t, 1.0 / 60.0, 200.0, 0.0)
	assert(is_equal_approx(t, 10.0))
	for i in 600:  # 10 s Pause
		t = Main.advance_workout(t, 1.0 / 60.0, 0.0, 0.0)
	assert(is_equal_approx(t, 10.0))
	t = Main.advance_workout(t, 0.5, 0.0, 80.0)  # Trittfrequenz ohne Leistungswert (manche Trainer) zählt auch
	assert(is_equal_approx(t, 10.5))
	# Wind: Gegenwind bremst, Rückenwind schiebt (schneller als das Rad -> Kraft nach vorn); Wetter deterministisch je Seed/Zeit
	const W := preload("res://weather.gd")
	assert(W.air_force(10.0, 5.0, 1.2, 0.32) > W.air_force(10.0, 0.0, 1.2, 0.32))
	assert(W.air_force(10.0, 0.0, 1.2, 0.32) > W.air_force(10.0, -5.0, 1.2, 0.32))
	assert(W.air_force(2.0, -6.0, 1.2, 0.32) < 0.0)
	assert(W.state(7, 1234567.0, 0.2, 0) == W.state(7, 1234567.0, 0.2, 0))
	assert(W.state(7, 1234567.0, 0.2, 0) != W.state(8, 1234567.0, 0.2, 0))
	for m in range(0, 20000, 37):  # Wüste: nie Regen; "Regen" erzwingt Regen
		assert(W.state(3, float(m), 1.0, 0).rain == 0.0)
	assert(W.state(3, 100.0, 1.0, 3).rain > 0.5 and W.state(3, 100.0, 0.0, 4).wind > 12.0)
	# Auswertung: 25 min 200 W konstant + 5 min 300 W -> Mittel, NP, Bestwerte, FTP-Schätzung, Zonen, Intervalle, TCX gültig
	var L := preload("res://ride_log.gd").new()
	var wo := {"segments": [{"t0": 0.0, "dur": 1500.0, "kind": "endurance", "free": false}, {"t0": 1500.0, "dur": 300.0, "kind": "vo2", "free": false}]}
	for i in 1800:
		var pw := 200.0 if i < 1500 else 300.0
		L.add(1.0, pw, 90.0, 140.0, 9.0, i * 9.0, 100.0 + i * 0.01, pw, float(i))
	var st: Dictionary = L.stats(250.0, wo)
	assert(st.n == 1800 and absf(st.power_avg - 216.67) < 0.1 and st.power_max == 300.0 and st.power_min == 200.0)
	assert(st.np > st.power_avg and st.np < 300.0)
	assert(is_equal_approx(st.best[300], 300.0) and is_equal_approx(st.best[5], 300.0))
	assert(is_equal_approx(st.ftp_guess, 300.0 * 0.82) and st.ftp_basis.begins_with("beste 5"))  # beste vorhandene Faustregel
	assert(st.zones[2] == 1500 and st.zones[5] == 300)  # 200/250 = 80 % -> Z3, 300/250 = 120 % -> Z6
	assert(st.intervals.size() == 2 and is_equal_approx(st.intervals[1].actual, 300.0))
	assert(absf(st.distance - 1799 * 9.0) < 0.1 and st.cad_avg == 90.0 and st.hr_max == 140.0)
	var x := XMLParser.new()
	assert(x.open_buffer(L.tcx("Test & Co").to_utf8_buffer()) == OK)
	var watts := 0
	while x.read() == OK:
		if x.get_node_type() == XMLParser.NODE_ELEMENT and x.get_node_name() == "ns3:Watts":
			watts += 1
	assert(watts == 1800)
	var st2: Dictionary = L.stats(250.0, {"name": "Rampentest", "segments": []})
	assert(is_equal_approx(st2.ftp_guess, 300.0 * 0.75) and st2.ftp_basis.begins_with("Rampentest"))
	print("ok")
	quit()
