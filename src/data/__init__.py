"""Capa de datos: cliente API-Football, caché en disco y modelos normalizados."""

from src.data.models import (
    Fixture,
    MarketOdds,
    Selection,
    TeamStats,
)

__all__ = ["Fixture", "MarketOdds", "Selection", "TeamStats"]
