"""Unit tests for the mapping engine's rule matching and templating (no broker needed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "booth"))
from mapping_engine import DEFAULT_FILE, Rule, dump_rules, load_rules, parse_rules, render  # noqa: E402


def test_default_mappings_file_is_valid():
    rules, extra = load_rules(DEFAULT_FILE)
    assert len(rules) >= 5
    assert all(r.name != "(unnamed)" for r in rules)


def test_render_keeps_type_for_whole_placeholder():
    msg = {"value": 2.5, "artist": "SideQuest", "soft": False}
    assert render("{value}", msg) == 2.5
    assert render("{soft}", msg) is False
    assert render("{missing}", msg) is None
    assert render("Now: {artist}!", msg) == "Now: SideQuest!"
    assert render({"a": "{value}", "b": ["{artist}", 1]}, msg) == {"a": 2.5, "b": ["SideQuest", 1]}
    assert render("no braces", msg) == "no braces"
    assert render("#ff4000", msg) == "#ff4000"


def test_rule_matches_topic_filter_and_fields():
    r = Rule({"name": "t", "when": {"topic": "staff/+", "gesture": "slam"},
              "then": [{"topic": "cmd/hallway/shockwave", "payload": {"i": "{intensity}"}}]})
    assert r.matches("staff/staff_01", {"gesture": "slam", "intensity": 0.9})
    assert not r.matches("staff/staff_01", {"gesture": "spin"})
    assert not r.matches("toy/staff_01", {"gesture": "slam"})
    assert r.outputs({"gesture": "slam", "intensity": 0.9}) == [
        {"topic": "cmd/hallway/shockwave", "payload": {"i": 0.9}, "retain": False}]


def test_rule_throttle():
    r = Rule({"name": "t", "when": {"topic": "toy/crank"}, "throttle_ms": 10_000,
              "then": [{"topic": "cmd/hallway/speed", "payload": {}}]})
    assert r.matches("toy/crank", {})
    assert not r.matches("toy/crank", {})


def test_dump_rules_round_trips_the_default_file(tmp_path):
    """What the Routes page writes back must load to exactly the same rules (names, matches, outputs, flags)."""
    import yaml
    rules, extra = load_rules(DEFAULT_FILE)
    text = dump_rules(rules, extra, note="test")
    again, extra2 = parse_rules(yaml.safe_load(text))
    assert [r.raw for r in again] == [r.raw for r in rules]
    assert extra2 == extra
    assert text.startswith("# Camp Bus mapping table")     # still a commented, hand-editable file


def test_disabled_rule_never_matches_but_is_kept():
    r = Rule({"name": "off", "enabled": False, "when": {"topic": "toy/x"}, "then": [{"topic": "cmd/x/y", "payload": {}}]})
    assert not r.matches("toy/x", {})
    assert r.raw["enabled"] is False
    on = Rule({"name": "on", "when": {"topic": "toy/x"}, "then": [{"topic": "cmd/x/y", "payload": {}}]})
    assert on.matches("toy/x", {}) and "enabled" not in on.raw


def test_groups_survive_the_writer():
    import yaml
    r = Rule({"name": "g", "group": "Props", "when": {"topic": "toy/x"}, "then": [{"topic": "cmd/x/y", "payload": {}}]})
    assert r.raw["group"] == "Props"
    text = dump_rules([r], [])
    again, _ = parse_rules(yaml.safe_load(text))
    assert again[0].group == "Props" and "# ---- Props" in text
    rules, _ = load_rules(DEFAULT_FILE)
    assert {x.group for x in rules} >= {"Toys", "Props", "Mycelium", "Geodes", "Plant", "Tubes", "Booth"}


@pytest.mark.parametrize("bad", [
    {"when": {"topic": "toy/x"}},                                              # no then
    {"when": {}, "then": [{"topic": "cmd/x/y"}]},                              # no topic
    {"when": {"topic": "toy/x"}, "then": [{"topic": "toy/y"}]},                # output outside cmd/ display/
    {"when": {"topic": "cmd/#"}, "then": [{"topic": "cmd/x/y"}]},              # would loop
    "not a mapping",
])
def test_bad_rules_are_rejected(bad):
    with pytest.raises(ValueError):
        Rule(bad)
