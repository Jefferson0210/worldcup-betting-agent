"""Predicción del modelo vs resultado real, partido por partido.

Para cada partido con una apuesta paper registrada, reúne:
  * lo que predijo el modelo (P(1/X/2), P(O/U 2.5) y su "pick" más probable),
    recalculado con los ratings activos (cargados del histórico);
  * la apuesta de valor registrada (selección, cuota, edge, stake);
  * el resultado real (de `data/results.csv`, si ya está disponible);
  * DOS veredictos separados:
       1) ¿acertó la PREDICCIÓN? (pick 1X2 del modelo == resultado real)
       2) ¿ganó la APUESTA?      (la value bet registrada acertó / perdió / pend.)

La apuesta de valor NO suele ser el favorito (el modelo apuesta donde ve valor,
a cuota alta), así que el pick y la apuesta pueden ser equipos distintos: por eso
los veredictos van separados.

Reutiliza el modelo (`PoissonModel`), el store de apuestas, `paper_fixtures` y la
liquidación existente. Sin red.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import CONFIG, Config
from src.data.models import Fixture
from src.model.poisson import PoissonModel
from src.settlement.settle import settle_leg_market
from src.storage.db import BettingStore

_SEL_ES = {
    "HOME": "1 (local)", "DRAW": "X (empate)", "AWAY": "2 (visitante)",
    "OVER": "Over 2.5", "UNDER": "Under 2.5",
}


@dataclass
class MatchPrediction:
    fixture_id: int
    fecha: str
    home: str
    away: str
    # predicción del modelo
    p_home: float
    p_draw: float
    p_away: float
    p_over: float
    p_under: float
    pick_1x2: str                      # "HOME" | "DRAW" | "AWAY"
    # apuesta registrada (si la hubo)
    bet_tipo: Optional[str] = None     # "single" | "parlay"
    bet_market: Optional[str] = None
    bet_selection: Optional[str] = None
    bet_odds: Optional[float] = None
    bet_edge: Optional[float] = None
    bet_stake: Optional[float] = None
    # resultado real
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    # veredictos
    pred_correct: Optional[bool] = None        # 1) ¿acertó la predicción?
    bet_result: str = "pendiente"              # 2) won|lost|void|pendiente|sin_apuesta

    @property
    def has_result(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def actual_1x2(self) -> Optional[str]:
        if not self.has_result:
            return None
        if self.home_goals > self.away_goals:  # type: ignore[operator]
            return "HOME"
        if self.home_goals == self.away_goals:
            return "DRAW"
        return "AWAY"


def _pick_1x2(p_home: float, p_draw: float, p_away: float) -> str:
    return max({"HOME": p_home, "DRAW": p_draw, "AWAY": p_away}.items(), key=lambda kv: kv[1])[0]


def build_predictions(
    store: BettingStore,
    model: PoissonModel,
    paper_store,
    hist_matches: list,
    *,
    config: Config = CONFIG,
    date_filter: Optional[str] = None,
    team_filter: Optional[str] = None,
) -> list[MatchPrediction]:
    """Construye la lista de predicciones vs resultado para los partidos apostados.

    `hist_matches` son los HistMatch de `results.csv` (para el resultado real).
    """
    # Resultado real por (fecha, equipos) — reutiliza el matcher del modo paper.
    from src.paper.runner import _find_result, _index_hist

    idx = _index_hist(hist_matches)

    # Mapa fixture_id -> (bet, leg) de la apuesta registrada (dedupe: 1 por partido).
    leg_by_fixture: dict[int, tuple] = {}
    for bet in store.all_bets():
        for leg in bet.legs:
            leg_by_fixture.setdefault(leg.fixture_id, (bet, leg))

    out: list[MatchPrediction] = []
    for pf in paper_store.all():
        if date_filter and pf.fecha != date_filter:
            continue
        if team_filter:
            tf = team_filter.lower()
            if tf not in pf.home.lower() and tf not in pf.away.lower():
                continue

        probs = model.probabilities(
            pf.fixture_id, pf.home, pf.away, neutral=config.mundial_es_neutral
        )
        pick = _pick_1x2(probs.p_home, probs.p_draw, probs.p_away)

        mp = MatchPrediction(
            fixture_id=pf.fixture_id, fecha=pf.fecha, home=pf.home, away=pf.away,
            p_home=probs.p_home, p_draw=probs.p_draw, p_away=probs.p_away,
            p_over=probs.p_over_25, p_under=probs.p_under_25, pick_1x2=pick,
        )

        # Apuesta registrada (si existe).
        entry = leg_by_fixture.get(pf.fixture_id)
        if entry is not None:
            bet, leg = entry
            mp.bet_tipo = bet.tipo
            mp.bet_market = leg.market
            mp.bet_selection = leg.selection
            mp.bet_odds = leg.odds
            # Edge a nivel de leg (prob_modelo·cuota − 1), más informativo por
            # partido que el edge combinado de una combinada.
            mp.bet_edge = leg.model_prob * leg.odds - 1.0
            mp.bet_stake = bet.stake
        else:
            mp.bet_result = "sin_apuesta"

        # Resultado real desde results.csv.
        res = _find_result(idx, pf.fecha, pf.home, pf.away)
        if res is not None:
            hg, ag = res
            mp.home_goals, mp.away_goals = hg, ag
            # Veredicto 1: ¿predicción acertada?
            mp.pred_correct = (mp.pick_1x2 == mp.actual_1x2)
            # Veredicto 2: ¿apuesta ganada? (si hubo apuesta)
            if entry is not None:
                fx = Fixture(
                    fixture_id=pf.fixture_id, date_utc=_iso_dt(pf.fecha), status_short="FT",
                    round_name="FIFA World Cup", home_team_id=0, home_team=pf.home,
                    away_team_id=0, away_team=pf.away, home_goals=hg, away_goals=ag,
                )
                mp.bet_result = settle_leg_market(mp.bet_market, mp.bet_selection, fx)
        # Si no hay resultado, bet_result queda "pendiente" (o "sin_apuesta").

        out.append(mp)

    out.sort(key=lambda m: (m.fecha, m.home))
    return out


def _iso_dt(fecha: str):
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(fecha + "T00:00:00+00:00").astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


# ─────────────────────────── render ───────────────────────────

def _si_no(v: Optional[bool]) -> str:
    if v is None:
        return "pendiente"
    return "SÍ" if v else "NO"


def _bet_verdict_es(result: str) -> str:
    return {
        "won": "SÍ (ganada)", "lost": "NO (perdida)", "void": "anulada",
        "pendiente": "pendiente", "sin_apuesta": "—",
    }.get(result, result)


def render_predictions(preds: list[MatchPrediction]) -> str:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  PREDICCIONES DEL MODELO vs RESULTADO — MUNDIAL 2026")
    lines.append("=" * 70)
    lines.append("  Nota: la APUESTA de valor no suele ser el favorito (el modelo apuesta")
    lines.append("  donde ve valor, a cuota alta). Por eso 'pick' y 'apuesta' pueden ser")
    lines.append("  equipos distintos, y los dos veredictos van separados.")
    if not preds:
        lines.append("\n  (No hay partidos con predicción/apuesta registrada.)")
        lines.append("=" * 70)
        return "\n".join(lines)

    n_pred = sum(1 for p in preds if p.pred_correct is not None)
    n_pred_ok = sum(1 for p in preds if p.pred_correct)
    n_bet_dec = sum(1 for p in preds if p.bet_result in {"won", "lost"})
    n_bet_won = sum(1 for p in preds if p.bet_result == "won")

    for p in preds:
        lines.append("")
        lines.append(f"  {p.fecha}  {p.home} vs {p.away}")
        lines.append(
            f"    Modelo: P(1)={p.p_home*100:4.1f}%  P(X)={p.p_draw*100:4.1f}%  "
            f"P(2)={p.p_away*100:4.1f}%   |  O2.5={p.p_over*100:4.1f}%  U2.5={p.p_under*100:4.1f}%"
        )
        lines.append(f"    Pick del modelo (1X2): {_SEL_ES.get(p.pick_1x2, p.pick_1x2)}")
        if p.bet_selection:
            tipo = f" [{p.bet_tipo}]" if p.bet_tipo else ""
            lines.append(
                f"    Apuesta registrada: {p.bet_market}:{_SEL_ES.get(p.bet_selection, p.bet_selection)} "
                f"@ {p.bet_odds:.2f}  edge={(p.bet_edge or 0)*100:.1f}%  stake={p.bet_stake:.2f}{tipo}"
            )
        else:
            lines.append("    Apuesta registrada: (ninguna)")
        if p.has_result:
            res_1x2 = {"HOME": p.home, "DRAW": "empate", "AWAY": p.away}[p.actual_1x2]  # type: ignore[index]
            lines.append(f"    Resultado real: {p.home_goals}-{p.away_goals}  ({res_1x2})")
        else:
            lines.append("    Resultado real: pendiente (sin marcador en results.csv)")
        lines.append(f"    ¿Predicción acertada?  {_si_no(p.pred_correct)}")
        lines.append(f"    ¿Apuesta ganada?       {_bet_verdict_es(p.bet_result)}")

    lines.append("")
    lines.append("-" * 70)
    lines.append("  RESUMEN")
    if n_pred:
        lines.append(f"    Predicciones acertadas: {n_pred_ok}/{n_pred} "
                     f"({n_pred_ok/n_pred*100:.1f}%)")
    else:
        lines.append("    Predicciones acertadas: — (sin resultados aún)")
    if n_bet_dec:
        lines.append(f"    Apuestas ganadas:       {n_bet_won}/{n_bet_dec} "
                     f"({n_bet_won/n_bet_dec*100:.1f}%)")
    else:
        lines.append("    Apuestas ganadas:       — (sin liquidar aún)")
    pend = sum(1 for p in preds if not p.has_result)
    lines.append(f"    Partidos pendientes:    {pend}/{len(preds)}")
    lines.append("=" * 70)
    return "\n".join(lines)
