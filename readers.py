"""
tyrell · lectores de conversaciones por CLI.

Cada lector devuelve una lista de conversaciones normalizadas:

    {
        "source":     "opencode" | "antigravity" | "cline" | "kilo" | "cursor" | "freebuff",
        "native_id":  "<id original>",
        "title":      "...",
        "created":    <epoch ms | None>,
        "updated":    <epoch ms | None>,
        "directory":  "...",
        "model":      "...",
        "messages":   [{"role": "...", "time": <ms|None>, "text": "..."}],
    }

Los formatos opacos (protobuf de antigravity, almacenes aún desconocidos de
cline/kilo/cursor/freebuff) se extraen de forma *best-effort*.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from common import expand, ms_to_iso, parse_time_any

# --------------------------------------------------------------------------- #
# Utilidades de extracción
# --------------------------------------------------------------------------- #

_PRINTABLE_RUN = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f]{8,}")


def printable_runs(blob: bytes, minlen: int = 12) -> list[str]:
    """Extrae fragmentos legibles de un blob binario (protobuf, etc.)."""
    text = blob.decode("utf-8", "ignore")
    runs = []
    for run in _PRINTABLE_RUN.findall(text):
        run = "".join(ch for ch in run if ch.isprintable() or ch in "\n\t").strip()
        if len(run) >= minlen and not _looks_binary(run):
            runs.append(run)
    # deduplica manteniendo orden
    seen, out = set(), []
    for r in runs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _looks_binary(s: str) -> bool:
    if not s:
        return True
    weird = sum(1 for c in s if ord(c) < 32 and c not in "\n\t\r")
    return weird / len(s) > 0.05


def flatten_content(content) -> str | None:
    """Normaliza el 'content' de un mensaje (str, lista de bloques, dict)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value", "message"):
            if key in content:
                got = flatten_content(content[key])
                if got:
                    return got
        return None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            got = flatten_content(item)
            if got:
                parts.append(got)
        return "\n\n".join(parts) if parts else None
    return str(content)


def messages_from_json(obj) -> list[dict] | None:
    """Intenta interpretar un JSON como lista de mensajes role/content."""
    if isinstance(obj, list):
        out, ok = [], False
        for item in obj:
            if not isinstance(item, dict):
                return None
            role = item.get("role") or item.get("sender") or item.get("author")
            content = item.get("content", item.get("text", item.get("message")))
            if role is None and content is None:
                return None
            text = flatten_content(content)
            if not text:
                continue
            out.append({
                "role": str(role or "msg"),
                "text": text,
                "time": parse_time_any(item.get("timestamp") or item.get("time") or item.get("ts")),
            })
            ok = True
        return out if ok else None
    if isinstance(obj, dict):
        for key in ("messages", "conversation", "history", "items", "turns", "chat"):
            if key in obj:
                got = messages_from_json(obj[key])
                if got:
                    return got
    return None


def _conn(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


# --------------------------------------------------------------------------- #
# opencode  ·  ~/.local/share/opencode/opencode.db  (SQLite + JSON)
# --------------------------------------------------------------------------- #

def _render_opencode_part(part: dict) -> str | None:
    if not isinstance(part, dict):
        return flatten_content(part)
    kind = part.get("type")
    if kind == "text":
        return part.get("text") or None
    if kind == "reasoning":
        text = part.get("text") or ""
        return f"<details><summary>🧠 razonamiento</summary>\n\n{text}\n\n</details>" if text else None
    if kind == "tool":
        name = part.get("name", "tool")
        state = part.get("state") or {}
        bits = []
        for key in ("input", "output"):
            if key in state:
                val = state[key]
                if not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False, indent=2)
                bits.append(f"**{key}**\n```\n{val.strip()[:4000]}\n```")
        if not bits:
            bits.append("```\n" + json.dumps(state, ensure_ascii=False)[:2000] + "\n```")
        return f"<details><summary>🛠️ `{name}`</summary>\n\n" + "\n\n".join(bits) + "\n\n</details>"
    if kind in ("step-start", "step-finish", "snapshot", "patch"):
        return None
    text = flatten_content(part)
    return text


