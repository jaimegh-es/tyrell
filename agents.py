"""
tyrell · plantillas AGENTS.md.

Se escribe un AGENTS.md maestro en la raíz y otro en *cada carpeta* que crea
el sistema, para que cualquier CLI que abra una de esas rutas entienda:
  1) documentar todo SIEMPRE en Markdown dentro de esta carpeta global;
  2) que puede leer las conversaciones de aquí para tener contexto.
"""
from __future__ import annotations

MANAGED_MARK = "<!-- tyrell:managed -->"

ROOT_AGENTS = """{mark}
# AGENTS.md · tyrell (conocimiento centralizado)

> Esta carpeta es el **cerebro compartido** entre todas mis IAs de terminal
> (antigravity, opencode, freebuff, cursor, cline, kilo). Léelo antes de trabajar.

## 1. Regla de oro: documenta SIEMPRE en Markdown

- Todo conocimiento, decisión, hallazgo, comando útil o resumen **debe** quedar
  escrito en un archivo `.md` **dentro de esta carpeta** (`{root}`).
- Escribe la documentación que tú generes en `documentacion/`.
- Nunca dejes información valiosa solo en el chat: si importa, va a un `.md`.

### Convenciones de los `.md`
- Un archivo = un tema. Sé descriptivo en el nombre: `solucion-gnome-freeze.md`.
- Usa **front-matter** para que el índice pueda clasificarlo:
  ```md
  ---
  title: "Solución al cuelgue de GNOME al abrir el cajón de apps"
  tags: ["gnome", "pulsar-os", "bug"]
  created: 2026-10-12T22:30:00
  ---
  ```
- Empieza con un resumen de 2–3 líneas y luego el detalle.
- Si actualizas algo, **edita el `.md` existente** en vez de crear un duplicado.

## 2. Puedes leer conversaciones para tener contexto

- `conversaciones/<cli>/<fecha>/` contiene el volcado diario y automático de las
  conversaciones de cada CLI. **Léelas** cuando necesites recuperar contexto,
  decisiones previas o soluciones ya intentadas.
- No edites a mano lo que hay en `conversaciones/`: es un volcado; tu salida va
  a `documentacion/`.

## 3. Búsqueda semántica (MCP `tyrell`)

Hay un servidor MCP llamado **tyrell** con acceso a una base de datos de todo el
conocimiento (conversaciones + documentación). Herramientas disponibles:

| Herramienta | Para qué |
|---|---|
| `buscar_palabra` | buscar por palabra/frase (índice FTS5/BM25) |
| `buscar_semantico` | buscar por significado (embeddings vía Ollama) |
| `buscar_por_fecha` | listar conocimiento por fecha de creación |
| `leer_documento` | abrir un `.md` completo por `id` o ruta |
| `listar_fuentes` | qué fuentes hay y cuántos documentos |
| `reindexar` | recalcular el índice tras añadir documentación |

Úsalo **antes** de responder preguntas sobre el proyecto: probablemente la
respuesta ya esté documentada aquí.

## 4. Estructura

```
{root}/
├── AGENTS.md            ← este archivo (reglas maestras)
├── README.md
├── conversaciones/      ← volcado automático diario (no editar a mano)
│   └── <cli>/<yyyy-mm-dd>/*.md
├── documentacion/       ← AQUÍ escriben las IAs sus .md
├── indice/              ← índices generados
└── .db/tyrell.db        ← base de datos de búsqueda (SQLite)
```
"""

FOLDER_AGENTS = """{mark}
# AGENTS.md · carpeta `{name}`

Esta carpeta forma parte de **tyrell**, el cerebro centralizado de conocimiento.
Reglas completas en el maestro: [`{root}/AGENTS.md`]({root}/AGENTS.md).

Recordatorios para esta carpeta:
- Documenta en Markdown (`.md`) todo lo relevante. **Nada importante solo en el chat.**
{doc_hint}
- Puedes **leer** `conversaciones/` del proyecto para recuperar contexto.
- Antes de crear un `.md` nuevo, comprueba si ya existe uno relacionado y edítalo.
"""

_DOC_HINTS = {
    "documentacion": "- Escribe aquí tus documentos. Un archivo por tema, con front-matter.",
    "conversaciones": "- Volcado automático de los CLIs. **No lo edites a mano.**",
    "indice": "- Índices generados automáticamente. **No editar a mano.**",
}


def _render(template: str, *, name: str = "", root: str = "", role: str = "") -> str:
    return (template
            .replace("{mark}", MANAGED_MARK)
            .replace("{name}", name)
            .replace("{root}", str(root))
            .replace("{doc_hint}", _DOC_HINTS.get(role, "")))


def root_agents_text(root) -> str:
    return _render(ROOT_AGENTS, root=root)


def folder_agents_text(root, name: str, role: str = "") -> str:
    return _render(FOLDER_AGENTS, name=name, root=root, role=role or name)


README = """# tyrell

Conocimiento centralizado de mis CLIs de IA (antigravity, opencode, freebuff,
cursor, cline, kilo) + documentación propia, todo en Markdown y con búsqueda
(semántica, por palabra y por fecha).

Generado y mantenido por `tools/tyrell.py`. Ver `AGENTS.md` para las reglas.

## Uso rápido

```bash
python3 tools/tyrell.py dump        # volcar conversaciones + reindexar
python3 tools/tyrell.py reindex     # solo reindexar
python3 tools/tyrell.py status      # estado del índice
python3 tools/tyrell.py search "texto" [--semantic] [--source opencode]
python3 tools/tyrell.py mcp         # servidor MCP (lo usan las IAs)
```

Se ejecuta a diario mediante un *timer* de systemd de usuario
(`tyrell-dump.timer`).
"""
