# Kurbel – working rules for Claude Code

Indoor cycling sim (Zwift replacement for two riders). Godot 4.7 (GDScript) + Python `uv` scripts.
Two world modes: **workout world** (default, `start.tscn`: procedural, generated from a workout by `world.py`) and
**real routes** (OSM/DEM packages from `route.py`, via the map picker).
User-facing docs: `README.md` (German). Status, debt, priorities, decisions: `docs/STATUS.md`.
Code comments and UI strings are German – keep it that way.

## Architecture (data flows one way)

```
route.py (offline bake, Python)            main.gd (runtime, Godot)              bridge.py (BLE, Python)
BRouter/GPX → DEM (AWS Terrarium) ─┐       loads routes/<name>/* into meshes,    FTMS Kickr + Zwift buttons
Overpass/OSM ──────────────────────┼─►     MultiMeshes, shaders; physics;   ◄──► UDP 47800/47801 (JSON)
ESA WorldCover → biome.py ─────────┘       grade_at() → trainer_grade() ──►      sim_command() clamps ±40 %
                routes/<name>/ (binary files + meta.json)
```

- `route.py`: everything geographic happens here, once, at build time. Output contract: `*.pos/*.nrm/*.idx/*.col/*.uv/*.uv2` (float32/int32 raw), `*.bin` MultiMesh buffers (12 floats/instance, houses 20), `ground.png` (RGB = ground colour, **A = grow code** read by `terrain`/`grass` shaders), `meta.json`.
  `terrain.*` is an arbitrary triangle mesh (`route.Ground`: coarse grid + fine cross-sections along the road, `road_terrain`);
  only old packages and `--preview` have a W×H grid there. Grass heights come from `heights.f32` (`meta.heights` = [w, h, spacing]),
  else from `terrain.pos` as grid. Never index `terrain.pos` as a grid in new code.
  Signs: `road.c0` (CUSTOM0, 4 floats/vertex), `marks.*` (painted cross lines), `signs.png` (atlas), `sign_plates.bin`
  (16 floats: 3×4 transform + atlas rect u0 v0 u1 v1), `sign_posts.bin` (also guardrail posts), `signals.bin`, `delineators.bin`
  (12 floats); guardrail beams are part of `structures.*`. All optional.
- `biome.py`: land cover (WorldCover + OSM classes), climate proxy (latitude + elevation), ground palette, tree/shrub mix. Pure numpy, no Godot knowledge.
- `road.py`: road layout. Route cleanup (spurs), line smoothing (≤2.5 m off, ≤6 m in hairpins), OSM attributes per route point
  (`match_ways`: half width, surface, marking, bridge, tunnel), grade-separated crossings, `road_profile` (heights; bridges/tunnels
  spanned, ≥5.5 m clearance over other roads), bridge/tunnel meshes. Surface code in meshes = `uv2_code()` (negative, so old
  packages with UV2.y = width still render as asphalt).
- `signs.py`: road furniture from OSM nodes + way tags: junction markings (`lane_marks` -> mesh CUSTOM0 = edge line broken
  left/right, centre solid), stop/give-way lines, zebra, traffic lights, speed/stop/give-way/city/street-name signs, guideposts;
  sign faces drawn with PIL into one atlas; `roadside`: delineators (50 m, closer in curves) and guardrails (OSM
  `barrier=guard_rail`, drop > 3 m beside the road, ±30 m at bridges) outside towns on our route. Visual only.
- `network.py`: all other streets/paths as one mesh (`streets.*`) from the OSM graph (shared node ids, never proximity):
  ways split at shared nodes, re-joined into chains over degree-2 nodes; heights solved once over the graph (terrain + lift,
  bridges spanned between abutments, clearance over crossings without a shared node via `layer`/`bridge`, ramps ≤ 6 %
  spread along connected roads by Dijkstra, embankment skirts); junction surfaces from the arms' edge polylines (rounded
  corners, gore fill on acute ramps, earcut; fallback: cross-section polygon, then convex hull); side streets end in a
  `mouth` flush with our road edge. Tunnels are dropped (street ends at the portal). Our route's own ways are not drawn.
- `regions.py`: building style per world region + climate (model choice, wall/roof palettes, flat vs gable). Model names must exist in `route.MODELS` and `blender/houses.py` (self-test checks the first).
- `main.gd`: no geographic queries, no network except OSM map tiles (`osm_view.gd`). Reads baked files only.
- `picker.gd`/`osm_view.gd`/`minimap.gd`: 2D Mercator map UI. Picker runs `route.py` as a subprocess (build, or `--preview`).
- `preview.gd`: 3D route preview (own `SubViewport` world, terrain shader, route ribbon, elevation profile). Loads any route package
  via `main.gd`'s static `load_array_mesh`/`load_ground`. `route.py --preview DIR` writes a coarse package (no OSM) from the same
  `ground_grid()` as the build, so heights/grades are identical.
- `bridge.py`: owns all trainer I/O. Game sends `{"grade": %}` (SIM) or `{"erg": W}` (ERG) every 0.2 s – one key per packet.

### Workout world (no geodata)
```
workout.py (.zwo/.erg/.mrc -> segments, zones, blueprint)  ->  world.py (plan -> road -> field -> tiles)  ==stdin/stdout==>
world_stream.gd (loader thread reads files, main thread builds meshes, 4 ms/frame)  ->  main.gd (physics, workout clock, ERG/SIM)
```
- `workout.py`: stdlib only, the single workout parser (start screen calls `--list`). Blueprint = phases by time, interval sets.
- `terrain.py`: geology for world.py (noise, mountain chains, regional erosion `Regions`, gullies, strata, road `conform`).
  Pure functions of world position + seed; regions blend across borders. No Godot, no workout knowledge.
