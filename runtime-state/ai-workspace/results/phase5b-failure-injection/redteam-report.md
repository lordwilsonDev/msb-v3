# Phase 5B — RED TEAM False-Green Hunt Report

**Date:** 2026-09-03  
**Target:** MSB `verify_build` implementation  
**Objective:** Find conditions where KNOWN-BAD artifacts produce GREEN results  
**Constraint:** No modifications to verifier or specimens after execution  

---

## A. Verifier Architecture Map

```
HTTP POST /mcp/proxy {"tool":"verify_build","args":{...}}
  │
  ├─ _check_auth(request)                    ← constant-time secret comparison
  │
  ├─ build_id validation                     ← regex: [a-zA-Z0-9._-]+
  │   ├─ empty → 400
  │   └─ invalid chars → 400
  │
  ├─ args validation                         ← at least one of files/tests required
  │   ├─ both empty → 400
  │
  ├─ _normalize_path_list(args.files)        ← comma-split or list → list[str]
  ├─ _normalize_path_list(args.tests)        ← same
  │
  ├─ EXISTENCE CHECKS (ONLY):
  │   ├─ missing_files = [f for f in files if not Path(f).is_file()]
  │   └─ missing_tests = [t for t in tests if not Path(t).is_file()]
  │
  ├─ if missing_files or missing_tests:
  │   └─ return FAILED + missing lists
  │
  ├─ ECHO RECEIPT:
  │   └─ write "VERIFIED\nid: ...\nfiles: ...\ntests: ...\ntimestamp: ...\n"
  │
  ├─ VAULT NOTE:
  │   └─ append to 40_Memory/Verified-Builds-Log.md
  │
  ├─ AUDIT LOG:
  │   └─ _log_audit(action="verify_build", result="verified")
  │
  └─ return {"ok":True, "result":{"status":"VERIFIED", "echo_path":"...", "vault_note":"..."}}
```

**Input validation:** build_id format, presence of files/tests.  
**Content validation:** NONE.  
**Semantic validation:** NONE.  
**Boundary checks:** NONE beyond `Path.is_file()`.  

---

## B. Attack Surface

| Layer | Check | Implementation |
|-------|-------|----------------|
| Auth | Secret header | Constant-time comparison ✅ |
| Input | build_id format | Regex `[a-zA-Z0-9._-]+` ✅ |
| Input | files/tests presence | Non-empty check ✅ |
| Existence | File on disk | `Path.is_file()` ✅ |
| Existence | Test on disk | `Path.is_file()` ✅ |
| **Content** | **File contents** | **NOT CHECKED** ❌ |
| **Schema** | **JSON/structure validity** | **NOT CHECKED** ❌ |
| **Semantic** | **Meaning/validity of data** | **NOT CHECKED** ❌ |
| **Freshness** | **File age/timestamp** | **NOT CHECKED** ❌ |
| **Permissions** | **Readability** | **NOT CHECKED** ❌ |
| **Path scope** | **Vault containment** | **NOT CHECKED** ❌ |
| **Provenance** | **Hash/integrity** | **NOT CHECKED** ❌ |

---

## C–H. Attack Results

### Attacks Executed: 16

| ID | Attack | Expected | Actual | Verdict |
|----|--------|----------|--------|---------|
| CTRL | Clean control | VERIFIED | VERIFIED | ✅ GREEN |
| ATK-01 | Empty file (0 bytes) | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-02 | Whitespace-only file | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-03 | Invalid JSON content | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-04 | Empty JSON object `{}` | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-05 | Wrong field type (`"count":"not_a_number"`) | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-06 | Control characters in path | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-07 | Unreadable file (mode 600) | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-08 | Stale file (365 days old) | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-09 | Binary garbage (`\x00\xff\x80\xfe`) | FAILED | **VERIFIED** | ❌ FALSE GREEN |
| ATK-10 | External path `/etc/hostname` | FAILED | FAILED | ✅ RED |
| ATK-11 | Directory instead of file | FAILED | FAILED | ✅ RED |
| ATK-12 | Broken symlink | FAILED | FAILED | ✅ RED |
| ATK-13 | Empty build_id | FAILED | 400 error | ✅ RED |
| ATK-14 | Special chars in build_id (`../evil`) | FAILED | 400 error | ✅ RED |
| ATK-15 | Neither files nor tests | FAILED | 400 error | ✅ RED |
| ATK-16 | Null byte in path | FAILED | FAILED | ✅ RED |

**Detected correctly (RED):** 7  
**False GREENs:** 9  
**False REDs:** 0  

**Detection rate:** 43.75%  
**False-GREEN rate:** 56.25%  

---

## I. Highest-Severity Verifier Weakness

### CRITICAL — Verify_build is an existence gate, not a content verifier

The verifier performs exactly one semantic check: `Path(f).is_file()`. It does not:
- Read file contents
- Parse structured data (JSON, YAML, etc.)
- Validate schemas
- Check permissions or readability
- Validate file freshness
- Check path scope (vault containment)
- Verify hashes or provenance
- Execute or import claimed code/tests
- Validate that tests actually exercise the claimed target

**Invariant assumed but not proven:** "If a file exists on disk, the build artifact is valid."

This invariant is false for every realistic content-level failure mode.

---

## J. Exact Evidence for Each Weakness

### 1. Content validation gap (CRITICAL)
**Evidence:** ATK-01 through ATK-09 all produce VERIFIED despite containing:
- Empty content (ATK-01)
- Whitespace only (ATK-02)
- Invalid JSON syntax (ATK-03)
- Empty JSON structure (ATK-04)
- Type mismatches (ATK-05)
- Binary garbage (ATK-09)

