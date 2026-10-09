// Facade Bus v2 - camp-bus reactive pattern for the lotus / flower-of-life DJ facade.
// Pixelblaze v3, 1215 px, 2D camera map centred on the flower of life.
//
// Layers, per pixel:
//   1. a vibe (camp-bus `mode`), chosen per ZONE - each zone can follow the global vibe or
//      run its own (zv0..zv6); vibe changes crossfade over 2 s per zone
//   2. a brightness envelope: beat pulse, or with `split` on, the audio split -
//      bass -> mushrooms, mids -> flower grid, beat -> ring + crown, highs -> leaves
//      Audio comes from the Pixelblaze sensor board when it hears music (zero latency,
//      works without the bus), else from the camp bus (audio/energy + audio/beat).
//      Music also drives the vibes themselves: louder = faster flow, beats launch things.
//   3. effects, each with a zone mask (bit z = zone z), mixed in by the camp-bus colour rule
//
// Everything exported is driven by tools/facade_bridge.py (setVars, never saved), and the
// Pixelblaze's own pattern page has controls for the same things (see "UI controls" at the end:
// last writer wins; control values are saved on the Pixelblaze and restored on load). Effects are
// triggered by resetting their timer (e.g. flashT = 0); timers count up in seconds here.
// Colours are hue/sat/val 0..1: mixing in HSV keeps the native hsv() fast path (a scripted
// hsv->rgb per pixel costs ~2/3 of the frame rate at this pixel count).
// Performance: each vibe is its own small function, dispatched through VIBE_FN. One big
// function with every vibe's locals cost ~30% of the frame rate (the VM sets up all of a
// function's locals on every call), so keep render2D and the per-vibe functions lean.

// ---- zones (ZONE_RUNS is generated from maps/<run>/zones.json by the bridge's --deploy) ----
// 0 ring  1 grid  2 mushroom_left  3 mushroom_right  4 leaves_left  5 leaves_right  6 crown
// 7 mushroom_both: a cloned data line - each index lights a piece on BOTH mushrooms, so it
//   counts as targeted when either mushroom is, and follows the left mushroom's vibe/envelope
var ZONE_RUNS = [0, 4]  // @zones
var NZ = 8
// Mirrored strip order (MIRROR_RANK, computed at start-up - see "start-up tables"): left-side LEDs
// are ranked in wiring order and each right-side LED takes the rank of the left-side LED nearest to
// its mirror image, so index-based animations (snake, twinkle, mycelium) play symmetrically. Nothing
// is baked for it: a per-LED literal cost 5 KB and a packed one 1.3 KB of the ~38 KB of bytecode the
// Pixelblaze will load, while the search takes about a second, once.

// ---- camp state ---------------------------------------------------------------
export var vibe = 5        // 0 cozy 1 party 2 chaos 3 wave 4 jumping 5 ethereal 6 snake
                           // 7 twinkle 8 heartbeat 9 aurora 10 meteor | facade: 11 bloom 12 kaleido
                           // 13 lotus 14 spectrum 15 fireflies 16 cascade 17 mycelium 18 butterfly
                           // 19 mosaic 20 wings 21 vortex
export var zv0 = -1        // per-zone vibe override, -1 = follow `vibe`
export var zv1 = -1
export var zv2 = -1
export var zv3 = -1
export var zv4 = -1
export var zv5 = -1
export var zv6 = -1
export var master = 1      // camp/mode: show 1, quiet 0.35 (and the controller's fader)
export var speed = 1       // cmd/*/speed multiplier
export var split = 1       // audio split across zones (0 = whole facade pulses together)
export var autoMode = 0    // full auto: new random vibe / zone vibes / speed / effect every autoSeconds
export var autoSeconds = 30
export var autoLeft = 30   // seconds until the next auto change (read by the controller)
export var bpm = 0         // 0 = no music: free-running clocks, no pulsing
export var beatT = 99      // seconds since last audio/beat (bridge resets to 0)
export var bpmSync = 1     // camp/sync.bpm_sync
export var pulseSync = 1   // camp/sync.pulse
export var bass = 0
export var mid = 0
export var high = 0
export var loud = 0

// ---- local audio: the Pixelblaze sensor board --------------------------------------------
export var frequencyData          // 32 frequency bins, filled in by the sensor board
export var energyAverage          // overall level, filled in by the sensor board
export var audioSrc = 0           // 0 auto (sensor while it hears music, else bus) | 1 sensor | 2 bus
export var sensorGain = 1         // sensitivity: >1 reacts to quieter input, <1 needs it louder
export var aBass = 0              // the levels everything below actually uses, 0..1
export var aMid = 0               //   (exported so the controller can show meters)
export var aHigh = 0
export var aLoud = 0
export var aLive = 0              // 1 while the sensor hears music
export var aBpm = 0               // tempo the sensor's beat detector is following

// ---- effects: colour H/S/V, intensity I, zone mask Z (255 = all), timer T -----------------
export var flashH = 0
export var flashS = 0
export var flashV = 1
export var flashI = 1
export var flashDur = 0.6
export var flashZ = 255
export var flashT = 99

export var pulseH = 0
export var pulseS = 0
export var pulseV = 1
export var pulseI = 1
export var pulseDur = 2
export var pulseZ = 255
export var pulseT = 99

export var tintH = 0
export var tintS = 0
export var tintV = 1
export var tintI = 1
export var tintDur = 2.5
export var tintZ = 255
export var tintT = 99

export var chargeH = 0
export var chargeS = 0
export var chargeV = 1
export var chargeZ = 255
export var chargeAmt = 0   // bridge adds per hit, bleeds off here

export var sweepH = 0
export var sweepS = 0
export var sweepV = 1
export var sweepI = 1
export var sweepSpeed = 1
export var sweepZ = 255
export var sweepT = 99

export var waveH = 0       // shockwave: one ring expanding from the centre (waveDir 1), or - halo / iris - a ring
export var waveS = 0       // starting at the flower's ring and closing in on the centre (waveDir -1)
export var waveV = 1
export var waveI = 1
export var waveZ = 255
export var waveT = 99
export var waveDir = 1

export var ringsH = 0      // rings: `count` rings chasing out from the centre
export var ringsS = 0
export var ringsV = 1
export var ringsI = 1
export var ringsDur = 2.5
export var ringsCount = 3
export var ringsZ = 255
export var ringsT = 99

export var spiralH = 0     // spiral: `spiralArms` arms turning around the centre (dir 0 and 12 arms = sunburst: straight rays)
export var spiralS = 0
export var spiralV = 1
export var spiralI = 1
export var spiralDur = 3
export var spiralDir = 1
export var spiralArms = 3
export var spiralZ = 255
export var spiralT = 99

export var wipeH = 0       // wipe: fill left to right, hold, release
export var wipeS = 0
export var wipeV = 1
export var wipeI = 1
export var wipeDur = 3
export var wipeZ = 255
export var wipeT = 99

export var rainH = 0       // rain: random twinkles in the colour
export var rainS = 0
export var rainV = 1
export var rainI = 1
export var rainDur = 4
export var rainZ = 255
export var rainT = 99

export var cometH = 0      // comet: head + tail racing along every strip in the zones
export var cometS = 0
export var cometV = 1
export var cometI = 1
export var cometSpeed = 1
export var cometZ = 255
export var cometT = 99

// ---- geometry, computed once ----------------------------------------------------------
var ASPECT = 2.0           // facade width / height (map axes are each stretched to 0..1)
var zoneOf = array(pixelCount)
var along = array(pixelCount)   // 0..1 position along this pixel's stretch of strip
var distC = array(pixelCount)   // distance from the flower-of-life centre (aspect-corrected)
var angC = array(pixelCount)    // angle around the centre, 0..1
var MIRROR_RANK = array(pixelCount)   // mirrored strip rank, computed below
var sparkle = array(pixelCount)       // twinkle sparkles and rain drops: t when each was born (shared table)
var facet = array(pixelCount)         // which cut-out pane (stretch of strip) each LED sits in, mirrored L/R (mosaic)
// Array memory (`mem` in the stats = free words, ~0.5k left): per-pixel tables are zoneOf, along, distC,
// angC, sparkle, MIRROR_RANK and facet; bytecode has its own ~38 KB limit (literals live there).
// Cheap values (grid cell) are computed inline.
var hasMirror = 1
var rankN = pixelCount                // the number of left-side LEDs, counted below

