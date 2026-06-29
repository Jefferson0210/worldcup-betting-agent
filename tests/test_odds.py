"""Tests de conversión de cuotas, overround y normalización de /odds."""
from __future__ import annotations

import math

import pytest

from src.data.models import MarketOdds
from src.value.value_engine import decimal_to_implied_prob, overround
from tests.conftest import ODDS_RESPONSE_FIXTURE_100


def test_decimal_to_implied_prob():
    assert decimal_to_implied_prob(2.0) == pytest.approx(0.5)
    assert decimal_to_implied_prob(4.0) == pytest.approx(0.25)
    assert decimal_to_implied_prob(1.25) == pytest.approx(0.8)


def test_decimal_to_implied_prob_invalida():
    with pytest.raises(ValueError):
        decimal_to_implied_prob(1.0)
    with pytest.raises(ValueError):
        decimal_to_implied_prob(0.5)


def test_overround_positivo():
    # 1X2 con margen: 1/2.10 + 1/3.40 + 1/3.60 ≈ 1.04 -> overround ≈ 0.04
    odds = [2.10, 3.40, 3.60]
    orr = overround(odds)
    expected = sum(1 / o for o in odds) - 1
    assert orr == pytest.approx(expected)
    assert orr > 0


def test_market_odds_normaliza_y_promedia():
    mo = MarketOdds.from_api(ODDS_RESPONSE_FIXTURE_100)
    assert mo is not None
    assert mo.fixture_id == 100
    assert mo.get("1X2", "HOME") == pytest.approx(2.10)
    assert mo.get("1X2", "DRAW") == pytest.approx(3.40)
    assert mo.get("1X2", "AWAY") == pytest.approx(3.60)
    assert mo.get("OU2.5", "OVER") == pytest.approx(2.00)
    assert mo.get("OU2.5", "UNDER") == pytest.approx(1.80)


def test_market_odds_promedia_entre_casas():
    resp = {
        "fixture": {"id": 7},
        "bookmakers": [
            {"name": "A", "bets": [{"name": "Match Winner", "values": [{"value": "Home", "odd": "2.00"}]}]},
            {"name": "B", "bets": [{"name": "Match Winner", "values": [{"value": "Home", "odd": "2.20"}]}]},
        ],
    }
    mo = MarketOdds.from_api(resp)
    assert mo.get("1X2", "HOME") == pytest.approx(2.10)  # media de 2.00 y 2.20


def test_market_odds_sin_bookmakers_devuelve_none():
    assert MarketOdds.from_api({"fixture": {"id": 1}, "bookmakers": []}) is None
