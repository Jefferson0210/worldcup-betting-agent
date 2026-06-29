"""Servicio de suscripción: orquesta usuarios, entitlement y cobro.

Reglas de producto (no negociables):
  * Verificación de edad 18+ obligatoria antes de cualquier acceso.
  * El contenido premium (`/today`, combinadas) solo para suscriptores activos.
  * El cobro va por una interfaz desacoplada (`PaymentProvider`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from config import CONFIG, Config
from src.subscriptions.models import SubStatus, User
from src.subscriptions.payments import CheckoutInfo, ManualPaymentProvider, PaymentProvider
from src.subscriptions.store import SubscriptionStore


class SubscriptionService:
    """Fachada de la lógica de suscripción usada por el bot y el scheduler."""

    def __init__(
        self,
        config: Config = CONFIG,
        store: Optional[SubscriptionStore] = None,
        payment_provider: Optional[PaymentProvider] = None,
    ) -> None:
        self.config = config
        self.store = store or SubscriptionStore(config)
        # Proveedor de cobro por defecto: manual/stub (sin dinero real).
        self.payments = payment_provider or ManualPaymentProvider()

    # ───────────────────────── registro / edad ─────────────────────────

    def register(self, telegram_id: int) -> User:
        """Asegura que el usuario existe (estado inicial 'none')."""
        return self.store.get_or_create_user(telegram_id)

    def verify_age(self, telegram_id: int, *, confirmed_18: bool) -> User:
        """Registra la verificación 18+ del usuario.

        `confirmed_18` debe venir de una confirmación explícita del usuario.
        """
        user = self.store.get_or_create_user(telegram_id)
        user.edad_verificada = bool(confirmed_18)
        return self.store.upsert_user(user)

    def is_age_verified(self, telegram_id: int) -> bool:
        user = self.store.get_user(telegram_id)
        return bool(user and user.edad_verificada)

    # ─────────────────────────── entitlement ───────────────────────────

    def is_entitled(self, telegram_id: int, *, now: Optional[datetime] = None) -> bool:
        """¿Puede ver contenido premium? (18+ y suscripción activa y vigente)."""
        user = self.store.get_user(telegram_id)
        if user is None:
            return False
        # Reconcilia estado caducado de forma perezosa.
        self._expire_if_needed(user, now=now)
        return user.is_entitled(now=now)

    def status(self, telegram_id: int, *, now: Optional[datetime] = None) -> User:
        """Devuelve el usuario, marcando como 'expired' si ya caducó."""
        user = self.store.get_or_create_user(telegram_id)
        self._expire_if_needed(user, now=now)
        return user

    def _expire_if_needed(self, user: User, *, now: Optional[datetime] = None) -> None:
        if user.estado == SubStatus.ACTIVE and not user.is_subscription_active(now=now):
            user.estado = SubStatus.EXPIRED
            self.store.upsert_user(user)

    # ─────────────────────────── suscripción ───────────────────────────

    def start_checkout(self, telegram_id: int, *, tier: Optional[str] = None) -> CheckoutInfo:
        """Inicia el flujo de cobro. Requiere edad verificada."""
        if not self.is_age_verified(telegram_id):
            raise PermissionError("Verificación de edad 18+ requerida antes de suscribir.")
        tier = tier or self.config.suscripcion_tier_default
        self.register(telegram_id)
        return self.payments.create_checkout(telegram_id, tier)

    def activate(
        self,
        telegram_id: int,
        *,
        tier: Optional[str] = None,
        dias: Optional[int] = None,
        now: Optional[datetime] = None,
        skip_age_check: bool = False,
    ) -> User:
        """Activa/renueva la suscripción si el pago está confirmado.

        Lanza si no hay edad verificada (salvo `skip_age_check`, uso admin) o si
        el proveedor de pago no confirma el cobro.
        """
        user = self.store.get_or_create_user(telegram_id)
        if not skip_age_check and not user.edad_verificada:
            raise PermissionError("Verificación de edad 18+ requerida.")
        if not self.payments.confirm_payment(telegram_id):
            raise RuntimeError("Pago no confirmado por el proveedor.")

        now = now or datetime.now(timezone.utc)
        dias = dias if dias is not None else self.config.suscripcion_dias
        # Si ya está activa y vigente, renueva desde la expiración actual.
        base = now
        if user.is_subscription_active(now=now) and user.fecha_expiracion:
            try:
                base = max(now, datetime.fromisoformat(user.fecha_expiracion))
            except ValueError:
                base = now
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
        nueva_exp = base + timedelta(days=dias)

        user.estado = SubStatus.ACTIVE
        user.tier = tier or user.tier or self.config.suscripcion_tier_default
        user.fecha_expiracion = nueva_exp.isoformat()
        return self.store.upsert_user(user)

    def cancel(self, telegram_id: int) -> User:
        user = self.store.get_or_create_user(telegram_id)
        user.estado = SubStatus.CANCELLED
        return self.store.upsert_user(user)

    def active_subscriber_ids(self, *, now: Optional[datetime] = None) -> list[int]:
        return self.store.active_subscriber_ids(now=now)

    def close(self) -> None:
        self.store.close()