for (k = 0; k < ZONE_RUNS.length; k += 2) {
  var runEnd = k + 2 < ZONE_RUNS.length ? ZONE_RUNS[k + 2] : pixelCount
  for (i = ZONE_RUNS[k]; i < runEnd; i++) zoneOf[i] = ZONE_RUNS[k + 1]
}
// each zone's extent in x and in distance from the centre: an effect whose band misses a zone
// entirely leaves that zone on the fast path (no per-pixel test at all)
var zX0 = array(8)
var zX1 = array(8)
var zD0 = array(8)
var zD1 = array(8)
for (k = 0; k < 8; k++) { zX0[k] = 9; zX1[k] = -9; zD0[k] = 9; zD1[k] = -9 }
mapPixels(function(index, x, y, z) {
  var dx = (x - 0.5) * ASPECT
  var dy = y - 0.5
  distC[index] = sqrt(dx * dx + dy * dy) / ASPECT
  angC[index] = atan2(dy, dx) / PI2 + 0.5
  sparkle[index] = x; along[index] = y              // parked here until the start-up tables below are built
  var zz = zoneOf[index]
  zX0[zz] = min(zX0[zz], x); zX1[zz] = max(zX1[zz], x)
  zD0[zz] = min(zD0[zz], distC[index]); zD1[zz] = max(zD1[zz], distC[index])
})
// ---- start-up tables: the cut-out panes now, the mirror ranks over the first seconds (mirrorStep, called
// from beforeRender: a 609 x 606 nearest-neighbour search in one go stalled start-up and tripped the VM).
// x and y stay parked in sparkle / along until the search is done (mirrorFinish gives those their jobs). ----
var XMID = 0.50107                 // the left/right split: the mapper's centre column (facade_zones.mirror_strip)
// left-side LEDs are ranked in wiring order; right-side ones get the rank of their mirror image once found
var nLeft = 0
for (i = 0; i < pixelCount; i++) {
  if (sparkle[i] > XMID) { MIRROR_RANK[i] = 0 } else { MIRROR_RANK[i] = nLeft; nLeft = nLeft + 1 }
}
rankN = nLeft
// panes: a new one at every jump along the strip (0.048 in y units, ~45 px of the camera image); right-side
// LEDs are negative until they take their mirror's pane in mirrorFinish, which makes the mosaic symmetric
var fid = 0
facet[0] = sparkle[0] > XMID ? -1 : 0
for (i = 1; i < pixelCount; i++) {
  var fdx = (sparkle[i] - sparkle[i - 1]) * ASPECT
  var fdy = along[i] - along[i - 1]
  if (fdx * fdx + fdy * fdy > 0.0023) fid = fid + 1
  facet[i] = sparkle[i] > XMID ? -1 - fid : fid
}
var mirI = 0                        // next LED the search looks at; pixelCount = done
var mirN = 0
var mBest = 0
var mRank = 0
var mX2 = 0
var mY2 = 0
function mirrorStep() {             // a few right-side LEDs per frame: the nearest left-side LED to the mirror image
  mirN = 0
  while (mirI < pixelCount && mirN < 6) {
    if (sparkle[mirI] > XMID) {
      mX2 = 1 - sparkle[mirI]; mY2 = along[mirI]; mBest = 9
      for (j = 0; j < pixelCount; j++) {
        if (sparkle[j] <= XMID) {
          var md = along[j] - mY2
          md = md * md
          if (md < mBest) {
            var mdx = (sparkle[j] - mX2) * ASPECT
            md = md + mdx * mdx
            if (md < mBest) { mBest = md; mRank = MIRROR_RANK[j] }
          }
        }
      }
      MIRROR_RANK[mirI] = mRank
      mirN = mirN + 1
    }
    mirI = mirI + 1
  }
  if (mirI >= pixelCount) mirrorFinish()
}
function mirrorFinish() {           // right-side panes take their mirror's (rank -> pane through sparkle); then sparkle and along get their jobs
  for (i = 0; i < pixelCount; i++) if (facet[i] >= 0) sparkle[MIRROR_RANK[i]] = facet[i]
  for (i = 0; i < pixelCount; i++) if (facet[i] < 0) facet[i] = sparkle[MIRROR_RANK[i]]
  for (i = 0; i < pixelCount; i++) sparkle[i] = -99
  for (k = 0; k < ZONE_RUNS.length; k += 2) {
    var aEnd = k + 2 < ZONE_RUNS.length ? ZONE_RUNS[k + 2] : pixelCount
    for (i = ZONE_RUNS[k]; i < aEnd; i++) along[i] = (i - ZONE_RUNS[k]) / max(1, aEnd - ZONE_RUNS[k] - 1)
  }
}

// ---- per-zone state ----------------------------------------------------------------
var zVibe = array(NZ)
var zPrev = array(NZ)
var zFade = array(NZ)
var zEnv = array(NZ)
var zEnvS = array(NZ)           // smoothed envelope, for the calm vibes
// How much each vibe follows the beat/audio envelope (1 = fully, low = gently, smoothed when < 0.5)
// 0 cozy 1 party 2 chaos 3 wave 4 jumping 5 ethereal 6 snake 7 twinkle 8 heartbeat 9 aurora 10 meteor 11 bloom 12 kaleido
// 13 lotus 14 spectrum 15 fireflies 16 cascade 17 mycelium 18 butterfly 19 mosaic 20 wings 21 vortex
// (the facade-only vibes drive their own reactivity, so gentle)
var ENV_DEPTH = [0.4, 1, 1, 0.8, 1, 0.4, 0.35, 0.4, 0.2, 0.4, 0.3, 0.8, 0.8, 0.25, 0.3, 0.2, 0.45, 0.6, 0.4, 0.45, 0.45, 0.6]
// aurora: each zone its own band of the aurora (ring, grid, mushrooms, leaves, crown, both)
var AURZ = [0.08, 0.15, 0.45, 0.4, 0, 0.03, 0.3, 0.42]
var ovH = array(NZ)
var ovS = array(NZ)
var ovV = array(NZ)
var ovW = array(NZ)
var sweepOn = array(NZ)
var waveOn = array(NZ)
var ringsOn = array(NZ)
var spiralOn = array(NZ)
var wipeOn = array(NZ)
var rainOn = array(NZ)
var cometOn = array(NZ)
for (z = 0; z < NZ; z++) { zVibe[z] = vibe; zPrev[z] = vibe; zFade[z] = 1; zEnv[z] = 1 }

// base hue per zone, for the zone-coloured vibes
var zHue = [0.11, 0.55, 0.86, 0.78, 0.3, 0.36, 0.13, 0.82]

// ---- internals ------------------------------------------------------------------------
var t = 0
var beatPhase = 0
var barPhase = 0
var beatCount = 0
var lastBeatT = 99
var env = 1
var beatEnv = 0
var bassS = 0
var midS = 0
var highS = 0
var loudS = 0
var spatialFx = 0
// sensor auto-gain per band (0 bass 1 mid 2 high 3 loud), values scaled x100 for fixed point
var raw = array(4)
var nf = array(4)               // noise floor: snaps down, creeps up over ~20 s
var pk = array(4)               // peak: snaps up, decays over ~8 s
var lvl = array(4)
var silence = 0.3               // quietest the room has been (slow), for "is music playing"
var liveHold = 0
var bassSlow = 0
var sinceBeat = 9
var localBeat = 0
var ibis = array(8)             // last 8 inter-beat intervals (folded into 85-170 BPM)
var ibiN = 0
var ibiI = 0
var ibiSort = array(8)
var music = 0                   // music is playing (bus beat or sensor), so pulse and split
var newBeat = 0                 // a beat landed this frame
var tA = 0                      // audio-driven time: runs faster with louder music
// per zone, decided once per frame (render2D's dispatch):
//   0 plain - just the vibe | 1 + whole-zone overlay (flash/pulse/color/charge), inlined |
//   3 vibe change only (bloom-out switch), inlined | 4 one spatial effect (+ overlay), inlined |
//   2 slow path - anything more (vibe change with effects, several spatial effects)
var zMode = array(8)
var zGain = array(8)            // zEnv * master
var zFx = array(56)             // active spatial effects per zone: zFx[z * 7 + k] = effect id
var zFxN = array(8)
// per frame, for each spatial effect: what the pixel test is on (0 none, 1 x, 2 distance from centre,
// 3 position along the strip) and the band; pixels outside take the plain path
var fxRej = [1, 2, 2, 0, 1, 0, 3]
var fxLo = array(7)
var fxHi = array(7)
// per-frame effect constants, so render2D does the least work per pixel
var sweepX = 0
var waveR = 0
var waveW = 0.06
var waveF = 0
var ringsR = 0
var ringsF = 0
var spiralPh = 0
var spiralF = 0
var spiralTw = 3                // how much the arms curl with radius (0 for sunburst's straight rays)
var wipeEdge = 0
var wipeF = 0
var cometHead = 0
var anyUniform = 0
var hh = 0
var ss = 0
var vv = 0

// jumping: three sparks
var sx = [0.2, 0.5, 0.8]
var sy = [0.5, 0.4, 0.5]
var tx = [0.2, 0.5, 0.8]
var ty = [0.5, 0.4, 0.5]
var sh = [0, 0.33, 0.66]
var hopT = 0
var hopBeat = 0
var burn = 0
var spX = array(3)              // spark positions this frame
var spY = array(3)
var cellSpark = array(128)      // nearest spark that can reach each grid cell, -1 = none
var useJump = 0
var SPARK_R = 0.3               // spark radius, aspect-corrected units (x0.8..1.3 with bass)
var sparkR = 0.3

// twinkle sparkles and rain drops store their birth time in sparkle[] (no per-frame decay loop)
var useTwinkle = 0
var twSym = 0                   // twinkle mirrored left/right right now

// meteor shower: two slots, positions in aspect-corrected units (X = x * ASPECT)
var mT = [99, 99]
var mX = array(2)
var mY = array(2)
var mDx = array(2)
var mDy = array(2)
var mHue = array(2)
var mSpd = array(2)
var mOn = array(2)              // per frame: meteor k is visible, and its streak's bounding box
var mX0 = array(2)
var mX1 = array(2)
var mY0 = array(2)
var mY1 = array(2)
var mNext = 0.5

