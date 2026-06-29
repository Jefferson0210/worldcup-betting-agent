"""Staking con Kelly fraccionado y tope de stake.

Fórmula de Kelly para una apuesta con cuota decimal d y prob p:
    kelly = (p·d − 1) / (d − 1) = edge / (d − 1)

Stake recomendado:
    stake = kelly · kelly_fraccion · bankroll_actual
    acotado a [0, stake_max_pct · bankroll_actual]

Reglas no negociables:
  * Kelly fraccionado SIEMPRE (nunca Kelly pleno).
  * Tope duro por apuesta (stake_max_pct).
  * NUNCA aumentar el stake para "alcanzar el objetivo". El objetivo es solo
    informativo. Si Kelly es ≤ 0 (sin valor), el stake es 0.

Una combinada se trata como una apuesta única con su cuota y prob combinadas.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import CONFIG, Config


@dataclass(frozen=True)
class StakeDecision:
    """Resultado del cálculo de staking."""

    kelly_full: float       # fracción de Kelly plena (puede ser >0 o ≤0)
    kelly_applied: float    # tras aplicar kelly_fraccion
    stake: float            # cantidad final (ya topada y no negativa)
    capped: bool            # True si el tope de stake recortó la cantidad


def kelly_fraction(prob: float, odds: float) -> float:
    """Fracción de Kelly plena. Devuelve ≤ 0 si no hay valor.

    No lanza para prob/odds fuera de rango razonable salvo cuota inválida.
    """
    if odds <= 1.0:
        raise ValueError(f"Cuota decimal inválida: {odds!r} (debe ser > 1.0)")
    return (prob * odds - 1.0) / (odds - 1.0)


def stake_for_bet(
    prob: float,
    odds: float,
    bankroll: float,
    *,
    config: Config = CONFIG,
) -> StakeDecision:
    """Calcula el stake disciplinado (Kelly fraccionado con tope).

    Si Kelly ≤ 0 o bankroll ≤ 0, el stake es 0 (no se apuesta sin valor).
    """
    if bankroll <= 0:
        return StakeDecision(0.0, 0.0, 0.0, capped=False)

    k_full = kelly_fraction(prob, odds)
    if k_full <= 0:
        return StakeDecision(k_full, 0.0, 0.0, capped=False)

    k_applied = k_full * config.kelly_fraccion
    raw_stake = k_applied * bankroll
    cap = config.stake_max_pct * bankroll

    capped = raw_stake > cap
    stake = min(raw_stake, cap)
    # Redondeo a 2 decimales (unidad monetaria) sin superar el tope.
    stake = round(max(0.0, stake), 2)
    return StakeDecision(kelly_full=k_full, kelly_applied=k_applied, stake=stake, capped=capped)
