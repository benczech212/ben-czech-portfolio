"""The bus -> MIDI translation (pure; no MIDI port or broker needed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from midi_bridge import channel_clashes, translate as _translate  # noqa: E402


def translate(msg, cfg=None, cc=74):          # the exact-list tests below predate the expression CC; it is tested separately
    return _translate(msg, cfg, cc, vel_cc=0)


def test_expression_cc_precedes_every_note_on_by_default():
    ev = [e for e, _ in _translate({"instrument": "mycelium", "pad": "amanita", "event": "hit", "velocity": 0.5, "note": 36, "ch": 1})]
    assert ev[0] == ("cc", 1, 11, 64) and ev[1] == ("note_on", 1, 36, 64)
    ev = [e for e, _ in _translate({"instrument": "geode", "pad": "a", "event": "touch", "velocity": 1.0, "value": 0.2, "note": 60, "ch": 2})]
    assert ev[:3] == [("cc", 2, 74, 25), ("cc", 2, 11, 127), ("note_on", 2, 60, 127)]
    ev = [e for e, _ in _translate({"instrument": "plant", "pad": "l", "event": "release", "velocity": 0.8, "value": 0, "note": 40, "ch": 3})]
    assert ("cc", 3, 11, 102) in ev and ev.index(("cc", 3, 11, 102)) < ev.index(("note_on", 3, 40, 102))
    ev = [e for e, _ in _translate({"instrument": "geode", "pad": "a", "event": "hold", "value": 0.5, "note": 60, "ch": 2})]
    assert all(e[2] != 11 for e in ev)                     # holds and bends never touch the expression CC


def test_channel_clashes_between_instruments_are_reported():
    ok = {"mycelium_01": {"a": {"note": 36, "ch": 10}}, "geode_01": {"b": {"note": 60, "ch": 1}}}
    assert channel_clashes(ok) == []
    bad = {"mycelium_01": {"a": {"note": 36, "ch": 1}}, "geode_01": {"b": {"note": 60, "ch": 1}}}
    assert channel_clashes(bad) and "channel 1" in channel_clashes(bad)[0]


def test_mushroom_hit_is_a_gated_note_on_channel_10():
    ev = translate({"instrument": "mycelium", "pad": "amanita", "event": "hit", "velocity": 0.5, "note": 36, "ch": 10})
    assert ev[0] == (("note_on", 10, 36, 64), 0.0)
    assert ev[1][0] == ("note_off", 10, 36, 0) and ev[1][1] < 0          # off after the gate


def test_geode_touch_hold_release_is_a_held_note_with_cc():
    on = translate({"instrument": "geode", "pad": "amethyst", "event": "touch", "velocity": 0.69, "value": 0.41, "note": 60, "ch": 1})
    assert [e for e, _ in on] == [("cc", 1, 74, 52), ("note_on", 1, 60, 88)]
    hold = translate({"instrument": "geode", "pad": "amethyst", "event": "hold", "velocity": 0.69, "value": 0.8, "note": 60, "ch": 1})
    assert [e for e, _ in hold] == [("cc", 1, 74, 102)]
    off = translate({"instrument": "geode", "pad": "amethyst", "event": "release", "velocity": 0, "value": 0, "note": 60, "ch": 1})
    assert [e for e, _ in off] == [("cc", 1, 74, 0), ("note_off", 1, 60, 0)]


def test_plant_bend_is_cc_and_the_snap_is_the_note():
    bend = translate({"instrument": "plant", "pad": "leaf_2", "event": "bend", "velocity": 0.5, "value": 0.5, "note": 40, "ch": 2})
    assert [e for e, _ in bend] == [("cc", 2, 74, 64)]
    snap = translate({"instrument": "plant", "pad": "leaf_2", "event": "release", "velocity": 0.88, "value": 0, "note": 40, "ch": 2})
    assert [e for e, _ in snap][:2] == [("cc", 2, 74, 0), ("note_on", 2, 40, 112)]
    assert snap[2][0] == ("note_off", 2, 40, 0) and snap[2][1] < 0


def test_tube_touch_hold_release_is_a_held_note_like_a_geode():
    on = translate({"instrument": "tubes", "pad": "tube_1", "event": "touch", "velocity": 0.7, "value": 0.52, "note": 48})   # no ch -> tubes default 4
    assert [e for e, _ in on] == [("cc", 4, 74, 66), ("note_on", 4, 48, 89)]
    hold = translate({"instrument": "tubes", "pad": "tube_1", "event": "hold", "velocity": 0.7, "value": 0.9, "note": 48, "ch": 4})
    assert [e for e, _ in hold] == [("cc", 4, 74, 114)]
    off = translate({"instrument": "tubes", "pad": "tube_1", "event": "release", "velocity": 0, "value": 0, "note": 48, "ch": 4})
    assert [e for e, _ in off] == [("cc", 4, 74, 0), ("note_off", 4, 48, 0)]           # held, not plucked: no note on the way out


def test_chord_mode_plays_every_note_of_the_chord():
    on = translate({"instrument": "geode", "pad": "amethyst", "event": "touch", "velocity": 0.5, "value": 0.5, "note": 60, "notes": [60, 64, 67, 71], "ch": 1})
    assert [e for e, _ in on] == [("cc", 1, 74, 64), ("note_on", 1, 60, 64), ("note_on", 1, 64, 64), ("note_on", 1, 67, 64), ("note_on", 1, 71, 64)]
    off = translate({"instrument": "geode", "pad": "amethyst", "event": "release", "velocity": 0, "value": 0, "note": 60, "notes": [60, 64, 67, 71], "ch": 1})
    assert [e[2] for e, _ in off if e[0] == "note_off"] == [60, 64, 67, 71]
    cfg = {"amethyst": {"note": 57, "ch": 1, "notes": [57, 60, 64]}}          # retained config can carry the chord too
    on = translate({"instrument": "geode", "pad": "amethyst", "event": "touch", "velocity": 1, "note": 60, "ch": 1}, cfg)
    assert [e[2] for e, _ in on if e[0] == "note_on"] == [57, 60, 64]


def test_config_overrides_note_and_channel_and_defaults_apply():
    cfg = {"amanita": {"note": 41, "ch": 11}}
    ev = translate({"instrument": "mycelium", "pad": "amanita", "event": "hit", "velocity": 1.0, "note": 36, "ch": 10}, cfg)
    assert ev[0][0] == ("note_on", 11, 41, 127)
    ev = translate({"instrument": "plant", "pad": "leaf_1", "event": "hit", "velocity": 0.3, "note": 36})   # no ch -> plant default 3
    assert ev[0][0] == ("note_on", 3, 36, 38)
    assert translate({"instrument": "x", "event": "nothing"}) == []
