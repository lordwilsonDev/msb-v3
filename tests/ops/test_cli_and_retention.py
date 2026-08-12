from pathlib import Path

from msb_v3.ops.backup import prune_backups, list_backups


def test_prune_keeps_newest_n(tmp_path: Path):
    for ts in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (tmp_path / ts).mkdir()
    deleted = prune_backups(tmp_path, keep=2)
    remaining = [p.name for p in list_backups(tmp_path)]
    assert remaining == ["20260102T000000Z", "20260103T000000Z"]
    assert [p.name for p in deleted] == ["20260101T000000Z"]
