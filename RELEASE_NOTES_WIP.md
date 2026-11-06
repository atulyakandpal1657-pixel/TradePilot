# RELEASE NOTES WIP (TradePilot v1.0 Release Worklog)

## 1. Database Protection & Verification
- **Backup Location:** `C:\Users\atuly\.gemini\antigravity\brain\bd783083-08d3-4de9-8cd8-711fb5411e0d\db_backup\tradepilot.db`
- **Initial Database Path:** `d:\Projects\TradePilot\data\tradepilot.db`
- **Initial SHA-256 Checksum:** `82AC2851E605B928663C971123A2AE622F9B8CB8042CC7CEA1F89D153375EE32`
- **Backup SHA-256 Checksum:** `82AC2851E605B928663C971123A2AE622F9B8CB8042CC7CEA1F89D153375EE32`
- **Backup Readability Check:** Verified via sqlite3 `SELECT count(*) FROM trade_records` -> returned 2 records (`PLTR` filled, `TSLA` pending).

## 2. Dedicated Release Branch & Pre-Release Checkpoint
- **Release Branch:** `release/tradepilot-v1.0`
- **Checkpoint Commit Message:** `pre-release snapshot`
- **Files Staged & Included:**
  - `tradepilot/agents/llm_strategy.py` (Gemini strategy agent updates)
  - `tradepilot/agents/strategy.py` (Strategy base and deterministic baseline updates)
  - `tradepilot/agents/evidence_auditor.py` (AI decision evidence auditor)
  - `tradepilot/api.py` (FastAPI backend endpoints)
  - `tradepilot/backtest/` (`__init__.py`, `data.py`, `engine.py`, `models.py`, `portfolio.py`)
  - `tradepilot/core/models.py` (Pydantic models)
  - `tradepilot/core/sizing.py` (Position sizing calculation)
  - `tradepilot/decision_trace.py` (Explainable decision trace updates)
  - `tradepilot/memory/__init__.py` (Memory exports)
  - `tradepilot/memory/journal.py` (SQLite journal for records, pending proposals, evidence audits)
  - `tradepilot/web/app.js` (Web UI client script)
  - `tradepilot/web/index.html` (Web UI layout)
  - `tradepilot/web/style.css` (Web UI stylesheet)
  - `tests/test_api.py` (API tests)
  - `tests/test_api_analyze.py` (API analyze tests)
  - `tests/test_api_approval.py` (API approval tests)
  - `tests/test_api_backtest.py` (API backtest tests)
  - `tests/test_api_execution.py` (API execution tests)
  - `tests/test_api_journal.py` (API journal tests)
  - `tests/test_api_risk.py` (API risk tests)
  - `tests/test_backtest_engine.py` (Backtest engine tests)
  - `tests/test_evidence_auditor.py` (Evidence auditor tests)
  - `tests/test_llm_retry.py` (LLM retry tests)
  - `tests/test_llm_strategy.py` (LLM strategy tests)
  - `PROJECT_TECHNICAL_AUDIT.md` (Comprehensive audit report from prior task)
- **Files Excluded from Commit:**
  - `data/tradepilot.db` (Excluded per Rule 2 & 4: database must never be committed; contains user paper trade records; preserved untouched in working tree)
  - `.env` (Excluded per Rule 2 & 4: contains API credentials; strictly gitignored)
  - `data/test_*.db`, `data/demo_*.db` (Excluded: disposable generated test/demo artifacts; gitignored)

## 3. Phase Log & Running Decisions
*(Will be appended after each phase)*

## 4. Phase A: Baseline Inspection Findings
- **Baseline Test Run Results:**
  - Total Collected: 323 tests
  - Passed: 317 tests
  - Failed: 6 tests (all 6 in `tests/test_evidence_auditor.py`)
  - Skipped: 0
  - Warnings: 2 warnings (`fastapi.testclient` deprecation for `httpx2`, and `websockets.legacy` deprecation warning)
  - Time: 10.72s
- **Baseline Failure Analysis:**
  - `tests/test_evidence_auditor.py::test_audit_all_supported_claims`
  - `tests/test_evidence_auditor.py::test_audit_absent_portfolio_not_treated_as_false`
  - `tests/test_evidence_auditor.py::test_audit_no_trade_proposal_without_inventing_evidence`
  - `tests/test_evidence_auditor.py::test_journal_isolation`
  - `tests/test_evidence_auditor.py::test_api_audit_endpoint`
  - `tests/test_evidence_auditor.py::test_api_trace_includes_evidence_audit`
  All failed with `AssertionError: assert 'INCONCLUSIVE' == 'PASSED'`.
