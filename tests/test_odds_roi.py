"""Tests del CSV de cuotas, el emparejado de nombres y el ROI real (sin red)."""
from __future__ import annotations

from datetime import date

import pytest

from src.model.backtest import run_backtest
from src.model.historical import HistMatch
from src.model.odds_history import (
    OddsBook,
    OddsEntry,
    OddsTriple,
    load_odds_csv,
    parse_odds_rows,
)


# ───────────────────────── parser del CSV ─────────────────────────

CSV_STD = (
    "date,home_team,away_team,odds_home,odds_draw,odds_away\n"
    "2022-12-18,Argentina,France,2.40,3.10,3.00\n"
    "2022-12-14,France,Morocco,1.70,3.50,5.50\n"
    "malformada,,,,,\n"                                   # se salta
    "2022-12-13,Argentina,Croatia,1.55,4.00,6.50\n"
)

# CSV con nombres de columna alternativos (autodetección).
CSV_ALT = (
    "match_date,home,away,1,X,2\n"
    "2022-12-18,Argentina,France,2.40,3.10,3.00\n"
)


def test_parse_csv_estandar(tmp_path):
    p = tmp_path / "odds.csv"
    p.write_text(CSV_STD, encoding="utf-8")
    book = load_odds_csv(p)
    assert len(book) == 3  # la fila malformada se descarta
    triple = book.lookup(date(2022, 12, 18), "Argentina", "France")
    assert triple is not None
    assert triple.home == pytest.approx(2.40)
    assert triple.away == pytest.approx(3.00)


def test_autodeteccion_columnas():
    book = OddsBook(parse_odds_rows(
        [{"match_date": "2022-12-18", "home": "Argentina", "away": "France",
          "1": "2.40", "X": "3.10", "2": "3.00"}]
    ))
    assert len(book) == 1
    assert book.lookup(date(2022, 12, 18), "Argentina", "France").draw == pytest.approx(3.10)


def test_csv_sin_columnas_minimas_lanza():
    with pytest.raises(ValueError, match="no tiene columnas"):
        parse_odds_rows([{"fecha": "2022-12-18", "local": "A", "visitante": "B"}])


def test_column_map_explicito():
    rows = [{"d": "2022-12-18", "h": "Argentina", "a": "France",
             "oh": "2.4", "od": "3.1", "oa": "3.0"}]
    cmap = {"date": "d", "home_team": "h", "away_team": "a",
            "odds_home": "oh", "odds_draw": "od", "odds_away": "oa"}
    book = OddsBook(parse_odds_rows(rows, cmap))
    assert len(book) == 1


# ───────────────────── emparejado de nombres ─────────────────────

def test_matching_normaliza_alias():
    # Cuotas con "United States"; el partido usa "USA" (alias del dataset).
    book = OddsBook([OddsEntry(
        fecha=date(2022, 11, 25), home="USA", away="England",
        triple=OddsTriple(3.2, 3.1, 2.4),
    )])
    # normalize_team("United States") -> "USA"; debe emparejar.
    t = book.lookup(date(2022, 11, 25), "United States", "England")
    assert t is not None and t.home == pytest.approx(3.2)


def test_matching_tolerancia_de_fecha():
    book = OddsBook([OddsEntry(
        fecha=date(2022, 11, 21), home="Argentina", away="Saudi Arabia",
        triple=OddsTriple(1.3, 5.0, 9.0),
    )])
    # Partido a 1 día -> empareja con day_tol=1.
    assert book.lookup(date(2022, 11, 22), "Argentina", "Saudi Arabia") is not None
    # A 3 días -> no.
    assert book.lookup(date(2022, 11, 24), "Argentina", "Saudi Arabia") is None


