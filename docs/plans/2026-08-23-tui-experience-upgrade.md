# TUI Experience & Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the interactive VibeTUI terminal interface with crisp, distinct section boundaries, TrueColor Obsidian Blue theme, rich emoji iconography, and real-time syntax/keyword highlighting.

**Architecture:** Implement a high-performance `TUIKeywordLexer` in `vibe/cli/tui.py` for `prompt_toolkit`, enhance `VibeTUI` layout with a dynamic top system bar and bottom shortcuts bar, and integrate emoji-rich status tags into `vibe/cli/main.py` and `vibe/cli/rendering.py`.

**Tech Stack:** Python 3.11+, prompt_toolkit (Layout, HSplit, Window, FormattedTextControl, Lexer, Style), HTML/ANSI formatting, pytest.

---

### Task 1: Implement `TUIKeywordLexer` and Obsidian Theme Palette

**Files:**
- Modify: `vibe/cli/tui.py`
- Test: `tests/cli/test_tui.py`

- [ ] **Step 1: Write unit tests for `TUIKeywordLexer` and new styling**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement `TUIKeywordLexer` and TrueColor `_TUI_STYLE` in `vibe/cli/tui.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 2: Enhance `VibeTUI` Layout with Top System Bar, Unicode Double-Frames, and Bottom Action Bar

**Files:**
- Modify: `vibe/cli/tui.py`
- Test: `tests/cli/test_tui.py`

- [ ] **Step 1: Write unit tests for top system bar, model/metrics state setters, and double-frame borders**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement top status bar, unicode frame dividers, and bottom shortcut bar in `vibe/cli/tui.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 3: Wire Emoji-Rich Prefixes and Live Metrics into `main.py` and `rendering.py`

**Files:**
- Modify: `vibe/cli/main.py`
- Modify: `vibe/cli/rendering.py`
- Test: `tests/cli/test_tui.py`

- [ ] **Step 1: Update `format_tool_result_text` in `vibe/cli/rendering.py` with emoji prefixes**
- [ ] **Step 2: Update `output_consumer` in `vibe/cli/main.py` to push model and token metrics to `VibeTUI`**
- [ ] **Step 3: Run existing CLI and TUI tests to verify everything passes**

---

### Task 4: Full Test Suite Verification & Documentation

**Files:**
- Modify: `docs/UI.md`
- Test: Full pytest suite

- [ ] **Step 1: Run `pytest -q` across the entire codebase (1,823+ tests)**
- [ ] **Step 2: Verify `ruff check` and `ruff format`**
- [ ] **Step 3: Commit and push changes**
