"""Tests de la lógica de liquidación (legs y combinadas) y del flujo de
almacenamiento + ajuste de bankroll."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.models import Fixture
from src.settlement.settle import _settle_single_bet, leg_outcome, settle_leg_market
from src.storage.db import Bet, BetLeg


def _fixture(fid, hg, ag, status="FT") -> Fixture:
    return Fixture(
        fixture_id=fid, date_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
        status_short=status, round_name="Group Stage - 1",
        home_team_id=fid * 10, home_team="H", away_team_id=fid * 10 + 1, away_team="A",
        home_goals=hg, away_goals=ag,
    )


# ───────────────────────── leg outcomes ─────────────────────────

def test_leg_outcome_1x2():
    assert leg_outcome("1X2", "HOME", 2, 1) is True
    assert leg_outcome("1X2", "HOME", 1, 1) is False
    assert leg_outcome("1X2", "DRAW", 1, 1) is True
    assert leg_outcome("1X2", "AWAY", 0, 2) is True


def test_leg_outcome_over_under():
    assert leg_outcome("OU2.5", "OVER", 2, 1) is True   # 3 goles
    assert leg_outcome("OU2.5", "OVER", 1, 1) is False  # 2 goles
    assert leg_outcome("OU2.5", "UNDER", 1, 1) is True
    assert leg_outcome("OU2.5", "UNDER", 2, 2) is False


def test_settle_leg_market_estados():
    assert settle_leg_market("1X2", "HOME", _fixture(1, 2, 0, "FT")) == "won"
    assert settle_leg_market("1X2", "AWAY", _fixture(1, 2, 0, "FT")) == "lost"
    assert settle_leg_market("1X2", "HOME", _fixture(1, None, None, "NS")) == "pending"
    assert settle_leg_market("1X2", "HOME", _fixture(1, None, None, "PST")) == "void"


# ───────────────────────── combinadas ─────────────────────────

def _bet_with_legs(legs) -> Bet:
    # Asigna ids a las legs (como haría la DB).
    for i, leg in enumerate(legs, start=1):
        leg.id = i
    return Bet(tipo="parlay", stake=10.0, cuota_combinada=4.0, prob=0.3,
               edge=0.2, legs=legs)


def test_parlay_gana_si_todas_ganan():
    legs = [BetLeg(1, "1X2", "HOME", 2.0, 0.6), BetLeg(2, "OU2.5", "OVER", 2.0, 0.55)]
    bet = _bet_with_legs(legs)
    results = {1: _fixture(1, 2, 0), 2: _fixture(2, 2, 1)}
    estado, payout, _ = _settle_single_bet(bet, results)
    assert estado == "won"
    assert payout == pytest.approx(40.0)  # 10 * 2.0 * 2.0


def test_parlay_pierde_si_una_pierde():
    legs = [BetLeg(1, "1X2", "HOME", 2.0, 0.6), BetLeg(2, "OU2.5", "OVER", 2.0, 0.55)]
    bet = _bet_with_legs(legs)
    results = {1: _fixture(1, 2, 0), 2: _fixture(2, 0, 0)}  # under -> over pierde
    estado, payout, _ = _settle_single_bet(bet, results)
    assert estado == "lost"
    assert payout == 0.0


def test_parlay_void_no_multiplica():
    # Una leg void se elimina; la combinada gana con la cuota superviviente.
    legs = [BetLeg(1, "1X2", "HOME", 2.0, 0.6), BetLeg(2, "1X2", "HOME", 3.0, 0.4)]
    bet = _bet_with_legs(legs)
    results = {1: _fixture(1, 2, 0, "FT"), 2: _fixture(2, None, None, "PST")}
    estado, payout, _ = _settle_single_bet(bet, results)
    assert estado == "won"
    assert payout == pytest.approx(20.0)  # 10 * 2.0 (la void no multiplica)


def test_parlay_todas_void_devuelve_stake():
    legs = [BetLeg(1, "1X2", "HOME", 2.0, 0.6)]
    bet = _bet_with_legs(legs)
    results = {1: _fixture(1, None, None, "CANC")}
    estado, payout, _ = _settle_single_bet(bet, results)
    assert estado == "void"
    assert payout == pytest.approx(10.0)


def test_parlay_pendiente_si_falta_resultado():
    legs = [BetLeg(1, "1X2", "HOME", 2.0, 0.6), BetLeg(2, "1X2", "HOME", 2.0, 0.5)]
    bet = _bet_with_legs(legs)
    results = {1: _fixture(1, 2, 0, "FT")}  # falta el fixture 2
    assert _settle_single_bet(bet, results) is None


# ───────────────────────── storage + bankroll ─────────────────────────

def test_store_descuenta_y_acredita(store):
    inicial = store.current_bankroll()
    legs = [BetLeg(fixture_id=1, market="1X2", selection="HOME", odds=2.0, model_prob=0.6)]
    bet = Bet(tipo="single", stake=50.0, cuota_combinada=2.0, prob=0.6, edge=0.2, legs=legs)
    bet_id = store.place_paper_bet(bet)
    assert store.current_bankroll() == pytest.approx(inicial - 50.0)

    saved = store.get_bet(bet_id)
    leg_id = saved.legs[0].id
    store.settle_bet(bet_id, "won", 100.0, {leg_id: "won"})
    # Se acredita el payout (stake ya descontado): inicial -50 +100 = inicial+50.
    assert store.current_bankroll() == pytest.approx(inicial + 50.0)
    assert store.get_bet(bet_id).estado == "won"


def test_store_rechaza_stake_mayor_que_bankroll(store):
    legs = [BetLeg(fixture_id=1, market="1X2", selection="HOME", odds=2.0, model_prob=0.6)]
    bet = Bet(tipo="single", stake=99999.0, cuota_combinada=2.0, prob=0.6, edge=0.2, legs=legs)
    with pytest.raises(ValueError):
        store.place_paper_bet(bet)