// ---- state for the facade-only vibes added 2026-10-07: lotus, spectrum, fireflies, cascade, mycelium, butterfly ----
// Small per-zone tables (8 entries, indexed by zone) instead of per-pixel conditionals; array memory is
// tight (~1.1k slots free), so nothing per pixel.
// lotus: how far open (0 closed .. 1 fully open), the opening edge's distance from the centre, the rim's beat glow
var lotOpen = 0.6
var lotEdge = 0.35
var lotRimG = 4
var LOT_H = [0.08, 0.12, 0.33, 0.33, 0.93, 0.93, 0.1, 0.33]    // ring amber, heart gold, lily pads green, petals pink, crown pale
var LOT_S = [0.8, 0.5, 0.85, 0.85, 0.7, 0.7, 0.15, 0.85]
var LOT_V = [1, 1, 0.8, 0.8, 1, 1, 1, 0.8]
// spectrum: 32 bins, each auto-gained against its own noise floor and peak, smoothed for display
var specF = array(32)
var specNf = array(32)
var specPk = array(32)
for (i = 0; i < 32; i++) specNf[i] = 1
var useSpec = 0
var specHue = 0
// fireflies: how locked to the beat they are (loud music), what fraction of LEDs are fireflies, the shared pulse
var ffSync = 0
var ffDens = 0.12
var ffPulse = 0
var FF_H = [0.1, 0.12, 0.8, 0.8, 0.17, 0.17, 0.15, 0.8]
// cascade: where the falling sheet is (y, 0 = crown, 1 = floor) and how hard it is landing
var casY = 0
var casSplash = 0
var CAS_H = [0.55, 0.55, 0.38, 0.38, 0.58, 0.58, 0.5, 0.38]
// mycelium: the mushrooms' glow (bass) and base level / hue per zone
var mycGlow = 0.5
var MYC_H = [0.75, 0.72, 0.85, 0.85, 0.78, 0.78, 0.7, 0.85]
var MYC_B = [0.06, 0.08, 0.3, 0.3, 0.04, 0.04, 0.1, 0.3]
// butterfly: zone tints, the body's (smoothed) beat pulse and the wings' (smoothed) bass lift
var bfHue = 0
var bfBody = 1
var bfLift = 0.9
var BF_H = [0.12, 0.12, 0.55, 0.55, 0.9, 0.9, 0.95, 0.55]
var BF_V = [1, 1, 0.9, 0.9, 0.9, 0.9, 1, 0.9]
// mosaic / wings / vortex (vibes 19-21, added later the same day)
var barNo = 0
var lastBarPh = 0
var mosBeat = 0
var mosLit = 0
var mosHue = 0
var MOS_H = [0.1, 0.13, 0.78, 0.78, 0.45, 0.45, 0.95, 0.78]     // palettes centred per zone: gold circle, violet mushrooms, teal leaves, pink crown
var wgPos = 0
var wgGain = 1
var slowHue = 0
var WG_H = [0.1, 0.12, 0.8, 0.8, 0.55, 0.55, 0, 0.8]
var WG_B = [0.1, 0.14, 0.06, 0.06, 0.03, 0.03, 0.1, 0.06]        // base glow: the heart stays lit, the petals wait for the beams
var vxTwist = 1
var VX_H = [0.6, 0.62, 0.85, 0.85, 0.52, 0.52, 0.55, 0.85]

function addFx(z, id) {
  // skip effects whose band can't reach this zone this frame (fxRej / fxLo / fxHi: see render2D)
  if (fxRej[id] == 1 && (fxHi[id] < zX0[z] || fxLo[id] > zX1[z])) return
  if (fxRej[id] == 2 && (fxHi[id] < zD0[z] || fxLo[id] > zD1[z])) return
  zFx[z * 7 + zFxN[z]] = id; zFxN[z] = zFxN[z] + 1
}

function bit(mask, z) {
  if (z == 7) return floor(mask / 4) % 2 || floor(mask / 8) % 2   // either mushroom
  return floor(mask / pow(2, z)) % 2
}

// Smoothing toward a target at `rate` per second (frame-rate independent); dtG is this frame's dt.
var dtG = 0
function sm(cur, target, rate) { return cur + (target - cur) * min(1, dtG * rate) }

// Camp-bus colour rule in HSV: pull hh/ss/vv toward (ch,cs,cv) by weight w.
// Hue takes the short way round; greys adopt the new hue outright.
var mdh = 0                     // mixIn scratch (global: mixIn runs per pixel during effects)
function mixIn(ch, cs, cv, w) {
  if (w <= 0) return
  if (w > 1) w = 1
  if (ss < 0.05) hh = ch
  else if (cs >= 0.05) {
    mdh = ch - hh
    hh = hh + (mdh - floor(mdh + 0.5)) * w
  }
  ss = ss + (cs - ss) * w
  vv = vv + (cv - vv) * w
}

// Stack a uniform layer onto zone z's per-frame overlay colour.
function overlay(z, ch, cs, cv, w) {
  if (w <= 0) return
  if (w > 1) w = 1
  if (ovW[z] <= 0) { ovH[z] = ch; ovS[z] = cs; ovV[z] = cv; ovW[z] = w; return }
  if (ovS[z] < 0.05) ovH[z] = ch
  else if (cs >= 0.05) {
    var dh = ch - ovH[z]
    ovH[z] = ovH[z] + (dh - floor(dh + 0.5)) * w
  }
  ovS[z] = ovS[z] + (cs - ovS[z]) * w
  ovV[z] = ovV[z] + (cv - ovV[z]) * w
  ovW[z] = 1 - (1 - ovW[z]) * (1 - w)
}

function hopSparks() {
  for (i = 0; i < 3; i++) {
    sx[i] = tx[i]; sy[i] = ty[i]
    tx[i] = 0.1 + random(0.8)
    ty[i] = 0.15 + random(0.7)
  }
  // sparks landing close together "burn": hues pull together and rotate, white flash
  for (i = 0; i < 3; i++) {
    for (j = i + 1; j < 3; j++) {
      if (abs(tx[i] - tx[j]) + abs(ty[i] - ty[j]) < 0.15) {
        var m = (sh[i] + sh[j]) / 2
        sh[i] = sh[i] + (m - sh[i]) / 3 + 0.1
        sh[j] = sh[j] + (m - sh[j]) / 3 + 0.1
        burn = 1
      }
    }
  }
}

function override(z) {
  var o = z == 0 ? zv0 : z == 1 ? zv1 : z == 2 || z == 7 ? zv2 : z == 3 ? zv3 : z == 4 ? zv4 : z == 5 ? zv5 : zv6
  return o >= 0 ? o : vibe
}

