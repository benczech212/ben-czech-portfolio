"""The spell knob, without a knob.

`knob.py` keeps the seesaw behind a four-method device, so everything that decides
something - which hue a click lands on, what colour a cast carries, when the pixel is
written, the cast flash and the startup rainbow, surviving a knob that stops
answering, remembering the hue - is tested here against a fake.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nodes", "staff_pi"))

from knob import Flash, SpellKnob, hex_to_hue, hue_to_hex, load_hue, pixel_rgb  # noqa: E402


class FakeSeesaw:
    def __init__(self):
        self.pos = 0
        self.down = False
        self.fail = False
        self.pixels = []

    def position(self):
        if self.fail:
            raise OSError(121, "Remote I/O error")
        return self.pos

    def pressed(self):
        if self.fail:
            raise OSError(121, "Remote I/O error")
        return self.down

    def pixel(self, r, g, b):
        if self.fail:
            raise OSError(121, "Remote I/O error")
        self.pixels.append((r, g, b))


def _knob(**kw):
    dev = FakeSeesaw()
    kw.setdefault("intro", 0)
    return dev, SpellKnob(dev, **kw)


def test_colour_conversions():
    assert hue_to_hex(0) == "#ff0000"
    assert hue_to_hex(120) == "#00ff00"
    assert hue_to_hex(360 + 240) == "#0000ff"
    assert hex_to_hue("#ff4000") == pytest.approx(15.0, abs=0.5)    # the default --color
    assert hex_to_hue("not a colour") == 0.0
    assert pixel_rgb(0, 0) == (0, 0, 0)
    assert pixel_rgb(0, 1) == (255, 0, 0)
    dim = pixel_rgb(0, 0.3)
    assert 0 < dim[0] < 40, "the resting glow should be dim but not off"


def test_clicks_turn_the_hue_and_recolour_the_casts():
    seen = []
    dev, k = _knob(hue=15, step=15, on_change=seen.append)
    k.tick(0.0)                      # first read only learns where the knob is
    assert k.hue == 15 and not seen
    dev.pos = 3
    k.tick(0.04)
    assert k.hue == 60 and seen[-1] == hue_to_hex(60) == k.color
    dev.pos = -2                     # back past where it started, and round the wheel
    k.tick(0.08)
    assert k.hue == pytest.approx(345) and k.turns == 8


def test_reverse_turns_the_other_way():
    dev, k = _knob(hue=90, step=15, reverse=True)
    k.tick(0.0)
    dev.pos = 2
    k.tick(0.04)
    assert k.hue == 60


def test_a_knob_at_rest_writes_nothing():
    dev, k = _knob(hue=200, rest=0.3)
    for i in range(50):
        k.tick(i * 0.04)
    assert len(dev.pixels) == 1, "the pixel is written once, then left alone"
    assert dev.pixels[0] == pixel_rgb(200, 0.3)


def test_a_cast_flashes_up_to_full_and_back_to_the_glow():
    dev, k = _knob(hue=0, rest=0.3)
    k.tick(0.0)
    k.flash.at = 10.0                # what cast() does, on a clock we control
    levels = []
    t = 10.0
    while t < 10.0 + k.flash.length + 0.2:
        k.tick(t)
        levels.append(k.level)
        t += 0.04
    assert max(levels) == 1.0
    assert levels[-1] == 0.3
    peak = levels.index(1.0)
    assert all(a >= b - 1e-9 for a, b in zip(levels[peak:], levels[peak + 1:])), "falls without flicker"
    assert dev.pixels[-1] == pixel_rgb(0, 0.3)


def test_flash_envelope_shape():
    f = Flash(rise=0.1, hold=0.2, fall=1.0)
    assert f.level(5.0, 0.3) == 0.3              # idle
    f.at = 5.0
    assert f.level(5.05, 0.3) == pytest.approx(0.65)
    assert f.level(5.2, 0.3) == 1.0
    assert 0.3 < f.level(5.8, 0.3) < 1.0
    assert f.level(6.4, 0.3) == 0.3 and f.at is None


def test_the_button_previews_once_per_press():
    pressed = []
    dev, k = _knob(on_press=lambda: pressed.append(1))
    k.tick(0.0)
    dev.down = True
    for i in range(5):               # held down for five polls is still one press
        k.tick(0.04 * (i + 1))
    dev.down = False
    k.tick(0.3)
    assert pressed == [1] and k.presses == 1
    assert k.flash.at is not None, "a preview flashes like a cast"


def test_startup_rainbow_goes_round_and_lands_home():
    dev, k = _knob(hue=120, rest=0.3, intro=1.0)
    k._intro_at = 0.0                # what start() does
    for i in range(30):
        k.tick(i * 0.04)
    hues = {hex_to_hue("#%02x%02x%02x" % p) // 30 for p in dev.pixels if any(p)}
    assert len(hues) >= 8, "the rainbow should visit most of the wheel"
    k.tick(1.2)
    assert k.level == 0.3 and dev.pixels[-1] == pixel_rgb(120, 0.3)


def test_a_knob_that_stops_answering_is_counted_not_fatal():
    dev, k = _knob(hue=10)
    k.tick(0.0)
    dev.fail = True
    k.tick(0.04)
    k.tick(0.08)
    assert k.ok is False and k.io_errors == 2
    assert k.state()["ok"] is False
    dev.fail = False
    dev.pos = 7                      # it may have been reset meanwhile: re-learn, do not jump
    k.tick(0.12)
    assert k.ok is True and k.hue == 10
    dev.pos = 8
    k.tick(0.16)
    assert k.hue == 25


def test_a_knob_that_comes_back_is_set_up_again():
    dev, k = _knob(hue=10)
    dev.setups = 0
    dev.setup = lambda: setattr(dev, "setups", dev.setups + 1)
    k.tick(0.0)
    dev.fail = True
    k.tick(0.04)
    dev.fail = False
    k.tick(0.08)
    assert dev.setups == 1 and k.ok
    k.tick(0.12)
    assert dev.setups == 1, "set up once on recovery, not every poll"


def test_the_hue_survives_a_restart(tmp_path):
    path = str(tmp_path / "knob.json")
    dev, k = _knob(hue=15, step=15, state_file=path)
    k.tick(0.0)
    dev.pos = 4
    k.tick(1.0)
    assert not os.path.exists(path), "not written on every click"
    k.tick(4.5)                      # quiet for >3 s: now it is saved
    assert load_hue(path, 0) == 75
    assert load_hue(str(tmp_path / "missing.json"), 15.0) == 15.0


def test_no_knob_is_a_reason_not_a_crash():
    knob, why = SpellKnob.open(bus=1, addr=0x36)
    if knob is not None:             # a real knob on this machine: fine, just tidy up
        knob.stop()
        pytest.skip("a knob is actually attached here")
    assert why


# ---- wired into the node -------------------------------------------------------

def test_casts_carry_the_knob_colour_and_flash_it(monkeypatch):
    import staff_node

    args = staff_node.build_parser().parse_args(["--dry-run", "--prop-id", "staff_test"])
    dev, k = _knob(hue=15, step=15, on_change=lambda c: setattr(args, "color", c))
    k.tick(0.0)
    dev.pos = 8                      # 15 + 120 = 135 degrees
    k.tick(0.04)
    monkeypatch.setattr(staff_node, "KNOB", k)
    monkeypatch.setattr(staff_node, "SOUND", None)
    payload = staff_node.cast_payload(args.prop_id, "spin", 0.8, args.color, spin_dir=1)
    staff_node.emit(None, args, payload)
    assert payload["color"] == hue_to_hex(135)
    assert set(payload) == {"prop", "prop_id", "gesture", "intensity", "color", "heading",
                            "spin_dir", "lat", "lon", "acc_m", "via"}, "no new fields on the bus"
    assert k.flash.at is not None


def test_keep_trying_until_it_works():
    import threading
    import time

    import staff_node

    calls = []
    done = threading.Event()

    def attempt():
        calls.append(1)
        if len(calls) >= 3:
            done.set()
            return True
        return False

    t = staff_node.keep_trying("t", attempt, fast=0.02, fast_for=10, slow=0.02)
    assert done.wait(5)
    t.join(2)
    assert not t.is_alive() and len(calls) == 3


def test_knob_flags_and_switches():
    import staff_node

    a = staff_node.build_parser().parse_args([])
    assert a.knob_addr == 0x36 and a.knob_hz == 25 and a.knob_intro == 1.0
    assert a.startup_sound == "magic/09-power-up.wav"
    assert staff_node.build_parser().parse_args(["--knob-addr", "0x37"]).knob_addr == 0x37
    a = staff_node.build_parser().parse_args(["--no-knob"])
    assert staff_node.open_knob(a, verbose=False) is None
    a = staff_node.build_parser().parse_args(["--fire", "slam"])
    assert staff_node.open_knob(a, verbose=False) is None


def test_charging_glow_climbs_and_a_cast_takes_over():
    dev, k = _knob(hue=0, rest=0.3)
    k.charge_ramp = 1.0
    k.tick(0.0)
    k._charge_at = 10.0                  # what charging(True) does, on our clock
    k.tick(10.5)
    mid = k.level
    k.tick(11.5)
    assert 0.3 < mid < k.level == pytest.approx(k.charge_peak)
    k.charging(False)
    k.tick(12.0)
    assert k.level == 0.3
    k.charging(True)
    k.cast()
    assert k._charge_at is None and k.flash.at is not None
