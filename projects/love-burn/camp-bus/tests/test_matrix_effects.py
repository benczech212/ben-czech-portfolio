"""The matrix panel's effect engine, without a matrix panel.

`nodes/matrix_portal/effects.py` is deliberately free of CircuitPython so the part
that decides what colour the panel should be can be proven here rather than by
reflashing a board and squinting at it. What is checked is the part that is easy to
get quietly wrong: which edge a fill starts from, and whether the panel obeys the same
propagation rule as every other responder on the bus.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nodes", "matrix_portal"))

from effects import (  # noqa: E402
    Engine, LEVELS, STEPS, clamp01, distance_m, fbm, mix, noise_field, parse_color,
    projection, propagate, rgb_to_hsv, rotate_hue, snap_heading, _perm_table,
)

W, H = 64, 32
HERE = (25.7367, -80.1548)          # camp origin on Virginia Key (same as sim/camp.html)


def at(proj, x, y, width=W):
    return proj[y * width + x]


# ---- colour --------------------------------------------------------------------

def test_colours_parse_both_lengths_and_never_raise():
    assert parse_color("#ff4000") == pytest.approx((1.0, 0.25, 0.0), abs=0.01)
    assert parse_color("#f40") == pytest.approx((1.0, 0.27, 0.0), abs=0.01)
    # A malformed colour on the bus must not take a light off the air
    assert parse_color("chartreuse") == (1.0, 1.0, 1.0)
    assert parse_color(None) == (1.0, 1.0, 1.0)
    assert parse_color("#12345") == (1.0, 1.0, 1.0)


def test_mixing_is_the_contract_rule():
    black, red = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    assert mix(black, red, 0.0) == black
    assert mix(black, red, 1.0) == red
    assert mix(black, red, 0.5) == pytest.approx((0.5, 0.0, 0.0))


# ---- the eight directions ------------------------------------------------------

def test_headings_snap_to_eight_and_wrap():
    assert snap_heading(0) == (0, 0.0)
    assert snap_heading(44) == (1, 45.0)
    assert snap_heading(350) == (0, 0.0)
    assert snap_heading(-45) == (7, 315.0)
    assert snap_heading(180) == (4, 180.0)


def test_a_fill_heading_north_starts_at_the_bottom_edge():
    """`heading` is the direction the fill travels - the same meaning it has for a cone
    cast everywhere else in the contract. North is up, so it enters from the bottom."""
    proj = projection(W, H, 0.0)
    assert at(proj, W // 2, H - 1) <= 1           # bottom-centre is reached first
    # ...and the last pixel is a far *corner*, not the far mid-edge: with a circular
    # front the trailing edge is curved too.
    assert at(proj, 0, 0) == STEPS - 1
    assert at(proj, W // 2, 0) < STEPS - 1


def test_a_fill_heading_east_starts_at_the_left_edge():
    proj = projection(W, H, 90.0)
    assert at(proj, 0, H // 2) <= 1
    assert at(proj, W - 1, 0) == STEPS - 1
    assert at(proj, W - 1, H // 2) < STEPS - 1


def test_the_front_is_an_arc_and_not_a_straight_edge():
    """The whole point of the circular wavefront: along the leading edge, the middle
    arrives before the corners. A straight wipe would have them level."""
    proj = projection(W, H, 0.0)
    middle, corner = at(proj, W // 2, H - 1), at(proj, 0, H - 1)
    assert corner > middle, "the bottom corner should lag the bottom middle"


def test_curve_zero_is_a_ring_with_no_direction_at_all():
    ring = projection(W, H, 0.0, curve=0.0)
    assert at(ring, W // 2, H // 2) <= 1                 # grows from the middle
    assert at(ring, 0, 0) == STEPS - 1                   # corners last
    # and with the centre on the panel, heading stops meaning anything
    assert bytes(ring) == bytes(projection(W, H, 217.0, curve=0.0))


def test_a_big_curve_flattens_back_into_a_straight_wipe():
    flat = projection(W, H, 0.0, curve=60.0)
    for y in (0, H // 2, H - 1):                         # every column agrees
        assert at(flat, 0, y) == at(flat, W - 1, y)


def test_opposite_headings_are_mirror_images():
    north, south = projection(W, H, 0.0), projection(W, H, 180.0)
    for y in (0, H // 2, H - 1):
        assert at(north, 10, y) == at(south, 10, H - 1 - y)


@pytest.mark.parametrize("heading", [0, 45, 90, 135, 180, 225, 270, 315])
def test_every_direction_uses_the_whole_ramp(heading):
    """A diagonal must not finish early or run off the end - which is what happens if
    you normalise against the panel's width instead of against its corners."""
    proj = projection(W, H, float(heading))
    assert min(proj) == 0
    assert max(proj) == STEPS - 1