export function beforeRender(delta) {
  var dt = delta / 1000
  dtG = dt
  if (mirI < pixelCount) mirrorStep()          // start-up: the mirror table, a few LEDs per frame
  t = t + dt * speed
  if (t > 3600) { t = t - 3600; for (i = 0; i < pixelCount; i++) sparkle[i] = -99 }   // birth times would be in the future

  // ---- local audio: bands from the sensor board, auto-gained like the camp analyser ----
  raw[0] = (frequencyData[0] + frequencyData[1] + frequencyData[2] + frequencyData[3]) * 25
  var acc = 0
  for (i = 4; i < 14; i++) acc = acc + frequencyData[i]
  raw[1] = acc * 10
  acc = 0
  for (i = 14; i < 32; i++) acc = acc + frequencyData[i]
  raw[2] = acc * 100 / 18
  raw[3] = energyAverage * 100
  for (b = 0; b < 4; b++) {
    nf[b] = min(raw[b], sm(nf[b], raw[b], 0.05))
    pk[b] = max(raw[b], pk[b] * (1 - min(1, dt / 8)))
    var range = max(pk[b] - nf[b], (nf[b] * 2 + 0.3) / max(0.1, sensorGain))
    lvl[b] = max(0, min(1, (raw[b] - nf[b]) / range))
  }
  silence = min(raw[3], sm(silence, raw[3], 0.008))
  if (raw[3] > silence * 3 + 0.4 / max(0.1, sensorGain)) { liveHold = 4 } else { liveHold = liveHold - dt }
  aLive = liveHold > 0

  // local beat: a bass onset well above its recent average
  bassSlow = sm(bassSlow, lvl[0], 3)
  sinceBeat = sinceBeat + dt
  localBeat = 0
  if (aLive && lvl[0] - bassSlow > 0.18 && lvl[0] > 0.4 && sinceBeat > 0.3) {
    if (sinceBeat < 1.5) {
      // Tempo = median of the last 8 intervals, each folded into 85-170 BPM: a missed kick
      // (double interval) or an off-beat hit can't drag it the way an average did.
      var iv = sinceBeat
      while (iv < 0.353) iv = iv * 2
      while (iv > 0.706) iv = iv / 2
      ibis[ibiI] = iv; ibiI = (ibiI + 1) % 8; ibiN = min(8, ibiN + 1)
      for (i = 0; i < ibiN; i++) {              // insertion sort into ibiSort
        var x = ibis[i]
        var j = i - 1
        while (j >= 0 && ibiSort[j] > x) { ibiSort[j + 1] = ibiSort[j]; j = j - 1 }
        ibiSort[j + 1] = x
      }
      aBpm = 60 / ibiSort[floor(ibiN / 2)]
    }
    sinceBeat = 0
    localBeat = 1
  }
  if (sinceBeat > 4) { aBpm = 0; ibiN = 0 }

  var useSensor = audioSrc == 1 || (audioSrc == 0 && aLive)
  if (useSensor) { aBass = lvl[0]; aMid = lvl[1]; aHigh = lvl[2]; aLoud = lvl[3] }
  else { aBass = bass; aMid = mid; aHigh = high; aLoud = loud }
  var synced = bpm > 0 && bpmSync && audioSrc != 1
  var sensorBeats = !synced && useSensor && aLive
  music = (bpm > 0 && audioSrc != 1) || (useSensor && aLive)

  // ---- beat clock: bus beats, else the sensor's onsets, else free-running ----
  var before = beatCount
  if (beatT < lastBeatT) beatCount = (beatCount + 1) % 4   // a new beat landed
  lastBeatT = beatT
  beatT = min(beatT + dt, 99)
  if (synced) {
    beatPhase = min(beatT / (60 / bpm), 1)
  } else if (sensorBeats) {
    // Flywheel: run at the tracked tempo and nudge toward detected kicks, so a missed or extra
    // onset doesn't stutter the pulse.
    beatPhase = beatPhase + dt * max(aBpm, 85) / 60
    if (localBeat) {
      if (beatPhase > 0.6) beatPhase = 1                   // kick slightly early: start the beat now
      else if (beatPhase < 0.3) beatPhase = beatPhase * 0.5   // slightly late: pull back toward it
    }
    if (beatPhase >= 1) {
      beatPhase = beatPhase - floor(beatPhase)
      beatT = 0; lastBeatT = 0; beatCount = (beatCount + 1) % 4
    }
  } else {
    var beatLen = 0.5 / speed
    var prevPhase = beatPhase
    beatPhase = (t / beatLen) - floor(t / beatLen)
    if (beatPhase < prevPhase) { beatT = 0; lastBeatT = 0; beatCount = (beatCount + 1) % 4 }
  }
  newBeat = beatCount != before
  barPhase = (beatCount + beatPhase) / 4

  // ---- smoothed audio: fast attack, slower release ----
  bassS = aBass > bassS ? aBass : sm(bassS, aBass, 6)
  midS = aMid > midS ? aMid : sm(midS, aMid, 4)
  highS = aHigh > highS ? aHigh : sm(highS, aHigh, 8)
  loudS = sm(loudS, aLoud, 4)
  var fall = 1 - beatPhase
  beatEnv = fall * fall * fall
  tA = tA + dt * speed * (music ? 0.5 + 1.5 * loudS : 1)
  if (tA > 3600) tA = tA - 3600

  // ---- brightness envelope per zone ----
  if (!music) env = 1                                           // no music: ambient, no pulsing
  else if (pulseSync) env = 0.55 + 0.45 * beatEnv * (0.5 + 0.5 * aBass)
  else env = 0.35 + 0.65 * loudS
  if (music && split) {
    var be = pulseSync ? beatEnv : loudS
    zEnv[0] = 0.4 + 0.6 * be              // ring: the beat
    zEnv[1] = 0.3 + 0.7 * midS            // grid: mids
    zEnv[2] = 0.2 + 0.8 * bassS           // mushrooms: bass
    zEnv[3] = zEnv[2]
    zEnv[4] = 0.4 + 0.6 * max(highS, loudS * 0.5)   // leaves: highs
    zEnv[5] = zEnv[4]
    zEnv[6] = 0.3 + 0.7 * be              // crown: the beat
    zEnv[7] = zEnv[2]
  } else {
    for (z = 0; z < NZ; z++) zEnv[z] = env
  }

  if (autoMode) autoStep(dt)

  // ---- per-zone vibe + crossfade ----
  useTwinkle = 0
  useJump = 0
  useSpec = 0
  for (z = 0; z < NZ; z++) {
    var want = override(z)
    if (want != zVibe[z]) { zPrev[z] = zVibe[z]; zVibe[z] = want; zFade[z] = 0 }
    zFade[z] = min(1, zFade[z] + dt / 2)
    if (zVibe[z] == 7 || (zFade[z] < 1 && zPrev[z] == 7)) useTwinkle = 1
    if (zVibe[z] == 4 || (zFade[z] < 1 && zPrev[z] == 4)) useJump = 1
    if (zVibe[z] == 14 || (zFade[z] < 1 && zPrev[z] == 14)) useSpec = 1
  }

  // ---- effect timers ----
  flashT = min(flashT + dt, 99)
  pulseT = min(pulseT + dt, 99)
  tintT = min(tintT + dt, 99)
  sweepT = min(sweepT + dt, 99)
  waveT = min(waveT + dt, 99)
  ringsT = min(ringsT + dt, 99)
  spiralT = min(spiralT + dt, 99)
  wipeT = min(wipeT + dt, 99)
  rainT = min(rainT + dt, 99)
  cometT = min(cometT + dt, 99)
  chargeAmt = max(0, chargeAmt - dt * 0.12)

  // uniform layers collapse into one overlay colour per zone per frame
  var fl = max(0, 1 - flashT / flashDur)
  anyUniform = 0
  for (z = 0; z < NZ; z++) {
    ovW[z] = 0
    if (chargeAmt > 0 && bit(chargeZ, z)) overlay(z, chargeH, chargeS, chargeV, chargeAmt)
    if (tintT < tintDur && bit(tintZ, z)) overlay(z, tintH, tintS, tintV, tintI * (1 - tintT / tintDur))
    if (pulseT < pulseDur && bit(pulseZ, z)) overlay(z, pulseH, pulseS, pulseV, pulseI * sin(PI * pulseT / pulseDur))
    if (fl > 0 && bit(flashZ, z)) overlay(z, flashH, flashS, flashV, flashI * fl * fl)
    if (ovW[z] > 0) anyUniform = 1
  }

  // spatial effects: which zones each one touches this frame
  var sweepLive = sweepT < 2 / sweepSpeed
  var waveLive = waveT < 1.5
  var ringsLive = ringsT < ringsDur
  var spiralLive = spiralT < spiralDur
  var wipeLive = wipeT < wipeDur
  var rainLive = rainT < rainDur + 1
  var cometLive = cometT * cometSpeed * 0.6 < 1.3
  spatialFx = sweepLive || waveLive || ringsLive || spiralLive || wipeLive || rainLive || cometLive
  sweepX = sweepT * sweepSpeed * 0.6 - 0.1
  waveR = waveDir > 0 ? waveT * 0.6 : 0.2 - waveT * 0.25        // outward from the centre, or in from the flower's ring
  waveW = 0.06 + waveT * 0.05                     // the ring widens as it travels
  waveF = waveI * min(1, waveT / 0.2) * (waveT < 0.9 ? 1 : max(0, (1.5 - waveT) / 0.6))   // ease in, fade out
  ringsR = ringsT * 0.45; ringsF = ringsI * (1 - ringsT / ringsDur)
  spiralPh = spiralDir * spiralT * 0.7; spiralF = spiralI * sin(PI * min(1, spiralT / spiralDur))
  spiralTw = spiralArms > 6 ? 0 : 3
  var wp = wipeT / wipeDur
  wipeEdge = wp / 0.35 * 1.1 - 0.05; wipeF = wipeI * (wp < 0.7 ? 1 : max(0, 1 - (wp - 0.7) / 0.3))
  cometHead = cometT * cometSpeed * 0.6
  fxLo[0] = sweepX - 0.12; fxHi[0] = sweepX + 0.12
  fxLo[1] = waveR - waveW; fxHi[1] = waveR + waveW
  fxLo[2] = ringsR - 0.135 * (ringsCount - 1) - 0.04; fxHi[2] = ringsR + 0.04
  fxLo[4] = -1; fxHi[4] = wipeEdge
  fxLo[6] = cometHead - 0.3; fxHi[6] = cometHead
  if (spatialFx) {
    for (z = 0; z < NZ; z++) {
      sweepOn[z] = sweepLive && bit(sweepZ, z)
      waveOn[z] = waveLive && bit(waveZ, z)
      ringsOn[z] = ringsLive && bit(ringsZ, z)
      spiralOn[z] = spiralLive && bit(spiralZ, z)
      wipeOn[z] = wipeLive && bit(wipeZ, z)
      rainOn[z] = rainLive && bit(rainZ, z)
      cometOn[z] = cometLive && bit(cometZ, z)
    }
  }

  lite = 0
  for (z = 0; z < NZ; z++) {
    zEnvS[z] = sm(zEnvS[z], zEnv[z], 4)
    var dep = ENV_DEPTH[zVibe[z]]
    zEnv[z] = 1 - dep * (1 - (dep < 0.5 ? zEnvS[z] : zEnv[z]))
    zGain[z] = zEnv[z] * master
    zFxN[z] = 0
    if (spatialFx) {
      if (sweepOn[z]) addFx(z, 0)
      if (waveOn[z]) addFx(z, 1)
      if (ringsOn[z]) addFx(z, 2)
      if (spiralOn[z]) addFx(z, 3)
      if (wipeOn[z]) addFx(z, 4)
      if (rainOn[z]) addFx(z, 5)
      if (cometOn[z]) addFx(z, 6)
    }
    if (zFade[z] < 1 || zFxN[z] > 0) lite = 1
    if (zFade[z] < 1) zMode[z] = zFxN[z] == 0 && ovW[z] <= 0 ? 3 : 2
    else if (zFxN[z] == 1) zMode[z] = 4
    else if (zFxN[z] > 1) zMode[z] = 2
    else zMode[z] = ovW[z] > 0 ? 1 : 0
  }

  // ---- rain: decay drops, seed new ones while the effect runs (not while sparkle still parks x for the mirror search) ----
  if (rainLive && rainT < rainDur && mirI >= pixelCount) {
    var drops = pixelCount * dt * 0.8
    for (i = 0; i < drops; i++) {
      var p = floor(random(pixelCount))
      if (rainOn[zoneOf[p]]) sparkle[p] = t
    }
  }

  // ---- jumping ----
  hopT = hopT + dt
  if (beatCount != hopBeat) {            // hop on every beat (free-running clock when no music)
    hopBeat = beatCount
    hopT = 0
    hopSparks()
  }
  burn = max(0, burn - dt * 3)
  // sparks glide to their new spot over 0.4 s with ease-in-out: at ~14 fps a faster hop is
  // only a couple of frames and reads as a jump cut
  var kk = min(1, hopT * 2.5 * speed)
  kk = kk * kk * (3 - 2 * kk)
  for (i = 0; i < 3; i++) { spX[i] = sx[i] + (tx[i] - sx[i]) * kk; spY[i] = sy[i] + (ty[i] - sy[i]) * kk }
  sparkR = SPARK_R * (music ? 0.8 + 0.5 * bassS : 1)
  if (useJump) {
    // Per cell: the nearest spark whose glow can reach it (cell half-diagonal ~0.09), so each
    // pixel evaluates at most one spark instead of three.
    for (c = 0; c < 128; c++) {
      var gx = ((c % 16) + 0.5) / 16
      var gy = (floor(c / 16) + 0.5) / 8
      var best = -1
      var bestD = sparkR + 0.09
      for (i = 0; i < 3; i++) {
        var ddx = (gx - spX[i]) * ASPECT
        var ddy = gy - spY[i]
        var dd = sqrt(ddx * ddx + ddy * ddy)
        if (dd < bestD) { bestD = dd; best = i }
      }
      cellSpark[c] = best
    }
  }

  // ---- twinkle ----
  if (useTwinkle && mirI >= pixelCount) {
    twSym = (t % 24) > 15
    var n = (twSym ? rankN : pixelCount) * dt * (0.4 + 2 * highS)
    for (i = 0; i < n; i++) sparkle[floor(random(twSym ? rankN : pixelCount))] = t
  }

  // ---- meteor shower: launch from above the top edge, falling 25-60 degrees ----
  for (k = 0; k < 2; k++) {
    mOn[k] = mT[k] < 2.5
    if (mOn[k]) {     // box around the streak (head to 0.7 behind, +/-0.07 wide): pixels outside skip the maths
      var hx = mX[k] + mDx[k] * mT[k] * mSpd[k]
      var hy = mY[k] + mDy[k] * mT[k] * mSpd[k]
      var tx0 = hx - mDx[k] * 0.7
      var ty0 = hy - mDy[k] * 0.7
      mX0[k] = (min(hx, tx0) - 0.08) / ASPECT; mX1[k] = (max(hx, tx0) + 0.08) / ASPECT
      mY0[k] = min(hy, ty0) - 0.08; mY1[k] = max(hy, ty0) + 0.08
    }
  }
  var sdt = dt * speed
  mT[0] = min(mT[0] + sdt, 99); mT[1] = min(mT[1] + sdt, 99)
  mNext = mNext - sdt
  if (mNext <= 0 || (music && newBeat && aBass > 0.5 && random(1) < 0.5)) {
    var slot = mT[0] > mT[1] ? 0 : 1
    var side = random(1) < 0.5 ? -1 : 1
    var a = (0.07 + random(0.1)) * PI2               // 25-61 degrees below horizontal
    mDx[slot] = cos(a) * side; mDy[slot] = sin(a)
    mX[slot] = side > 0 ? random(ASPECT * 0.6) : ASPECT * (0.4 + random(0.6))
    mY[slot] = -0.15
    mSpd[slot] = 1.6 + random(1)
    mHue[slot] = random(1) < 0.6 ? 0.07 + random(0.06) : 0.5 + random(0.12)   // fire or ice
    mT[slot] = 0
    mNext = 0.8 + random(2.6)
  }

  // ---- lotus: opens with the music's energy (opens in ~1.5 s, closes over ~5 s); breathes slowly without music ----
  var lw = music ? clamp((loudS - 0.12) * 1.35, 0, 1) : 0.55 + 0.45 * sin(t * 0.3)
  lotOpen = sm(lotOpen, lw, lw > lotOpen ? 0.9 : 0.25)
  lotEdge = 0.06 + lotOpen * 0.52
  lotRimG = music ? 2.4 + 1.6 * beatEnv : 4        // rim = f * (1 - f) * lotRimG peaks at 1 when 4
  LOT_V[2] = 0.55 + 0.45 * (music ? bassS : wave(t * 0.1)); LOT_V[3] = LOT_V[2]; LOT_V[7] = LOT_V[2]

  // ---- spectrum: per-bin auto-gain (noise floor snaps down / creeps up, peak snaps up / decays), smoothed;
  // no music: a slow pulse radiating outward ----
  if (useSpec) {
    for (b = 0; b < 32; b++) {
      var f = frequencyData[b] * 50
      specNf[b] = min(f, sm(specNf[b], f, 0.05))
      specPk[b] = max(f, specPk[b] * (1 - min(1, dt / 6)))
      var lv = music ? (f - specNf[b]) / max(specPk[b] - specNf[b], (specNf[b] * 2 + 0.3) / sensorGain) : wave(b / 16 - t * 0.35)
      lv = clamp(lv, 0, 1)
      specF[b] = sm(specF[b], lv * lv, lv * lv > specF[b] ? 10 : 3)
    }
  }
  specHue = 0.06 * sin(tA * 0.07)

  // ---- fireflies: they fall into step with the beat as the music gets loud; more of them with the highs ----
  ffSync = sm(ffSync, loudS * loudS * music, 0.7)
  ffDens = sm(ffDens, 0.13 + 0.12 * highS, 2)
  ffPulse = wave((beatCount % 2 + beatPhase) * 0.5 + 0.25)      // one shared swell every two beats, peaking on the beat
  ffPulse = ffPulse * ffPulse * ffPulse

  // ---- cascade: a sheet of light falls from the crown to the floor once a bar (every 2.4 s without music) ----
  casY = barPhase * 1.3 - 0.12                                  // (the beat clock free-runs without music)
  casSplash = clamp((casY - 0.7) * 3, 0, 1) * clamp((1.18 - casY) * 4, 0, 1)

  // ---- mycelium: the mushrooms glow with the bass ----
  mycGlow = sm(mycGlow, music ? 0.3 + 0.7 * bassS : 0.5 + 0.35 * wave(t * 0.12), 5)
  MYC_B[2] = 0.12 + 0.6 * mycGlow; MYC_B[3] = MYC_B[2]; MYC_B[7] = MYC_B[2]

  // ---- butterfly: the body (flower of life) pulses with the beat, the wings lift with the bass - both smoothed, so
  // a nudge of the beat flywheel never reads as a flicker; the hues wobble gently ----
  bfBody = sm(bfBody, music ? 0.62 + 0.38 * beatEnv : 1, 9)
  bfLift = sm(bfLift, music ? 0.6 + 0.4 * bassS : 0.9, 3)
  BF_V[0] = bfBody; BF_V[1] = bfBody; BF_V[4] = bfLift; BF_V[5] = bfLift
  bfHue = 0.06 * sin(tA * 0.04)                                 // a gentle wobble, so the zone tints keep their identity

  // ---- bar counter, for the two-bar cycles of wings and mosaic ----
  if (barPhase < lastBarPh) barNo = (barNo + 1) % 4
  lastBarPh = barPhase

  // ---- mosaic: one group of panes lights on each beat (8 groups over two bars) ----
  mosBeat = (beatCount + 4 * (barNo % 2)) % 8                  // (the beat clock free-runs without music)
  mosLit = 0.55 * beatEnv
  mosHue = 0.05 * sin(tA * 0.03)

  // ---- wings: the beams go from the crown to the floor over a bar and back over the next ----
  wgPos = (1 - abs(barNo % 2 + barPhase - 1)) * 0.5
  wgGain = 0.7 + 0.3 * beatEnv
  slowHue = tA * 0.01                                           // a slow hue turn shared by wings and vortex

  // ---- vortex: the pull twists with the bass ----
  vxTwist = sm(vxTwist, music ? 0.3 + 2.2 * bassS : 0.8 + 0.7 * wave(t * 0.05), 2)

}