- `blender/trees.py` -> `models/trees.glb`: tree/shrub species (+ `_lod`), vertex colours for `props.gdshader`; names = `main.TREES`.
  `blender/deco.py` -> `models/deco.glb` (house surroundings, cars; placed by `world._deco`) + `models/animals.glb` (`animals.gd`).
- `world.py`: `plan()` maps blueprint -> regions (scene × biome, length = duration × reference-rider speed); `_road()` builds
  the centre line first; `field(x, z)` = terrain/colour/vegetation masks anywhere (road bed, embankment, landscape); `tile()`/
  `road_chunk()` write the same file formats as `route.py` (`save_mesh`, 12-float instance buffers, house rows). Biomes and
  scenes are data (`BIOMES`, `SCENES`, `MAPPING`). Everything is a pure function of (workout, seed, settings, VERSION):
  bump `VERSION` when output changes.
- Tile keys: `n i_j` 128 m/2 m, `m` 256 m/8 m, `f` 1024 m/32 m, `r k` road piece (50 points). Fine tiles ship grass maps
  (`.g.png` + `.h.f32`, MARGIN ≥ grass radius); `main.set_grass` switches them by the rider's tile.

### Conventions
- Coordinates: local metres around the route centre, `x` = east, `z` = **south** (Godot −Z = north), `y` = metres above the route's lowest point (`meta.climate.elev0` = that point's real elevation). `Proj` in `route.py` is the only lon/lat ↔ xz conversion; `meta.json` carries `lon0, lat0, kx, ky`.
- Determinism: all randomness in `route.py` comes from seeded `np.random.default_rng(n)`; shaders hash world positions. Same inputs → same files.
- New route files must be optional at load time (`_instances`/`_load_mesh` skip missing files), so old route packages keep working.

### Hard boundaries
- **ERG is the only exception to the next rule**: in ERG the workout target (W) controls the trainer and grade is visual/physics
  only. `main.gd::trainer_command` must keep returning exactly one key; `bridge.py::command` must send only the active mode.
  Workout power never feeds into road geometry (world.py uses only zone/intensity class for scene choice and FTP fraction for
  the reference speed).
- **Trainer resistance (SIM) depends only on route geometry**: `road.py::road_profile()` (DEM → Whittaker-smoothed heights) → `route.pos` → `curve` → `grade_at()` → `trainer_grade()` (difficulty, gear, clamp −10…20 %) → `bridge.py`. Changing `road_profile` changes what riders feel: measure grade percentiles before/after on a mountain and a hilly route. Biome, season, snow and visuals must never feed into it. Don't touch `bridge.py` protocol code without a demonstrated need; run `python3 bridge.py --test` if you do.
- Picker/preview code must never send to the bridge (only `main.gd` does).
- `network.py` is visual only: it reads `rh`/`rhw` of our route but never changes them (`route.pos` must stay byte-identical).
- `world.py` never touches the trainer; `world_stream.gd` never sends to the bridge.
- Hot paths: `main.gd::_process` (every frame), `world_stream.gd::update`, all shaders (grass shader runs ~50k instances). No file/network/geo lookups there; precompute in `route.py` or `_build_world`.

## How to work
1. Read the code you touch first; the codebase is small (~3k lines) – read whole files, trace the data flow.
2. Extend existing systems (`route.py` stages, `main.gd::_build_world`, shaders) instead of adding parallel ones. No new dependencies without asking.
3. Keep diffs focused; leave `ponytail:` comments on deliberate shortcuts (ceiling + upgrade path).
4. Add/extend the assert-based self-tests (`test()` in each Python file).
5. Validate (below). Report what actually ran; never claim hardware testing.
6. Record decisions/debt/next steps in `docs/STATUS.md` (short, dated) – not here.

## Validated commands
```sh
uv run route.py --test          # route + road + network + regions + signs + biome + workout + world self-tests (offline)
uv run world.py --test          # procedural world only (determinism, seams, road limits, terrain under the road)
godot --headless --path . -s res://selftest.gd          # workout clock, target power, ERG/SIM command exclusivity
uv run world.py info --workout workouts/vo2_5x3.zwo --seed 7    # regions by time/distance
uv run world.py preview --workout F --seed N --png out.png     # top-down render of the generated world
python3 bridge.py --test        # FTMS/Zwift protocol parsers
godot --headless --path . --quit                       # GDScript parse check (silent = ok)
uv run route.py <name> --via "lon,lat|lon,lat|…"       # build a route (network, 1–3 min, caches in ~/.cache/kurbel)
uv run route.py --via "…" --preview /tmp/x             # coarse 3D-preview package only (seconds when tiles are cached)
godot --path . -- --fake        # run without hardware
```
Visual check: Godot MCP (`run_project` + `take_screenshot`). Workout world: in the start scene `run_script`
`s.list.select(i); s._on_pick(i); s._go()`; set `main.power`/`main.cadence` to ride without a trainer, `main.dist` to jump.
Real routes: `Engine.set_meta("route", …)` or `kurbel.cfg [route] name`.

## Subagents (`.claude/agents/`)
`geo-environment-specialist` (geo data, biome rules), `procedural-world-engineer` (shaders, meshes, instancing in `main.gd`),
`map-ux-engineer` (picker / route preview), `road-network-engineer` (network.py: OSM road topology, junctions, ramps, grade separation), `performance-regression-reviewer` (read-only review before calling work done).
Delegate only independent or context-heavy work; give each implementing agent explicit file ownership.
