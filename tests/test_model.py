"""Tests de sanidad del modelo Poisson."""
from __future__ import annotations

import pytest

from src.data.models import TeamStats
from src.model.poisson import PoissonModel


def test_probabilidades_suman_uno(tmp_config):
    model = PoissonModel(tmp_config)
    probs = model.probabilities(1, "Brazil", "Qatar")
    assert probs.p_home + probs.p_draw + probs.p_away == pytest.approx(1.0, abs=1e-6)
    assert probs.p_over_25 + probs.p_under_25 == pytest.approx(1.0, abs=1e-6)


def test_favorito_local_tiene_mas_prob(tmp_config):
    model = PoissonModel(tmp_config)
    # Brazil (fuerte) local vs Qatar (débil) -> P(local) alta.
    probs = model.probabilities(1, "Brazil", "Qatar")
    assert probs.p_home > probs.p_away
    assert probs.p_home > 0.5


def test_datos_observados_afectan_lambda(tmp_config):
    model = PoissonModel(tmp_config)
    fuerte = TeamStats(team_id=1, team_name="X", played=6, goals_for=15, goals_against=2)
    flojo = TeamStats(team_id=2, team_name="Y", played=6, goals_for=2, goals_against=14)
    probs = model.probabilities(1, "X", "Y", fuerte, flojo)
    assert probs.lambda_home > probs.lambda_away
    assert probs.p_home > probs.p_away


def test_ventaja_local_incrementa_goles(tmp_config):
    model = PoissonModel(tmp_config)
    # Mismo equipo de ambos lados: la localía debe dar más goles al local.
    probs = model.probabilities(1, "Mexico", "Mexico")
    assert probs.lambda_home > probs.lambda_away