// ---- vibes: each sets hh/ss/vv for pixel pI at (pX, pY) in zone pZ (passed as arguments:
// cheaper than writing four globals per pixel) ---------------------------------------------
// tA is audio-driven time (faster with louder music); aBass/aMid/aHigh/aLoud are 0..1.

// Scratch for the vibe functions. Globals, not locals: the VM sets up a function's locals on
// every call, and these run for every pixel.
var vqX = 0
var vqa6 = 0
var vqacross = 0
var vqback0 = 0
var vqbw = 0
var vqc = 0
var vqg = 0
var vqph = 0
var vqcs = 0
var vqex = 0
var vqey = 0
var vqf = 0
var vqfl = 0
var vqfold = 0
var vqhp = 0
var vqkw = 0
var vqmush = 0
var vqmw = 0
var vqn = 0
var vqn1 = 0
var vqn2 = 0
var vqq = 0
var vqr = 0
var vqseg = 0
var vqsp = 0
var vqsr = 0
var vqtail = 0
var vqtw = 0
var vqw = 0
var vqwhich = 0
var vqd = 0
var vqb = 0
var vqi = 0
var vqlv = 0
var vqm = 0
var vqp = 0
var vqs = 0
var vqe = 0

function vCozy(pI, pX, pY, pZ) {          // embers drifting up, coals flickering (flaring with the highs), mushrooms breathing
  vqn = perlin(pX * 3, pY * 3 + tA * 0.35, tA * 0.1, 0)
  vqfl = 0
  if (!lite) {                              // per-coal flicker (skipped in lite mode)
    vqfl = wave(tA * 0.9 + pI * 0.618)
    vqfl = vqfl * vqfl; vqfl = vqfl * vqfl; vqfl = vqfl * vqfl
    vqfl = vqfl * (0.7 + 0.6 * highS)
  }
  vqmush = pZ == 2 || pZ == 3 || pZ == 7
  hh = 0.035 + 0.05 * vqn + 0.03 * vqfl - (vqmush ? 0.045 : 0)
  ss = 0.92 - 0.3 * vqfl
  vv = 0.22 + 0.38 * (vqn * 0.5 + 0.5) + 0.4 * vqfl
  if (vqmush) vv = vv * (0.55 + 0.45 * wave(tA * 0.11 + (pZ == 3 ? 0.5 : 0)))
}
function vParty(pI, pX, pY, pZ) {         // "Hue Rotate": rainbow rings flowing out of the centre, kicking on the beat
  hh = distC[pI] * 1.2 - tA * 0.25; ss = 1
  vv = music ? 0.6 + 0.4 * beatEnv : 1
}
function vChaos(pI, pX, pY, pZ) {         // every LED pulses on its own clock, taking a new random colour while dark
  vqg = pI * 0.618034 + 0.31
  vqg = vqg - floor(vqg)                    // per-LED hash
  vqph = tA * (0.55 + 0.6 * highS) + vqg * 7.3
  vqc = vqph - floor(vqph)
  vv = wave(vqc - 0.25)                     // dark at each cycle's edge, so colour changes are hidden
  vv = vv * vv
  hh = floor(vqph) * 0.618034 + vqg * 3.7; ss = 1
}
function vWave(pI, pX, pY, pZ) {          // brightness waves rolling out from the centre, one per bar with music
  vqw = wave(abs(pX - 0.5) * 2 - (music ? barPhase : t / 4))   // mirrored: rolls out from the centre
  hh = 0.55 + 0.1 * pY + tA * 0.02; ss = 0.9; vv = 0.15 + 0.85 * vqw * vqw * (music ? 0.6 + 0.4 * loudS : 1)
}
function vJumping(pI, pX, pY, pZ) {       // three sparks glide to a new spot each beat; size breathes with the bass
  vqcs = cellSpark[min(15, floor(pX * 16)) + 16 * min(7, floor(pY * 8))]
  if (vqcs < 0) { vv = 0; hh = 0; ss = 1; return }
  vqex = (pX - spX[vqcs]) * ASPECT
  vqey = pY - spY[vqcs]
  vv = 1 - (vqex * vqex + vqey * vqey) / (sparkR * sparkR)
  if (vv < 0) vv = 0
  hh = sh[vqcs] + zHue[pZ]                  // each zone colours the spark differently
  ss = 1 - burn * 0.8
  if (vv > 0.01) vv = vv + (1 - vv) * burn * 0.6
}
function vEthereal(pI, pX, pY, pZ) {      // colour clouds threaded with wisps; the mids light the wisps
  vqn1 = perlin(pX * 1.6, pY * 1.6, tA * 0.07, 1)
  vqn2 = lite ? vqn1 * 1.7 : perlin(pX * 3 + tA * 0.05, pY * 3, tA * 0.15, 2)   // lite: wisps from the one field
  vqr = 1 - abs(vqn2)
  vqr = vqr * vqr * vqr
  hh = 0.6 + 0.35 * vqn1 + tA * 0.008 + 0.3 * vqr * vqr
  ss = 0.95 - 0.55 * vqr
  vv = 0.18 + 0.82 * max(vqr * (0.75 + 0.5 * midS), (vqn1 * 0.5 + 0.5) * 0.45)
}
function vSnake(pI, pX, pY, pZ) {         // three snakes, mirrored left/right, speeding up with the music
  vqseg = rankN / 3
  vqback0 = (tA * 55) % rankN - MIRROR_RANK[pI]
  if (vqback0 < 0) vqback0 = vqback0 + rankN
  vqwhich = floor(vqback0 / vqseg)
  vqf = 1 - (vqback0 - vqwhich * vqseg) / 110
  hh = t * 0.04 + vqwhich * 0.33 + (1 - vqf) * 0.25; ss = 1
  vv = vqf > 0 ? vqf * vqf * (music ? 0.7 + 0.3 * (0.5 - 0.5 * cos(PI2 * barPhase)) : 1) : 0   // one smooth swell per bar
}
function vTwinkle(pI, pX, pY, pZ) {       // dim base with sparkles (now and then mirrored left/right); more with the highs
  vqsp = 1 - (t - sparkle[twSym ? MIRROR_RANK[pI] : pI]) / 0.7
  vqsp = vqsp > 1 || vqsp < 0 ? 0 : vqsp
  hh = 0.62 - 0.1 * vqsp; ss = 0.6 - 0.6 * vqsp; vv = 0.06 + 0.94 * vqsp * vqsp
}
function vHeartbeat(pI, pX, pY, pZ) {     // lub-dub from the centre outward on every beat
  vqhp = beatPhase - distC[pI] * 0.25
  if (vqhp < 0) vqhp = vqhp + 1
  hh = 0.98; ss = 0.9                       // softer: higher floor, wider beats
  vv = 0.22 + 0.78 * max(max(0, 1 - abs(vqhp - 0.03) * 7), max(0, 1 - abs(vqhp - 0.25) * 8) * 0.7)
}
function vAurora(pI, pX, pY, pZ) {        // curtains drifting upward, each zone its own band; glowing with the mids
  vqc = perlin(pX * 4, pY * 1.5 + tA * 0.2, tA * 0.1, 3) * 0.5 + 0.5
  hh = 0.33 + 0.35 * (1 - pY) + 0.08 * vqc + AURZ[pZ]; ss = 0.9; vv = vqc * vqc * (music ? 0.6 + 0.6 * midS : 1)
}
function vMeteor(pI, pX, pY, pZ) {        // a meteor shower over a twinkling night sky; beats launch meteors
  hh = 0.64; ss = 0.85; vv = 0.035
  vqsr = pI * 0.618034
  if (vqsr - floor(vqsr) < 0.22) {
    vqtw = wave(t * 0.25 + pI * 0.7531)
    vqtw = vqtw * vqtw; vqtw = vqtw * vqtw; vqtw = vqtw * vqtw
    vv = vv + 0.4 * vqtw; ss = ss - 0.65 * vqtw
  }
  vqX = pX * ASPECT
  for (k = 0; k < 2; k++) {
    if (mOn[k] && pX > mX0[k] && pX < mX1[k] && pY > mY0[k] && pY < mY1[k]) {
      vqtail = mT[k] * mSpd[k] - ((vqX - mX[k]) * mDx[k] + (pY - mY[k]) * mDy[k])
      if (vqtail >= 0 && vqtail < 0.7) {
        vqacross = abs((vqX - mX[k]) * mDy[k] - (pY - mY[k]) * mDx[k])
        if (vqacross < 0.07) {
          vqq = 1 - vqtail / 0.7
          vqmw = vqq * vqq * (1 - vqacross / 0.07) * (0.65 + 0.35 * wave(pI * 0.37 + t * 9))
          if (vqmw > vv) { vv = vqmw; hh = mHue[k] - 0.04 * (1 - vqq); ss = vqtail < 0.06 ? 0 : 0.85 - 0.5 * vqq }
        }
      }
    }
  }
}
function vBloom(pI, pX, pY, pZ) {         // light flowing out of the flower, each zone its own colour; bass swells it
  vqbw = wave(distC[pI] * 2.5 - tA * 0.35)
  hh = zHue[pZ] + 0.05 * sin(tA * 0.2 + distC[pI] * 4); ss = pZ == 6 ? 0.3 : 0.85
  vv = (0.12 + 0.88 * vqbw * vqbw) * (music ? 0.7 + 0.45 * bassS : 1)
}
function vKaleido(pI, pX, pY, pZ) {       // six-fold symmetry like the flower of life, pumping with the bass
  vqa6 = angC[pI] * 6
  vqfold = vqa6 - floor(vqa6)
  vqfold = min(vqfold, 1 - vqfold)
  vqkw = wave(vqfold * 2 + distC[pI] * (4 + 2 * bassS) - tA * 0.5)
  hh = vqfold * 0.5 + distC[pI] * 1.2 - tA * 0.08; ss = 0.9; vv = 0.25 + 0.75 * vqkw * vqkw
}

