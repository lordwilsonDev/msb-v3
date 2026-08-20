"""Root conftest — makes the repo root importable under any pytest invocation.

Local runs use `python -m pytest` (scripts/test.sh), which puts the CWD on
sys.path — so tests can `import experiments` etc. CI runs the bare `pytest`
console script, which does NOT add the CWD, and tests/contracts/
test_phase1_contract.py failed there with ModuleNotFoundError: No module
named 'experiments'. This conftest pins the repo root onto sys.path
explicitly so both launchers behave identically.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
