from tradepilot.core.models import TradeProposal, RiskDecision, RiskStatus, Action, PortfolioState, ApprovedTrade

class RiskEngine:
    def __init__(self, max_position_pct: float, max_risk_usd: float, max_open_positions: int, daily_loss_limit: float):
        self.max_position_pct = max_position_pct
        self.max_risk_usd = max_risk_usd
        self.max_open_positions = max_open_positions
        self.daily_loss_limit = daily_loss_limit

    def evaluate(self, proposal: TradeProposal, portfolio: PortfolioState) -> RiskDecision:
        reasons = []
        status = RiskStatus.APPROVED

        # 0. SELL-to-close only — short selling is not supported in the current MVP.
        if proposal.action == Action.SELL:
            held_qty = portfolio.position_quantities.get(proposal.symbol, 0.0)
            if held_qty <= 0:
                reasons.append(
                    f"SELL rejected: no long position in {proposal.symbol}. "
                    "Short selling is not supported."
                )
                status = RiskStatus.REJECTED
            elif proposal.quantity > held_qty:
                reasons.append(
                    f"SELL quantity ({proposal.quantity}) exceeds held long position "
                    f"({held_qty}) for {proposal.symbol}."
                )
                status = RiskStatus.REJECTED

        estimated_cost = proposal.quantity * proposal.current_price
        
        # 1. Check Max Position Percentage
        exposure_pct = estimated_cost / portfolio.account_equity if portfolio.account_equity > 0 else float('inf')
        if exposure_pct > self.max_position_pct:
             reasons.append(f"Position exposure ({exposure_pct:.2%}) exceeds max allowed ({self.max_position_pct:.2%}).")
             status = RiskStatus.REJECTED

        # 2. Check Buying Power
        if estimated_cost > portfolio.buying_power:
            reasons.append(f"Insufficient buying power. Cost: {estimated_cost:.2f}, BP: {portfolio.buying_power:.2f}")
            status = RiskStatus.REJECTED

        # 3. Check Max Open Positions (only if buying a new position not currently held)
        # Note: simplistic assumption that all BUYs are opening positions for this MVP phase.
        if proposal.action == Action.BUY and proposal.symbol not in portfolio.open_positions:
            if portfolio.number_of_open_positions >= self.max_open_positions:
                reasons.append(f"Max open positions reached ({self.max_open_positions}).")
                status = RiskStatus.REJECTED

        # 4. Check Daily Loss Limit
        if portfolio.current_daily_loss >= self.daily_loss_limit:
            reasons.append(f"Daily loss limit reached ({portfolio.current_daily_loss:.2f} >= {self.daily_loss_limit:.2f}).")
            status = RiskStatus.REJECTED

        # 5. Check Trade Risk (Stop Loss distance)
        if proposal.stop_loss:
            if proposal.action == Action.BUY:
                risk_amount = (proposal.current_price - proposal.stop_loss) * proposal.quantity
            else: # SELL
                risk_amount = (proposal.stop_loss - proposal.current_price) * proposal.quantity
            
            if risk_amount > self.max_risk_usd:
                reasons.append(f"Trade risk ({risk_amount:.2f}) exceeds max allowed ({self.max_risk_usd:.2f}).")
                status = RiskStatus.REJECTED

        if status == RiskStatus.REJECTED:
            return RiskDecision(status=status, reasons=reasons, approved_trade=None)
        
        reasons.append("All risk parameters met.")
        approved_trade = ApprovedTrade(trade_id=proposal.trade_id, proposal=proposal)
        return RiskDecision(status=status, reasons=reasons, approved_trade=approved_trade)