function vLotus(pI, pX, pY, pZ) {         // the lotus opens from its golden heart out to the petal tips with the music's energy
  vqd = distC[pI]
  vqf = clamp((lotEdge - vqd) * 10, 0, 1)   // 1 inside the open part, 0 beyond it, a soft 0.1-wide edge
  vqf = vqf * vqf * (3 - 2 * vqf)
  vqr = vqf * (1 - vqf) * lotRimG           // the unfolding edge glows white, pulsing with the beat (lotRimG carries the x4)
  hh = LOT_H[pZ] + vqd * 0.08               // petals blush deeper toward the tips
  ss = LOT_S[pZ] * (1 - vqr)
  vv = 0.035 + 0.85 * vqf * LOT_V[pZ] + 0.6 * vqr
}
function vSpectrum(pI, pX, pY, pZ) {      // a radial spectrum analyser: bass at the heart, highs at the petal tips
  vqb = clamp((distC[pI] - 0.03) * 62, 0, 30.99)
  vqi = floor(vqb)
  vqlv = specF[vqi]
  vqlv = vqlv + (specF[vqi + 1] - vqlv) * (vqb - vqi)
  hh = vqb * 0.026 + specHue                // red at the heart through green to violet at the tips
  ss = 1 - 0.5 * vqlv
  vv = 0.04 + 0.96 * vqlv
}
function vFireflies(pI, pX, pY, pZ) {     // warm glows in the dark that fall into step with the beat as the music swells
  vqg = pI * 0.618034 + 0.41
  vqg = vqg - floor(vqg)                    // per-LED hash: which LEDs are fireflies, and each one's own rhythm
  if (vqg > ffDens) { hh = FF_H[pZ]; ss = 0.95; vv = 0.03; return }   // the dark forest, faintly zone-tinted
  vqg = vqg / ffDens
  vqw = wave(tA * 0.22 + vqg * 9.7)         // its own slow glow (~4.5 s)...
  vqw = vqw * vqw * vqw
  vqw = vqw + (ffPulse - vqw) * ffSync      // ...pulled toward the shared beat pulse as the music gets loud
  hh = FF_H[pZ] + vqg * 0.07
  ss = 0.85 - 0.55 * vqw
  vv = 0.03 + 0.97 * vqw * vqw
}
function vCascade(pI, pX, pY, pZ) {       // sheets of light fall from the crown through the flower to the floor, one a bar
  vqd = pY - casY                           // > 0: below the sheet (dry), < 0: above it (the wet wake)
  vqs = vqd > 0 ? 1 - vqd * 7 : 1 + vqd * 1.5
  if (vqs < 0) vqs = 0
  vqs = vqs * vqs
  vqw = wave(pY * 5 - tA * 0.9 + pX * 0.4)  // fine downward flow
  hh = CAS_H[pZ] + 0.05 * vqw
  ss = 0.85 - 0.55 * vqs                    // the sheet itself is near white
  vv = 0.04 + 0.12 * vqw + 0.84 * vqs + casSplash * max(0, pY - 0.6) * 2   // the floor lights up as the sheet lands
}
function vMycelium(pI, pX, pY, pZ) {      // bioluminescent pulses running along every strip; the mushrooms glow with the bass
  vqp = wave(MIRROR_RANK[pI] * 0.04 - tA * 1.1)   // pulses 25 LEDs apart, mirrored left/right
  vqp = vqp * vqp; vqp = vqp * vqp; vqp = vqp * vqp                    // short bright pulses, dark between
  hh = MYC_H[pZ] - 0.3 * vqp                // violet threads, teal where a pulse passes
  ss = 0.95 - 0.45 * vqp
  vv = MYC_B[pZ] + 0.9 * vqp
}
function vButterfly(pI, pX, pY, pZ) {     // colour clouds mirrored left/right like wings; the flower of life is the body, pulsing with the beat
  vqn = perlin(abs(pX - 0.5) * 3.4, pY * 2.2 + tA * 0.06, tA * 0.11, 7)
  vqq = vqn * 0.8 + 0.5
  vqq = vqq * vqq                           // contrast (perlin stays within about +/-0.7, so no clamp needed)
  hh = BF_H[pZ] + 0.18 * vqn + bfHue
  ss = 0.95 - 0.35 * vqq
  vv = (0.08 + 0.92 * vqq) * BF_V[pZ]       // BF_V: body pulse (smoothed beat) and wing lift (smoothed bass), per zone
}

