"""Cobro como interfaz desacoplada (proveedor de pago conectable).

La v1 trae `ManualPaymentProvider`: un stub donde la activación la confirma un
admin (o se simula), sin mover dinero real. Los puntos de integración para
Telegram Payments / Stripe están señalados pero NO implementados.

⚠️ AVISO IMPORTANTE: muchos proveedores de pago (Stripe, Telegram Payments y los
PSP que hay detrás) restringen o prohíben los servicios relacionados con
apuestas. Verifica los términos y la licencia aplicable ANTES de integrar un
cobro real. Este producto es educativo / paper y no coloca apuestas reales.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CheckoutInfo:
    """Información que el bot muestra al usuario para completar el pago."""

    provider: str
    instructions: str
    # URL de pago (Stripe Checkout / link), o None para flujo manual.
    url: Optional[str] = None
    # Referencia opaca para conciliar el pago luego (p.ej. id de sesión).
    reference: Optional[str] = None


class PaymentProvider(ABC):
    """Interfaz que debe cumplir cualquier proveedor de cobro.

    El `SubscriptionService` solo conoce esta interfaz; cambiar de proveedor no
    toca la lógica de entitlement.
    """

    name: str = "abstract"

    @abstractmethod
    def create_checkout(self, telegram_id: int, tier: str) -> CheckoutInfo:
        """Inicia un cobro y devuelve instrucciones para el usuario."""

    @abstractmethod
    def confirm_payment(self, telegram_id: int, reference: Optional[str] = None) -> bool:
        """Confirma si el pago se completó (True = activar suscripción).

        En proveedores reales esto se resolvería vía webhook/verificación de
        firma. En el stub manual lo decide el admin.
        """


class ManualPaymentProvider(PaymentProvider):
    """Proveedor manual/stub para la v1 (sin dinero real).

    Flujo: el usuario pide `/subscribe`; el bot le da instrucciones de pago
    manual (p.ej. contactar al admin). El admin confirma el pago llamando a
    `confirm_payment` (vía un comando admin o marcando una referencia como
    pagada). Por defecto, `auto_confirm=False` (requiere confirmación explícita).

    Para demos/tests se puede crear con `auto_confirm=True`, de modo que cualquier
    referencia se considere pagada de inmediato.
    """

    name = "manual"

    def __init__(self, *, auto_confirm: bool = False, contacto: str = "@admin") -> None:
        self.auto_confirm = auto_confirm
        self.contacto = contacto
        self._confirmadas: set[str] = set()

    def _ref(self, telegram_id: int) -> str:
        return f"manual:{telegram_id}"

    def create_checkout(self, telegram_id: int, tier: str) -> CheckoutInfo:
        ref = self._ref(telegram_id)
        instr = (
            f"Suscripción tier '{tier}' (modo manual).\n"
            f"Para activarla, contacta con {self.contacto} y referencia tu ID "
            f"de Telegram: {telegram_id}.\n"
            "El acceso se habilita tras confirmar el pago (sin apuestas reales)."
        )
        return CheckoutInfo(provider=self.name, instructions=instr, url=None, reference=ref)

    def mark_paid(self, telegram_id: int) -> None:
        """El admin marca como pagada la referencia de este usuario."""
        self._confirmadas.add(self._ref(telegram_id))

    def confirm_payment(self, telegram_id: int, reference: Optional[str] = None) -> bool:
        if self.auto_confirm:
            return True
        ref = reference or self._ref(telegram_id)
        return ref in self._confirmadas


# ─────────────── puntos de integración (NO implementados) ───────────────

class StripePaymentProvider(PaymentProvider):  # pragma: no cover - placeholder
    """Punto de integración para Stripe Checkout.

    Implementación pendiente: crear una Checkout Session, devolver su URL en
    `create_checkout`, y confirmar vía webhook firmado en `confirm_payment`.
    Verifica antes las políticas de Stripe sobre apuestas/gambling.
    """

    name = "stripe"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def create_checkout(self, telegram_id: int, tier: str) -> CheckoutInfo:
        raise NotImplementedError(
            "Integración Stripe pendiente. Verifica políticas de gambling antes."
        )

    def confirm_payment(self, telegram_id: int, reference: Optional[str] = None) -> bool:
        raise NotImplementedError("Integración Stripe pendiente.")


class TelegramPaymentsProvider(PaymentProvider):  # pragma: no cover - placeholder
    """Punto de integración para Telegram Payments (sendInvoice / pre-checkout).

    Implementación pendiente. Telegram Payments también restringe gambling según
    el proveedor conectado; verifica antes de habilitarlo.
    """

    name = "telegram_payments"

    def __init__(self, provider_token: str) -> None:
        self.provider_token = provider_token

    def create_checkout(self, telegram_id: int, tier: str) -> CheckoutInfo:
        raise NotImplementedError("Integración Telegram Payments pendiente.")

    def confirm_payment(self, telegram_id: int, reference: Optional[str] = None) -> bool:
        raise NotImplementedError("Integración Telegram Payments pendiente.")
