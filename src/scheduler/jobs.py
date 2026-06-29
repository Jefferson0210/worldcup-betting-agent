"""Jobs del scheduler y su ensamblado con APScheduler.

`broadcast` es un callable `(text: str, chat_ids: list[int]) -> None` inyectado
por el caller (p.ej. un wrapper que usa el bot de Telegram, o un stub que
imprime). Así los jobs son testeables sin red ni dependencia de APScheduler.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from config import CONFIG, Config
from src.bot import content
from src.service import BettingService
from src.subscriptions.service import SubscriptionService

Broadcast = Callable[[str, list[int]], None]


def _noop_broadcast(text: str, chat_ids: list[int]) -> None:  # pragma: no cover
    print(f"[broadcast→{len(chat_ids)} subs]\n{text}\n")


# ─────────────────────────────── jobs ───────────────────────────────

def publish_recommendations_job(
    service: BettingService,
    subs: SubscriptionService,
    broadcast: Broadcast = _noop_broadcast,
    *,
    date_iso: Optional[str] = None,
    round_name: Optional[str] = None,
    config: Config = CONFIG,
    place_paper: bool = True,
) -> tuple[str, list[int]]:
    """Genera y publica las recomendaciones del día a los suscriptores activos.

    Persiste las publicaciones (track record) y, por defecto, las registra como
    paper bets. Devuelve (texto, ids de suscriptores notificados).
    """
    if date_iso is None and round_name is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()
    text, _rec_ids = content.publish_today(
        service, subs.store, date_iso=date_iso, round_name=round_name,
        max_recs=config.max_recs_publicadas, place_paper=place_paper,
    )
    subscriber_ids = subs.active_subscriber_ids()
    if subscriber_ids:
        broadcast(text, subscriber_ids)
    return text, subscriber_ids


def settle_and_update_job(
    service: BettingService,
    subs: SubscriptionService,
    broadcast: Broadcast = _noop_broadcast,
    *,
    config: Config = CONFIG,
    notify: bool = True,
) -> list[tuple[int, str, float]]:
    """Liquida apuestas pendientes y actualiza el estado de las publicaciones.

    Notifica a los suscriptores un resumen si hubo liquidaciones.
    """
    settled = service.settle()
    if not settled:
        return []

    # Propaga el resultado a las recomendaciones publicadas enlazadas por bet_id.
    for bet_id, estado, _payout in settled:
        pub = subs.store.recommendation_by_bet(bet_id)
        if pub is not None and pub.id is not None:
            subs.store.update_recommendation_result(pub.id, estado, estado)

    if notify:
        lines = ["📈 *Resultados liquidados*"]
        for bet_id, estado, payout in settled:
            lines.append(f"• Apuesta #{bet_id}: {estado.upper()} (payout {payout:.2f})")
        lines.append("\n" + content.texts.DISCLAIMER_CORTO)
        subscriber_ids = subs.active_subscriber_ids()
        if subscriber_ids:
            broadcast("\n".join(lines), subscriber_ids)
    return settled


# ─────────────────────── ensamblado APScheduler ───────────────────────

def build_scheduler(
    service: BettingService,
    subs: SubscriptionService,
    broadcast: Broadcast = _noop_broadcast,
    *,
    config: Config = CONFIG,
):
    """Construye un BackgroundScheduler con ambos jobs.

    - Publicación: cron diario a config.publicar_hora:publicar_minuto.
    - Liquidación: cada config.liquidar_cada_min minutos.

    APScheduler se importa aquí (lazy) para no exigir la dependencia en los
    tests de la lógica de jobs.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(
        lambda: publish_recommendations_job(service, subs, broadcast, config=config),
        trigger=CronTrigger(hour=config.publicar_hora, minute=config.publicar_minuto),
        id="publicar_recomendaciones",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: settle_and_update_job(service, subs, broadcast, config=config),
        trigger=IntervalTrigger(minutes=config.liquidar_cada_min),
        id="liquidar_y_actualizar",
        replace_existing=True,
    )
    return scheduler
