"""Caché en disco para respuestas de la API.

El plan gratuito de API-Football permite ~100 req/día, así que cacheamos
TODAS las respuestas en JSON y las reutilizamos mientras estén frescas.
La clave de caché se deriva del endpoint + parámetros.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


class DiskCache:
    """Caché simple basada en archivos JSON con TTL."""

    def __init__(self, cache_dir: Path, ttl_horas: int = 24) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seg = ttl_horas * 3600

    def _key(self, endpoint: str, params: dict[str, Any] | None) -> str:
        payload = json.dumps(
            {"endpoint": endpoint, "params": params or {}},
            sort_keys=True,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        safe = endpoint.strip("/").replace("/", "_")
        return f"{safe}__{digest}"

    def _path(self, endpoint: str, params: dict[str, Any] | None) -> Path:
        return self.cache_dir / f"{self._key(endpoint, params)}.json"

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        ignore_ttl: bool = False,
    ) -> Optional[Any]:
        """Devuelve la respuesta cacheada o None si no existe / expiró."""
        path = self._path(endpoint, params)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                wrapper = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        if not ignore_ttl:
            age = time.time() - wrapper.get("_cached_at", 0)
            if age > self.ttl_seg:
                return None
        return wrapper.get("data")

    def set(self, endpoint: str, params: dict[str, Any] | None, data: Any) -> None:
        path = self._path(endpoint, params)
        wrapper = {"_cached_at": time.time(), "endpoint": endpoint,
                   "params": params or {}, "data": data}
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(wrapper, fh, ensure_ascii=False)
        tmp.replace(path)  # escritura atómica
