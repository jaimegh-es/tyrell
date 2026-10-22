<!-- tyrell:managed -->
# AGENTS.md · tyrell (conocimiento centralizado)

> Esta carpeta es el **cerebro compartido** entre todas mis IAs de terminal
> (antigravity, opencode, freebuff, cursor, cline, kilo). Léelo antes de trabajar.

## 1. Regla de oro: documenta SIEMPRE en Markdown

- Todo conocimiento, decisión, hallazgo, comando útil o resumen **debe** quedar
  escrito en un archivo `.md` **dentro de esta carpeta** (`/home/(user)/Documentos/tyrell`).
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
/home/jaime/Documentos/tyrell/
├── AGENTS.md            ← este archivo (reglas maestras)
├── README.md
├── conversaciones/      ← volcado automático diario (no editar a mano)
│   └── <cli>/<yyyy-mm-dd>/*.md
├── documentacion/       ← AQUÍ escriben las IAs sus .md
├── indice/              ← índices generados
└── .db/tyrell.db        ← base de datos de búsqueda (SQLite)
```
