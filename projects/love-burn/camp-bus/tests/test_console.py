"""The per-device web console (campbus.console). No broker and no browser needed."""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))
from campbus import Node, console  # noqa: E402


def _get(httpd, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}{path}", timeout=5) as r:
        return r.status, r.read().decode()


def _post(httpd, path):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}{path}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_console_serves_its_node_and_advertises_itself():
    node = Node("crystal_test", fw="crystal-0.1", role="responder")
    node.subscribe("cmd/crystals/#", lambda t, m: None)
    httpd = console.serve(node, port=0)
    try:
        # the node now tells the bus where its console is; a bare port means "me, this port"
        assert node.console_url == httpd.server_port
        assert node._status_payload()["ui"] == httpd.server_port
        assert node._status_payload()["role"] == "responder"

        code, page = _get(httpd, "/")
        assert code == 200 and "crystal_test" in page
        assert "http" not in page.split("<style>")[0].replace("http-equiv", ""), "no CDN: camp has no internet"

        code, body = _get(httpd, "/api/state")
        s = json.loads(body)
        assert code == 200
        assert s["node_id"] == "crystal_test" and s["role"] == "responder"
        assert s["subscriptions"] == ["cmd/crystals/#"]
        assert s["connected"] is False and s["actions"] == []
    finally:
        httpd.shutdown()


def test_console_shows_traffic_and_runs_controls():
    node = Node("bell_test", fw="bell-0.1", role="responder")
    said = []
    httpd = console.serve(node, port=0, actions={"Ring it": lambda: said.append(1) or "rang"})
    try:
        node.publish("kitchen/foodcall", {"meal": "dinner"})
        node._remember("in", "cmd/kitchen/bell", {"soft": False})
        s = json.loads(_get(httpd, "/api/state")[1])
        assert [(d, t) for _, d, t, _ in s["recent"]] == [("out", "kitchen/foodcall"), ("in", "cmd/kitchen/bell")]

        assert _post(httpd, "/api/action?name=Ring%20it") == (200, {"ok": True, "said": "rang"})
        assert said == [1]
        code, body = _post(httpd, "/api/action?name=Nope")
        assert code == 404 and body["ok"] is False
    finally:
        httpd.shutdown()


def test_a_broken_control_reports_instead_of_taking_the_console_down():
    node = Node("wobbly_test", fw="x")

    def boom():
        raise RuntimeError("the LED driver is not answering")

    httpd = console.serve(node, port=0, actions={"Self-test": boom})
    try:
        code, body = _post(httpd, "/api/action?name=Self-test")
        assert code == 500 and "LED driver" in body["error"]
        assert _get(httpd, "/api/state")[0] == 200          # still serving
    finally:
        httpd.shutdown()


def test_ui_host_env_makes_a_node_advertise_a_reachable_url(monkeypatch):
    """A node in a container cannot be reached at its own IP, so $CAMPBUS_UI_HOST overrides it."""
    monkeypatch.setenv("CAMPBUS_UI_HOST", "localhost")
    node = Node("boxed_test", fw="x")
    httpd = console.serve(node, port=0)
    try:
        assert node.console_url == f"http://localhost:{httpd.server_port}/"
    finally:
        httpd.shutdown()
