"""
tyrell · servidor MCP (Model Context Protocol) por stdio.

Protocolo JSON-RPC 2.0 con mensajes delimitados por saltos de línea.
Sin dependencias: solo la librería estándar. Los registros van a stderr para
no contaminar el canal stdout que usan los clientes MCP.
"""
from __future__ import annotations

import json
import sys
import traceback

import store
from common import Log, parse_time_any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "tyrell", "version": "1.0.0"}

TOOLS = [
    {
        "name": "buscar_palabra",
        "description": "Busca por palabra o frase en todas las conversaciones y documentación "
                       "(índice FTS5/BM25). Devuelve título, id, fuente y un extracto.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "palabras a buscar"},
                "limit": {"type": "integer", "description": "máximo de resultados", "default": 20},
                "source": {"type": "string", "description": "limitar a una fuente (opencode, antigravity, …)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "buscar_semantico",
        "description": "Busca por significado (embeddings vía Ollama). Útil cuando no recuerdas "
                       "las palabras exactas. Requiere Ollama en marcha con el modelo de embeddings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "source": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "buscar_por_fecha",
        "description": "Lista documentos por fecha de creación. Acepta fechas ISO "
                       "(2026-10-01) o epoch ms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "fecha inicial (ISO o epoch ms)"},
                "hasta": {"type": "string", "description": "fecha final (ISO o epoch ms)"},
                "source": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "leer_documento",
        "description": "Devuelve el contenido completo de un documento por su id "
                       "(o por su ruta relativa dentro de tyrell).",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "listar_fuentes",
        "description": "Lista las fuentes disponibles (CLIs, documentación manual) con el número "
                       "de documentos y el rango de fechas.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reindexar",
        "description": "Recalcula el índice de búsqueda tras añadir documentación manual.",
        "inputSchema": {
            "type": "object",
            "properties": {"forzar": {"type": "boolean", "default": False}},
        },
    },
]


def _text(content: str) -> dict:
    return {"content": [{"type": "text", "text": content}]}


def _fmt_rows(rows, include_path=True) -> str:
    if not rows:
        return "Sin resultados."
    out = []
    for r in rows:
        title = r.get("title", "?")
        src = r.get("source", "?")
        score = r.get("score")
        head = f"● {title}  [{src}]" + (f"  score={score:.3f}" if isinstance(score, float) else "")
        ident = r.get("doc_id") or r.get("id") or ""
        lines = [head, f"  id: {ident}"]
        if include_path and r.get("path"):
            lines.append(f"  ruta: tyrell/{r['path']}")
        if r.get("created_iso"):
            lines.append(f"  fecha: {r['created_iso']}")
        ex = (r.get("excerpt") or "").replace("\n", " ").strip()
        if ex:
            lines.append(f"  extracto: {ex[:300]}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def call_tool(cfg: dict, name: str, args: dict) -> dict:
    log = Log(quiet=True)
    if name == "buscar_palabra":
        rows = store.search_keyword(cfg, args.get("query", ""),
                                    limit=int(args.get("limit", 20)), source=args.get("source"))
        return _text(_fmt_rows(rows))
    if name == "buscar_semantico":
        rows, err = store.search_semantic(cfg, args.get("query", ""),
                                          limit=int(args.get("limit", 10)), source=args.get("source"))
        if err:
            return _text("Búsqueda semántica no disponible: Ollama no responde o falta el modelo. "
                         "Arranca Ollama y ejecuta `ollama pull nomic-embed-text`.")
        return _text(_fmt_rows(rows))
    if name == "buscar_por_fecha":
        from common import ms_to_iso
        rows = store.search_date(cfg, parse_time_any(args.get("desde")), parse_time_any(args.get("hasta")),
                                 source=args.get("source"), limit=int(args.get("limit", 100)))
        for r in rows:
            r["created_iso"] = ms_to_iso(r.get("created"))
        return _text(_fmt_rows(rows))
    if name == "leer_documento":
        doc = store.get_doc(cfg, args.get("doc_id", ""))
        if not doc:
            return _text(f"Documento no encontrado: {args.get('doc_id')}")
        return _text(f"# {doc['title']}\n\nruta: tyrell/{doc['path']}\nsource: {doc['source']}\n"
                     f"created: {ms_to_iso(doc['created'])}\n\n---\n\n{doc['content']}")
    if name == "listar_fuentes":
        rows = store.list_sources(cfg)
        if not rows:
            return _text("Índice vacío.")
        return _text("\n".join(
            f"● {r['source']:<14} {r['docs']:>5} docs   {r['first_iso'] or '-'} → {r['last_iso'] or '-'}"
            for r in rows))
    if name == "reindexar":
        stats = store.index_all(cfg, log, embed=True, force=bool(args.get("forzar", False)))
        return _text(json.dumps(stats, ensure_ascii=False, indent=2))
    raise ValueError(f"Herramienta desconocida: {name}")


def handle(cfg: dict, msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = call_tool(cfg, params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as exc:  # error de herramienta -> contenido de texto
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"isError": True, "content": [{"type": "text", "text": f"Error: {exc}"}]}}
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Método no soportado: {method}"}}


def run(cfg: dict) -> None:
    from common import load_config
    cfg = cfg or load_config()
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("tyrell-mcp: mensaje JSON inválido", file=sys.stderr)
            continue
        try:
            reply = handle(cfg, msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            reply = None
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    run({})
