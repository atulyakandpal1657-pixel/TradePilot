import uuid
from typing import Optional
from google import genai
from pydantic import ValidationError

from tradepilot.core.models import TradeRecord, TradeForensics, MarketSnapshot
from tradepilot.core.config import settings

class ForensicsAgent:
    """
    Analyzes closed trades to extract lessons and produce TradeForensics.
    """
    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
        self.model_name = settings.gemini_model_name
        
    def analyze_trade(
        self,
        record: TradeRecord,
        entry_snapshot: Optional[MarketSnapshot] = None,
        exit_snapshot: Optional[MarketSnapshot] = None
    ) -> Optional[TradeForensics]:
        """
        Compares WHAT WE BELIEVED against WHAT ACTUALLY HAPPENED.
        Returns a structured TradeForensics object.
        """
        if not self.client:
            print("Forensics Error: GEMINI_API_KEY is not configured.")
            return None
            
        system_instruction = (
            "You are TradePilot's Trade Forensics Analyst.\n"
            "Your job is to compare a trading hypothesis against the actual trade outcome.\n"
            "You must diagnose what was correct and incorrect.\n"
            "You must NOT invent facts.\n"
            "If evidence is insufficient, mark the relevant conclusion INCONCLUSIVE.\n"
            "You must generate a structured lesson to be stored as memory."
        )
        
        prompt = (
            f"Analyze the following completed trade for {record.symbol}:\n\n"
            f"--- WHAT WE BELIEVED ---\n"
            f"Original Thesis: {record.original_thesis}\n"
            f"Assumptions: {', '.join(record.assumptions)}\n"
            f"Invalidation Conditions: {', '.join(record.invalidation_conditions)}\n\n"
            f"--- WHAT ACTUALLY HAPPENED ---\n"
            f"Action: {record.action.value}\n"
            f"Entry Price: {record.entry_price}\n"
            f"Exit Price: {record.exit_price}\n"
            f"Realized PnL: {record.realized_pnl}\n"
            f"Realized Return: {record.realized_return_pct * 100 if record.realized_return_pct else 0:.2f}%\n"
        )
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=TradeForensics,
                    temperature=0.2
                )
            )
            
            forensics = response.parsed
            
            if not isinstance(forensics, TradeForensics):
                 forensics = TradeForensics.model_validate_json(response.text)
                 
            # Ensure the trade_id matches the original record
            forensics.trade_id = record.trade_id
            
            return forensics
            
        except ValidationError as e:
            print(f"Forensics Error: Malformed response from model: {e}")
            return None
        except Exception as e:
            print(f"Forensics API Error: {e}")
            return None