def test_matching_orientacion_invertida():
    # El libro tiene el partido con local/visitante intercambiados.
    book = OddsBook([OddsEntry(
        fecha=date(2022, 12, 18), home="France", away="Argentina",
        triple=OddsTriple(home=3.00, draw=3.10, away=2.40),
    )])
    # Buscamos Argentina (local) vs France (visitante): debe invertir las cuotas.
    t = book.lookup(date(2022, 12, 18), "Argentina", "France")
    assert t is not None
    assert t.home == pytest.approx(2.40)  # cuota de Argentina
    assert t.away == pytest.approx(3.00)  # cuota de France


def test_no_empareja_equipos_distintos():
    book = OddsBook([OddsEntry(
        fecha=date(2022, 12, 18), home="Brazil", away="Germany",
        triple=OddsTriple(2.0, 3.0, 4.0),
    )])
    assert book.lookup(date(2022, 12, 18), "Argentina", "France") is None


# ───────────────────── ROI real en el backtest ─────────────────────

def _wc_match(d, home, away, hg, ag):
    return HistMatch(fecha=d, home=home, away=away, home_goals=hg, away_goals=ag,
                     tournament="FIFA World Cup", neutral=True)


def test_roi_real_se_calcula_y_empareja(tmp_config):
    # Historial: muchos amistosos para entrenar + un "Mundial" de 2 partidos.
    train = [_wc_match(date(2000 + i // 12, (i % 12) + 1, 1),
                       "Brazil" if i % 2 else "Spain",
                       "Qatar" if i % 2 else "Bolivia", 3, 0)
             for i in range(300)]
    for m in train:
        object.__setattr__(m, "tournament", "Friendly")
    wc = [
        _wc_match(date(2023, 6, 10), "Brazil", "Qatar", 4, 0),
        _wc_match(date(2023, 6, 11), "Spain", "Bolivia", 2, 1),
    ]
    matches = train + wc

    # Cuotas reales generosas para los favoritos -> habrá value bets.
    book = OddsBook([
        OddsEntry(date(2023, 6, 10), "Brazil", "Qatar", OddsTriple(1.9, 3.5, 5.0)),
        OddsEntry(date(2023, 6, 11), "Spain", "Bolivia", OddsTriple(1.8, 3.6, 5.5)),
    ])

    res = run_backtest(
        matches, config=tmp_config, tournament_filter="FIFA World Cup",
        min_train=50, odds_book=book,
    )
    assert res.has_real_odds is True
    assert res.odds_matched == 2          # ambos partidos emparejaron
    assert res.odds_unmatched == 0
    assert res.real_bets >= 1             # al menos una apuesta de valor
    # El bankroll se movió y el ROI es un número finito.
    assert res.real_bankroll != res.real_bankroll0
    assert isinstance(res.real_roi, float)
    assert res.bankroll_curve  # hay curva


def test_sin_odds_book_no_calcula_roi_real(tmp_config):
    matches = [_wc_match(date(2023, 6, 10), "Brazil", "Qatar", 2, 0)]
    matches += [HistMatch(date(2000, 1, 1), "A", "B", 1, 0, "Friendly", True)] * 60
    res = run_backtest(matches, config=tmp_config, tournament_filter="FIFA World Cup",
                       min_train=50, odds_book=None)
    assert res.has_real_odds is False
    assert res.real_bets == 0
    assert res.odds_matched == 0


def test_roi_real_edge_y_liquidacion_directa(tmp_config):
    # Edge claro: modelo fuerte favorito a cuota alta -> value bet ganadora.
    book = OddsBook([OddsEntry(date(2023, 6, 10), "Brazil", "Qatar",
                               OddsTriple(2.5, 4.0, 6.0))])
    train = [HistMatch(date(2000, 1, 1) , "Brazil", "Qatar", 5, 0, "Friendly", True)] * 80
    wc = [_wc_match(date(2023, 6, 10), "Brazil", "Qatar", 3, 0)]
    res = run_backtest(train + wc, config=tmp_config, tournament_filter="FIFA World Cup",
                       min_train=50, odds_book=book)
    assert res.real_bets >= 1
    # Brazil ganó: si apostó a HOME, debió ganar (P&L positivo).
    if res.real_wins:
        assert res.real_pnl > 0