def read_opencode(log) -> list[dict]:
    db = expand("~/.local/share/opencode/opencode.db")
    if not db.exists():
        return []
    convs: list[dict] = []
    try:
        con = _conn(db)
        sessions = con.execute(
            "select id, title, directory, model, time_created, time_updated "
            "from session_v2 order by time_created"
        ).fetchall()
        for s in sessions:
            msgs = []
            for row in con.execute(
                "select type, data from session_message where session_id=? order by seq",
                (s["id"],),
            ):
                try:
                    data = json.loads(row["data"])
                except json.JSONDecodeError:
                    continue
                kind = row["type"]
                t = parse_time_any((data.get("time") or {}).get("created"))
                if kind == "user":
                    text = data.get("text") or flatten_content(data.get("content"))
                    if text:
                        msgs.append({"role": "user", "time": t, "text": text})
                elif kind == "assistant":
                    blocks = [b for b in (_render_opencode_part(p) for p in data.get("content", [])) if b]
                    text = "\n\n".join(blocks)
                    if data.get("error"):
                        err = data["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        text = (text + f"\n\n> ⚠️ error: {msg}").strip()
                    if text:
                        msgs.append({"role": "assistant", "time": t, "text": text})
                elif kind == "idle":
                    outcome = data.get("outcome")
                    if outcome and outcome != "success":
                        msgs.append({"role": "info", "time": t, "text": f"(estado de sesión: {outcome})"})
                elif kind == "model-switched":
                    m = data.get("model", {})
                    msgs.append({"role": "info", "time": t,
                                 "text": f"(modelo cambiado a {m.get('providerID')}/{m.get('id')})"})
            if not msgs:
                continue
            model = s["model"]
            if model:
                try:
                    mo = json.loads(model)
                    model = f"{mo.get('providerID')}/{mo.get('id')}"
                except (json.JSONDecodeError, TypeError):
                    pass
            convs.append({
                "source": "opencode",
                "native_id": s["id"],
                "title": s["title"] or "(sin título)",
                "created": parse_time_any(s["time_created"]),
                "updated": parse_time_any(s["time_updated"]),
                "directory": s["directory"],
                "model": model,
                "messages": msgs,
            })
        con.close()
    except sqlite3.Error as exc:
        log.warn(f"opencode: no se pudo leer {db}: {exc}")
    return convs


# --------------------------------------------------------------------------- #
# antigravity  ·  ~/.gemini/antigravity-cli  (SQLite + protobuf)
# --------------------------------------------------------------------------- #

_AG_ROLE = {14: "user", 15: "assistant", 132: "tool", 101: "info"}


def _keep_ag_run(run: str) -> bool:
    """Heurística: descarta identificadores/base64 y artefactos del protocolo."""
    stripped = run.strip()
    if len(stripped) < 20:
        return False
    spaces = stripped.count(" ")
    if spaces == 0 and len(stripped) < 80:
        return False           # probablemente un id, hash o uuid
    alnum = sum(c.isalnum() for c in stripped)
    if alnum / len(stripped) > 0.92 and spaces <= 1:
        return False           # token tipo base64
    return True


def read_antigravity(log) -> list[dict]:
    base = expand("~/.gemini/antigravity-cli")
    convs_dir = base / "conversations"
    summaries_db = base / "conversation_summaries.db"
    if not convs_dir.exists() and not summaries_db.exists():
        return []

    summaries: dict[str, dict] = {}
    if summaries_db.exists():
        try:
            con = _conn(summaries_db)
            for row in con.execute("select * from conversation_summaries"):
                summaries[row["conversation_id"]] = dict(row)
            con.close()
        except sqlite3.Error as exc:
            log.warn(f"antigravity: no se pudo leer resúmenes: {exc}")

    ids = set(summaries)
    if convs_dir.exists():
        ids |= {p.stem for p in convs_dir.glob("*.db")}

    convs: list[dict] = []
    for cid in sorted(ids):
        meta = summaries.get(cid, {})
        db = convs_dir / f"{cid}.db"
        msgs: list[dict] = []
        if db.exists():
            try:
                con = _conn(db)
                for row in con.execute("select idx, step_type, step_payload from steps order by idx"):
                    blob = row["step_payload"]
                    if not blob:
                        continue
                    runs = [r for r in printable_runs(blob, minlen=16) if _keep_ag_run(r)]
                    if not runs:
                        continue
                    text = "\n\n".join(runs)
                    role = _AG_ROLE.get(row["step_type"], f"step-{row['step_type']}")
                    msgs.append({"role": role, "time": None, "text": text})
                con.close()
            except sqlite3.Error as exc:
                log.warn(f"antigravity: fallo leyendo {db.name}: {exc}")
        if not msgs:
            continue
        created = parse_time_any(meta.get("last_user_input_time")) or parse_time_any(meta.get("last_modified_time"))
        convs.append({
            "source": "antigravity",
            "native_id": cid,
            "title": meta.get("title") or "(sin título)",
            "created": created,
            "updated": parse_time_any(meta.get("last_modified_time")),
            "directory": meta.get("workspace_uris") or "",
            "model": meta.get("agent_name") or "",
            "messages": msgs,
        })
    return convs


# --------------------------------------------------------------------------- #
# kilo  ·  ~/.local/share/kilo/kilo.db  (MySQLo: schema derivado de opencode)
# --------------------------------------------------------------------------- #

def _render_kilo_part(p: dict) -> str | None:
    kind = p.get("type")
    if kind == "text":
        text = p.get("text")
        if not text:
            return None
        if p.get("synthetic") or p.get("ignored"):
            return None
        return text
    if kind == "reasoning":
        text = p.get("text") or ""
        return f"<details><summary>🧠 razonamiento</summary>\n\n{text}\n\n</details>" if text else None
    if kind == "tool":
        name = p.get("name", "tool")
        state = p.get("state") or {}
        bits = []
        for key in ("input", "output"):
            if key in state:
                val = state[key]
                if not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False, indent=2)
                bits.append(f"**{key}**\n```\n{val.strip()[:4000]}\n```")
        if bits:
            return f"<details><summary>🛠️ `{name}`</summary>\n\n" + "\n\n".join(bits) + "\n\n</details>"
        return None
    if kind == "file":
        fn = p.get("filename") or p.get("url") or "archivo"
        content = p.get("content")
        if not content:
            return None
        return f"<details><summary>📄 `{fn}`</summary>\n\n```\n{str(content)[:4000]}\n```\n\n</details>"
    return None


