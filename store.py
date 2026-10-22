"""
tyrell · índice de búsqueda (SQLite + FTS5 + vectores).

Una única base de datos en <root>/.db/tyrell.db con:
  - docs       : metadatos + contenido de cada .md
  - docs_fts   : índice FTS5 (búsqueda por palabra, con ranking BM25)
  - chunks     : trozos de texto para embeddings
  - vectors    : vectores float32 (búsqueda semántica)
  - meta       : estado interno

Búsqueda semántica: numpy si está disponible, si no, Python puro.
"""
from __future__ import annotations

import json
import re
import sqlite3
import struct
from pathlib import Path

from common import read_markdown, root_dir, sha1, now_ms, ms_to_iso, parse_time_any

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

SCHEMA = """
create table if not exists docs (
    id text primary key,
    source text,
    path text,
    title text,
    created integer,
    updated integer,
    content text,
    hash text,
    indexed_at integer
);
create table if not exists chunks (
    id integer primary key autoincrement,
    doc_id text,
    ord integer,
    text text,
    hash text
);
create index if not exists chunks_doc_idx on chunks(doc_id);
create table if not exists vectors (
    chunk_id integer primary key,
    dim integer,
    vec blob
);
create table if not exists meta (
    key text primary key,
    value text
);
"""


def db_path(cfg: dict | None = None) -> Path:
    return root_dir(cfg) / ".db" / "tyrell.db"


def _ensure_schema(con: sqlite3.Connection) -> None:
    """Crea las tablas y garantiza que `docs_fts` sea una FTS5 real.

    (Si un esquema antiguo la dejó como tabla normal —con `unindexed` como tipo,
    sin shadow-tables— la reemplaza y marca que hay que reconstruir el índice.)
    """
    con.executescript(SCHEMA)
    has = con.execute("select count(*) from sqlite_master "
                      "where type='table' and name='docs_fts'").fetchone()[0]
    shadows = con.execute("select count(*) from sqlite_master "
                          "where type='table' and name like 'docs_fts_%'").fetchone()[0]
    if has and shadows == 0:
        con.execute("drop table if exists docs_fts")
        has = 0
    if not has:
        con.execute("create virtual table if not exists docs_fts "
                    "using fts5(doc_id unindexed, title, content)")
        con.execute("insert into meta(key,value) values('fts_rebuild','1') "
                    "on conflict(key) do update set value='1'")
    con.commit()


def connect(cfg: dict | None = None) -> sqlite3.Connection:
    path = db_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    _ensure_schema(con)
    return con


# --------------------------------------------------------------------------- #
# Vectores
# --------------------------------------------------------------------------- #

def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob)//4}f", blob))


