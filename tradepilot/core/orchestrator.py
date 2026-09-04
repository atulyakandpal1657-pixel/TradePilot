from tradepilot.core.models import TradeProposal, RiskStatus, ExecutionResult, ExecutionStatus, PortfolioState, ApprovedTrade
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
                status=ExecutionStatus.REJECTED,
                symbol=mocked_proposal.symbol,
                action=mocked_proposal.action,
                filled_quantity=0,
                filled_price=None,
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
            status=ExecutionStatus.FILLED,
            symbol=proposal.symbol,
            action=proposal.action,
            filled_quantity=proposal.quantity,
            filled_price=proposal.current_price,
            message="Order filled successfully."
        )
