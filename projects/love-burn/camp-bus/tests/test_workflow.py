"""End-to-end workflow tests: simulated input -> broker -> mapping engine -> mock device.

Runs against an in-process amqtt broker by default, or the Docker mosquitto with
CAMPBUS_HOST=localhost. Uses the same scenario runner as `sim/inputs.py run`.
"""
import os
import sys
import textwrap
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "sim"))
sys.path.insert(0, os.path.join(ROOT, "booth"))

from inputs import build, run_scenario  # noqa: E402

SMOKE = os.path.join(ROOT, "sim", "scenarios", "smoke.yaml")


def test_smoke_scenario_passes(engine, devices, recorder):
    node, rec = recorder
    passed, failed, failures = run_scenario(SMOKE, node, rec, default_within_ms=1500, verbose=False)
    assert failed == 0, "\n".join(failures)
    assert passed >= 10


def test_mock_devices_receive_the_right_effects(engine, devices, recorder):
    node, rec = recorder
    since = time.perf_counter()
    node.publish(*_msg("press", "big_red_button"))
    node.publish(*_msg("checkin", "SideQuest"))
    node.publish(*_msg("foodcall", "dinner"))
    time.sleep(1.0)
    assert devices["hallway"].seen("cmd/hallway/sweep", since), "hallway never got its sweep"
    assert devices["crystal_01"].seen("cmd/crystals/flash", since)
    assert devices["crystal_02"].seen("cmd/crystals/flash", since)
    assert devices["resolume"].seen("cmd/resolume/clip", since)[0]["name"] == "SideQuest"
    assert devices["display_entrance"].seen("booth/dj", since)[0]["artist"] == "SideQuest"
    assert devices["display_entrance"].seen("display/banner", since)[0]["level"] == "takeover"
    assert devices["kitchen_bell"].seen("cmd/kitchen/bell", since)
    # the kitchen bell must not hear toy traffic
    assert not devices["kitchen_bell"].seen("cmd/hallway/#", since)


def test_late_joiner_gets_retained_dj(engine, devices, recorder, broker):
    """A display that boots after check-in still shows who's on (retained booth/dj)."""
    from devices import MockDisplay
    node, rec = recorder
    node.publish(*_msg("checkin", "LateNightLarry"))
    time.sleep(0.3)
    host, port = broker
    from conftest import SUFFIX
    late = MockDisplay("display_late" + SUFFIX, host, port, "mock-display", quiet=True).start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not late.seen("booth/dj"):
            time.sleep(0.05)
        got = late.seen("booth/dj")
        assert got and got[0]["artist"] == "LateNightLarry"
    finally:
        late.stop()


