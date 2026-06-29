"""Reportes: rendimiento (ROI, acierto, P&L), progreso al objetivo y
una tabla de calibración (probabilidad predicha vs frecuencia real de acierto).

Exportable a consola, markdown y CSV.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import CONFIG, Config
from src.storage.db import Bet, BettingStore


@dataclass
class PerformanceSummary:
    """Métricas agregadas de las apuestas liquidadas."""

    total_apuestas: int
    liquidadas: int
    ganadas: int
    perdidas: int
    void: int
    total_apostado: float
    retorno_total: float          # suma de payouts
    pnl_neto: float               # retorno − apostado (solo liquidadas)
    roi_pct: float                # pnl / apostado · 100
    acierto_pct: float            # ganadas / (ganadas+perdidas) · 100
    bankroll_inicial: float
    bankroll_actual: float
    objetivo_ganancia: float
    progreso_objetivo_pct: float

    def __post_init__(self) -> None:
        pass


@dataclass
class CalibrationBin:
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


def build_summary(store: BettingStore, *, config: Config = CONFIG) -> PerformanceSummary:
    """Calcula el resumen de rendimiento a partir de la base de datos."""
    all_bets = store.all_bets()
    settled = [b for b in all_bets if b.estado != "pending"]
    # Para ROI consideramos apuestas resueltas con dinero en juego (no void puro).
    contables = [b for b in settled if b.estado in {"won", "lost"}]

    ganadas = sum(1 for b in settled if b.estado == "won")
    perdidas = sum(1 for b in settled if b.estado == "lost")
    void = sum(1 for b in settled if b.estado == "void")

    total_apostado = sum(b.stake for b in contables)
    retorno_total = sum(b.payout for b in contables)
    pnl_neto = retorno_total - total_apostado
    roi_pct = (pnl_neto / total_apostado * 100.0) if total_apostado > 0 else 0.0
    decididas = ganadas + perdidas
    acierto_pct = (ganadas / decididas * 100.0) if decididas > 0 else 0.0

    bankroll_actual = store.current_bankroll()
    bankroll_inicial = config.bankroll_inicial
    ganancia_actual = bankroll_actual - bankroll_inicial
    progreso = (ganancia_actual / config.objetivo_ganancia * 100.0) if config.objetivo_ganancia else 0.0

    return PerformanceSummary(
        total_apuestas=len(all_bets),
        liquidadas=len(settled),
        ganadas=ganadas,
        perdidas=perdidas,
        void=void,
        total_apostado=round(total_apostado, 2),
        retorno_total=round(retorno_total, 2),
        pnl_neto=round(pnl_neto, 2),
        roi_pct=round(roi_pct, 2),
        acierto_pct=round(acierto_pct, 2),
        bankroll_inicial=round(bankroll_inicial, 2),
        bankroll_actual=round(bankroll_actual, 2),
        objetivo_ganancia=round(config.objetivo_ganancia, 2),
        progreso_objetivo_pct=round(progreso, 2),
    )


def calibration_table(store: BettingStore, *, n_bins: int = 5) -> list[CalibrationBin]:
    """Tabla de calibración a nivel de LEG resuelta.

    Compara la probabilidad del modelo de cada leg con si acertó o no.
    Los legs void se ignoran. Bins uniformes en [0, 1].
    """
    bins = [CalibrationBin(low=i / n_bins, high=(i + 1) / n_bins) for i in range(n_bins)]
    for bet in store.settled_bets():
        for leg in bet.legs:
            if leg.result not in {"won", "lost"}:
                continue
            p = leg.model_prob
            idx = min(int(p * n_bins), n_bins - 1)
            b = bins[idx]
            b.n += 1
            b.pred_sum += p
            b.hits += 1 if leg.result == "won" else 0
    return bins


# ─────────────────────────── renderizado ───────────────────────────

def _fmt_money(x: float) -> str:
    return f"{x:,.2f}"


def render_console(store: BettingStore, *, config: Config = CONFIG) -> str:
    """Reporte legible para consola (texto plano)."""
    s = build_summary(store, config=config)
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append("  REPORTE DE PAPER TRADING — MUNDIAL 2026")
    lines.append("=" * 64)

    settled = [b for b in store.all_bets() if b.estado != "pending"]
    if settled:
        lines.append("\nApuestas liquidadas:")
        lines.append(f"  {'#':>3} {'tipo':<7} {'estado':<6} {'stake':>9} {'cuota':>7} {'payout':>10} {'P&L':>10}")
        for b in settled:
            pnl = b.payout - b.stake if b.estado in {"won", "lost"} else 0.0
            lines.append(
                f"  {b.id:>3} {b.tipo:<7} {b.estado:<6} {_fmt_money(b.stake):>9} "
                f"{b.cuota_combinada:>7.2f} {_fmt_money(b.payout):>10} {_fmt_money(pnl):>10}"
            )
    else:
        lines.append("\n(No hay apuestas liquidadas todavía.)")

    pend = store.pending_bets()
    if pend:
        lines.append(f"\nApuestas pendientes: {len(pend)}")

    lines.append("\n" + "-" * 64)
    lines.append("RESUMEN")
    lines.append("-" * 64)
    lines.append(f"  Total apuestas:        {s.total_apuestas}")
    lines.append(f"  Liquidadas:            {s.liquidadas}  (G:{s.ganadas} P:{s.perdidas} Void:{s.void})")
    lines.append(f"  Total apostado:        {_fmt_money(s.total_apostado)}")
    lines.append(f"  Retorno total:         {_fmt_money(s.retorno_total)}")
    lines.append(f"  P&L neto:              {_fmt_money(s.pnl_neto)}")
    lines.append(f"  ROI:                   {s.roi_pct:.2f}%")
    lines.append(f"  % de acierto:          {s.acierto_pct:.2f}%")
    lines.append(f"  Bankroll inicial:      {_fmt_money(s.bankroll_inicial)}")
    lines.append(f"  Bankroll actual:       {_fmt_money(s.bankroll_actual)}")
    lines.append(f"  Objetivo de ganancia:  {_fmt_money(s.objetivo_ganancia)}  (informativo)")
    lines.append(f"  Progreso al objetivo:  {s.progreso_objetivo_pct:.2f}%")

    # Calibración
    bins = calibration_table(store)
    if any(b.n for b in bins):
        lines.append("\n" + "-" * 64)
        lines.append("CALIBRACIÓN (prob. predicha vs acierto real, por leg)")
        lines.append("-" * 64)
        lines.append(f"  {'rango':<14} {'n':>4} {'pred.media':>11} {'acierto':>9}")
        for b in bins:
            if b.n == 0:
                continue
            lines.append(
                f"  [{b.low:.2f}-{b.high:.2f})   {b.n:>4} {b.pred_mean*100:>10.1f}% {b.hit_rate*100:>8.1f}%"
            )

    lines.append("=" * 64)
    return "\n".join(lines)


def render_markdown(store: BettingStore, *, config: Config = CONFIG) -> str:
    """Reporte en markdown exportable."""
    s = build_summary(store, config=config)
    md: list[str] = []
    md.append("# Reporte de paper trading — Mundial 2026\n")
    md.append("## Resumen\n")
    md.append("| Métrica | Valor |")
    md.append("|---|---|")
    md.append(f"| Total apuestas | {s.total_apuestas} |")
    md.append(f"| Liquidadas | {s.liquidadas} (G:{s.ganadas} P:{s.perdidas} Void:{s.void}) |")
    md.append(f"| Total apostado | {_fmt_money(s.total_apostado)} |")
    md.append(f"| Retorno total | {_fmt_money(s.retorno_total)} |")
    md.append(f"| P&L neto | {_fmt_money(s.pnl_neto)} |")
    md.append(f"| ROI | {s.roi_pct:.2f}% |")
    md.append(f"| % de acierto | {s.acierto_pct:.2f}% |")
    md.append(f"| Bankroll inicial | {_fmt_money(s.bankroll_inicial)} |")
    md.append(f"| Bankroll actual | {_fmt_money(s.bankroll_actual)} |")
    md.append(f"| Objetivo (informativo) | {_fmt_money(s.objetivo_ganancia)} |")
    md.append(f"| Progreso al objetivo | {s.progreso_objetivo_pct:.2f}% |")

    settled = [b for b in store.all_bets() if b.estado != "pending"]
    if settled:
        md.append("\n## Apuestas liquidadas\n")
        md.append("| # | Tipo | Estado | Stake | Cuota | Payout | P&L |")
        md.append("|---|---|---|---|---|---|---|")
        for b in settled:
            pnl = b.payout - b.stake if b.estado in {"won", "lost"} else 0.0
            md.append(
                f"| {b.id} | {b.tipo} | {b.estado} | {_fmt_money(b.stake)} | "
                f"{b.cuota_combinada:.2f} | {_fmt_money(b.payout)} | {_fmt_money(pnl)} |"
            )

    bins = calibration_table(store)
    if any(b.n for b in bins):
        md.append("\n## Calibración (por leg)\n")
        md.append("| Rango prob. | n | Pred. media | Acierto real |")
        md.append("|---|---|---|---|")
        for b in bins:
            if b.n == 0:
                continue
            md.append(
                f"| [{b.low:.2f}-{b.high:.2f}) | {b.n} | {b.pred_mean*100:.1f}% | {b.hit_rate*100:.1f}% |"
            )

    md.append("\n> ⚠️ Herramienta educativa de análisis, no asesoría financiera. "
              "Las combinadas acumulan el margen de la casa y por defecto tienen valor "
              "esperado negativo. Resultados pasados no garantizan resultados futuros.")
    return "\n".join(md)


def write_csv(store: BettingStore, path: Path) -> Path:
    """Exporta las apuestas (con sus legs aplanadas) a CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "bet_id", "tipo", "estado", "stake", "cuota_combinada", "prob", "edge",
            "payout", "fecha", "fecha_liquidacion",
            "leg_fixture_id", "leg_mercado", "leg_seleccion", "leg_cuota",
            "leg_prob_modelo", "leg_resultado",
        ])
        for b in store.all_bets():
            if not b.legs:
                writer.writerow([b.id, b.tipo, b.estado, b.stake, b.cuota_combinada,
                                 b.prob, b.edge, b.payout, b.fecha, b.fecha_liquidacion,
                                 "", "", "", "", "", ""])
            for leg in b.legs:
                writer.writerow([
                    b.id, b.tipo, b.estado, b.stake, b.cuota_combinada, b.prob, b.edge,
                    b.payout, b.fecha, b.fecha_liquidacion,
                    leg.fixture_id, leg.market, leg.selection, leg.odds,
                    leg.model_prob, leg.result,
                ])
    return path
