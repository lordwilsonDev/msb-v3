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
