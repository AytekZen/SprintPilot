"""Tests for EvidenceStore."""
import pytest
from sprintpilot.evidence import EvidenceStore, EvidenceStoreError


def test_write_and_read(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write("data.json", {"key": "value"})
    result = store.read("data.json")
    assert result == {"key": "value"}


def test_atomic_write_no_partial(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write("data.json", {"x": 1})
    tmp_files = list(store.sprint_dir.glob("*.tmp"))
    assert tmp_files == [], f"Temp files remain: {tmp_files}"


def test_read_nonexistent_returns_none(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    assert store.read("missing.json") is None


def test_write_text_and_read_text(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write_text("patch.diff", "--- a\n+++ b\n")
    content = store.read_text("patch.diff")
    assert content == "--- a\n+++ b\n"


def test_read_text_nonexistent(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    assert store.read_text("nope.txt") is None


def test_list_files(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write("a.json", {})
    store.write("b.json", {})
    store.write_text("c.txt", "hello")
    files = store.list_files()
    assert sorted(files) == ["a.json", "b.json", "c.txt"]


def test_exists(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    assert not store.exists("x.json")
    store.write("x.json", {})
    assert store.exists("x.json")


def test_overwrite_is_atomic(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write("data.json", {"v": 1})
    store.write("data.json", {"v": 2})
    assert store.read("data.json") == {"v": 2}


def test_nested_sprint_dir_created(tmp_path):
    base = tmp_path / "deep" / "nested"
    store = EvidenceStore(base, "my-sprint")
    assert store.sprint_dir.exists()


def test_write_list(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    store.write("list.json", [1, 2, 3])
    assert store.read("list.json") == [1, 2, 3]


def test_sprint_dir_is_resolved(tmp_path):
    store = EvidenceStore(tmp_path, "s1")
    assert store.sprint_dir.is_absolute()