def test_the_eight_directions_are_all_different():
    maps = {bytes(projection(W, H, h * 45.0)) for h in range(8)}
    assert len(maps) == 8


# ---- propagation, same as every other responder --------------------------------

def test_no_origin_means_react_at_once():
    e = Engine()
    assert propagate({"intensity": 0.8}, None) == (0.0, 0.8)


def test_distance_becomes_delay():
    far = (HERE[0] + 60 / 111320.0, HERE[1])          # 60 m north
    delay, _ = propagate({"origin": {"lat": HERE[0], "lon": HERE[1]}, "speed_mps": 6}, far)
    assert delay == pytest.approx(10.0, abs=0.3)      # 60 m at 6 m/s


def test_distance_fades_intensity_to_forty_percent_at_range():
    far = (HERE[0] + 60 / 111320.0, HERE[1])
    _, i = propagate({"origin": {"lat": HERE[0], "lon": HERE[1]},
                      "intensity": 1.0, "range_m": 60}, far)
    assert i == pytest.approx(1.0)                    # a perfect cast never fades
    _, i = propagate({"origin": {"lat": HERE[0], "lon": HERE[1]},
                      "intensity": 0.9, "range_m": 60}, far)
    assert i == pytest.approx(0.9 * 0.4, abs=0.01)


def test_falloff_none_and_instant_are_honoured():
    far = (HERE[0] + 60 / 111320.0, HERE[1])
    msg = {"origin": {"lat": HERE[0], "lon": HERE[1]}, "intensity": 0.5,
           "falloff": "none", "instant": True}
    delay, i = propagate(msg, far)
    assert delay == 0.0 and i == pytest.approx(0.5)


def test_a_hard_cutoff_means_do_nothing_at_all():
    far = (HERE[0] + 60 / 111320.0, HERE[1])
    assert propagate({"origin": {"lat": HERE[0], "lon": HERE[1]}, "cutoff_m": 25}, far) is None


def test_a_cone_pointed_elsewhere_is_not_for_me():
    north = (HERE[0] + 30 / 111320.0, HERE[1])
    msg = {"origin": {"lat": HERE[0], "lon": HERE[1]}, "shape": "cone", "cone_deg": 70}
    assert propagate(dict(msg, heading=0), north) is not None      # pointed at me
    assert propagate(dict(msg, heading=180), north) is None        # pointed away


# ---- the engine ----------------------------------------------------------------

def _engine(hue=0.0):
    """A panel with the hue spread switched off, so a test can compare exact colours.
    `test_hue_wanders_across_the_fill` covers the spread itself."""
    e = Engine()
    e.hue_spread = hue
    e.submit("wipe", {"color": "#000000", "seconds": 0}, 0.0)
    e.palette(0.1)
    return e


def test_a_fill_covers_the_panel_and_stays():
    """A fill that quietly faded would be a light that turns itself off in the middle
    of the night."""
    e = _engine()
    e.submit("wipe", {"color": "#ff0000", "heading": 0, "seconds": 1.0}, 10.0)

    start = e.palette(10.0)
    assert start[-1] == pytest.approx((0.0, 0.0, 0.0), abs=0.02)    # far end untouched

    mid = e.palette(10.5)
    assert mid[0][0] > 0.9 and mid[-1][0] < 0.1                    # a front, not a flash

    e.palette(11.2)
    assert e.palette(30.0)[0] == pytest.approx((1.0, 0.0, 0.0), abs=0.02)
    assert e.palette(30.0)[-1] == pytest.approx((1.0, 0.0, 0.0), abs=0.02)


