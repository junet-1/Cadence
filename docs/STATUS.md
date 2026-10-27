# Cadence (formerly Kurbel) – status, decisions, next steps

Short and current. Newest decisions at the top of their section. How to work: `CLAUDE.md`.

## Spruce sprays crossed, grass with depth, real flowers (2026-10-27)
- User: "Tannen sehen ganz komisch aus", ground plants "keine Tiefe". Spruce sprays lay flat -> from the road only edges and
  bare sticks. Now each spray is a crossed pair (tilted flat + upright), larger, whorls every ~1.1 m, darker core.
  `trees.py --` preview is now EEVEE with the atlas (looks like the game; `-- out.glb preview.png`).
- Grass (`main._grass_mesh`): 12 thin curved blades (3 segments, pointed) per tuft instead of 6 wedges, indexed; per-blade
  brightness/yellowing in COLOR.rg; blade normals mixed 55 % with up (light/shadow side). `grass.gdshader`: dark base
  (×0.35 -> ×1.15 at the tip), straw tint. `terrain.gdshader`: ground under grass darkened and blotchy near the rider
  (global `grass_r` = grass radius, fades with the grass) so gaps read as shade.
- Flowers: stem + 2 leaves, 5 petals cupped around a centre (COLOR.a 1 / 0.5), head nods.
- Cost, same session (the user's game ran on the same GPU, so absolute FPS is low): grass on/off 79/82 FPS; tree detail
  600 m vs 200 m 75/86 FPS (45 M vs 29 M triangles).

## Detailed trees: leaf cards (2026-10-27)
- User: trees "sehr off, zu wenig Detail" (crowns were 20–40 smooth icosahedron blobs, spruce = stacked cones). Blender MCP is
  only configured for ~/cargo, so the existing headless pipeline was extended (`blender -b -P blender/trees.py -- models/trees.glb`).
- `trees.py` renders `models/leaves.png` (1024², 2×2: broad leaves, small leaves, spruce spray, pine tufts; grey + alpha,
  colour under transparent pixels = mean grey against dark mip fringes). Near versions: one more branching level, 160–780
  double-sided cards per tree (`Tree.card/leaves`) + small inner blobs against see-through; spruce = whorls of drooping
  branches with flat needle sprays + slim core; cypress = core + upright needle cards. `_lod` unchanged (blobs, no cards).
- `props.gdshader`: UV.x > 0.999 = card -> sample atlas, discard below 0.5 alpha (alpha raised with mip level so crowns stay
  dense far away), colour × texture × 1.55. Everything else in props (houses, deco, animals) has UV 0 -> unchanged.
  `main.leaf_atlas()` loads it from disk like the glb (`data()`), own mipmaps; `install.sh` copies it.
- Cost (forest, same seed/spot, hidden window, VSync off): 153 -> 141 FPS, 14.2 M -> 20.8 M triangles/frame.
- Detail distance tripled on user request: trees 200 -> 600 m, bushes 80 -> 240 m, undergrowth 40 -> 120 m (`main.TREE_DETAIL`,
  `plant_multimesh`). Same spot: 41 M triangles/frame (was 21 M), FPS in the hidden test window not meaningfully lower.
- Next: palm fronds with a texture; cards for `_lod` if the 600 m switch still shows.
- Safety: a second instance (GAME_PORT taken) or `MCP_BACKGROUND=1` never sends to the bridge (`main.trainer_owner`). Two MCP
  test runs had sent SIM grades to the user's running bridge (real KICKR) while they rode the installed game.

## Options menu + ride weather/time (2026-10-27)
- User: settings "didn't apply". There was no Apply at all: video options applied on each dropdown change, and in fullscreen
  "resolution" only changed `scaling_3d_scale` silently. Now `options.gd` (start screen + pause menu): staged values, "Übernehmen"
  saves `user://start.cfg [video]` and applies, then the status line reads back mode/size/3D size from DisplayServer; "Zurück"
  discards. Window size (windowed only) and 3D resolution % are separate controls. Grass/shadows/SSAO/volume moved here
  (grass from next ride; shadows/SSAO live via `main.apply_settings`). `[video] resolution` from before is ignored (defaults).
- Verified with a real window (X11/XWayland, 5120×2160): windowed 1280×720, maximized, fullscreen, scale, FPS cap, VSync, volume.
- `Options.store()` reloads before writing: start screen, options and ride pickers write the same file without clobbering.
- Ride pickers (start screen grid, picker panel): weather preset (weather.gd PRESETS; `weather.begin` snaps state, road wet in
  rain) and time of day as local solar time -> `time_shift_hours` for this ride only (`main._set_clock`, -2 = sun 6° up in
  the afternoon). Passed via `Engine` meta "ride". `cadence.cfg [sound]` removed again (volume lives in options).
- Test runs under `MCP_BACKGROUND=1` start the bridge with `--fake`: an MCP test had connected to the real KICKR.
- Rain/wet spray were too loud and harsh (user): rain 0.45 -> 0.12, band 250–3200 Hz, fewer/softer drops; spray 0.12, ≤ 4.5 kHz.

## Sound (2026-10-27)
- `sound.gd`: no audio files, everything synthesized once at start in a WorkerThreadPool task (~95 ms): looped band-passed
  noise (air at the ear = ride speed + weather wind, tyre, wet spray, rain + drops, leaf rustle with gusts), freewheel click
  loop (pitch = wheel rev/s × `POE` / 48, only while cadence < 5), bird phrases (4 patterns × 2) and an owl as one-shots on
  3 `AudioStreamPlayer3D` 15–55 m around the rider. Per frame only volume/pitch; tree count within 40 m every 0.5 s from
  `main.tree_pts` (all trees, filled in `add_leaf_trees`, dropped with tiles). Bike sounds mute in pause. Visual-only rule holds.
- Not done: surface-dependent tyre sound (gravel), the partner's bike, cicadas/crickets in warm biomes, water near rivers.

## Standalone Linux build (2026-10-27)
- `./install.sh`: `godot --export-release Linux` (preset in `export_presets.cfg`, pck embedded, `routes/` excluded) to
  `~/.local/share/cadence/cadence.x86_64`, copies `*.py`, `models/*.glb`, `workouts/`, `routes/` (reflink on btrfs) next to it, writes
  `~/.local/share/applications/cadence.desktop`. `cadence.cfg` is only copied if missing.
- `main.gd::data(p)`: every on-disk path (Python scripts, routes, glb, workouts, cfg) goes through it; in an export (`template`
  feature) `res://` maps to the executable's directory. New disk paths must use it, or they break only in the export.
- Debt: the install is a snapshot; routes built from the installed game land in its own `routes/`, not the repo's. uv is still required.

## Terrain quality pass: walls, seams, road integration (2026-10-27, world.py VERSION 12)
Root causes found by measuring (`uv run world.py diag --workout F --seed N --biome B [--profile P] --km a,b,c`, new):
- **Filled basins** (`terrain.erode`): depressions were filled to spill level in the terrain itself -> flat plateaus up to
  +97 m with walls around; worst on switchback blocks (box = hillside surrounded by valley walls = basin). Now fill only
  routes water (lakes); no erosion inside a basin, the outlet incises. Erosion dh max +97.5 -> +22 m (VO2/Reise 7, km 10.6).
- **Closed basins at road sags** (`_drains`/`_carve`): every low point of the road was ringed by valley walls. A side valley
  (relief squeezed toward a meandering axis, floor −2.5 %) leaves each sag (≥ 10 m counter-climb both sides) on the downhill side.
- **Crease rays from switchbacks** (`params`): every support extrapolated its block plane ≤ 150 m, IDW switched supports at
  medial lines -> straight creases hundreds of m long (valley floor up to 147 % steep). Now `_floor`: plane clamped to the
  block box + residual of the legs (64 nearest road points), geometric weight around the box; `hgrad/cgrad` removed.
  Needle creases between legs (13-NN residual) fixed with 64-NN.
- **Vertical walls at the cut/fill reach** (`terrain.conform`): a 1:1 cut / 1:1.5 fill never meets terrain steeper than
  itself, at 60–80 m the cone was switched off -> 100 m walls (alpen seed 3 km 6, slope 57.9). Batters now steepen beyond
  25 m where the terrain out-climbs them (rock cut / retaining-wall steep). Same spot: a 9 %-limited **side road ran 400 m
  inside a mountain** and conform dug a trench -> `_sides.add` ends a side road where its profile leaves the terrain by > 10 m.
- **LOD seams** (far 32 m vs mid 8 m tiles): up to 11.9 m gaps, the 40 m skirt stood as a wall. Only mid tiles border far
  tiles, and only on 1024 m lines -> mid tiles geomorph a 32 m band onto the far triangulation there (`_far_edge`, away from
  roads; props follow). fm gap max 11.9 -> 2.7 m (p99 5.5 -> 1.0).
- **Cliff-band props** (`_cliffs`) read as stacked slabs in rows -> only slope > 1.15, narrower bands, 45 %, sunk 35 %.
- Biomes: treeline as a band (south faces higher, north lower, wavy; forest thins over ~200 m, none on slope > 0.8; single
  trees up to ~140 m above), alpine palette/rock use it. Snow (`shaders/snow.gdshaderinc`, shared by terrain, road shoulder,
  grass): wavy line, north faces −90 m, flat holds more, on rock only in streaks.
- Road: crossfall in curves (`World.bank`, ≤ 4 %, `edge_y()`; side-road mouths use it). Visual only.
- Unchanged on purpose: `route.pos` SHA1 identical (VO2 Reise 7, Alpen panorama 7, Wald 3, free Mittelmeer 1) -> SIM grades
  identical. Erosion fade at 1.4–1.9 km kept (seam no longer visible once fills were gone). Grade/hairpin geometry untouched.
- Forest (user request): graded verge (gravel 0.7 m -> sparse short grass -> meadow by ~3 m, noisy edge); undergrowth
  `farn`/`hochgras`/`staude` (blender/trees.py, near tiles only, visible 160–200 m; ferns in patches, tall grass/herbs on
  verges and edges, lush biomes only); animals max 12 deer / 14 hares, forest groups 2–4 (cap off-by-one fixed).
  ~700 ferns + ~150 tufts per forest tile, near tile build unchanged (~120 ms).
- Verified: tests (terrain/world/route, selftest.gd), diag on Reise 7 / Alpen 7 panorama / Alpen 3 anstieg / Wald 3 /
  Wüste 3 / Mittelmeer 5, screenshots (Mischwald 3 forest road + floor, Reise 7 alpine flank). Not verified: hairpins
  in-game after kurbel-21's concurrent `_hairpins` rework; motion of the new plants; performance on weaker GPUs.
- Debt: desert near/mid seams up to ~10 m (sharp terraces on near tiles vs box-filtered on mid; skirt 5 m) – needs the
  same filter on near-tile edges or a deeper mid skirt; steepened batters have no wall mesh (rock-textured faces only).

## Video settings (2026-10-27)
- (Superseded by the options menu above.) Start screen "Anzeige": window mode (windowed / borderless fullscreen / exclusive), resolution, frame rate. Saved in
  `user://start.cfg [video]`, applied by `start.gd::apply_video` on every start-screen load; no `[video]` section = project.godot untouched.
- Godot never changes the monitor mode: fullscreen = native resolution, a smaller pick sets `root.scaling_3d_scale` (3D only, HUD sharp).
  Refresh rate can't be set from Godot -> VSync or `Engine.max_fps` cap. Skipped under `MCP_BACKGROUND=1` so MCP tests stay hidden.
- ponytail: bilinear 3D scaling; FSR (`scaling_3d_mode`) if low scales look soft.

## Climbs onto mountains, irregular switchbacks (2026-10-27, world.py VERSION 13)
- User: "die Berge um mich rum sind alle höher, ich fahre zwischen Bergen hoch". Cause: valley walls, mountains and hills in
  `_macro` are relative to the smoothed road height, and climbs even got ×1.35 mountains -> every climb was a valley.
  Now `_coarse` derives per road sample cw (climb > ~150 m within ±8 km) and pk (top 60 % of the climb): mountains/valley
  walls × (1 − 0.5 cw) and massif × (1 − 0.85 pk), valley walls × (1 − pk), hills × (1 − 0.8 pk), cross slope × (1 − 0.7 pk),
  forest × (1 − 0.8 pk) (alpine meadow on top), and a fall-off beside the road at the top (~40 % slope after 30 m, at most
  80 % of the climb) -> ridge/dome. Fog −45 % at summits (atmo). Switchback blocks keep 450 m away from summits of climbs
  > 2.5 km (their plane made a plateau). Measured at the summit, ring 300/700/1100/1500 m: VO2 Alpen Panorama 7 100/100/100/94 %
  of the terrain lower, Mischwald Panorama 77298 100 % (was 29–56 %). Test asserts ≥ 70 % at 700/1100 m.
- First version used sign(t) of the nearest road point for "valley side open" -> flipped between road parts (kurbel-ed's
  `world.py diag`, alpen seed 3 anstieg: slopes up to 92, 19 m steps, LOD gaps 84 m). Replaced by the smooth factor above;
  diag now slope max 1.5/0.94/1.1, step ≤ 0.5 m, LOD ≤ 3.6 m.
- Switchbacks (`_hairpins`) were identical legs/hairpins: now leg lengths × 0.6–1.4, leg angle ±3° per leg (fanning kept),
  radius per hairpin, mild S-bend per leg (net 0), lengths re-balanced so the block length and exit heading are exact;
  no switchbacks in regions with |grade| < 4.5 % (gentle panorama descents had 8 km of them). Grades along s unchanged.
  The documented 0.3 m gap tolerance under the shoulder edge now applies to whole switchback blocks (one leg case, VO2 Reise 7
  km 10.9, 16 cm).
- Verified in screenshots: summit ridge seen from 2.4 m and 25 m (valley below, mountains beyond), switchbacks top-down.

## Side roads, forks, bridges (2026-10-27, world.py VERSION 11)
- `world._sides` (visual only; rider stays on the main road): from the seed, junctions every ~0.8–2.4 km (÷ village weight,
  × 1.6 on steep grades), none in switchback blocks ±400 m, tight curves, or into water (water regions: only the dry side).
  13 % crossings (both sides), 25 % forks after 100–400 m (40–70°), 12 % bridges over the main road (rider passes under:
  deck = max main height ±30 m + 7 m, ramps ≤ 7 %, only where the natural ground fits). Side roads: 55–125° off, 10-m steps
  with smoothed-noise curvature (R ≥ ~180 m), 0.6–2.5 km, end when they come within 70 m of the main road, 20 m of a house
  centre, or 45 m of another side road. Heights = macro relief (no erosion) blended from the main-road height at the mouth,
  ≤ 9 %/10 m, smoothed. 30 % gravel tracks outside villages (shader grass strip), hw ≥ 2.6 paved get a centre line.
- Terrain: `field` conforms to side roads first (bench 1.5 m), then the main road as before (its bench/cuts win; the bridge
  embankments end in the main road's 1:1 cut slope). Afterwards `d` = distance to the nearest road edge of either -> trees,
  rocks, cliffs, forest, grass (grow PAVING) keep off side roads. House deco filtered.
- Meshes go into the road chunk of the junction (`road_chunk` + `_side_mesh`), no streaming change: same 6-column cross
  section; the first section is pushed along the side road onto the main/parent edge line (flush, no gap/overlap); main
  road edge line broken at mouths via CUSTOM0 (`.c0` now deleted by world_stream.gd); delineators skipped there. Bridges are
  resampled at 2.5 m: concrete deck sides, soffit, parapets, two piers 2.2 m outside the main road edge (interpolated).
- Measured: route.pos SHA1 unchanged on VO2 Reise 7, VO2 Alpen Panorama 7, Grundlage 60 Wald 3, free ride Mittelmeer 1.
  Grundlage 60 Wald 3: 27 T + 6 forks + 4 bridges over 79 km (59 km of side road); init +0.4 s; world ready 9.2 s; 30 s at
  ~58 km/h: frame p50 = p99 16.7 ms (vsync), 1/1799 > 25 ms, 690 draw calls. Road chunks with side roads ~100–250 ms in the
  generator process (shoulder colour sampled every 40 m). Self-test: count, bridge clearance ≥ 5.5 m, piers ≥ 1 m off the
  edge, flush mouths, no return within 69 m, grade < 11.5 %, terrain not above side roads.
- Verified in screenshots: bridge with two piers from the ride camera (km 1.7), gravel track mouth top-down (km 5.1), village
  street with centre line (km 23.9). First version put both piers on the main-road centreline (10 m sampling) – fixed.
- Mouths fixed after a user screenshot (green strip + square corners at a gravel track, Mischwald seed 77298 km 0.9): the
  side road started at the outer shoulder edge (hw + 0.35), so the main road's grass-coloured shoulder showed between the two
  asphalts. Now the mouth reaches 10 cm onto the main asphalt, 1 cm above it (`edge_y`, with kurbel-ed's crossfall), side
  shoulders stay low there, and both corners get a fillet fan (quadratic Bézier, R = 6 m asphalt / 4 m gravel, longer on acute
  angles; heights from the main edge and the side road). `_side_index` widens the first segment by R, so bench, grass and
  trees respect the flare. Verified top-down and from 1.8 m eye height.
- Not done: underpass (main road on a bridge over a side road) – the terrain is a height field, no hole under the main road;
  no rounded mouth corners, no give-way lines/signs at side roads, no traffic; houses only along the main road, so village
  junctions are rare; side roads have no far-tile representation beyond their road-chunk mesh.

## Terrain profile + snowy summits (2026-10-27, world.py VERSION 10)
- Start screen "Gelände" (`opts.profile` -> `world.py --profile`, in the world key): auto (old behaviour: workout zones pick
  scenes), flach (FLAT scenes by biome weight), wellig (new scene `hills`, ±5 %), leicht (new `gentle`, ~3 %), anstieg
  (`climb` the whole workout), panorama (`climb` for 55 % of the workout time, then `descent`). After the workout: one descent,
  then free-ride scenes; free ride cycles per CYCLE = 60 min. Profile descents spread the gain over the whole descent run.
  Measured VO2 5×3 seed 7 Alpen: anstieg 677 m in 10.5 km (p50 6.5 %), panorama summit at 26 % of the distance (400 m),
  flach p95 |grade| 0.8 %, wellig p5/p95 −5.4/4.9 % with net 0. Self-test covers all four + snow. Auto worlds keep their
  scenes (only VERSION/snow changed). Trainer: SIM gets exactly the profile's grades (chosen by the rider); ERG unchanged.
- Snow: alpen `cap = 140`: where the road gains > 300 m within ±8 km, the snow line drops to 140 m below the local top (±2 km)
  -> top of big alpine climbs white (terrain, grass gone, snow on crowns), rain falls as snow there (existing weather.gd rule).
  Road shader: shoulder outside the carriageway takes snow above `snow_line` (was a green strip); asphalt stays clear.
- Verified in screenshots (VO2 5×3, Alpen, Panorama, seed 7): summit at km 6.5 / 1300 m in snow, weather preset Regen ->
  "Schnee" with flakes. No snow in Mischwald/Mittelmeer/Wüste by design.
- Fix: the summit was the highest point within ±2 km, so mid-climb the snow line followed the rider (white from halfway up).
  Now `World.snow_line()` uses the highest point within ±8 km (the actual summit): panorama seed 7 snow from 260 m, summit
  400 m (test: 30 % up the climb the line is > 100 m above the road). Same function feeds `_plants`: within 80 m below the
  snow line of the nearest road point only spruce (75 %) / pine – no beech/oak/birch in the snow. Garden trees in villages
  (`_deco`) are not switched.

## Cliff bands on mountains (2026-10-27, world.py VERSION 9)
- Rocks on steep mountain rock were only 2.5–7.5 m boulders. New `fels_wand_{granit,kalk,sand}_{0,1}` (+ `_lod`) in
  `blender/deco.py`: wide slab, face leans back ~75° into the slope, vertical ribs, set-back ledges, jagged crest with towers,
  lens-shaped plan (ends taper); 588 / 108 faces.
- `world._cliffs` (fine + mid tiles, same 24 m grid and seeds for both LODs): only where exposed rock > 0.5 and slope > 0.9,
  and only in height bands (every ~70 m, warped by fbm, 28 % of the height) -> horizontal cliff bands with scree/grass between,
  not a carpet; 15–60 m high, height varies smoothly over the area (fbm, not per cell -> continuous band instead of blocks),
  front faces downhill (gradient from 4 extra height samples), ends kept ≥ 8 m from the road edge. Biome mix: granite /
  limestone / sandstone. Visible to 1.5 km like other `fels*` deco, LOD beyond 200 m.
- VO2/Reise seed 7 alpine window: ~1000 wall segments loaded (km 9.0 and 10.6). Same view at km 11.6: 691 draw calls,
  4.7 M triangles, frames vsync-capped 16.7 ms p99. First attempt (every 18 m cell, 10–35 m) covered whole slopes like
  bricks; per-cell heights made boxes – both fixed as above (fly-mode screenshots). Grades/route.pos untouched.
- Not done: far tiles (32 m, > ~3 km) get no cliffs, so distant ridges stay smooth; Mittelmeer/Wüste cliffs not checked in-game.

## Cadence polish pass (2026-10-27)
- **Rename Kurbel -> Cadence**: project name, UI, docs, TCX default name, HTTP User-Agent. Config is `cadence.cfg`
  (`main.config()` falls back to `kurbel.cfg`). Godot's user dir follows the project name (`app_userdata/Cadence`);
  `start.gd::migrate_user_dir` moves rides/tiles/preview/start.cfg from `app_userdata/Kurbel` once (never overwrites; an
  empty new dir is replaced). Kept on purpose: repo dir, `~/.cache/kurbel` (geo cache), "kurbel" as a village-name syllable
  in world.py (changing it would change worlds).
- **Correctness**: power/cadence/HR stayed at the last value forever when the trainer dropped (rider kept riding, workout
  clock and TCX recorded phantom watts) -> zeroed once after 3 s without data (`SENSOR_TIMEOUT`), red HUD chip + toast.
  HUD appeared ~6 s before the world existed (rider over fog) -> opaque loading screen until `world_ready` (workout, tile
  count, seconds); generator crash after start is now shown with "auswerten"/"Startbildschirm" buttons (was invisible).
  Pause menu didn't pause -> `main.paused()` (menu or recap open) freezes distance, ride time, workout clock and recording;
  the trainer keeps getting the same command. Recap buttons were below the fold at 1600×900 -> fixed footer.
  Start screen overflowed with "Erweitert" open -> settings moved left, map button to the header, scroll fallback.
  `bridge.py` crashed with "Address already in use" when a ride started while the previous bridge still held port 47801
  (watchdog ≤ 10 s) -> bind retried 15 s (lifecycle only, protocol untouched). Ride files transliterate umlauts.
- **UI**: one theme for every screen (`ui_theme.gd`: focus rings for keyboard navigation, disabled colours,
  `UI.primary`). `canvas_items` stretch (base 1600×900, expand): same layout scaled 1.2× at 1080p, 1.6× at 1440p/3440×1440,
  2.4× at 5120×2160; map tiles and the 3D preview render 1:1 in physical pixels. Picker/preview restyled (scrolling route
  list with km, build progress + cancel, actionable network errors, grade legend, Esc/Enter). ERG/SIM explained on the
  start screen; free ride shows SIM (mode disabled). Workout bar, HUD and recap share the Coggan zone colours. HUD: trainer
  status chip, zone-coloured power tile frame (number stays white), peer distance as a ride-panel row, accent countdown.
- **Rider**: `blender/rider.py` -> `models/rider.glb` -> `rider.gd` (road bike + rider ~6.6k tris, kit colour per rider,
  2-bone leg IK on the pedals, wheels turn with speed); peer speed estimated from packets. Camera (0, 2.1, 4.6), −7°.
- Measured (RX 9070 XT, MCP background window, vsync 60): VO2/Reise seed 7 at ~58 km/h for 40 s: frame p50 = p99 =
  16.7 ms, max 26 ms (1/2397 frames > 25 ms) -> streaming is not a bottleneck, nothing optimised. World ready 7.8–8.7 s.
  6 × 2 km jumps (13 km): RAM 129–142 MB, VRAM 256–280 MB, node count bounded -> no growth. Real route Gotthard loads in 0.9 s.
- Verified in screenshots: start (ERG/SIM/free ride, settings open), loading screen, HUD, pause, recap, picker, preview,
  Gotthard real route. Sensor loss verified by injecting one UDP packet then silence. Not verified: real Kickr/Zwift
  hardware, 5120×2160 window (scaling math only), two riders over the network (peer speed estimate untested).
- **World (world.py VERSION 8)**: fine/mid tiles are chosen from the road stretch around the rider (was: nearest road point
  to the tile centre -> on winding sections the rider's own tile was only a 32 m far block, rider buried in a slope, VO2/Reise
  seed 7 km 10.6; 11 of 254 sample positions affected, now 0, self-test added); strata box-filtered on mid/far tiles (sawtooth
  stripes on desert slopes); road shoulder as its own strip (crisp asphalt edge); sky cubemap returns a warm ground bounce
  below the horizon (less turquoise shadows). `route.pos` SHA1 identical before/after (Reise 7, Wüste 3) -> grades unchanged.
  Road meshes cast no shadow (blue-grey smear along the edge at low sun, both world modes). Dusk: dropped
  `background_energy_multiplier` (it inflated reflections and haze -> blue world, road like water, white grass), neutral
  ambient by day + 45 % more at dusk. FPS same view unchanged (±2 %) except km 10.6 which now actually renders its tiles.
- Real routes show/record real elevation (`meta.climate.elev0`; was relative height labelled "ü. M.").
  `bridge.py` watchdog also ends a bridge that never got a packet (was: ran forever).
- Debt: map tiles 1:1 make OSM labels small at ≥ 1.6× (needs @2x tiles); old `app_userdata/Kurbel/tiles` stays when the new
  cache already has tiles; rider/geometry constants duplicated in rider.py and rider.gd; two game instances on one machine
  share `user://world` (the second deletes the first's tiles – dev-only); teal tint in desert shadows mid-afternoon; wavy
  self-shadow bands on bumpy ground at very low sun; sensor timeout may flag a trainer that stops notifying at standstill.

## Rocks by biome (2026-10-27, world.py VERSION 7)
- `blender/deco.py` adds `fels_{granit,kalk,sand,moos}_{0,1,2}` (+ `_lod`): icosphere with radial jitter and planar fracture
  cuts (faceted), flat base, per-face colour variation darker at the bottom; granite angular/tall, limestone light/rounded,
  sandstone stacked banded layers, mossy boulders green on top. Colours deliberately dark (sRGB .34 granite): under sun 1.6
  + sky ambient a .46 grey read as almost white.
- `world._rocks` per near (8 m cells) and mid tile (16 m cells, only rocks ≥ 2 m): probability from the field material masks
  (exposed rock², scree) + rare boulders in forest/meadow in soft/ridged terrain; size: outcrops 2.5–7.5 m on rock, 0.3–0.9 m
  on scree, 0.8–2.2 m otherwise; non-uniform stretch, random yaw, sunk by 0.25–0.85 × size with slope; type by biome weights
  (st_ridged -> granite, dry non-desert -> limestone, desert/mesa -> sandstone, soft -> moss). Not within hw + 4 m + size of
  the road, not in villages or water. Written as `deco_fels_*` (shared deco path, visible to 1.5 km, LOD beyond 200 m).
  ~40 rocks per tile in the alpine section of VO2/Reise seed 7.
- Verified 2026-10-27 (fly-mode screenshots): granite block field below a scree slope (alpine, km 10.7), sandstone ledges in
  the canyon walls (Wüste seed 3, km 16.2). Not checked: Mittelmeer limestone in-game (only Blender preview).

## Switchbacks sit on one hillside (2026-10-27, world.py VERSION 6)
- Valley walls, hills and mountain suppression used the distance to the *nearest* road; in a switchback block every leg dug
  its own valley and ridges stayed between the legs (washboard, user screenshot km 9.4 VO2/Reise seed 7). Now each block has
  an oriented box (PCA of its points + 25 m, `self.boxes`); where `P["hp"]` (switchback weight) is high, those terms use the
  distance to the box instead (0 inside -> the fitted hillside plane + half-strength hill undulation across the legs).
  Legs are cut in as benches by `terrain.conform` only. Outside the box nothing changes. Verified in fly-mode screenshots
  (km 8.9–10.5): continuous slope, no per-leg troughs; remaining visible bands are the 1:1 cut / 1:1.5 fill faces.

## House surroundings + wildlife (2026-10-27, world.py VERSION 5)
- `blender/deco.py` -> `models/deco.glb` (zaun, zaun_holz, mauer, mauer_lehm, hecke, briefkasten, holzstapel, topf, bank,
  haltestelle, auto_0…5) and `models/animals.glb` (reh_rumpf/kopf/bein in parts, hase). Long axis along X, vertex colours.
- `world._deco` (workout world only): per house a front boundary 2.6 m beside the road edge by region style (picket/hedge,
  alpine rail fence, whitewashed or adobe wall) with a gap at the driveway, a driveway painted into the ground (`self.drive`
  kd-tree in `field`, paving grow code -> no grass), car in 70 % of driveways, letterbox, 1–3 shrubs, a garden tree (species
  by region), regional extras (woodpile, terracotta pots); cars parked half on the verge in villages (not at lamps or
  driveways), one bus stop per village. Positions interpolate between the 5 m road samples (else 2 m fence panels collapsed).
  Written per tile as `deco_<kind>.bin` (12-float rows with yaw), loaded via `plant_multimesh` (trees keep wind sway).
  Counts (Grundlage 60, Mischwald seed 3): 373 houses, 1265 fence panels, 959 hedge panels, ~350 cars.
- `animals.gd` (workout world only, purely visual, not deterministic): up to 7 roe deer (groups of 1–3) and 10 rabbits spawn
  ahead beside the road on forest/meadow/field (grow codes from the fine tiles' grass maps via `main.ground_at`), despawn
  behind. State machine graze -> walk -> alert (head up, look) -> flee (deer 9–12 m/s bounding, rabbits zig-zag); alert
  radius grows with rider speed; a fleeing deer alerts its group; water/map edge turns them around. The free-fly camera
  also scares them.
- Verified 2026-10-27 (Godot MCP screenshots, Mischwald seed 3): village with fences/hedges, driveway gaps, parked cars,
  garden trees; a grazing deer at the forest edge that switched to FLEE at 10 m/s when the camera came within 13 m.
- Not done: real-route packages (route.py/OSM houses) get no garden deco and no animals; animals ignore buildings/roads.

## Road alignment like a designed road (2026-10-27, world.py VERSION 4)
- Horizontal line was noise curvature (sign flips every few hundred metres -> endless S-bends even on "straight" scenes).
  Now `_road` strings design elements: straight (scene `curve[1]` × 0.35–1.7) -> clothoid (curvature linear, R/6, 25–100 m)
  -> circular arc (R = 1/`curve[0]` × 0.7–2.2, ≥ R_MIN; 8–50° turns, in tight scenes 30 % chance of a 40–95° bend) ->
  clothoid. Bend direction pulls back to the main heading (stronger the further off), 25 % chance of a reverse curve after a
  15–60 m tangent (proper S-bend). Mountain switchbacks (`_hairpins`) unchanged. Drift correction weakened to dev/1500.
  Measured: 48–59 % of the road with R > 3 km (straights), min R 115 m (Mischwald) / 158 m (Mittelmeer). Grades unchanged
  (vertical profile untouched), so trainer/workout behaviour is identical. Same seeds now give different roads (VERSION 4).

## Smooth wind + falling leaves (2026-10-27)
- Twitching came from `sin(TIME * (a + b * strength))`: when gusts changed the strength, the phase jumped by TIME × Δb.
  Now `weather.gd` integrates a phase `wind_t` and a gust-field offset `wind_scroll` (globals); grass and trees only use
  those. Trees: bend grows with height (y²/(y+12)), base lean + travelling gust field + slow per-tree swing, fine flutter in
  the crown. Rule for new shaders: never multiply TIME by a value that changes at runtime.
- Leaves fall from deciduous crowns (buche/eiche/birke): `main.add_leaf_trees` stores 3 crown points per tree (near tiles in
  the workout world, all chunks on real routes); every 0.5 s the points within 70 m become the particle emission points; wind
  drifts them via sideways gravity. Amount = wind + autumn; green leaves outside autumn (`leaf.gdshader autumn`).
- Verified 2026-10-27 (Godot MCP screenshots, Mischwald seed 3, Sturm/Wolkig): leaves leave the crown and drift across the
  field. Smoothness of the sway is motion and was not judged from stills.

## UI rework + ride recap (2026-10-27)
- `ui_theme.gd`: one theme (dark translucent rounded panels, system font Inter/Noto/DejaVu with grayscale AA – LCD AA frays
  on translucent panels, orange accent, Coggan zone colours) for start screen, HUD and recap.
- `hud.gd` replaces the text HUD: tiles (power 3-s avg in zone colour + target delta, cadence, speed, HR hidden without
  sensor), workout panel (interval, target W/% FTP, countdown, progress, next interval, pause hint), ride panel (grade, km,
  altitude, ride time, ERG/SIM, region, weather with wind arrow relative to heading), workout bar, toasts on interval/region
  change, loading panel, pause menu on Esc. Root Controls under a CanvasLayer need `set_anchors_and_offsets_preset`
  (`set_anchors_preset` left the HUD at size 0).
- `ride_log.gd`: 1 sample/s from the first pedal stroke; stats (avg/NP/IF/TSS/kJ, min/max, best 5 s–20 min, FTP guess,
  zones, intervals target vs actual by workout clock), TCX export + JSON in `user://rides/`. `recap.gd` shows it at workout
  end (can continue) and on "Fahrt beenden". selftest.gd checks the stats and that the TCX parses with 1 Watts per second.
- Verified 2026-10-27 (Godot MCP screenshots): start screen, HUD in a VO2 ERG ride, pause menu, recap with 40 min synthetic
  data. Not verified: recap after a real ride with a trainer; a Strava upload of the TCX (format follows the Garmin TCX v2
  schema with ActivityExtension v2 Watts); picker/preview screens are not restyled yet.

## Consistent look: sky, distance haze, heat, water (2026-10-27)
- Sky shader fixed: in sky shaders `LIGHT0_DIRECTION` points **to** the sun (the first version negated it and always drew the
  night gradient). Colours kept low-saturation (AgX turns pure blue into navy); energy 1.5 calibrated in-game. Sun 1.6 /
  ambient 0.7 for more contrast (both world modes). `fog_aerial_perspective = 1` so distant terrain takes the horizon colour.
- Heat (`weather.heat`: sun elevation × clear sky × dry road × `main.warmth` from biome warmth/aridity or route climate +
  season): full-screen shimmer quad on the camera (`heat.gdshader`, only near-horizontal rays beyond ~70 m, ALPHA 0 elsewhere)
  and a mirage on far asphalt at grazing angles (road shader). Not judged in stills (it is motion); October afternoon sun gives
  heat ≈ 0.25 in the Mittelmeer world.
- Water (workout world): own `water.gdshader` on the water surface; `world.py` writes the water depth per vertex (UV.x,
  VERSION 3) -> turquoise shallows, deep blue, foam line at the shore, wind-driven waves; the terrain under water is now a
  sand/gravel bed. Real-route packages keep the old in-terrain water.
- Verified 2026-10-27 (Godot MCP screenshots, Mittelmeer seed 5): light-blue sky gradient, coast from 40 m height (turquoise
  shore band, foam, waves, hazy hills).

## Weather (2026-10-27, both world modes)
- `weather.gd`: cloud cover, rain/snow, wind (direction, speed, gusts) as smooth noise of (seed, real Unix minutes) -> two
  riders on the same seed share the weather; biome-aware (`arid` suppresses rain), presets on key W (Auto/Sonnig/Wolkig/
  Regen/Sturm). Wind enters **only** the ride physics: `air_force(speed, headwind)` with the along-track component at rider
  height (×0.75); tailwind faster than the bike pushes. `trainer_grade()`/`bridge.py` untouched (SIM stays route geometry,
  CLAUDE.md boundary). Decision to keep: FTMS 0x11 has a wind field; feeding it would be a protocol + boundary change, ask first.
- `shaders/sky.gdshader` replaces PhysicalSky: gradient by sun elevation, sun disc/glow, desert haze, cumulus layer (half-res
  pass) + cirrus, rain greys it, stars at night; `Sky.PROCESS_MODE_REALTIME` (radiance 256) so ambient follows the clouds.
  `clouds.gdshaderinc` is shared with terrain/road -> moving cloud shadows on the ground match the sky.
- Rain particles (snow above the snow line), road wetness (darker, glossy, puddles), fog density up with overcast/rain, sun
  dimmed when overcast. Grass and trees bend with wind direction and gust strength (global `wind`).
- Verified 2026-10-27 (Godot MCP screenshots, Mittelmeer seed 5, 220 W set by script): Wolkig (cloud shadows on road/fields),
  Regen (streaks, wet road), Sturm (31 km/h tailwind -> 44.7 km/h at 220 W, grass flattened), Sonnig. selftest.gd covers
  headwind/tailwind force signs, weather determinism, desert never rains, presets.
- Not verified: night sky with clouds, snow particles (no ride above the snow line tested), real-route packages with the new
  sky (code path shared), a real trainer feeling it (by design it doesn't).

## Geological terrain + real tree species (2026-10-27, procedural world)
- **Terrain pipeline** (`terrain.py`, new, numpy/scipy only): macro relief (mountain chains along a seeded strike direction,
  domain-warped, style per biome: alpine ridged, temperate rounded, desert plateaus) -> regional erosion (2048 m regions +
  768 m margin on a 16 m grid: MST spill-fill, D8 receivers, implicit stream power via `spsolve_triangular`, deposition on
  flats, thermal talus; overlapping regions blended ±256 m -> seamless and order-independent; LRU cache, regions prefetched
  in a spawn process pool by `world.stream`) -> slope-aligned gullies (40/20/10 m) -> desert strata (`terrace`, per-layer
  hardness, only on walls) -> water -> road conform (`conform`: flat bench, cut 1:1 / fill 1:1.5 cones from every 10 m
  road piece, min/max over all pieces -> works between switchback legs). Valley shape per biome (`geo.vexp`: V/U/box canyon).
- **Switchbacks** (`world._hairpins`): steep/climb/descent regions in alpine biomes (`geo.kehren`) get a zig-zag curvature
  program (legs ±(90°−12°), hairpins R 16–22 m, fanning legs -> ~30–40 % slope between them); grade profile unchanged
  (trainer path untouched: `route.pos` heights still come only from scene grades). Blocks sit on a fitted hillside plane.
- **Materials** (`terrain.gdshader`): tiles carry UV = (wetness from flow, sediment), UV2 = (exposed rock, scree); rock is
  triplanar with strata bands and 50 m ribs, colour from the biome (desert walls = sandstone). Real-route packages have no
  UV -> unchanged look.
- **Trees** (`blender/trees.py` -> `models/trees.glb`, 12 species, each with `_lod`): Buche, Eiche, Birke, Fichte, Kiefer;
  Mallorca: Olive (gnarled, two stems), Pinie (umbrella), Aleppo-Kiefer, Steineiche, Zypresse, Kanarische Dattelpalme,
  Macchia-Busch. Workout world: species mix per biome (`BIOMES.trees`), olive groves in 7 m rows on terra rossa on
  Mediterranean farmland (`_grove`). Real routes: conifer/broad/palm/shrubs mapped to species by climate (base temp > 14.5 °C
  = Mediterranean) at load time, no rebuild needed. Every instance gets a hash yaw (`main.spin`); detail model to 200 m
  (shrubs 80 m), LOD beyond.
- **Verified 2026-10-27** (Godot MCP screenshots, no trainer): VO2 5×3 Reise seed 7 (switchback block on a forested slope,
  hairpin flush with terrain, pass chapel, rock massif), Wüste seed 3 (canyon with layered sandstone walls, stepped plateau),
  Mischwald seed 3 (cut slope beside the road; mixed beech/birch/spruce forest), Mittelmeer seed 5 (Aleppo/stone pines, holm
  oaks, cypress, olive grove rows on red soil). Tests: `route.py --test` (incl. `terrain.test`, `world.test`), selftest.gd.
- **Measured**: stream ready after 7.6–8.9 s in Godot (was 5.5–6.5 s; region prefetch pool, 8 workers); region ≈ 0.8 s each;
  near tile Ø ~100 ms; generator RSS ~390 MB; forest view 28.8 M triangles at 165 FPS (RX 9070 XT), 1080 draw calls.
- Debt: tiles still built serially (parallel tile jobs would cut startup to ~2–3 s); inner edge of tight hairpins can sit
  ≤ 0.3 m below the road (bench of a lower curve part); erosion fades out beyond 1.9 km from the road (far mountains are
  macro only); pool workers are orphaned if world.py is SIGKILLed (normal exit via stdin EOF is clean); tree crowns are
  stylised low-poly clumps (no leaf cards); Mallorca dry-stone terraces not modelled.

## Workout-driven procedural world (2026-10-27, default flow)
Start screen (`start.gd`, main scene) -> workout (`workouts/*.zwo|erg|mrc`, import) + biome/seed/ERG|SIM -> `main.gd` spawns
`uv run world.py stream`, which generates chunks ahead of the rider; `world_stream.gd` loads them. OSM routes stay reachable
via "Echte Strecke auf der Karte …" (picker); nothing geographic was removed (still needed for that mode).
- **Verified 2026-10-27** (Godot MCP screenshots, RX 9070 XT, no trainer, power set by script): VO2 5×3 (Reise: Flusstal,
  Seeufer-Dorf with church + city sign, alpine 9 % ramp, pass chapel), Gemischt 45 (Mittelmeer, SIM: dry hills, coast with
  headland, village), 59 km/h streaming run without holes. First ride-ready after 5.5–6.5 s; 165 FPS (vsync cap), 400–530
  draw calls, 1.4–2.6 M triangles, ~240 MB VRAM. Tile generation Ø 40–56 ms (fine 80 ms, mid 19, far 14, road 5);
  main-thread integration Ø 0.2 ms per tile (4 ms budget/frame).
- Not verified: real Kickr in ERG (FTMS 0x05 is spec-conformant and unit-tested, hardware untested by the AI); two riders on
  the same seed (same files by test, not ridden); rides > 1 h in one session.

### Decisions (procedural world)
- **Generator in Python, separate process, streamed** (not GDScript): numpy is ~50× faster than GDScript for the terrain field,
  the repo's test infrastructure is Python, and a separate process can never block the render loop. Protocol: stdin = rider
  distance per line, stdout = one JSON line per finished element; files are deleted after loading (regenerable from the seed).
  Godot: one loader thread reads files only; ArrayMesh/MultiMesh/ImageTexture are created on the main thread (RenderingServer
  threading is not enabled in this project).
- **Road first, terrain second.** Centre line = integrated curvature (smooth noise × scene strength, restoring term to a slowly
  drifting main heading, |κ| ≤ 1/55 m) and grade (scene mean + waves, clamped 13 %, smoothed twice). The road never returns
  near itself (test: points within 250 m are < 1.2 km apart along the road), so "nearest road point" is unique near the road.
  Terrain = flat bed SINK under the road for hw + 3.5 m (≥ the diagonal of a 2 m cell -> no triangle can poke through), 1:1.5
  embankment (6–30 m, < R_min so curve insides stay continuous), then the landscape: inverse-distance blend of coarse road
  samples (height smoothed 300 m, scene/biome parameters) + valley walls, cross slope, hills, mountains (soft/ridged/mesa),
  lakes/rivers/sea dug below a water level. IDW uses 12 neighbours minus the 13th's weight (else visible steps).
- **Time vs distance:** regions are laid out with a reference rider (240 W, 80 kg) on the planned grade; actual riders reach
  them earlier/later. The world never reshapes (two riders, reproducibility). Workout clock runs while pedalling, independent
  of speed. Road length = 2 × workout distance + 20 km (free ride 150 km); after the workout the road continues.
- **Reproducibility:** `world.py` key = sha1(VERSION, seed, biome, drama, density, variation, workout segments, length). Noise
  is hashed (stateless), RNGs seeded per purpose -> byte-identical files (tested). FTP/mass/graphics never change the world
  (except .erg files in watts without FTP in the header).
- **ERG vs SIM:** `main.gd::trainer_command` sends exactly one of `{"erg": W}` / `{"grade": %}` per packet; `bridge.py`
  keeps one mode and writes only that mode's FTMS command (0x05 target power or 0x11 simulation). ERG only while the workout
  has a target; free segments, after the workout and free ride -> SIM. Shifting in ERG = ±5 % intensity.
- Chunking: 128 m fine tiles (2 m, trees, grass maps) up to 1.6 km ahead, 256 m mid tiles (8 m), 1024 m far blocks (32 m)
  to 3.2 km; LOD is chosen per tile from road distance + rider window, skirts hide resolution seams; road in 250 m pieces with
  analytic normals (no seams).

### Known issues / debt (procedural world)
- Float precision: no origin rebasing; at 70 km from the start float32 positions jitter by ~8 mm. Rebase the world root every
  ~20 km if that becomes visible.
- Teleports (or riding > 1.6 km/min) can show holes briefly until mid tiles arrive (drop happens before replacement).
- No branching roads/junctions, no bridges/tunnels, no guardrails in the procedural world (signs.py/network.py are OSM-bound).
- Biome assets are the primitive tree/shrub/palm meshes; no cacti/olive trees; mesa rock is a colour, not geometry.
- Atmosphere follows the rider's distance (fog/haze/snow line/sun warmth); sun position is real time at the start biome.
- Generation is single-threaded Python (~3 s per km of road at fine detail); multiprocessing if faster riders need it.

## Working features (verified 2026-10-26)
- Route building (`route.py`): BRouter or GPX → AWS Terrarium DEM (z13) → Overpass OSM → terrain, roads, all streets,
  buildings (Blender models + procedural), trees, lamps, `ground.png`. Self-test: `uv run route.py --test`.
- **Biome system** (`biome.py`, since 2026-10-26): ESA WorldCover fills everything OSM doesn't tag; climate proxy
  (latitude + elevation) sets the treeline and tree species (conifer/broadleaf/palm); aridity (share of sand/scrub nearby) drives
  dry palettes, sparse grass and shrubs; alpine palette above the treeline; seasonal snow at runtime (`snow_line` shader global
  from `meta.climate` + month). Trees/shrubs never land on roads, paths or buildings (`bare` mask).
  Visually checked 2026-10-26 (Godot MCP screenshots, no bridge): `routes/biome-desert` (Tozeur→Nefta: sand, no grass, palms,
  shrubs), `routes/biome-alpine` (Stelvio: conifer valley, scree/alpine grass above treeline, January snow, July snow-free),
  `routes/biome-taunus` (temperate mixed forest, unchanged look).
  Polish 2026-10-26: `routes/biome-tropical` (Bali, southern hemisphere: lush tropical palette, rice-green fields, palms, no
  conifers, no autumn leaves); flat-roof clay buildings in arid regions; autumn leaves only near broadleaf trees and only in
  autumn (hemisphere-aware, none in the tropics); dust haze by aridity; drooping palm crowns.
  Regional buildings checked in screenshots: Bali (stilt houses + concrete), Nefta (flat roofs with stair boxes), Stelvio
  (Alpine style); Taunus unchanged. Stelvio switchbacks no longer show terrain walls.
- Runtime (`main.gd`): physics (P/v vs rolling + gradient + air), FTMS grade to the Kickr every 0.2 s (difficulty, virtual gears,
  clamp −10…20 %), peer ghost over UDP, real sun position, night lighting, GPU grass, minimap.
- Picker: 2D OSM map, BRouter routing, Nominatim search, builds routes via `route.py` subprocess.
- 3D route preview (`preview.gd`): "3D" next to saved routes, "3D-Vorschau" for new ones (`route.py --preview`, ~1 s cached);
  orbit/pan/zoom, route ribbon, start/finish poles, elevation profile coloured by grade with hover marker, labelled ×2 height toggle.
  Verified in screenshots (Stelvio, Taunus, new Ubud route); preview heights == build heights (0.0 m difference, 7478 points).
  Not exercised: "Losfahren" from the preview (would start the BLE bridge; code path = existing `_start`/`_build`).
- `bridge.py`: FTMS Kickr + Zwift Click/Play/Ride. Self-test: `python3 bridge.py --test`. Hardware is untested by the AI.

## Decisions
- **2026-10-26 Street network from the OSM graph** (`network.py`, replaces `route.py::ribbons`). Root causes of broken roads:
  Overpass node ids were ignored, every way was a separate strip with square ends (wedge holes outside turns, overlaps inside,
  83 % of junctions overlapping with 4–10 cm height steps from per-class lift + jitter); side streets were clipped at 2 m
  samples against route points (up to 2 m short of our edge); `*_link` got full motorway width; heights per way, so bridges
  raised over our route left metre-high steps at the adjacent ramp ways and bridges over other streets lay at grade.
  Now: ways split at shared node ids and re-joined into chains; one height solve over the graph (terrain + lift; bridges spanned
  between abutments by a Laplace solve; crossings without a shared node get clearance by `layer`/`bridge`, ramps ≤ 6 %
  (paths 12 %) spread along every connected road by Dijkstra, not across our route; embankment skirts that never land on
  another road); junction surfaces built from the arms' edge polylines (rounded corners = `road.corner_radius`, round join on
  the outside of bends, width taper, gore filled on acute ramps); side streets end in a mouth flush with our edge (edge-line
  break uses the same radius). Same position = same height (welded), terrain poke post-pass.
  Clearance cones are relative to the terrain (excess decays per metre; absolute cones hung mountain roads 300 m in the air) and
  skip crossings the terrain already separates (hairpins: `layer` is only a local order). One width function
  (`road.street_hw`, `road.ROADS`) for meshes, mouths and edge-line gaps.
  Measured, same OSM data old vs new, 300 random junctions of a 2.5 km Bad Homburg route through the A661 interchange:
  junctions with overlap 83 % → 20 %, overlap area 14.6 % → 4.1 %, height step inside overlaps p95 10.5 → 6.8 cm, junctions
  with a 0.15–3 m step 1.3 % → 0.7 %, hole area 1.5 % → 0.6 % (rest: width tapers, smoothed footway corners vs raw OSM).
  Builds (network stage): Bad Homburg 2.5 km 2 s (1654 junctions, 4 hull fallbacks, 45 grade separations, min clearance 3.0 m);
  Gotthard 17.6 km 1 s (558 junctions, 1 hull, min 2.4 m); Taunus 36.5 km 13 s (6556 junctions, 10 hull, 545 mouths, min
  0.2 m at park footways that connect within ~25 m). Terrain over the network ≤ 0 m on all three. Street triangles +45–60 %
  (Taunus 364k → 530k). `route.pos` byte-identical old vs new on all three routes (trainer untouched).
  Existing route packages keep loading but need a rebuild to get the new streets.
  Follow-up: hairpin apexes were drawn twice (our line is smoothed up to 6 m inside, our own OSM way outside ±hw+1 became a
  street). Our way is now recognised by class/name/ref (`network.own_key`) within hw + 1 + the smoothing offset (`rd["dev"]`).
  Gotthard: 558 → 554 junctions, 72 → 80 mouths; `route.pos` byte-identical, Bad Homburg unchanged.
- **2026-10-26 Delineators and guardrails** (`signs.roadside`, our route only, outside towns = WorldCover/OSM urban ±50 m,
  classes motorway…unclassified, paved). Delineators in opposite pairs every 50 m (DE rule spacing), in curves R/4 down to 10 m
  (approximation, not the RPS table), not in front of a guardrail or in side-street openings. Guardrails: OSM
  `barrier=guard_rail` within 25 m (own Overpass query, optional), else where the terrain 1.5–8 m beside the edge lies > 3 m
  below the road, and 30 m before/after bridges; gaps ≤ 25 m closed, pieces < 40 m dropped, open at side streets, ends lowered
  into the ground over 10 m (not at a bridge parapet). Beam = W profile in `structures.*` (road shader, steel colour), posts
  every 2 m in `sign_posts.bin`. Gotthard 17.6 km: 470 delineators, 4.9 km guardrail; Taunus 36.5 km: 620, 3.0 km.
  overpass-api.de refused connections on 2026-10-26 (also in the browser) -> `route.OVERPASS` falls back to mirrors
  (kumi.systems answered, data as of 2026-06-01; private.coffee). Gotthard: 0 OSM guardrails mapped within 25 m, so all
  4.9 km there come from the drop/bridge rules; OSM path only covered by the self-test. No reflectors on guardrails; side
  streets (`network.py`) get neither.
- **2026-10-26 Road furniture from OSM** (`signs.py`, `road.junctions`). Junctions are found geometrically (Overpass `out geom`
  has no node ids): a drivable way inside our carriageway that leaves at ≥ 20° and goes ≥ 4 m past the edge. Markings in the
  road shader via CUSTOM0 (broken edge line at mouths, solid centre 30 m before lights); cross lines (stop, give-way, zebra,
  Furt) as a small mesh. Signs only for our direction (right side, facing us), faces drawn by PIL into an atlas. Untagged side
  streets at tertiary+ roads get "give way" in Europe only (common there, rarely tagged). Traffic lights cycle (36 s, own phase
  each) but are visual only: riders never stop, trainer untouched. Measured on Taunus (36.6 km): 265 mouths, 20 lights,
  12 zebras, 14 Furten, 23 speed, 4 city, 114 street-name, 7 guidepost, 100 stop/give-way signs.
- **2026-10-26 Terrain is a road-aware triangle mesh** (`route.road_terrain` + `Ground`). A 20–45 m grid can't follow a road
  on a hillside: the old fit put the terrain 0.8 m below the road plus a cap to the lowest nearby road point, which looked like
  a dam (edge above ground: Taunus p50 0.82 m, Stelvio p50 3.9 m / p95 11.9 m). Now cross-sections every 2.5–15 m along the
  road (flat bed SINK = 0.15 m under the asphalt, then the real DEM cross-slope) replace the grid near the road, Delaunay
  joins them; measured edge gap 0.20 m p50 and p95 on all five test routes. Grass reads `heights.f32` (≤ 2048², ≤ 16 MB per
  package). Trainer path untouched (`route.pos` comes from `road_profile` as before). Side streets lie 4–14 cm above ground.
  Road/DEM height differences (ramps, cuts, DEM error) fade with a 1:1.5 embankment, not over 70 m (was a plateau at bridge
  ramps). Cross-sections continue under bridges at DEM height (else Delaunay spanned a sheet at deck level end to end, the
  bridge lay on the ground and lost its pillars). Bridges now have abutment walls (`road.bridge_mesh`).
- **2026-10-26 Road layout is data-driven and grade-separated** (`road.py`). Per route point the matched OSM way decides width
  (min 2.2 m half width for two riders), surface (asphalt/setts/unpaved → shader), markings (major: centre+edges, tertiary:
  centre, else none), bridge, tunnel. Bridges/tunnels are spanned in the height profile (no valley dips, no climbing over
  mountains – this changes trainer grades on such routes); our bridges get ≥5.5 m clearance over roads they cross (soft ramps),
  side-street bridges over our road likewise. Spurs to off-road waypoints are cut (≤300 m each way); intentional out-and-back
  climbs stay. Kinks are rounded (≤2.5 m off the OSM line, ≤6 m in hairpins). Buildings overlapping the asphalt are dropped.
  Tunnels are concrete galleries in an open cut (heightmap terrain can't have holes).
- **2026-10-26 Regional building styles** (`regions.py`): 12 styles chosen per building from route location (lon/lat boxes per
  continent) + local climate (arid → flat roofs everywhere, cold European mountain villages → Alpine). Each style names Blender
  models (`blender/houses.py`, 11 regional models added), wall/roof palettes and whether procedural fallbacks get gable roofs.
- **2026-10-26 Road heights = weighted Whittaker fit, not a 50 m Gaussian.** The DEM is unreliable on steep cross-slopes
  (±8 m lateral error × slope, switchback legs blur), which produced 20–27 % spikes on Stelvio (real: ~8 % avg, ~12 % max).
  Samples are down-weighted by cross-slope and the profile minimises grade changes; total climb and sustained real ramps stay
  (self-test: a true 20 % fall-line ramp keeps ≥18 %). Measured: Stelvio max 10 m grade 27→12 %, Taunus 18→12.4 %, net climb kept.
  **Both riders must rebuild their packages** (different heights ⇒ different grades).
- **2026-10-26 Biome data = WorldCover + OSM + climate proxy, all baked at build time.** WorldCover v200 (CC BY 4.0) is global,
  on AWS S3 as Deflate COGs → read with stdlib only (`zlib`, HTTP Range), ~100 KB per 1024² block at ~20 m, cached in
  `~/.cache/kurbel/worldcover`. OSM polygons stay on top (finer, local). No Köppen/WorldClim raster yet: latitude + lapse rate is
  ±5 K and ignores maritime/continental effects – fine for the visual choice of palette/species, upgrade if it misfires.
- **Season is runtime, not baked**: route packages stay valid all year; `kurbel.cfg [world] month` forces a month for testing.
- **Dimensions stay separate** (land class, aridity, alpine factor, season) instead of one biome enum.
- **Biome never touches physics**: `route.pos`/grade/trainer path is independent of all biome code (see CLAUDE.md boundaries).

## Fixed along the way (2026-10-26)
- A→B routes: `grade_at` no longer measures across the restart at km 0 (was a brief −10 % at the Kickr); loop flag comes from
  `meta.loop` (route.py decides once).
- Terrain poked through the road on hills (Stelvio 50 % of points, Taunus 8.6 %, up to 16 m): nearest-road heights were snapped
  to 20 m grid cells and switchback legs produced walls. `route.py::fit_terrain` now uses a KD-tree blend of the nearest route
  points plus a clearance cap (under the road in every touching triangle, ≤45° cut slope beyond) → 0 % on all test routes.
  The build prints `Gelände über der Fahrbahn: max …` as a check.
- "Weird colours": road shoulders were always green (now sampled from the ground texture); urban ground was concrete grey and
  grass took that colour (now garden green-grey).
- Grass shader `step(hash, density)` spawned grass at density 0 (hash == 0.0 far from origin) → strict `<`.
- S3TC padded `ground.png` to a multiple of 4 → texture drifted up to ~10 m, grass on the road → resize before compress.
- WorldCover edge-block padding overwrote the neighbouring 3° tile (found by the reviewer agent) → cropped + offline test.

## Known issues / debt
- Signs are German-style everywhere (yellow city sign, DIN-like colours); no country styles. Only our direction gets signs.
  No turn lanes/arrows (`turn:lanes`), no motorway destination signs (`destination`), no navi-style turn instructions yet.
- Street network (`network.py`): junction nodes joined by very short chains (< 15 m) get two overlapping surfaces instead of one
  cluster polygon (no polygon union without a new dependency); ~0.3 % of junctions fall back to a convex hull (paves concave
  corners). Streets in real cuttings/embankments the DEM misses can have < 5.5 m clearance; terrain is not cut for streets.
  Street tunnels are dropped (road ends at the portal). Junction surfaces have no markings and world-aligned sett pattern.
  There is no runtime chunk streaming: a package is one world, roads end only where OSM ways end (whole ways come back from
  Overpass `around:600`); a way's continuation beyond that isn't fetched.
- Hairpins whose legs are only ~10 m apart stay at ~3 m centre-line radius (geometry limit of smoothing); the camera heading is
  smoothed in `main.gd::_place` so it doesn't jerk. A "bulb" arc fit for hairpins would fix it properly.
- Tunnels are open-cut galleries, not bored through the mountain. Railways are not fetched, so bridges over railways get no
  clearance and railways aren't drawn.
- Surface does not change rolling resistance (deliberately: physics unchanged). Gravel CRR would be a separate, explicit feature.
- Preview fetches the road ways once with a 45 s timeout; if that fails, preview heights can differ from the build on routes with
  bridges/tunnels (message printed).
- Climate proxy: wrong for strongly maritime/continental places (e.g. Norway coast too cold, Siberia too warm). Fix: WorldClim/Köppen raster in `biome.climate`.
- Snow = monthly mean < 0 °C; no snowfall model, no snow on roofs/buildings.
- Aridity comes only from WorldCover; offline builds fall back to the old green OSM-only look.
- Irrigated fields in deserts get the dry farmland palette.
- Tree/shrub/palm meshes are primitive shapes; no species variety per region (e.g. no cacti, no birch).
- Ground texture ≤ 4096 px → 2–3 m/texel on long routes; WorldCover edges are noise-warped 20 m cells.
- Desert haze (turbidity/mie/fog by `meta.climate.arid`) is subtle.
- Plant density is per grid cell; long routes use 30–45 m cells → forests sparser than on short routes.
- Snow edge: grass (hash band) and terrain (noise band + slope) don't match exactly.
- Terrain beyond ~25 m from the road is still the 20–45 m grid (DEM-limited anyway). `heights.f32` is float32 (≤ 16 MB);
  16-bit (RG8 + manual bilinear in the grass shader) would halve it if packages get too big.
- Existing pre-2026-10-26 route packages need a rebuild to get biomes (they still load, without snow/biomes).

## Next deliverables
1. Procedural world: hairpin climbs, a simple minimap of the generated road, junction/side roads, region-specific props
   (olive trees, cacti, lighthouses), origin rebasing for > 2 h rides.
2. Stronger desert haze; region-specific tree meshes (birch/cactus/acacia); WorldClim climate.

## 3D route preview (milestone 2, done 2026-10-26 – original plan kept for reference)
Goal: orbitable 3D terrain with the route draped on it plus an honest elevation profile, without building the full world.
Flat picker stays the default and the fallback.

- **One geo pipeline**: add `route.py --preview <via>` that only does BRouter → resample → DEM (reuse `Proj`, `Dem`, the 50 m
  height smoothing) on a coarse grid (≤ 256², z11–12 tiles) and writes `user://preview/{terrain.f32, route.pos, meta.json}`.
  Seconds instead of minutes, no Overpass. Heights/distances are therefore identical to what the ride will use.
- **Saved routes** preview directly from `routes/<name>/terrain.*`, `route.pos`, `ground.png` (no rebuild).
- **`preview.gd`** (new, owned by map-ux-engineer): `SubViewport` + `Node3D` inside the picker; one grid `ArrayMesh`;
  shader drapes the cached OSM tiles from `osm_view.gd` (new route) or `ground.png` (saved route); route as a ribbon lifted 2 m;
  start/finish markers; orbit (drag), pan (right drag), zoom (wheel), "frame route" button.
- **Elevation profile**: 2D `Control` under the 3D view from `route.pos` (distance vs elevation, gradient colour bands); hovering
  moves a marker in 3D. No vertical exaggeration by default; an optional, labelled ×2 toggle.
- **Safety**: preview never opens the bridge socket; only `main.gd` talks to the trainer.
- Steps: (1) `--preview` + self-test, (2) saved-route preview, (3) live preview after BRouter in the picker, (4) profile + hover.
