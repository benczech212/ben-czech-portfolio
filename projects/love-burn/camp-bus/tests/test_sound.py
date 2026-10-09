"""The staff's sound player, without a sound card.

What matters here is not that aplay works - the Pi proves that - but the parts that
are easy to get quietly wrong: a new sound must stop the old one, a sound that ends
by itself must stop showing as playing, a URL must never reach outside the sounds
folder, a machine with no card must say so instead of crashing, and casts must stay
silent unless someone asked for a sound.

A short-lived Python process stands in for aplay, so this runs anywhere.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nodes", "staff_pi"))

from sound import SoundPlayer, card_of, parse_map  # noqa: E402


def _player(cmd_seconds: float):
    """A player command that 'plays' for this long, whatever file it is given."""
    return [sys.executable, "-c", "import time; time.sleep(%r)" % cmd_seconds]


@pytest.fixture
def sounds(tmp_path):
    (tmp_path / "magic").mkdir()
    (tmp_path / "magic" / "deep").mkdir()
    for rel in ("magic/01-glow.wav", "magic/02-bell.WAV", "magic/deep/x.wav",
                "top.wav", "notes.txt", "magic/.hidden.wav"):
        (tmp_path / rel).write_bytes(b"RIFF")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "nope.wav").write_bytes(b"RIFF")
    return tmp_path


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_card_of():
    assert card_of("plughw:wm8960soundcard") == "wm8960soundcard"
    assert card_of("hw:1,0") == "1"
    assert card_of("plughw:CARD=wm8960soundcard,DEV=0") == "wm8960soundcard"
    assert card_of("default") is None


def test_parse_map_is_empty_by_default():
    assert parse_map("") == {}
    assert parse_map("spin=a.wav, slam = b/c.wav,junk,=x.wav") == {"spin": "a.wav", "slam": "b/c.wav"}


def test_lists_wavs_recursively_and_nothing_else(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(5))
    assert sp.files == ["magic/01-glow.wav", "magic/02-bell.WAV", "magic/deep/x.wav", "top.wav"]


def test_new_files_are_found_on_rescan_or_on_play(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(0.05))
    (sounds / "late.wav").write_bytes(b"RIFF")
    assert "late.wav" not in sp.files
    assert sp.play("late.wav")          # dropped in after start: play rescans once
    sp.stop()


def test_a_url_cannot_reach_outside_the_folder(sounds, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "secret.wav"
    outside.write_bytes(b"RIFF")
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(5))
    for bad in ("../secret.wav", str(outside), "/etc/passwd", "notes.txt", "", "magic/../../x.wav"):
        assert sp.resolve(bad) is None, bad
        assert sp.play(bad) is False, bad
    assert sp.playing is None


def test_a_new_sound_stops_the_old_one(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(10))
    assert sp.play("top.wav")
    first = sp._proc
    assert sp.playing == "top.wav"
    assert sp.play("magic/01-glow.wav")
    assert first.poll() is not None, "the first sound is still running"
    assert sp.playing == "magic/01-glow.wav"
    sp.stop()
    assert sp.playing is None
    assert sp._proc is None


def test_a_sound_that_ends_stops_showing_as_playing(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(0.1))
    assert sp.play("top.wav")
    assert _wait(lambda: sp.playing is None)
    assert sp.last_error is None
    assert sp.state()["playing"] is None


def test_a_failed_play_says_why(sounds):
    fail = [sys.executable, "-c", "import sys; sys.stderr.write('aplay: main:831: audio open error: No such device\\n'); sys.exit(1)"]
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=fail)
    assert sp.play("top.wav")
    assert _wait(lambda: sp.playing is None)
    assert "No such device" in (sp.last_error or "")


def test_no_card_is_a_state_not_a_crash(sounds):
    sp = SoundPlayer(str(sounds), device="plughw:no_such_card_here", mixer="Speaker",
                     player=_player(5))
    assert sp.available is False
    assert "no_such_card_here" in sp.why
    assert sp.play("top.wav") is False
    assert sp.set_volume(50) is None
    st = sp.state()
    assert st["present"] is False and st["playing"] is None and st["volume"] is None
    sp.stop()                           # harmless


def test_a_late_card_is_found_and_announced(sounds):
    """The boot race of 2026-10-04: the service started ~15 s before the wm8960
    driver probed. The card must be picked up when it appears, not never."""
    seen = []
    sp = SoundPlayer(str(sounds), device="plughw:not_yet", mixer="", player=_player(5),
                     on_ready=lambda p, late: seen.append((p.available, late)),
                     retry=(0.05, 10, 0.05))
    assert sp.available is False and "looking again" in sp.why
    sp._probe = lambda: (True, "")          # the driver probes
    assert _wait(lambda: seen)
    assert seen[0][0] is True and seen[0][1] > 0
    assert sp.available and sp.why == "" and sp.state()["late"] is not None
    assert sp.play("top.wav")
    sp.close()


def test_a_missing_card_stops_looking_on_close(sounds):
    sp = SoundPlayer(str(sounds), device="plughw:never", mixer="", player=_player(5),
                     retry=(0.05, 10, 0.05))
    sp.close()
    time.sleep(0.2)
    assert sp.available is False


def test_missing_player_is_a_state_not_a_crash(sounds):
    sp = SoundPlayer(str(sounds), device="default", player=["no-such-aplay-binary"])
    assert sp.available is False and "not installed" in sp.why


def test_casts_are_silent_unless_mapped(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(5))
    assert sp.on_cast({"gesture": "spin", "spin_dir": 1}) is None
    assert sp.playing is None


def test_cast_map_prefers_the_direction(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(5),
                     cast_map=parse_map("spin=top.wav,spin_ccw=magic/01-glow.wav"))
    assert sp.sound_for({"gesture": "spin", "spin_dir": 1}) == "top.wav"
    assert sp.sound_for({"gesture": "spin", "spin_dir": -1}) == "magic/01-glow.wav"
    assert sp.sound_for({"gesture": "slam", "spin_dir": None}) is None
    sp.cast_map["*"] = "magic/deep/x.wav"       # any cast nothing more specific matched
    assert sp.sound_for({"gesture": "slam", "spin_dir": None}) == "magic/deep/x.wav"
    assert sp.sound_for({"gesture": "spin", "spin_dir": 1}) == "top.wav"
    del sp.cast_map["*"]
    assert sp.on_cast({"gesture": "spin", "spin_dir": -1}) == "magic/01-glow.wav"
    assert _wait(lambda: sp.playing == "magic/01-glow.wav")
    sp.stop()


# ---- the page's endpoints ------------------------------------------------------

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
        return json.loads(r.read())


def _post(port, path, header=True):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=b"", method="POST",
                                 headers={"X-Campbus": "1"} if header else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


@pytest.fixture
def web(sounds):
    from webui import start_web

    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(10))
    port = _free_port()
    httpd = start_web(port, lambda: {"sound": sp.state()}, sound=sp)
    yield port, sp
    sp.close()
    httpd._campbus_stop.set()
    httpd.shutdown()


def test_endpoints_list_play_stop(web):
    port, sp = web
    d = _get(port, "/sound/list")
    assert d["present"] is True and "magic/deep/x.wav" in d["files"]
    d = _post(port, "/sound/play?f=magic%2Fdeep%2Fx.wav")
    assert d["ok"] is True and d["playing"] == "magic/deep/x.wav"
    assert _get(port, "/state")["sound"]["playing"] == "magic/deep/x.wav"
    d = _post(port, "/sound/play?f=..%2F..%2Fetc%2Fpasswd")
    assert d["ok"] is False
    d = _post(port, "/sound/stop")
    assert d["playing"] is None
    d = _post(port, "/sound/volume?v=abc")          # no mixer here: ignored, not a 500
    assert d["volume"] is None


def test_state_changing_routes_reject_get_and_headerless_post(web):
    port, sp = web
    for path in ("/sound/play?f=magic%2Fdeep%2Fx.wav", "/sound/stop", "/config?arm_seconds=2"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(port, path)
        assert e.value.code == 405
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(port, path, header=False)
        assert e.value.code == 403
    assert sp.state()["playing"] is None


def test_endpoints_without_a_player():
    from webui import start_web

    port = _free_port()
    httpd = start_web(port, dict)
    try:
        d = _get(port, "/sound/list")
        assert d["present"] is False and d["files"] == []
    finally:
        httpd._campbus_stop.set()
        httpd.shutdown()


def test_node_flags_default_to_the_hat_and_home_sounds():
    import staff_node

    a = staff_node.build_parser().parse_args(["--startup-sound", ""])
    assert a.sounds == "~/sounds"
    assert a.sound_device == "plughw:wm8960soundcard"
    assert a.sound_mixer == "Speaker"
    assert a.sound_map == "" and a.sound_volume is None
    # nothing asks for sound without the page, a map or a startup sound: nothing is opened
    assert staff_node.open_sound(a, verbose=False) is None
    a = staff_node.build_parser().parse_args(["--web", "--no-sound"])
    assert staff_node.open_sound(a, verbose=False) is None


def test_stop_if_only_stops_that_sound(sounds):
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(10))
    assert sp.play("top.wav")
    assert sp.stop_if("magic/01-glow.wav") is False and sp.playing == "top.wav"
    assert sp.stop_if("top.wav") is True and sp.playing is None


def test_the_worker_keeps_order(sounds):
    """charge, then ready, then stop-if-charge must land in that order."""
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(10))
    sp.later(sp.play, "magic/01-glow.wav")
    sp.later(sp.play, "top.wav")
    sp.later(sp.stop_if, "magic/01-glow.wav")      # too late: must not stop top.wav
    assert _wait(lambda: sp._q.empty() and sp.playing == "top.wav")
    time.sleep(0.2)
    assert sp.playing == "top.wav"
    sp.close()


def test_pan_reverse_swaps_the_directions(sounds):
    m = parse_map("*=c.wav,spin_cw=l2r.wav,spin_ccw=r2l.wav")
    sp = SoundPlayer(str(sounds), device="default", mixer="", player=_player(5), cast_map=m)
    assert sp.sound_for({"gesture": "spin", "spin_dir": 1}) == "l2r.wav"      # cw = L->R
    assert sp.sound_for({"gesture": "spin", "spin_dir": -1}) == "r2l.wav"
    assert sp.sound_for({"gesture": "slam", "spin_dir": None}) == "c.wav"     # no direction: centred
    sp.pan_reverse = True
    assert sp.sound_for({"gesture": "spin", "spin_dir": 1}) == "r2l.wav"
    assert sp.sound_for({"gesture": "spin", "spin_dir": -1}) == "l2r.wav"
    assert sp.sound_for({"gesture": "slam", "spin_dir": None}) == "c.wav"
