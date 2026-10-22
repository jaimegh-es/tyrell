# Tyrell · Switch between AI's without losing anything  

---

## Why?  

Not all of us have a subscription to Claude Pro Max Ultra Mega or OpenAI Astra Galaxia or similar so we switch between free code agents.  
Personally, my route is the following:  
- Antigravity (students free plan)
- Opencode (big pickle)
- Freebuff
- Kilo Code
- Cursor (free tier)
- Cline (free models)  


But normally we want to have generalized knowledge and dump conversations, right? Well, that's what Tyrell is for, to dump conversations in all these providers and generate a knowledge base that can be navigated by AI as well as continue conversations.  

Below I leave you the detailed explanation that the AI gave me :)) :



---
A system that **every day**, automatically:

1. **Dump ALL conversations** from your CLIs (antigravity, opencode,
   freebuff, cursor, cline and kilo) to `.md` files in `conversations/`,
   organized by `cli/year-month-day/`.
2. **Places an `AGENTS.md`** in the root and **in each folder you create**, so that
   any AI knows that you should **document everything in Markdown** within this
   global folder, and that can **read conversations** for context.
3. **Reindexes the knowledge base** (` .db/tyrell.db`) so that AIs
   can search by **word**, **creation date** and **meaning
   (semantics via Ollama)** through a **MCP server**.

> "Brain" in *Blade Runner*. The information from your AI machines lives here.

---

## Structure

```
~/Documents/tyrell/
├── AGENTS.md ← master rules for all AIs
├── README.md ← this file
├── conversations/ ← daily automatic dump (no editing by hand)
│ └── <cli>/<yyyy-mm-dd>/*.md
├── documentation/ ← HERE AIs write their .md documents
├── index/ ← generated indexes (_index.md, by-date.md)
├── .db/tyrell.db ← SQLite index (FTS5 + vectors)
└── tools/ ← the program (tyrell.py, readers, MCP, installer)
```

## How it works via CLI

| CLI | Warehouse read | Dump quality |
|---|---|---|
| **opencode** | `~/.local/share/opencode/opencode.db` | ✅ complete (text, reasoning, tools) |
| **kilo** | `~/.local/share/kilo/kilo.db` | ✅ complete (same format as opencode) |
| **antigravity** | `~/.gemini/antigravity-cli/` | 🟡 messages are protobuf → extraction *best-effort* |
| **cline** | `~/.cline/` | 🟡 best-effort (generic JSON) |
| **cursor** | `~/.cursor/chats`, `~/.local/share/cursor-agent` | 🟡 best-effort |
| **freebuff** | `~/.codebuff` | 🟡 best-effort |

CLIs that don't already have conversations on your machine simply don't generate
dumped until they have them. When something changes format, **edit them on
corresponding reader in `tools/readers.py`**.

## Use from terminal

```bash
python3 tools/tyrell.py dump # dump conversations + reindex
python3 tools/tyrell.py reindex # reindex only
python3 tools/tyrell.py status # index status
python3 tools/tyrell.py sources # sources and counts
python3 tools/tyrell.py search "text" # by word
python3 tools/tyrell.py search "text" --semantic # by meaning (Ollama)
python3 tools/tyrell.py search "" --by-date --since 2026-10-01
python3 tools/tyrell.py mcp # MCP server (stdio)
```

## Semantic search (Ollama) — optional installation

Without Ollama, searching by **word and date** works the same. For the
**semantics**:

```bash
curl -fsSL https://ollama.com/install.sh | sh # if you don't have Ollama
rocama pull nomic-embed-text
```

Then, `dump`/`reindex` detects Ollama and vectorizes automatically. Model
configurable in `~/.config/tyrell/config.json` (`embed_model`).

## MCP: connect AIs

The server exposes: `search_word`, `search_semantic`, `search_by_date`,
`read_document`, `list_sources` and `reindex`.

```
python3 tools/tyrell.py mcp-config # print the ready fragments for each CLI
```

Example (opencode, in `~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "tyrell": {
      "type": "local",
      "command": ["/usr/bin/python3", "/home/jaime/Documents/tyrell/tools/tyrell.py", "mcp"],
      "enabled": true
    }
  }
}
```

Example (cline/kilo/cursor/freebuff):

```json
{
  "mcpServers": {
    "tyrell": {
      "command": "/usr/bin/python3",
      "args": ["/home/jaime/Documents/tyrell/tools/tyrell.py", "mcp"]
    }
  }
}
```

## Automation (systemd, no cron)

`tools/install.sh` creates a user systemd *timer* that runs the dump
daily (persistent: if the computer was turned off, it runs when turned on):

```bash
~/Documents/tyrell/tools/install.sh
```

```
systemctl --user status tyrell-dump.service # latest dump
systemctl --user list-timers tyrell-dump.timer
```