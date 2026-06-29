"""Tests del generador de la página HTML de predicciones (sin red)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.model.odds_history import OddsTriple
from src.model.poisson import PoissonModel
from src.reporting.webpage import (
    PagePrediction,
    build_day_parlays,
    collect_predictions,
    generate_page,
    render_html,
)


class FakeProvider:
    """list_upcoming_fixtures (+ historical_1x2 opcional para cuotas del pick)."""

    def __init__(self, fixtures, odds_map=None):
        self.fixtures = fixtures
        self.odds_map = odds_map or {}

    def list_upcoming_fixtures(self, tournament_id=None):
        return self.fixtures

    def historical_1x2(self, fixture_id, bookmakers=None):
        return self.odds_map.get(fixture_id)


def _fx(fid, home, away, dt):
    return {"fixtureId": fid, "participant1Name": home, "participant2Name": away,
            "startTime": dt}


FIXTURES = [
    _fx("idA", "Brazil", "Qatar", "2026-06-29T18:00:00.000Z"),
    _fx("idB", "France", "Japan", "2026-06-29T15:00:00.000Z"),
    # Placeholder del cuadro (equipos no reales) -> debe filtrarse.
    _fx("idW", "W75", "RU101", "2026-07-02T18:00:00.000Z"),
]

NOW = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)


def _pred(fecha, home, away, ph, pd, pa, pick, odds=None):
    return PagePrediction(
        fecha=fecha, hora="18:00 UTC", home=home, away=away,
        p_home=ph, p_draw=pd, p_away=pa, p_over=0.5, p_under=0.5,
        pick_1x2=pick, pick_odds=odds,
    )


# ───────────────────── recolección y filtro ─────────────────────

def test_collect_filtra_placeholders(tmp_config):
    model = PoissonModel(tmp_config)
    preds = collect_predictions(FakeProvider(FIXTURES), model, config=tmp_config, with_odds=False)
    assert {p.home for p in preds} == {"Brazil", "France"}   # placeholder excluido
    p = preds[0]
    assert p.p_home + p.p_draw + p.p_away == pytest.approx(1.0, abs=1e-6)


def test_collect_lee_cuota_del_pick_de_cache(tmp_config):
    model = PoissonModel(tmp_config)
    odds = {"idA": OddsTriple(1.60, 3.8, 6.0), "idB": OddsTriple(1.40, 4.5, 8.0)}
    preds = collect_predictions(FakeProvider(FIXTURES, odds), model, config=tmp_config)
    by = {p.home: p for p in preds}
    # Brazil y France son favoritos locales -> pick HOME -> cuota = home.
    assert by["Brazil"].pick_1x2 == "HOME"
    assert by["Brazil"].pick_odds == pytest.approx(1.60)
    assert by["France"].pick_odds == pytest.approx(1.40)


# ───────────────────── agrupado por día + producto ─────────────────────

def test_build_day_parlays_agrupa_por_fecha():
    preds = [
        _pred("2026-06-29", "Brazil", "Qatar", 0.60, 0.25, 0.15, "HOME", 1.7),
        _pred("2026-06-29", "France", "Japan", 0.50, 0.25, 0.25, "HOME", 2.0),
        _pred("2026-06-30", "Spain", "Morocco", 0.55, 0.25, 0.20, "HOME", 1.8),
    ]
    days = build_day_parlays(preds)
    assert [d.fecha for d in days] == ["2026-06-29", "2026-06-30"]
    d0 = days[0]
    assert d0.n == 2
    # Probabilidad combinada = producto de los picks.
    assert d0.combined_prob == pytest.approx(0.60 * 0.50)
    # Cuota combinada = producto de cuotas.
    assert d0.combined_odds == pytest.approx(1.7 * 2.0)
    assert days[1].combined_prob == pytest.approx(0.55)


def test_combinada_sin_cuota_completa_es_none():
    preds = [
        _pred("2026-06-29", "Brazil", "Qatar", 0.60, 0.25, 0.15, "HOME", 1.7),
        _pred("2026-06-29", "France", "Japan", 0.50, 0.25, 0.25, "HOME", None),  # falta cuota
    ]
    day = build_day_parlays(preds)[0]
    assert day.combined_prob == pytest.approx(0.30)
    assert day.combined_odds is None


# ───────────────────── render HTML ─────────────────────

def test_render_incluye_combinada_y_advertencia(tmp_config):
    preds = [
        _pred("2026-06-29", "Brazil", "Qatar", 0.60, 0.25, 0.15, "HOME", 1.7),
        _pred("2026-06-29", "France", "Japan", 0.50, 0.25, 0.25, "HOME", 2.0),
    ]
    days = build_day_parlays(preds)
    html = render_html(days, generated_at="2026-06-28 10:00 UTC")
    assert html.startswith("<!doctype html>")
    assert "Combinada del día" in html
    assert "30.0%" in html                       # 0.6*0.5 combinada
    assert "3.40" in html                        # 1.7*2.0 cuota combinada
    assert "multiplica el riesgo" in html        # advertencia de honestidad
    assert "favoritos" in html
    assert "asesoría" in html and "+18" in html  # disclaimer del pie


def test_dia_de_un_solo_partido_no_muestra_combinada(tmp_config):
    days = build_day_parlays([_pred("2026-06-29", "Brazil", "Qatar", 0.6, 0.25, 0.15, "HOME", 1.7)])
    html = render_html(days, generated_at="2026-06-28 10:00 UTC")
    assert "Combinada del día" not in html       # 1 partido != combinada
    assert "Brazil" in html


def test_generate_page_escribe_y_regenera(tmp_config, tmp_path):
    model = PoissonModel(tmp_config)
    out_file = tmp_path / "predicciones.html"
    odds = {"idA": OddsTriple(1.60, 3.8, 6.0), "idB": OddsTriple(1.40, 4.5, 8.0)}
    out, n = generate_page(FakeProvider(FIXTURES, odds), model, config=tmp_config,
                           out_path=out_file, now=NOW)
    assert (out, n) == (out_file, 2)
    content = out_file.read_text(encoding="utf-8")
    assert "Mundial 2026" in content
    # Brazil y France son el mismo día -> hay combinada del día.
    assert "Combinada del día" in content
    # Regenerable: sobrescribe.
    out2, n2 = generate_page(FakeProvider(FIXTURES[:1], odds), model, config=tmp_config,
                             out_path=out_file, now=NOW)
    assert n2 == 1
    assert "France" not in out_file.read_text(encoding="utf-8")


def test_pagina_vacia_no_rompe():
    html = render_html([], generated_at="2026-06-28 10:00 UTC")
    assert "<!doctype html>" in html
    assert "No hay partidos próximos" in html
