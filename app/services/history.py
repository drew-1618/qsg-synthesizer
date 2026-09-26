"""Lightweight JSON-file-backed storage for past comparisons.

Each synthesized battlecard is written as its own `<id>.json` file under
`HISTORY_DIR`, keeping this dependency-free (no SQLite/ORM) for a
low-volume, single-instance tool. Tests monkeypatch `HISTORY_DIR` to a
temp directory so the real suite never touches this module's default path.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.schemas import BattlecardResponse, HistoryRecord, HistorySummary

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_DIR = BASE_DIR / "data" / "history"

MAX_HISTORY_ENTRIES = 50

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def save_comparison(battlecard: BattlecardResponse) -> HistoryRecord:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    model_names = battlecard.comparison_matrix.get("model_name", {})
    record = HistoryRecord(
        id=uuid.uuid4().hex,
        created_at=datetime.now(timezone.utc),
        device_a_name=model_names.get("device_a", "Device A"),
        device_b_name=model_names.get("device_b", "Device B"),
        battlecard=battlecard,
    )
    (HISTORY_DIR / f"{record.id}.json").write_text(
        record.model_dump_json(), encoding="utf-8"
    )
    _prune_oldest()
    return record


def _prune_oldest() -> None:
    """Keep at most MAX_HISTORY_ENTRIES records, dropping the oldest first."""
    stale = _load_all_summaries()[MAX_HISTORY_ENTRIES:]
    for summary in stale:
        (HISTORY_DIR / f"{summary.id}.json").unlink(missing_ok=True)


def _load_all_summaries() -> List[HistorySummary]:
    if not HISTORY_DIR.exists():
        return []
    summaries = []
    for path in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summaries.append(
            HistorySummary(
                id=data["id"],
                created_at=data["created_at"],
                device_a_name=data["device_a_name"],
                device_b_name=data["device_b_name"],
                installation_friction_score_device_a=data["battlecard"][
                    "installation_friction_score_device_a"
                ],
                installation_friction_score_device_b=data["battlecard"][
                    "installation_friction_score_device_b"
                ],
            )
        )
    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return summaries


def list_comparisons(
    limit: Optional[int] = None, offset: int = 0
) -> List[HistorySummary]:
    summaries = _load_all_summaries()
    if limit is None:
        return summaries[offset:]
    return summaries[offset : offset + limit]


def count_comparisons() -> int:
    if not HISTORY_DIR.exists():
        return 0
    return sum(1 for _ in HISTORY_DIR.glob("*.json"))


def get_comparison(history_id: str) -> Optional[HistoryRecord]:
    if not _ID_PATTERN.match(history_id):
        return None
    path = HISTORY_DIR / f"{history_id}.json"
    if not path.exists():
        return None
    return HistoryRecord.model_validate_json(path.read_text(encoding="utf-8"))


def delete_comparison(history_id: str) -> bool:
    if not _ID_PATTERN.match(history_id):
        return False
    path = HISTORY_DIR / f"{history_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_history() -> int:
    if not HISTORY_DIR.exists():
        return 0
    deleted = 0
    for path in HISTORY_DIR.glob("*.json"):
        path.unlink()
        deleted += 1
    return deleted
