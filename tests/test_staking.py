"""Tests de la fracción de Kelly y del staking con tope."""
from __future__ import annotations

import pytest

from src.value.staking import kelly_fraction, stake_for_bet


def test_kelly_fraction_basico():
    # p=0.6, d=2.0 -> (1.2-1)/(2-1) = 0.2
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.2)
    # p=0.5, d=2.0 -> 0 (apuesta justa)
    assert kelly_fraction(0.5, 2.0) == pytest.approx(0.0)


def test_kelly_negativo_sin_valor():
    # p=0.4, d=2.0 -> (0.8-1)/1 = -0.2
    assert kelly_fraction(0.4, 2.0) == pytest.approx(-0.2)


def test_kelly_cuota_invalida():
    with pytest.raises(ValueError):
        kelly_fraction(0.5, 1.0)


def test_stake_kelly_fraccionado(tmp_config):
    # Kelly pleno 0.2; fracción 0.25 -> 0.05; bankroll 1000 -> 50, justo en el tope 5%.
    dec = stake_for_bet(0.6, 2.0, 1000.0, config=tmp_config)
    assert dec.kelly_full == pytest.approx(0.2)
    assert dec.kelly_applied == pytest.approx(0.05)
    assert dec.stake == pytest.approx(50.0)
    assert dec.capped is False


def test_stake_respeta_tope(tmp_config):
    # Kelly muy alto -> raw stake supera el 5% -> se topa a 50.
    dec = stake_for_bet(0.9, 3.0, 1000.0, config=tmp_config)
    # Kelly pleno = (2.7-1)/2 = 0.85; aplicado 0.2125; raw 212.5 > tope 50.
    assert dec.stake == pytest.approx(50.0)
    assert dec.capped is True


def test_stake_cero_sin_valor(tmp_config):
    dec = stake_for_bet(0.4, 2.0, 1000.0, config=tmp_config)
    assert dec.stake == 0.0


def test_stake_cero_bankroll_no_negativo(tmp_config):
    dec = stake_for_bet(0.6, 2.0, 0.0, config=tmp_config)
    assert dec.stake == 0.0
