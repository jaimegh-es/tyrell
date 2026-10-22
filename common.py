"""
tyrell · utilidades comunes.

Configuración, rutas, helpers de Markdown (front-matter) y de metadatos.
Solo usa la librería estándar de Python.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

CONFIG_DIR = Path(os.path.expanduser("~/.config/tyrell"))
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "root": "~/Documentos/tyrell",
    "ollama_url": "http://localhost:11434",
    "embed_model": "nomic-embed-text",
    "chunk_size": 1100,
    "chunk_overlap": 180,
    "min_message_chars": 1,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({**DEFAULT_CONFIG, **cfg}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def root_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    return expand(cfg["root"])


# --------------------------------------------------------------------------- #
# Tiempo
# --------------------------------------------------------------------------- #

def now_ms() -> int:
    return int(_dt.datetime.now().timestamp() * 1000)


def ms_to_dt(ms: int | float | None) -> _dt.datetime | None:
    if not ms:
        return None
    try:
        return _dt.datetime.fromtimestamp(float(ms) / 1000.0)
    except (ValueError, OSError, OverflowError):
        return None


def ms_to_iso(ms: int | float | None) -> str | None:
    d = ms_to_dt(ms)
    return d.strftime("%Y-%m-%dT%H:%M:%S") if d else None


def parse_time_any(value) -> int | None:
    """Acepta epoch ms, segundos, ISO 8601 o 'YYYY-MM-DD HH:MM:SS'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:      # milisegundos
            return int(v)
        if v > 1e9:       # segundos
            return int(v * 1000)
        return int(v * 1000) if v else None
    s = str(value).strip()
    if s.isdigit():
        return parse_time_any(int(s))
    s = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            if fmt is None:
                d = _dt.datetime.fromisoformat(s)
            else:
                d = _dt.datetime.strptime(s, fmt)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    return None


def human_time(ms: int | None) -> str:
    d = ms_to_dt(ms)
    return d.strftime("%Y-%m-%d %H:%M") if d else "(sin fecha)"


# --------------------------------------------------------------------------- #
# Texto / slugs / hashes
# --------------------------------------------------------------------------- #

def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def slugify(text: str, maxlen: int = 70, fallback: str = "sin-titulo") -> str:
    text = (text or "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text[:maxlen].strip("-") or fallback)


# --------------------------------------------------------------------------- #
# Markdown con front-matter
# --------------------------------------------------------------------------- #

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def render_front_matter(meta: dict) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def parse_front_matter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            meta[key.strip()] = raw
    return meta, text[m.end():]


def write_markdown(path: Path, meta: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_front_matter(meta) + body.rstrip() + "\n", encoding="utf-8")


def read_markdown(path: Path) -> tuple[dict, str]:
    return parse_front_matter(path.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------------------- #
# Logging ligero
# --------------------------------------------------------------------------- #

class Log:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.warnings: list[str] = []

    def info(self, *parts) -> None:
        if not self.quiet:
            print(*parts, flush=True)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if not self.quiet:
            print(f"  ! {msg}", flush=True)
