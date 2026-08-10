# msb-v3 Mutation Analysis & Adversarial Audit Report

**Date:** 2026-08-10  
**Generator:** `scripts/hygiene/mutation_score_snapshot.py`  
**Target:** `src/msb_v3/` core modules  

## Overview

Mutation testing evaluates test suite quality by injecting synthetic mutations into application code and verifying whether the test suite detects ("kills") the change.

- **Total Mutants Evaluated:** 120
- **Killed Mutants:** 85
- **Survived Mutants:** 35
- **Timeout / Errors:** 0
- **Mutation Score:** **70.8%**

## Findings & Survivor Clusters

1. **Guardrails & Step Enforcer (`src/msb_v3/guardrails/fold.py`)**:
   - High kill rate (>85%). Edge cases in sequence step enforcement are well-tested by unit tests.

2. **Local AI Client (`src/msb_v3/local_ai/ollama.py`)**:
   - Tool loop execution and argument formatting killed mutants in `execute_tool_loop()`. Survived mutants primarily consist of logging and optional timeout parameters.

3. **API Routing (`src/msb_v3/api/app.py`)**:
   - Middleware ordering and CORS setup are tested by live endpoint probes.

## Tier Elevation

With a verified mutation score of **70.8%** (exceeding the T5 threshold of 50.0%), `msb-v3` advances from **T4 INTEGRATED** to **T5 ADVERSARIAL** in the sovereign constellation hygiene ledger.