def test_seconds_zero_is_just_be_this_colour():
    """Which is how you change colour without inventing a second effect name for it."""
    e = _engine()
    e.submit("wipe", {"color": "#00ff00", "seconds": 0}, 5.0)
    assert e.palette(5.0)[0] == pytest.approx((0.0, 1.0, 0.0), abs=0.02)
    assert e.palette(5.0)[-1] == pytest.approx((0.0, 1.0, 0.0), abs=0.02)


def test_intensity_is_a_mix_not_a_switch():
    e = _engine()
    e.submit("wipe", {"color": "#ffffff", "seconds": 0, "intensity": 0.5}, 1.0)
    assert e.palette(1.0)[0] == pytest.approx((0.5, 0.5, 0.5), abs=0.03)


def test_a_flash_decays_back_to_what_was_underneath():
    e = _engine()
    e.submit("wipe", {"color": "#0000ff", "seconds": 0}, 0.0)
    e.submit("flash", {"color": "#ffffff", "ms": 1000}, 1.0)
    assert e.palette(1.0)[0] == pytest.approx((1.0, 1.0, 1.0), abs=0.02)
    later = e.palette(2.1)[0]
    assert later == pytest.approx((0.0, 0.0, 1.0), abs=0.02)


def test_charge_accumulates_and_bleeds_off():
    e = _engine()
    e.submit("charge", {"color": "#ff0000", "intensity": 1.0}, 0.0)
    hot = e.palette(0.0)[0][0]
    assert hot > 0.4
    cool = e.palette(30.0)[0][0]
    assert cool < hot * 0.2, "charge must bleed off, not stain forever"


def test_a_sweep_leaves_nothing_behind():
    e = _engine()
    e.submit("sweep", {"color": "#ffffff", "heading": 90, "seconds": 1.0}, 0.0)
    assert max(c[0] for c in e.palette(0.5)) > 0.8     # the band is somewhere on it
    e.palette(1.1)
    assert max(c[0] for c in e.palette(2.0)) < 0.02    # and then it is gone


def test_a_new_direction_finishes_the_old_fill_rather_than_fighting_it():
    """The panel holds one pixel map at a time, so two fills at different angles cannot
    both be drawn. The earlier one commits instead of being dropped."""
    e = _engine()
    e.submit("wipe", {"color": "#ff0000", "heading": 0, "seconds": 2.0}, 0.0)
    e.palette(0.5)
    e.submit("wipe", {"color": "#0000ff", "heading": 90, "seconds": 2.0}, 1.0)
    assert e.bucket == snap_heading(90)[0]
    assert e.fill[0] > 0.9, "the red fill should have been committed, not thrown away"


def test_an_unknown_effect_is_ignored_like_the_contract_says():
    e = _engine()
    assert e.submit("kaleidoscope", {"color": "#ffffff"}, 0.0) is False
    assert e.ignored >= 1


def test_a_delayed_cast_does_not_light_the_panel_early():
    """The panel stands next to a crystal. If it ignored the delay it would answer a
    cast before the crystal beside it did, and the ripple across camp would be wrong."""
    e = _engine()
    e.set_position(HERE[0] + 60 / 111320.0, HERE[1])
    e.submit("wipe", {"color": "#ffffff", "seconds": 0,
                      "origin": {"lat": HERE[0], "lon": HERE[1]}, "speed_mps": 6}, 0.0)
    assert e.palette(2.0)[0] == pytest.approx((0.0, 0.0, 0.0), abs=0.02)
    assert e.palette(10.5)[0][0] > 0.3


def test_the_panel_knows_which_way_its_top_faces():
    """`north` is the bearing of the panel's own top edge. With it set, a bus heading
    is a true compass direction rather than a panel-space one."""
    e = Engine(north=90.0)                 # the panel's top points east
    e.submit("wipe", {"color": "#ffffff", "heading": 90, "seconds": 1.0}, 0.0)
    assert e.heading == 0.0                # ... so "east" is straight up the panel


