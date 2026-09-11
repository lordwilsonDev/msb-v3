#!/usr/bin/env python3
"""MSB v3 production gate.

This runner aggregates the repository's existing verification commands without
mutating source or Git state. Every leg is recorded as PASS, FAIL,
CONDITIONAL, WAIVED, or NOT_IMPLEMENTED in an atomic JSON evidence manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

CATALOG_VERSION = 1
SCHEMA_VERSION = 1
MAX_DETAIL = 4000
STATUSES = {"PASS", "FAIL", "CONDITIONAL", "WAIVED", "NOT_IMPLEMENTED"}
FOLLOW_UP_BLOCKERS = {
    "replay_idempotency": "OPEN",
    "failure_taxonomy": "OPEN",
    "mutation_thresholds": "OPEN",
    "slos": "OPEN",
}

_SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE|CREDENTIAL)", re.I)
_SECRET_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class Leg:
    name: str
    classification: str
    runner: Callable[[Path], tuple[int, str, str, list[str]]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded(text: str) -> str:
    return text[-MAX_DETAIL:]


def redact(text: str, environ: dict[str, str] | None = None) -> str:
    """Redact secret values and common credential-shaped output."""
    result = text
    for key, value in (environ or os.environ).items():
        if _SECRET_NAME.search(key) and value and len(value) >= 4:
            result = result.replace(value, "[REDACTED]")
    for pattern in _SECRET_SHAPES:
        result = pattern.sub("[REDACTED]", result)
    return bounded(result)


def run_command(
    command: Sequence[str],
    repo: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 1800.0,
) -> tuple[int, str, str, list[str]]:
    """Run one gate command and return rc, redacted output, and evidence paths."""
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    child_env.setdefault("PYTHONPATH", str(repo / "src"))
    try:
        result = subprocess.run(
            list(command),
            cwd=repo,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (
            result.returncode,
            redact(result.stdout, child_env),
            redact(result.stderr, child_env),
            [],
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, redact(stdout, child_env), redact(f"timeout after {timeout}s\n{stderr}", child_env), []
    except OSError as exc:
        return 127, "", redact(f"could not execute command: {exc}", child_env), []


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def python_executable(repo: Path) -> str:
    configured = os.getenv("MSB_PYTHON")
    if configured:
        return configured
    venv_python = repo / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def interpreter_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return (result.stdout or result.stderr).strip() or platform.python_version()
    except (OSError, subprocess.TimeoutExpired):
        return platform.python_version()


def identity(repo: Path) -> dict[str, Any]:
    status = git_output(repo, "status", "--short")
    commit = git_output(repo, "rev-parse", "HEAD")
    executable = python_executable(repo)
    return {
        "commit": commit or None,
        "branch": git_output(repo, "branch", "--show-current") or None,
        "dirty": bool(status),
        "tag": git_output(repo, "describe", "--tags", "--always", "--dirty") or None,
        "python": executable,
        "python_version": interpreter_version(executable),
        "os": platform.system(),
        "architecture": platform.machine(),
        "lock_hashes": {
            "runtime": sha256_file(repo / "requirements-runtime.lock"),
            "dev": sha256_file(repo / "requirements-dev.lock"),
        },
        # Local-only after the frontier retirement (D1, 2026-09-09): the
        # model inventory is chat + embedding on the local stack.
        "models": {
            "chat": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
            "embedding": os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        },
    }


def pass_leg(name: str, detail: str, evidence: Iterable[str] = ()) -> tuple[int, str, str, list[str]]:
    return 0, detail, "", list(evidence)


def conditional_leg(detail: str) -> tuple[int, str, str, list[str]]:
    return 0, detail, "", []


def not_implemented_leg(detail: str) -> tuple[int, str, str, list[str]]:
    return 0, detail, "", []


def identity_runner(repo: Path) -> tuple[int, str, str, list[str]]:
    required = (repo / "pyproject.toml", repo / "MANIFEST.md")
    missing = [str(p.relative_to(repo)) for p in required if not p.is_file()]
    if missing:
        return 1, "", f"missing identity files: {', '.join(missing)}", []
    return pass_leg(
        "identity files present",
        "pyproject.toml and MANIFEST.md present",
        [p.relative_to(repo).as_posix() for p in required],
    )


def command_runner(command: Sequence[str], *, env: dict[str, str] | None = None, timeout: float = 1800.0) -> Callable[[Path], tuple[int, str, str, list[str]]]:
    def runner(repo: Path) -> tuple[int, str, str, list[str]]:
        return run_command(command, repo, env=env, timeout=timeout)
    return runner


def dependency_runner(repo: Path) -> tuple[int, str, str, list[str]]:
    if shutil.which("pip-audit") is None:
        return 127, "", "pip-audit is not installed; required dependency scan cannot run", []
    return run_command(
        ["pip-audit", "-r", "requirements-runtime.lock", "--strict", "--disable-pip"],
        repo,
        timeout=600,
    )


def package_runner(repo: Path) -> tuple[int, str, str, list[str]]:
    executable = python_executable(repo)
    if importlib_available("build", executable=executable) is False:
        return 127, "", "python -m build is not installed; required package verification cannot run", []
    with tempfile.TemporaryDirectory(prefix="msb-production-build-") as output:
        return run_command(
            [executable, "-m", "build", "--wheel", "--sdist", "--outdir", output],
            repo,
            timeout=600,
        )


def importlib_available(module: str, *, executable: str | None = None) -> bool:
    try:
        result = subprocess.run(
            [executable or sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def optional_runner(
    env_name: str,
    command: Sequence[str],
    description: str,
    *,
    timeout: float = 1800.0,
) -> Callable[[Path], tuple[int, str, str, list[str]]]:
    def runner(repo: Path) -> tuple[int, str, str, list[str]]:
        if os.getenv(env_name) != "1":
            return conditional_leg(f"{description} not enabled; set {env_name}=1 to execute")
        return run_command(command, repo, timeout=timeout)
    return runner


def docker_runner(repo: Path) -> tuple[int, str, str, list[str]]:
    if shutil.which("docker") is None:
        return conditional_leg("Docker CLI unavailable")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        return conditional_leg(f"Docker daemon unavailable: {redact(probe.stderr)}")
    return run_command(
        ["bash", "scripts/close-out-gate.sh"],
        repo,
        env={"MSB_CLOSE_OUT_SKIP": "lint,pytest,pip-audit"},
        timeout=1200,
    )


def ledger_runner(repo: Path) -> tuple[int, str, str, list[str]]:
    """The exact standalone JSONL/root verifier is intentionally still open."""
    return not_implemented_leg(
        "standalone `msb-ledger verify --ledger ... --expected-root ...` contract is not implemented"
    )


def catalog() -> list[Leg]:
    py = python_executable(Path(__file__).resolve().parents[1])
    return [
        Leg("identity", "required", identity_runner),
        Leg("lint-type-claims-policy", "required", command_runner(["make", f"PY={py}", "lint"], timeout=1200)),
        Leg(
            "tests-coverage",
            "required",
            command_runner(
                ["bash", "scripts/close-out-gate.sh"],
                env={
                    "MSB_CLOSE_OUT_SKIP": "lint,pip-audit,docker",
                    "MSB_PYTHON": python_executable(Path(__file__).resolve().parents[1]),
                },
                timeout=1800,
            ),
        ),
        Leg("secret-scan", "required", command_runner([py, "scripts/scan-secrets.py", "--tree"], timeout=300)),
        Leg("dependency-audit", "required", dependency_runner),
        Leg("migration-tests", "required", command_runner([py, "-m", "pytest", "tests/db", "-q"], timeout=600)),
        Leg("package-build", "required", package_runner),
        Leg("standalone-ledger-verifier", "required", ledger_runner),
        Leg(
            "clean-install-smoke",
            "conditional",
            optional_runner("PRODUCTION_GATE_CLEAN_INSTALL", ["bash", "scripts/verify-release.sh"], "clean-install smoke", timeout=1800),
        ),
        Leg("portability", "conditional", optional_runner("PRODUCTION_GATE_PORTABILITY", ["make", "portability"], "portability", timeout=1800)),
        Leg("docker-build-smoke", "conditional", docker_runner),
        Leg(
            "live-provider-integration",
            "conditional",
            optional_runner("PRODUCTION_GATE_LIVE", ["bash", "scripts/production-live-gate.sh"], "live provider integration", timeout=900),
        ),
    ]


def parse_waivers(legs: list[Leg], waiver_legs: list[str], reasons: list[str]) -> dict[str, str]:
    if len(waiver_legs) != len(reasons):
        raise ValueError("each --waive LEG must have one following --reason REASON")
    known = {leg.name: leg for leg in legs}
    waivers: dict[str, str] = {}
    for name, reason in zip(waiver_legs, reasons):
        if name not in known:
            raise ValueError(f"cannot waive unknown leg: {name}")
        if known[name].classification != "conditional":
            raise ValueError(f"only conditional legs may be waived: {name}")
        if not reason.strip():
            raise ValueError(f"waiver reason cannot be empty: {name}")
        if name in waivers:
            raise ValueError(f"duplicate waiver: {name}")
        waivers[name] = reason.strip()
    return waivers


def classify_result(leg: Leg, rc: int, detail: str, stderr: str, waivers: dict[str, str]) -> str:
    if leg.name in waivers:
        return "WAIVED"
    if detail.startswith("standalone `msb-ledger verify"):
        return "NOT_IMPLEMENTED"
    if detail.startswith(("Docker CLI unavailable", "Docker daemon unavailable", "clean-install smoke not enabled", "portability not enabled", "live provider integration not enabled")):
        return "CONDITIONAL"
    return "PASS" if rc == 0 else "FAIL"


def overall_verdict(results: list[dict[str, Any]]) -> str:
    if any(r["status"] in {"FAIL", "NOT_IMPLEMENTED"} and r["class"] == "required" for r in results):
        return "FAIL"
    if any(r["status"] == "FAIL" for r in results):
        return "FAIL"
    if any(r["status"] in {"CONDITIONAL", "WAIVED"} for r in results):
        return "CONDITIONAL"
    return "PASS"


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final = output_dir / f"gate-{manifest['gate_run_id']}.json"
    temporary = output_dir / f".{final.name}.{secrets.token_hex(6)}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, final)
    return final


def execute(
    repo: Path,
    output_dir: Path,
    waivers: dict[str, str],
    *,
    profile: str = "strict",
    invocation: Sequence[str] | None = None,
) -> tuple[int, Path, dict[str, Any]]:
    started = now_iso()
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    results: list[dict[str, Any]] = []
    for leg in catalog():
        leg_started = now_iso()
        rc: int
        stdout: str
        stderr: str
        evidence: list[str]
        if leg.name in waivers:
            rc, stdout, stderr, evidence = 0, "", "", []
            detail = f"WAIVED: {waivers[leg.name]}"
            status = "WAIVED"
        else:
            try:
                rc, stdout, stderr, evidence = leg.runner(repo)
            except Exception as exc:  # the gate must record an unexpected leg failure
                rc, stdout, stderr, evidence = 1, "", f"runner error: {type(exc).__name__}: {exc}", []
            detail = bounded("\n".join(part for part in (stdout, stderr) if part).strip())
            status = classify_result(leg, rc, detail, stderr, waivers)
        results.append(
            {
                "name": leg.name,
                "class": leg.classification,
                "status": status,
                "exit_code": rc,
                "started_at": leg_started,
                "finished_at": now_iso(),
                "evidence": evidence,
                "detail": redact(detail),
            }
        )
    verdict = overall_verdict(results)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "gate_run_id": run_id,
        "started_at": started,
        "finished_at": now_iso(),
        "identity": identity(repo),
        "catalog_version": CATALOG_VERSION,
        "profile": profile,
        "invocation": list(invocation or []),
        "legs": results,
        "waivers": [
            {"leg": name, "reason": reason, "timestamp": now_iso(), "operator": os.getenv("USER", "unknown")}
            for name, reason in waivers.items()
        ],
        "follow_up_blockers": dict(FOLLOW_UP_BLOCKERS),
        "overall_verdict": verdict,
    }
    path = write_manifest(output_dir, manifest)
    return (0 if verdict == "PASS" else 1), path, manifest


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=("strict", "candidate"), default="strict")
    ap.add_argument("--allow-conditional", action="store_true", help="allow a CONDITIONAL overall verdict to exit 0")
    ap.add_argument("--waive", action="append", default=[], metavar="LEG")
    ap.add_argument("--reason", action="append", default=[], metavar="REASON")
    ap.add_argument("--output-dir", type=Path, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    output_dir = (args.output_dir or repo / "artifacts" / "production-gate").resolve()
    try:
        waivers = parse_waivers(catalog(), args.waive, args.reason)
    except ValueError as exc:
        parser().error(str(exc))
    rc, path, manifest = execute(
        repo,
        output_dir,
        waivers,
        profile=args.profile,
        invocation=list(argv if argv is not None else sys.argv[1:]),
    )
    verdict = manifest["overall_verdict"]
    if verdict == "CONDITIONAL" and (args.allow_conditional or args.profile == "candidate"):
        rc = 0
    print(f"[production-gate] verdict={verdict} manifest={path.relative_to(repo) if path.is_relative_to(repo) else path}")
    for leg in manifest["legs"]:
        print(f"[production-gate] {leg['status']:<16} {leg['name']}")
    if manifest["follow_up_blockers"]:
        print("[production-gate] follow-up blockers remain OPEN: " + ", ".join(manifest["follow_up_blockers"]))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
