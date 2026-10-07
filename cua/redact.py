"""Redaction applied to everything that is logged, saved, or sent to the model."""

from __future__ import annotations

import re
from typing import Any

# Shapes that are sensitive wherever they appear, declared or not.
PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{9,}\b"), "[NUMBER]"),
]


class Redactor:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}  # raw value -> replacement token

    def add(self, value: str, token: str) -> None:
        if value and len(value) >= 3:  # shorter values would shred unrelated text
            self.values[value] = token

    def text(self, s: str) -> str:
        for value in sorted(self.values, key=len, reverse=True):
            s = s.replace(value, self.values[value])
        for pattern, token in PATTERNS:
            s = pattern.sub(token, s)
        return s

    def deep(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self.deep(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.deep(v) for v in obj]
        return obj
