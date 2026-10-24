"""Lightweight JSON-file-backed storage for past comparisons.

Each synthesized battlecard is written as its own `<id>.json` file under
`HISTORY_DIR`, keeping this dependency-free (no SQLite/ORM) for a
low-volume, single-instance tool. Tests monkeypatch `HISTORY_DIR` to a
temp directory so the real suite never touches this module's default path.

`HISTORY_DIR` defaults to `<repo>/data/history` but can be overridden via the
`HISTORY_DIR` environment variable — set this when running an ad hoc/throwaway
server (manual verification, a script, CI) alongside an already-running dev
server, so the two don't share and clobber the same on-disk history.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.schemas import (
    BattlecardResponse,
    HistoryRecord,
    HistoryStack,
    HistorySummary,
)


def _resolve_history_dir(base_dir: Path, env_override: Optional[str]) -> Path:
    return Path(env_override) if env_override else base_dir / "data" / "history"


BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_DIR = _resolve_history_dir(BASE_DIR, os.environ.get("HISTORY_DIR"))

MAX_HISTORY_ENTRIES = 50

logger = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def save_comparison(
    battlecard: BattlecardResponse, input_fingerprint: Optional[str] = None
) -> HistoryRecord:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    model_names = battlecard.comparison_matrix.get("model_name", {})
    record = HistoryRecord(
        id=uuid.uuid4().hex,
        created_at=datetime.now(timezone.utc),
        device_a_name=model_names.get("device_a", "Device A"),
        device_b_name=model_names.get("device_b", "Device B"),
        battlecard=battlecard,
        input_fingerprint=input_fingerprint,
    )
    _write_atomic(HISTORY_DIR / f"{record.id}.json", record.model_dump_json())
    _prune_oldest()
    return record


def _write_atomic(path: Path, content: str) -> None:
    """Write via a temp file + rename so a crash never leaves a truncated record."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
            summaries.append(
                HistorySummary(
                    id=data["id"],
                    created_at=data["created_at"],
                    device_a_name=data["device_a_name"],
                    device_b_name=data["device_b_name"],
                    installation_friction_score_device_a=data["battlecard"].get(
                        "installation_friction_score_device_a"
                    ),
                    installation_friction_score_device_b=data["battlecard"].get(
                        "installation_friction_score_device_b"
                    ),
                    perspective=data["battlecard"].get("perspective"),
                    input_fingerprint=data.get("input_fingerprint"),
                )
            )
        except (ValueError, KeyError, TypeError, OSError):
            logger.warning("Skipping unreadable history record %s", path.name)
    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return summaries


def _filtered_summaries(query: Optional[str]) -> List[HistorySummary]:
    summaries = _load_all_summaries()
    if not query:
        return summaries
    needle = query.lower()
    return [
        s
        for s in summaries
        if needle in s.device_a_name.lower() or needle in s.device_b_name.lower()
    ]


def list_comparisons(
    limit: Optional[int] = None, offset: int = 0, query: Optional[str] = None
) -> List[HistorySummary]:
    summaries = _filtered_summaries(query)
    if limit is None:
        return summaries[offset:]
    return summaries[offset : offset + limit]


def count_comparisons(query: Optional[str] = None) -> int:
    return len(_filtered_summaries(query))


def _stack_key(summary: HistorySummary) -> str:
    """Same device pair (in order: A vs B differs from B vs A) and same mode."""
    perspective = summary.perspective or "neutral"
    return "|".join(
        (
            summary.device_a_name.strip().casefold(),
            summary.device_b_name.strip().casefold(),
            perspective,
        )
    )


def _build_stacks(summaries: List[HistorySummary]) -> List[HistoryStack]:
    """Group newest-first summaries into stacks, ordered by their newest entry."""
    groups: dict[str, List[HistorySummary]] = {}
    for summary in summaries:
        groups.setdefault(_stack_key(summary), []).append(summary)
    stacks = []
    for key, members in groups.items():
        seen: set[str] = set()
        redundant = []
        for member in members:
            fingerprint = member.input_fingerprint
            if fingerprint is None:
                continue
            if fingerprint in seen:
                redundant.append(member.id)
            seen.add(fingerprint)
        stacks.append(
            HistoryStack(
                key=key,
                latest=members[0],
                others=members[1:],
                redundant_ids=redundant,
            )
        )
    return stacks


def list_stacks(
    limit: Optional[int] = None, offset: int = 0, query: Optional[str] = None
) -> tuple[List[HistoryStack], int, int]:
    """Return (page of stacks, total stacks, total records) matching `query`."""
    summaries = _filtered_summaries(query)
    stacks = _build_stacks(summaries)
    page = stacks[offset:] if limit is None else stacks[offset : offset + limit]
    return page, len(stacks), len(summaries)


def delete_older_copies(history_id: str) -> Optional[int]:
    """Delete the redundant copies in `history_id`'s stack: older records made
    from the same documents as a newer one. Returns how many were deleted, or
    None if the record doesn't exist."""
    summaries = _load_all_summaries()
    target = next((s for s in summaries if s.id == history_id), None)
    if target is None:
        return None
    key = _stack_key(target)
    stack = next(s for s in _build_stacks(summaries) if s.key == key)
    deleted = 0
    for record_id in stack.redundant_ids:
        if delete_comparison(record_id):
            deleted += 1
    return deleted


def get_comparison(history_id: str) -> Optional[HistoryRecord]:
    if not _ID_PATTERN.match(history_id):
        return None
    path = HISTORY_DIR / f"{history_id}.json"
    if not path.exists():
        return None
    try:
        return HistoryRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        logger.warning("Unreadable history record %s", path.name)
        return None


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
