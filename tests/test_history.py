"""Unit tests for the comparison-history storage service.

tests/conftest.py's autouse fixture redirects history.HISTORY_DIR to a
per-test tmp_path, so these never touch the real repo's data/ directory.
"""

import json
from pathlib import Path

from app.schemas import BattlecardResponse
from app.services import history


def _make_battlecard(name_a="Device A", name_b="Device B") -> BattlecardResponse:
    return BattlecardResponse(
        comparison_matrix={
            "model_name": {"device_a": name_a, "device_b": name_b},
            "default_ip": {"device_a": "192.168.1.1", "device_b": "192.168.1.1"},
            "is_private_ip": {"device_a": True, "device_b": True},
            "provisioning_method": {"device_a": "TR-069", "device_b": "Console"},
            "required_tools": {"device_a": [], "device_b": []},
            "ports": {"device_a": [], "device_b": []},
            "led_states": {"device_a": [], "device_b": []},
        },
        installation_friction_score_device_a=1,
        installation_friction_score_device_b=9,
        key_risks_device_a=[],
        key_risks_device_b=[],
        key_advantages_device_a=[],
        key_advantages_device_b=[],
        sales_talk_track="Talk track.",
    )


def test_save_and_get_roundtrip():
    battlecard = _make_battlecard()
    record = history.save_comparison(battlecard)

    fetched = history.get_comparison(record.id)
    assert fetched is not None
    assert fetched.battlecard == battlecard


def test_prunes_oldest_entries_beyond_max():
    total = history.MAX_HISTORY_ENTRIES + 5
    for i in range(total):
        history.save_comparison(_make_battlecard(name_a=f"Device {i}"))

    summaries = history.list_comparisons()
    assert len(summaries) == history.MAX_HISTORY_ENTRIES

    remaining_names = {s.device_a_name for s in summaries}
    for i in range(5):
        assert f"Device {i}" not in remaining_names
    for i in range(5, total):
        assert f"Device {i}" in remaining_names


class TestResolveHistoryDir:
    def test_uses_env_override_when_set(self):
        assert history._resolve_history_dir(
            Path("/repo"), "/tmp/qsg-scratch-history"
        ) == Path("/tmp/qsg-scratch-history")

    def test_falls_back_to_data_dir_when_unset(self):
        assert history._resolve_history_dir(Path("/repo/data"), None) == Path(
            "/repo/data/history"
        )

    def test_falls_back_to_data_dir_when_empty_string(self):
        assert history._resolve_history_dir(Path("/repo/data"), "") == Path(
            "/repo/data/history"
        )


def test_save_leaves_no_temp_files():
    history.save_comparison(_make_battlecard())
    assert [p.suffix for p in history.HISTORY_DIR.iterdir()] == [".json"]


def test_failed_write_keeps_existing_record_and_cleans_temp(monkeypatch):
    record = history.save_comparison(_make_battlecard())
    path = history.HISTORY_DIR / f"{record.id}.json"
    original = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(history.os, "replace", boom)
    try:
        history._write_atomic(path, "partial")
    except OSError:
        pass
    assert path.read_text(encoding="utf-8") == original
    assert not list(history.HISTORY_DIR.glob("*.tmp"))


def test_corrupt_and_incomplete_records_are_skipped_and_not_counted():
    good = history.save_comparison(_make_battlecard())
    (history.HISTORY_DIR / ("a" * 32 + ".json")).write_text('{"id": "x"')
    (history.HISTORY_DIR / ("b" * 32 + ".json")).write_text('{"id": "b"}')

    assert [s.id for s in history.list_comparisons()] == [good.id]
    assert history.count_comparisons() == 1
    assert history.get_comparison("a" * 32) is None
    assert history.get_comparison("b" * 32) is None


def test_records_saved_before_perspectives_still_load():
    record = history.save_comparison(_make_battlecard())
    path = history.HISTORY_DIR / f"{record.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["battlecard"].pop("perspective", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    assert history.get_comparison(record.id).battlecard.perspective is None
    assert history.list_comparisons()[0].perspective is None


