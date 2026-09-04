from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    alpaca_api_key: str = Field(default="", description="Alpaca API Key")
    alpaca_secret_key: str = Field(default="", description="Alpaca Secret Key")
    alpaca_paper_trade: bool = Field(default=True, description="Enforce Paper Trading")
    gemini_api_key: str = Field(default="", description="Gemini API Key")
    gemini_model_name: str = Field(default="gemini-3.6-flash", description="Gemini Model Name")
    
    # Risk Limits Configuration
    max_position_pct: float = Field(default=0.1, description="Maximum portfolio percentage per position")
    max_risk_usd: float = Field(default=10000.0, description="Maximum dollar risk per position")
    max_open_positions: int = Field(default=5, description="Maximum number of concurrent positions")
    daily_loss_limit: float = Field(default=2000.0, description="Maximum daily loss before trading stops")
    
    # Demo Safety
    max_demo_order_notional: float = Field(default=100.00, description="Maximum notional value for demo executions")
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
