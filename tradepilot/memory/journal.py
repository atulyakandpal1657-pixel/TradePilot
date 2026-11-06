import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone
from typing import List, Optional
from tradepilot.core.models import (
    TradeRecord, PendingProposal, TradeLifecycleStatus, ExecutionResult,
    EvidenceAuditReport
)

class TradeJournal:
    """
    SQLite-backed journal for TradeRecords.
    """
    def __init__(self, db_path: str = "data/tradepilot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    trade_id TEXT PRIMARY KEY,
                    thesis_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    data JSON
                )
            """)
            # pending_proposals: pre-execution risk-approved proposals (Step 4).
            # trade_records is NOT modified — this is a new, additive table.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_proposals (
                    trade_id          TEXT PRIMARY KEY,
                    thesis_id         TEXT NOT NULL,
                    symbol            TEXT NOT NULL,
                    action            TEXT NOT NULL,
                    lifecycle_status  TEXT NOT NULL DEFAULT 'RISK_APPROVED',
                    proposal_data     JSON NOT NULL,
                    risk_evaluated_at TEXT NOT NULL,
                    human_approved_at TEXT,
                    created_at        TEXT NOT NULL
                )
            """)
            # evidence_audits: Step 10 AI Decision Evidence Auditor reports.
            # Additive table — never mutates trade_records or pending_proposals.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evidence_audits (
                    audit_id          TEXT PRIMARY KEY,
                    trade_id          TEXT,
                    symbol            TEXT NOT NULL,
                    overall_status    TEXT NOT NULL,
                    audit_data        JSON NOT NULL,
                    created_at        TEXT NOT NULL
                )
            """)
            conn.commit()
            
    def save_record(self, record: TradeRecord):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trade_records (trade_id, thesis_id, symbol, action, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET data=excluded.data
                """,
                (
                    str(record.trade_id),
                    str(record.thesis_id),
                    record.symbol,
                    record.action.value,
                    record.model_dump_json()
                )
            )
            conn.commit()
            
    def get_record(self, trade_id: uuid.UUID) -> Optional[TradeRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT data FROM trade_records WHERE trade_id = ?", (str(trade_id),))
            row = cursor.fetchone()
            if row:
                return TradeRecord.model_validate_json(row[0])
            return None

    def get_record_by_alpaca_order_id(self, alpaca_order_id: str) -> Optional[TradeRecord]:
        """Look up a TradeRecord by Alpaca's own order UUID (stored in data JSON)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM trade_records WHERE json_extract(data, '$.alpaca_order_id') = ?",
                (alpaca_order_id,)
            )
            row = cursor.fetchone()
            if row:
                return TradeRecord.model_validate_json(row[0])
            return None

    def list_all_records(self) -> List[TradeRecord]:
        """Return all TradeRecords, newest first (by rowid)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM trade_records ORDER BY rowid DESC"
            )
            return [TradeRecord.model_validate_json(row[0]) for row in cursor.fetchall()]

    # ── Pending Proposals (Step 4) ────────────────────────────────────────────

    def save_pending_proposal(self, proposal: PendingProposal) -> None:
        """Persist a new RISK_APPROVED PendingProposal.

        Raises sqlite3.IntegrityError if trade_id already exists (PKe constraint).
        trade_records is never written to by this method.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO pending_proposals
                    (trade_id, thesis_id, symbol, action, lifecycle_status,
                     proposal_data, risk_evaluated_at, human_approved_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(proposal.trade_id),
                    str(proposal.thesis_id),
                    proposal.symbol,
                    proposal.action.value,
                    proposal.lifecycle_status.value,
                    proposal.model_dump_json(),
                    proposal.risk_evaluated_at.isoformat(),
                    proposal.human_approved_at.isoformat() if proposal.human_approved_at else None,
                    proposal.created_at.isoformat(),
                )
            )
            conn.commit()

    def get_pending_proposal(self, trade_id: uuid.UUID) -> Optional[PendingProposal]:
        """Retrieve a PendingProposal by trade_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT proposal_data FROM pending_proposals WHERE trade_id = ?",
                (str(trade_id),)
            )
            row = cursor.fetchone()
            if row:
                return PendingProposal.model_validate_json(row[0])
            return None

    def list_all_pending_proposals(self) -> List[PendingProposal]:
        """Return all PendingProposals, newest first (by rowid)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT proposal_data FROM pending_proposals ORDER BY rowid DESC"
            )
            return [PendingProposal.model_validate_json(row[0]) for row in cursor.fetchall()]

    def approve_pending_proposal(self, trade_id: uuid.UUID, approved_at: datetime) -> bool:
        """Atomically transition a proposal from RISK_APPROVED to HUMAN_APPROVED.

        Uses a conditional UPDATE so that only one concurrent request succeeds:

            UPDATE ... WHERE trade_id = ? AND lifecycle_status = 'RISK_APPROVED'

        Returns True if exactly one row was updated (success).
        Returns False if zero rows were updated (already approved or not found).

        trade_records is never written to by this method.
        """
        approved_at_iso = approved_at.isoformat()
        # We must also update the JSON blob so that model_validate_json reflects
        # the new state when the record is later retrieved.
        with sqlite3.connect(self.db_path) as conn:
            # First fetch existing proposal_data to update the JSON blob
            cursor = conn.execute(
                "SELECT proposal_data FROM pending_proposals WHERE trade_id = ? AND lifecycle_status = 'RISK_APPROVED'",
                (str(trade_id),)
            )
            row = cursor.fetchone()
            if not row:
                return False
            data = json.loads(row[0])
            data['lifecycle_status'] = TradeLifecycleStatus.HUMAN_APPROVED.value
            data['human_approved_at'] = approved_at_iso
            updated_json = json.dumps(data)

            result = conn.execute(
                """
                UPDATE pending_proposals
                   SET lifecycle_status  = 'HUMAN_APPROVED',
                       human_approved_at = ?,
                       proposal_data     = ?
                 WHERE trade_id = ?
                   AND lifecycle_status = 'RISK_APPROVED'
                """,
                (approved_at_iso, updated_json, str(trade_id))
            )
            conn.commit()
            return result.rowcount == 1

    # ── Step 5: Execution lifecycle ───────────────────────────────────────────

    def transition_to_execution_pending(self, trade_id: uuid.UUID) -> bool:
        """Atomically transition a proposal from HUMAN_APPROVED to EXECUTION_PENDING.

        Uses a conditional UPDATE so that only one concurrent request succeeds:

            UPDATE pending_proposals
               SET lifecycle_status = 'EXECUTION_PENDING'
             WHERE trade_id = ?
               AND lifecycle_status = 'HUMAN_APPROVED'

        Returns True if exactly one row was updated (transition succeeded).
        Returns False if zero rows were updated (already EXECUTION_PENDING,
        EXECUTED, or not found — caller must inspect current status to distinguish).

        trade_records is never written to by this method.
        """
        with sqlite3.connect(self.db_path) as conn:
            # Update both the column and the JSON blob atomically.
            cursor = conn.execute(
                """
                SELECT proposal_data FROM pending_proposals
                 WHERE trade_id = ?
                   AND lifecycle_status = 'HUMAN_APPROVED'
                """,
                (str(trade_id),)
            )
            row = cursor.fetchone()
            if not row:
                return False
            data = json.loads(row[0])
            data['lifecycle_status'] = TradeLifecycleStatus.EXECUTION_PENDING.value
            updated_json = json.dumps(data)

            result = conn.execute(
                """
                UPDATE pending_proposals
                   SET lifecycle_status = 'EXECUTION_PENDING',
                       proposal_data    = ?
                 WHERE trade_id = ?
                   AND lifecycle_status = 'HUMAN_APPROVED'
                """,
                (updated_json, str(trade_id))
            )
            conn.commit()
            return result.rowcount == 1

    def mark_proposal_executed(self, trade_id: uuid.UUID) -> None:
        """Transition a proposal from EXECUTION_PENDING to EXECUTED.

        Called after a successful TradeRecord has been persisted.  Tolerates
        the case where the row is already EXECUTED (idempotent).
        trade_records is never written to by this method.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT proposal_data FROM pending_proposals WHERE trade_id = ?",
                (str(trade_id),)
            )
            row = cursor.fetchone()
            if not row:
                return  # already gone — nothing to do
            data = json.loads(row[0])
            data['lifecycle_status'] = TradeLifecycleStatus.EXECUTED.value
            updated_json = json.dumps(data)

            conn.execute(
                """
                UPDATE pending_proposals
                   SET lifecycle_status = 'EXECUTED',
                       proposal_data    = ?
                 WHERE trade_id = ?
                """,
                (updated_json, str(trade_id))
            )
            conn.commit()

    # ── Step 10: Evidence Audit Persistence ───────────────────────────────────

    def save_evidence_audit(
        self,
        report: EvidenceAuditReport,
        trade_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Persist an EvidenceAuditReport.

        Safe and additive: writes only to the evidence_audits table.
        Does NOT mutate trade_records or pending_proposals.
        """
        tid_str = str(trade_id) if trade_id else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO evidence_audits
                    (audit_id, trade_id, symbol, overall_status, audit_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(audit_id) DO UPDATE SET
                    audit_data = excluded.audit_data,
                    overall_status = excluded.overall_status
                """,
                (
                    str(report.audit_id),
                    tid_str,
                    report.symbol,
                    report.overall_status.value,
                    report.model_dump_json(),
                    report.timestamp.isoformat(),
                )
            )
            conn.commit()

    def get_evidence_audit(self, audit_id: uuid.UUID) -> Optional[EvidenceAuditReport]:
        """Retrieve an EvidenceAuditReport by its audit_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT audit_data FROM evidence_audits WHERE audit_id = ?",
                (str(audit_id),)
            )
            row = cursor.fetchone()
            if row:
                return EvidenceAuditReport.model_validate_json(row[0])
            return None

    def get_evidence_audit_by_trade_id(self, trade_id: uuid.UUID) -> Optional[EvidenceAuditReport]:
        """Retrieve the most recent EvidenceAuditReport linked to a trade_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT audit_data FROM evidence_audits WHERE trade_id = ? ORDER BY rowid DESC LIMIT 1",
                (str(trade_id),)
            )
            row = cursor.fetchone()
            if row:
                return EvidenceAuditReport.model_validate_json(row[0])
            return None

    def list_evidence_audits(self, limit: int = 50) -> List[EvidenceAuditReport]:
        """List recent EvidenceAuditReports, newest first."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT audit_data FROM evidence_audits ORDER BY rowid DESC LIMIT ?",
                (limit,)
            )
            return [EvidenceAuditReport.model_validate_json(row[0]) for row in cursor.fetchall()]
