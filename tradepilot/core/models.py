from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, model_validator

class Action(str, Enum):
    """Trade action semantics for the MVP.

    BUY      — open or increase a long position.
    SELL     — reduce or close an existing long position (never open a short).
    NO_TRADE — no action.

    Short selling is not supported in the current MVP.
    """
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"

class RiskStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class ExecutionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

class Quality(str, Enum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    INCORRECT = "INCORRECT"
    INCONCLUSIVE = "INCONCLUSIVE"

class MemoryInfluence(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class PortfolioState(BaseModel):
    account_equity: float = Field(ge=0)
    buying_power: float = Field(ge=0)
    cash: float = Field(ge=0)
    current_daily_loss: float = Field(default=0.0)
    open_positions: List[str] = Field(default_factory=list)
    number_of_open_positions: int = Field(ge=0)
    # Long quantities held per symbol; used to enforce SELL-to-close only (no shorts).
    position_quantities: Dict[str, float] = Field(default_factory=dict)

class MarketStatus(BaseModel):
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime

class MarketSnapshot(BaseModel):
    symbol: str
    current_price: float = Field(gt=0)
    timestamp: datetime
    volume: float = Field(ge=0)
    previous_close: float = Field(gt=0)
    price_change_pct: float
    sma_20: float = Field(gt=0)
    sma_50: float = Field(gt=0)
    volatility: float = Field(ge=0, description="Standard deviation of recent daily returns")

class StrategyProposal(BaseModel):
    action: Action
    confidence: float
    thesis: str
    evidence: str
    assumptions: List[str]
    invalidation_conditions: List[str]
    suggested_stop_loss: Optional[float] = None
    reasoning_summary: str
    memory_influence: MemoryInfluence = Field(default=MemoryInfluence.NONE)
    memory_reasoning: str = Field(default="")

class TradeProposal(BaseModel):
    """Application trade intent. Short selling is not supported in the current MVP."""
    trade_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    thesis_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    symbol: str = Field(..., min_length=1, pattern=r"^[A-Z]+$")
    action: Action
    order_type: OrderType = Field(default=OrderType.MARKET)
    time_in_force: TimeInForce = Field(default=TimeInForce.DAY)
    quantity: float = Field(gt=0)
    current_price: float = Field(gt=0)
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1.0)
    reasoning: str
    assumptions: List[str]
    invalidation_conditions: List[str]
    memory_influence: MemoryInfluence = Field(default=MemoryInfluence.NONE)

    @model_validator(mode="after")
    def validate_stop_loss(self) -> 'TradeProposal':
        if self.stop_loss is not None:
            if self.action == Action.BUY and self.stop_loss >= self.current_price:
                raise ValueError("For BUY, stop loss must be below current price.")
            if self.action == Action.SELL and self.stop_loss <= self.current_price:
                raise ValueError("For SELL-to-close, stop loss must be above current price.")
        return self

class ApprovedTrade(BaseModel):
    trade_id: uuid.UUID
    proposal: TradeProposal
    approved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class RiskDecision(BaseModel):
    status: RiskStatus
    reasons: List[str]
    approved_trade: Optional[ApprovedTrade] = None

class ExecutionResult(BaseModel):
    trade_id: uuid.UUID
    thesis_id: uuid.UUID
    client_order_id: uuid.UUID
    alpaca_order_id: Optional[str] = None
    status: ExecutionStatus
    symbol: str
    action: Action
    quantity: float = Field(ge=0)
    order_type: OrderType
    filled_quantity: float = Field(ge=0, default=0.0)
    average_fill_price: Optional[float] = None
    message: str
    submitted_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TradeRecord(BaseModel):
    trade_id: uuid.UUID
    thesis_id: uuid.UUID
    # Correlation: trade_id is used as Alpaca client_order_id
    alpaca_order_id: Optional[str] = None   # Set once Alpaca returns its own order UUID
    symbol: str
    action: Action
    # ── Quantity lifecycle ────────────────────────────────────────────────────
    # quantity        = what we asked to buy/sell (never mutated after creation)
    # filled_quantity = cumulative shares confirmed filled by Alpaca
    # remaining_quantity is a computed convenience: quantity - filled_quantity
    quantity: float                              # requested — immutable
    filled_quantity: float = Field(default=0.0) # cumulative fills from Alpaca events
    average_fill_price: Optional[float] = None  # volume-weighted avg across fills
    original_thesis: str
    original_confidence: float
    assumptions: List[str]
    invalidation_conditions: List[str]
    risk_decision: RiskStatus

    # ── Entry (populated on fill) ─────────────────────────────────────────────
    entry_price: Optional[float] = None       # = average_fill_price when FILLED
    entry_timestamp: Optional[datetime] = None
    execution_status: ExecutionStatus = ExecutionStatus.PENDING

    # ── Exit (populated only when position confirmed closed) ───────────────────
    # A SELL order does NOT automatically mark this trade as closed.
    # exit_price is set only when the position is confirmed zero by Alpaca.
    exit_price: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    realized_pnl: Optional[float] = None
    realized_return_pct: Optional[float] = None

    @property
    def remaining_quantity(self) -> float:
        """Shares requested but not yet confirmed filled."""
        return max(0.0, self.quantity - self.filled_quantity)
    
class TradeForensics(BaseModel):
    trade_id: uuid.UUID
    thesis_correctness: Quality
    successful_assumptions: List[str]
    failed_assumptions: List[str]
    actual_market_conditions: str
    primary_failure_or_success_factor: str
    thesis_quality: Quality
    timing_quality: Quality
    risk_assessment_quality: Quality
    lesson: str
    confidence: float = Field(ge=0, le=1.0)
    evidence_summary: str

class TradeMemory(BaseModel):
    memory_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_trade_id: uuid.UUID
    symbol: str
    market_condition: str
    lesson: str
    applicable_conditions: List[str]
    evidence_count: int = Field(default=1, ge=0)
    confidence: float = Field(ge=0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
