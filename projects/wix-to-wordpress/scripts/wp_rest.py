"""Tiny WordPress REST client shared by wp_media.py and wp_build.py (stdlib only).

Auth: application password from .secrets/wordpress.env (WP_URL, WP_APP_USER, WP_APP_PASSWORD).
Secrets are never printed; error bodies are truncated and contain no credentials.

vm_wp() reaches the staging VM over IAP ssh. Its target comes from the environment:
GCP_PROJECT (required), GCP_ACCOUNT (optional; else gcloud's active account),
GCP_ZONE (default us-east1-b), GCP_VM (default cs-wordpress).
"""
from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets" / "wordpress.env"
UA = "WixToWordPressMigration/1.0 (+staging rebuild)"


def load_env(path: Path = SECRETS) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class WP:
    def __init__(self, env: dict[str, str] | None = None):
        env = env or load_env()
        self.site = env["WP_URL"].rstrip("/")
        self.base = self.site + "/wp-json/wp/v2"
        token = base64.b64encode(f'{env["WP_APP_USER"]}:{env["WP_APP_PASSWORD"]}'.encode()).decode()
        self.headers = {"Authorization": "Basic " + token, "User-Agent": UA}

    def request(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None,
                json_body: dict | None = None, timeout: int = 180):
        h = dict(self.headers)
        if headers:
            h.update(headers)
        if json_body is not None:
            data = json.dumps(json_body).encode()
            h["Content-Type"] = "application/json"
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            body = e.read()[:600].decode("utf-8", "replace")
            return e.code, {"error": body}

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict):
        return self.request("POST", path, json_body=body)


def vm_wp(*args: str, timeout: int = 300) -> str:
    """Run a wp-cli command on the staging VM (IAP ssh) and return its combined output.

    Every argument is shell-quoted before it joins the remote command line, so a value taken
    from a content file can't smuggle in remote shell syntax.

    Host keys: gcloud keeps the VM's host key in ~/.ssh/google_compute_known_hosts and checks it on
    every connection after the first. Provision the VM with enable-guest-attributes=TRUE (see
    infra/gcp/provision.sh) so gcloud can fetch and pin the key on the first connection too.
    """
    project = os.environ.get("GCP_PROJECT")
    if not project:
        raise RuntimeError("Set GCP_PROJECT (and optionally GCP_ACCOUNT, GCP_ZONE, GCP_VM) to reach the VM")
    cmd = "cd /opt/wordpress && sudo docker compose run --rm -T cli wp " + " ".join(shlex.quote(a) for a in args)
    env = dict(os.environ, CLOUDSDK_CORE_PROJECT=project)
    if os.environ.get("GCP_ACCOUNT"):
        env["CLOUDSDK_CORE_ACCOUNT"] = os.environ["GCP_ACCOUNT"]
    gcloud = "gcloud.cmd" if os.name == "nt" else "gcloud"
    out = subprocess.run([gcloud, "compute", "ssh", os.environ.get("GCP_VM", "cs-wordpress"),
                          "--zone", os.environ.get("GCP_ZONE", "us-east1-b"),
                          "--tunnel-through-iap", "--command", cmd],
                         capture_output=True, text=True, timeout=timeout, env=env)
    lines = [l for l in (out.stdout + out.stderr).splitlines() if not l.startswith(" Container ")]
    return "\n".join(lines)
