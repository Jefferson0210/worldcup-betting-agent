"""Tests del cálculo de edge y de la detección de selecciones de valor."""
from __future__ import annotations

import pytest

from src.data.models import MarketOdds
from src.model.poisson import MarketProbabilities
from src.value.value_engine import edge, find_value_bets


def test_edge_basico():
    # p=0.6, d=2.0 -> 0.6*2 - 1 = 0.2
    assert edge(0.6, 2.0) == pytest.approx(0.2)
    # apuesta justa: p=0.5, d=2.0 -> 0
    assert edge(0.5, 2.0) == pytest.approx(0.0)
    # sin valor: p=0.4, d=2.0 -> -0.2
    assert edge(0.4, 2.0) == pytest.approx(-0.2)


def test_edge_valida_entradas():
    with pytest.raises(ValueError):
        edge(0.5, 1.0)
    with pytest.raises(ValueError):
        edge(1.5, 2.0)


def _probs(p_home, p_draw, p_away, p_over, p_under) -> MarketProbabilities:
    return MarketProbabilities(
        fixture_id=100, lambda_home=1.5, lambda_away=1.2,
        p_home=p_home, p_draw=p_draw, p_away=p_away,
        p_over_25=p_over, p_under_25=p_under,
    )


def test_find_value_bets_detecta_solo_valor(tmp_config):
    # Cuotas con HOME @ 2.10 (implícita 0.476). Si el modelo da 0.60 -> edge ~0.26.
    odds = MarketOdds(fixture_id=100, bookmaker="x", selections={
        ("1X2", "HOME"): 2.10,
        ("1X2", "DRAW"): 3.40,
        ("1X2", "AWAY"): 3.60,
        ("OU2.5", "OVER"): 2.00,
        ("OU2.5", "UNDER"): 1.80,
    })
    probs = _probs(0.60, 0.22, 0.18, 0.40, 0.60)
    vbs = find_value_bets(odds, probs, "Brazil", "Serbia", config=tmp_config)

    selecciones = {(vb.market, vb.selection) for vb in vbs}
    # HOME es claramente de valor; UNDER (0.60*1.80=1.08 -> edge 0.08) también.
    assert ("1X2", "HOME") in selecciones
    # DRAW (0.22*3.40=0.748 -> edge negativo) NO debe aparecer.
    assert ("1X2", "DRAW") not in selecciones
    # Orden por edge descendente.
    edges = [vb.edge for vb in vbs]
    assert edges == sorted(edges, reverse=True)
    assert all(vb.edge > tmp_config.umbral_valor for vb in vbs)


def test_find_value_bets_respeta_umbral(tmp_config):
    odds = MarketOdds(fixture_id=1, bookmaker="x", selections={("1X2", "HOME"): 2.00})
    # edge = 0.52*2 - 1 = 0.04 < umbral 0.05 -> no es valor.
    probs = _probs(0.52, 0.24, 0.24, 0.5, 0.5)
    vbs = find_value_bets(odds, probs, "A", "B", config=tmp_config)
    assert vbs == []
