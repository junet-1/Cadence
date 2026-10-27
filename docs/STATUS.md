# Kurbel – status, decisions, next steps

Short and current. Newest decisions at the top of their section. How to work: `CLAUDE.md`.

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
