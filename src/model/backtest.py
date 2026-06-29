"""Backtest del modelo sobre torneos y eliminatorias internacionales pasadas.

Para cada torneo objetivo (por defecto las Copas del Mundo del histórico):

  1. Se entrenan los ratings con TODOS los partidos ANTERIORES al inicio del
     torneo (sin fuga de información: `as_of = inicio - 1 día`).
  2. Se predice cada partido del torneo en cancha neutral (modelo Poisson).
  3. Se compara con el resultado real de los 90' (1X2) y el total de goles.

Métricas
--------
  * accuracy   : aciertos del favorito 1X2 (argmax) / partidos.
  * brier      : Brier multiclase (1X2), media por partido.
  * log_loss   : −log(prob del resultado real), media.
  * calibración: fiabilidad agrupada de las probabilidades 1X2 (pred vs real).
  * ROI REAL: si se pasa un `OddsBook` (cuotas históricas reales), se simula
    apostar el valor del modelo (edge > umbral) con el staking existente (Kelly
    fraccionado con tope) y se liquida con el resultado real. Es el yield
    verificable, claramente separado del proxy.
  * ROI (proxy): valor extraído frente a un "mercado" baseline construido SOLO
    con el Elo (sin la forma ataque/defensa) más un margen sintético. ⚠️ NO usa
    cuotas reales: es un proxy de referencia, no un ROI real.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from config import CONFIG, Config
from src.model import ratings as ratings_mod
from src.model.historical import HistMatch
from src.model.odds_history import OddsBook
from src.model.poisson import PoissonModel, TeamStrength
from src.value.staking import stake_for_bet

# Margen sintético del "mercado" baseline para el ROI proxy (overround ~6%).
MARKET_MARGIN: float = 0.06


@dataclass
class CalibBin:
    low: float
    high: float
    n: int = 0
    pred_sum: float = 0.0
    hits: int = 0

    @property
    def pred_mean(self) -> float:
        return self.pred_sum / self.n if self.n else 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0


@dataclass
class BacktestResult:
    """Resultado agregado del backtest."""

    n_partidos: int = 0
    n_torneos: int = 0
    aciertos_1x2: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0
    # ROI proxy (mercado sintético baseline-Elo, NO cuotas reales)
    apuestas: int = 0
    total_apostado: float = 0.0
    pnl: float = 0.0
    calib: list[CalibBin] = field(default_factory=list)
    torneos: list[str] = field(default_factory=list)

    # ── ROI REAL (contra cuotas históricas reales) ──
    has_real_odds: bool = False
    odds_matched: int = 0
    odds_unmatched: int = 0
    real_bets: int = 0
    real_staked: float = 0.0
    real_pnl: float = 0.0
    real_wins: int = 0
    real_losses: int = 0
    real_bankroll0: float = 0.0
    real_bankroll: float = 0.0
    bankroll_curve: list[float] = field(default_factory=list)

    @property
    def real_roi(self) -> float:
        """Yield real = P&L / total apostado (solo apuestas con cuota real)."""
        return self.real_pnl / self.real_staked if self.real_staked > 0 else 0.0

    @property
    def real_hit_rate(self) -> float:
        decididas = self.real_wins + self.real_losses
        return self.real_wins / decididas if decididas else 0.0

    @property
    def accuracy(self) -> float:
        return self.aciertos_1x2 / self.n_partidos if self.n_partidos else 0.0

    @property
    def brier(self) -> float:
        return self.brier_sum / self.n_partidos if self.n_partidos else 0.0

    @property
    def log_loss(self) -> float:
        return self.logloss_sum / self.n_partidos if self.n_partidos else 0.0

    @property
    def roi(self) -> float:
        return self.pnl / self.total_apostado if self.total_apostado > 0 else 0.0


def _strength(ratings: ratings_mod.Ratings, team: str) -> TeamStrength:
    atk, dfn = ratings.strength_multipliers(team)
    return TeamStrength(team, atk, dfn)


def _elo_strength(ratings: ratings_mod.Ratings, team: str) -> TeamStrength:
    """Fuerza baseline: deriva ataque/defensa SOLO del Elo (mercado proxy)."""
    atk, dfn = ratings_mod.elo_to_multipliers(ratings.elo(team))
    return TeamStrength(team, atk, dfn)


def _market_odds(p_home: float, p_draw: float, p_away: float) -> dict[str, float]:
    """Cuotas del mercado baseline con margen sintético sobre las probs Elo."""
    out: dict[str, float] = {}
    for key, p in (("HOME", p_home), ("DRAW", p_draw), ("AWAY", p_away)):
        p = min(0.999, max(1e-6, p))
        fair = 1.0 / p
        out[key] = max(1.01, fair / (1.0 + MARKET_MARGIN))  # margen a favor de la casa
    return out


def _actual_1x2(m: HistMatch) -> str:
    if m.home_goals > m.away_goals:
        return "HOME"
    if m.home_goals == m.away_goals:
        return "DRAW"
    return "AWAY"


def _group_tournaments(matches: list[HistMatch], filtro: str) -> list[tuple[str, list[HistMatch]]]:
    """Agrupa los partidos objetivo por (torneo, año), ordenados por fecha."""
    filtro = filtro.lower()
    grupos: dict[tuple[str, int], list[HistMatch]] = {}
    for m in matches:
        if filtro and filtro not in m.tournament.lower():
            continue
        grupos.setdefault((m.tournament, m.fecha.year), []).append(m)
    ordenados = sorted(grupos.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    return [(f"{name} {year}", sorted(ms, key=lambda x: x.fecha)) for (name, year), ms in ordenados]


def run_backtest(
    matches: list[HistMatch],
    *,
    config: Config = CONFIG,
    tournament_filter: str = "FIFA World Cup",
    min_train: int = 200,
    desde_anio: Optional[int] = None,
    n_bins: int = 10,
    model: Optional[PoissonModel] = None,
    odds_book: Optional[OddsBook] = None,
) -> BacktestResult:
    """Ejecuta el backtest walk-forward sobre los torneos objetivo.

    Parameters
    ----------
    matches : histórico completo (HistMatch), cronológico.
    tournament_filter : subcadena del nombre de torneo a backtestear.
    min_train : nº mínimo de partidos previos para entrenar ratings.
    desde_anio : ignora torneos anteriores a este año.
    odds_book : si se pasa, calcula además el ROI REAL contra cuotas históricas
        (emparejando cada partido por fecha + equipos).
    """
    model = model or PoissonModel(config)
    matches = sorted(matches, key=lambda m: m.fecha)
    grupos = _group_tournaments(matches, tournament_filter)

    res = BacktestResult()
    res.calib = [CalibBin(low=i / n_bins, high=(i + 1) / n_bins) for i in range(n_bins)]
    if odds_book is not None:
        res.has_real_odds = True
        res.real_bankroll0 = config.bankroll_inicial
        res.real_bankroll = config.bankroll_inicial

    for nombre, partidos in grupos:
        if not partidos:
            continue
        inicio = min(m.fecha for m in partidos)
        if desde_anio is not None and inicio.year < desde_anio:
            continue
        as_of = inicio - timedelta(days=1)
        train = [m for m in matches if m.fecha <= as_of]
        if len(train) < min_train:
            continue

        ratings = ratings_mod.compute_ratings(
            train,
            half_life_days=config.ratings_half_life_dias,
            iterations=config.ratings_iteraciones,
            home_advantage=config.ventaja_local,
            as_of=as_of,
        )

        res.n_torneos += 1
        res.torneos.append(nombre)

        for m in partidos:
            probs = model.probabilities_from_strengths(
                0, _strength(ratings, m.home), _strength(ratings, m.away), neutral=True
            )
            _score_match(res, probs, m, n_bins)
            _roi_match(res, ratings, model, probs, m, config)
            if odds_book is not None:
                _real_roi_match(res, probs, m, odds_book, config)

    if odds_book is not None:
        res.odds_matched = odds_book.stats.matched
        res.odds_unmatched = odds_book.stats.unmatched
    return res


def _real_roi_match(
    res: BacktestResult, probs, m: HistMatch, odds_book: OddsBook, config: Config
) -> None:
    """ROI REAL: apuesta de valor del modelo contra la cuota histórica real.

    Reúsa el staking existente (Kelly fraccionado con tope) sobre un bankroll de
    paper que se actualiza partido a partido (curva de bankroll).
    """
    triple = odds_book.lookup(m.fecha, m.home, m.away, record=True)
    if triple is None or not triple.valid():
        return
    model_p = {"HOME": probs.p_home, "DRAW": probs.p_draw, "AWAY": probs.p_away}
    real_odds = {"HOME": triple.home, "DRAW": triple.draw, "AWAY": triple.away}
    actual = _actual_1x2(m)

    for sel in ("HOME", "DRAW", "AWAY"):
        odd = real_odds[sel]
        p = model_p[sel]
        e = p * odd - 1.0
        if e <= config.umbral_valor:
            continue
        dec = stake_for_bet(p, odd, res.real_bankroll, config=config)
        if dec.stake <= 0:
            continue
        res.real_bets += 1
        res.real_staked += dec.stake
        if sel == actual:
            profit = dec.stake * (odd - 1.0)
            res.real_wins += 1
        else:
            profit = -dec.stake
            res.real_losses += 1
        res.real_pnl += profit
        res.real_bankroll += profit
        res.bankroll_curve.append(round(res.real_bankroll, 2))


def _score_match(res: BacktestResult, probs, m: HistMatch, n_bins: int) -> None:
    actual = _actual_1x2(m)
    p_map = {"HOME": probs.p_home, "DRAW": probs.p_draw, "AWAY": probs.p_away}

    res.n_partidos += 1
    # accuracy (favorito)
    pred = max(p_map, key=p_map.get)
    if pred == actual:
        res.aciertos_1x2 += 1
    # brier multiclase + log loss
    for sel, p in p_map.items():
        y = 1.0 if sel == actual else 0.0
        res.brier_sum += (p - y) ** 2
        # calibración agrupada
        idx = min(int(p * n_bins), n_bins - 1)
        b = res.calib[idx]
        b.n += 1
        b.pred_sum += p
        b.hits += int(y)
    p_actual = min(0.999999, max(1e-9, p_map[actual]))
    res.logloss_sum += -math.log(p_actual)


def _roi_match(res: BacktestResult, ratings, model, probs, m: HistMatch, config: Config) -> None:
    """ROI proxy: apuesta donde el modelo ve valor frente al mercado baseline."""
    base = model.probabilities_from_strengths(
        0, _elo_strength(ratings, m.home), _elo_strength(ratings, m.away), neutral=True
    )
    odds = _market_odds(base.p_home, base.p_draw, base.p_away)
    model_p = {"HOME": probs.p_home, "DRAW": probs.p_draw, "AWAY": probs.p_away}
    actual = _actual_1x2(m)

    for sel, cuota in odds.items():
        edge = model_p[sel] * cuota - 1.0
        if edge > config.umbral_valor:
            res.apuestas += 1
            res.total_apostado += 1.0  # stake plano de 1 unidad
            res.pnl += (cuota - 1.0) if sel == actual else -1.0


# ─────────────────────────── render ───────────────────────────

def render_backtest(res: BacktestResult) -> str:
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append("  BACKTEST — modelo Poisson sobre histórico internacional")
    lines.append("=" * 64)
    lines.append(f"  Torneos evaluados:   {res.n_torneos}")
    lines.append(f"  Partidos:            {res.n_partidos}")
    if res.n_partidos == 0:
        lines.append("\n  (Sin partidos: ¿falta el CSV histórico o el filtro no casa?)")
        lines.append("=" * 64)
        return "\n".join(lines)
    lines.append(f"  Accuracy 1X2:        {res.accuracy*100:.2f}%")
    lines.append(f"  Brier (multiclase):  {res.brier:.4f}   (menor es mejor)")
    lines.append(f"  Log-loss:            {res.log_loss:.4f}   (menor es mejor)")
    lines.append("")
    # ── ROI REAL (cuotas reales) — sección destacada ──
    if res.has_real_odds:
        lines.append("  " + "-" * 60)
        lines.append("  ROI REAL — contra CUOTAS HISTÓRICAS REALES (no proxy)")
        lines.append("  " + "-" * 60)
        lines.append(f"    Partidos con cuota real: {res.odds_matched} emparejados / "
                     f"{res.odds_unmatched} sin emparejar")
        if res.real_bets > 0:
            lines.append(f"    Apuestas de valor:       {res.real_bets}")
            lines.append(f"    % acierto value bets:    {res.real_hit_rate*100:.2f}% "
                         f"(G:{res.real_wins} P:{res.real_losses})")
            lines.append(f"    Total apostado:          {res.real_staked:.2f}")
            lines.append(f"    P&L:                     {res.real_pnl:+.2f}")
            lines.append(f"    ROI REAL (yield):        {res.real_roi*100:+.2f}%")
            lines.append(f"    Bankroll:                {res.real_bankroll0:.2f} -> {res.real_bankroll:.2f}")
        else:
            lines.append("    (Sin apuestas de valor sobre los partidos emparejados.)")
        lines.append("")

    lines.append("  ROI proxy vs mercado baseline-Elo (sintético, NO cuotas reales):")
    lines.append(f"    Apuestas de valor: {res.apuestas}")
    lines.append(f"    Total apostado:    {res.total_apostado:.0f} u")
    lines.append(f"    P&L:               {res.pnl:+.2f} u")
    lines.append(f"    ROI:               {res.roi*100:+.2f}%")
    lines.append("")
    lines.append("  CALIBRACIÓN (prob. 1X2 predicha vs frecuencia real)")
    lines.append(f"  {'rango':<14} {'n':>6} {'pred.media':>11} {'real':>9}")
    for b in res.calib:
        if b.n == 0:
            continue
        lines.append(
            f"  [{b.low:.2f}-{b.high:.2f})   {b.n:>6} {b.pred_mean*100:>10.1f}% {b.hit_rate*100:>8.1f}%"
        )
    lines.append("=" * 64)
    if res.has_real_odds and res.real_bets > 0:
        lines.append("  El ROI REAL usa cuotas históricas reales (yield verificable).")
    lines.append("  El ROI proxy es sintético (baseline-Elo). Las métricas")
    lines.append("  probabilísticas (Brier, log-loss, calibración) son objetivas.")
    lines.append("  Herramienta educativa, no asesoría financiera.")
    return "\n".join(lines)


def render_roi_markdown(res: BacktestResult, *, titulo: str = "Backtest — ROI real") -> str:
    """Reporte markdown enfocado en el ROI real (para reports/backtest_roi.md)."""
    md: list[str] = [f"# {titulo}\n"]
    md.append("## Métricas predictivas (objetivas)\n")
    md.append("| Métrica | Valor |")
    md.append("|---|---|")
    md.append(f"| Torneos | {res.n_torneos} |")
    md.append(f"| Partidos | {res.n_partidos} |")
    md.append(f"| Accuracy 1X2 | {res.accuracy*100:.2f}% |")
    md.append(f"| Brier (multiclase) | {res.brier:.4f} |")
    md.append(f"| Log-loss | {res.log_loss:.4f} |")

    md.append("\n## ROI REAL — contra cuotas históricas reales\n")
    if not res.has_real_odds:
        md.append("> ⚠️ No se calculó: no había `data/odds.csv` ni `ODDS_API_KEY`. "
                  "El backtest corrió solo con métricas predictivas.")
        return "\n".join(md)

    md.append(f"- Partidos emparejados con cuota real: **{res.odds_matched}** "
              f"(sin emparejar: {res.odds_unmatched})")
    if res.real_bets > 0:
        md.append("")
        md.append("| Métrica | Valor |")
        md.append("|---|---|")
        md.append(f"| Apuestas de valor | {res.real_bets} |")
        md.append(f"| % acierto value bets | {res.real_hit_rate*100:.2f}% (G:{res.real_wins} P:{res.real_losses}) |")
        md.append(f"| Total apostado | {res.real_staked:.2f} |")
        md.append(f"| P&L | {res.real_pnl:+.2f} |")
        md.append(f"| **ROI real (yield)** | **{res.real_roi*100:+.2f}%** |")
        md.append(f"| Bankroll | {res.real_bankroll0:.2f} → {res.real_bankroll:.2f} |")
        if res.bankroll_curve:
            puntos = res.bankroll_curve
            muestra = puntos if len(puntos) <= 40 else puntos[:: max(1, len(puntos) // 40)]
            md.append("\n### Curva de bankroll (muestreada)\n")
            md.append("```")
            md.append(" -> ".join(f"{v:.0f}" for v in muestra))
            md.append("```")
    else:
        md.append("\n> Sin apuestas de valor sobre los partidos emparejados "
                  "(el modelo no superó el umbral frente a esas cuotas).")

    md.append("\n## ROI proxy (sintético, baseline-Elo) — referencia\n")
    md.append(f"- Apuestas: {res.apuestas} · ROI proxy: {res.roi*100:+.2f}% "
              "(NO usa cuotas reales; es un baseline sintético).")
    md.append("\n> Este ROI **real** sí se calcula contra cuotas históricas reales, "
              "a diferencia del proxy. Herramienta educativa, no asesoría financiera. "
              "Resultados pasados no garantizan resultados futuros.")
    return "\n".join(md)
