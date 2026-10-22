"""
tyrell · cliente de embeddings vía Ollama (http://localhost:11434).

Sin dependencias externas. Si Ollama no está disponible, todo el sistema
sigue funcionando en modo "solo palabra + fecha" (FTS5).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def available(cfg: dict) -> bool:
    url = cfg["ollama_url"].rstrip("/")
    try:
        data = _get(f"{url}/api/tags", 3)
        return isinstance(data, dict)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def model_present(cfg: dict) -> bool:
    url = cfg["ollama_url"].rstrip("/")
    model = cfg["embed_model"]
    try:
        data = _get(f"{url}/api/tags", 3)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    base = model.split(":")[0]
    return any(base in (m.get("name") or "") for m in data.get("models", []))


_BATCH = 32  # trocear la entrada: docs grandes -> miles de fragmentos


def embed(texts: list[str], cfg: dict, timeout: float = 120.0) -> list[list[float]] | None:
    """Devuelve una lista de vectores, o None si Ollama no responde.

    Envía la entrada en lotes pequeños para no mandar peticiones gigantes
    (documentos con miles de fragmentos) que superarían el timeout.
    """
    if not texts:
        return []
    url = cfg["ollama_url"].rstrip("/")
    model = cfg["embed_model"]
    out: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        part = texts[start:start + _BATCH]
        # API moderna por lotes
        done = False
        try:
            data = _post(f"{url}/api/embed", {"model": model, "input": part}, timeout)
            embs = data.get("embeddings")
            if embs and len(embs) == len(part):
                out.extend(embs)
                done = True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if done:
            continue
        # API clásica, uno a uno
        try:
            for t in part:
                data = _post(f"{url}/api/embeddings", {"model": model, "prompt": t}, timeout)
                out.append(data["embedding"])
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            return None
    return out
