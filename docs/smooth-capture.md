# Smooth capture

Trailers recorded live on a weak laptop come out at 25-30 fps with hitches. Smooth capture records **offline**: the
game runs as slowly as it needs to, but every game update moves its clock by exactly the same step and the frames
are saved, so the video has exactly 1/60 s of game time between frames. ffmpeg then makes a 1080p60 (or 4K) H.264
video.

```
python tools/smooth_capture.py build          # smoothcap.dll, staged as a mod folder in out\capture-build\smoothcap-mod
python tools/smooth_capture.py selftest       # its self-test (no game)
python tools/smooth_capture.py record --name castle --seconds 10 --auto-continue --wait 20
python tools/smooth_capture.py encode --name castle --4k --keep-frames     # again, from kept frames
```

`record` makes a scratch library (`out\capture-lib`, with smoothcap in it), copies the test save (`out\test-save`, never
the real one) and a capture copy of the settings into `out\capture-<name>\`, starts Savage Wind with a control port,
waits until Link is in the world (with `--auto-continue` it also presses A every 20 s while there is no world yet),
checks smoothcap's status (clock hooked, F12 recording there, controller step its own), runs the `--ctl` commands,
goes to `--goto "x y z"` (or a line from the `pos` command) and checks Link got there (the `goto` again up to 3 times,
then a plain error: the game can ignore it while he falls), waits `--wait` seconds (or for Enter with `--enter`),
records `--seconds` of **game** time, closes the game, encodes `out\capture-<name>\<name>.mp4` (60 fps, CRF 18,
libx264 `slow`, lanczos to 1920x1080 or 3840x2160 with `--4k`) and deletes the frames unless `--keep-frames`.
`attach` does the capture part on a game you started yourself (smoothcap in its library, `SAVAGEWIND_CONTROL=<port>`).
`check-log` proves the step was fixed: start the capture with `--env SAVAGEWIND_CLOCK_PROBE=1000`, and it reads the
"[clock]" lines (game updates and the game's own time) while the recording was on from `savagewind.log`.

A 10 s capture at 60 fps is 600 saved frames (and 1200 rendered): at a few frames a second that takes several minutes.
The frames take about 2-4 MB each on the disk; the tool refuses to start without room for them and stops when less
than 4.5 GB is left (Windows' swap file grows 3-4 GB while the game runs).

Before any folder is made or the game starts, the tool refuses while `<Savage Wind>\build\frames` has any
`rec_*.png` (an F12 recording, or leftovers): the runtime counts its frames from 0 at each game start, so they would be
overwritten. Move them somewhere else first. `out\game.lock` names the tool's pid and start time; a lock whose
process isn't running any more is removed (a lock without a pid, another job's, is waited for). After a rebuild the
scratch library's copy of smoothcap.dll is replaced when it differs from the staged one.

## How Savage Wind keeps time (0.4.1-alpha.4)

Read from the Savage Wind source at the 0.4.1-alpha.4 commit (76e61af), `runtime/hle/framepace.c`:

- BotW's logic does not count frames: its clock is `ksys::VFR`. On the Switch the frame loop asks for a present every
  2 vsyncs (30 fps; 3 after slow frames, 20 fps) and VFR turns that into the step: ratio 1.0 = 1/30 s, 1.5 = 1/20 s.
  About 1600 places read the step (time of day, Havok's physics step, animation, AI, stamina).
- Savage Wind runs the game at any frame rate by writing VFR's ratio itself, once per game update, in
  `rt_vfr_update(vfr)`: the step it **measured from the real time** (smoothed, at most 3% change a frame, 1/300 s to
  1/20 s, time owed paid back). So a slowed-down game takes long steps: recording a slow game just gives a choppy
  video, whatever the encoder does.
- There is no fixed-step, frame-advance or speed option and no pause/step control command. `SAVAGEWIND_FRAMEPACE=0`
  gives the console's own clock (also from the real time: 30 or 20 fps steps), `SAVAGEWIND_FPS` only sets the pace,
  `SAVAGEWIND_SCRIPT_CLOCK` only changes how test scripts count frames.
- Saving frames: the `shot <file>` command (the game image of the last present, PNG); the start-up lists
  `SAVAGEWIND_SHOT_AT`, `SAVAGEWIND_SCREENSHOT_EVERY`/`_FROM` and `SAVAGEWIND_WINDOW_SHOT_AT` (absolute frame numbers
  from start-up: can't be started on cue); and the F12 recording, the exported `g_record_frames`: while it is 1,
  every present with an even frame number saves the game image to `<data root>\build\frames\rec_<frame>.png`. That
  save is synchronous, inside the present (`renderer_flush`, readback, PNG), so the game waits for each frame.
- What modhost can reach: `hle_override` only changes calls through the dispatcher (guest code), `hook` needs game
  code rebuilt with hook slots, and the overlay host's `menu_render` runs inside the present while the frame's
  command buffer is being recorded (no place for a readback). But the game code calls the runtime through
  `swgame.dll`'s **import table** from `savagewind.exe`: reading `extracted\swgame.dll`'s imports shows 57 from
  `savagewind.exe`, among them `rt_vfr_update`, `rt_gui_speed`, `rt_arrow_gravity`, `rt_snd_frame_floor`,
  `rt_solver_steps`. `savagewind.exe` exports `g_record_frames` (read from `savagewind.dll.a`).

## The design

1. **smoothcap.dll** (native mod, `native/examples/smoothcap/smoothcap.c`) replaces `swgame.dll`'s imports of
   `rt_vfr_update`, `rt_gui_speed`, `rt_arrow_gravity` and `rt_snd_frame_floor`. The runtime's own functions still
   run; while a capture runs, the step is then overwritten with a fixed one: VFR's ratio and new ratio, its interval
   (1 for steps under 0.7, as the runtime does at 60 fps and up), the menus' speed, the arrows' gravity and the sound
   frame floor. With `SAVAGEWIND_NO_CONTROLLER_STEP=1` (the tool sets it) and the clock hooked, the mod also writes the
   controller hold counters' step (site `controller_step`): the fixed step during a capture, the measured frame length
   otherwise. Without the hook it doesn't claim it (status `controller_step` false) and the tool won't capture.
2. **Saving** is the runtime's own F12 recording, switched on and off by the mod through `g_record_frames`, only from
   the game thread: each update of a capture sets it to "recording or not", and `smoothcap stop` only changes the
   state, so the next update turns it off (status `recording`). It saves
   one present in two, so the fixed step is half the video's frame time: **1/120 s for a 60 fps video** (ratio 0.25,
   what Savage Wind runs at 120 fps), 1/60 s for `--fps 30`. Eight updates at the fixed step come first (the
   anti-aliasing history settles), recording runs for 2 x frames + 3 updates (the tool drops the extra frames), two
   more follow, then the clock is the runtime's again.
3. **tools/smooth_capture.py** sends `smoothcap start <frames> <fps>`, moves each finished `rec_*.png` from
   `<Savage Wind>\build\frames` into `out\capture-<name>\frames\000000.png...` (in frame order, each once its PNG
   ends with its IEND chunk), sends `smoothcap stop`, waits until the status says off (recording off too) and no new
   `rec_*.png` came for 2 s, deleting the extra ones, then removes the `buildrames` folders it created (the runtime
   only makes the last one), stops the game with the control command `exit`, and encodes.

Commands (developer mode): `smoothcap start <frames> [fps]`, `smoothcap hold <fps>|off` (the fixed step without
saving, to look at it), `smoothcap stop`, `smoothcap status`.

## Verified and not verified

Verified in the game (2026-11-18, Savage Wind 0.4.1-alpha.4, test save, 1920x1080 window, noon frozen):

- A 10 s capture: 600 frames of 1920x1080, every one different (no exact or near duplicates, ffmpeg's mpdecimate
  keeps all 600, freezedetect finds none), the change between frames even (0.9 to 2.2 grey levels, no spikes).
  `check-log`: the step was 0.250 (1/120 s) in all 455 probe samples, FIXED. The 1080p60 MP4 has 600 frames, 10.00 s.
- Cost: 458 s of real time for the 10 s (about 46x slower), 2.7 GB of PNGs (about 4.4 MB a frame); the encode
  (x264 slow) took about a minute.
- For comparison, the same game recorded live (ddagrab, 60 fps): the game ran at 41 fps (frames over 33 ms: 27, worst
  75 ms) but the recording has only about 200 new pictures in 600 frames, 1 to 22 frames apart.
- The scripted Continue (`--auto-continue`) can come too early when the title runs at 200+ fps: the run stayed on
  "Load save data?" until a `press a`. `record --auto-continue` now also presses A every 20 s while there's no world
  (not tried in the game yet). The `shot` command only takes a .png name inside the game's folder.

Verified before the game test:

- From the source: how the clock works (above), that the F12 recording saves synchronously in the present, the VFR
  field offsets the runtime writes (0x28 changed, 0x30 new ratio, 0x34 interval, 0x3c ratio) at 0.4.1.
- From the files: `swgame.dll` imports those runtime functions from `savagewind.exe`; `savagewind.exe` exports
  `g_record_frames`.
- smoothcap's self-test (`selftest`, all passed): the import-table patch (on the self-test DLL's own import of
  GetCurrentProcessId), the phases, the fixed values on a made-up VFR, the free-camera near-stop left alone, hold and
  capture inside a hold. smoothcap.dll builds with the zig that ships with Savage Wind and imports only kernel32 and
  the C runtime.
- tests/test_smooth_capture.py: settings copy, frame order and moves, cleanup, disk guard, ffmpeg command, a real
  ffmpeg encode of made-up frames (when ffmpeg is on PATH).

Not verified (needs the game test):

- That the game reads the overwritten step in the same update (the runtime writes the same fields at the same point,
  so it should).
- The interval's "changed" flag: set when the interval changes from the game's last one; whether the game needs more
  around a change is unknown.
- The saved image's size: the game's own final image (with its HUD, without Savage Wind's menus) at its render size,
  not the window's. The encode scales to 1080p/4K either way; for sharp frames start from settings with a high
  upscaler quality (`--settings`).
- Whether the controller step written in the update reaches the controllers in the same frame.
- The system tick: the game code reads it through `rt_ticks` (also a `swgame.dll` import), and that stays the real
  time. Whatever the game times with it (rather than with VFR) still runs at the real speed during a capture. It is
  left alone on purpose: a tick that stops between updates could hang a wait that spins on it.

### Recheck in game (next session; the 2026-11-18 fixes were made offline)

1. `--auto-continue` from the title screen: the A press every 20 s gets past "Load save data?" into the world.
2. `smoothcap status` before the capture shows `clock_hooked`, `controller_step` and `record_available` true; the tool
   starts. `attach` on a game started without `SAVAGEWIND_NO_CONTROLLER_STEP=1` still captures (controller step the
   runtime's).
3. `--goto "x y z"` (or a `pos` line) after a fall: Link ends up there (the tool says "at ..."), or the tool stops
   with "goto didn't take Link to ..." instead of capturing where he is.
4. After the capture: status `recording` false, F12 still toggles the recording by hand afterwards, no `rec_*.png`
   left in `<Savage Wind>\build\frames` and the `build\frames` folders the tool made are gone.
5. A `rec_00002.png` put into `build\frames` first: the tool refuses before starting the game.
6. After `build` with a changed DLL: "smoothcap was rebuilt: putting the new DLL into the library" and the log's
   smoothcap line comes from the new DLL.
7. A `out\game.lock` left by a killed run (its pid gone): removed at once instead of a 150 min wait.

## Limits

- **Nobody can play at a few frames a second.** Set the scene up first (`--ctl` commands, or by hand before pressing
  Enter with `--enter`), then capture what the game does by itself: enemies, weather, physics, an event, a ghost
  replay that follows game time. Scripted input (`SAVAGEWIND_INPUT`) counts presented frames from the start, so it
  can't be lined up with a capture started later; during a capture a presented frame is 1/120 s of game time.
- Control commands sent during a capture land on whatever frame is presented then: they are not on game time.
- Movies (pre-rendered cutscenes) are paced by the real time: they don't capture smoothly.
- No sound is captured: the music goes on in editing.
- Events whose waits count frames (Savage Wind holds `Demo014_0` at 30 fps for that) can misbehave at 1/120 s steps:
  capture those with `--fps 30`.
- Frame generation must be off (the capture settings turn it off): generated frames are presents of their own.
- Made for Savage Wind 0.4.1-alpha.4. If `swgame.dll` doesn't import `rt_vfr_update`, the mod logs it and its
  commands answer with an error; nothing else changes.
