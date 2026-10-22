#!/usr/bin/env python3
"""
tyrell · cerebro centralizado de conocimiento para CLIs de IA.

Comandos:
  init        crea la estructura de carpetas y los AGENTS.md
  dump        vuelca TODAS las conversaciones a .md y reindexa
  reindex     reconstruye el índice de búsqueda
  status      estado del índice
  sources     fuentes detectadas
  search      búsqueda por palabra / semántica / fecha
  mcp         servidor MCP por stdio (lo consumen las IAs)
  mcp-config  imprime los fragmentos de configuración MCP por CLI
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# permitir imports relativos cuando se ejecuta como script
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402
import store  # noqa: E402
from common import (Log, load_config, root_dir, save_config, write_markdown,  # noqa: E402
                    ms_to_iso, ms_to_dt, now_ms, sha1, slugify, DEFAULT_CONFIG)

SOURCES = ["opencode", "antigravity", "cline", "kilo", "cursor", "freebuff"]


# --------------------------------------------------------------------------- #
# Estructura / AGENTS.md
# --------------------------------------------------------------------------- #

def _ensure_agents(path: Path, text: str) -> None:
    target = path / "AGENTS.md"
    if target.exists():
        try:
            if agents.MANAGED_MARK not in target.read_text(encoding="utf-8", errors="replace"):
                return  # respeta un AGENTS.md escrito por el usuario
        except OSError:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def ensure_skeleton(cfg, log) -> Path:
    root = root_dir(cfg)
    (root).mkdir(parents=True, exist_ok=True)
    (root / "conversaciones").mkdir(exist_ok=True)
    (root / "documentacion").mkdir(exist_ok=True)
    (root / "indice").mkdir(exist_ok=True)
    (root / ".db").mkdir(exist_ok=True)
    for s in SOURCES:
        (root / "conversaciones" / s).mkdir(parents=True, exist_ok=True)

    # maestro (se sobrescribe: es nuestro)
    (root / "AGENTS.md").write_text(agents.root_agents_text(root), encoding="utf-8")
    if not (root / "README.md").exists():
        (root / "README.md").write_text(agents.README, encoding="utf-8")

    # uno por carpeta creada
    for rel, role in [("conversaciones", "conversaciones"),
                      ("documentacion", "documentacion"),
                      ("indice", "indice")]:
        _ensure_agents(root / rel, agents.folder_agents_text(root, rel, role))
    for s in SOURCES:
        _ensure_agents(root / "conversaciones" / s,
                       agents.folder_agents_text(root, f"conversaciones/{s}", "conversaciones"))
    log.info(f"estructura lista en {root}")
    return root


# --------------------------------------------------------------------------- #
# Dump
# --------------------------------------------------------------------------- #

def cmd_dump(args, cfg, log) -> int:
    import readers

    root = ensure_skeleton(cfg, log)
    log.info("leyendo conversaciones…")
    data = readers.read_all(cfg, log)

    written = 0
    per_source: dict[str, int] = {}
    for source, convs in data.items():
        for conv in convs:
            if not conv.get("messages"):
                continue
            created = conv.get("created") or conv.get("updated") or now_ms()
            day = (ms_to_dt(created) or ms_to_dt(now_ms())).strftime("%Y-%m-%d")
            native = str(conv.get("native_id") or "")
            short = "".join(ch for ch in native if ch.isalnum())[-8:] or sha1(native)[:8]
            slug = slugify(conv.get("title") or native or "sin-titulo")
            out_dir = root / "conversaciones" / source / day
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{day}-{slug}-{short}.md"

            meta = {
                "title": conv.get("title") or "(sin título)",
                "source": source,
                "id": f"{source}:{native}",
                "native_id": native,
                "created": ms_to_iso(created),
                "updated": ms_to_iso(conv.get("updated")),
                "directory": conv.get("directory") or None,
                "model": conv.get("model") or None,
                "messages": len(conv["messages"]),
            }
            body = f"# {meta['title']}\n\n" + readers.render_conversation(conv)
            write_markdown(path, meta, body)

            _ensure_agents(out_dir, agents.folder_agents_text(root, f"conversaciones/{source}/{day}", "conversaciones"))
            _ensure_agents(out_dir.parent, agents.folder_agents_text(root, f"conversaciones/{source}", "conversaciones"))
            written += 1
            per_source[source] = per_source.get(source, 0) + 1
        log.info(f"  {source}: {len(convs)} conversaciones leídas")

    _write_indexes(cfg, root, log, per_source)

    if args.prune:
        current_ids = {f"{c['source']}:{c['native_id']}" for src in data.values() for c in src if c.get("messages")}
        for f in (root / "conversaciones").rglob("*.md"):
            if f.name == "AGENTS.md":
                continue
            meta = __import__("common").read_markdown(f)[0]
            if meta.get("source") in SOURCES and meta.get("id") not in current_ids:
                f.unlink()
                log.info(f"  - eliminado volcado obsoleto: {f.name}")

    log.info("indexando…")
    store.index_all(cfg, log, embed=not args.no_embed, force=args.force)
    s = store.stats(cfg)
    log.info(f"dump completo: {written} conversaciones escritas, "
             f"índice -> {s['docs']} docs, {s['vectors']} vectores "
             f"({'semántico activo' if s['semantic'] else 'solo palabra+fecha'})")
    return 0


def _write_indexes(cfg, root: Path, log, per_source: dict) -> None:
    idx = root / "indice"
    idx.mkdir(exist_ok=True)
    srccounts = store.list_sources(cfg)

    lines = ["# Índice de conocimiento · tyrell", "",
             f"Actualizado: {ms_to_iso(now_ms())}", "",
             "## Volcado de esta ejecución", ""]
    if per_source:
        for s, n in sorted(per_source.items()):
            lines.append(f"- `{s}`: {n} conversaciones")
    else:
        lines.append("- (sin conversaciones nuevas)")
    lines += ["", "## Fuentes en el índice", "",
              "| fuente | docs | primera | última |", "|---|---|---|---|"]
    for s in srccounts:
        lines.append(f"| {s['source']} | {s['docs']} | {s['first_iso'] or '-'} | {s['last_iso'] or '-'} |")
    (idx / "_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    docs = store.search_date(cfg, limit=500)
    lines = ["# Cronología", ""]
    current = None
    for d in docs:
        day = (ms_to_iso(d["created"]) or "")[:10] or "(sin fecha)"
        if day != current:
            lines.append(f"\n## {day}")
            current = day
        lines.append(f"- [{d['title']}](../{d['path']}) — `{d['source']}`")
    (idx / "por-fecha.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Otros comandos
# --------------------------------------------------------------------------- #

def cmd_reindex(args, cfg, log) -> int:
    stats = store.index_all(cfg, log, embed=not args.no_embed, force=args.force)
    log.info(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args, cfg, log) -> int:
    s = store.stats(cfg)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


def cmd_sources(args, cfg, log) -> int:
    for s in store.list_sources(cfg):
        print(f"{s['source']:<14} {s['docs']:>5} docs   {s['first_iso'] or '-'} → {s['last_iso'] or '-'}")
    return 0


def cmd_search(args, cfg, log) -> int:
    if args.semantic:
        results, err = store.search_semantic(cfg, args.query, limit=args.limit, source=args.source)
        if err:
            print("Búsqueda semántica no disponible (¿Ollama y el modelo instalados?).", file=sys.stderr)
            return 2
    elif args.by_date:
        from common import parse_time_any
        results = store.search_date(cfg, parse_time_any(args.since), parse_time_any(args.until),
                                    source=args.source, limit=args.limit)
    else:
        results = store.search_keyword(cfg, args.query, limit=args.limit, source=args.source)
    for r in results:
        title = r.get("title")
        src = r.get("source")
        score = r.get("score")
        extra = f"  [{score:.3f}]" if isinstance(score, (int, float)) else ""
        print(f"\n● {title}  ({src}){extra}")
        print(f"  id: {r.get('doc_id', r.get('id'))}")
        exc = (r.get("excerpt") or r.get("path") or "").replace("\n", " ")
        if exc:
            print(f"  {exc[:240]}")
    return 0


def cmd_mcp(args, cfg, log) -> int:
    import mcp_server
    mcp_server.run(cfg)
    return 0


def cmd_mcp_config(args, cfg, log) -> int:
    tool = str(Path(__file__).resolve())
    py = sys.executable
    snippets = {
        "opencode": {
            "mcp": {"tyrell": {"type": "local", "command": [py, tool, "mcp"], "enabled": True}}
        },
        "cline": {"mcpServers": {"tyrell": {"command": py, "args": [tool, "mcp"]}}},
        "cursor": {"mcpServers": {"tyrell": {"command": py, "args": [tool, "mcp"]}}},
        "kilo": {"mcpServers": {"tyrell": {"command": py, "args": [tool, "mcp"]}}},
        "freebuff": {"mcpServers": {"tyrell": {"command": py, "args": [tool, "mcp"]}}},
    }
    print(json.dumps(snippets, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args, cfg, log) -> int:
    save_config(cfg)
    ensure_skeleton(cfg, log)
    log.info(f"config: {__import__('common').CONFIG_PATH}")
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tyrell", description="Conocimiento centralizado para CLIs de IA")
    p.add_argument("--quiet", action="store_true", help="silencia la salida informativa")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_embed_flags(sp):
        sp.add_argument("--no-embed", action="store_true", help="no calcular embeddings (solo palabra+fecha)")
        sp.add_argument("--force", action="store_true", help="reindexa todo aunque no haya cambios")

    sub.add_parser("init", help="crea estructura y config").set_defaults(func=cmd_init)
    sp = sub.add_parser("dump", help="volcar conversaciones + reindexar")
    add_embed_flags(sp)
    sp.add_argument("--quiet", action="store_true", help="silencia la salida informativa")
    sp.add_argument("--prune", action="store_true",
                    help="elimina volcados .md que ya no corresponden a ninguna conversación")
    sp.set_defaults(func=cmd_dump)
    sp = sub.add_parser("reindex", help="reconstruir índice")
    add_embed_flags(sp)
    sp.add_argument("--quiet", action="store_true", help="silencia la salida informativa")
    sp.set_defaults(func=cmd_reindex)
    sub.add_parser("status", help="estado del índice").set_defaults(func=cmd_status)
    sub.add_parser("sources", help="fuentes detectadas").set_defaults(func=cmd_sources)

    sp = sub.add_parser("search", help="buscar")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--semantic", action="store_true")
    sp.add_argument("--by-date", action="store_true")
    sp.add_argument("--since"); sp.add_argument("--until")
    sp.add_argument("--source"); sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_search)

    sub.add_parser("mcp", help="servidor MCP (stdio)").set_defaults(func=cmd_mcp)
    sub.add_parser("mcp-config", help="fragmentos de config MCP").set_defaults(func=cmd_mcp_config)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    log = Log(quiet=args.quiet)
    try:
        return args.func(args, cfg, log)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
