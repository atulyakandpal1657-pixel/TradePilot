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
