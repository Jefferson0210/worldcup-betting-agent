"""Modo paper en vivo sobre el Mundial 2026 vía OddsPapi.

Toma los partidos próximos + cuotas de OddsPapi (el plan gratuito de
API-Football no cubre 2026), corre el motor existente (modelo → valor →
combinadas → staking) y registra apuestas en modo PAPER, liquidándolas con los
resultados de `results.csv`. Reutiliza `OddsPapiProvider`, `BettingService`,
`src/settlement` y `src/storage`; no reescribe nada.

⚠️ Solo paper (dinero ficticio). Prueba privada del usuario; sin cobro ni
contenido público.
"""
from src.paper.runner import PaperRunner  # noqa: F401
from src.paper.store import PaperFixtureStore  # noqa: F401
