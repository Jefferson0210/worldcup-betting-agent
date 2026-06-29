"""Tests del entitlement de suscripción y la persistencia (sin red)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.subscriptions.models import PublishedRecommendation, SubStatus, User
from src.subscriptions.payments import ManualPaymentProvider
from src.subscriptions.service import SubscriptionService
from src.subscriptions.store import SubscriptionStore


@pytest.fixture
def subs(tmp_config):
    store = SubscriptionStore(tmp_config)
    provider = ManualPaymentProvider(auto_confirm=True)  # demo: pago siempre confirmado
    svc = SubscriptionService(tmp_config, store=store, payment_provider=provider)
    yield svc
    svc.close()


UID = 999_111


# ───────────────────────── verificación de edad ─────────────────────────

def test_acceso_bloqueado_sin_edad(subs):
    subs.register(UID)
    assert subs.is_entitled(UID) is False
    # No se puede activar sin verificar la edad.
    with pytest.raises(PermissionError):
        subs.activate(UID)


def test_edad_y_activacion_dan_entitlement(subs):
    subs.verify_age(UID, confirmed_18=True)
    assert subs.is_age_verified(UID) is True
    user = subs.activate(UID)
    assert user.estado == SubStatus.ACTIVE
    assert user.tier == subs.config.suscripcion_tier_default
    assert subs.is_entitled(UID) is True


def test_edad_no_basta_sin_suscripcion(subs):
    subs.verify_age(UID, confirmed_18=True)
    # Verificó edad pero no se suscribe -> sin entitlement.
    assert subs.is_entitled(UID) is False


# ─────────────────────────── caducidad ───────────────────────────

def test_suscripcion_caduca(subs):
    subs.verify_age(UID, confirmed_18=True)
    subs.activate(UID, dias=30)
    futuro = datetime.now(timezone.utc) + timedelta(days=31)
    assert subs.is_entitled(UID, now=futuro) is False
    # status reconcilia el estado a 'expired'.
    user = subs.status(UID, now=futuro)
    assert user.estado == SubStatus.EXPIRED


def test_renovacion_extiende_desde_expiracion(subs):
    subs.verify_age(UID, confirmed_18=True)
    now = datetime.now(timezone.utc)
    u1 = subs.activate(UID, dias=30, now=now)
    exp1 = datetime.fromisoformat(u1.fecha_expiracion)
    # Renueva estando aún activa: se extiende desde la expiración previa.
    u2 = subs.activate(UID, dias=30, now=now)
    exp2 = datetime.fromisoformat(u2.fecha_expiracion)
    assert exp2 > exp1
    assert (exp2 - exp1).days == pytest.approx(30, abs=1)


def test_pago_no_confirmado_no_activa(tmp_config):
    store = SubscriptionStore(tmp_config)
    provider = ManualPaymentProvider(auto_confirm=False)  # requiere confirmación
    svc = SubscriptionService(tmp_config, store=store, payment_provider=provider)
    svc.verify_age(UID, confirmed_18=True)
    with pytest.raises(RuntimeError):
        svc.activate(UID)
    # Tras marcar el pago, sí activa.
    provider.mark_paid(UID)
    user = svc.activate(UID)
    assert user.is_subscription_active()
    svc.close()


# ─────────────────── listado de suscriptores activos ───────────────────

def test_active_subscriber_ids_filtra_por_entitlement(subs):
    # Usuario A: edad + suscripción -> cuenta.
    subs.verify_age(UID, confirmed_18=True)
    subs.activate(UID)
    # Usuario B: suscrito pero sin edad verificada -> NO cuenta.
    b = 222
    subs.store.upsert_user(User(
        telegram_id=b, estado=SubStatus.ACTIVE, edad_verificada=False,
        fecha_expiracion=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
    ))
    ids = subs.active_subscriber_ids()
    assert UID in ids
    assert b not in ids


# ─────────────────────── tabla recommendations ───────────────────────

def test_recommendations_persisten_y_se_actualizan(tmp_config):
    store = SubscriptionStore(tmp_config)
    rec = PublishedRecommendation(
        fecha=datetime.now(timezone.utc).isoformat(), jornada="2026-06-28",
        tipo="parlay", descripcion="A vs B + C vs D", cuota=4.2, prob=0.27,
        edge=0.13, stake=12.5, estado="pending", bet_id=77,
    )
    rec_id = store.add_recommendation(rec)
    assert rec_id > 0
    fetched = store.recommendation_by_bet(77)
    assert fetched is not None and fetched.tipo == "parlay"

    store.update_recommendation_result(rec_id, "won", "won")
    actualizado = store.recommendations(limit=1)[0]
    assert actualizado.estado == "won"
    assert actualizado.resultado == "won"
    store.close()
