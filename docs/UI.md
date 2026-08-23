# Vibe Agent — TUI Design & Experience Specification

> **Status:** Active  
> **Theme:** Antigravity / Obsidian Blue with Rich Emoji Iconography  
> **Scope:** `vibe/cli/tui.py`, `vibe/cli/rendering.py`, `vibe/cli/main.py`, `tests/cli/test_tui.py`

---

## 1. Objectives

1. **Crisp Section Boundaries**: Eliminate visual blurring between thinking, working log, and input tiles after long multi-turn sessions. Use distinct background cards and persistent unicode double-line box frames.
2. **Emoji-Rich Visual Hierarchy**: Use expressive, intuitive emojis across headers, status bars, tool calls, logs, and keyboard shortcuts.
3. **Dynamic Keyword & Syntax Highlighting**: Real-time syntax coloring for tool names, status tags, file paths, URLs, metrics, and JSON payloads via `TUIKeywordLexer`.
4. **Parity with Antigravity / Modern Agent TUIs**: TrueColor 24-bit obsidian palette with neon cyan, vibrant purple, and emerald accents.

---

## 2. Layout Architecture

```
┌─ 🌌 ◆ VIBE AGENT ── 🤖 [model: qwen3:8b] ── 🟢 [● READY] ── 📊 [1.2k tokens │ ⚡ 48.2 tok/s] ──────┐
│                                                                                                     │
│  🧠 💭 AGENT THINKING STREAM                                                                        │
│  The user is querying technical architecture details.                                               │
│  Let's inspect the local SQLite store and format the summary.                                       │
│                                                                                                     │
╞══ ⚡ 🛠️ WORKING LOG & TOOL ACTIONS ════════════════════════════════════════════════════════════════╡
│  ❯ 💻 [TOOL:bash] python scripts/fetch_data.py --ticker QQQ                                         │
│    ✨ ✔ SUCCESS in 124ms (exit 0)                                                                   │
│    📄 Output: {"status": "ok", "volume": 48201000, "price": 482.15}                                 │
│                                                                                                     │
│  ❯ 💬 Response: The current price for QQQ is $482.15 with 48.2M volume.                             │
│                                                                                                     │
╞══ ⌨️ 💬 USER PROMPT ── 📬 [queue: 0] ───────────────────────────────────────────────────────────────╡
│  ❯ _                                                                                                │
└─ 🚪 [Ctrl-C] Exit  │  📜 [↑/↓] History  │  🧹 [/clear] Reset  │  🔍 [/verbose]  │  ⚙️ [/bg] ─────────┘
```

---

## 3. Visual Components & Styling

### 3.1 Color Palette (Obsidian Blue)

| Element | Background | Foreground | Accent / Border |
|---|---|---|---|
| **Top System Bar** | `#0b0f19` | `#e2e8f0 bold` | Cyan `#38bdf8` / Emerald `#34d399` |
| **Thinking Pane** | `#0e0d17` | `#e2e8f0` | Electric Violet `#c084fc` |
| **Working Log Pane** | `#080c14` | `#e2e8f0` | Neon Cyan `#38bdf8` |
| **Input Pane** | `#111827` | `#ffffff` | Amber `#fbbf24` / Emerald `#34d399` |
| **Bottom Action Bar**| `#0b0f19` | `#94a3b8` | Subtle Slate `#475569` |

### 3.2 Iconography & Emoji Legend

- **System & Header**:
  - `🌌 ◆ VIBE AGENT`: App Title
  - `🤖`: Model Pill
  - `🟢 [● READY]`: Idle & Listening
  - `⚡ [● BUSY]`: Thinking / Processing
  - `📊`: Token usage & Speed
- **Section Headers**:
  - `🧠 💭 AGENT THINKING STREAM`: Reasoning traces
  - `⚡ 🛠️ WORKING LOG & TOOL ACTIONS`: Tool calls, logs, synthesis
  - `⌨️ 💬 USER PROMPT`: Input field & queue
- **Tool Calls & Logs**:
  - `💻 [TOOL:bash]`: Shell executions
  - `📄 [TOOL:file_read]` / `📝 [TOOL:file_write]`: File operations
  - `🌐 [TOOL:browse]` / `🔗 [TOOL:fetch_url]`: Browser & web
  - `🧩 [SKILL]`: Skill execution
  - `✨ ✔ SUCCESS` / `🟢 DONE`: Successful completion
  - `💥 ✖ ERROR` / `🛑 FAILED`: Errors & exceptions
  - `💡 HINT`: Actionable recommendations
  - `⚠️ WARNING`: Security / caution alerts
  - `🗜️`: Context compaction notes
- **Footer Shortcuts**:
  - `🚪 [Ctrl-C] Exit`
  - `📜 [↑/↓] History`
  - `🧹 [/clear] Reset`
  - `🔍 [/verbose] Details`
  - `⚙️ [/bg] Background`

---

## 4. Technical Implementation

### 4.1 `TUIKeywordLexer` (`vibe/cli/tui.py`)
Custom prompt_toolkit `Lexer` that returns token styling tuples `(style_str, text)` per line:
- Highlighting tool brackets `[TOOL:...]`, `[SKILL]`, `[REASONING]`
- Highlighting status tokens `✔`, `✖`, `SUCCESS`, `ERROR`, `WARNING`, `HINT`
- Highlighting URLs (`https?://\S+`) and file paths (`/[\w.-]+/[\w.-]+`)
- Highlighting metrics (`\d+(?:\.\d+)?\s*(?:ms|s|tok/s|tokens)`) and costs (`\$\d+\.\d+`)

### 4.2 TUI Structure (`VibeTUI`)
- `top_system_bar`: Dynamic FormattedTextControl showing App Title, Model, State, Tokens/Speed.
- `thinking_area`: `TextArea` with `TUIKeywordLexer` and violet header.
- `log_area`: `TextArea` with `TUIKeywordLexer` and cyan/emerald header.
- `input_area`: `TextArea` with prompt `❯ ` and amber/queue header.
- `bottom_action_bar`: FormattedTextControl showing shortcut guide.

---

## 5. Experience Changelog & Versioning

- **v0.5.1**:
  - Modernized TUI with full Obsidian Blue theme.
  - Added emoji-rich top status bar and bottom shortcut guide.
  - Implemented `TUIKeywordLexer` for real-time syntax and keyword colorization.
  - Added persistent unicode box-drawing frames (`╞══ ══╡`) preventing visual section blurring.
