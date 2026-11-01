"""Persistência simples do histórico da conversa em JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_HISTORY = (
    Path(__file__).resolve().parent.parent / ".history" / "conversation.json"
)


def load_history(path: Path = DEFAULT_HISTORY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_history(messages: list[dict[str, Any]], path: Path = DEFAULT_HISTORY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
