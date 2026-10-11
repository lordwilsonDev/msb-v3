"""Off-box append-only notary (security-hardening #2).

The remote sink is exercised two ways: a ``LocalDirSink`` on a separate
directory (same code path, deterministic) and a REAL ``RcloneSink`` against a
fake ``rclone`` executable — so the actual arg parsing / output parsing of the
rclone integration is covered, not just an abstraction. Divergence semantics
are the core guarantee: verify reads the REMOTE head, never the local last
line; a rolled-back local log is DIVERGED, a missing remote entry is
REMOTE_BEHIND, an unreachable remote is fail-closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.chain_anchor import ChainAnchor, generate_seed
from msb_v3.uac.notary import (
    LocalDirSink,
    NotaryDiverged,
    NotaryRemoteError,
    NotaryService,
    RcloneSink,
)
from msb_v3.uac.timestamping import LocalReceiveTimestamper

FAKE_RCLONE = """#!/usr/bin/env bash
# Fake rclone: maps <remote>:<path> onto $FAKE_RCLONE_ROOT/<path>.
set -u
FAKE_ROOT="${FAKE_RCLONE_ROOT:?}"
cmd="$1"; shift
localize() { printf '%s' "$1" | sed "s|^[^:]*:||"; }
case "$cmd" in
  copyto)
    src="$1"; dst="$(localize "$2")"
    mkdir -p "$(dirname "$FAKE_ROOT/$dst")"
    cp "$src" "$FAKE_ROOT/$dst" ;;
  cat)
    f="$(localize "$1")"
    if [ -f "$FAKE_ROOT/$f" ]; then cat "$FAKE_ROOT/$f"; else exit 3; fi ;;
  lsf)
    d="$(localize "$1")"
    if [ -d "$FAKE_ROOT/$d" ]; then
      for f in "$FAKE_ROOT/$d"/*; do [ -e "$f" ] && basename "$f"; done | sort
    fi ;;
  lsl)
    f="$(localize "$1")"
    if [ -f "$FAKE_ROOT/$f" ]; then
      # GNU vs BSD stat: both emit '<size> <modtime...> <name>' so the sink's
      # whitespace parse works on macOS (BSD) and Linux CI (GNU).
      if stat -c %y /dev/null >/dev/null 2>&1; then
        stat -c '%s %y %n' "$FAKE_ROOT/$f"
      else
        stat -f '%z %Sm %N' -t '%Y-%m-%d %H:%M:%S %z' "$FAKE_ROOT/$f"
      fi
    else exit 3; fi ;;
  *) echo "unknown rclone cmd: $cmd" >&2; exit 2 ;;
esac
"""


@pytest.fixture
def chain(tmp_path: Path) -> AuditChain:
    c = AuditChain(str(tmp_path / "audit.db"))
    c.append("s", "e", {"n": 1})
    return c


@pytest.fixture
def anchor() -> ChainAnchor:
    return ChainAnchor(seed=generate_seed())


@pytest.fixture
def service_factory(chain: AuditChain, anchor: ChainAnchor, tmp_path: Path):
    def make(remote_dir: Path | None = None, timestamper=None, local_log: Path | None = None) -> NotaryService:
        remote = LocalDirSink(remote_dir) if remote_dir is not None else None
        return NotaryService(
            chain=chain,
            anchor=anchor,
            local_log=local_log or (tmp_path / "notary.jsonl"),
            remote=remote,
            timestamper=timestamper,
        )

    return make


@pytest.fixture
def fake_rclone(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    script = tmp_path / "fake-rclone"
    script.write_text(FAKE_RCLONE)
    script.chmod(0o755)
    root = tmp_path / "remote-root"
    root.mkdir()
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(root))
    return script, root


def test_notarize_appends_local_and_remote(service_factory, tmp_path: Path):
    remote_dir = tmp_path / "remote"
    service = service_factory(remote_dir=remote_dir, timestamper=LocalReceiveTimestamper())
    result = service.notarize()

    assert result["ok"] is True
    assert result["remote_push_ok"] is True
    assert result["seq"] == 1

    # Local JSONL holds one line (the existing --verify-notary format).
    lines = [line for line in (tmp_path / "notary.jsonl").read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["anchor"]["snapshot"]["tip_hash"] == result["tip_hash"]
    assert entry["timestamp"]["source"] == "receive_time"

    # Remote holds one per-entry object with the same content.
    remote_entries = [p.name for p in remote_dir.iterdir()]
    assert len(remote_entries) == 1
    assert (remote_dir / remote_entries[0]).read_text().strip() == lines[0]


def test_verify_healthy(service_factory, tmp_path: Path):
    service = service_factory(remote_dir=tmp_path / "remote")
    service.notarize()
    service.notarize()

    report = service.verify()
    assert report["verdict"] == "HEALTHY"
    assert report["local_entries"] == 2
    assert report["remote_entries"] == 2
    assert report["remote_head_seq"] == 2
    assert report["head"]["valid"] is True
    assert report["timestamp"] is None  # no timestamper configured


def test_notarize_backfills_missing_remote_entries(service_factory, tmp_path: Path):
    """A remote left behind by a failed push converges on the next notarize:
    older local entries are re-pushed (never overwritten) so the remote
    reaches full history and verify goes HEALTHY."""
    remote_dir = tmp_path / "remote"
    service = service_factory(remote_dir=remote_dir)
    service.notarize()  # seq 1
    service.notarize()  # seq 2
    # Simulate a failed push for seq 1: the remote only ever received seq 2.
    first = min(p for p in remote_dir.iterdir())
    first.unlink()

    result = service.notarize()  # seq 3 -> must ALSO backfill seq 1
    assert result["ok"] is True
    assert result["seq"] == 3
    assert result["backfilled"] == [1]
    names = sorted(p.name for p in remote_dir.iterdir())
    assert len(names) == 3
    assert any(n.startswith("000001-") for n in names)
    assert any(n.startswith("000002-") for n in names)
    assert any(n.startswith("000003-") for n in names)

    report = service.verify()
    assert report["verdict"] == "HEALTHY"
    assert report["remote_entries"] == 3


def test_notarize_backfill_failure_is_loud(service_factory, tmp_path: Path):
    """If a backfilled entry cannot be pushed, notarize stays loud (ok=False)
    so the schedule alerts — the remote is still behind."""
    remote_dir = tmp_path / "remote"
    service = service_factory(remote_dir=remote_dir)
    service.notarize()  # seq 1
    service.notarize()  # seq 2
    first = min(p for p in remote_dir.iterdir())
    first.unlink()

    class FlakySink(LocalDirSink):
        def append(self, name: str, data: bytes) -> None:
            if name.startswith("000001-"):
                raise NotaryRemoteError("gdrive: transient timeout")
            super().append(name, data)

    service.remote = FlakySink(remote_dir)
    result = service.notarize()
    assert result["ok"] is False
    assert result["backfill_errors"], "expected a loud backfill failure"
    assert "1:" in result["backfill_errors"][0]  # seq 1 backfill failed
    assert "transient timeout" in result["backfill_errors"][0]


def test_verify_remote_behind_when_remote_entry_missing(service_factory, tmp_path: Path):
    remote_dir = tmp_path / "remote"
    service = service_factory(remote_dir=remote_dir)
    service.notarize()
    service.notarize()
    # Delete the remote head: the off-box copy no longer covers the local log.
    head = max(p for p in remote_dir.iterdir())
    head.unlink()

    report = service.verify()
    assert report["verdict"] == "REMOTE_BEHIND"
    assert "missing local entries" in report["detail"]


def test_verify_diverged_when_local_rolled_back(service_factory, tmp_path: Path):
    remote_dir = tmp_path / "remote"
    local_log = tmp_path / "notary.jsonl"
    service = service_factory(remote_dir=remote_dir, local_log=local_log)
    service.notarize()
    service.notarize()
    # Roll the LOCAL log back one entry while the remote still has both.
    lines = [line for line in local_log.read_text().splitlines() if line.strip()]
    local_log.write_text(lines[0] + "\n")

    report = service.verify()
    assert report["verdict"] == "DIVERGED"
    assert "local rollback" in report["detail"]


def test_verify_remote_unreachable_is_fail_closed(service_factory, tmp_path: Path):
    class BrokenSink(LocalDirSink):
        def list(self) -> list[str]:
            raise NotaryRemoteError("gdrive: connection refused")

    svc = service_factory(remote_dir=tmp_path / "remote")
    svc.notarize()
    svc.remote = BrokenSink(tmp_path / "remote")

    report = svc.verify()
    assert report["verdict"] == "REMOTE_UNREACHABLE"


def test_notarize_refuses_when_local_rolled_back(service_factory, tmp_path: Path):
    remote_dir = tmp_path / "remote"
    local_log = tmp_path / "notary.jsonl"
    service = service_factory(remote_dir=remote_dir, local_log=local_log)
    service.notarize()
    service.notarize()
    # Roll local back to 1 entry: the remote knows about seq 2.
    lines = [line for line in local_log.read_text().splitlines() if line.strip()]
    local_log.write_text(lines[0] + "\n")

    with pytest.raises(NotaryDiverged, match="rollback"):
        service.notarize()


def test_verify_local_invalid_on_corrupt_log(service_factory, tmp_path: Path):
    local_log = tmp_path / "notary.jsonl"
    local_log.write_text("this is not json\n")
    service = service_factory(remote_dir=tmp_path / "remote", local_log=local_log)
    report = service.verify()
    assert report["verdict"] == "LOCAL_INVALID"
    assert "corrupt" in report["detail"]


def test_verify_local_only_when_no_remote(service_factory):
    service = service_factory(remote_dir=None)
    service.notarize()
    report = service.verify()
    assert report["verdict"] == "LOCAL_ONLY"
    assert "same-box only" in report["detail"]


def test_rclone_sink_roundtrip(fake_rclone, tmp_path: Path):
    script, root = fake_rclone
    sink = RcloneSink("gdrive:msb-v3/chain-anchor-notary", rclone=str(script))
    sink.append("000001-20260816T071005Z.line", b'{"entry": 1}\n')
    sink.append("000002-20260816T071006Z.line", b'{"entry": 2}\n')

    assert sink.list() == ["000001-20260816T071005Z.line", "000002-20260816T071006Z.line"]
    assert sink.read("000001-20260816T071005Z.line") == b'{"entry": 1}\n'
    assert sink.read("does-not-exist") is None
    assert sink.stat("000002-20260816T071006Z.line") is not None  # server-side modtime evidence


def test_rclone_sink_notarize_verify_end_to_end(fake_rclone, chain, anchor, tmp_path: Path):
    script, root = fake_rclone
    remote = RcloneSink("gdrive:msb-v3/chain-anchor-notary", rclone=str(script))
    service = NotaryService(
        chain=chain,
        anchor=anchor,
        local_log=tmp_path / "notary.jsonl",
        remote=remote,
        timestamper=LocalReceiveTimestamper(),
    )
    result = service.notarize()
    assert result["ok"] is True and result["remote_push_ok"] is True

    # The fake remote holds the entry under gdrive:msb-v3/chain-anchor-notary/<name>.
    remote_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.line"))
    assert remote_files == [f"msb-v3/chain-anchor-notary/{result['remote_entry']}"]

    report = service.verify()
    assert report["verdict"] == "HEALTHY"
    assert report["head"]["timestamp_valid"] is True
    assert report["timestamp"]["source"] == "receive_time"


class _SameNameSiblingSink(LocalDirSink):
    """Simulates Google Drive's same-name siblings: several objects may share one
    name, and ``rclone cat`` on such a path returns them CONCATENATED.

    Measured live on 2026-10-11 — six remote names were duplicated and each read
    back as two byte-identical copies (1484 = 2 x 742). A plain directory cannot
    hold two same-named files, so the ambiguity is reproduced at the read
    boundary, which is exactly where the verifier meets it.
    """

    def __init__(self, root: Path, duplicates: set[str], copies: int = 2) -> None:
        super().__init__(root)
        self.duplicates = set(duplicates)
        self.copies = copies

    def read(self, name: str):
        data = super().read(name)
        if data is None or name not in self.duplicates:
            return data
        return data * self.copies


def _service(chain, anchor, tmp_path: Path, sink, name: str = "notary.jsonl"):
    return NotaryService(
        chain=chain,
        anchor=anchor,
        local_log=tmp_path / name,
        remote=sink,
    )


def test_verify_names_duplicated_remote_objects_instead_of_calling_them_corrupt(
    chain, anchor, tmp_path: Path
):
    """A remote name resolving to several objects is an AMBIGUOUS remote, not
    corruption: the content is intact copies. It was reported as "not JSON",
    which reads as tampering and sends the operator after a fault that did not
    happen — the live notary alert ran that way from 2026-09-26."""
    remote_dir = tmp_path / "remote"
    sink = _SameNameSiblingSink(remote_dir, duplicates=set())
    service = _service(chain, anchor, tmp_path, sink)
    service.notarize()
    service.notarize()
    names = sorted(p.name for p in remote_dir.iterdir())
    assert len(names) == 2
    sink.duplicates = {names[1]}

    report = service.verify()
    assert report["verdict"] == "REMOTE_INVALID"
    assert report["invalid_count"] == 1
    assert "resolves to 2 objects" in report["detail"]
    assert "not JSON" not in report["detail"]


def test_verify_still_reports_genuine_non_json_as_not_json(service_factory, tmp_path: Path):
    """The new label must not swallow the real fault: a payload that simply is
    not JSON still says so."""
    remote_dir = tmp_path / "remote"
    service = service_factory(remote_dir=remote_dir)
    service.notarize()
    name = next(p.name for p in remote_dir.iterdir())
    (remote_dir / name).write_text("this is not json at all")

    report = service.verify()
    assert report["verdict"] == "REMOTE_INVALID"
    assert f"{name}: not JSON" in report["detail"]


def test_verify_reports_the_true_invalid_count_past_the_display_cap(
    chain, anchor, tmp_path: Path
):
    """The live 2026-10-11 alert named exactly five failures while SIX names were
    duplicated: the display slice hid one and made the set look complete."""
    remote_dir = tmp_path / "remote"
    sink = _SameNameSiblingSink(remote_dir, duplicates=set())
    service = _service(chain, anchor, tmp_path, sink)
    for _ in range(7):
        service.notarize()
    names = sorted(p.name for p in remote_dir.iterdir())
    assert len(names) == 7
    sink.duplicates = set(names[1:])  # 6 of the 7

    report = service.verify()
    assert report["verdict"] == "REMOTE_INVALID"
    assert report["invalid_count"] == 6
    assert "and 1 more" in report["detail"]
    assert "invalid_count=6" in report["detail"]
