"""Tests de los jobs del scheduler y la publicación, con servicio mockeado."""
from __future__ import annotations

import pytest

from src.scheduler import jobs
from src.service import RecommendedBet
from src.subscriptions.payments import ManualPaymentProvider
from src.subscriptions.service import SubscriptionService
from src.subscriptions.store import SubscriptionStore
from src.value.value_engine import ValueBet


class FakeService:
    """Sustituto de BettingService que no toca red ni modelo."""

    def __init__(self, recs):
        self._recs = recs
        self._settle = []
        self.placed = []

    def analyze_round(self, round_name=None, date_iso=None):
        return []

    def build_recommendations(self, analyses, *, include_singles=True, top_parlays=5):
        return list(self._recs)

    def place_paper_bet(self, rec):
        bet_id = 1000 + len(self.placed)
        self.placed.append((bet_id, rec))
        return bet_id

    def settle(self):
        return self._settle

    def summary_dict(self):
        return {
            "liquidadas": 1, "ganadas": 1, "perdidas": 0, "void": 0,
            "roi_pct": 12.5, "acierto_pct": 100.0,
            "bankroll_actual": 1100.0, "bankroll_inicial": 1000.0,
        }


def _rec():
    vb = ValueBet(
        fixture_id=1, home_team="Brazil", away_team="Qatar", market="1X2",
        selection="HOME", odds=1.8, model_prob=0.62, edge=0.12,
    )
    return RecommendedBet(
        tipo="single", legs=[vb], combined_odds=1.8, combined_prob=0.62,
        edge=0.12, stake=15.0, capped=False,
    )


@pytest.fixture
def subs(tmp_config):
    store = SubscriptionStore(tmp_config)
    svc = SubscriptionService(tmp_config, store=store,
                              payment_provider=ManualPaymentProvider(auto_confirm=True))
    # Un suscriptor con derecho (18+ y activo).
    svc.verify_age(555, confirmed_18=True)
    svc.activate(555)
    yield svc
    svc.close()


def test_publish_job_persiste_registra_y_difunde(tmp_config, subs):
    service = FakeService([_rec()])
    enviados = []
    def broadcast(text, ids):
        enviados.append((text, ids))

    text, subscriber_ids = jobs.publish_recommendations_job(
        service, subs, broadcast, date_iso="2026-06-28", config=tmp_config,
    )
    # Se registró como paper bet y se persistió la recomendación.
    assert len(service.placed) == 1
    assert subs.store.recommendations(limit=10)
    # Se difundió al suscriptor con derecho.
    assert 555 in subscriber_ids
    assert enviados and 555 in enviados[0][1]
    # El texto premium incluye disclaimers.
    assert "asesoría" in text.lower() or "riesgo" in text.lower()


def test_settle_job_actualiza_publicaciones(tmp_config, subs):
    service = FakeService([_rec()])
    # Publica para crear la recomendación enlazada a un bet_id.
    jobs.publish_recommendations_job(service, subs, lambda *_: None,
                                     date_iso="2026-06-28", config=tmp_config)
    bet_id = service.placed[0][0]
    # Ahora el servicio liquida esa apuesta como ganada.
    service._settle = [(bet_id, "won", 27.0)]
    enviados = []
    settled = jobs.settle_and_update_job(
        service, subs, lambda t, ids: enviados.append((t, ids)), config=tmp_config,
    )
    assert settled == [(bet_id, "won", 27.0)]
    pub = subs.store.recommendation_by_bet(bet_id)
    assert pub is not None and pub.estado == "won" and pub.resultado == "won"
    assert enviados  # se notificó


def test_settle_job_sin_pendientes_no_notifica(tmp_config, subs):
    service = FakeService([])
    enviados = []
    settled = jobs.settle_and_update_job(
        service, subs, lambda t, ids: enviados.append((t, ids)), config=tmp_config,
    )
    assert settled == []
    assert enviados == []