class TestStacks:
    def test_same_pair_and_mode_stack_newest_first(self):
        old = history.save_comparison(_make_battlecard(), input_fingerprint="f1")
        other = history.save_comparison(_make_battlecard("X", "Y"))
        new = history.save_comparison(_make_battlecard(), input_fingerprint="f2")

        stacks, total, record_total = history.list_stacks()
        assert (total, record_total) == (2, 3)
        assert stacks[0].latest.id == new.id
        assert [s.id for s in stacks[0].others] == [old.id]
        assert stacks[1].latest.id == other.id and stacks[1].others == []

    def test_names_match_case_insensitively_but_order_and_mode_matter(self):
        history.save_comparison(_make_battlecard("Router", "Modem"))
        history.save_comparison(_make_battlecard("router ", "MODEM"))
        history.save_comparison(_make_battlecard("Modem", "Router"))
        sales = _make_battlecard("Router", "Modem")
        sales.perspective = "sales"
        history.save_comparison(sales)

        stacks, total, _ = history.list_stacks()
        assert total == 3
        assert sorted(len(s.others) for s in stacks) == [0, 0, 1]

    def test_only_older_exact_copies_are_redundant(self):
        oldest = history.save_comparison(_make_battlecard(), input_fingerprint="same")
        history.save_comparison(_make_battlecard(), input_fingerprint="different")
        history.save_comparison(_make_battlecard())  # legacy: no fingerprint
        history.save_comparison(_make_battlecard())
        history.save_comparison(_make_battlecard(), input_fingerprint="same")

        stack = history.list_stacks()[0][0]
        assert stack.redundant_ids == [oldest.id]

    def test_pages_by_stack(self):
        for i in range(3):
            history.save_comparison(_make_battlecard(name_a=f"Device {i}"))
            history.save_comparison(_make_battlecard(name_a=f"Device {i}"))

        page, total, record_total = history.list_stacks(limit=2, offset=2)
        assert (total, record_total) == (3, 6)
        assert [s.latest.device_a_name for s in page] == ["Device 0"]
        assert len(page[0].others) == 1

    def test_search_filters_records_before_stacking(self):
        history.save_comparison(_make_battlecard("Alpha", "Beta"))
        history.save_comparison(_make_battlecard("Gamma", "Delta"))

        page, total, _ = history.list_stacks(query="gam")
        assert total == 1 and page[0].latest.device_a_name == "Gamma"

    def test_delete_older_copies_keeps_newest_and_different_documents(self):
        history.save_comparison(_make_battlecard(), input_fingerprint="same")
        different = history.save_comparison(
            _make_battlecard(), input_fingerprint="different"
        )
        newest = history.save_comparison(_make_battlecard(), input_fingerprint="same")
        unrelated = history.save_comparison(
            _make_battlecard("X", "Y"), input_fingerprint="same"
        )

        assert history.delete_older_copies(newest.id) == 1
        remaining = {s.id for s in history.list_comparisons()}
        assert remaining == {different.id, newest.id, unrelated.id}

    def test_delete_older_copies_of_unknown_record(self):
        assert history.delete_older_copies("c" * 32) is None


class TestSources:
    def _sources_dir(self):
        return history.HISTORY_DIR / "sources"

    def _age_sources(self):
        # Texts younger than the grace period are never treated as orphans.
        import os
        import time

        old = time.time() - history._ORPHAN_GRACE_SECONDS - 1
        for path in self._sources_dir().glob("*.txt"):
            os.utime(path, (old, old))

    def test_sources_round_trip(self):
        record = history.save_comparison(
            _make_battlecard(), sources=[("a.pdf", "text A"), ("b.md", "text B")]
        )
        loaded = history.load_sources(history.get_comparison(record.id))
        assert loaded == [("a.pdf", "text A"), ("b.md", "text B")]

    def test_identical_texts_are_stored_once(self):
        for _ in range(3):
            history.save_comparison(
                _make_battlecard(), sources=[("a", "same"), ("b", "same")]
            )
        assert len(list(self._sources_dir().glob("*.txt"))) == 1

    def test_records_without_sources_cannot_be_reloaded(self):
        record = history.save_comparison(_make_battlecard())
        assert history.load_sources(record) is None

    def test_missing_or_tampered_source_returns_none(self):
        record = history.save_comparison(
            _make_battlecard(), sources=[("a", "text A"), ("b", "text B")]
        )
        record.sources[0].text_sha256 = "../" + "0" * 61
        assert history.load_sources(record) is None

    def test_delete_removes_texts_no_record_uses(self):
        shared = history.save_comparison(
            _make_battlecard(), sources=[("a", "shared"), ("b", "only first")]
        )
        history.save_comparison(
            _make_battlecard(), sources=[("a", "shared"), ("b", "only second")]
        )
        self._age_sources()

        history.delete_comparison(shared.id)
        assert len(list(self._sources_dir().glob("*.txt"))) == 2

    def test_deleting_a_new_record_removes_its_texts_at_once(self):
        record = history.save_comparison(
            _make_battlecard(), sources=[("a", "fresh"), ("b", "fresh too")]
        )
        history.delete_comparison(record.id)
        assert list(self._sources_dir().glob("*.txt")) == []

    def test_recent_unrelated_texts_survive_cleanup(self):
        # e.g. a text another process stored but hasn't written a record for yet
        record = history.save_comparison(
            _make_battlecard(), sources=[("a", "mine"), ("b", "mine too")]
        )
        in_flight = history._store_source("someone else's")
        history.delete_comparison(record.id)
        assert [p.stem for p in self._sources_dir().glob("*.txt")] == [in_flight]

    def test_pruning_removes_orphaned_texts(self, monkeypatch):
        monkeypatch.setattr(history, "MAX_HISTORY_ENTRIES", 1)
        history.save_comparison(_make_battlecard(), sources=[("a", "1"), ("b", "2")])
        self._age_sources()
        history.save_comparison(_make_battlecard(), sources=[("a", "3"), ("b", "4")])
        self._age_sources()
        history.save_comparison(_make_battlecard(), sources=[("a", "5"), ("b", "6")])
        assert len(list(self._sources_dir().glob("*.txt"))) == 2

    def test_clear_history_removes_all_texts(self):
        history.save_comparison(_make_battlecard(), sources=[("a", "x"), ("b", "y")])
        history.clear_history()
        assert not self._sources_dir().exists()


def test_forget_all_sources_with_no_history():
    assert history.forget_all_sources() == 0


def test_delete_after_a_concurrent_delete_reports_not_found(monkeypatch):
    record = history.save_comparison(_make_battlecard())
    path = history.HISTORY_DIR / f"{record.id}.json"
    real_source_hashes = history._source_hashes

    def delete_meanwhile(history_id):
        hashes = real_source_hashes(history_id)
        path.unlink()  # the other request wins the race
        return hashes

    monkeypatch.setattr(history, "_source_hashes", delete_meanwhile)
    assert history.delete_comparison(record.id) is False
