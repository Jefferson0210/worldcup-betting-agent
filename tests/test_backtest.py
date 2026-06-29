"""Tests del backtest y del ajuste de cancha neutral del modelo (sin red)."""
from __future__ import annotations

import pytest

from src.model.backtest import run_backtest
from src.model.poisson import PoissonModel
from tests.conftest import synthetic_matches


def test_backtest_produce_metricas_sanas(tmp_config):
    _teams, _strength, matches = synthetic_matches()
    res = run_backtest(
        matches, config=tmp_config, tournament_filter="FIFA World Cup", min_train=50,
    )
    assert res.n_torneos >= 1
    assert res.n_partidos > 0
    # Métricas en rangos válidos.
    assert 0.0 <= res.accuracy <= 1.0
    assert 0.0 <= res.brier <= 2.0
    assert res.log_loss > 0.0
    # La calibración cuenta 3 observaciones (1X2) por partido.
    total_calib = sum(b.n for b in res.calib)
    assert total_calib == res.n_partidos * 3


def test_backtest_sin_coincidencias_devuelve_vacio(tmp_config):
    _teams, _strength, matches = synthetic_matches()
    res = run_backtest(
        matches, config=tmp_config, tournament_filter="Torneo Inexistente", min_train=50,
    )
    assert res.n_partidos == 0
    assert res.accuracy == 0.0


def test_min_train_evita_torneos_sin_historia(tmp_config):
    _teams, _strength, matches = synthetic_matches()
    # Con un min_train enorme, ningún torneo tiene suficiente historia previa.
    res = run_backtest(
        matches, config=tmp_config, tournament_filter="FIFA World Cup", min_train=10_000,
    )
    assert res.n_torneos == 0
    assert res.n_partidos == 0


def test_poisson_cancha_neutral_sin_ventaja_local(tmp_config):
    model = PoissonModel(tmp_config)
    # Mismo equipo a ambos lados: en cancha neutral no hay ventaja de localía.
    neutral = model.probabilities(1, "Mexico", "Mexico", neutral=True)
    assert neutral.lambda_home == pytest.approx(neutral.lambda_away)
    assert neutral.p_home == pytest.approx(neutral.p_away, abs=1e-9)
    # Con sede (no neutral) sí hay ventaja local.
    sede = model.probabilities(1, "Mexico", "Mexico", neutral=False)
    assert sede.lambda_home > sede.lambda_away
