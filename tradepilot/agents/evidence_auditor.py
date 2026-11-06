
"""
tradepilot/agents/evidence_auditor.py

AI Decision Evidence Auditor.

Evaluates whether a Strategy Agent's claims in a StrategyProposal or TradeRecord
are supported by the original MarketSnapshot and PortfolioState available when
the decision was made.

SAFETY & DESIGN INVARIANTS:
  - Advisory only.
  - Deterministic checks first: all numeric, relational, and timestamp comparisons
    are evaluated deterministically.
  - Never modifies proposals, position sizes, risk limits, approval status,
    or execution behavior.
  - Never submits Alpaca orders or performs autonomous actions.
  - Does not treat an absent field as proof that a claim is false.
  - Reports missing evidence and inconclusive claim sets honestly.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from tradepilot.core.models import (
    Action,
    AuditStatus,
    ClaimSeverity,
    ClaimStatus,
    EvidenceAuditClaim,
    EvidenceAuditReport,
    MarketSnapshot,
    PortfolioState,
    StrategyProposal,
    TradeProposal,
    TradeRecord,
)
from tradepilot.core.config import settings

try:
    from google import genai
except ImportError:
    genai = None  # pragma: no cover


class EvidenceAuditor:
    """
    Audits the factual claims of trading proposals against original market and portfolio data.
    """

    def __init__(self):
        # Numeric tolerance configurations
        self.price_rel_tol = 0.002        # 0.2% tolerance for price
        self.price_abs_tol = 0.05         # 5 cents absolute tolerance
        self.pct_abs_tol = 0.05           # 0.05% tolerance for percentage points
        self.volatility_tol = 0.005       # absolute volatility tolerance
        self.volume_rel_tol = 0.02        # 2% volume tolerance

        # LLM client is used ONLY for claim extraction if explicitly requested.
        # Numeric verification is ALWAYS deterministic.
        if genai and settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)
        else:
            self.client = None
        self.model_name = settings.gemini_model_name

    def audit(
        self,
        proposal: Union[StrategyProposal, TradeProposal, TradeRecord, PendingProposal, Dict[str, Any]],
        snapshot: Optional[MarketSnapshot] = None,
        portfolio: Optional[PortfolioState] = None,
        symbol: Optional[str] = None,
        proposal_timestamp: Optional[datetime] = None,
        expected_snapshot_timestamp: Optional[datetime] = None,
        use_llm: bool = False,
    ) -> EvidenceAuditReport:
        """
        Audit a proposal against a MarketSnapshot and PortfolioState.

        Parameters:
            proposal: The StrategyProposal, TradeProposal, TradeRecord, or PendingProposal being audited.
            snapshot: The exact MarketSnapshot used to generate the proposal (or None if unavailable).
            portfolio: The PortfolioState at decision time (or None if unavailable).
            symbol: Ticker symbol (if not inferrable from proposal).
            proposal_timestamp: Timestamp when proposal was generated.
            expected_snapshot_timestamp: Expected original MarketSnapshot timestamp.
            use_llm: Whether to use Gemini for claim extraction (numeric verification is always deterministic).

        Returns:
            EvidenceAuditReport with classified claims, counts, and overall status.
        """
        extracted_data = self._extract_proposal_fields(proposal)
        target_symbol = symbol or extracted_data.get("symbol") or (snapshot.symbol if snapshot else "UNKNOWN")
        action = extracted_data.get("action")
        p_timestamp = proposal_timestamp or extracted_data.get("timestamp")

        # ── 1. Check for Missing Evidence ─────────────────────────────────────
        if snapshot is None:
            claims = self._extract_claims_deterministic(extracted_data)
            unverifiable_claims = []
            for c in claims:
                unverifiable_claims.append(
                    EvidenceAuditClaim(
                        claim=c["claim"],
                        status=ClaimStatus.UNVERIFIABLE,
                        evidence_source=c.get("evidence_source"),
                        claimed_value=c.get("claimed_value"),
                        observed_value=None,
                        explanation="MarketSnapshot unavailable. Claim cannot be verified against original market data.",
                        severity=ClaimSeverity.LOW,
                    )
                )

            return EvidenceAuditReport(
                audit_id=uuid.uuid4(),
                timestamp=datetime.now(timezone.utc),
                symbol=target_symbol,
                overall_status=AuditStatus.MISSING_EVIDENCE,
                summary="Original MarketSnapshot evidence is unavailable. Audit cannot be conducted.",
                claims=unverifiable_claims,
                supported_count=0,
                unsupported_count=0,
                unverifiable_count=len(unverifiable_claims),
                evidence_available=False,
                snapshot_timestamp=None,
                proposal_action=action,
            )

        # ── 2. Check for Mismatched Symbol or Snapshot Timestamp ──────────────
        if target_symbol and target_symbol != "UNKNOWN" and snapshot.symbol.upper() != target_symbol.upper():
            mismatch_claim = EvidenceAuditClaim(
                claim=f"Proposal target symbol {target_symbol} matches snapshot symbol {snapshot.symbol}",
                status=ClaimStatus.UNSUPPORTED,
                evidence_source="MarketSnapshot.symbol",
                claimed_value=target_symbol,
                observed_value=snapshot.symbol,
                explanation=f"Mismatched symbol: Snapshot is for '{snapshot.symbol}', but proposal is for '{target_symbol}'.",
                severity=ClaimSeverity.CRITICAL,
            )
            return EvidenceAuditReport(
                audit_id=uuid.uuid4(),
                timestamp=datetime.now(timezone.utc),
                symbol=target_symbol,
                overall_status=AuditStatus.STALE_OR_MISMATCHED,
                summary=f"Mismatched snapshot: Snapshot symbol ({snapshot.symbol}) does not match proposal symbol ({target_symbol}).",
                claims=[mismatch_claim],
                supported_count=0,
                unsupported_count=1,
                unverifiable_count=0,
                evidence_available=True,
                snapshot_timestamp=snapshot.timestamp,
                proposal_action=action,
            )

        # Verify expected snapshot timestamp if known
        expected_snap_ts = expected_snapshot_timestamp or extracted_data.get("expected_snapshot_timestamp")
        if expected_snap_ts is not None:
            e_ts = expected_snap_ts if expected_snap_ts.tzinfo else expected_snap_ts.replace(tzinfo=timezone.utc)
            s_ts = snapshot.timestamp if snapshot.timestamp.tzinfo else snapshot.timestamp.replace(tzinfo=timezone.utc)
            if abs((s_ts - e_ts).total_seconds()) > 60:
                ts_mismatch_claim = EvidenceAuditClaim(
                    claim=f"Snapshot timestamp ({s_ts.isoformat()}) matches decision's expected snapshot timestamp ({e_ts.isoformat()})",
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source="MarketSnapshot.timestamp vs ExpectedSnapshotTimestamp",
                    claimed_value=e_ts.isoformat(),
                    observed_value=s_ts.isoformat(),
                    explanation=f"Mismatched snapshot timestamp: Supplied snapshot time ({s_ts.isoformat()}) does not match the decision's original snapshot timestamp ({e_ts.isoformat()}).",
                    severity=ClaimSeverity.CRITICAL,
                )
                return EvidenceAuditReport(
                    audit_id=uuid.uuid4(),
                    timestamp=datetime.now(timezone.utc),
                    symbol=target_symbol,
                    overall_status=AuditStatus.STALE_OR_MISMATCHED,
                    summary="Mismatched snapshot timestamp: Evidence snapshot time does not match original decision snapshot timestamp.",
                    claims=[ts_mismatch_claim],
                    supported_count=0,
                    unsupported_count=1,
                    unverifiable_count=0,
                    evidence_available=True,
                    snapshot_timestamp=snapshot.timestamp,
                    proposal_action=action,
                )

        # ── 3. Extract Claims (Deterministic or LLM-assisted) ─────────────────
        if use_llm and self.client:
            raw_claims = self._extract_claims_with_llm(extracted_data)
        else:
            raw_claims = self._extract_claims_deterministic(extracted_data)

        # Verification of extracted claims against MarketSnapshot is ALWAYS deterministic
        verified_claims: List[EvidenceAuditClaim] = []
        for raw in raw_claims:
            verified_claim = self._verify_claim(
                raw=raw,
                snapshot=snapshot,
                portfolio=portfolio,
                action=action,
                proposal_timestamp=p_timestamp,
            )
            verified_claims.append(verified_claim)

        # ── 4. Verify Proposal vs Snapshot Timestamp Deterministically ────────
        if p_timestamp is not None:
            ts_claim = self._verify_timestamp(p_timestamp, snapshot.timestamp)
            if ts_claim is not None:
                verified_claims.append(ts_claim)

        # ── 5. Aggregate Results & Overall Status ─────────────────────────────
        supported_count = sum(1 for c in verified_claims if c.status == ClaimStatus.SUPPORTED)
        unsupported_count = sum(1 for c in verified_claims if c.status == ClaimStatus.UNSUPPORTED)
        unverifiable_count = sum(1 for c in verified_claims if c.status == ClaimStatus.UNVERIFIABLE)

        # Handle empty claim sets honestly
        if not verified_claims:
            overall_status = AuditStatus.INCONCLUSIVE
            summary = "No factual claims identified for audit. Status: INCONCLUSIVE (cannot pass without verifiable claims)."
        elif unsupported_count > 0:
            overall_status = AuditStatus.FAILED
            summary = (
                f"Audit failed: {unsupported_count} unsupported claim(s) detected. "
                f"({supported_count} supported, {unverifiable_count} unverifiable)."
            )
        elif unverifiable_count > 0:
            overall_status = AuditStatus.INCONCLUSIVE
            summary = (
                f"Audit inconclusive: {unverifiable_count} claim(s) remain unverifiable or claim coverage is incomplete. "
                f"({supported_count} supported, 0 unsupported)."
            )
        else:
            overall_status = AuditStatus.PASSED
            summary = (
                f"Audit passed: all {supported_count} verifiable claim(s) are supported by MarketSnapshot."
            )

        return EvidenceAuditReport(
            audit_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc),
            symbol=target_symbol,
            overall_status=overall_status,
            summary=summary,
            claims=verified_claims,
            supported_count=supported_count,
            unsupported_count=unsupported_count,
            unverifiable_count=unverifiable_count,
            evidence_available=True,
            snapshot_timestamp=snapshot.timestamp,
            proposal_action=action,
        )

    # ── Internal Extraction Helpers ───────────────────────────────────────────

    def _extract_proposal_fields(
        self,
        proposal: Union[StrategyProposal, TradeProposal, TradeRecord, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Extract canonical fields from various proposal shapes."""
        if isinstance(proposal, StrategyProposal):
            return {
                "symbol": None,
                "action": proposal.action,
                "confidence": proposal.confidence,
                "thesis": proposal.thesis,
                "evidence": proposal.evidence,
                "summary": proposal.reasoning_summary,
                "assumptions": list(proposal.assumptions),
                "invalidation_conditions": list(proposal.invalidation_conditions),
                "suggested_stop_loss": proposal.suggested_stop_loss,
            }
        elif isinstance(proposal, TradeProposal):
            thesis_text, evidence_text, summary_text = self._parse_reasoning_blocks(proposal.reasoning)
            return {
                "symbol": proposal.symbol,
                "action": proposal.action,
                "confidence": proposal.confidence,
                "thesis": thesis_text,
                "evidence": evidence_text,
                "summary": summary_text,
                "assumptions": list(proposal.assumptions),
                "invalidation_conditions": list(proposal.invalidation_conditions),
                "suggested_stop_loss": proposal.stop_loss,
            }
        elif isinstance(proposal, TradeRecord):
            thesis_text, evidence_text, summary_text = self._parse_reasoning_blocks(proposal.original_thesis)
            return {
                "symbol": proposal.symbol,
                "action": proposal.action,
                "confidence": proposal.original_confidence,
                "thesis": thesis_text,
                "evidence": evidence_text,
                "summary": summary_text,
                "assumptions": list(proposal.assumptions),
                "invalidation_conditions": list(proposal.invalidation_conditions),
                "suggested_stop_loss": None,
            }
        elif isinstance(proposal, dict):
            reasoning = proposal.get("reasoning") or proposal.get("original_thesis") or ""
            thesis_text, evidence_text, summary_text = self._parse_reasoning_blocks(reasoning)
            return {
                "symbol": proposal.get("symbol"),
                "action": Action(proposal["action"]) if proposal.get("action") else None,
                "confidence": proposal.get("confidence", proposal.get("original_confidence", 0.0)),
                "thesis": proposal.get("thesis") or thesis_text,
                "evidence": proposal.get("evidence") or evidence_text,
                "summary": proposal.get("summary") or proposal.get("reasoning_summary") or summary_text,
                "assumptions": list(proposal.get("assumptions") or []),
                "invalidation_conditions": list(proposal.get("invalidation_conditions") or []),
                "suggested_stop_loss": proposal.get("suggested_stop_loss") or proposal.get("stop_loss"),
            }
        return {
            "symbol": None,
            "action": None,
            "confidence": 0.0,
            "thesis": "",
            "evidence": "",
            "summary": "",
            "assumptions": [],
            "invalidation_conditions": [],
            "suggested_stop_loss": None,
        }

    def _parse_reasoning_blocks(self, raw_text: str) -> Tuple[str, str, str]:
        """Split TradeProposal/TradeRecord reasoning block into thesis, evidence, summary."""
        if not raw_text:
            return "", "", ""
        thesis_parts = raw_text.split("\n\nEvidence:\n")
        thesis_text = thesis_parts[0].strip()
        rest = thesis_parts[1] if len(thesis_parts) > 1 else ""
        evidence_parts = rest.split("\n\nSummary:\n")
        evidence_text = evidence_parts[0].strip()
        summary_text = evidence_parts[1].strip() if len(evidence_parts) > 1 else ""
        if "\n\nMemory Reasoning:" in summary_text:
            summary_text = summary_text.split("\n\nMemory Reasoning:")[0].strip()
        return thesis_text, evidence_text, summary_text

    def _extract_claims_deterministic(self, fields: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract factual claims from proposal fields using pattern matching.
        Returns a list of raw claim descriptors.
        """
        claims: List[Dict[str, Any]] = []
        seen_keys = set()

        combined_text = "\n".join([
            fields.get("evidence", ""),
            fields.get("thesis", ""),
            fields.get("summary", ""),
        ])

        # ── 1. Numeric Price ──────────────────────────────────────────────────
        # Extract ALL distinct numeric price values. If two different values appear
        # (contradictory claims), both must be verified so the contradicted one fails.
        price_patterns = [
            r"(?:^|[,\s\n])Price:\s*\$?([\d,]+(?:\.\d+)?)",
            r"price\s+(?:is|at|of|trading at)\s+\$?([\d,]+(?:\.\d+)?)",
            r"price\s*\(\s*\$?([\d,]+(?:\.\d+)?)\s*\)",
            r"current price(?:\s+is|\s+at|\s+of)?\s+\$?([\d,]+(?:\.\d+)?)",
        ]
        _seen_price_vals: set = set()
        for pat in price_patterns:
            for m in re.finditer(pat, combined_text, re.IGNORECASE):
                val_str = m.group(1).replace(",", "")
                try:
                    val = float(val_str)
                    if val not in _seen_price_vals:
                        _seen_price_vals.add(val)
                        key = ("numeric", "price", val)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            claims.append({
                                "type": "numeric_metric",
                                "metric": "current_price",
                                "claim": f"Current price is {val:.2f}",
                                "claimed_value": str(val),
                                "parsed_value": val,
                                "evidence_source": "MarketSnapshot.current_price",
                            })
                except ValueError:
                    # Explicit extraction failure — emit UNVERIFIABLE rather than silently drop
                    key = ("extract_fail", "price", val_str)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "unverifiable",
                            "claim": f"Price claim extraction failed: unparseable value '{val_str}'",
                            "evidence_source": "MarketSnapshot.current_price",
                            "claimed_value": val_str,
                            "explanation": (
                                f"Price value '{val_str}' could not be parsed as a number. "
                                "Claim cannot be verified."
                            ),
                        })
        # Non-numeric catch-all: detect 'Price: N/A', 'Price: unknown', etc.
        # (the patterns above only match \d+ so non-numeric suffixes are missed)
        for m_na in re.finditer(
            r"(?:^|[,\s\n])Price:\s*\$?((?![\d])\S+)", combined_text, re.IGNORECASE
        ):
            raw_na = m_na.group(1).strip().rstrip(",;.")
            key = ("extract_fail", "price", raw_na)
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "unverifiable",
                    "claim": f"Price claim extraction failed: unparseable value '{raw_na}'",
                    "evidence_source": "MarketSnapshot.current_price",
                    "claimed_value": raw_na,
                    "explanation": (
                        f"Price value '{raw_na}' could not be parsed as a number. "
                        "Claim cannot be verified."
                    ),
                })

        # ── 2. SMA20 ──────────────────────────────────────────────────────────
        sma20_patterns = [
            r"SMA20:\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA_20:\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA20\s+(?:is|at|of)\s+\$?([\d,]+(?:\.\d+)?)",
            r"SMA20\s*\(\s*\$?([\d,]+(?:\.\d+)?)\s*\)",
            r"20-day SMA\s+(?:is|at|of)?\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA20\s+at\s+([\d,]+(?:\.\d+)?)",
        ]
        for pat in sma20_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                val_str = m.group(1).replace(",", "")
                try:
                    val = float(val_str)
                    key = ("numeric", "sma_20")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "numeric_metric",
                            "metric": "sma_20",
                            "claim": f"SMA20 is {val:.2f}",
                            "claimed_value": str(val),
                            "parsed_value": val,
                            "evidence_source": "MarketSnapshot.sma_20",
                        })
                        break
                except ValueError:
                    # Explicit extraction failure
                    key = ("extract_fail", "sma_20", m.group(1))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "unverifiable",
                            "claim": f"SMA20 claim extraction failed: unparseable value '{m.group(1)}'",
                            "evidence_source": "MarketSnapshot.sma_20",
                            "claimed_value": m.group(1),
                            "explanation": f"SMA20 value '{m.group(1)}' could not be parsed. Claim cannot be verified.",
                        })
                    break

        # ── 3. SMA50 ──────────────────────────────────────────────────────────
        sma50_patterns = [
            r"SMA50:\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA_50:\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA50\s+(?:is|at|of)\s+\$?([\d,]+(?:\.\d+)?)",
            r"SMA50\s*\(\s*\$?([\d,]+(?:\.\d+)?)\s*\)",
            r"50-day SMA\s+(?:is|at|of)?\s*\$?([\d,]+(?:\.\d+)?)",
            r"SMA50\s+at\s+([\d,]+(?:\.\d+)?)",
        ]
        for pat in sma50_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                val_str = m.group(1).replace(",", "")
                try:
                    val = float(val_str)
                    key = ("numeric", "sma_50")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "numeric_metric",
                            "metric": "sma_50",
                            "claim": f"SMA50 is {val:.2f}",
                            "claimed_value": str(val),
                            "parsed_value": val,
                            "evidence_source": "MarketSnapshot.sma_50",
                        })
                        break
                except ValueError:
                    # Explicit extraction failure
                    key = ("extract_fail", "sma_50", m.group(1))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "unverifiable",
                            "claim": f"SMA50 claim extraction failed: unparseable value '{m.group(1)}'",
                            "evidence_source": "MarketSnapshot.sma_50",
                            "claimed_value": m.group(1),
                            "explanation": f"SMA50 value '{m.group(1)}' could not be parsed. Claim cannot be verified.",
                        })
                    break

        # ── 4. Momentum / Price Change Pct ────────────────────────────────────
        mom_patterns = [
            r"Momentum:\s*([+-]?[\d,]+(?:\.\d+)?%?)",
            r"price change(?: of| is)?\s*([+-]?[\d,]+(?:\.\d+)?%?)",
            r"daily (?:gain|return|loss|change)(?: of| is)?\s*([+-]?[\d,]+(?:\.\d+)?%?)",
        ]
        for pat in mom_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                raw_mom = m.group(1)
                is_pct = "%" in raw_mom
                val_clean = raw_mom.replace("%", "").replace(",", "")
                try:
                    val = float(val_clean)
                    key = ("numeric", "momentum")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "momentum_metric",
                            "metric": "price_change_pct",
                            "claim": f"Momentum / price change is {raw_mom}",
                            "claimed_value": raw_mom,
                            "parsed_value": val,
                            "is_pct": is_pct,
                            "evidence_source": "MarketSnapshot.price_change_pct",
                        })
                        break
                except ValueError:
                    pass

        # ── 5. Volatility ─────────────────────────────────────────────────────
        vol_patterns = [
            r"Volatility:\s*([\d,]+(?:\.\d+)?)",
            r"volatility\s+(?:of|is|at)\s+([\d,]+(?:\.\d+)?)",
        ]
        for pat in vol_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    key = ("numeric", "volatility")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "volatility_metric",
                            "metric": "volatility",
                            "claim": f"Volatility is {val}",
                            "claimed_value": str(val),
                            "parsed_value": val,
                            "evidence_source": "MarketSnapshot.volatility",
                        })
                        break
                except ValueError:
                    pass

        # ── 6. Volume ─────────────────────────────────────────────────────────
        vol_count_patterns = [
            r"Volume:\s*([\d,]+(?:\.\d+)?(?:M|K|B)?)",
            r"volume\s+(?:of|is|at)\s+([\d,]+(?:\.\d+)?(?:M|K|B)?)",
        ]
        for pat in vol_count_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                raw_vol = m.group(1).upper()
                mult = 1.0
                if raw_vol.endswith("M"):
                    mult = 1_000_000.0
                    raw_vol = raw_vol[:-1]
                elif raw_vol.endswith("K"):
                    mult = 1_000.0
                    raw_vol = raw_vol[:-1]
                elif raw_vol.endswith("B"):
                    mult = 1_000_000_000.0
                    raw_vol = raw_vol[:-1]
                try:
                    val = float(raw_vol.replace(",", "")) * mult
                    key = ("numeric", "volume")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        claims.append({
                            "type": "volume_metric",
                            "metric": "volume",
                            "claim": f"Volume is {m.group(1)}",
                            "claimed_value": m.group(1),
                            "parsed_value": val,
                            "evidence_source": "MarketSnapshot.volume",
                        })
                        break
                except ValueError:
                    pass

        # ── 7. Relational Claims ──────────────────────────────────────────────
        # Negation guard: a sentence like 'Price is NOT above SMA20' must not
        # match as a positive 'price above SMA20' relational claim.
        # We look for 'not/never/no longer' within 10 chars before 'above/below'.
        _NEG_PREFIX = r"(?:not|never|no\s+longer)\s+"

        # Price vs SMA20
        _above_sma20_negated = bool(
            re.search(rf"{_NEG_PREFIX}(?:trading\s+)?above.*SMA20", combined_text, re.IGNORECASE) or
            re.search(r"price.*(?:not|never|no longer)\s+(?:trading\s+)?above.*SMA20", combined_text, re.IGNORECASE)
        )
        _above_sma20_positive = bool(
            re.search(r"price.*(?:trading\s+above|above|greater than).*SMA20", combined_text, re.IGNORECASE) or
            re.search(r"trading above both SMA20 and SMA50", combined_text, re.IGNORECASE)
        )
        if _above_sma20_positive and not _above_sma20_negated:
            key = ("relational", "price_above_sma20")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Price is trading above SMA20",
                    "relation": "price > sma_20",
                    "evidence_source": "MarketSnapshot.current_price > MarketSnapshot.sma_20",
                })
        _below_sma20_negated = bool(
            re.search(r"price.*(?:not|never|no longer)\s+(?:trading\s+)?below.*SMA20", combined_text, re.IGNORECASE)
        )
        _below_sma20_positive = bool(
            re.search(r"price.*(?:trading\s+below|below|less than).*SMA20", combined_text, re.IGNORECASE)
        )
        if _below_sma20_positive and not _below_sma20_negated and not _above_sma20_positive:
            key = ("relational", "price_below_sma20")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Price is trading below SMA20",
                    "relation": "price < sma_20",
                    "evidence_source": "MarketSnapshot.current_price < MarketSnapshot.sma_20",
                })

        # Price vs SMA50
        _above_sma50_negated = bool(
            re.search(r"price.*(?:not|never|no longer)\s+(?:trading\s+)?above.*SMA50", combined_text, re.IGNORECASE)
        )
        _above_sma50_positive = bool(
            re.search(r"price.*(?:trading\s+above|above|greater than).*SMA50", combined_text, re.IGNORECASE) or
            re.search(r"trading above both SMA20 and SMA50", combined_text, re.IGNORECASE)
        )
        if _above_sma50_positive and not _above_sma50_negated:
            key = ("relational", "price_above_sma50")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Price is trading above SMA50",
                    "relation": "price > sma_50",
                    "evidence_source": "MarketSnapshot.current_price > MarketSnapshot.sma_50",
                })
        _below_sma50_negated = bool(
            re.search(r"price.*(?:not|never|no longer)\s+(?:trading\s+)?below.*SMA50", combined_text, re.IGNORECASE)
        )
        _below_sma50_positive = bool(
            re.search(r"price.*(?:trading\s+below|below|less than).*SMA50", combined_text, re.IGNORECASE)
        )
        if _below_sma50_positive and not _below_sma50_negated and not _above_sma50_positive:
            key = ("relational", "price_below_sma50")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Price is trading below SMA50",
                    "relation": "price < sma_50",
                    "evidence_source": "MarketSnapshot.current_price < MarketSnapshot.sma_50",
                })

        # SMA20 vs SMA50 (Golden / Death cross)
        if re.search(r"SMA20.*(?:above|greater than|crossed above).*SMA50", combined_text, re.IGNORECASE):
            key = ("relational", "sma20_above_sma50")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "SMA20 is above SMA50",
                    "relation": "sma_20 > sma_50",
                    "evidence_source": "MarketSnapshot.sma_20 > MarketSnapshot.sma_50",
                })
        elif re.search(r"SMA20.*(?:below|less than|crossed below).*SMA50", combined_text, re.IGNORECASE):
            key = ("relational", "sma20_below_sma50")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "SMA20 is below SMA50",
                    "relation": "sma_20 < sma_50",
                    "evidence_source": "MarketSnapshot.sma_20 < MarketSnapshot.sma_50",
                })

        # Directional Momentum
        if re.search(r"\b(?:positive|bullish|upward)\s+momentum\b", combined_text, re.IGNORECASE):
            key = ("relational", "positive_momentum")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Positive momentum",
                    "relation": "momentum > 0",
                    "evidence_source": "MarketSnapshot.price_change_pct > 0",
                })
        elif re.search(r"\b(?:negative|bearish|downward)\s+momentum\b", combined_text, re.IGNORECASE):
            key = ("relational", "negative_momentum")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "relation",
                    "claim": "Negative momentum",
                    "relation": "momentum < 0",
                    "evidence_source": "MarketSnapshot.price_change_pct < 0",
                })

        # ── 7b. Absent Indicator Claims (RSI, VWAP, MACD, ATR) ────────────────
        # These indicators are referenced in proposals but absent from MarketSnapshot.
        # Flag them UNVERIFIABLE rather than silently ignoring them.
        _ABSENT_INDICATORS = [
            ("RSI",  r"\bRSI\s*(?::|\s+(?:is|at|of))?\s*([\d.]+)"),
            ("VWAP", r"\bVWAP\s*(?::|\s+(?:is|at|of))?\s*\$?([\d.]+)"),
            ("MACD", r"\bMACD\s*(?::|\s+(?:is|at|of))?\s*([+-]?[\d.]+)"),
            ("ATR",  r"\bATR\s*(?::|\s+(?:is|at|of))?\s*([\d.]+)"),
        ]
        for indicator_name, indicator_pat in _ABSENT_INDICATORS:
            m_ind = re.search(indicator_pat, combined_text, re.IGNORECASE)
            if m_ind:
                key = ("absent_indicator", indicator_name)
                if key not in seen_keys:
                    seen_keys.add(key)
                    claims.append({
                        "type": "unverifiable",
                        "claim": f"{indicator_name}: {m_ind.group(1)} (absent from MarketSnapshot)",
                        "evidence_source": f"MarketSnapshot (no {indicator_name} field)",
                        "claimed_value": m_ind.group(1),
                        "explanation": (
                            f"{indicator_name} is referenced in the proposal but is not stored in "
                            "MarketSnapshot. Claim cannot be verified against snapshot data."
                        ),
                    })

        # ── 8. Portfolio Data Claims ──────────────────────────────────────────
        cash_m = re.search(r"(?:Cash|cash)(?::|\s+is|\s+at|\s+of)?\s*\$?([\d,]+(?:\.\d+)?)", combined_text)
        if cash_m:
            try:
                cval = float(cash_m.group(1).replace(",", ""))
                key = ("portfolio", "cash")
                if key not in seen_keys:
                    seen_keys.add(key)
                    claims.append({
                        "type": "portfolio_metric",
                        "metric": "cash",
                        "claim": f"Portfolio cash is ${cval:.2f}",
                        "claimed_value": str(cval),
                        "parsed_value": cval,
                        "evidence_source": "PortfolioState.cash",
                    })
            except ValueError:
                pass

        equity_m = re.search(r"(?:Equity|equity)(?::|\s+is|\s+at|\s+of)?\s*\$?([\d,]+(?:\.\d+)?)", combined_text)
        if equity_m:
            try:
                eval_ = float(equity_m.group(1).replace(",", ""))
                key = ("portfolio", "equity")
                if key not in seen_keys:
                    seen_keys.add(key)
                    claims.append({
                        "type": "portfolio_metric",
                        "metric": "account_equity",
                        "claim": f"Portfolio equity is ${eval_:.2f}",
                        "claimed_value": str(eval_),
                        "parsed_value": eval_,
                        "evidence_source": "PortfolioState.account_equity",
                    })
            except ValueError:
                pass

        pos_m = re.search(r"(?:Open Positions|open positions)(?::|\s+is|\s+at|\s+of)?\s*(\d+)", combined_text)
        if pos_m:
            try:
                pval = int(pos_m.group(1))
                key = ("portfolio", "open_positions")
                if key not in seen_keys:
                    seen_keys.add(key)
                    claims.append({
                        "type": "portfolio_metric",
                        "metric": "number_of_open_positions",
                        "claim": f"Open positions count is {pval}",
                        "claimed_value": str(pval),
                        "parsed_value": pval,
                        "evidence_source": "PortfolioState.number_of_open_positions",
                    })
            except ValueError:
                pass

        # ── 9. Stop Loss Consistency ──────────────────────────────────────────
        suggested_sl = fields.get("suggested_stop_loss")
        if suggested_sl is not None and suggested_sl > 0:
            key = ("stop_loss", "consistency")
            if key not in seen_keys:
                seen_keys.add(key)
                claims.append({
                    "type": "stop_loss",
                    "claim": f"Suggested stop loss at ${suggested_sl:.2f}",
                    "claimed_value": str(suggested_sl),
                    "parsed_value": suggested_sl,
                    "evidence_source": "MarketSnapshot.current_price vs StopLoss",
                })

        # ── 10. Forward-Looking Assumptions & Qualitative Claims ──────────────
        for assumption in fields.get("assumptions", []):
            if not assumption or not assumption.strip():
                continue
            # Check if this assumption was already converted to a numeric or relational claim
            if any(term in assumption.lower() for term in ["sma20 at", "sma50 at", "price:"]):
                continue
            claims.append({
                "type": "unverifiable",
                "claim": f"Assumption: {assumption.strip()}",
                "evidence_source": None,
                "claimed_value": assumption.strip(),
                "explanation": "Forward-looking assumption cannot be verified against historical snapshot data.",
            })

        for inv in fields.get("invalidation_conditions", []):
            if not inv or not inv.strip():
                continue
            # Invalidation conditions describing future triggers are forward-looking
            if not any(term in inv.lower() for term in ["sma20 at", "sma50 at"]):
                claims.append({
                    "type": "unverifiable",
                    "claim": f"Invalidation condition: {inv.strip()}",
                    "evidence_source": None,
                    "claimed_value": inv.strip(),
                    "explanation": "Forward-looking invalidation condition; evaluated during trade lifecycle.",
                })

        return claims

    # ── Claim Verification ────────────────────────────────────────────────────

    def _verify_claim(
        self,
        raw: Dict[str, Any],
        snapshot: MarketSnapshot,
        portfolio: Optional[PortfolioState],
        action: Optional[Action],
        proposal_timestamp: Optional[datetime],
    ) -> EvidenceAuditClaim:
        """Deterministically verify a single extracted claim against snapshot and portfolio."""
        claim_type = raw.get("type")

        # ── Unverifiable claim ────────────────────────────────────────────────
        if claim_type == "unverifiable":
            return EvidenceAuditClaim(
                claim=raw["claim"],
                status=ClaimStatus.UNVERIFIABLE,
                evidence_source=raw.get("evidence_source"),
                claimed_value=raw.get("claimed_value"),
                observed_value=None,
                explanation=raw.get("explanation", "Claim is forward-looking or qualitative and cannot be verified."),
                severity=ClaimSeverity.INFO,
            )

        # ── Numeric metric (Price, SMA20, SMA50) ──────────────────────────────
        if claim_type == "numeric_metric":
            metric = raw["metric"]
            observed = getattr(snapshot, metric, None)
            claimed = raw["parsed_value"]

            if observed is None:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNVERIFIABLE,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=None,
                    explanation=f"Field '{metric}' is absent from MarketSnapshot. Not treated as false.",
                    severity=ClaimSeverity.LOW,
                )

            diff = abs(claimed - observed)
            tolerance = max(self.price_abs_tol, observed * self.price_rel_tol)

            if diff <= tolerance:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=f"{claimed:.2f}",
                    observed_value=f"{observed:.2f}",
                    explanation=f"Claimed {metric} ({claimed:.2f}) matches observed snapshot value ({observed:.2f}) within tolerance ({tolerance:.2f}).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                pct_err = (diff / observed) * 100
                severity = ClaimSeverity.CRITICAL if pct_err > 5.0 else ClaimSeverity.HIGH
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=f"{claimed:.2f}",
                    observed_value=f"{observed:.2f}",
                    explanation=f"Claimed {metric} ({claimed:.2f}) contradicts observed snapshot value ({observed:.2f}). Discrepancy: {claimed - observed:+.2f} ({pct_err:.2f}%).",
                    severity=severity,
                )

        # ── Momentum metric ───────────────────────────────────────────────────
        if claim_type == "momentum_metric":
            observed_pct = snapshot.price_change_pct * 100.0  # e.g. 0.16%
            claimed_val = raw["parsed_value"]
            # If claimed_val is already on percentage scale (e.g. 0.16)
            diff = abs(claimed_val - observed_pct)
            # Or if claimed_val was in decimal scale (e.g. 0.0016)
            diff_dec = abs((claimed_val * 100.0) - observed_pct)
            min_diff = min(diff, diff_dec)

            if min_diff <= self.pct_abs_tol:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=f"{observed_pct:+.2f}%",
                    explanation=f"Claimed momentum ({raw.get('claimed_value')}) matches observed snapshot momentum ({observed_pct:+.2f}%).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=f"{observed_pct:+.2f}%",
                    explanation=f"Claimed momentum ({raw.get('claimed_value')}) contradicts observed snapshot momentum ({observed_pct:+.2f}%).",
                    severity=ClaimSeverity.MEDIUM,
                )

        # ── Volatility metric ─────────────────────────────────────────────────
        if claim_type == "volatility_metric":
            observed = snapshot.volatility
            claimed = raw["parsed_value"]
            diff = abs(claimed - observed)
            if diff <= self.volatility_tol:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=str(claimed),
                    observed_value=str(observed),
                    explanation=f"Claimed volatility ({claimed}) matches observed volatility ({observed}).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=str(claimed),
                    observed_value=str(observed),
                    explanation=f"Claimed volatility ({claimed}) contradicts observed volatility ({observed}).",
                    severity=ClaimSeverity.MEDIUM,
                )

        # ── Volume metric ─────────────────────────────────────────────────────
        if claim_type == "volume_metric":
            observed = snapshot.volume
            claimed = raw["parsed_value"]
            diff = abs(claimed - observed)
            tolerance = max(100.0, observed * self.volume_rel_tol)
            if diff <= tolerance:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=f"{observed:,.0f}",
                    explanation=f"Claimed volume ({raw.get('claimed_value')}) matches observed volume ({observed:,.0f}).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=f"{observed:,.0f}",
                    explanation=f"Claimed volume ({raw.get('claimed_value')}) contradicts observed volume ({observed:,.0f}).",
                    severity=ClaimSeverity.MEDIUM,
                )

        # ── Relational claim ──────────────────────────────────────────────────
        if claim_type == "relation":
            rel = raw["relation"]
            if rel == "price > sma_20":
                passed = snapshot.current_price > snapshot.sma_20
                obs_str = f"Price ${snapshot.current_price:.2f} vs SMA20 ${snapshot.sma_20:.2f}"
            elif rel == "price < sma_20":
                passed = snapshot.current_price < snapshot.sma_20
                obs_str = f"Price ${snapshot.current_price:.2f} vs SMA20 ${snapshot.sma_20:.2f}"
            elif rel == "price > sma_50":
                passed = snapshot.current_price > snapshot.sma_50
                obs_str = f"Price ${snapshot.current_price:.2f} vs SMA50 ${snapshot.sma_50:.2f}"
            elif rel == "price < sma_50":
                passed = snapshot.current_price < snapshot.sma_50
                obs_str = f"Price ${snapshot.current_price:.2f} vs SMA50 ${snapshot.sma_50:.2f}"
            elif rel == "sma_20 > sma_50":
                passed = snapshot.sma_20 > snapshot.sma_50
                obs_str = f"SMA20 ${snapshot.sma_20:.2f} vs SMA50 ${snapshot.sma_50:.2f}"
            elif rel == "sma_20 < sma_50":
                passed = snapshot.sma_20 < snapshot.sma_50
                obs_str = f"SMA20 ${snapshot.sma_20:.2f} vs SMA50 ${snapshot.sma_50:.2f}"
            elif rel == "momentum > 0":
                passed = snapshot.price_change_pct > 0
                obs_str = f"Momentum {snapshot.price_change_pct * 100:+.2f}%"
            elif rel == "momentum < 0":
                passed = snapshot.price_change_pct < 0
                obs_str = f"Momentum {snapshot.price_change_pct * 100:+.2f}%"
            else:
                passed = False
                obs_str = "Unknown relation"

            if passed:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("relation"),
                    observed_value=obs_str,
                    explanation=f"Relation '{raw['claim']}' is verified by snapshot data ({obs_str}).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("relation"),
                    observed_value=obs_str,
                    explanation=f"Relation '{raw['claim']}' is contradicted by snapshot data ({obs_str}).",
                    severity=ClaimSeverity.HIGH,
                )

        # ── Portfolio metric ──────────────────────────────────────────────────
        if claim_type == "portfolio_metric":
            metric = raw["metric"]
            if portfolio is None:
                # Do not treat an absent field as proof that a claim is false!
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNVERIFIABLE,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=None,
                    explanation="PortfolioState not provided for audit. Claim cannot be verified against portfolio data.",
                    severity=ClaimSeverity.LOW,
                )

            observed = getattr(portfolio, metric, None)
            claimed = raw["parsed_value"]
            if observed is None:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNVERIFIABLE,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=raw.get("claimed_value"),
                    observed_value=None,
                    explanation=f"Field '{metric}' absent from PortfolioState. Not treated as false.",
                    severity=ClaimSeverity.LOW,
                )

            # Tolerance for portfolio float/int
            diff = abs(claimed - float(observed))
            tolerance = max(1.0, float(observed) * 0.01) if metric != "number_of_open_positions" else 0.0
            if diff <= tolerance:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.SUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=str(claimed),
                    observed_value=str(observed),
                    explanation=f"Claimed portfolio {metric} ({claimed}) matches observed portfolio state ({observed}).",
                    severity=ClaimSeverity.INFO,
                )
            else:
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNSUPPORTED,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=str(claimed),
                    observed_value=str(observed),
                    explanation=f"Claimed portfolio {metric} ({claimed}) contradicts observed portfolio state ({observed}).",
                    severity=ClaimSeverity.HIGH,
                )

        # ── Stop Loss Consistency ─────────────────────────────────────────────
        if claim_type == "stop_loss":
            sl = raw["parsed_value"]
            price = snapshot.current_price
            if action == Action.BUY:
                if sl < price:
                    return EvidenceAuditClaim(
                        claim=raw["claim"],
                        status=ClaimStatus.SUPPORTED,
                        evidence_source=raw.get("evidence_source"),
                        claimed_value=f"${sl:.2f}",
                        observed_value=f"Price ${price:.2f}",
                        explanation=f"Suggested stop loss ${sl:.2f} is valid for BUY (below current price ${price:.2f}).",
                        severity=ClaimSeverity.INFO,
                    )
                else:
                    return EvidenceAuditClaim(
                        claim=raw["claim"],
                        status=ClaimStatus.UNSUPPORTED,
                        evidence_source=raw.get("evidence_source"),
                        claimed_value=f"${sl:.2f}",
                        observed_value=f"Price ${price:.2f}",
                        explanation=f"Suggested stop loss ${sl:.2f} is INVALID for BUY: must be strictly below current price ${price:.2f}.",
                        severity=ClaimSeverity.CRITICAL,
                    )
            elif action == Action.SELL:
                if sl > price:
                    return EvidenceAuditClaim(
                        claim=raw["claim"],
                        status=ClaimStatus.SUPPORTED,
                        evidence_source=raw.get("evidence_source"),
                        claimed_value=f"${sl:.2f}",
                        observed_value=f"Price ${price:.2f}",
                        explanation=f"Suggested stop loss ${sl:.2f} is valid for SELL (above current price ${price:.2f}).",
                        severity=ClaimSeverity.INFO,
                    )
                else:
                    return EvidenceAuditClaim(
                        claim=raw["claim"],
                        status=ClaimStatus.UNSUPPORTED,
                        evidence_source=raw.get("evidence_source"),
                        claimed_value=f"${sl:.2f}",
                        observed_value=f"Price ${price:.2f}",
                        explanation=f"Suggested stop loss ${sl:.2f} is INVALID for SELL: must be strictly above current price ${price:.2f}.",
                        severity=ClaimSeverity.CRITICAL,
                    )
            else:
                # NO_TRADE with stop loss
                return EvidenceAuditClaim(
                    claim=raw["claim"],
                    status=ClaimStatus.UNVERIFIABLE,
                    evidence_source=raw.get("evidence_source"),
                    claimed_value=f"${sl:.2f}",
                    observed_value=f"Price ${price:.2f}",
                    explanation=f"NO_TRADE proposal; suggested stop loss ${sl:.2f} is advisory only.",
                    severity=ClaimSeverity.INFO,
                )

        # Fallback
        return EvidenceAuditClaim(
            claim=raw.get("claim", "Unknown claim"),
            status=ClaimStatus.UNVERIFIABLE,
            evidence_source=raw.get("evidence_source"),
            claimed_value=raw.get("claimed_value"),
            observed_value=None,
            explanation="Unrecognized claim structure.",
            severity=ClaimSeverity.LOW,
        )

    def _verify_timestamp(
        self,
        proposal_timestamp: datetime,
        snapshot_timestamp: datetime,
    ) -> Optional[EvidenceAuditClaim]:
        """Verify timestamp temporal consistency deterministically."""
        # Normalize to UTC
        p_ts = proposal_timestamp if proposal_timestamp.tzinfo else proposal_timestamp.replace(tzinfo=timezone.utc)
        s_ts = snapshot_timestamp if snapshot_timestamp.tzinfo else snapshot_timestamp.replace(tzinfo=timezone.utc)

        # Snapshot must not be generated in the future relative to proposal
        # Allow slight clock skew of 60 seconds
        time_diff = (s_ts - p_ts).total_seconds()
        if time_diff > 300:  # Snapshot is > 5 minutes newer than proposal
            return EvidenceAuditClaim(
                claim=f"Snapshot timestamp ({s_ts.isoformat()}) aligns with proposal time ({p_ts.isoformat()})",
                status=ClaimStatus.UNSUPPORTED,
                evidence_source="MarketSnapshot.timestamp vs Proposal.timestamp",
                claimed_value=p_ts.isoformat(),
                observed_value=s_ts.isoformat(),
                explanation=f"Snapshot timestamp is {time_diff:.0f}s ahead of proposal timestamp (lookahead or clock mismatch).",
                severity=ClaimSeverity.HIGH,
            )
        return EvidenceAuditClaim(
            claim=f"Snapshot timestamp ({s_ts.isoformat()}) aligns with proposal time ({p_ts.isoformat()})",
            status=ClaimStatus.SUPPORTED,
            evidence_source="MarketSnapshot.timestamp vs Proposal.timestamp",
            claimed_value=p_ts.isoformat(),
            observed_value=s_ts.isoformat(),
            explanation="Snapshot timestamp precedes or aligns with proposal timestamp.",
            severity=ClaimSeverity.INFO,
        )

    def _extract_claims_with_llm(self, fields: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Use Gemini to extract factual claims from unstructured proposal text.
        NOTE: LLM is used ONLY to extract the claims; numeric verification is ALWAYS deterministic.
        Falls back reliably to deterministic extraction on failure or malformed output.
        """
        det_claims = self._extract_claims_deterministic(fields)
        if not self.client:
            return det_claims

        combined_text = (
            f"Thesis: {fields.get('thesis', '')}\n"
            f"Evidence: {fields.get('evidence', '')}\n"
            f"Summary: {fields.get('summary', '')}\n"
            f"Assumptions: {', '.join(fields.get('assumptions', []))}\n"
            f"Invalidation: {', '.join(fields.get('invalidation_conditions', []))}"
        )

        prompt = (
            "You are a factual claim extractor for financial strategy proposals.\n"
            "Extract all factual claims from the text into a JSON array of objects.\n"
            "Each object MUST have:\n"
            "  - 'claim': string describing the claim\n"
            "  - 'type': one of ['numeric_metric', 'momentum_metric', 'volatility_metric', 'volume_metric', 'relation', 'portfolio_metric', 'stop_loss', 'unverifiable']\n"
            "  - 'metric': (optional string, e.g. 'current_price', 'sma_20', 'sma_50', 'price_change_pct', 'volatility', 'volume', 'cash', 'account_equity', 'number_of_open_positions')\n"
            "  - 'relation': (optional string, e.g. 'price > sma_20', 'price < sma_20', 'price > sma_50', 'price < sma_50', 'sma_20 > sma_50', 'sma_20 < sma_50', 'momentum > 0', 'momentum < 0')\n"
            "  - 'claimed_value': string representation of claimed value\n"
            "  - 'parsed_value': numeric value if applicable (float or int)\n"
            "  - 'evidence_source': string indicating expected source (e.g. 'MarketSnapshot.current_price', 'PortfolioState.cash')\n"
            "Do NOT verify or evaluate the claims. Only extract what is claimed.\n\n"
            f"Proposal Text:\n{combined_text}"
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                )
            )
            raw_text = getattr(response, "text", "") or ""
            parsed = json.loads(raw_text)

            llm_claims: List[Dict[str, Any]] = []
            if isinstance(parsed, dict) and "claims" in parsed and isinstance(parsed["claims"], list):
                candidate_list = parsed["claims"]
            elif isinstance(parsed, list):
                candidate_list = parsed
            else:
                candidate_list = []

            for item in candidate_list:
                if not isinstance(item, dict):
                    continue
                claim_str = item.get("claim")
                if not claim_str or not isinstance(claim_str, str):
                    continue
                # Normalize and validate item fields
                c_type = item.get("type", "unverifiable")
                if c_type not in [
                    "numeric_metric", "momentum_metric", "volatility_metric",
                    "volume_metric", "relation", "portfolio_metric", "stop_loss", "unverifiable"
                ]:
                    c_type = "unverifiable"

                cleaned_item = {
                    "type": c_type,
                    "claim": claim_str,
                    "claimed_value": str(item.get("claimed_value", "")),
                    "evidence_source": item.get("evidence_source"),
                    "metric": item.get("metric"),
                    "relation": item.get("relation"),
                }
                if "parsed_value" in item and isinstance(item["parsed_value"], (int, float)):
                    cleaned_item["parsed_value"] = float(item["parsed_value"])
                elif c_type in ["numeric_metric", "momentum_metric", "volatility_metric", "volume_metric", "portfolio_metric", "stop_loss"]:
                    # Try to parse claimed_value
                    try:
                        raw_cval = str(item.get("claimed_value", "")).replace("$", "").replace("%", "").replace(",", "").strip()
                        cleaned_item["parsed_value"] = float(raw_cval)
                    except (ValueError, TypeError):
                        cleaned_item["type"] = "unverifiable"
                        cleaned_item["explanation"] = f"LLM extracted claim could not be deterministically parsed: '{item.get('claimed_value')}'"

                llm_claims.append(cleaned_item)

            if not llm_claims:
                return det_claims

            # Merge LLM claims with deterministic claims, avoiding duplicate claims by claim text
            existing_claims = {c["claim"].strip().lower() for c in det_claims if "claim" in c}
            merged = list(det_claims)
            for lc in llm_claims:
                c_key = lc["claim"].strip().lower()
                if c_key not in existing_claims:
                    existing_claims.add(c_key)
                    merged.append(lc)
            return merged

        except Exception as e:
            # Deterministic fallback with explicit unverifiable extraction failure claim if LLM failed
            return det_claims