function vMosaic(pI, pX, pY, pZ) {        // every cut-out pane its own stained-glass colour, mirrored left/right; a group of panes lights on each beat
  vqg = facet[pI] * 0.618034 + 0.23
  vqg = vqg - floor(vqg)                    // per-pane hash
  vqw = wave(tA * 0.09 + vqg * 5.3)         // each pane breathes on its own slow cycle
  vqw = vqw * vqw
  hh = MOS_H[pZ] + vqg * 0.28 + mosHue      // panes spread around a palette centred per zone
  ss = 0.8 + 0.2 * vqw
  vv = 0.1 + 0.7 * vqw + (floor(vqg * 8) == mosBeat ? mosLit : 0)
}
function vWings(pI, pX, pY, pZ) {         // two beams sweep from the crown down around both sides of the flower and back, lighting the petals they pass
  vqe = abs(angC[pI] - 0.25)
  if (vqe > 0.5) vqe = 1 - vqe              // elevation from the crown, mirrored left/right: 0 top .. 0.5 bottom
  vqs = 1 - abs(vqe - wgPos) * 7
  if (vqs < 0) vqs = 0
  vqs = vqs * vqs
  hh = WG_H[pZ] + vqe * 0.3 + slowHue       // the beams change colour as they descend
  ss = 0.9 - 0.6 * vqs * vqs                // white-hot cores
  vv = WG_B[pZ] + wgGain * vqs
}
function vVortex(pI, pX, pY, pZ) {        // rings drawn into the heart; the pull twists into a whirlpool as the bass grows
  vqe = abs(angC[pI] - 0.25)
  if (vqe > 0.5) vqe = 1 - vqe
  vqd = distC[pI]
  vqw = wave(vqd * 3.5 + tA * 0.4 + vqe * vxTwist)   // + tA: the rings move inward; vqe * twist bends them into mirrored spiral arms
  vqw = vqw * vqw; vqw = vqw * vqw
  hh = VX_H[pZ] - vqd * 0.35 + slowHue      // hue deepens toward the centre
  ss = 0.95 - 0.4 * vqw
  vv = 0.08 + 0.92 * vqw
}
var VIBE_FN = [vCozy, vParty, vChaos, vWave, vJumping, vEthereal, vSnake, vTwinkle, vHeartbeat, vAurora, vMeteor,
               vBloom, vKaleido, vLotus, vSpectrum, vFireflies, vCascade, vMycelium, vButterfly, vMosaic, vWings, vVortex]

// Vibe change (2 s): each pixel switches from the old vibe to the new one at its own moment,
// blooming out from the flower's centre with a little sparkle. Only pixels right at the switching
// edge blend both vibes - a full crossfade rendered every pixel twice and halved the frame rate.
var dsH = 0
var dsS = 0
var dsV = 0
function dissolve(index, x, y, z) {
  var g = index * 0.618034
  var e = zFade[z] * 1.4 - 0.2 - (distC[index] * 1.4 + (g - floor(g)) * 0.3)
  if (e <= -0.05) { VIBE_FN[zPrev[z]](index, x, y, z); return }
  VIBE_FN[zVibe[z]](index, x, y, z)
  if (e < 0.05) {
    dsH = hh; dsS = ss; dsV = vv
    VIBE_FN[zPrev[z]](index, x, y, z)
    mixIn(dsH, dsS, dsV, (e + 0.05) * 10)
  }
}

// Spatial effects: each is a small function, and a pixel calls only those active in its zone
// (zFx). The strongest one at the pixel wins - one mix, not one per effect. Scratch values are
// globals: locals are set up on every call, and these run per pixel.
var eW = 0
var eH = 0
var eS = 0
var eV = 0
var ew = 0
var eu = 0
function fxSweep(index, x) {        // band travels left to right
  ew = (1 - abs(x - sweepX) / 0.12) * sweepI
  if (ew > eW) { eW = ew; eH = sweepH; eS = sweepS; eV = sweepV }
}
function fxWave(index, x) {         // one ring out from the centre
  ew = 1 - abs(distC[index] - waveR) / waveW
  if (ew > 0) { ew = ew * ew * waveF; if (ew > eW) { eW = ew; eH = waveH; eS = waveS; eV = waveV } }
}
function fxRings(index, x) {        // rings 0.135 apart moving out at 0.45/s; nearest one without a loop
  eu = (ringsR - distC[index]) / 0.135
  if (eu > -0.5 && eu < ringsCount - 0.5) {
    ew = (1 - abs(eu - floor(eu + 0.5)) * 3.857) * ringsF        // 3.857 = 0.135 / 0.035
    if (ew > eW) { eW = ew; eH = ringsH; eS = ringsS; eV = ringsV }
  }
}
function fxSpiral(index, x) {       // arms turning around the centre (or sunburst's still rays)
  ew = wave(angC[index] * spiralArms + spiralPh - distC[index] * spiralTw)
  ew = ew * ew
  ew = ew * ew * spiralF
  if (ew > eW) { eW = ew; eH = spiralH; eS = spiralS; eV = spiralV }
}
function fxWipe(index, x) {         // fill left to right, hold, release
  if (x < wipeEdge) {
    ew = wipeF * min(1, (wipeEdge - x) * 12)
    if (ew > eW) { eW = ew; eH = wipeH; eS = wipeS; eV = wipeV }
  }
}
function fxRain(index, x) {         // twinkles in the colour (drops share twinkle's birth-time table)
  ew = 1 - (t - sparkle[index]) / 0.6
  if (ew > 0) { ew = ew * ew * rainI; if (ew > eW) { eW = ew; eH = rainH; eS = rainS; eV = rainV } }
}
function fxComet(index, x) {        // head + tail racing along each strip
  eu = cometHead - along[index]
  if (eu >= 0 && eu < 0.3) {
    ew = 1 - eu / 0.3
    if (cometI * ew * ew > eW) { eS = cometS * (1 - ew * 0.6); eW = cometI * ew * ew; eH = cometH; eV = cometV }
  }
}
var FX_FN = [fxSweep, fxWave, fxRings, fxSpiral, fxWipe, fxRain, fxComet]
var fk = 0
var lite = 0                    // an effect or a vibe change is on screen: heavy vibes drop detail

// Zone in mode 2: vibe change and/or spatial effects, then the zone's overlay.
function slowPixel(index, x, y, z) {
  if (zFade[z] < 1) dissolve(index, x, y, z)
  else VIBE_FN[zVibe[z]](index, x, y, z)
  vv = vv * zEnv[z]
  if (zFxN[z] > 0) {
    eW = 0
    for (fk = 0; fk < zFxN[z]; fk++) FX_FN[zFx[z * 7 + fk]](index, x)
    if (eW > 0.02) mixIn(eH, eS, eV, eW)     // skip invisible contributions
  }
  if (ovW[z] > 0) mixIn(ovH[z], ovS[z], ovV[z], ovW[z])
  hsv(hh, ss, vv * master)
}

// ---- effects fired from inside the pattern: the UI buttons below (trigger*) also serve auto mode, which
// borrows the effect colour for a moment. Intensity / duration / speed / count keep whatever the bus set
// last (the defaults when no PC is attached). ----------------------------------------------------
var FIRE_FN = array(9)          // filled after the trigger functions below: 0 flash 1 pulse 2 rings 3 spiral 4 shockwave 5 sweep 6 wipe 7 rain 8 comet

// ---- full auto -----------------------------------------------------------------------------
var VIBE_COUNT = 22
var autoFxT = 8
var autoWait = 0
var autoFxSoon = 0
var auH = 0
var auS = 0
var auV = 0
function autoEffect() {
  auH = uiH; auS = uiS; auV = uiV
  uiH = random(1); uiS = 0.55 + random(0.45); uiV = 1
  FIRE_FN[floor(random(9))]()
  uiH = auH; uiS = auS; uiV = auV
}
function autoPick() {
  var nv = floor(random(VIBE_COUNT - 1))
  vibe = nv >= vibe ? nv + 1 : nv                   // never the same vibe twice in a row
  zv0 = -1; zv1 = -1; zv2 = -1; zv3 = -1; zv4 = -1; zv5 = -1; zv6 = -1
  var r = random(1)
  var o = floor(random(VIBE_COUNT))
  if (r < 0.15) { zv0 = o; zv1 = o }                 // sometimes the circle goes its own way...
  else if (r < 0.3) { zv2 = o; zv3 = o }             // ...or the mushrooms
  else if (r < 0.4) { zv4 = o; zv5 = o; zv6 = o }    // ...or the leaves
  speed = 0.7 + random(0.8)
  if (random(1) < 0.6) autoFxSoon = 2.3     // after the vibe change, not stacked on it
}
function autoStep(dt) {
  autoLeft = max(0, autoLeft - dt)
  if (autoLeft <= 0) {
    autoWait = autoWait + dt
    // with music, land the change on the start of a bar (give up waiting after 4 s)
    if (!music || (newBeat && beatCount == 0) || autoWait > 4) { autoPick(); autoLeft = autoSeconds; autoWait = 0 }
  }
  if (autoFxSoon > 0) { autoFxSoon = autoFxSoon - dt; if (autoFxSoon <= 0) autoEffect() }
  autoFxT = autoFxT - dt
  if (autoFxT <= 0 && autoLeft > 2.5 && autoLeft < autoSeconds - 2.5) {   // not during a change                                // an occasional effect between changes
    autoFxT = 6 + random(10)
    if (random(1) < (music ? 0.6 : 0.35)) autoEffect()
  }
}

