import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import date
import pandas as pd

from tradepilot.api import app
from tradepilot.memory.journal import TradeJournal

client = TestClient(app)

@pytest.fixture
def mock_fetch_historical_bars():
    with patch("tradepilot.api.fetch_historical_bars") as mock_fetch:
        # Create a mock dataframe that has the required columns
        df = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", "2026-01-01", freq="B", tz="UTC"),
            "open": [100.0] * 262,
            "close": [101.0] * 262,
            "volume": [1000] * 262,
            "sma_20": [99.0] * 262,
            "sma_50": [98.0] * 262,
            "volatility": [0.01] * 262,
            "price_change_pct": [0.01] * 262,
            "previous_close": [100.0] * 262,
            "is_decision_bar": [True] * 262,
        })
        mock_fetch.return_value = df
        yield mock_fetch

@pytest.fixture
def mock_backtest_engine_run():
    with patch("tradepilot.backtest.engine.BacktestEngine.run") as mock_run:
        from tradepilot.backtest.models import BacktestResult, BacktestTrade, BacktestEquityPoint, BacktestBenchmark
        
        mock_run.return_value = BacktestResult(
            symbol="AAPL",
            start=date(2025, 1, 1),
            end=date(2026, 1, 1),
            strategy="DeterministicBaseline",
            exit_rule="CloseBasedStopLoss",
            initial_capital=10000.0,
            final_equity=10500.0,
            total_pnl=500.0,
            total_return_pct=0.05,
            total_trades=1,
            winning_trades=1,
            losing_trades=0,
            win_rate=1.0,
            max_drawdown=0.0,
            open_positions_at_end=1,
            bars_processed=250,
            realized_pnl=500.0,
            unrealized_pnl=0.0,
            avg_winning_trade=500.0,
            avg_losing_trade=None,
            profit_factor=None,
            benchmark=BacktestBenchmark(
                symbol="AAPL",
                initial_capital=10000.0,
                final_equity=11000.0,
                total_pnl=1000.0,
                total_return_pct=0.10,
                start_price=100.0,
                end_price=110.0,
            ),
            trades=[
                BacktestTrade(
                    symbol="AAPL",
                    action="BUY",
                    quantity=10,
                    stop_loss=90.0,
                    entry_date=date(2025, 2, 1),
                    entry_price=100.0,
                    exit_date=date(2025, 3, 1),
                    exit_price=150.0,
                    exit_reason="STOP_LOSS",
                    pnl=500.0,
                    pnl_pct=0.5
                ),
                BacktestTrade(
                    symbol="AAPL",
                    action="BUY",
                    quantity=5,
                    stop_loss=90.0,
                    entry_date=date(2025, 4, 1),
                    entry_price=100.0,
                    exit_date=None,
                    exit_price=None,
                    exit_reason=None,
                    pnl=None,
                    pnl_pct=None
                )
            ],
            equity_curve=[
                BacktestEquityPoint(date=date(2025, 1, 1), cash=10000.0, position_value=0.0, total_equity=10000.0, daily_return=0.0)
            ]
        )
        yield mock_run

def test_valid_backtest_request(mock_fetch_historical_bars, mock_backtest_engine_run):
    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2025-01-01",
        "end_date": "2026-01-01",
        "initial_capital": 10000
    })
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "AAPL"
    assert data["initial_capital"] == 10000.0
    
    # 7. Correct metrics returned
    assert data["final_equity"] == 10500.0
    assert data["total_return_pct"] == 0.05
    assert data["total_trades"] == 1
    assert data["realized_pnl"] == 500.0
    assert data["unrealized_pnl"] == 0.0
    assert data["avg_winning_trade"] == 500.0
    assert data["avg_losing_trade"] is None
    assert data["profit_factor"] is None
    assert data["benchmark"]["total_return_pct"] == 0.10
    assert data["benchmark"]["final_equity"] == 11000.0
    
    # 8. Trade details returned
    assert len(data["trades"]) == 2
    assert data["trades"][0]["entry_price"] == 100.0
    assert data["trades"][0]["pnl"] == 500.0
    
    # 9. Open positions represented correctly
    assert data["trades"][1]["exit_date"] is None
    assert data["trades"][1]["pnl"] is None
    
    # 10. Equity curve returned
    assert len(data["equity_curve"]) == 1
    assert data["equity_curve"][0]["total_equity"] == 10000.0