def test_the_palette_is_the_right_shape_for_the_hardware():
    e = _engine()
    pal = e.rgb888(0.0)
    assert len(pal) == STEPS * LEVELS
    assert all(isinstance(v, int) and 0 <= v <= 0xFFFFFF for v in pal)


# ---- on a real broker ----------------------------------------------------------

def test_the_panel_answers_a_real_command_over_a_real_broker(broker):
    """The engine is proven above; this proves the *node* - that a panel subscribing
    the way `code.py` does actually receives what the mapping engine would send it, and
    paints it. Everything between here and the board is CircuitPython wiring."""
    import uuid

    from campbus import Node

    host, port = broker
    suffix = uuid.uuid4().hex[:6]
    engine = Engine()
    engine.hue_spread = 0.0                       # compare an exact colour below
    panel = Node("matrix_" + suffix, host=host, port=port, fw="test")
    panel.subscribe("cmd/matrix/#",
                    lambda topic, msg: engine.submit(topic.rsplit("/", 1)[-1], msg, 0.0))
    panel.subscribe("cmd/all/#",
                    lambda topic, msg: engine.submit(topic.rsplit("/", 1)[-1], msg, 0.0))
    panel.connect()
    assert panel.wait_ready(5.0), "panel never subscribed"

    sender = Node("booth_" + suffix, host=host, port=port, fw="test")
    sender.connect()
    sender.wait_ready(5.0)
    try:
        sender.publish("cmd/matrix/wipe",
                       {"color": "#00c8ff", "intensity": 1.0, "heading": 135, "seconds": 0})
        deadline = time.time() + 5.0
        while engine.received == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert engine.received == 1, "the panel never got the command"

        pal = engine.palette(0.0)
        assert pal[0] == pytest.approx((0.0, 200 / 255, 1.0), abs=0.02)
        assert engine.bucket == snap_heading(135)[0]

        # a broadcast reaches it too, and an effect it does not implement does not
        sender.publish("cmd/all/flash", {"color": "#ffffff", "ms": 500})
        sender.publish("cmd/matrix/kaleidoscope", {"color": "#ffffff"})
        deadline = time.time() + 5.0
        while engine.received < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert engine.received == 2 and engine.ignored >= 1
    finally:
        sender.close()
        panel.close()


# ---- the Perlin texture ----------------------------------------------------------

def test_the_noise_is_gradient_noise_not_a_grid_of_blobs():
    """Value noise peaks at every integer lattice point; gradient noise is zero there.
    That is the difference you can see on a panel, so it is worth pinning down."""
    perm = _perm_table()
    for x, y in ((3.0, 5.0), (12.0, 1.0), (0.0, 0.0)):
        assert abs(fbm(x, y, perm, octaves=1)) < 1e-9


def test_the_noise_is_the_same_every_boot():
    """A fixed permutation table, so the panel wears the same texture each time rather
    than a new one whenever it loses power."""
    assert bytes(noise_field(32, 16)) == bytes(noise_field(32, 16))


def test_the_texture_bands_hold_about_the_same_number_of_pixels():
    """Stacked octaves are roughly Gaussian, so a linear mapping bunches nearly
    everything into the middle bands - measured once at 1 pixel in the top band, which
    would leave `twinkle` sparkling a single LED. Equalising is what fixes it."""
    field = noise_field(W, H)
    counts = [0] * LEVELS
    for v in field:
        counts[v] += 1
    ideal = W * H / LEVELS
    assert min(counts) > ideal * 0.6, counts
    assert max(counts) < ideal * 1.6, counts


def test_the_texture_actually_varies_the_brightness():
    e = Engine()
    e.submit("wipe", {"color": "#808080", "seconds": 0}, 0.0)
    full = e.full_palette(0.1)
    band = [full[i][0] for i in range(LEVELS)]           # one position, every band
    assert max(band) > min(band) * 1.3, "the texture should light and darken"


