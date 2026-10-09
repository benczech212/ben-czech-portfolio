"""campbus, small enough for CircuitPython.

The same conventions as `clients/python/campbus`, minus everything a microcontroller
cannot afford (threads, paho, dataclasses). A node on this bus must:

  * use its `node_id` as the MQTT client id
  * hold a **retained** `node/<id>/status` saying it is online, with a **last will**
    on the same topic saying it is not - so a panel that loses power stops claiming
    to be alive the moment the broker notices
  * re-publish that status every 30 s as a heartbeat
  * count interactions on `node/<id>/count`, retained, no faster than 1/s

Getting these right is what makes the panel indistinguishable from the simulated
devices, which is the point of the prototype: the camp cannot tell them apart.

There are no threads here, so `loop()` has to be called often from the main loop.
It never raises: a dead broker leaves the panel showing whatever it was showing,
which is the right failure mode for a light at 2 am.
"""
import json
import time

import adafruit_minimqtt.adafruit_minimqtt as MQTT

STATUS_INTERVAL_S = 30
COUNT_INTERVAL_S = 1.0
RECONNECT_MAX_S = 10
COMPLAIN_EVERY_S = 10       # how often a persistent fault is allowed to say so
SOCKET_TIMEOUT_S = 0.05     # per-recv; big enough for a multi-packet payload
LOOP_TIMEOUT_S = 0.06       # must be >= SOCKET_TIMEOUT_S


class Node:
    def __init__(self, node_id, host, port=1883, fw="matrix-0.1",
                 socket_pool=None, on_message=None, keepalive=30):
        self.node_id = node_id
        self.host, self.port, self.fw = host, port, fw
        self.on_message = on_message
        self.connected = False
        self.ip = "0.0.0.0"

        self._started = time.monotonic()
        self._subs = []
        self._interactions = 0
        self._count_dirty = False
        self._last_count = 0.0
        self._last_status = 0.0
        self._next_try = 0.0
        self._backoff = 1.0
        self._complained_at = -1e9

        # socket_timeout has to be *below* whatever we pass to loop(), and it is the
        # time a single recv gets before the ESP32 raises. Two measured failures set
        # this number:
        #   0.002 - the panel dropped off the broker entirely and sat reconnecting,
        #           which on the bus is indistinguishable from a node that is gone
        #   0.01  - small messages fine, but a 792-byte retained zone map raised
        #           ETIMEDOUT mid-read and took the connection down with it. Short
        #           payloads arrive in one packet and hid this for a long time.
        # The frame cost is paid back by polling less often - see code.py's main loop.
        self._mqtt = MQTT.MQTT(
            broker=host, port=port, client_id=node_id,
            socket_pool=socket_pool, is_ssl=False, keep_alive=keepalive,
            socket_timeout=SOCKET_TIMEOUT_S,
        )
        # Set before connecting, or the broker never learns it.
        self._mqtt.will_set(self.status_topic,
                            json.dumps({"online": False, "ts": 0}), qos=0, retain=True)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message

    # ---- topics --------------------------------------------------------------
    @property
    def status_topic(self):
        return "node/{}/status".format(self.node_id)

    @property
    def count_topic(self):
        return "node/{}/count".format(self.node_id)

    # ---- lifecycle -----------------------------------------------------------
    def subscribe(self, topic):
        """Remembered, so it survives a reconnect the same way the Python client's does."""
        if topic not in self._subs:
            self._subs.append(topic)
        if self.connected:
            try:
                self._mqtt.subscribe(topic, 0)
            except Exception:
                pass

    def _on_connect(self, client, userdata, flags, rc):
        self.connected = True
        self._backoff = 1.0
        for topic in self._subs:
            try:
                client.subscribe(topic, 0)
            except Exception:
                pass
        self._publish_status()

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False

    def _on_message(self, client, topic, message):
        if self.on_message is None:
            return
        try:
            payload = json.loads(message) if message else {}
        except (ValueError, TypeError):
            return                       # malformed JSON is not worth a crash
        if isinstance(payload, dict):
            self.on_message(topic, payload)

    def _publish_status(self):
        self._last_status = time.monotonic()
        self.publish(self.status_topic, {
            "online": True, "ip": self.ip, "fw": self.fw,
            "uptime": int(time.monotonic() - self._started),
        }, retain=True)

    # ---- pub/sub -------------------------------------------------------------
    def publish(self, topic, payload, retain=False):
        """Fire and forget. Never raises."""
        if "ts" not in payload:
            payload = dict(payload)
            payload["ts"] = int(time.time() * 1000)
        try:
            self._mqtt.publish(topic, json.dumps(payload), retain, 0)
        except Exception as exc:
            self._complain("publish " + topic, exc)
            self.connected = False

    def count(self, n=1):
        self._interactions += n
        self._count_dirty = True

    def _flush_count(self, now):
        if not self._count_dirty or now - self._last_count < COUNT_INTERVAL_S:
            return
        self._last_count = now
        self._count_dirty = False
        self.publish(self.count_topic, {"n": self._interactions}, retain=True)

    def _complain(self, where, exc):
        """Say what went wrong, at most once every `COMPLAIN_EVERY_S`.

        Rate-limited rather than capped: a cap means a fault that starts an hour in is
        silent, which is exactly the case where you need to be told. Swallowing these
        entirely cost an hour once - a bad loop() argument looked just like a clean
        disconnect, because the only symptom on the bus was the last will firing.
        """
        now = time.monotonic()
        if now - self._complained_at < COMPLAIN_EVERY_S:
            return
        self._complained_at = now
        print("[bus] %s: %s: %s" % (where, exc.__class__.__name__, exc))

    # ---- the pump ------------------------------------------------------------
    def loop(self, timeout=LOOP_TIMEOUT_S):
        """Call this often. Connects, reconnects, pumps messages, heartbeats."""
        now = time.monotonic()
        if not self.connected:
            if now < self._next_try:
                return False
            try:
                self._mqtt.connect()
            except Exception as exc:
                # Back off, but never give up: the broker may just be booting.
                self._complain("connect", exc)
                self._backoff = min(RECONNECT_MAX_S, self._backoff * 2)
                self._next_try = now + self._backoff
                return False
        try:
            self._mqtt.loop(timeout)
        except Exception as exc:
            self._complain("loop", exc)
            self.connected = False
            self._next_try = now + self._backoff
            return False

        if now - self._last_status >= STATUS_INTERVAL_S:
            self._publish_status()
        self._flush_count(now)
        return True

    def close(self):
        try:
            self.publish(self.status_topic, {"online": False}, retain=True)
            self._mqtt.disconnect()
        except Exception:
            pass
