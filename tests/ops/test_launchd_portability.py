"""M7 dry-run regression guards (2026-08-17).

The operator dry-run on a fresh clone caught two real blockers that would
have broken setup for any independent user:

1. The committed launchd plist hardcoded the builder's machine home path
   (a /Users/<name> literal) as the program path — an agent bootstrapped
   from it on any other machine would point at a nonexistent run.sh. The
   plist is now a path-neutral template (__MSB_REPO__ placeholder) that
   start.sh renders to the installed location before bootstrap.

2. run.sh hardcoded a machine-wide python (/opt/homebrew/.../miniforge)
   and ignored the venv the setup guide creates — so the freshly
   installed locks would never run. run.sh now prefers
   $REPO/.venv/bin/python when present (explicit MSB_PYTHON still wins).

These tests pin both so the regression cannot be reintroduced silently.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLIST = ROOT / "scripts" / "launchd" / "com.lordwilson.msb-v3.plist"
RUN_SH = ROOT / "scripts" / "run.sh"
START_SH = ROOT / "scripts" / "start.sh"

# The builder's home directory — derived at runtime so this file itself never
# embeds a machine literal (the portability gate scans for those). On the
# machine where the plist used to break, this equals the offending path.
BUILDER_HOME = str(Path.home())
# Any absolute home path (defense in depth: any /Users/<name> would break too).
_ABS_HOME = re.compile(r"/Users/\w+")


def test_plist_has_no_machine_home_paths() -> None:
    text = PLIST.read_text(encoding="utf-8")
    assert BUILDER_HOME not in text, "plist must not embed the builder's home path"
    assert not _ABS_HOME.search(text), "plist must not embed ANY /Users/<name> path"


def test_plist_is_a_template_with_repo_placeholder() -> None:
    text = PLIST.read_text(encoding="utf-8")
    # The program path, working dir and log paths must all be templated.
    assert "__MSB_REPO__/scripts/run.sh" in text
    assert "<string>__MSB_REPO__</string>" in text
    assert "__MSB_REPO__/logs/gateway.out.log" in text
    assert "__MSB_REPO__/logs/gateway.err.log" in text
    # Placeholders: run.sh, WorkingDirectory, out log, err log (4 path uses)
    # + 1 mention in the explanatory comment = 5.
    assert text.count("__MSB_REPO__") == 5


def test_start_sh_renders_plist_from_template() -> None:
    text = START_SH.read_text(encoding="utf-8")
    assert "TEMPLATE_PLIST=" in text, "start.sh must know the repo template"
    assert "render_plist" in text, "start.sh must define render_plist()"
    # The render must substitute the placeholder with the actual repo path.
    assert 'sed "s|__MSB_REPO__|$REPO|g"' in text


def test_run_sh_prefers_repo_venv() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert "$REPO/.venv/bin/python" in text, "run.sh must prefer the checkout venv"
    assert "MSB_PYTHON" in text, "explicit MSB_PYTHON override must still win"
    # The venv preference must come before the hardcoded fallback.
    venv_idx = text.find(".venv/bin/python")
    fallback_idx = text.find("miniforge")
    assert venv_idx != -1 and fallback_idx != -1
    assert venv_idx < fallback_idx, "venv preference must precede the base-python fallback"