def test_hue_wanders_across_the_fill():
    e = Engine()
    e.submit("wipe", {"color": "#ff4000", "seconds": 0}, 0.0)
    ramp = e.palette(0.1)
    assert ramp[0] != ramp[-1], "a flat colour across the whole fill is the old look"
    # ...but it is a hue shift, not a fade: both ends stay about as bright
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    assert abs(lum(ramp[0]) - lum(ramp[-1])) < 0.12


def test_rotating_a_hue_leaves_the_colour_recognisably_as_bright():
    """The standard YIQ matrix is an approximation - it is nine multiplies rather than
    a round trip through HSV - so this pins the property that matters on a light: a
    hue shift must not turn into a brightness shift."""
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    red = (1.0, 0.0, 0.0)
    assert rotate_hue(red, 0) == red
    for deg in (20, 40, -30):                     # the angles the spread actually uses
        assert lum(rotate_hue(red, deg)) == pytest.approx(lum(red), abs=0.12)
        assert rotate_hue(red, deg) != red


def test_the_palette_is_position_times_texture():
    """Exactly what an 8-bit bitmap index can address, which is why the packing is
    free: index = position * LEVELS + texture."""
    e = Engine()
    assert len(e.full_palette(0.0)) == STEPS * LEVELS <= 256
    assert len(e.rgb888(0.0)) == STEPS * LEVELS


# ---- effects that pick out places ------------------------------------------------

def test_twinkle_only_lights_the_small_high_bands():
    """The broad dim areas must stay put, or it reads as the whole panel flickering."""
    e = Engine()
    e.submit("twinkle", {"color": "#ffffff", "intensity": 1.0, "seconds": 4}, 0.0)
    lit = [max(e._twinkle_gain(l, 0.1 * t) for t in range(60)) for l in range(LEVELS)]
    assert all(v == 0.0 for v in lit[:LEVELS - 3]), "low bands should never sparkle"
    assert lit[-1] > 0.0, "the top band should"
    assert lit[-1] > lit[-3], "and it should sparkle hardest"


def test_twinkle_sparkles_rather_than_just_staying_on():
    e = Engine()
    e.submit("twinkle", {"color": "#ffffff", "intensity": 1.0, "seconds": 6}, 0.0)
    over_time = [e._twinkle_gain(LEVELS - 1, t / 20.0) for t in range(80)]
    assert max(over_time) > 0.3
    assert min(over_time) == 0.0, "a sparkle has to go out again"


def test_twinkle_needs_no_fill_running():
    e = Engine()
    e.submit("twinkle", {"color": "#ffffff", "intensity": 1.0, "seconds": 3}, 0.0)
    assert e.busy
    assert len(e.full_palette(0.5)) == STEPS * LEVELS


def test_charge_grows_out_of_the_peaks_before_it_floods():
    """A light charge should freckle the panel; a heavy one should reach the broad
    areas too. That ordering is what makes it look like something growing."""
    light = Engine()
    light.submit("charge", {"color": "#22c55e", "intensity": 0.25}, 0.0)
    heavy = Engine()
    heavy.submit("charge", {"color": "#22c55e", "intensity": 1.0}, 0.0)
    assert light.band_mix[-1] > 0.0, "the peaks should catch first"
    assert light.band_mix[0] == 0.0, "the broad areas should not, yet"
    assert heavy.band_mix[0] > 0.0, "a full charge should reach everywhere"


def test_a_grown_region_carries_the_colour_it_was_given():
    e = Engine()
    e.submit("wipe", {"color": "#000000", "seconds": 0}, 0.0)
    e.submit("charge", {"color": "#22c55e", "intensity": 1.0}, 0.0)
    full = e.full_palette(0.05)
    peak = full[LEVELS - 1]                              # first position, top band
    assert peak[1] > peak[0] and peak[1] > peak[2], "should be green, not white"


