"""Textos del bot: bienvenida, disclaimers y ayuda (es-ES).

Los disclaimers son OBLIGATORIOS y visibles. No se promete ganancias.
"""
from __future__ import annotations

# Disclaimer corto que acompaña a todo contenido sensible.
DISCLAIMER_CORTO = (
    "⚠️ Solo modo PAPER (dinero ficticio). Herramienta educativa de análisis, "
    "NO asesoría financiera. Apostar implica riesgo de pérdida."
)

# Disclaimer completo (se muestra en /start y bajo demanda).
DISCLAIMER_COMPLETO = (
    "📜 *Aviso legal y de riesgo*\n"
    "• Esto NO es asesoría financiera ni una recomendación de inversión.\n"
    "• Apostar implica *riesgo de pérdida*; nunca apuestes lo que no puedas perder.\n"
    "• Las *combinadas* acumulan el margen de la casa y, por defecto, tienen "
    "*valor esperado negativo*.\n"
    "• Los *resultados pasados no garantizan resultados futuros*.\n"
    "• El sistema es solo modo *paper* (dinero ficticio): no coloca apuestas "
    "reales ni automatiza casas de apuestas.\n"
    "• No prometemos ganancias.\n"
    "• Juego responsable: si el juego es un problema para ti, busca ayuda "
    "(p.ej. línea de atención al jugador de tu país). Solo +18."
)

WELCOME = (
    "👋 *Bienvenido al asistente de análisis del Mundial 2026*\n\n"
    "Te comparto análisis probabilístico (modelo Poisson sobre ratings "
    "internacionales) y selecciones de *valor* con staking disciplinado "
    "(Kelly fraccionado con tope). Todo es educativo y en modo paper.\n\n"
    "Antes de continuar necesito confirmar que eres *mayor de edad (+18)*."
)

AGE_PROMPT = (
    "🔞 *Verificación de edad*\n"
    "Este servicio es solo para mayores de 18 años. ¿Confirmas que tienes 18 "
    "años o más?"
)

AGE_CONFIRMED = (
    "✅ Edad verificada. Ya puedes usar el bot.\n\n" + DISCLAIMER_COMPLETO
)

AGE_DENIED = (
    "🚫 Lo siento, este servicio es solo para mayores de 18 años. No puedo "
    "darte acceso."
)

NEED_AGE = (
    "🔞 Primero debes verificar que eres mayor de edad. Usa /start y confirma +18."
)

NEED_SUBSCRIPTION = (
    "🔒 Este contenido es solo para *suscriptores activos*.\n"
    "Usa /subscribe para activar tu suscripción y /status para ver tu estado."
)

HELP = (
    "🧭 *Comandos disponibles*\n"
    "/start — bienvenida y verificación de edad (+18)\n"
    "/subscribe — activar o renovar la suscripción\n"
    "/status — estado de tu suscripción (tier y días restantes)\n"
    "/today — recomendaciones del día (solo suscriptores)\n"
    "/record — track record público (resultados publicados)\n"
    "/help — esta ayuda\n\n"
    + DISCLAIMER_CORTO
)
