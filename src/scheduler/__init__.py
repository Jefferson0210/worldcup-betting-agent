"""Jobs programados (APScheduler): publicar recomendaciones antes de cada
jornada y liquidar + actualizar el track record tras los partidos.

La lógica de los jobs (`jobs`) es independiente de APScheduler y del envío real
de mensajes (se inyecta un `broadcast`), para poder testearla sin red ni hilos.
"""
from src.scheduler.jobs import (  # noqa: F401
    build_scheduler,
    publish_recommendations_job,
    settle_and_update_job,
)
