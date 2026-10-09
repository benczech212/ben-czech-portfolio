"""campbus - tiny Python client for the AWEtisticats camp event bus.

Wraps paho-mqtt with the conventions from docs/SCHEMA.md:
  * client id == node_id
  * retained node/<id>/status with a last-will marking the node offline
  * status heartbeat every 30 s, interaction counter on node/<id>/count
  * JSON payloads in and out, subscriptions survive reconnects

Minimal use:

    from campbus import Node

    node = Node("crystal_03", fw="crystal-0.1")

    @node.on("audio/beat")
    def beat(topic, msg):
        print("beat", msg["n"], msg["bpm"])

    node.connect()          # non-blocking; reconnects forever in the background
    node.publish("toy/big_red_button", {"toy": "big_red_button", "event": "press", "value": 1})
    node.count()            # one more interaction for the analytics
    node.wait()             # block until Ctrl-C
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt

DEFAULT_HOST = os.environ.get("CAMPBUS_HOST", "campbus.local")
DEFAULT_PORT = int(os.environ.get("CAMPBUS_PORT", "1883"))
# Optional broker credentials. The dev "virtual camp" broker (bus/mosquitto.dev.conf) is
# anonymous, so these stay unset there; the production broker (bus/mosquitto.conf) needs them.
DEFAULT_USER = os.environ.get("CAMPBUS_USER") or None
DEFAULT_PASSWORD = os.environ.get("CAMPBUS_PASSWORD") or None
STATUS_INTERVAL_S = 30

Handler = Callable[[str, dict], None]


def now_ms() -> int:
    return int(time.time() * 1000)


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


class Node:
    def __init__(self, node_id: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 fw: str = "py-0.1", keepalive: int = 20, role: Optional[str] = None):
        self.node_id = node_id
        self.host, self.port, self.fw, self.keepalive = host, port, fw, keepalive
        self.role = role                     # input | instrument | responder | bridge | booth (SCHEMA node lifecycle)
        self.console_url: Optional[Any] = None   # set by campbus.console.serve(); published as `ui`
        self.connected = False
        self.connects = 0
        self.recent: Deque[Tuple[float, str, str, dict]] = deque(maxlen=200)   # (ts, "in"|"out", topic, payload)
        self._started = time.monotonic()
        self._interactions = 0
        self._count_dirty = False
        self._last_count_pub = 0.0
        self._last_status = 0.0
        self._subs: List[Tuple[str, Handler]] = []
        self._pending_subs: set = set()      # SUBSCRIBE mids not yet acked
        self._ready = threading.Event()      # connected AND every subscription acked
        self._lock = threading.Lock()

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=node_id, clean_session=True)
        c.will_set(self.status_topic, json.dumps({"online": False, "ts": 0}), qos=0, retain=True)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        if DEFAULT_USER:
            c.username_pw_set(DEFAULT_USER, DEFAULT_PASSWORD)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.on_subscribe = self._on_subscribe
        self._client = c

    # ---- topics --------------------------------------------------------------
    @property
    def status_topic(self) -> str:
        return f"node/{self.node_id}/status"

    @property
    def count_topic(self) -> str:
        return f"node/{self.node_id}/count"

    # ---- lifecycle -----------------------------------------------------------
    def connect(self) -> "Node":
        """Start the background network loop. Returns immediately; reconnects forever."""
        self._client.connect_async(self.host, self.port, keepalive=self.keepalive)
        self._client.loop_start()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        return self

    def close(self) -> None:
        try:
            self._client.publish(self.status_topic, json.dumps({"online": False, "ts": now_ms()}),
                                 retain=True).wait_for_publish(timeout=1)
        except Exception:
            pass
        self._client.loop_stop()
        self._client.disconnect()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Block until connected and every subscription is acknowledged by the broker.
        Publish only after this if you need your own subscriptions live first (tests, responders)."""
        return self._ready.wait(timeout)

    def wait(self) -> None:
        """Block the main thread until Ctrl-C, then close cleanly."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    # ---- pub/sub -------------------------------------------------------------
    def publish(self, topic: str, payload: dict, retain: bool = False, qos: int = 0) -> None:
        """Fire-and-forget JSON publish. Never raises; a dead bus breaks nothing."""
        if "ts" not in payload:
            payload = {**payload, "ts": now_ms()}
        self._remember("out", topic, payload)
        try:
            self._client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=qos, retain=retain)
        except Exception:
            pass

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to an MQTT topic filter; handler(topic, payload_dict). Survives reconnects."""
        with self._lock:
            self._subs.append((topic, handler))
        if self.connected:
            self._do_subscribe(topic)

    def on(self, topic: str) -> Callable[[Handler], Handler]:
        """Decorator form of subscribe()."""
        def deco(fn: Handler) -> Handler:
            self.subscribe(topic, fn)
            return fn
        return deco

    def count(self, n: int = 1) -> int:
        """Bump the interaction counter (published, rate-limited to 1/s, retained)."""
        with self._lock:
            self._interactions += n
            self._count_dirty = True
        self._flush_count()
        return self._interactions

    # ---- internals -----------------------------------------------------------
    def _status_payload(self) -> dict:
        p = {"online": True, "ip": _local_ip(), "fw": self.fw,
             "uptime": int(time.monotonic() - self._started), "ts": now_ms()}
        if self.role:
            p["role"] = self.role
        if self.console_url is not None:
            p["ui"] = self.console_url
        return p

    def publish_status(self) -> None:
        """Push the retained status now - after setting `role` or a console url, say."""
        if self.connected:
            self._last_status = time.monotonic()
            self.publish(self.status_topic, self._status_payload(), retain=True)

    @property
    def subscriptions(self) -> List[str]:
        with self._lock:
            return [t for t, _ in self._subs]

    def _remember(self, direction: str, topic: str, payload: dict) -> None:
        """Ring buffer of recent traffic for this node's console. Own heartbeat is noise, so skip it."""
        if topic.startswith(f"node/{self.node_id}/"):
            return
        with self._lock:
            self.recent.append((time.time(), direction, topic, payload))

    def _do_subscribe(self, topic: str) -> None:
        self._ready.clear()
        result, mid = self._client.subscribe(topic, qos=0)
        if result == mqtt.MQTT_ERR_SUCCESS:
            with self._lock:
                self._pending_subs.add(mid)
        self._check_ready()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._lock:
            self._pending_subs.discard(mid)
        self._check_ready()

    def _check_ready(self) -> None:
        with self._lock:
            pending = bool(self._pending_subs)
        if self.connected and not pending:
            self._ready.set()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = True
        self.connects += 1
        self._last_status = time.monotonic()
        client.publish(self.status_topic, json.dumps(self._status_payload()), retain=True)
        with self._lock:
            subs = list(self._subs)
            self._pending_subs.clear()
        for topic, _ in subs:
            self._do_subscribe(topic)
        self._check_ready()
        self._count_dirty = True
        self._flush_count()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        self._ready.clear()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8")) if msg.payload else {}
        except (ValueError, UnicodeDecodeError):
            payload = {"_raw": msg.payload}
        with self._lock:
            subs = list(self._subs)
        if any(mqtt.topic_matches_sub(t, msg.topic) for t, _ in subs):
            self._remember("in", msg.topic, payload if isinstance(payload, dict) else {})
        for topic, handler in subs:
            if mqtt.topic_matches_sub(topic, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception as e:  # a bad handler must not kill the network loop
                    print(f"[campbus] handler for {topic} raised: {e!r}")

    def _flush_count(self) -> None:
        if not self.connected or not self._count_dirty:
            return
        if time.monotonic() - self._last_count_pub < 1.0:
            return  # heartbeat thread will flush it
        self._last_count_pub = time.monotonic()
        self._count_dirty = False
        self.publish(self.count_topic, {"interactions": self._interactions}, retain=True)

    def _heartbeat(self) -> None:
        while True:
            time.sleep(1)
            self._flush_count()
            if self.connected and time.monotonic() - self._last_status >= STATUS_INTERVAL_S:
                self._last_status = time.monotonic()
                self.publish(self.status_topic, self._status_payload(), retain=True)


__all__ = ["Node", "now_ms", "DEFAULT_HOST", "DEFAULT_PORT"]
