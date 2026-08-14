from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from msb_v3.node.filesystem import CapabilityViolation, FileWriter


def test_new_file_write_and_rollback(tmp_path: Path) -> None:
    writer = FileWriter(tmp_path / "sandbox", max_bytes=100)
    receipt = writer.write("new.txt", b"created")
    assert receipt.existed is False
    assert receipt.after_sha256 == hashlib.sha256(b"created").hexdigest()
    assert (tmp_path / "sandbox" / "new.txt").read_bytes() == b"created"

    rollback = writer.rollback(receipt)
    assert rollback["ok"] is True
    assert not (tmp_path / "sandbox" / "new.txt").exists()


def test_existing_file_write_requires_precondition_and_rolls_back(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    target = root / "existing.txt"
    target.write_bytes(b"before")
    writer = FileWriter(root, max_bytes=100)
    before_hash = hashlib.sha256(b"before").hexdigest()

    with pytest.raises(CapabilityViolation, match="precondition"):
        writer.write("existing.txt", b"after", expected_sha256="wrong")
    receipt = writer.write("existing.txt", b"after", expected_sha256=before_hash)
    assert target.read_bytes() == b"after"
    assert writer.rollback(receipt)["ok"] is True
    assert target.read_bytes() == b"before"


def test_writer_rejects_scope_escape_symlink_and_size(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    writer = FileWriter(root, max_bytes=3)

    with pytest.raises(CapabilityViolation, match="outside"):
        writer.write("../outside.txt", b"x")
    with pytest.raises(CapabilityViolation, match="exceeds"):
        writer.write("large.txt", b"1234")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    with pytest.raises(CapabilityViolation, match="symlink"):
        writer.write("link.txt", b"x")
