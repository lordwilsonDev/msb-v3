#!/usr/bin/env python3
"""Broader secret scan for vault export: the repo scanner's families PLUS the
key shapes AI/dev notes actually contain (Anthropic, OpenAI project, Stripe,
fine-grained GitHub, HF, JWT, bearer headers, key=value assignments).
Usage: vault-secret-scan.py DIR   -> exit 0 clean, 1 findings."""
import importlib.util, re, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("ss", Path(__file__).with_name("scan-secrets.py"))
ss = importlib.util.module_from_spec(spec); spec.loader.exec_module(ss)

EXTRA = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-project", re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}")),
    ("stripe", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("github-token", re.compile(r"\b(?:gh[ousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("huggingface", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("assignment", re.compile(r"""(?i)\b[\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d)[\w-]*["']?\s*[:=]\s*["']?[A-Za-z0-9/+_.=-]{16,}""")),
]
SKIP = {".git", ".obsidian", "node_modules"}
bad = 0
for p in sorted(Path(sys.argv[1]).rglob("*")):
    if not p.is_file() or SKIP & set(p.parts):
        continue
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    for i, line in enumerate(lines, 1):
        fam = (ss.scan_lines([line]) or [{}])[0].get("family") or next((n for n, r in EXTRA if r.search(line)), None)
        if fam:
            bad += 1
            print(f"{p}:{i} [{fam}] {line.strip()[:60]}...", file=sys.stderr)
sys.exit(1 if bad else 0)