// ---- UI controls (the Pixelblaze's own pattern page and Firestorm) -------------------------
// Controls run only when changed (and once on load with their saved value) - no frame cost.
// Pixelblaze controls can show numbers and gauges but not text, so vibes are named buttons and
// the current vibe is a number (the controller page at :8121 shows it by name).
// Brightness is the Pixelblaze's built-in slider at the top of its page.

export function toggleAutoMode(on) { autoMode = on; autoLeft = autoSeconds }
export function sliderAutoSeconds(v) { autoSeconds = 5 + v * v * 115; autoLeft = min(autoLeft, autoSeconds) }  // 5 s .. 2 min
export function showNumberAutoSeconds() { return floor(autoSeconds) }
export function showNumberNextChangeIn() { return autoMode ? ceil(autoLeft) : 0 }

export function showNumberVibe() { return vibe }   // 0 cozy 1 party 2 chaos 3 wave 4 jumping 5 ethereal 6 snake 7 twinkle 8 heartbeat 9 aurora 10 meteor 11 bloom 12 kaleido 13 lotus 14 spectrum 15 fireflies 16 cascade 17 mycelium 18 butterfly 19 mosaic 20 wings 21 vortex
function pickVibe(n) { vibe = n; zv0 = -1; zv1 = -1; zv2 = -1; zv3 = -1; zv4 = -1; zv5 = -1; zv6 = -1; autoLeft = autoSeconds }
export function triggerVibeCozy() { pickVibe(0) }
export function triggerVibeHueRotate() { pickVibe(1) }   // "party" on the camp bus
export function triggerVibeChaos() { pickVibe(2) }
export function triggerVibeWave() { pickVibe(3) }
export function triggerVibeJumping() { pickVibe(4) }
export function triggerVibeEthereal() { pickVibe(5) }
export function triggerVibeSnake() { pickVibe(6) }
export function triggerVibeTwinkle() { pickVibe(7) }
export function triggerVibeHeartbeat() { pickVibe(8) }
export function triggerVibeAurora() { pickVibe(9) }
export function triggerVibeMeteor() { pickVibe(10) }
export function triggerVibeBloom() { pickVibe(11) }
export function triggerVibeKaleido() { pickVibe(12) }
export function triggerVibeLotus() { pickVibe(13) }
export function triggerVibeSpectrum() { pickVibe(14) }
export function triggerVibeFireflies() { pickVibe(15) }
export function triggerVibeCascade() { pickVibe(16) }
export function triggerVibeMycelium() { pickVibe(17) }
export function triggerVibeButterfly() { pickVibe(18) }
export function triggerVibeMosaic() { pickVibe(19) }
export function triggerVibeWings() { pickVibe(20) }
export function triggerVibeVortex() { pickVibe(21) }

export function sliderSpeed(v) { speed = 0.25 + v * 2.75 }            // 0.25x .. 3x; 1x at 0.27
export function toggleAudioSplit(on) { split = on }
export function sliderAudioSource(v) { audioSrc = min(2, floor(v * 3)) }   // left auto | middle sensor | right bus
export function showNumberAudioSource() { return audioSrc }               // 0 auto 1 sensor 2 bus
export function sliderSensitivity(v) { sensorGain = pow(2, (v - 0.5) * 6) }  // 1/8x .. 8x, middle 1x

// effect colour + buttons (whole facade, full strength, default durations)
var uiH = 0
var uiS = 0
var uiV = 1
export function hsvPickerEffectColor(h, sat, v) { uiH = h; uiS = sat; uiV = v }
export function triggerFlash() { flashH = uiH; flashS = uiS; flashV = uiV; flashZ = 255; flashT = 0 }
export function triggerPulse() { pulseH = uiH; pulseS = uiS; pulseV = uiV; pulseZ = 255; pulseT = 0 }
export function triggerRings() { ringsH = uiH; ringsS = uiS; ringsV = uiV; ringsZ = 255; ringsT = 0 }
export function triggerSpiral() { spiralH = uiH; spiralS = uiS; spiralV = uiV; spiralDir = random(1) < 0.5 ? -1 : 1; spiralArms = 3; spiralZ = 255; spiralT = 0 }
export function triggerShockwave() { waveH = uiH; waveS = uiS; waveV = uiV; waveDir = 1; waveZ = 255; waveT = 0 }
export function triggerSweep() { sweepH = uiH; sweepS = uiS; sweepV = uiV; sweepZ = 255; sweepT = 0 }
export function triggerWipe() { wipeH = uiH; wipeS = uiS; wipeV = uiV; wipeZ = 255; wipeT = 0 }
export function triggerRain() { rainH = uiH; rainS = uiS; rainV = uiV; rainZ = 255; rainT = 0 }
export function triggerComet() { cometH = uiH; cometS = uiS; cometV = uiV; cometZ = 255; cometT = 0 }
FIRE_FN[0] = triggerFlash; FIRE_FN[1] = triggerPulse; FIRE_FN[2] = triggerRings; FIRE_FN[3] = triggerSpiral; FIRE_FN[4] = triggerShockwave
FIRE_FN[5] = triggerSweep; FIRE_FN[6] = triggerWipe; FIRE_FN[7] = triggerRain; FIRE_FN[8] = triggerComet
// the 2026-10-07 one-shots, variants of the machinery above (petalfall = a black shockwave and sporeburst = rain
// on the mushrooms then the leaves exist as bridge presets only: no room left for their buttons)
export function triggerHalo() { triggerShockwave(); waveDir = -1 }                      // the ring flares, the glow closes in over the grid
export function triggerIris() { triggerShockwave(); waveS = 0; waveV = 0; waveDir = -1 } // the circle closes and reopens (a dark ring in)
export function triggerSunburst() { triggerSpiral(); spiralDir = 0; spiralArms = 12 }    // twelve rays from the heart

// read-outs, for tuning the audio feed
export function showNumberBpm() { return music ? (bpm > 0 && audioSrc != 1 ? bpm : aBpm) : 0 }
export function showNumberHearsMusic() { return aLive }
export function gaugeBass() { return bassS }
export function gaugeMids() { return midS }
export function gaugeHighs() { return highS }
export function gaugeLoudness() { return loudS }

var od = 0
var ow = 0
var fxi = 0
var rv = 0
var dg = 0
var de = 0
export function render2D(index, x, y) {
  var z = zoneOf[index]
  var m = zMode[z]
  if (m == 0) {                              // the common case: just the vibe
    VIBE_FN[zVibe[z]](index, x, y, z)
    hsv(hh, ss, vv * zGain[z])
    return
  }
  if (m == 1) {                              // + a whole-zone overlay, mixed inline (no call)
    VIBE_FN[zVibe[z]](index, x, y, z)
    vv = vv * zEnv[z]
    ow = ovW[z]
    if (ss < 0.05) hh = ovH[z]
    else if (ovS[z] >= 0.05) { od = ovH[z] - hh; hh = hh + (od - floor(od + 0.5)) * ow }
    ss = ss + (ovS[z] - ss) * ow
    vv = vv + (ovV[z] - vv) * ow
    hsv(hh, ss, vv * master)
    return
  }
  if (m == 3) {                              // vibe change only: one vibe per pixel except at the edge
    dg = index * 0.618034
    de = zFade[z] * 1.4 - 0.2 - (distC[index] * 1.4 + (dg - floor(dg)) * 0.3)   // same as dissolve()
    if (de >= 0.05) VIBE_FN[zVibe[z]](index, x, y, z)
    else if (de <= -0.05) VIBE_FN[zPrev[z]](index, x, y, z)
    else dissolve(index, x, y, z)
    hsv(hh, ss, vv * zGain[z])
    return
  }
  if (m == 4) {                              // one spatial effect (+ maybe an overlay)
    fxi = zFx[z * 7]
    VIBE_FN[zVibe[z]](index, x, y, z)
    if (fxRej[fxi] && ovW[z] <= 0) {         // outside the effect's band: nothing to mix
      rv = fxRej[fxi] == 1 ? x : fxRej[fxi] == 2 ? distC[index] : along[index]
      if (rv < fxLo[fxi] || rv > fxHi[fxi]) { hsv(hh, ss, vv * zGain[z]); return }
    }
    vv = vv * zEnv[z]
    eW = 0
    FX_FN[fxi](index, x)
    if (eW > 0.02) {                         // mixIn, inlined (no call per pixel)
      if (eW > 1) eW = 1
      if (ss < 0.05) hh = eH
      else if (eS >= 0.05) { od = eH - hh; hh = hh + (od - floor(od + 0.5)) * eW }
      ss = ss + (eS - ss) * eW
      vv = vv + (eV - vv) * eW
    }
    if (ovW[z] > 0) mixIn(ovH[z], ovS[z], ovV[z], ovW[z])
    hsv(hh, ss, vv * master)
    return
  }
  slowPixel(index, x, y, z)
}

export function render(index) {   // no map: lay the strip out left to right
  render2D(index, index / pixelCount, 0.5)
}
