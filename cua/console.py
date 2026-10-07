"""Web console backend: read-only views of capabilities and run evidence, plus the
operator side of the handoff queue. It never drives the application itself; a run is
a separate process, and the two meet only in the runs/_pending directory."""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOTS = [Path("evidence"), Path("runs")]
PENDING = Path("runs/_pending")
DIST = Path("console/dist")
DEMO_CAPABILITY = Path("capabilities/member.read_savings_balance.yaml")


def run_dirs() -> list[Path]:
    return sorted((p.parent for root in ROOTS if root.exists() for p in root.rglob("events.jsonl")),
                  key=lambda d: d.name, reverse=True)


def read_events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines() if line.strip()]


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def summary(run: Path) -> dict:
    events = read_events(run)
    result = read_json(run / "result.json") or {}
    by_type = {e["type"]: e for e in events}
    finished = by_type.get("run_finished", {})
    return {
        "id": run.name,
        "path": run.as_posix(),
        "label": run.parent.name if run.parent not in ROOTS else "",
        "source": run.parts[0],
        "kind": events[0].get("kind", ""),
        "name": events[0].get("name", ""),
        "started": events[0]["ts"],
        "status": result.get("status") or finished.get("status") or "running",
        "side_effects": result.get("side_effects", "none"),
        "error_kind": (result.get("error") or {}).get("kind"),
        "outcome": (result.get("outcome") or {}).get("name"),
        "handoffs": sum(e["type"] == "intervention_requested" for e in events),
        "recoveries": result.get("recoveries", []),
        "model": by_type.get("discovery_started", {}).get("model"),
        "dry_run": by_type.get("replay_started", {}).get("dry_run", False),
    }


def detail(run: Path) -> dict:
    return {
        **summary(run),
        "events": read_events(run),
        "result": read_json(run / "result.json"),
        "files": sorted(p.name for p in run.iterdir() if p.suffix in (".png", ".txt")),
        "interventions": [read_json(p) for p in sorted(run.glob("intervention-*.json"))],
    }


def capabilities() -> list[dict]:
    return [{**yaml.safe_load(p.read_text()), "file": p.as_posix()} for p in sorted(Path("capabilities").glob("*.yaml"))]


def pending() -> list[dict]:
    if not PENDING.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(PENDING.glob("*.json")) if not p.name.endswith(".decision.json")]


def mock_app_url() -> str:
    return yaml.safe_load(Path("tenants/harbor.yaml").read_text())["base_url"]


def mock_app_up() -> bool:
    try:
        urllib.request.urlopen(mock_app_url() + "/login", timeout=1).read()
        return True
    except OSError:
        return False


def start_demo() -> dict:
    """Start a replay that is certain to get stuck, so the handoff can be tried by hand."""
    if not mock_app_up():
        return {"ok": False, "message": "The mock app is not running. Start it with: uv run python -m mockapp.server"}
    if pending():
        return {"ok": False, "message": "A run is already waiting for an operator."}
    urllib.request.urlopen(mock_app_url() + "/__fault?error=1", timeout=2).read()
    subprocess.Popen([sys.executable, "-m", "cua", "replay", str(DEMO_CAPABILITY), "--param", "member_id=10042",
                      "--operator", "web", "--operator-timeout", "240", "--headed"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "message": "Run started. A browser window will open and stop on an error page."}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status: int = 200) -> None:
        self.send(json.dumps(data).encode(), "application/json", status)

    def send_file(self, path: Path, allowed: list[Path]) -> None:
        resolved = path.resolve()
        if not resolved.is_file() or not any(resolved.is_relative_to(root.resolve()) for root in allowed):
            return self.send_json({"error": "not found"}, 404)
        self.send(resolved.read_bytes(), mimetypes.guess_type(resolved.name)[0] or "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/api/status":
            return self.send_json({"mock_app": mock_app_up(), "mock_app_url": mock_app_url()})
        if path == "/api/capabilities":
            return self.send_json(capabilities())
        if path == "/api/runs":
            return self.send_json([summary(r) for r in run_dirs()])
        if path.startswith("/api/runs/"):
            run = next((r for r in run_dirs() if r.name == path.rsplit("/", 1)[1]), None)
            return self.send_json(detail(run)) if run else self.send_json({"error": "not found"}, 404)
        if path == "/api/interventions":
            return self.send_json(pending())
        if path.startswith("/files/"):
            return self.send_file(Path(path.removeprefix("/files/")), ROOTS)
        if path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        asset = DIST / path.lstrip("/")
        if not DIST.exists():
            return self.send(b"The console UI is not built. Run: npm --prefix console install && npm --prefix console run build",
                             "text/plain", 503)
        self.send_file(asset if asset.is_file() else DIST / "index.html", [DIST])

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if path == "/api/demo/handoff":
            return self.send_json(start_demo())
        if path.startswith("/api/interventions/"):
            request = next((p for p in pending() if p["id"] == path.rsplit("/", 1)[1]), None)
            if request is None or body.get("action") not in request["options"]:
                return self.send_json({"error": "no such pending request, or option not offered"}, 400)
            (PENDING / f"{request['id']}.decision.json").write_text(
                json.dumps({"action": body["action"], "by": body.get("by") or "console-operator"}))
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)


def serve(port: int) -> int:
    print(f"cua console on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0
