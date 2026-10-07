"""Per-run evidence: a structured, redacted event log plus failure artefacts."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redact import Redactor


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RunLog:
    def __init__(self, kind: str, name: str, redactor: Redactor, root: str | Path = "runs") -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.run_id = f"{stamp}-{kind}-{secrets.token_hex(3)}"
        self.dir = Path(root) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor
        self.events: list[dict] = []
        self.event("run_started", kind=kind, name=name)

    def event(self, type: str, **data: Any) -> None:
        self.events.append({"seq": len(self.events) + 1, "ts": now(), "type": type, **data})
        self._flush()

    def _flush(self) -> None:
        # Rewritten whole each time so a value learned to be sensitive later
        # (an extracted balance, say) is also scrubbed from earlier events.
        lines = (json.dumps(self.redactor.deep(e), default=str) for e in self.events)
        (self.dir / "events.jsonl").write_text("\n".join(lines) + "\n")

    def write_text(self, name: str, text: str) -> str:
        path = self.dir / name
        path.write_text(self.redactor.text(text))
        return str(path)

    def write_json(self, name: str, data: Any) -> str:
        path = self.dir / name
        path.write_text(json.dumps(self.redactor.deep(data), indent=2, default=str) + "\n")
        return str(path)