def test_mapping_set_rewrites_the_file_and_rejects_bad_edits(broker, recorder, tmp_path):
    """The Routes page publishes the whole rule list to mapping/set; the engine writes the yaml, reloads and
    answers with mapping/rules. A rule with an illegal output is rejected on mapping/error and nothing changes."""
    from mapping_engine import MappingEngine, load_rules
    node, rec = recorder
    host, port = broker
    f = tmp_path / "mappings.yaml"
    f.write_text(textwrap.dedent("""
        rules:
          - name: lever
            when: { topic: "toy/lever", event: "pull" }
            then: [{ topic: "cmd/hallway/sweep", payload: { color: "#00ff00" } }]
    """), encoding="utf-8")
    from conftest import SUFFIX
    eng = MappingEngine(str(f), host, port, node_id="mapping_set" + SUFFIX, poll_s=0.1, verbose=False).start()
    try:
        _wait(eng.node)
        since = time.perf_counter()
        new_rules = [
            {"name": "lever", "when": {"topic": "toy/lever", "event": "pull"}, "then": [{"topic": "cmd/hallway/warp", "payload": {"color": "#ff00ff"}}]},
            {"name": "button off", "enabled": False, "throttle_ms": 50, "when": {"topic": "toy/big_red_button"}, "then": [{"topic": "cmd/all/flash", "payload": {"ms": 300}}]},
        ]
        node.publish("mapping/set", {"rules": new_rules, "by": "test"})
        got = rec.wait_for(since, [{"topic": "mapping/rules", "payload": None}], 3000)
        assert got[0] is not None, "engine never answered mapping/set with mapping/rules"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and "warp" not in f.read_text(encoding="utf-8"):
            time.sleep(0.05)
        rules, _ = load_rules(str(f))
        assert [r.name for r in rules] == ["lever", "button off"]
        assert rules[1].enabled is False and rules[1].throttle_ms == 50
        # the new routing is live, the disabled one is silent
        since = time.perf_counter()
        node.publish(*_msg("pull", "lever"))
        node.publish(*_msg("press", "big_red_button"))
        got = rec.wait_for(since, [{"topic": "cmd/hallway/warp", "payload": {"color": "#ff00ff"}}], 1500)
        assert got[0] is not None
        time.sleep(0.3)
        assert rec.find(since, "cmd/all/flash", None) is None
        # a bad edit is rejected and leaves the file alone
        since = time.perf_counter()
        node.publish("mapping/set", {"rules": [{"name": "evil", "when": {"topic": "toy/x"}, "then": [{"topic": "toy/y", "payload": {}}]}]})
        got = rec.wait_for(since, [{"topic": "mapping/error", "payload": None}], 3000)
        assert got[0] is not None and "toy/y" in got[0]["error"]
        assert "evil" not in f.read_text(encoding="utf-8")
    finally:
        eng.stop()


def test_hot_reload_picks_up_new_rule(broker, recorder, tmp_path):
    from mapping_engine import MappingEngine
    node, rec = recorder
    host, port = broker
    f = tmp_path / "mappings.yaml"
    f.write_text(textwrap.dedent("""
        rules:
          - name: lever
            when: { topic: "toy/lever", event: "pull" }
            then: [{ topic: "cmd/hallway/sweep", payload: { color: "#00ff00" } }]
    """), encoding="utf-8")
    from conftest import SUFFIX
    eng = MappingEngine(str(f), host, port, node_id="mapping_reload" + SUFFIX, poll_s=0.1, verbose=False).start()
    try:
        _wait(eng.node)
        since = time.perf_counter()
        node.publish(*_msg("pull", "lever"))
        assert rec.wait_for(since, [{"topic": "cmd/hallway/sweep"}], 1500)[0]
        # the new rule isn't there yet
        since = time.perf_counter()
        node.publish(*_msg("press", "new_button"))
        assert rec.wait_for(since, [{"topic": "cmd/crystals/flash"}], 400)[0] is None

        # edit the file: add a rule (mtime must change - bump it explicitly for fast filesystems)
        time.sleep(0.05)
        f.write_text(f.read_text(encoding="utf-8") + (
            '  - name: new button\n'
            '    when: { topic: "toy/new_button", event: "press" }\n'
            '    then: [{ topic: "cmd/crystals/flash", payload: { color: "#0000ff" } }]\n'
        ), encoding="utf-8")
        os.utime(f, (time.time() + 1, time.time() + 1))
        time.sleep(0.5)
        assert len(eng.rules) == 2
        since = time.perf_counter()
        node.publish(*_msg("press", "new_button"))
        assert rec.wait_for(since, [{"topic": "cmd/crystals/flash", "payload": {"color": "#0000ff"}}], 1500)[0]

        # a broken edit is rejected and the 2 good rules stay live
        f.write_text("rules: [ { name: broken, when: { topic: 'toy/x' } } ]", encoding="utf-8")
        os.utime(f, (time.time() + 2, time.time() + 2))
        time.sleep(0.5)
        assert len(eng.rules) == 2
        since = time.perf_counter()
        node.publish(*_msg("pull", "lever"))
        assert rec.wait_for(since, [{"topic": "cmd/hallway/sweep"}], 1500)[0]
    finally:
        eng.stop()


def _msg(kind, *args):
    topic, payload, retain = build(kind, list(args) if args else None)
    return topic, payload, retain


def _wait(n, timeout=5.0):
    assert n.wait_ready(timeout), f"{n.node_id} not ready"
