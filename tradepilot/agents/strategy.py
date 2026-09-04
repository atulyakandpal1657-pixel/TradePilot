from abc import ABC, abstractmethod
from typing import Optional, List
import uuid

from tradepilot.core.models import (
    MarketSnapshot, 
    PortfolioState, 
    TradeProposal, 
    TradeMemory,
    Action,
    OrderType,
    TimeInForce
)
from tradepilot.core.config import settings

class StrategyAgent(ABC):
    """
    Abstract interface for Strategy Agents.
    This enables us to swap the deterministic baseline with an LLM later.
    """
    @abstractmethod
    def generate_proposal(
        self, 
        snapshot: MarketSnapshot, 
        portfolio: PortfolioState,
        memories: Optional[List[TradeMemory]] = None
    ) -> Optional[TradeProposal]:
        """
        Generates a TradeProposal based on market data and portfolio state.
        Returns None if no trade should be taken.
        """
        pass
        
    def _calculate_position_size(self, current_price: float, portfolio: PortfolioState, max_pct: float) -> float:
        """
        Calculates a safe position size based on account equity, NOT buying power.
        """
        max_notional = portfolio.account_equity * max_pct
        # Round down to nearest whole share
        quantity = int(max_notional / current_price)
        return float(quantity)

class DeterministicBaselineStrategy(StrategyAgent):
    """
    A transparent baseline strategy. NOT an AI.
    BUY when:
    - current price > SMA20
    - SMA20 > SMA50
    - price change is positive
    """
    def generate_proposal(
        self, 
        snapshot: MarketSnapshot, 
        portfolio: PortfolioState,
        memories: Optional[List[TradeMemory]] = None
    ) -> Optional[TradeProposal]:
        
        # Determine Signal
        is_bullish = (
            snapshot.current_price > snapshot.sma_20 and
            snapshot.sma_20 > snapshot.sma_50 and
            snapshot.price_change_pct > 0
        )
        
        if not is_bullish:
            return None # NO TRADE
            
        # Position Sizing
        quantity = self._calculate_position_size(
            current_price=snapshot.current_price,
            portfolio=portfolio,
            max_pct=settings.max_position_pct
        )
        
        if quantity <= 0:
            return None # Insufficient funds for even 1 share
            
        # Define Trade
        stop_loss = snapshot.sma_50 # Conservative stop at the 50-day moving average
        if stop_loss >= snapshot.current_price:
            stop_loss = snapshot.current_price * 0.95 # Fallback to 5% below if SMA50 is weirdly high
            
        return TradeProposal(
            trade_id=uuid.uuid4(),
            thesis_id=uuid.uuid4(),
            symbol=snapshot.symbol,
            action=Action.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            quantity=quantity,
            current_price=snapshot.current_price,
            stop_loss=stop_loss,
            confidence=0.8,
            reasoning=f"Baseline: Price ({snapshot.current_price:.2f}) > SMA20 ({snapshot.sma_20:.2f}) > SMA50 ({snapshot.sma_50:.2f}) with positive momentum.",
            assumptions=["Trend continues", "SMA50 acts as support"],
            invalidation_conditions=["Price drops below SMA50"]
        )