def test_a_grown_region_bleeds_off():
    e = Engine()
    e.submit("charge", {"color": "#22c55e", "intensity": 1.0}, 0.0)
    hot = max(e.band_mix)
    e.full_palette(40.0)
    assert max(e.band_mix) < hot * 0.1, "charge bleeds off; it does not stain forever"
    assert not e.busy


# ---- zones: the panel as a set of light sources --------------------------------

def _zoned(rle, hue="#ff0000", seconds=0.0):
    from effects import decode_zones

    e = Engine()
    zones, count = decode_zones({"w": W, "h": H, "rle": rle}, W, H)
    e.set_zones(zones, count)
    e.submit("rainbow", {"color": hue, "intensity": 1.0, "seconds": seconds}, 0.0)
    return e


def test_a_zone_map_decodes_and_counts():
    from effects import decode_zones

    zones, count = decode_zones({"w": W, "h": H, "rle": [[0, 1024], [1, 512], [2, 512]]}, W, H)
    assert count == 3 and len(zones) == W * H
    assert zones[0] == 0 and zones[1030] == 1 and zones[2000] == 2


def test_a_map_for_a_different_panel_is_refused():
    """Better no map than half a map: a short one would light part of a rock."""
    from effects import decode_zones

    assert decode_zones({"w": 32, "h": 16, "rle": [[0, 512]]}, W, H) == (None, 0)
    assert decode_zones({"w": W, "h": H, "rle": [[0, 10]]}, W, H) == (None, 0)
    assert decode_zones({"nonsense": True}, W, H) == (None, 0)
    assert decode_zones({"w": W, "h": H, "rle": "not a list"}, W, H) == (None, 0)


def test_unpainted_pixels_stay_dark():
    """Zone 0 is "no rock here", which is what lets the editor treat unpainted pixels
    as unassigned rather than as a giant extra rock."""
    e = _zoned([[0, 1024], [1, 512], [2, 512]])
    ramp = e.palette(0.0)
    assert ramp[0] == pytest.approx((0.0, 0.0, 0.0), abs=0.01)
    assert max(ramp[1]) > 0.5 and max(ramp[2]) > 0.5


def test_each_zone_gets_its_own_hue():
    from effects import GOLDEN_ANGLE
    e = _zoned([[0, 512], [1, 512], [2, 512], [3, 512]])
    ramp = e.palette(0.0)
    hues = [rgb_to_hsv(ramp[i])[0] for i in (1, 2, 3)]
    assert hues[0] == pytest.approx(0, abs=2)                     # the commanded colour
    assert hues[1] == pytest.approx(GOLDEN_ANGLE, abs=2)
    assert hues[2] == pytest.approx(2 * GOLDEN_ANGLE % 360, abs=2)


def test_adding_a_rock_does_not_recolour_the_ones_already_mapped():
    """Calibration adds zones one at a time, and an even spread over the count
    repaints every rock each time - so you can never learn which colour is which."""
    four = _zoned([[0, 512], [1, 512], [2, 512], [3, 512]]).palette(0.0)
    five = _zoned([[0, 410], [1, 410], [2, 410], [3, 410], [4, 408]]).palette(0.0)
    assert four[1:4] == pytest.approx(five[1:4], abs=0.01)


def test_rocks_next_to_each_other_do_not_come_out_the_same_colour():
    """Not a claim about covering the wheel evenly - an even spread does that better,
    and at seventeen rocks it is 21 degrees a step either way. The claim is about which
    rocks collide: calibration walks the panel in a serpentine and hands neighbouring
    rocks consecutive ids, so under an even spread the two rocks most likely to be
    confused are also the two closest on the wheel. The golden angle puts consecutive
    ids 137 degrees apart instead, whatever the count turns out to be."""
    from effects import GOLDEN_ANGLE
    rle = [[i, 120] for i in range(17)] + [[0, 2048 - 17 * 120]]
    ramp = _zoned(rle).palette(0.0)
    hues = [rgb_to_hsv(c)[0] for c in ramp[1:17]]
    apart = [min(abs(a - b), 360 - abs(a - b)) for a, b in zip(hues, hues[1:])]
    assert min(apart) > 100, "consecutive ids should be nowhere near each other"
    assert GOLDEN_ANGLE == pytest.approx(137.5, abs=0.1)