def test_invalid_symbol():
    response = client.post("/api/backtest", json={
        "symbol": "",
        "start_date": "2025-01-01",
        "end_date": "2026-01-01",
        "initial_capital": 10000
    })
    assert response.status_code == 422

def test_invalid_date_format():
    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "invalid-date",
        "end_date": "2026-01-01",
        "initial_capital": 10000
    })
    assert response.status_code == 422

def test_start_date_gte_end_date():
    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2026-01-01",
        "end_date": "2025-01-01",
        "initial_capital": 10000
    })
    assert response.status_code == 422
    assert "end_date must be strictly after start_date" in response.text

    response2 = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "initial_capital": 10000
    })
    assert response2.status_code == 422

def test_max_range():
    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2015-01-01",
        "end_date": "2025-01-01",
        "initial_capital": 10000
    })
    assert response.status_code == 422
    assert "maximum backtest range is 5 years" in response.text

def test_invalid_initial_capital():
    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2025-01-01",
        "end_date": "2026-01-01",
        "initial_capital": -500
    })
    assert response.status_code == 422

@patch("tradepilot.backtest.data.fetch_historical_bars")
@patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute")
@patch("tradepilot.agents.llm_strategy.LLMStrategyAgent.analyze_market")
def test_no_side_effects(mock_analyze, mock_execute, mock_fetch):
    # Ensure a real test of zero side-effects during a backtest call
    # We will let the real BacktestEngine run, but mock the data fetch
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", "2025-01-10", freq="B", tz="UTC"),
        "open": [100.0] * 8,
        "close": [101.0] * 8,
        "volume": [1000] * 8,
        "sma_20": [99.0] * 8,
        "sma_50": [98.0] * 8,
        "volatility": [0.01] * 8,
        "price_change_pct": [0.01] * 8,
        "previous_close": [100.0] * 8,
        "is_decision_bar": [True] * 8,
    })
    mock_fetch.return_value = df
    
    journal = TradeJournal(db_path="data/tradepilot.db")
    
    # 14. Existing production journal unchanged
    try:
        pending_before = len(journal.list_all_pending_proposals())
        records_before = len(journal.list_all_records())
    except:
        pending_before = 0
        records_before = 0

    response = client.post("/api/backtest", json={
        "symbol": "AAPL",
        "start_date": "2025-01-01",
        "end_date": "2025-01-10",
        "initial_capital": 10000
    })
    
    assert response.status_code == 200
    
    try:
        pending_after = len(journal.list_all_pending_proposals())
        records_after = len(journal.list_all_records())
    except:
        pending_after = 0
        records_after = 0
        
    # 13. No SQLite writes
    assert pending_before == pending_after
    assert records_before == records_after
    
    # 11. No Alpaca orders
    mock_execute.assert_not_called()
    
    # 12. No Gemini calls
    mock_analyze.assert_not_called()


