import uuid
from datetime import datetime, timezone
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce as AlpacaTimeInForce
from alpaca.common.exceptions import APIError

from tradepilot.core.config import settings
from tradepilot.core.models import (
    ApprovedTrade, 
    ExecutionResult, 
    ExecutionStatus, 
    Action, 
    OrderType,
    TimeInForce
)

class AlpacaPaperExecutor:
    """
    Executes ApprovedTrades against the Alpaca Paper Trading API.
    Short selling is not supported in the current MVP; SELL orders must be
    approved by RiskEngine as SELL-to-close before reaching this layer.
    """
    def __init__(self, client: Optional[TradingClient] = None):
        if not settings.alpaca_paper_trade:
            raise ValueError("Safety Violation: AlpacaPaperExecutor initialized with PAPER_TRADE=False")
        
        self.client = client
        if self.client is None and settings.alpaca_api_key and settings.alpaca_secret_key:
            self.client = TradingClient(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                paper=True
            )

    def execute(self, approved_trade: ApprovedTrade) -> ExecutionResult:
        if not isinstance(approved_trade, ApprovedTrade):
            raise TypeError("Execution layer only accepts ApprovedTrade instances.")
            
        if not self.client:
            return ExecutionResult(
                trade_id=approved_trade.trade_id,
                thesis_id=approved_trade.proposal.thesis_id,
                client_order_id=approved_trade.trade_id,
                status=ExecutionStatus.REJECTED,
                symbol=approved_trade.proposal.symbol,
                action=approved_trade.proposal.action,
                quantity=approved_trade.proposal.quantity,
                order_type=approved_trade.proposal.order_type,
                message="Missing Alpaca credentials.",
            )
            
        proposal = approved_trade.proposal
        side = OrderSide.BUY if proposal.action == Action.BUY else OrderSide.SELL
        time_in_force = AlpacaTimeInForce.DAY if proposal.time_in_force == TimeInForce.DAY else AlpacaTimeInForce.GTC
        
        try:
            if proposal.order_type == OrderType.MARKET:
                req = MarketOrderRequest(
                    symbol=proposal.symbol,
                    qty=proposal.quantity,
                    side=side,
                    time_in_force=time_in_force,
                    client_order_id=str(approved_trade.trade_id)
                )
            elif proposal.order_type == OrderType.LIMIT:
                if not proposal.limit_price:
                    raise ValueError("LIMIT order requires limit_price")
                req = LimitOrderRequest(
                    symbol=proposal.symbol,
                    qty=proposal.quantity,
                    side=side,
                    time_in_force=time_in_force,
                    limit_price=proposal.limit_price,
                    client_order_id=str(approved_trade.trade_id)
                )
            else:
                 raise NotImplementedError(f"Order type {proposal.order_type} not supported yet.")
                 
            order = self.client.submit_order(req)
            
            status_map = {
                "accepted": ExecutionStatus.ACCEPTED,
                "pending_new": ExecutionStatus.PENDING,
                "new": ExecutionStatus.ACCEPTED,
                "partially_filled": ExecutionStatus.PARTIALLY_FILLED,
                "filled": ExecutionStatus.FILLED,
                "rejected": ExecutionStatus.REJECTED,
                "canceled": ExecutionStatus.CANCELLED
            }
            
            return ExecutionResult(
                trade_id=approved_trade.trade_id,
                thesis_id=proposal.thesis_id,
                client_order_id=approved_trade.trade_id,
                alpaca_order_id=str(order.id),
                status=status_map.get(order.status.value if hasattr(order.status, 'value') else str(order.status), ExecutionStatus.PENDING),
                symbol=proposal.symbol,
                action=proposal.action,
                quantity=proposal.quantity,
                order_type=proposal.order_type,
                filled_quantity=float(order.filled_qty) if order.filled_qty else 0.0,
                average_fill_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                message="Order successfully submitted to Alpaca."
            )
            
        except APIError as e:
            return ExecutionResult(
                trade_id=approved_trade.trade_id,
                thesis_id=proposal.thesis_id,
                client_order_id=approved_trade.trade_id,
                status=ExecutionStatus.REJECTED,
                symbol=proposal.symbol,
                action=proposal.action,
                quantity=proposal.quantity,
                order_type=proposal.order_type,
                message=f"Alpaca API Error: {str(e)}"
            )
        except Exception as e:
            return ExecutionResult(
                trade_id=approved_trade.trade_id,
                thesis_id=proposal.thesis_id,
                client_order_id=approved_trade.trade_id,
                status=ExecutionStatus.REJECTED,
                symbol=proposal.symbol,
                action=proposal.action,
                quantity=proposal.quantity,
                order_type=proposal.order_type,
                message=f"Execution Error: {str(e)}"
            )