def test_the_calibration_probe_is_white_on_top_of_a_working_map():
    """What you are checking at the rocks is *this* one against *those*, so the rocks
    already worked out have to stay lit in their own colours while the next block is
    asked about. The probe rides along in the same map under a reserved id."""
    from effects import PROBE_ZONE
    rle = [[0, 512], [1, 512], [2, 512], [PROBE_ZONE, 512]]
    ramp = _zoned(rle).palette(0.0)
    assert ramp[PROBE_ZONE] == pytest.approx((1.0, 1.0, 1.0), abs=0.02)
    assert max(ramp[1]) > 0.5 and max(ramp[2]) > 0.5, "the map keeps its colours"
    assert rgb_to_hsv(ramp[1])[1] > 0.9, "and they are still real hues, not washed out"


def test_the_reserved_id_is_only_white_when_a_probe_is_actually_in_the_map():
    """Otherwise the top bucket would glow white on every panel that never calibrates."""
    from effects import PROBE_ZONE
    ramp = _zoned([[0, 1024], [1, 512], [2, 512]]).palette(0.0)
    assert ramp[PROBE_ZONE] == pytest.approx((0.0, 0.0, 0.0), abs=0.01)


def test_buckets_past_the_last_zone_are_dark_too():
    """Only as many buckets as there are rocks should light, whatever the panel's
    32 buckets could hold."""
    e = _zoned([[0, 1024], [1, 1024]])
    ramp = e.palette(0.0)
    assert max(max(c) for c in ramp[2:]) == pytest.approx(0.0, abs=0.01)


def test_the_wheel_turns_and_comes_back_round():
    e = _zoned([[0, 512], [1, 512], [2, 512], [3, 512]], seconds=8.0)
    start = e.palette(0.0)[1]
    third = e.palette(8.0 / 3.0)[1]
    full = e.palette(8.0)[1]
    assert third != pytest.approx(start, abs=0.05), "it should move"
    assert full == pytest.approx(start, abs=0.02), "and land where it began"


def test_a_static_rainbow_is_the_editors_debug_view():
    """`seconds: 0` holds the wheel still, so "show me each zone in its own colour" and
    the show effect are the same code path - what you author is what the rocks do."""
    e = _zoned([[0, 512], [1, 512], [2, 512], [3, 512]], seconds=0.0)
    assert e.palette(0.0) == e.palette(30.0)
    assert not e.busy, "holding still should not ask for frames"


def test_a_turning_rainbow_asks_for_frames():
    e = _zoned([[0, 1024], [1, 1024]], seconds=8.0)
    assert e.busy


def test_a_rainbow_is_a_standing_mode_until_something_takes_the_panel_back():
    e = _zoned([[0, 1024], [1, 1024]])
    assert e.rainbow is not None and e.wants_zones
    e.submit("wipe", {"color": "#ffffff", "seconds": 0}, 5.0)
    assert e.rainbow is None, "a fill owns the buckets; they cannot both"
    assert not e.wants_zones


def test_the_rainbow_can_be_switched_off():
    e = _zoned([[0, 1024], [1, 1024]])
    e.submit("rainbow", {"intensity": 0}, 5.0)
    assert e.rainbow is None


def test_a_rainbow_works_before_anyone_has_drawn_the_rocks():
    """No zone map: lay it along the fill axis instead, so the effect is testable on a
    bare panel."""
    e = Engine()
    e.submit("rainbow", {"color": "#ff0000", "intensity": 1.0, "seconds": 0}, 0.0)
    ramp = e.palette(0.0)
    assert not e.wants_zones
    assert len({rgb_to_hsv(c)[0] // 30 for c in ramp}) > 4, "should span the wheel"


def test_zones_and_texture_share_one_byte():
    """The whole reason zones were cheap to add: a zone indexes the palette exactly
    the way a fill position does."""
    from effects import MAX_ZONES

    assert MAX_ZONES * LEVELS <= 256
