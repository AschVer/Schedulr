"""Atomic JSON cache for the latest timetable snapshot."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import Timetable


class TimetableStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Timetable | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Timetable.from_dict(raw)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, timetable: Timetable) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            timetable.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        handle, temp_name = tempfile.mkstemp(
            prefix=".timetable-",
            suffix=".json",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