def _cosine_matrix(query: list[float], mat):
    if _np is not None:
        q = _np.asarray(query, dtype=_np.float32)
        m = _np.asarray(mat, dtype=_np.float32)
        qn = q / (_np.linalg.norm(q) + 1e-9)
        mn = m / (_np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        return mn @ qn
    # Python puro
    import math
    def norm(v):
        return math.sqrt(sum(x * x for x in v)) or 1e-9
    qn = norm(query)
    return [sum(a * b for a, b in zip(query, row)) / (qn * norm(row)) for row in mat]


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #

def chunk_text(text: str, size: int = 1100, overlap: int = 180) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            brk = text.rfind("\n", start + size // 2, end)
            if brk != -1:
                end = brk
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# --------------------------------------------------------------------------- #
# Indexado
# --------------------------------------------------------------------------- #

def index_all(cfg, log, embed: bool = True, force: bool = False) -> dict:
    root = root_dir(cfg)
    con = connect(cfg)

    # si un esquema anterior dejó FTS sin reconstruir, reindexar todo
    rebuild_flag = con.execute(
        "select value from meta where key='fts_rebuild'"
    ).fetchone()
    if rebuild_flag and rebuild_flag[0] == "1":
        force = True

    # ¿Ollama disponible?
    embed_fn = None
    if embed:
        try:
            import embed as _embed
            if _embed.available(cfg) and _embed.model_present(cfg):
                def embed_fn(texts, _e=_embed):
                    return _e.embed(texts, cfg)
            elif _embed.available(cfg):
                log.warn(f"Ollama activo pero el modelo '{cfg['embed_model']}' no está instalado "
                         f"(ejecuta: ollama pull {cfg['embed_model']})")
        except Exception as exc:  # pragma: no cover
            log.warn(f"embeddings desactivados: {exc}")

    files = [p for p in sorted(root.rglob("*.md"))
             if "tools" not in p.relative_to(root).parts
             and p.name != "AGENTS.md"]
    seen_ids: set[str] = set()
    stats = {"files": len(files), "new": 0, "updated": 0, "skipped": 0,
             "embedded_chunks": 0, "removed": 0}

    for path in files:
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = read_markdown(path)
        content = text  # guardamos el fichero completo (front-matter incluido)
        doc_id = meta.get("id") or f"file:{rel}"
        seen_ids.add(doc_id)
        digest = sha1(text)

        row = con.execute("select hash from docs where id=?", (doc_id,)).fetchone()
        unchanged = row is not None and row["hash"] == digest
        if unchanged and not force:
            # aún así puede faltar vectorizar o faltar su fila FTS
            if not con.execute("select 1 from docs_fts where doc_id=?", (doc_id,)).fetchone():
                con.execute("insert into docs_fts(doc_id,title,content) values(?,?,?)",
                            (doc_id, meta.get("title") or path.stem, content))
            stats["skipped"] += 1
            if embed_fn and _doc_needs_vectors(con, doc_id):
                stats["embedded_chunks"] += _embed_doc(con, cfg, doc_id, content, log)
            continue

        created = parse_time_any(meta.get("created")) or int(path.stat().st_mtime * 1000)
        updated = parse_time_any(meta.get("updated")) or int(path.stat().st_mtime * 1000)
        title = meta.get("title") or path.stem
        source = meta.get("source") or ("manual" if "/" not in rel else rel.split("/")[0])

        con.execute(
            """insert into docs(id,source,path,title,created,updated,content,hash,indexed_at)
               values(?,?,?,?,?,?,?,?,?)
               on conflict(id) do update set
                 source=excluded.source, path=excluded.path, title=excluded.title,
                 created=excluded.created, updated=excluded.updated,
                 content=excluded.content, hash=excluded.hash, indexed_at=excluded.indexed_at""",
            (doc_id, source, rel, title, created, updated, content, digest, now_ms()),
        )
        con.execute("delete from docs_fts where doc_id=?", (doc_id,))
        con.execute("insert into docs_fts(doc_id,title,content) values(?,?,?)",
                    (doc_id, title, content))
        stats["new" if row is None else "updated"] += 1

        # chunks + vectores
        con.execute("delete from vectors where chunk_id in (select id from chunks where doc_id=?)", (doc_id,))
        con.execute("delete from chunks where doc_id=?", (doc_id,))
        chunks = chunk_text(content, cfg.get("chunk_size", 1100), cfg.get("chunk_overlap", 180))
        if chunks:
            vecs = embed_fn(chunks) if embed_fn else None
            if vecs and len(vecs) == len(chunks):
                for i, (ch, vec) in enumerate(zip(chunks, vecs)):
                    cur = con.execute("insert into chunks(doc_id,ord,text,hash) values(?,?,?,?)",
                                      (doc_id, i, ch, sha1(ch)))
                    con.execute("insert into vectors(chunk_id,dim,vec) values(?,?,?)",
                                (cur.lastrowid, len(vec), pack(vec)))
                stats["embedded_chunks"] += len(chunks)
            else:
                for i, ch in enumerate(chunks):
                    con.execute("insert into chunks(doc_id,ord,text,hash) values(?,?,?,?)",
                                (doc_id, i, ch, sha1(ch)))

    # eliminar documentos que ya no existen
    for row in con.execute("select id from docs").fetchall():
        if row["id"] not in seen_ids:
            con.execute("delete from docs where id=?", (row["id"],))
            con.execute("delete from docs_fts where doc_id=?", (row["id"],))
            con.execute("delete from vectors where chunk_id in (select id from chunks where doc_id=?)", (row["id"],))
            con.execute("delete from chunks where doc_id=?", (row["id"],))
            stats["removed"] += 1

    con.execute("insert into meta(key,value) values('last_index',?) "
                "on conflict(key) do update set value=excluded.value", (str(now_ms()),))
    if force:
        con.execute("insert into meta(key,value) values('fts_rebuild','0') "
                    "on conflict(key) do update set value='0'")
    con.commit()
    con.close()
    stats["semantic"] = bool(embed_fn)
    return stats


def _doc_needs_vectors(con, doc_id) -> bool:
    missing = con.execute(
        "select count(*) from chunks where doc_id=? and id not in (select chunk_id from vectors)",
        (doc_id,),
    ).fetchone()[0]
    return missing > 0


def _embed_doc(con, cfg, doc_id, content, log) -> int:
    import embed as _embed
    chunks = [r["text"] for r in con.execute("select text from chunks where doc_id=? order by ord", (doc_id,))]
    if not chunks:
        return 0
    vecs = _embed.embed(chunks, cfg)
    if not vecs or len(vecs) != len(chunks):
        return 0
    ids = [r["id"] for r in con.execute("select id from chunks where doc_id=? order by ord", (doc_id,))]
    for cid, vec in zip(ids, vecs):
        con.execute("insert or replace into vectors(chunk_id,dim,vec) values(?,?,?)",
                    (cid, len(vec), pack(vec)))
    con.commit()
    return len(chunks)


# --------------------------------------------------------------------------- #
# Búsquedas
# --------------------------------------------------------------------------- #

def _fts_query(query: str) -> str:
    tokens = re.findall(r"\w+", query or "")
    return " OR ".join(f'"{t}"' for t in tokens)


def search_keyword(cfg, query: str, limit: int = 20, source: str | None = None) -> list[dict]:
    con = connect(cfg)
    match = _fts_query(query)
    if not match:
        con.close()
        return []
    sql = ("""select docs_fts.doc_id, docs.title, bm25(docs_fts) as score,
                     snippet(docs_fts, 2, '«', '»', ' … ', 12) as excerpt,
                     docs.source, docs.path, docs.created, docs.updated
              from docs_fts join docs on docs.id = docs_fts.doc_id
              where docs_fts match ?"""
           + (" and docs.source = ?" if source else "")
           + " order by score limit ?")
    args = [match] + ([source] if source else []) + [limit]
    rows = con.execute(sql, args).fetchall()
    con.close()
    return [dict(r) for r in rows]


def search_semantic(cfg, query: str, limit: int = 10, source: str | None = None) -> tuple[list[dict], str | None]:
    import embed as _embed
    if not _embed.available(cfg) or not _embed.model_present(cfg):
        return [], "semantic_unavailable"
    vecs = _embed.embed([query], cfg)
    if not vecs:
        return [], "semantic_unavailable"
    q = vecs[0]
    con = connect(cfg)
    sql = """select c.id as chunk_id, c.doc_id, c.text, v.vec, d.title, d.source, d.path, d.created
             from vectors v join chunks c on c.id = v.chunk_id join docs d on d.id = c.doc_id"""
    args = []
    if source:
        sql += " where d.source = ?"
        args.append(source)
    rows = con.execute(sql, args).fetchall()
    con.close()
    if not rows:
        return [], None
    mat, meta = [], []
    for r in rows:
        vec = unpack(r["vec"])
        if len(vec) != len(q):
            continue
        mat.append(vec)
        meta.append(r)
    if not mat:
        return [], None
    scores = _cosine_matrix(q, mat)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:limit]
    out = []
    for i in order:
        r = meta[i]
        out.append({"doc_id": r["doc_id"], "chunk_id": r["chunk_id"], "title": r["title"],
                    "source": r["source"], "path": r["path"], "created": r["created"],
                    "score": float(scores[i]), "excerpt": (r["text"] or "")[:400]})
    return out, None


def search_date(cfg, since_ms: int | None = None, until_ms: int | None = None,
                source: str | None = None, limit: int = 200) -> list[dict]:
    con = connect(cfg)
    sql = "select id, source, path, title, created, updated from docs where 1=1"
    args = []
    if since_ms:
        sql += " and created >= ?"
        args.append(since_ms)
    if until_ms:
        sql += " and created <= ?"
        args.append(until_ms)
    if source:
        sql += " and source = ?"
        args.append(source)
    sql += " order by created desc limit ?"
    args.append(limit)
    rows = con.execute(sql, args).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_doc(cfg, doc_id: str) -> dict | None:
    con = connect(cfg)
    row = con.execute("select * from docs where id=?", (doc_id,)).fetchone()
    if row is None:
        row = con.execute("select * from docs where path=?", (doc_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def list_sources(cfg) -> list[dict]:
    con = connect(cfg)
    rows = con.execute(
        "select source, count(*) as docs, min(created) as first, max(created) as last "
        "from docs group by source order by docs desc"
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["first_iso"] = ms_to_iso(d["first"])
        d["last_iso"] = ms_to_iso(d["last"])
        out.append(d)
    return out


def stats(cfg) -> dict:
    con = connect(cfg)
    total = con.execute("select count(*) from docs").fetchone()[0]
    chunks = con.execute("select count(*) from chunks").fetchone()[0]
    vectors = con.execute("select count(*) from vectors").fetchone()[0]
    last = con.execute("select value from meta where key='last_index'").fetchone()
    con.close()
    return {"docs": total, "chunks": chunks, "vectors": vectors,
            "last_index": ms_to_iso(parse_time_any(last[0])) if last else None,
            "semantic": vectors > 0}
