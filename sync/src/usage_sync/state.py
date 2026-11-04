"""Per-bucket sync cursors, persisted as JSON with atomic replace."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class State:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cursors: dict[str, datetime] = self._load()

    def _load(self) -> dict[str, datetime]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: _aware(datetime.fromisoformat(v)) for k, v in raw.items()}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            log.warning("state file %s is corrupt (%s); starting empty", self.path, exc)
            return {}

    def cursor(self, bucket_id: str) -> datetime | None:
        return self._cursors.get(bucket_id)

    def cursors(self) -> dict[str, datetime]:
        return dict(self._cursors)

    def advance(self, bucket_id: str, ts: datetime) -> None:
        ts = _aware(ts)
        current = self._cursors.get(bucket_id)
        if current is not None and ts <= current:
            return
        self._cursors = {**self._cursors, bucket_id: ts}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({k: v.isoformat() for k, v in self._cursors.items()})
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