def read_kilo(log) -> list[dict]:
    db = expand("~/.local/share/kilo/kilo.db")
    if not db.exists():
        return []
    convs: list[dict] = []
    try:
        con = _conn(db)
        sessions = con.execute(
            "select id, title, directory, metadata, time_created, time_updated "
            "from session order by time_created"
        ).fetchall()
        for s in sessions:
            rows = con.execute(
                """select p.time_created as t, p.data as pdata, m.data as mdata
                   from part p join message m on m.id = p.message_id
                   where p.session_id = ? order by p.time_created""",
                (s["id"],),
            ).fetchall()
            msgs: list[dict] = []
            model = None
            for r in rows:
                try:
                    pdat = json.loads(r["pdata"])
                    mdat = json.loads(r["mdata"])
                except (json.JSONDecodeError, TypeError):
                    continue
                role = mdat.get("role") or "assistant"
                t = parse_time_any(r["t"])
                if role == "assistant" and mdat.get("modelID") and not model:
                    model = f"{mdat.get('providerID')}/{mdat.get('modelID')}"
                text = _render_kilo_part(pdat)
                if text:
                    msgs.append({"role": role, "time": t, "text": text})
            if not msgs:
                continue
            if not model and s["metadata"]:
                try:
                    md = json.loads(s["metadata"])
                    if md.get("model"):
                        mo = md["model"]
                        model = f"{mo.get('providerID')}/{mo.get('id')}"
                except (json.JSONDecodeError, TypeError):
                    pass
            convs.append({
                "source": "kilo",
                "native_id": s["id"],
                "title": s["title"] or "(sin título)",
                "created": parse_time_any(s["time_created"]),
                "updated": parse_time_any(s["time_updated"]),
                "directory": s["directory"],
                "model": model,
                "messages": msgs,
            })
        con.close()
    except sqlite3.Error as exc:
        log.warn(f"kilo: no se pudo leer {db}: {exc}")
    return convs


