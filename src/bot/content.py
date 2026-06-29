"""Construcción del contenido del bot a partir del motor existente.

Funciones puras / de orquestación (sin dependencia de telegram), para poder
testearlas y reutilizarlas desde el scheduler:

  * build_today        — genera recomendaciones del día (no persiste).
  * publish_today      — genera + persiste en `recommendations` (track record).
  * format_today_text  — texto premium para los suscriptores.
  * record_text        — track record público (resultados publicados).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.bot import texts
from src.service import BettingService, RecommendedBet
from src.subscriptions.models import PublishedRecommendation
from src.subscriptions.store import SubscriptionStore

_SEL_ES = {
    "HOME": "1 (local)", "DRAW": "X (empate)", "AWAY": "2 (visitante)",
    "OVER": "Más de 2.5", "UNDER": "Menos de 2.5",
}


def _leg_text(vb) -> str:
    sel = _SEL_ES.get(vb.selection, vb.selection)
    return f"{vb.home_team} vs {vb.away_team} → {vb.market}: {sel} @ {vb.odds:.2f}"


def describe_rec(rec: RecommendedBet) -> str:
    """Descripción legible de una recomendación (single o combinada)."""
    if len(rec.legs) == 1:
        return _leg_text(rec.legs[0])
    return " + ".join(_leg_text(vb) for vb in rec.legs)


def build_today(
    service: BettingService,
    *,
    date_iso: Optional[str] = None,
    round_name: Optional[str] = None,
    max_recs: int = 5,
    top_parlays: int = 5,
) -> list[RecommendedBet]:
    """Genera las recomendaciones del día (singles de valor + combinadas)."""
    analyses = service.analyze_round(round_name, date_iso)
    recs = service.build_recommendations(
        analyses, include_singles=True, top_parlays=top_parlays
    )
    return recs[:max_recs]


def format_today_text(
    recs: list[RecommendedBet],
    *,
    jornada: str,
) -> str:
    """Texto premium con las recomendaciones del día."""
    if not recs:
        return (
            f"📅 *{jornada}*\nHoy el modelo no encuentra selecciones de valor con "
            "stake > 0. A veces la mejor apuesta es no apostar.\n\n"
            + texts.DISCLAIMER_CORTO
        )
    lines = [f"📅 *Recomendaciones — {jornada}*\n"]
    for i, r in enumerate(recs, 1):
        cap = " (stake topado)" if r.capped else ""
        etiqueta = "COMBINADA" if r.tipo == "parlay" else "SIMPLE"
        lines.append(
            f"*{i}. {etiqueta}* — edge {r.edge*100:.1f}% · cuota {r.combined_odds:.2f} · "
            f"prob {r.combined_prob*100:.1f}% · stake {r.stake:.2f}{cap}\n"
            f"   {describe_rec(r)}"
        )
    lines.append("\n" + texts.DISCLAIMER_COMPLETO)
    return "\n".join(lines)


def publish_today(
    service: BettingService,
    sub_store: SubscriptionStore,
    *,
    date_iso: Optional[str] = None,
    round_name: Optional[str] = None,
    max_recs: int = 5,
    place_paper: bool = True,
) -> tuple[str, list[int]]:
    """Genera, persiste (y opcionalmente registra como paper) las recomendaciones.

    Devuelve (texto premium, ids de recommendations creadas). Usado por el
    scheduler antes de cada jornada.
    """
    jornada = round_name or date_iso or datetime.now(timezone.utc).date().isoformat()
    recs = build_today(
        service, date_iso=date_iso, round_name=round_name, max_recs=max_recs
    )
    rec_ids: list[int] = []
    now = datetime.now(timezone.utc).isoformat()
    for r in recs:
        bet_id = None
        if place_paper:
            try:
                bet_id = service.place_paper_bet(r)
            except Exception:  # noqa: BLE001 — no romper la publicación por un stake inválido
                bet_id = None
        pub = PublishedRecommendation(
            fecha=now, jornada=jornada, tipo=r.tipo, descripcion=describe_rec(r),
            cuota=r.combined_odds, prob=r.combined_prob, edge=r.edge, stake=r.stake,
            estado="pending" if bet_id else "published", bet_id=bet_id,
        )
        rec_ids.append(sub_store.add_recommendation(pub))
    return format_today_text(recs, jornada=jornada), rec_ids


def record_text(sub_store: SubscriptionStore, service: BettingService, *, limit: int = 15) -> str:
    """Track record público: resumen agregado + últimas publicaciones."""
    recs = sub_store.recommendations(limit=limit)
    summary = service.summary_dict()

    lines = ["📊 *Track record público*\n"]
    lines.append(
        f"Apuestas liquidadas: {summary['liquidadas']} "
        f"(G:{summary['ganadas']} P:{summary['perdidas']} Void:{summary['void']})"
    )
    lines.append(f"ROI: {summary['roi_pct']:.2f}% · % acierto: {summary['acierto_pct']:.2f}%")
    lines.append(
        f"Bankroll: {summary['bankroll_actual']:.2f} (inicial {summary['bankroll_inicial']:.2f})"
    )

    if recs:
        lines.append("\n*Últimas publicaciones:*")
        for r in recs:
            estado = r.estado.upper()
            if r.resultado:
                estado = r.resultado.upper()
            lines.append(
                f"• [{estado}] {r.tipo} · cuota {r.cuota:.2f} · edge {r.edge*100:.1f}% — "
                f"{r.descripcion[:80]}"
            )
    else:
        lines.append("\n(Aún no hay publicaciones registradas.)")

    lines.append("\n" + texts.DISCLAIMER_CORTO)
    return "\n".join(lines)
