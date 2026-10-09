#!/usr/bin/env python3
"""Mapping engine - the only thing on the bus that writes to cmd/.

Reads a mappings.yaml (see booth/mappings.yaml for the format), listens to the
input topics, and publishes the commands the rules ask for. The file is
hot-reloaded when it changes on disk; a broken edit is reported and the previous
rules stay live.

    python booth/mapping_engine.py --host localhost
    python booth/mapping_engine.py --check            # validate the yaml and exit

Publishes mapping/loaded (retained) after every successful load so dashboards
and tests can see which rules are live, and mapping/rules (retained) with the
full rule list so the Routes page (sim/routes.html) can show and edit them.
A UI publishes the edited list to mapping/set; the engine validates it, rewrites
mappings.yaml (compact JSON-flow style, hand-editable), reloads, and answers with
a fresh mapping/rules - or mapping/error if the edit was rejected.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clients", "python"))
import paho.mqtt.client as mqtt  # noqa: E402  (topic_matches_sub)
import yaml  # noqa: E402

from campbus import Node, now_ms  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "mappings.yaml")

# Everything the engine listens to. Rules whose when.topic is outside these roots
# still work if you list the topic under `subscribe:` at the top of the yaml.
INPUT_ROOTS = ["toy/#", "prop/#", "staff/#", "instrument/#", "midi/#", "kitchen/#", "booth/#", "dj/#", "audio/beat"]
ALLOWED_OUTPUT_PREFIXES = ("cmd/", "display/")
SET_TOPIC = "mapping/set"          # a UI writes the whole rule list here; the engine rewrites the yaml
RULES_TOPIC = "mapping/rules"      # retained: the live rule list, full detail
ERROR_TOPIC = "mapping/error"      # a rejected mapping/set (not retained)

_WHOLE = re.compile(r"^\{(\w+)\}$")


class _SafeMap(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(value: Any, msg: Dict[str, Any]) -> Any:
    """Fill "{field}" placeholders from the input payload. Whole-string placeholders keep type."""
    if isinstance(value, str):
        m = _WHOLE.match(value)
        if m:
            return msg.get(m.group(1))
        if "{" in value:
            try:
                return value.format_map(_SafeMap(msg))
            except (ValueError, IndexError):
                return value
        return value
    if isinstance(value, dict):
        return {k: render(v, msg) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, msg) for v in value]
    return value


class Rule:
    def __init__(self, d: Dict[str, Any]):
        if not isinstance(d, dict):
            raise ValueError(f"rule must be a mapping, got {type(d).__name__}")
        self.name: str = str(d.get("name") or "(unnamed)")
        self.enabled: bool = d.get("enabled", True) is not False
        self.group: str = str(d.get("group") or "").strip()
        when = d.get("when") or {}
        if not isinstance(when, dict) or not when.get("topic"):
            raise ValueError(f"rule {self.name!r}: when.topic is required")
        self.topic: str = str(when["topic"])
        self.match: Dict[str, Any] = {k: v for k, v in when.items() if k != "topic"}
        then = d.get("then") or []
        if not isinstance(then, list) or not then:
            raise ValueError(f"rule {self.name!r}: then must be a non-empty list")
        self.then: List[Dict[str, Any]] = []
        for out in then:
            if not isinstance(out, dict) or not out.get("topic"):
                raise ValueError(f"rule {self.name!r}: every then item needs a topic")
            t = str(out["topic"])
            if not t.startswith(ALLOWED_OUTPUT_PREFIXES):
                raise ValueError(f"rule {self.name!r}: output {t!r} must start with one of {ALLOWED_OUTPUT_PREFIXES}")
            if mqtt.topic_matches_sub(self.topic, t):
                raise ValueError(f"rule {self.name!r}: output {t!r} would re-trigger its own input (loop)")
            self.then.append({"topic": t, "payload": out.get("payload") or {}, "retain": bool(out.get("retain", False))})
        self.throttle_ms = int(d.get("throttle_ms") or 0)
        self._last_fire = 0.0
        self.fired = 0
        # normalised form: what mapping/rules publishes and what the yaml writer serialises
        self.raw: Dict[str, Any] = {"name": self.name, "when": {"topic": self.topic, **self.match},
                                    "then": [{"topic": o["topic"], "payload": o["payload"], **({"retain": True} if o["retain"] else {})}
                                             for o in self.then]}
        if self.group:
            self.raw["group"] = self.group
        if self.throttle_ms:
            self.raw["throttle_ms"] = self.throttle_ms
        if not self.enabled:
            self.raw["enabled"] = False

    def matches(self, topic: str, msg: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if not mqtt.topic_matches_sub(self.topic, topic):
            return False
        for k, v in self.match.items():
            if msg.get(k) != v:
                return False
        if self.throttle_ms:
            now = time.monotonic() * 1000
            if now - self._last_fire < self.throttle_ms:
                return False
            self._last_fire = now
        return True

    def outputs(self, msg: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"topic": o["topic"], "payload": render(o["payload"], msg), "retain": o["retain"]} for o in self.then]


def parse_rules(doc: Any) -> "tuple[List[Rule], List[str]]":
    """Validate a parsed document ({rules: [...], subscribe: [...]}). Returns (rules, extra_subscriptions)."""
    if not isinstance(doc, dict):
        raise ValueError("top level must be a mapping with a `rules:` list")
    raw = doc.get("rules") or []
    if not isinstance(raw, list):
        raise ValueError("`rules:` must be a list")
    rules = [Rule(r) for r in raw]
    extra = [str(t) for t in (doc.get("subscribe") or [])]
    return rules, extra


def load_rules(path: str) -> "tuple[List[Rule], List[str]]":
    """Parse the yaml file. Returns (rules, extra_subscriptions). Raises on any error."""
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return parse_rules(doc)


def dump_rules(rules: List[Rule], extra: List[str], note: str = "") -> str:
    """Serialise rules as yaml in a compact one-line-per-item style (JSON flow mappings are valid yaml),
    so the file the Routes page writes is still easy to read and edit by hand."""
    j = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    out = ["# Camp Bus mapping table - the ONLY thing that decides what an input controls.",
           "# Hot-reloaded by booth/mapping_engine.py on save; a bad edit is rejected and the previous rules stay live.",
           "# Edit here or in the Routes page (sim/routes.html), which rewrites this file in this compact style.",
           "# rule: name, group, when {topic filter + fields that must match}, then [{topic, payload, retain}], throttle_ms, enabled.",
           "# Payload strings like \"{intensity}\" are filled from the input; outputs only go to cmd/ and display/.",
           "# Contract: docs/SCHEMA.md." + (f"  ({note})" if note else ""), ""]
    if extra:
        out.append("subscribe: " + j(extra))
    out.append("rules:")
    last_group = None
    for r in rules:
        if r.group != last_group:                       # a comment banner per group keeps the file scannable
            out.append(f"  # ---- {r.group or 'ungrouped'}")
            last_group = r.group
        out.append("  - name: " + j(r.name))
        if r.group:
            out.append("    group: " + j(r.group))
        if not r.enabled:
            out.append("    enabled: false")
        if r.throttle_ms:
            out.append(f"    throttle_ms: {r.throttle_ms}")
        out.append("    when: " + j(r.raw["when"]))
        out.append("    then:")
        for o in r.raw["then"]:
            out.append("      - " + j(o))
        out.append("")
    return "\n".join(out)


class MappingEngine:
    def __init__(self, path: str = DEFAULT_FILE, host: Optional[str] = None, port: Optional[int] = None,
                 node_id: str = "mapping_engine", poll_s: float = 1.0, verbose: bool = True):
        kw = {}
        if host:
            kw["host"] = host
        if port:
            kw["port"] = port
        self.path = path
        self.poll_s = poll_s
        self.verbose = verbose
        self.rules: List[Rule] = []
        self.extra: List[str] = []
        self._mtime = -1.0
        self._subscribed: set = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.node = Node(node_id, fw="mapping-0.1", role="booth", **kw)
        for t in INPUT_ROOTS:
            self._subscribe(t)
        self.node.subscribe(SET_TOPIC, self._on_set)

    # -- lifecycle
    def start(self) -> "MappingEngine":
        self.node.connect()
        self.node.wait_ready(5.0)          # publish mapping/loaded + mapping/rules only once the link is up (QoS 0 before connect is dropped)
        self.reload(force=True)
        threading.Thread(target=self._watch, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.node.close()

    # -- rules
    def reload(self, force: bool = False) -> bool:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError as e:
            self._log(f"cannot stat {self.path}: {e}")
            return False
        if not force and mtime == self._mtime:
            return False
        try:
            rules, extra = load_rules(self.path)
        except Exception as e:  # keep the old rules live
            self._log(f"REJECTED {os.path.basename(self.path)}: {e}  (keeping {len(self.rules)} previous rules)")
            self._mtime = mtime
            return False
        with self._lock:
            self.rules = rules
            self.extra = extra
        self._mtime = mtime
        for t in extra:
            self._subscribe(t)
        names = [r.name for r in rules]
        self._log(f"loaded {len(rules)} rules from {os.path.basename(self.path)}: " + "; ".join(names))
        self.node.publish("mapping/loaded", {"file": os.path.basename(self.path), "rules": len(rules),
                                             "names": names}, retain=True)
        self.node.publish(RULES_TOPIC, {"file": os.path.basename(self.path), "rules": [r.raw for r in rules],
                                        "subscribe": extra}, retain=True)
        return True

    def apply(self, doc: Dict[str, Any], note: str = "") -> "tuple[bool, str]":
        """Validate a whole rule document, write it to the yaml file and reload. Returns (ok, message)."""
        try:
            rules, extra = parse_rules(doc)
            text = dump_rules(rules, extra, note)
        except Exception as e:
            return False, str(e)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, self.path)
        self.reload(force=True)
        return True, f"{len(rules)} rules"

    def _on_set(self, topic: str, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        ok, info = self.apply({"rules": msg.get("rules") or [], "subscribe": msg.get("subscribe") or []},
                              note=f"written from {msg.get('by') or 'the bus'} {time.strftime('%Y-%m-%d %H:%M')}")
        if ok:
            self._log(f"mapping/set applied: {info}")
        else:
            self._log(f"mapping/set REJECTED: {info}")
            self.node.publish(ERROR_TOPIC, {"error": info, "by": msg.get("by")})

    def _watch(self) -> None:
        while not self._stop.wait(self.poll_s):
            self.reload()

    # -- routing
    def _subscribe(self, topic: str) -> None:
        if topic in self._subscribed:
            return
        self._subscribed.add(topic)
        self.node.subscribe(topic, self._on_input)

    def _on_input(self, topic: str, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        with self._lock:
            rules = list(self.rules)
        for rule in rules:
            if not rule.matches(topic, msg):
                continue
            rule.fired += 1
            for out in rule.outputs(msg):
                self.node.publish(out["topic"], out["payload"], retain=out["retain"])
                if self.verbose:
                    self._log(f"{topic} -> {out['topic']} {out['payload']}   [{rule.name}]")
            self.node.count()

    def _log(self, s: str) -> None:
        if self.verbose:
            print(f"{time.strftime('%H:%M:%S')} [mapping] {s}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--host", default=None, help="broker (default: $CAMPBUS_HOST or campbus.local)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--check", action="store_true", help="validate the yaml and exit")
    a = ap.parse_args()
    if a.check:
        try:
            rules, extra = load_rules(a.file)
        except Exception as e:
            print(f"INVALID: {e}")
            sys.exit(1)
        print(f"OK: {len(rules)} rules" + (f", extra subscriptions {extra}" if extra else ""))
        for r in rules:
            print(f"  - {r.name}: {r.topic} {r.match or ''} -> {[o['topic'] for o in r.then]}")
        return
    eng = MappingEngine(a.file, a.host, a.port).start()
    print(f"mapping engine -> {eng.node.host}:{eng.node.port}, watching {a.file} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        eng.stop()


if __name__ == "__main__":
    main()
