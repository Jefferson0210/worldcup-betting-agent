"""Capa de producto: entitlement de suscripción, usuarios y publicaciones.

Desacopla la lógica de "quién puede ver el contenido premium" del motor de
apuestas (`src/service.py`). El cobro es una interfaz conectable
(`payments.PaymentProvider`); la v1 trae un proveedor manual/stub.
"""
from src.subscriptions.models import (  # noqa: F401
    PublishedRecommendation,
    User,
    SubStatus,
)
from src.subscriptions.payments import (  # noqa: F401
    CheckoutInfo,
    ManualPaymentProvider,
    PaymentProvider,
)
from src.subscriptions.service import SubscriptionService  # noqa: F401
from src.subscriptions.store import SubscriptionStore  # noqa: F401
