import uuid
from typing import Optional, List
from google import genai
from pydantic import ValidationError

from tradepilot.core.models import (
    MarketSnapshot, 
    PortfolioState, 
    TradeProposal, 
    TradeMemory,
    Action,
    OrderType,
    TimeInForce,
    StrategyProposal
)
from tradepilot.core.config import settings
from tradepilot.agents.strategy import StrategyAgent

class LLMStrategyAgent(StrategyAgent):
    """
    LLM-backed Strategy Agent using Google Gemini SDK with Structured Outputs.
    """
    def __init__(self):
        # We initialize the client if the key is present. 
        # Otherwise, generate_proposal will fail cleanly.
        self.client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
        self.model_name = settings.gemini_model_name
        
    def generate_proposal(
        self, 
        snapshot: MarketSnapshot, 
        portfolio: PortfolioState,
        memories: Optional[List[TradeMemory]] = None
    ) -> Optional[TradeProposal]:
        
        if not self.client:
            print("LLM Error: GEMINI_API_KEY is not configured.")
            return None
            
        system_instruction = (
            "You are TradePilot's Strategy Analyst.\n"
            "Your job is to analyze supplied market and portfolio data and produce a structured trading hypothesis.\n"
            "You do NOT execute trades.\n"
            "You do NOT control position size.\n"
            "You do NOT override risk controls.\n"
            "You must:\n"
            "1. Analyze the supplied evidence.\n"
            "2. State the thesis.\n"
            "3. State assumptions.\n"
            "4. State what would invalidate the thesis.\n"
            "5. Use NO_TRADE when evidence is insufficient.\n"
            "6. Never invent market data.\n"
            "7. Never claim information that was not supplied.\n"
            "8. TradePilot currently supports BUY-to-open and SELL-to-close only. "
            "Do not propose opening short positions."
        )
        
        memory_context = "No relevant memories found."
        if memories:
            memories_text = []
            for i, mem in enumerate(memories):
                memories_text.append(
                    f"Memory {i+1}:\n"
                    f"Condition: {mem.market_condition}\n"
                    f"Lesson: {mem.lesson}\n"
                    f"Confidence: {mem.confidence}\n"
                    f"Evidence Count: {mem.evidence_count}\n"
                )
            memory_context = "\n".join(memories_text)

        prompt = (
            f"Analyze the following market and portfolio data for {snapshot.symbol}:\n\n"
            f"--- Market Snapshot ---\n"
            f"Price: {snapshot.current_price}\n"
            f"Volume: {snapshot.volume}\n"
            f"SMA20: {snapshot.sma_20}\n"
            f"SMA50: {snapshot.sma_50}\n"
            f"Momentum: {snapshot.price_change_pct * 100:.2f}%\n"
            f"Volatility: {snapshot.volatility}\n"
            f"Timestamp: {snapshot.timestamp}\n\n"
            f"--- Portfolio State ---\n"
            f"Equity: {portfolio.account_equity}\n"
            f"Cash: {portfolio.cash}\n"
            f"Open Positions: {portfolio.number_of_open_positions}\n"
            f"Held Quantities: {portfolio.position_quantities}\n\n"
            f"--- Past Trade Memories ---\n"
            f"{memory_context}\n\n"
            f"Explicitly evaluate whether these memories apply to the current situation. "
            f"Indicate memory_influence and memory_reasoning in your response."
        )
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=StrategyProposal,
                    temperature=0.2
                )
            )
            
            # The SDK parses it into a Pydantic object if using pydantic directly, 
            # or returns a model object. Let's explicitly parse to ensure strict validation.
            strategy_proposal = response.parsed
            
            # If the response isn't directly the pydantic model, parse it from JSON text
            if not isinstance(strategy_proposal, StrategyProposal):
                 strategy_proposal = StrategyProposal.model_validate_json(response.text)
                 
        except ValidationError as e:
            print(f"LLM Error: Malformed response from model: {e}")
            return None
        except Exception as e:
            print(f"LLM API Error: {e}")
            return None
            
        if strategy_proposal.action == Action.NO_TRADE:
            return None
            
        # Application Logic: Position Sizing (LLM does NOT control this)
        quantity = self._calculate_position_size(
            current_price=snapshot.current_price,
            portfolio=portfolio,
            max_pct=settings.max_position_pct
        )
        
        if quantity <= 0:
            print(f"Strategy proposes {strategy_proposal.action}, but calculated quantity is 0. Returning NO_TRADE.")
            return None
            
        try:
            # Create final TradeProposal
            proposal = TradeProposal(
                trade_id=uuid.uuid4(),
                thesis_id=uuid.uuid4(),
                symbol=snapshot.symbol,
                action=strategy_proposal.action,
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                quantity=quantity,
                current_price=snapshot.current_price,
                stop_loss=strategy_proposal.suggested_stop_loss,
                confidence=strategy_proposal.confidence,
                reasoning=f"{strategy_proposal.thesis}\n\nEvidence:\n{strategy_proposal.evidence}\n\nSummary:\n{strategy_proposal.reasoning_summary}\n\nMemory Reasoning: {strategy_proposal.memory_reasoning}",
                assumptions=strategy_proposal.assumptions,
                invalidation_conditions=strategy_proposal.invalidation_conditions,
                memory_influence=strategy_proposal.memory_influence
            )
        except ValidationError as e:
            print(f"Validation Error in LLM suggested parameters (e.g. invalid stop loss): {e}")
            return None
            
        return proposal