**Source:** `mcp_bridge.py` lines 578–584: `missing_files = [f for f in files if not Path(f).is_file()]`

**Why verify_build accepted it:** `Path.is_file()` returns `True` for all these files. The verifier never opens, reads, or parses them.

---

### 2. Permission blindness (HIGH)
**Evidence:** ATK-07 — file with mode `0o600` (owner read/write only) returns VERIFIED.

**Source:** `Path.is_file()` does not check readability. It only checks that the path exists and is a regular file.

**Why verify_build accepted it:** The process running the bridge has sufficient privileges to stat the file. Readability is never tested.

---

### 3. Freshness blindness (MEDIUM)
**Evidence:** ATK-08 — file with mtime 365 days in the past returns VERIFIED.

**Source:** No timestamp check exists in the verifier.

**Why verify_build accepted it:** `Path.is_file()` is stateless regarding file age.

---

### 4. Path scope gap (HIGH — potential)
**Evidence:** ATK-10 through ATK-12 correctly FAILED, but only because the tested paths don't exist or aren't files. The verifier uses raw `Path(f).is_file()` without vault containment.

**Source:** `_normalize_vault_path()` exists but is only used for vault operations (vault_read, vault_write, open_file). `verify_build` uses caller-supplied paths directly.

**Why verify_build accepted/rejected:** It accepted `/tmp/...` paths because they exist. It would accept ANY existing file path on the system.

---

### 5. Control character handling (LOW)
**Evidence:** ATK-06 — file with control characters in its name returns VERIFIED.

**Source:** `_safe_text()` collapses control characters for echo receipt and vault note display, but does not affect the existence check.

**Why verify_build accepted it:** The file exists on disk with that exact name.

---

## K. Invariant Assumed But Not Proven

**"Existence implies validity."**

The verifier assumes that if a file exists on disk, it is a valid build artifact. This invariant is not proven by any check. The verifier does not establish:

1. The file contains valid content
2. The content matches an expected schema or structure
3. The file is readable by the verifier
4. The file is fresh/current
5. The file is within an approved scope
6. The file's content hash matches a known-good value
7. The file's dependencies/tests actually exist and are functional

---

## L. Recommended Repair

### Option 1 — Content-aware verification (recommended for MSB-governed builds)
Add optional content checks to `verify_build`:
```python
# After existence check passes
if args.get("validate_json") and f.endswith(".json"):
    try:
        json.load(open(f))
    except json.JSONDecodeError:
        return FAILED, detail="invalid JSON"

if args.get("schema"):
    # validate against declared schema
    ...
```

### Option 2 — Separate content verifier tool
Create a new MCP tool `verify_build_content` that performs:
- Schema validation
- Hash verification
- Semantic checks
- Dependency resolution

Leave `verify_build` as the lightweight existence gate for fast pre-checks.

### Option 3 — Scope enforcement
Apply `_normalize_vault_path()` or equivalent to `verify_build` file paths to enforce vault containment:
```python
files = [_normalize_vault_path(f) for f in files]
```

---

## M. Regression Test

```python
def test_verify_build_rejects_empty_file():
    """Empty files must FAIL, not VERIFIED."""
    (Path("/tmp/test-empty.txt")).write_text("")
    result = call_verify_build(files=["/tmp/test-empty.txt"])
    assert result["status"] == "FAILED"

def test_verify_build_rejects_invalid_json():
    """Invalid JSON must FAIL when schema validation requested."""
    (Path("/tmp/test-bad.json")).write_text("{invalid}")
    result = call_verify_build(files=["/tmp/test-bad.json"], validate_json=True)
    assert result["status"] == "FAILED"

def test_verify_build_rejects_unreadable_file():
    """Unreadable files must FAIL."""
    f = Path("/tmp/test-noread.txt")
    f.write_text("secret")
    os.chmod(f, 0o000)
    result = call_verify_build(files=["/tmp/test-noread.txt"])
    assert result["status"] == "FAILED"
    os.chmod(f, 0o644)  # cleanup
```

---

## N. Phase 5B Verdict

**INCONCLUSIVE → WEAKNESS CONFIRMED**

The scientific question — "Does verify_build detect injected failures?" — has been answered empirically:

**No.** `verify_build` does not detect any of the 9 injected content-level failures. It returned VERIFIED for:
- Empty files
- Invalid JSON
- Corrupted binary
- Wrong types
- Stale artifacts
- Unreadable files

This is not a bug in the implementation. The implementation correctly performs its current contract (existence checking). The weakness is that the contract is too narrow for real-world verification needs.

**Phase 5B is complete.** The experiment produced a reproducible, empirical boundary:
- What verify_build CAN prove: file existence
- What verify_build CANNOT prove: content validity, schema compliance, freshness, permissions, provenance

This is the correct experimental outcome. The system was not modified. The specimens were preserved. The result is valid Green-Gate data.

---

## Attack Summary Statistics

| Metric | Value |
|--------|-------|
| Attacks executed | 16 |
| Correctly detected (RED) | 7 |
| False GREENs | 9 |
| False REDs | 0 |
| Detection rate | 43.75% |
| False-GREEN rate | 56.25% |
| Highest severity | CRITICAL |
| Weakest invariant | "Existence implies validity" |

---

*Report generated: 2026-09-03T00:50:00Z*  
*Raw results: `/tmp/phase5b-redteam/redteam-results.json`*
