from __future__ import annotations

import json
from pathlib import Path

from ecommerce_agent.trace.schema import TraceRecord


def _append_line(obj: dict, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, default=str) + "\n")


def dump_trace(record: TraceRecord, path: str) -> None:
    """Append one trace record as a JSON line."""
    _append_line(record.to_dict(), path)


def append_eval_baseline(entry: dict, path: str) -> None:
    """Append one eval-batch baseline record as a JSON line."""
    _append_line(entry, path)
