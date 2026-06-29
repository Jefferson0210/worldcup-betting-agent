"""Liquidación: determina el resultado de cada leg y de la apuesta completa.

Reglas de combinada:
  * Todas las legs ganan  -> apuesta ganada (payout = stake · cuota_combinada).
  * Alguna leg pierde      -> apuesta perdida (payout = 0).
  * Legs void (partido cancelado/pospuesto): la leg se quita de la combinada;
    su cuota deja de multiplicar (se trata como cuota 1.0). Si TODAS las legs
    quedan void -> apuesta void (devolución del stake).

Esto se calcula sobre los resultados reales (status FT/AET/PEN).
"""
from __future__ import annotations

from typing import Optional

from config import CONFIG, Config
from src.data.api_client import ApiFootballClient
from src.data.models import Fixture
from src.storage.db import Bet, BettingStore


def leg_outcome(market: str, selection: str, home_goals: int, away_goals: int) -> bool:
    """¿Acierta esta leg dado el marcador final? (True = gana)."""
    if market == "1X2":
        if selection == "HOME":
            return home_goals > away_goals
        if selection == "DRAW":
            return home_goals == away_goals
        if selection == "AWAY":
            return home_goals < away_goals
        raise ValueError(f"Selección 1X2 desconocida: {selection!r}")

    if market == "OU2.5":
        total = home_goals + away_goals
        if selection == "OVER":
            return total >= 3
        if selection == "UNDER":
            return total <= 2
        raise ValueError(f"Selección OU2.5 desconocida: {selection!r}")

    raise ValueError(f"Mercado no soportado en liquidación: {market!r}")


def settle_leg_market(market: str, selection: str, fixture: Fixture) -> str:
    """Resultado de una leg según el estado del partido: won|lost|void|pending."""
    if fixture.is_void:
        return "void"
    if not fixture.is_finished or fixture.home_goals is None or fixture.away_goals is None:
        return "pending"
    won = leg_outcome(market, selection, fixture.home_goals, fixture.away_goals)
    return "won" if won else "lost"


def _settle_single_bet(bet: Bet, results: dict[int, Fixture]) -> Optional[tuple[str, float, dict[int, str]]]:
    """Calcula (estado, payout, leg_results) o None si aún no liquidable.

    No toca la base de datos; es pura para poder testearla.
    """
    leg_results: dict[int, str] = {}
    any_pending = False
    any_lost = False
    all_void = True
    surviving_odds = 1.0

    for leg in bet.legs:
        fixture = results.get(leg.fixture_id)
        if fixture is None:
            any_pending = True
            continue
        outcome = settle_leg_market(leg.market, leg.selection, fixture)
        leg_results[leg.id] = outcome  # type: ignore[index]
        if outcome == "pending":
            any_pending = True
        elif outcome == "void":
            pass  # no multiplica
        else:
            all_void = False
            if outcome == "lost":
                any_lost = True
            else:  # won
                surviving_odds *= leg.odds

    # Si cualquier leg ya perdió, la combinada está perdida (aunque falten otras).
    if any_lost:
        return ("lost", 0.0, leg_results)

    # Si quedan legs por resolver y nada ha perdido, todavía no se puede liquidar.
    if any_pending:
        return None

    # Todas resueltas, ninguna perdió.
    if all_void:
        return ("void", round(bet.stake, 2), leg_results)  # devolución

    # Ganada con las legs supervivientes (las void no multiplican).
    payout = round(bet.stake * surviving_odds, 2)
    return ("won", payout, leg_results)


def settle_pending_bets(
    store: BettingStore,
    client: ApiFootballClient,
    *,
    config: Config = CONFIG,
) -> list[tuple[int, str, float]]:
    """Liquida todas las apuestas pendientes usando resultados reales.

    Devuelve una lista de (bet_id, estado, payout) de las que se liquidaron.
    Las que aún tienen legs pendientes se dejan tal cual.
    """
    pending = store.pending_bets()
    if not pending:
        return []

    # Reunir todos los fixtures necesarios en una sola llamada.
    fixture_ids = sorted({leg.fixture_id for bet in pending for leg in bet.legs})
    results = client.get_results(fixture_ids)

    settled: list[tuple[int, str, float]] = []
    for bet in pending:
        outcome = _settle_single_bet(bet, results)
        if outcome is None:
            continue
        estado, payout, leg_results = outcome
        store.settle_bet(bet.id, estado, payout, leg_results)  # type: ignore[arg-type]
        settled.append((bet.id, estado, payout))  # type: ignore[arg-type]
    return settled
