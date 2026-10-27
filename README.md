# Cadence

Indoor-Radsimulator (Zwift-Ersatz für zwei): Wahoo Kickr (FTMS) + Zwift Click/Play/Ride über Bluetooth, Godot 4.7.

## Start

1. Einmalig: [uv](https://docs.astral.sh/uv/) installieren (zieht `bleak` automatisch)
2. Zwift/Companion-App schließen, sonst blockieren die den Kickr
3. `godot --path ~/kurbel` (Repo-Ordner; startet die Bridge selbst)

Ohne Hardware testen: `godot --path ~/kurbel -- --fake`
Eigenständig installieren (Startmenü „Cadence“): `./install.sh` – exportiert nach `~/.local/share/cadence/`, erneut ausführen = Update (`cadence.cfg` und eigene Strecken bleiben)
Selbsttest Protokoll-Parser: `python3 bridge.py --test`

## Workout-Welt (Standard)

Nach dem Start: Workout wählen → Welt wählen → **Losfahren**. Die Landschaft entsteht aus dem Workout, ohne Karten oder
Höhendaten aus dem Netz (`world.py`, ein paar Sekunden bis zum Losfahren, dann wird vor dir weitergebaut):

- Einfahren = sanftes Hügelland, Grundlage = Flusstäler, Seen, Dörfer, Wald; Tempo = kurvige Waldstraße oder Küste;
  Schwelle = langer Anstieg; VO2max = Steilrampe; Erholung = Abfahrt oder Seeufer; Ausfahren = weites Land
- Intervall-Sätze werden ein zusammenhängendes Gebirge (Rampe hoch, Abfahrt runter, Pass mit Kapelle und Schild)
- Biome: Alpen, Mischwald, Mittelmeer, Wüste – oder **Reise** (Gegenden wechseln unterwegs mit weichen Übergängen)
- **Gelände** (neben der Welt): „Wie das Workout“ (Intervalle werden Rampen, wie oben), Flach, Wellig (auf und ab, ±5 %),
  Leichter Anstieg (~3 %), Anstieg (~6–7 %) oder Panorama (gut die Hälfte der Zeit hoch auf einen Pass, dann wieder runter).
  Im SIM-Modus spürst du genau diese Steigungen, im ERG hält der Trainer trotzdem die Zielwatt.
- Nebenstraßen: links und rechts zweigen Straßen und Feldwege ab (manchmal Kreuzungen, manchmal teilen sie sich), ab und zu
  führt eine Brücke über deine Straße. Nur Kulisse: du bleibst auf deiner Strecke, Steigung und Trainer ändern sich nicht.
- Schnee: in den Alpen liegt oben auf großen Anstiegen (> 300 Höhenmeter) Schnee – weiße Hänge, verschneite Bäume, das
  Bankett ist weiß, die Fahrbahn geräumt, im Schnee wachsen nur noch Fichten und Kiefern. Regen fällt dort als Schnee.
- **ERG**: Trainer hält die Zielwatt des Workouts, die Steigung ist nur Bild und Tempo. **SIM**: Steigung geht an den
  Trainer wie bei echten Strecken, die Zielwatt stehen nur im HUD. Ohne Workout (freie Fahrt) immer SIM.
- Workout-Uhr läuft, solange du trittst (Pause = aufhören zu treten), unabhängig vom Tempo. Schalten im ERG: ±5 % Intensität.
- Seed: gleiches Workout + gleicher Seed + gleiche Einstellungen = exakt dieselbe Welt (für zwei Fahrer: beide gleich einstellen).
  Anderer Seed = andere Welt. FTP/Gewicht/Grafik ändern die Welt nicht.
- Eigene Workouts: „Workout importieren“ (Zwift `.zwo`, `.erg` in Watt, `.mrc` in % FTP) oder Datei nach `workouts/` legen
- Mitgeliefert (`workouts/`, Prozent = Anteil FTP): Grundlage 60 min / 2 h, Erholung 45 min, Tempo 3 × 20 (83 %),
  Sweet Spot 3 × 15 (90 %), Schwelle 2 × 20 (95 %), Over-Unders 3 × 9 (92/107 %), Seiler 4 × 8 (105 %, Seiler et al. 2013),
  Norweger 4 × 4 (115 %, Helgerud et al. 2007), VO2max 5 × 3, Rønnestad 30/15 (3 × 13, Rønnestad et al. 2020),
  40/20 3 × 8 (125 %), Sprints 8 × 15 s, Gemischt 45, Rampentest (FTP = 75 % beste Minute), FTP-Test 20 min (Allen/Coggan,
  FTP = 95 % der 20 min). Kurze Wechsel (≤ 90 s) werden in der Welt je Satz ein Anstieg.
- „Einstellungen“ (Startbildschirm und Pausemenü), gelten mit „Übernehmen“: Fenster/Vollbild/Exklusiv-Vollbild, Fenstergröße,
  3D-Auflösung (der Monitor bleibt nativ), Bildrate (VSync oder FPS-Grenze), Gras, Schatten, SSAO, Lautstärke.
- Wetter und Tageszeit der Fahrt wählst du auf dem Startbildschirm bzw. neben der Karte (Echt, Morgen, Mittag, Nachmittag,
  Abendsonne, Nacht = Sonnenstand am Ort der Strecke).
- Nach dem Workout geht die Straße einfach weiter. F3 = Diagnose (Kacheln, Erzeugungszeit, FPS, Draw Calls, Speicher)
- F1 = Entwickler-Freiflug: WASD, E/Q hoch/runter, Shift schneller, rechte Maustaste halten zum Umsehen, Mausrad Tempo,
  T setzt den Fahrer auf die Straße unter der Kamera, H blendet das HUD aus, [ ] verschiebt die Uhrzeit. Der Generator baut
  im Flug um die Kamera herum (nächster Straßenpunkt); Fahrer und Trainer laufen normal weiter.
- Esc = Pause (Weiterfahren / Fahrt beenden und auswerten / Startbildschirm). Am Workout-Ende und beim Beenden kommt eine
  Auswertung: Ø Leistung, Normalized Power, FTP-Schätzung (beste 20 min × 0,95, sonst 8 min × 0,9 bzw. 5 min × 0,82),
  Ø/max Trittfrequenz und Puls, Min/Max, Bestwerte 5 s – 20 min, Zeit in Zonen, Intervalle Ziel → Ist, TSS, kJ.
  Jede Fahrt wird als TCX gespeichert (Leistung, Trittfrequenz, Puls; zu Strava/Garmin Connect/intervals.icu hochladbar),
  Ordner: `~/.local/share/godot/app_userdata/Cadence/rides/`. Die letzten Fahrten stehen im Startbildschirm.
  Fahrten, Einstellungen und Kartenkacheln aus dem alten Ordner `…/app_userdata/Kurbel/` werden beim ersten Start
  übernommen (nur wenn es im neuen Ordner noch nichts Gleichnamiges gibt).
- Wetter: ziehende Wolken mit Schatten am Boden, Regen (über der Schneegrenze Schnee), nasse Straße, Wind mit Böen.
  Gegenwind bremst, Rückenwind schiebt (virtuelles Tempo); der Trainerwiderstand bleibt reine Steigung. Gleicher Seed +
  gleiche Uhrzeit = gleiches Wetter für beide. Taste W: Auto → Sonnig → Wolkig → Regen → Sturm
- Geräusche (`sound.gd`, alles synthetisiert): Fahrtwind, Reifen, Freilauf-Klacken beim Rollen, Gischt auf nasser Straße, Regen,
  Blätterrauschen und Vögel im Wald, nachts ein Kauz. Taste M = stumm, Lautstärke unter „Einstellungen“

Ohne Spiel prüfen:

    uv run world.py info --workout workouts/vo2_5x3.zwo --seed 7     # Regionen nach Zeit und Strecke
    uv run world.py preview --workout workouts/vo2_5x3.zwo --png welt.png   # Draufsicht
    uv run world.py --test

## Eigene Strecke (echte Karte)

Im Startbildschirm „Echte Strecke auf der Karte …“.

In der Streckenwahl: Wegpunkte klicken, dann **3D-Vorschau** (Gelände, Höhenprofil, ein paar Sekunden) oder gleich bauen.
Gespeicherte Strecken haben einen **3D**-Knopf. Im 3D-Bild: links ziehen = drehen, rechts ziehen = schieben, Mausrad = Zoom.

Route in Komoot/Strava planen, als GPX exportieren, dann:

    uv run route.py hausrunde --gpx ~/Downloads/hausrunde.gpx

Oder BRouter plant direkt über Wegpunkte (lon,lat):

    uv run route.py taunus --via "8.57,50.203|8.518,50.247|8.4575,50.2325|8.464,50.183|8.505,50.18|8.57,50.203"

Dann in `cadence.cfg` unter `[route]` den Namen eintragen. Höhen kommen aus AWS Terrain Tiles,
Wald/Felder/Wasser/Gebäude aus OpenStreetMap, alles was OSM nicht kennt aus ESA WorldCover (Landbedeckung weltweit). Dauert 1–3 min, Höhen- und OSM-Daten werden in `~/.cache/kurbel` gecacht. Beide Fahrer brauchen dasselbe Paket, sonst passt der Abstand nicht.

## Optik

- Tag/Nacht nach echtem Sonnenstand am Ort der Strecke (in `cadence.cfg` mit `time_shift_hours` verschiebbar)
- Nachts: Sterne, Mondlicht, beleuchtete Fenster, Laternen im Ort, Frontlicht
- Alle OSM-Straßen/Wege/Plätze, Häuser mit Satteldach + Fenstern, Laub- und Nadelbäume im Wind, Wasser spiegelt
- Häuser: 11 typisch deutsche Modelle aus Blender (Einfamilienhaus Sattel-/Walmdach, Siedlungshaus mit Gauben,
  Fachwerk, Mehrfamilienhaus, Stadthaus, Wohnblock, Halle, Garage, Scheune, Kirche), passend zu OSM-Typ, Fläche und
  Geschossen auf die echten Grundrisse gesetzt. Verwinkelte Grundrisse bleiben prozedural.
  Modelle ändern: `blender/houses.py` bearbeiten, dann `blender -b -P blender/houses.py -- models/houses.glb vorschau.png`
- Gras + Wildblumen um den Fahrer (wiegen im Wind, weichen beim Durchfahren aus), Herbstlaub fliegt herum.
  Gras-Dichte in `cadence.cfg` → `grass` (kleiner = schneller, 0 = aus)
- Biome weltweit (`biome.py`): Wüste, Steppe, Wald, Tropen, Hochgebirge, Stadt, Agrarland. Bodenfarben, Gras, Bäume
  (Nadel/Laub/Palme), Büsche und Baumgrenze aus Landbedeckung + Klima (Breite und Höhe). Schnee je nach Monat und Höhe,
  in `cadence.cfg` → `month` festlegbar (0 = echtes Datum). Biome ändern nie die Steigung oder den Widerstand am Kickr
- Shader in `shaders/`. Strecken, die vor diesen Änderungen gebaut wurden, mit `route.py` neu bauen

## Einstellungen: `cadence.cfg`

Ältere Installationen mit `kurbel.cfg` (ohne `cadence.cfg`) lesen weiter `kurbel.cfg`.


- `name`, `mass`: Name über dem Fahrer, Gewicht Fahrer+Rad
- `difficulty`: Anteil der Steigung, der am Kickr ankommt (0.5 wie Zwift)
- `ftp`: Watt für die Workout-Ziele (der Startbildschirm merkt sich zusätzlich den zuletzt eingestellten Wert)
- `gear_step`: % Steigung pro Gang (Controller +/-, oder Pfeiltasten hoch/runter)
- Karte unten rechts: echte Position auf OpenStreetMap, `+`/`-` zoomt (Kacheln werden in `user://tiles` gecacht)
- `peer_host`: IP vom Kollegen. Beide tragen die IP des anderen ein, UDP-Port 47810 muss erreichbar sein (Tailscale am einfachsten)

## Straßen

- Breite, Belag (Asphalt, Pflaster, Schotter mit Grasnarbe) und Markierungen kommen je Abschnitt aus OSM
- Brücken mit Brüstung und Pfeilern, Tunnel als Galerie; über anderen Straßen hält die Brücke 5,5 m Durchfahrtshöhe
  (mit sanften Rampen, das spürt man am Kickr), Brücken und Tunnel haben keine Dellen/Buckel mehr
- Stichstraßen, die BRouter zu einem Wegpunkt neben der Straße fährt, werden automatisch herausgeschnitten
- Häuser, die in OSM in die Fahrbahn ragen (Durchfahrten), werden weggelassen
- Kreuzungen: Randlinie an Einmündungen gestrichelt, Haltlinie + durchgezogene Mittellinie vor Ampeln, Warte-/Haltlinien an
  Nebenstraßen, Zebrastreifen und Fußgängerfurten (aus OSM)
- Schilder aus OSM: Ampeln (schalten, nur Optik), Tempo (wo sich `maxspeed` ändert), Stopp/Vorfahrt gewähren, Ortstafeln mit
  echtem Namen (Ein- und Ausgang), Straßennamen an Einmündungen, Rad- und Wanderwegweiser mit echten Zielen in echter Richtung
- Die Straße liegt bündig im Gelände (Kante 20 cm über dem Boden statt 0,8–12 m Damm): am Hang geht es neben der
  Fahrbahn bergauf und bergab wie in echt, Gras wächst bis an den Rand

## Bekannte Grenzen

- Zwift Ride: Firmware nicht über 1.2 updaten (ab 1.3 sperrt Zwift die Tasten für andere Apps)
- Strecken ohne Rundkurs: am Ende geht es wieder bei km 0 los
- Tunnel sind offene Galerien (das Gelände-Raster kann keine Löcher), Bahnstrecken fehlen
- Kickr ohne FTMS (Firmware vor ~2020) wird nicht erkannt → Firmware updaten

## Daten & Lizenzen

- Karten/Flächen/Gebäude: © OpenStreetMap-Mitwirkende (ODbL)
- Höhen (nur echte Strecken): AWS Terrain Tiles (Mapzen/Terrarium)
- Landbedeckung: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021), CC BY 4.0