- **Database Write Incident & Fix:**
  - During the baseline run, `tests/test_api_risk.py` executed `POST /api/risk/{symbol}` without patching `tradepilot.api.DB_PATH`.
  - This resulted in 3 rows added to `pending_proposals` and 3 rows added to `evidence_audits` in `data/tradepilot.db`.
  - Checksum changed from `82AC2851E605B928663C971123A2AE622F9B8CB8042CC7CEA1F89D153375EE32` to `45F09E205997B3215A1FCBFD4ADF156E7BF1D2CA1A076CA40D334F2C3677950B`.
  - Both copies were immediately preserved:
    - Pre-run backup: `C:\Users\atuly\.gemini\antigravity\brain\bd783083-08d3-4de9-8cd8-711fb5411e0d\db_backup\tradepilot.db`
    - Post-baseline copy: `C:\Users\atuly\.gemini\antigravity\brain\bd783083-08d3-4de9-8cd8-711fb5411e0d\db_backup\tradepilot_post_baseline.db`
  - Per instructions ("treat that as a defect and fix it by isolating the test, not by restoring the file afterward"), an `autouse=True` fixture was added to `tests/test_api_risk.py` redirecting `tradepilot.api.DB_PATH` to `tmp_path / "test_api_risk.db"`.
  - Re-run of `tests/test_api_risk.py` confirmed 13 passed in 1.57s with identical checksum before and after (`45F09E205997B3215A1FCBFD4ADF156E7BF1D2CA1A076CA40D334F2C3677950B`).

## 5. Phase B: EvidenceAuditor Enum Mapping, Precedence & Proposal Shapes
- **AuditStatus Enum Verification:**
  - Evaluated `AuditStatus` in `tradepilot/core/models.py`.
  - Canonical categories confirmed 1:1 mapping:
    - `MISSING_EVIDENCE` -> `AuditStatus.MISSING_EVIDENCE`
    - `STALE_OR_MISMATCHED` -> `AuditStatus.STALE_OR_MISMATCHED`
    - `FAILED` -> `AuditStatus.FAILED`
    - `INCONCLUSIVE` -> `AuditStatus.INCONCLUSIVE`
    - `PASSED` -> `AuditStatus.PASSED`
  - No intermediate, ambiguous, or unmapped states.
- **EvidenceAuditor Root Cause Defect Fixed:**
  - In `tradepilot/agents/evidence_auditor.py`, forward-looking assumptions and invalidation conditions were previously counted towards `unverifiable_count > 0` which forced `overall_status` into `INCONCLUSIVE`, even when all factual market and portfolio claims passed.
  - Separated factual evidence gaps (e.g. absent indicators like RSI/VWAP/MACD, missing portfolio state when a portfolio metric was claimed, unparseable numeric values, absent snapshot fields) from qualitative assumptions and invalidation conditions.
  - Implemented exact precedence order:
    `MISSING_EVIDENCE > STALE_OR_MISMATCHED > FAILED > INCONCLUSIVE > PASSED`
    1. `MISSING_EVIDENCE`: `snapshot is None` immediately returns `MISSING_EVIDENCE`.
    2. `STALE_OR_MISMATCHED`: Snapshot symbol mismatch, expected snapshot timestamp mismatch (>60s), or proposal lookahead/stale timestamp (>300s) produces `STALE_OR_MISMATCHED`.
    3. `FAILED`: Any unsupported factual claim makes the result `FAILED`, even if other claims are unverifiable or missing evidence.
    4. `INCONCLUSIVE`: When no claims failed, but factual evidence gaps exist (absent indicators, missing portfolio data when claimed, unparseable values, or no verifiable factual claims were extracted).
    5. `PASSED`: All verifiable factual claims are supported (at least 1 verifiable factual claim extracted, 0 unsupported claims, 0 factual gaps, matching symbol/snapshot window). Qualitative assumptions are noted in the summary but do not degrade `PASSED` to `INCONCLUSIVE`.
- **Handling of All Proposal Representations:**
  - Added import `PendingProposal` in `tradepilot/agents/evidence_auditor.py`.
  - Added full `PendingProposal` support to `_extract_proposal_fields()`, extracting from `proposal.proposal_snapshot` and capturing `expected_snapshot_timestamp=proposal.snapshot_timestamp`.
  - Verified `StrategyProposal`, `TradeProposal`, `TradeRecord`, `PendingProposal`, and plain `dict`.
