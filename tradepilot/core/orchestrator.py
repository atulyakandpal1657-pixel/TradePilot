"""
tradepilot/core/orchestrator.py

LEGACY / PROTOTYPE MODULE:
This module represents the initial Step 1 prototype orchestrator used early in development
to demonstrate the 3-stage loop: Strategy Proposal -> RiskEngine -> Execution.

In the current v1.0 architecture:
  - Proposal creation is driven by StrategyAgent / LLMStrategyAgent.
  - RiskEngine evaluates proposals and records them in PendingProposals.
  - Human approval is mandatory (Step 4 / Phase D) via CLI or Web Dashboard.
  - Paper execution is performed by AlpacaClient (Step 5).
  - Web backend is served by tradepilot.api (FastAPI).

This module is preserved for backward compatibility, reference, and standalone testing.
"""

import uuid
from tradepilot.core.models import (
    Action,
    ApprovedTrade,
    ExecutionResult,
    ExecutionStatus,
    PortfolioState,
    RiskStatus,
    TradeProposal,
)
from tradepilot.core.risk_engine import RiskEngine


class Orchestrator:
    def __init__(self, risk_engine: RiskEngine):
        self.risk_engine = risk_engine

    def run_cycle(self, mocked_proposal: TradeProposal, portfolio_state: PortfolioState) -> ExecutionResult:
        print(f"--- Starting Orchestrator Cycle for {mocked_proposal.symbol} ---")
        
        # 1. Strategy Agent (Mocked)
        print(f"Strategy Agent proposed: {mocked_proposal.action.value} {mocked_proposal.quantity} {mocked_proposal.symbol}")
        
        # 2. Risk Engine
        decision = self.risk_engine.evaluate(mocked_proposal, portfolio_state)
        print(f"Risk Engine Decision: {decision.status.value}")
        for reason in decision.reasons:
            print(f" - {reason}")
            
        # 3. Execution Agent (Mocked)
        if decision.status == RiskStatus.APPROVED and decision.approved_trade is not None:
            return self._execute_trade(decision.approved_trade)
        else:
            print("Execution Agent: Order aborted due to risk rejection.")
            return ExecutionResult(
                trade_id=mocked_proposal.trade_id,
                thesis_id=mocked_proposal.thesis_id,
                client_order_id=mocked_proposal.trade_id,
                alpaca_order_id=None,
                status=ExecutionStatus.REJECTED,
                symbol=mocked_proposal.symbol,
                action=mocked_proposal.action,
                quantity=mocked_proposal.quantity,
                order_type=mocked_proposal.order_type,
                filled_quantity=0.0,
                average_fill_price=None,
                message="Aborted by risk engine."
            )
            
    def _execute_trade(self, approved_trade: ApprovedTrade) -> ExecutionResult:
        """
        HARD EXECUTION BOUNDARY.
        This method strictly accepts an ApprovedTrade, never a raw TradeProposal.
        """
        print(f"Execution Agent: Executing strictly approved trade: {approved_trade.trade_id}")
        proposal = approved_trade.proposal
        
        # Simulate execution success
        return ExecutionResult(
            trade_id=approved_trade.trade_id,
            thesis_id=proposal.thesis_id,
            client_order_id=approved_trade.trade_id,
            alpaca_order_id=f"mock-alpaca-{uuid.uuid4().hex[:8]}",
            status=ExecutionStatus.FILLED,
            symbol=proposal.symbol,
            action=proposal.action,
            quantity=proposal.quantity,
            order_type=proposal.order_type,
            filled_quantity=proposal.quantity,
            average_fill_price=proposal.current_price,
            message="Order filled successfully (simulated legacy execution)."
        )
