# Dashboard Code Review (Gemini CLI + Self-Review)

Date: 2026-05-18
Scope: Wiki detail view, regenerate button, API endpoints

---

## Gemini CLI Review Findings

### 1. Security Issues

| Severity | Issue | Location | Fix |
|----------|-------|----------|-----|
| **Critical** | Path Traversal in `get_wiki_page` | `server.py:431` | `slug` is user-controlled, can use `..%2f` to escape `wiki_dir` |
| | | | **Fix**: Validate slug with regex `^[a-zA-Z0-9_-]+$` or use `Path.resolve()` + `os.path.commonpath()` |

**Vulnerable code:**
```python
md_file = state.wiki_dir / f"{slug}.md"  # Can escape with ../
```

**Fix:**
```python
import re
if not re.match(r'^[a-zA-Z0-9_-]+$', slug):
    return {"error": "Invalid slug"}
md_file = (state.wiki_dir / f"{slug}.md").resolve()
if not str(md_file).startswith(str(state.wiki_dir.resolve())):
    return {"error": "Access denied"}
```

### 2. Performance Concerns

| Severity | Issue | Location | Fix |
|----------|-------|----------|-----|
| **High** | Blocking event loop in `regenerate_wiki` | `server.py:468-488` | File I/O in async function blocks all requests |
| | | | **Fix**: Wrap file loop in `asyncio.to_thread()` or use `aiofiles` |
| **Medium** | O(N²) graph edge calculation | `app.js:247-254` | Nested loop comparing all page pairs |
| | | | **Fix**: Build inverted tag index, then edges in O(N) |

**O(N²) code:**
```javascript
for (let i = 0; i < pages.length; i++) {
  for (let j = i + 1; j < pages.length; j++) {
    const shared = (pages[i].tags || []).filter(t => (pages[j].tags || []).includes(t));
    if (shared.length > 0) edges.push({ source: i, target: j });
  }
}
```

**Fix:**
```javascript
const tagToPages = {};
pages.forEach((p, i) => {
  (p.tags || []).forEach(tag => {
    if (!tagToPages[tag]) tagToPages[tag] = [];
    tagToPages[tag].push(i);
  });
});
const edgeSet = new Set();
Object.values(tagToPages).forEach(indices => {
  for (let i = 0; i < indices.length; i++) {
    for (let j = i + 1; j < indices.length; j++) {
      edgeSet.add(`${indices[i]}-${indices[j]}`);
    }
  }
});
```

### 3. Code Quality

| Severity | Issue | Location | Fix |
|----------|-------|----------|-----|
| **High** | Naive markdown parsing | `app.js:209-223` | Custom split by `\n` breaks on code blocks, nested lists, inline formatting |
| | | | **Fix**: Use `react-markdown` or `marked` library |
| **Medium** | Fragile frontmatter parsing | `server.py:441-456` | `content.split("---", 2)` breaks if body contains `---` |
| | | | **Fix**: Use `python-frontmatter` or `pyyaml` |

### 4. Error Handling

| Severity | Issue | Location | Fix |
|----------|-------|----------|-----|
| **Medium** | Unsafe fetch in regenerate | `app.js:540-550` | `fetch().then(r => r.json())` fails on HTTP errors (returns HTML, not JSON) |
| | | | **Fix**: Check `r.ok` before `.json()` |
| **Low** | Missing fallback handling | `server.py:478` | Multiple sessions with missing ID overwrite `session-unknown.md` |

### 5. UI/UX

| Severity | Issue | Location | Fix |
|----------|-------|----------|-----|
| **Medium** | No loading state on regenerate | `app.js:538` | Users can double-click, triggering duplicate requests |
| | | | **Fix**: Add `isRegenerating` state, disable button, show spinner |
| **Low** | Destructive page reload | `app.js:545` | `window.location.reload()` wipes all state |
| | | | **Fix**: Re-fetch wiki data and update React state instead |
| **Low** | Broken browser navigation | `app.js:425-477` | View switching via React state breaks Back/Forward buttons and deep links |
| | | | **Fix**: Use URL hash routing (e.g., `#wiki/my-slug`) |

---

## Self-Review Findings

### Issues I Found Independently

1. **Missing `key` prop in WikiPageDetail content mapping** (`app.js:209-223`)
   - Using array index as key is an anti-pattern for dynamic content
   - **Fix**: Use content hash or line content as key

2. **No error boundary in React app** (`app.js:421-577`)
   - Any component crash takes down entire dashboard
   - **Fix**: Wrap App in ErrorBoundary component

3. **D3 simulation not cleaned up** (`app.js:274-328`)
   - `useEffect` creates new simulation on every graph change but never calls `simulation.stop()`
   - Memory leak + CPU waste
   - **Fix**: Return cleanup function from useEffect

4. **Missing CORS headers on regenerate endpoint** (`server.py:469`)
   - `POST /api/wiki/regenerate` doesn't have `@app.options` or CORS middleware
   - Could fail on cross-origin requests
   - **Fix**: Ensure CORS middleware covers all routes

5. **No rate limiting on regenerate** (`server.py:469`)
   - Expensive endpoint can be spammed
   - **Fix**: Add rate limiter or debounce

6. **WikiPageDetail doesn't handle markdown frontmatter in content** (`app.js:209`)
   - If `body` still contains frontmatter, it renders as plain text
   - **Fix**: Strip frontmatter before rendering

7. **Console.log left in production code** (`app.js:449, 507, 541`)
   - Debug statements should be removed
   - **Fix**: Delete or use proper logging

### What I Did Well

1. **Used `useCallback` for event handlers** — prevents unnecessary re-renders
2. **Added `event.stopPropagation()`** in D3 click handler — prevents event bubbling issues
3. **Proper React state management** — `view` + `selectedWikiSlug` pattern is clean
4. **API endpoint follows REST conventions** — `GET /api/wiki/{slug}`, `POST /api/wiki/regenerate`
5. **Error handling in API** — returns JSON error objects, not exceptions

---

## Action Items

| Priority | Item | Owner |
|----------|------|-------|
| P0 | Fix path traversal vulnerability | Self |
| P0 | Fix blocking file I/O in regenerate | Self |
| P1 | Add `r.ok` check in fetch | Self |
| P1 | Add loading state to regenerate button | Self |
| P1 | Fix D3 simulation cleanup | Self |
| P2 | Replace naive markdown parser with library | Self |
| P2 | Replace naive frontmatter parser with library | Self |
| P2 | Optimize graph edge calculation | Self |
| P2 | Add URL hash routing | Self |
| P3 | Remove console.log statements | Self |
| P3 | Add ErrorBoundary | Self |

---

*Review conducted by: Gemini CLI (gemini-2.5-pro) + Self-review*
*Date: 2026-05-18*
