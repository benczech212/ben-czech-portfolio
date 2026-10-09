"""Test fixtures: a broker (in-process amqtt unless CAMPBUS_HOST points at a real one),
the mapping engine on the default mappings.yaml, the mock devices, and a recorder."""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

import uuid

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in ("clients/python", "booth", "sim"):
    sys.path.insert(0, os.path.join(ROOT, p))

from campbus import Node  # noqa: E402

SUFFIX = "_t" + uuid.uuid4().hex[:6]   # unique client ids per test run, so a live sim on the same broker is never kicked


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def broker():
    """(host, port). Uses $CAMPBUS_HOST/$CAMPBUS_PORT if set (e.g. the Docker mosquitto),
    otherwise starts a pure-Python amqtt broker in a background thread."""
    host = os.environ.get("CAMPBUS_HOST")
    if host:
        yield host, int(os.environ.get("CAMPBUS_PORT", "1883"))
        return
    amqtt = pytest.importorskip("amqtt.broker")
    port = _free_port()
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
        "topic-check": {"enabled": False},
    }
    loop = asyncio.new_event_loop()
    brk = amqtt.Broker(config, loop=loop)
    ready = threading.Event()

    def run():
        loop.run_until_complete(brk.start())
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(10), "amqtt broker did not start"
    yield "127.0.0.1", port
    try:
        fut = asyncio.run_coroutine_threadsafe(brk.shutdown(), loop)
        fut.result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)


def wait_connected(node: Node, timeout: float = 5.0) -> None:
    assert node.wait_ready(timeout), f"node {node.node_id} never became ready (connected + subscriptions acked)"


@pytest.fixture
def engine(broker):
    from mapping_engine import MappingEngine
    host, port = broker
    eng = MappingEngine(host=host, port=port, node_id="mapping_engine" + SUFFIX, poll_s=0.2,
                        verbose=False).start()
    wait_connected(eng.node)
    yield eng
    eng.stop()


@pytest.fixture
def devices(broker):
    from devices import build_devices, start_all, stop_all
    host, port = broker
    devs = start_all(build_devices(host, port, quiet=True, suffix=SUFFIX))
    for d in devs.values():
        wait_connected(d.node)
    yield devs
    stop_all(devs)


@pytest.fixture
def recorder(broker):
    from inputs import Recorder
    host, port = broker
    node = Node("test_recorder" + SUFFIX, host=host, port=port, fw="test")
    rec = Recorder(node)
    node.connect()
    wait_connected(node)
    time.sleep(0.2)   # let retained state land before tests start timing
    yield node, rec
    node.close()
