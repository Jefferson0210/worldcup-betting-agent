"""Tests del constructor de combinadas: producto de cuotas/probabilidades,
edge combinado y filtro de fixtures distintos."""
from __future__ import annotations

import math

import pytest

from src.value.parlays import build_parlays
from src.value.value_engine import ValueBet


def _vb(fixture_id, odds, prob, market="1X2", selection="HOME", edge=0.1) -> ValueBet:
    return ValueBet(
        fixture_id=fixture_id, home_team=f"H{fixture_id}", away_team=f"A{fixture_id}",
        market=market, selection=selection, odds=odds, model_prob=prob, edge=edge,
    )


def test_parlay_combina_cuotas_y_probs(tmp_config):
    # Dos legs de valor, fixtures distintos.
    vbs = [_vb(1, 2.0, 0.6, edge=0.2), _vb(2, 2.5, 0.5, edge=0.25)]
    parlays = build_parlays(vbs, config=tmp_config)
    assert len(parlays) == 1
    p = parlays[0]
    assert p.combined_odds == pytest.approx(5.0)        # 2.0 * 2.5
    assert p.combined_prob == pytest.approx(0.30)       # 0.6 * 0.5
    assert p.edge == pytest.approx(0.30 * 5.0 - 1)      # 0.5
    assert p.independence_assumed is True


def test_parlay_evita_mismo_fixture(tmp_config):
    # Dos legs del MISMO fixture -> no debe combinarlas (correlación).
    vbs = [
        _vb(1, 2.0, 0.6, market="1X2", selection="HOME"),
        _vb(1, 1.9, 0.55, market="OU2.5", selection="OVER"),
    ]
    parlays = build_parlays(vbs, config=tmp_config)
    assert parlays == []


def test_parlay_solo_edge_positivo(tmp_config):
    # Combinada con edge negativo no debe aparecer.
    # odds 1.2/1.2 prob 0.5/0.5 -> combinada odds 1.44 prob 0.25 -> edge = 0.36-1 < 0
    vbs = [_vb(1, 1.2, 0.5), _vb(2, 1.2, 0.5)]
    parlays = build_parlays(vbs, config=tmp_config)
    assert parlays == []


def test_parlay_respeta_max_legs(tmp_config):
    object.__setattr__(tmp_config, "max_legs", 2)
    vbs = [_vb(1, 2.0, 0.6), _vb(2, 2.0, 0.6), _vb(3, 2.0, 0.6)]
    parlays = build_parlays(vbs, config=tmp_config)
    # Solo combinadas de 2 legs (no de 3).
    assert all(p.n_legs == 2 for p in parlays)
    # C(3,2) = 3 combinadas.
    assert len(parlays) == 3


def test_parlay_orden_por_edge(tmp_config):
    vbs = [_vb(1, 2.0, 0.6), _vb(2, 2.5, 0.55), _vb(3, 3.0, 0.5)]
    parlays = build_parlays(vbs, config=tmp_config)
    edges = [p.edge for p in parlays]
    assert edges == sorted(edges, reverse=True)
