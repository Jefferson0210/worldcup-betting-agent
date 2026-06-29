"""Modelos de la capa de suscripción."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


class SubStatus:
    """Estados posibles de una suscripción."""

    NONE = "none"          # registrado, nunca suscrito
    ACTIVE = "active"      # suscripción vigente
    EXPIRED = "expired"    # caducada
    CANCELLED = "cancelled"  # cancelada por el usuario/admin

    ALL = {NONE, ACTIVE, EXPIRED, CANCELLED}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class User:
    """Un usuario del bot y su estado de suscripción.

    `edad_verificada` refleja la verificación 18+ obligatoria antes de acceder a
    cualquier contenido. Sin ella, el gating bloquea incluso a suscriptores.
    """

    telegram_id: int
    estado: str = SubStatus.NONE
    tier: Optional[str] = None
    fecha_expiracion: Optional[str] = None   # ISO-8601 UTC
    edad_verificada: bool = False
    creado: Optional[str] = None
    actualizado: Optional[str] = None

    def is_subscription_active(self, *, now: Optional[datetime] = None) -> bool:
        """¿Suscripción vigente? (estado ACTIVE y no caducada)."""
        if self.estado != SubStatus.ACTIVE:
            return False
        exp = _parse_iso(self.fecha_expiracion)
        if exp is None:
            return False
        now = now or datetime.now(timezone.utc)
        return exp > now

    def is_entitled(self, *, now: Optional[datetime] = None) -> bool:
        """¿Tiene derecho al contenido premium? Requiere 18+ y suscripción activa."""
        return self.edad_verificada and self.is_subscription_active(now=now)

    def dias_restantes(self, *, now: Optional[datetime] = None) -> int:
        exp = _parse_iso(self.fecha_expiracion)
        if exp is None:
            return 0
        now = now or datetime.now(timezone.utc)
        delta = exp - now
        return max(0, delta.days)


@dataclass
class PublishedRecommendation:
    """Una combinada/single publicada a los suscriptores (track record público)."""

    fecha: str                       # ISO de publicación
    jornada: str                     # ronda o fecha de los partidos
    tipo: str                        # "single" | "parlay"
    descripcion: str                 # texto legible de las legs
    cuota: float
    prob: float
    edge: float
    stake: float
    estado: str = "published"        # published | pending | won | lost | void
    resultado: Optional[str] = None
    bet_id: Optional[int] = None     # enlace a la paper bet en `bets`, si se registró
    id: Optional[int] = None