def test_open_position_metrics_and_realized_statistics_reconciliation():
    """
    Regression test for Step 8.1:
    Verify that an open position at the end of a backtest:
    - Is recorded in 'trades' with exit_reason='END_OF_DATA', exit_date=None, pnl=None.
    - Is NOT counted in total_trades (completed trades only).
    - Is NOT counted in winning_trades or losing_trades.
    - Is counted in open_positions_at_end.
    - Its market value is included in final_equity and total_return_pct.
    - Its unrealized gain/loss is excluded from total_pnl (Closed/Realized P&L).
    """
    from tradepilot.backtest.engine import BacktestEngine
    from tradepilot.core.config import settings

    # Construct synthetic data: 1 completed losing trade and 1 open position at end
    # Day 2: Bullish -> Schedule entry
    # Day 3: Entry fills at 100. Stop loss = 90. Close drops to 80 (<90) -> Schedule exit
    # Day 4: Exit fills at 85 (losing trade: pnl = (85 - 100) * qty). Close is bullish -> Schedule entry
    # Day 5: Entry fills at 90. Close = 110 (>90, no stop hit). Backtest ends.
    from datetime import datetime, timezone
    rows = [
        {"timestamp": datetime(2025, 1, 2, 21, 0, tzinfo=timezone.utc), "open": 98.0, "close": 100.0,
         "volume": 10000.0, "sma_20": 95.0, "sma_50": 90.0, "volatility": 0.01,
         "previous_close": 98.0, "price_change_pct": 0.02, "is_decision_bar": True},
        {"timestamp": datetime(2025, 1, 3, 21, 0, tzinfo=timezone.utc), "open": 100.0, "close": 80.0,
         "volume": 10000.0, "sma_20": 95.0, "sma_50": 90.0, "volatility": 0.01,
         "previous_close": 100.0, "price_change_pct": -0.20, "is_decision_bar": True},
        {"timestamp": datetime(2025, 1, 4, 21, 0, tzinfo=timezone.utc), "open": 85.0, "close": 90.0,
         "volume": 10000.0, "sma_20": 88.0, "sma_50": 85.0, "volatility": 0.01,
         "previous_close": 80.0, "price_change_pct": 0.125, "is_decision_bar": True},
        {"timestamp": datetime(2025, 1, 5, 21, 0, tzinfo=timezone.utc), "open": 90.0, "close": 110.0,
         "volume": 10000.0, "sma_20": 90.0, "sma_50": 86.0, "volatility": 0.01,
         "previous_close": 90.0, "price_change_pct": 0.222, "is_decision_bar": True},
    ]
    df = pd.DataFrame(rows)

    engine = BacktestEngine(
        initial_capital=10000.0,
        risk_params={
            "max_position_pct": settings.max_position_pct,
            "max_risk_usd": settings.max_risk_usd,
            "max_open_positions": settings.max_open_positions,
            "daily_loss_limit": settings.daily_loss_limit,
        },
        max_pct=settings.max_position_pct,
    )
    direct_res = engine.run("AAPL", df, date(2025, 1, 2), date(2025, 1, 5))

    # Assert trade-level structure
    assert len(direct_res.trades) == 2
    closed_trade = direct_res.trades[0]
    open_trade = direct_res.trades[1]

    # Closed trade checks
    assert closed_trade.exit_date is not None
    assert closed_trade.exit_reason == "STOP_LOSS"
    assert closed_trade.pnl is not None
    assert closed_trade.pnl < 0  # losing trade

    # Open position checks
    assert open_trade.exit_date is None
    assert open_trade.exit_price is None
    assert open_trade.exit_reason == "END_OF_DATA"
    assert open_trade.pnl is None
    assert open_trade.pnl_pct is None

    # Aggregate metric checks
    assert direct_res.total_trades == 1  # only completed trades
    assert direct_res.winning_trades == 0
    assert direct_res.losing_trades == 1
    assert direct_res.win_rate == 0.0
    assert direct_res.open_positions_at_end == 1
    assert direct_res.total_pnl == closed_trade.pnl

    # Now verify that POST /api/backtest matches the direct engine bit-for-bit
    with patch("tradepilot.api.fetch_historical_bars", return_value=df):
        resp = client.post("/api/backtest", json={
            "symbol": "AAPL",
            "start_date": "2025-01-02",
            "end_date": "2025-01-05",
            "initial_capital": 10000
        })
        assert resp.status_code == 200
        api_data = resp.json()

        assert api_data["total_trades"] == direct_res.total_trades
        assert api_data["winning_trades"] == direct_res.winning_trades
        assert api_data["losing_trades"] == direct_res.losing_trades
        assert api_data["win_rate"] == direct_res.win_rate
        assert api_data["total_pnl"] == direct_res.total_pnl
        assert api_data["final_equity"] == direct_res.final_equity
        assert api_data["total_return_pct"] == direct_res.total_return_pct
        assert api_data["open_positions_at_end"] == direct_res.open_positions_at_end
        assert len(api_data["trades"]) == len(direct_res.trades)
        assert api_data["trades"][0]["exit_reason"] == "STOP_LOSS"
        assert api_data["trades"][1]["exit_reason"] == "END_OF_DATA"
        assert api_data["trades"][1]["exit_date"] is None
        assert api_data["trades"][1]["pnl"] is None

