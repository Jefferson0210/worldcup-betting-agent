"""Tests de los dos veredictos: predicción acertada/fallada y apuesta won/lost/pend."""
from __future__ import annotations

from datetime import date

import pytest

from src.model.historical import HistMatch
from src.model.poisson import PoissonModel
from src.paper.store import PaperFixtureStore
from src.reporting.predictions import build_predictions
from src.storage.db import Bet, BetLeg, BettingStore


def _place(store, paper_store, fid, fecha, home, away, selection, odds, prob):
    """Registra una apuesta paper single + su metadata de fixture."""
    leg = BetLeg(fixture_id=fid, market="1X2", selection=selection, odds=odds, model_prob=prob)
    bet = Bet(tipo="single", stake=5.0, cuota_combinada=odds, prob=prob,
              edge=prob * odds - 1.0, legs=[leg])
    store.place_paper_bet(bet)
    paper_store.upsert(fid, fecha, home, away)


@pytest.fixture
def setup(tmp_config):
    store = BettingStore(tmp_config)
    paper_store = PaperFixtureStore(tmp_config)
    model = PoissonModel(tmp_config)  # usa el prior por defecto (ratings activos)
    yield store, paper_store, model, tmp_config
    store.close()
    paper_store.close()


def _by_team(preds, home):
    return next(p for p in preds if p.home == home)


def test_dos_veredictos(setup):
    store, paper_store, model, cfg = setup

    # A) Favorito local, apuesta a HOME, gana 3-0 -> pred OK + apuesta ganada.
    _place(store, paper_store, 101, "2026-06-29", "Brazil", "Qatar", "HOME", 1.6, 0.62)
    # B) Favorito local, pero apuesta al VISITANTE (underdog); gana el local 2-0
    #    -> pred OK (pick=local) pero apuesta PERDIDA. (la separación clave)
    _place(store, paper_store, 102, "2026-06-29", "France", "Japan", "AWAY", 5.3, 0.16)
    # C) Sin resultado en results.csv -> pendiente en ambos veredictos.
    _place(store, paper_store, 103, "2026-06-30", "Spain", "Morocco", "HOME", 1.7, 0.60)
    # D) Upset: local débil gana; el pick del modelo era el visitante -> pred FALLA.
    _place(store, paper_store, 104, "2026-07-01", "Qatar", "Brazil", "AWAY", 1.6, 0.62)

    hist = [
        HistMatch(date(2026, 6, 29), "Brazil", "Qatar", 3, 0, "FIFA World Cup", True),
        HistMatch(date(2026, 6, 29), "France", "Japan", 2, 0, "FIFA World Cup", True),
        # Spain vs Morocco: NO está -> pendiente.
        HistMatch(date(2026, 7, 1), "Qatar", "Brazil", 1, 0, "FIFA World Cup", True),
    ]

    preds = build_predictions(store, model, paper_store, hist, config=cfg)
    assert len(preds) == 4

    a = _by_team(preds, "Brazil")
    assert a.pick_1x2 == "HOME"            # Brazil favorito
    assert a.pred_correct is True          # acertó la predicción
    assert a.bet_result == "won"           # y la apuesta (HOME) ganó

    b = _by_team(preds, "France")
    assert b.pick_1x2 == "HOME"            # el modelo predice al local
    assert b.pred_correct is True          # predicción acertada...
    assert b.bet_result == "lost"          # ...pero la apuesta (AWAY) perdió

    c = _by_team(preds, "Spain")
    assert c.has_result is False
    assert c.pred_correct is None          # pendiente
    assert c.bet_result == "pendiente"

    d = _by_team(preds, "Qatar")
    assert d.pick_1x2 == "AWAY"            # el modelo prefería a Brazil (visitante)
    assert d.actual_1x2 == "HOME"          # pero ganó Qatar (local)
    assert d.pred_correct is False         # predicción FALLADA
    assert d.bet_result == "lost"          # apuesta (AWAY) perdida


def test_filtros_por_fecha_y_equipo(setup):
    store, paper_store, model, cfg = setup
    _place(store, paper_store, 201, "2026-06-29", "Brazil", "Qatar", "HOME", 1.6, 0.62)
    _place(store, paper_store, 202, "2026-06-30", "Spain", "Morocco", "HOME", 1.7, 0.60)

    solo_29 = build_predictions(store, model, paper_store, [], config=cfg, date_filter="2026-06-29")
    assert [p.home for p in solo_29] == ["Brazil"]

    solo_spain = build_predictions(store, model, paper_store, [], config=cfg, team_filter="spain")
    assert [p.home for p in solo_spain] == ["Spain"]


def test_over_under_presente_en_prediccion(setup):
    store, paper_store, model, cfg = setup
    _place(store, paper_store, 301, "2026-06-29", "Brazil", "Qatar", "HOME", 1.6, 0.62)
    preds = build_predictions(store, model, paper_store, [], config=cfg)
    p = preds[0]
    assert p.p_over + p.p_under == pytest.approx(1.0, abs=1e-6)
    assert p.p_home + p.p_draw + p.p_away == pytest.approx(1.0, abs=1e-6)