- **Test Corrections Under Test-Versus-Semantics Rule:**
  - `test_audit_absent_portfolio_not_treated_as_false`: Updated line 257 from `assert report.overall_status == AuditStatus.PASSED` to `assert report.overall_status == AuditStatus.INCONCLUSIVE`. When cash is explicitly claimed in the proposal evidence, missing portfolio state represents a genuine factual evidence gap, which must report `INCONCLUSIVE`.
  - `test_journal_isolation`: Added `sample_portfolio` fixture and passed `portfolio=sample_portfolio` to `auditor.audit()` so that `Cash: 10000` is verified against portfolio state, correctly asserting `AuditStatus.PASSED`.
  - `test_api_trace_includes_evidence_audit`: Passed `portfolio=sample_portfolio` (already present in test arguments) to `auditor.audit()` so that the audit is complete and produces `AuditStatus.PASSED`.
- **New Regression Tests Added to `tests/test_evidence_auditor.py`:**
  - `test_audit_pending_proposal_shape`: Verifies `PendingProposal` is cleanly extracted and audited.
  - `test_precedence_missing_evidence_over_mismatch`: Verifies `snapshot is None` yields `MISSING_EVIDENCE` even if symbol mismatch occurs.
  - `test_precedence_stale_or_mismatch_over_failed`: Verifies snapshot symbol mismatch yields `STALE_OR_MISMATCHED` over unsupported factual claims.
  - `test_precedence_failed_over_inconclusive`: Verifies unsupported claim yields `FAILED` even when absent indicators (RSI) coexist.
  - `test_precedence_inconclusive_over_passed`: Verifies absent indicator (RSI) yields `INCONCLUSIVE` despite supported price claim.
  - `test_passed_with_qualitative_assumptions`: Verifies qualitative forward-looking assumptions do not degrade an otherwise supported audit to `INCONCLUSIVE`.
- **Test Results:** 33 passed, 0 failed in 2.16s.
- **Database Checksum:** Verified unchanged (`45F09E205997B3215A1FCBFD4ADF156E7BF1D2CA1A076CA40D334F2C3677950B`).

## 6. Phase C: Focused Cleanup & Repository Protection
- **Audit of `tradepilot/core/orchestrator.py` and `main.py`:**
  - Evaluated code references: Neither is imported by the active FastAPI application (`tradepilot/api.py`), CLI (`tradepilot/cli.py`), or test suite (`tests/`).
  - Both represented early Step 1 prototype implementations of a synchronous loop without human-in-the-loop approval, database persistence, evidence audits, or decision traces.
  - Per the prompt retention rule ("Do not delete anything unless you have proved it is completely unreferenced across the codebase, tests and documentation"), `orchestrator.py` is referenced in `README.md`'s architectural tree.
  - Action taken:
    - Retained both files and documented them with comprehensive module docstrings marking them as legacy prototype demonstration components.
    - Fixed `ExecutionResult` field instantiation in `Orchestrator` (`thesis_id`, `client_order_id`, `quantity`, `order_type`, `average_fill_price`) so that running `uv run python main.py` executes successfully without Pydantic validation errors.
- **`.gitignore` Hardening & Test DB Isolation:**
  - Expanded `.gitignore` to explicitly ignore `data/*.db`, `data/test_*.db`, `data/demo_*.db`, `data/tradepilot.db`, `*.db-journal`, and SQLite temp files.
  - Running test suites and demo scripts will never dirty Git working tree status.
- **`data/tradepilot.db` Git Tracking Decision:**
  - Prior to Phase C, `data/tradepilot.db` was tracked in Git history, causing local paper trading state changes to show up in `git status`.
  - Decision: Stop tracking `data/tradepilot.db` in Git (`git rm --cached data/tradepilot.db`) while strictly preserving the working tree file on disk.
  - Verified SHA-256 checksum before untracking: `45F09E205997B3215A1FCBFD4ADF156E7BF1D2CA1A076CA40D334F2C3677950B`.
  - Verified SHA-256 checksum after untracking: `45F09E205997B3215A1FCBFD4ADF156E7BF1D2CA1A076CA40D334F2C3677950B`.
  - Result: Local user data is preserved untouched on disk, but never committed to version control.


