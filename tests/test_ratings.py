"""Tests de los ratings internacionales derivados de datos (sin red)."""
from __future__ import annotations

from datetime import date

import pytest

from src.model import historical, ratings as R
from tests.conftest import synthetic_matches


# ───────────────────────── loader histórico ─────────────────────────

CSV_SAMPLE = (
    "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
    "2018-06-14,United States,South Korea,2,1,Friendly,Moscow,Russia,True\n"
    "2018-06-15,Spain,Portugal,3,3,FIFA World Cup,Sochi,Russia,False\n"
    "bad,row,here,x,y,z,c,co,True\n"            # fecha/score inválidos -> se salta
    "2018-06-16,Brazil,,1,0,Friendly,Rostov,Russia,False\n"  # away vacío -> se salta
)


def test_load_matches_normaliza_y_filtra(tmp_path):
    p = tmp_path / "results.csv"
    p.write_text(CSV_SAMPLE, encoding="utf-8")
    matches = historical.load_matches(p)
    # Solo las dos filas válidas sobreviven.
    assert len(matches) == 2
    m0 = matches[0]
    assert m0.home == "USA"               # United States -> USA
    assert m0.away == "Korea Republic"    # South Korea -> Korea Republic
    assert m0.neutral is True
    assert matches[1].neutral is False
    assert matches[1].total_goals == 6


def test_load_matches_sin_fichero(tmp_path):
    with pytest.raises(FileNotFoundError):
        historical.load_matches(tmp_path / "no_existe.csv")


# ───────────────────────── cómputo de ratings ─────────────────────────

def test_ratings_recuperan_orden_de_fuerza():
    teams, strength, matches = synthetic_matches()
    rt = R.compute_ratings(matches, half_life_days=1460, iterations=40)
    assert rt.source == "historico"
    assert len(rt) == len(teams)
    # El equipo latente más fuerte debe tener mayor Elo y mayor ataque que el más débil.
    fuerte = max(teams, key=lambda t: strength[t])
    debil = min(teams, key=lambda t: strength[t])
    assert rt.elo(fuerte) > rt.elo(debil)
    atk_f, def_f = rt.strength_multipliers(fuerte)
    atk_d, def_d = rt.strength_multipliers(debil)
    assert atk_f > atk_d
    assert def_f < def_d  # el fuerte concede menos


def test_decaimiento_temporal_pesa_lo_reciente():
    from src.model.historical import HistMatch

    # Team 'New' fue débil hasta 2010 y dominante desde 2018.
    matches = []
    for y in range(2000, 2011):
        matches.append(HistMatch(date(y, 6, 1), "New", "Rival", 0, 3, "Friendly", False))
    for y in range(2018, 2024):
        matches.append(HistMatch(date(y, 6, 1), "New", "Rival", 4, 0, "Friendly", False))

    corto = R.compute_ratings(matches, half_life_days=365, iterations=60, as_of=date(2024, 1, 1))
    largo = R.compute_ratings(matches, half_life_days=100000, iterations=60, as_of=date(2024, 1, 1))
    # Con vida media corta, lo reciente (dominante) pesa más -> ataque mayor.
    assert corto.strength_multipliers("New")[0] > largo.strength_multipliers("New")[0]


def test_as_of_excluye_partidos_futuros():
    teams, strength, matches = synthetic_matches()
    corte = date(2010, 1, 1)
    rt = R.compute_ratings(matches, as_of=corte, iterations=20)
    # mean_goals se calcula solo con partidos <= corte (no lanza, valor sano).
    assert 0.2 <= rt.mean_goals <= 5.0


# ───────────────────── singleton de ratings activos ─────────────────────

def test_set_active_cambia_delegacion():
    teams, strength, matches = synthetic_matches()
    rt = R.compute_ratings(matches, iterations=30)
    fuerte = max(teams, key=lambda t: strength[t])

    # Por defecto (prior Elo de relleno) Team* no está -> multiplicadores ~1.
    base_atk, _ = R.elo_strength_multipliers(fuerte)
    assert base_atk == pytest.approx(1.0, abs=1e-9)

    R.set_active(rt)
    data_atk, _ = R.elo_strength_multipliers(fuerte)
    assert data_atk == pytest.approx(rt.strength_multipliers(fuerte)[0])
    assert data_atk != pytest.approx(1.0, abs=1e-6)

    R.reset_active()
    assert R.elo_strength_multipliers(fuerte)[0] == pytest.approx(1.0, abs=1e-9)


def test_default_ratings_mantienen_prior_conocido():
    # Sin histórico, una selección top del prior conserva ataque > 1.
    atk, dfn = R.elo_strength_multipliers("Brazil")
    assert atk > 1.0
    assert dfn < 1.0
