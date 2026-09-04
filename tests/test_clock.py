from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest
from tradepilot.alpaca.clock import is_market_open
from tradepilot.core.models import MarketStatus

@patch("tradepilot.alpaca.clock.get_alpaca_client")
def test_is_market_open_when_open(mock_get_client):
    mock_client = MagicMock()
    mock_clock = MagicMock()
    mock_clock.is_open = True
    mock_clock.timestamp = datetime(2023, 10, 25, 14, 30, tzinfo=timezone.utc)
    mock_clock.next_open = datetime(2023, 10, 26, 13, 30, tzinfo=timezone.utc)
    mock_clock.next_close = datetime(2023, 10, 25, 20, 00, tzinfo=timezone.utc)
    mock_client.get_clock.return_value = mock_clock
    mock_get_client.return_value = mock_client

    status = is_market_open()

    assert status.is_open is True
    assert status.timestamp == mock_clock.timestamp
    assert status.next_open == mock_clock.next_open
    assert status.next_close == mock_clock.next_close

@patch("tradepilot.alpaca.clock.get_alpaca_client")
def test_is_market_open_when_closed(mock_get_client):
    mock_client = MagicMock()
    mock_clock = MagicMock()
    mock_clock.is_open = False
    mock_clock.timestamp = datetime(2023, 10, 25, 22, 30, tzinfo=timezone.utc)
    mock_clock.next_open = datetime(2023, 10, 26, 13, 30, tzinfo=timezone.utc)
    mock_clock.next_close = datetime(2023, 10, 26, 20, 00, tzinfo=timezone.utc)
    mock_client.get_clock.return_value = mock_clock
    mock_get_client.return_value = mock_client

    status = is_market_open()

    assert status.is_open is False
    assert status.timestamp == mock_clock.timestamp
    assert status.next_open == mock_clock.next_open
    assert status.next_close == mock_clock.next_close
