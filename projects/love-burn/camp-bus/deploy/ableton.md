# Ableton layout for the touch instruments

Built 2026-09-21 through the AbletonMCP Remote Script (Live 12 Lite); rebuilt the same day in a fresh set with sequential channels. The set is the booth's instrument
rig: every touch instrument on the camp bus arrives as MIDI on one loopMIDI port (`Camp Bus`) via
`tools/midi_bridge.py`, one MIDI channel per instrument. The Instruments page (`sim/instruments.html`)
plays the same way, so the set can be rehearsed with nothing built.

## Tracks

| # | Track | Input | Instrument | What the bus sends |
|---|---|---|---|---|
| 1 | `Mushrooms · ch1` | Camp Bus, ch 1 | **MPE Thunder Drum** (Sounds > MPE Sounds; Ben's pick 2026-09-21; a pitched instrument rack, so each mushroom's note plays a different pitch of the drum rather than a different pad) |
| 2 | `Geodes · ch2` | Camp Bus, ch 2 | **Detune Tune** (Instrument Rack > Effects / Drift > Effects; Ben's pick; rack, polyphonic, chord mode works) | `touch` = note on, held until `release`; `hold` = CC 74 with the live capacitive reading. Default notes 60/64/67/71 (C major 7) |
| 3 | `Plant · ch3` | Camp Bus, ch 3 | **Helly Belly** (Instrument Rack > Synth Rhythmic; Ben's pick; a rhythmic rack, so a snapped leaf starts a pulsing pattern for the gate time) | `bend` = CC 74 with the flex; `release` = the note at the snap velocity (gated); default notes 36/40/43/47 |
| 4 | `Tubes · ch4` | Camp Bus, ch 4 | **empty - still to pick** (wants something polyphonic that sustains, like the geodes: Drift pad, Mallets, Strings) | the water chord tubes (`instrument: tubes`, three tubes tuned as one chord): `touch` = note on held until `release`, `hold` = CC 74 with the live water reading |
| 5, 6 | `3-Audio`, `4-Audio` | | | untouched from the template; use for the PA return / ambient bed later |

No clips: the set is a live play-through rig. Everything the instruments send is heard as it arrives
(Monitor = In on each track); nothing loops and nothing needs the transport running. Tempo 120 only
matters for the synced delay. (Demo clips were tried on 2026-09-21 and removed: Session clips loop by
default and Ben wants the sounds to play as they come in.)

## Things the Remote Script cannot do (do these by hand once)

1. **Track inputs.** In each of the four MIDI tracks pick `Camp Bus` in the "MIDI From" chooser and the
   channel from the table (ch 1 / 2 / 3 / 4), set Monitor to **In**, and arm the track. Without a
   channel filter every track plays every instrument.
2. **CC 74 → filter.** Press Ctrl+M, click the rack macro, then send a CC from the instrument (hold a
   geode / bend a leaf on the Instruments page, or the bridge) and Live binds it:
   - Geodes: Detune Tune's first macro is **Filter Cutoff** - map CC 74 there.
   - Plant: Helly Belly's macros - map CC 74 to its filter macro (open the rack's macros with the round
     button on its title bar).
   Ctrl+M again to leave mapping mode. Then the touch reading opens the pad and the bend shapes the bass.
3. **Returns.** Return A: Reverb (hall, ~4 s decay, 20% send from Geodes and Plant, 10% from Mushrooms).
   Return B: Delay, synced 1/8 dotted, 15% from Geodes. The script cannot address return tracks.
4. **Preferences > Link, Tempo & MIDI:** `Camp Bus` input on for Track and Remote (Remote is what makes
   the Ctrl+M mapping work).

## Velocity -> loudness

Whether a harder hit is louder depends on the patch: Drift presets have a `Vel > Vol` amount (Detune Tune's
inner Drift sits at 0.5, so velocity moves half the volume range), Wavetable and MPE racks route velocity
wherever the designer chose, and the Remote Script cannot reach those nested knobs. So the bus does it for
you: the bridge and the page send **CC 11 (expression) = velocity right before every note on**, and each
instrument track now has a **Utility** after its instrument. One-time mapping per track:

1. Ctrl+M, click Utility's **Gain** knob, hit the instrument once (page, CLI or bridge) so CC 11 arrives on
   that track's channel, Ctrl+M again.
2. In the mapping browser (left), set that mapping's **Min to -18 dB and Max to 0 dB** so a soft touch is
   quieter, not silent.

Hold and bend readings never touch CC 11, only CC 74, so the filter mappings are unaffected. `--vel-cc 0`
on the bridge turns it off; `--vel-cc 7` uses channel volume instead if you would rather map that.

## Keeping the instruments out of each other's way

- One port, one channel per instrument, sequential (1 / 2 / 3 / 4). The track input chooser's channel filter is what
  separates them; the Instruments page's MIDI table shows "no conflicts" or a red line if two instruments
  share a channel or a pad note is duplicated, and the bridge logs a WARNING for the same clash.
- Note numbers can overlap across instruments (the plant's 36 and the kick's 36) because the channel differs.
- On the camp bus each instrument is its own `from`, has its own map position and its own `nearest` crystals,
  and its routes carry `combo: false`, so instruments playing together never trigger combo animations on a
  shared crystal (props still can).
- CC 74 is per channel, so the geode reading and the plant bend never touch each other's filter.

## Choosing sounds per instrument

Three ways, from quickest to most flexible:

1. **Drag from Live's Browser.** Open the Browser (Ctrl+Alt+B), pick a category on the left (Drums for
   kits, Instruments > Drift for synth presets, Sounds for everything sorted by type), and drag a preset
   onto the track header. It replaces the current instrument. `Preview` (the headphone icon at the bottom
   of the Browser) lets you audition before dropping.
2. **Hot-swap.** Click the instrument's title bar in the device view and press **Q**: the Browser jumps to
   presets of the same kind and every click swaps the sound in place, so you can step through kits or pads
   while playing the Instruments page in the other window. Q again to leave.
3. **Ask the assistant.** Over the MCP link it can list any Browser folder and load a preset by name
   ("load Balsam Gamelan on the geodes", "try the Ahlimba kit on the mushrooms"). Loading replaces the
   instrument on the track; nothing stacks.

What each instrument needs from a sound: mushrooms want a Drum Rack (any kit; pads on C1 up so the
page's notes 36/38/42/45/39/51 hit something), geodes want something polyphonic that sustains while
held and has a filter for CC 74 (Drift pads, Mallets, Strings), the plant wants a mono or poly bass with
a filter (Drift Bass) - the snap plays the note, the bend rides the filter - and the tubes want the same kind
of sustaining polyphonic patch as the geodes, since all three tubes are held together as a chord. Whatever you pick, the bus
side does not change: notes, channels and CC are the same.

## Presets vs. Live

Note numbers come from the Instruments page's MIDI table and ride the bus as retained
`config/midi/<instrument_id>`; the bridge stamps them on every event, so switching a note preset there
retunes what Live receives without touching the set. Keep the Drum Rack on the GM layout and the two
Drift racks chromatic and every preset works.

## Recreating

The set was built by the assistant over the MCP link (the third-party AbletonMCP server, registered with
`claude mcp add ableton -- <path-to-ableton-mcp>`, and its Remote Script installed in Live's User Library). Save it as `Camp Bus.als` in the User Library's
Templates so a fresh laptop starts from it; the steps above are all that is not in the file.