# --------------------------------------------------------------------------- #
# cline  ·  ~/.cline/data/sessions/<id>/<id>.messages.json
# --------------------------------------------------------------------------- #

def _render_content_blocks(blocks) -> str | None:
    parts: list[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            t = flatten_content(b)
            if t:
                parts.append(t)
            continue
        kind = b.get("type")
        if kind in ("text", "content") or (kind is None and b.get("text")):
            txt = b.get("text") or b.get("content")
            if txt:
                parts.append(str(txt))
        elif kind in ("thinking", "reasoning"):
            txt = b.get("thinking") or b.get("text")
            if txt:
                parts.append(f"<details><summary>🧠 razonamiento</summary>\n\n{txt}\n\n</details>")
        elif kind in ("tool_use", "toolUse", "tool"):
            name = b.get("name", "tool")
            inp = b.get("input") or b.get("state") or b.get("arguments")
            if not isinstance(inp, str):
                try:
                    inp = json.dumps(inp, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    inp = str(inp)
            parts.append(f"<details><summary>🛠️ `{name}`</summary>\n\n```\n{inp[:4000]}\n```\n\n</details>")
        elif kind in ("tool_result", "toolResult", "result"):
            txt = flatten_content(b.get("content")) or flatten_content(b.get("output"))
            if txt:
                parts.append(f"```\n{txt[:4000]}\n```")
        elif kind in ("file", "image"):
            continue
        else:
            txt = flatten_content(b.get("text")) or flatten_content(b.get("content"))
            if txt:
                parts.append(str(txt))
    return "\n\n".join(parts) if parts else None


def read_cline(log) -> list[dict]:
    base = expand("~/.cline/data/sessions")
    if not base.exists():
        return []
    convs: list[dict] = []
    for msgs_path in sorted(base.glob("*/**.messages.json")):
        try:
            data = json.loads(msgs_path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        msgs: list[dict] = []
        first_ts = updated = None
        model = None
        title = None
        for m in data.get("messages") or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role") or "msg"
            ts = parse_time_any(m.get("ts") or m.get("timestamp"))
            first_ts = first_ts or ts
            updated = ts or updated
            if m.get("modelInfo") and not model:
                mi = m["modelInfo"]
                model = f"{mi.get('provider')}/{mi.get('id')}"
            text = flatten_content(m.get("content")) or _render_content_blocks(m.get("blocks"))
            if not text:
                text = _render_content_blocks(m.get("content"))
            if text:
                msgs.append({"role": role, "time": ts, "text": text})
        if not msgs:
            continue
        # título: prompt guardado o primer mensaje de usuario
        session_file = msgs_path.with_suffix("")
        try:
            meta = json.loads(session_file.with_name(session_file.name).read_text(encoding="utf-8", errors="replace"))
            title = meta.get("prompt") or title
        except (json.JSONDecodeError, OSError):
            pass
        if not title:
            title = next((m["text"] for m in msgs if m["role"] == "user"), None) or msgs[0]["text"]
        title = (title or "").strip().splitlines()[0][:80]
        convs.append({
            "source": "cline",
            "native_id": msgs_path.stem,
            "title": title or msgs_path.stem,
            "created": first_ts,
            "updated": updated,
            "directory": "",
            "model": model,
            "messages": msgs,
        })
    # si no hay archivos .messages.json, caer al genérico sobre sessions.db
    if not convs:
        db = expand("~/.cline/data/db/sessions.db")
        if db.exists():
            try:
                con = _conn(db)
                for row in con.execute("select session_id, prompt, transcript_path, started_at from sessions").fetchall():
                    if not row["prompt"]:
                        continue
                    t = parse_time_any(row["started_at"])
                    convs.append({
                        "source": "cline", "native_id": row["session_id"],
                        "title": row["prompt"].strip()[:80],
                        "created": t, "updated": t, "directory": "", "model": "",
                        "messages": [{"role": "user", "time": t, "text": row["prompt"]}],
                    })
                con.close()
            except sqlite3.Error as exc:
                log.warn(f"cline: no se pudo leer sessions.db: {exc}")
    return convs


# --------------------------------------------------------------------------- #
# freebuff  ·  ~/.config/manicode/projects/<proyecto>/chats/<ts>/
# --------------------------------------------------------------------------- #

def _parse_chat_dirname(name: str):
    """Normaliza '2026-10-12T20-35-27.843Z' a ISO válido y lo convierte a ms."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})(.*)$", name)
    if m:
        name = f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}{m.group(5)}"
    return parse_time_any(name)


def read_freebuff(log) -> list[dict]:
    base = expand("~/.config/manicode/projects")
    if not base.exists():
        return []
    convs: list[dict] = []
    for mpath in sorted(base.glob("*/chats/*/chat-messages.json")):
        chat_dir = mpath.parent
        try:
            data = json.loads(mpath.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list) or not data:
            continue
        msgs: list[dict] = []
        title = None
        for m in data:
            if not isinstance(m, dict):
                continue
            variant = m.get("variant")
            role = variant if variant in ("user", "ai", "tool", "assistant", "system") else \
                   {"user": "user", "ai": "assistant"}.get(variant, str(variant or "msg"))
            text = m.get("content") or _render_content_blocks(m.get("blocks"))
            if not text:
                continue
            if role == "user" and not title:
                title = text.strip().splitlines()[0][:80]
            msgs.append({"role": role, "time": None, "text": text})
        if not msgs:
            continue
        created = _parse_chat_dirname(chat_dir.name)  # 2026-10-12T20-35-27.843Z
        updated = created
        meta_path = chat_dir / "chat-meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
                if meta.get("messagesMtimeMs"):
                    updated = parse_time_any(meta["messagesMtimeMs"])
                title = title or str(meta.get("firstPrompt") or "").strip() or title
            except (json.JSONDecodeError, OSError):
                pass
        path_parts = chat_dir.relative_to(base).parts
        convs.append({
            "source": "freebuff",
            "native_id": f"{path_parts[0]}/{path_parts[2]}",
            "title": title or mpath.stem,
            "created": created,
            "updated": updated,
            "directory": str(base / path_parts[0]),
            "model": "",
            "messages": msgs,
        })
    return convs


# --------------------------------------------------------------------------- #
# Lector genérico (cursor)
# --------------------------------------------------------------------------- #

# nombres que casi nunca son conversaciones y sí mucho ruido
_NOISE_NAMES = {
    "package.json", "package-lock.json", "tsconfig.json", "jsconfig.json",
    "manifest.json", "settings.json", "export.json", "launch.json",
    "extensions.json", "keybindings.json", "args.json", "state.json",
}
# nombres/directorios que SÍ sugieren conversaciones
_CONVO_HINTS = ("conversation", "chat", "history", "message", "task", "session",
                "transcript", "trajectory", "turn")
_SKIP_PARTS = {"node_modules", ".git", "dist", "build", "out", "_versions",
               "versions", "tree-sitter", "__pycache__", ".cache"}


def _looks_conversation(path: Path) -> bool:
    name = path.name.lower()
    return any(h in name for h in _CONVO_HINTS) or any(
        h in part.lower() for part in path.parts for h in _CONVO_HINTS
    )


def _extract_json_convs(obj, source: str, path: Path, allow_raw: bool) -> list[dict]:
    def _conv(msgs: list[dict]) -> dict:
        mt = parse_time_any(int(path.stat().st_mtime * 1000))
        return {
            "source": source,
            "native_id": str(path.relative_to(expand("~"))),
            "title": path.stem,
            "created": mt,
            "updated": mt,
            "directory": "",
            "model": "",
            "messages": msgs,
        }

    msgs = messages_from_json(obj)
    if msgs:
        return [_conv(msgs)]
    if not allow_raw:
        return []
    strings = printable_strings_from_obj(obj)
    if not strings:
        return []
    mt = parse_time_any(int(path.stat().st_mtime * 1000))
    return [_conv([{"role": "raw", "time": mt, "text": s} for s in strings])]


def printable_strings_from_obj(obj, minlen: int = 40) -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, str):
            s = node.strip()
            if len(s) >= minlen and not _looks_binary(s):
                found.append(s)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    seen, out = set(), []
    for s in found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _db_convs(db: Path, source: str, log) -> list[dict]:
    convs: list[dict] = []
    try:
        con = _conn(db)
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'"
        )]
        for table in tables:
            if not any(h in table.lower() for h in _DB_TABLE_HINTS):
                continue
            try:
                cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')]
            except sqlite3.Error:
                continue
            text_cols = [c for c in cols if any(
                k in c.lower() for k in ("text", "message", "content", "prompt", "response", "data")
            )]
            if not text_cols:
                continue
            rows = con.execute(f'select * from "{table}" limit 5000').fetchall()
            msgs = []
            for row in rows:
                for c in text_cols:
                    val = row[c]
                    if isinstance(val, (bytes, bytearray)):
                        runs = printable_runs(bytes(val))
                        if runs:
                            msgs.append({"role": "raw", "time": None, "text": "\n\n".join(runs)})
                    elif isinstance(val, str) and len(val.strip()) >= 20:
                        msgs.append({"role": "raw", "time": None, "text": val.strip()})
            if msgs:
                mt = parse_time_any(int(db.stat().st_mtime * 1000))
                convs.append({
                    "source": source, "native_id": f"{db.name}:{table}",
                    "title": f"{db.name} · {table}", "created": mt, "updated": mt,
                    "directory": "", "model": "", "messages": msgs,
                })
        con.close()
    except sqlite3.Error as exc:
        log.warn(f"{source}: no se pudo leer {db.name}: {exc}")
    return convs


_DB_TABLE_HINTS = ("conversation", "message", "chat", "task", "history", "step", "trajectory")


def read_generic(source: str, candidate_dirs: list[str], log) -> list[dict]:
    convs: list[dict] = []
    for raw in candidate_dirs:
        d = expand(raw)
        if not d.exists():
            continue
        for path in sorted(d.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.name in _NOISE_NAMES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0 or size > 25 * 1024 * 1024:
                continue
            if path.suffix == ".json" or path.name.endswith(".jsonl"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                convs.extend(_extract_json_convs(data, source, path, allow_raw=_looks_conversation(path)))
            elif path.suffix == ".db":
                convs.extend(_db_convs(path, source, log))
    return convs


# --------------------------------------------------------------------------- #
# cursor  ·  ~/.cursor/projects/*/agent-transcripts/<thread>/<thread>.jsonl
# --------------------------------------------------------------------------- #

def _cursor_meta_for(thread: str) -> dict | None:
    """Busca chat-meta.json en ~/.cursor/chats/*/<thread>/meta.json."""
    chats = expand("~/.cursor/chats")
    if not chats.exists():
        return None
    for side in chats.glob("*/" + thread):
        mp = side / "meta.json"
        if mp.exists():
            try:
                return json.loads(mp.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                return None
    return None


def _cursor_user_text(text: str) -> str:
    """Limpia el envoltorio <timestamp>/<user_query> que añade cursor al prompt."""
    m = re.search(r"<user_query>(.*?)</user_query>", text, re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return re.sub(r"<timestamp>.*?</timestamp>", "", text, flags=re.S).strip()


def read_cursor(log) -> list[dict]:
    convs: list[dict] = []
    base = expand("~/.cursor/projects")
    if base.exists():
        for jsonl in sorted(base.glob("*/agent-transcripts/*/*.jsonl")):
            thread = jsonl.parent.name
            msgs: list[dict] = []
            first_ts = updated = None
            meta = _cursor_meta_for(thread)
            if meta:
                first_ts = parse_time_any(meta.get("createdAtMs"))
                updated = parse_time_any(meta.get("updatedAtMs"))
            for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = ev.get("role")
                if role not in ("user", "assistant"):
                    continue  # eventos sin role (turn_ended, status…)
                msg = ev.get("message") or {}
                text = _render_content_blocks(msg.get("content"))
                if not text:
                    continue
                if role == "user":
                    text = _cursor_user_text(text) or text
                msgs.append({"role": role, "time": None, "text": text})
            if not msgs:
                continue
            title = (meta or {}).get("title") or next(
                (m["text"] for m in msgs if m["role"] == "user"), None) or thread
            title = str(title).strip().splitlines()[0][:80]
            convs.append({
                "source": "cursor",
                "native_id": thread,
                "title": title,
                "created": first_ts,
                "updated": updated,
                "directory": str(jsonl.parent.parent.parent),
                "model": "",
                "messages": msgs,
            })
    # respaldo: chats sin transcript todavía → prompt_history.json + meta.json
    if not convs:
        chats = expand("~/.cursor/chats")
        if chats.exists():
            for meta_p in sorted(chats.glob("*/*/meta.json")):
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not meta.get("hasConversation"):
                    continue
                ph = meta_p.parent / "prompt_history.json"
                prompts = []
                if ph.exists():
                    try:
                        prompts = json.loads(ph.read_text(encoding="utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        prompts = []
                msgs = [{"role": "user", "time": None, "text": str(p)} for p in prompts if str(p).strip()]
                if msgs:
                    ts = parse_time_any(meta.get("createdAtMs"))
                    convs.append({
                        "source": "cursor",
                        "native_id": meta_p.parent.name,
                        "title": str(meta.get("title") or prompts[0])[:80],
                        "created": ts, "updated": parse_time_any(meta.get("updatedAtMs")) or ts,
                        "directory": str(meta_p.parent), "model": "",
                        "messages": msgs,
                    })
    return convs


# --------------------------------------------------------------------------- #
# Registro de fuentes
# --------------------------------------------------------------------------- #

GENERIC_SOURCES: dict[str, list[str]] = {}


def read_all(cfg, log) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    result["opencode"] = read_opencode(log)
    result["antigravity"] = read_antigravity(log)
    result["kilo"] = read_kilo(log)
    result["cline"] = read_cline(log)
    result["freebuff"] = read_freebuff(log)
    result["cursor"] = read_cursor(log)
    for source, dirs in GENERIC_SOURCES.items():
        result[source] = [c for c in read_generic(source, dirs, log) if c["messages"]]
    return result


# --------------------------------------------------------------------------- #
# Render a Markdown
# --------------------------------------------------------------------------- #

_ROLE_ICON = {"user": "👤 Usuario", "assistant": "🤖 Asistente", "tool": "🛠️ Herramienta",
              "system": "⚙️ Sistema", "info": "ℹ️", "raw": "📄"}


def render_conversation(conv: dict) -> str:
    lines = []
    for msg in conv["messages"]:
        label = _ROLE_ICON.get(msg["role"], f"`{msg['role']}`")
        stamp = f" — {ms_to_iso(msg['time'])}" if msg.get("time") else ""
        lines.append(f"### {label}{stamp}\n")
        lines.append((msg.get("text") or "").rstrip() + "\n")
    return "\n".join(lines)
