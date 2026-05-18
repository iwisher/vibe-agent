# Dashboard Code Review V2 (Post-Fix)

Date: 2026-05-18
Reviewer: Gemini CLI (gemini-2.5-pro)
Scope: All dashboard changes after first round of fixes

---

## Summary

The first round of fixes addressed the most critical issues (path traversal, blocking I/O, unsafe fetch, D3 memory leak). However, a second review reveals **new High-severity issues** that were introduced or missed in the first pass.

---

## Security

### [High] CSRF on File Generation Endpoint

| | |
|:---|:---|
| **Location** | `server.py:482`, `app.js:544` |
| **Issue** | `POST /api/wiki/regenerate` performs state-changing file operations with no CSRF protection |
| **Impact** | Malicious website can force user's browser to hit `localhost:8080/api/wiki/regenerate`, causing DoS via disk spam |
| **Fix** | Require custom header `X-Requested-With: XMLHttpRequest` on backend; send it from frontend |

**Backend:**
```python
# In auth_middleware or endpoint
token = request.headers.get("x-requested-with", "")
if token != "XMLHttpRequest":
    return JSONResponse({"error": "CSRF protection"}, status_code=403)
```

**Frontend:**
```javascript
fetch('/api/wiki/regenerate', { 
  method: 'POST',
  headers: { 'X-Requested-With': 'XMLHttpRequest' }
})
```

### [Medium] Path Traversal via Session ID

| | |
|:---|:---|
| **Location** | `server.py:506-507` |
| **Issue** | `slug = f"session-{session_id[:8]}"` uses database content without sanitization |
| **Impact** | If session_id contains `../`, writes files outside wiki_dir or overwrites critical files |
| **Fix** | Sanitize before constructing path |

```python
safe_session_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(session_id))[:8]
slug = f"session-{safe_session_id}"
```

---

## Performance

### [High] O(N²) Graph Edge Calculation

| | |
|:---|:---|
| **Location** | `app.js:242-247` (WikiGraph) |
| **Issue** | Nested loop compares every page pair for shared tags |
| **Impact** | With 5,000 pages, ~12.5M array intersections on main thread → browser freeze |
| **Fix** | Use inverted index for O(N*T) |

```javascript
const tagMap = {};
pages.forEach((p, i) => {
  (p.tags || []).forEach(t => {
    if (!tagMap[t]) tagMap[t] = [];
    tagMap[t].push(i);
  });
});

const edgeSet = new Set();
Object.values(tagMap).forEach(indices => {
  for (let i = 0; i < indices.length; i++) {
    for (let j = i + 1; j < indices.length; j++) {
      const u = indices[i], v = indices[j];
      const edgeId = u < v ? `${u}-${v}` : `${v}-${u}`;
      if (!edgeSet.has(edgeId)) {
        edgeSet.add(edgeId);
        edges.push({ source: u, target: v });
      }
    }
  }
});
```

### [Medium] Blocking I/O in Async Endpoint (get_wiki_page)

| | |
|:---|:---|
| **Location** | `server.py:445`, `server.py:474` |
| **Issue** | `md_file.read_text()` and `md_file.stat()` are sync blocking calls in `async def` |
| **Impact** | Reading large files freezes entire web server, blocks all requests |
| **Fix** | Use `asyncio.to_thread()` or make endpoint sync |

```python
# Option 1: async with thread pool
content = await asyncio.to_thread(md_file.read_text, encoding="utf-8")
stat = await asyncio.to_thread(md_file.stat)

# Option 2: sync endpoint (FastAPI handles thread pool)
def get_wiki_page(slug: str, request: Request) -> dict[str, Any]:
```

---

## Error Handling

### [High] Server Crash on Missing/Invalid Session ID

| | |
|:---|:---|
| **Location** | `server.py:496-506` |
| **Issue** | `session_id[:8]` crashes if session_id is None or int |
| **Impact** | TypeError crashes background thread → 500 error, generation stops |
| **Fix** | Safe cast and check before slicing |

```python
raw_id = session.get("session_id")
if not raw_id:
    continue
session_id = str(raw_id)
slug = f"session-{session_id[:8]}"
```

### [Medium] Missing Encoding Error Handling

| | |
|:---|:---|
| **Location** | `server.py:445` |
| **Issue** | `read_text(encoding="utf-8")` throws UnicodeDecodeError on binary files |
| **Impact** | 500 error instead of graceful failure |
| **Fix** | Wrap in try/except |

```python
try:
    content = md_file.read_text(encoding="utf-8")
except UnicodeDecodeError:
    return {"error": "File is not valid UTF-8 text"}
```

---

## Code Quality

### [Medium] React State Updates on Unmounted Components

| | |
|:---|:---|
| **Location** | `app.js:176` (WikiPageDetail), `app.js:258` (WikiGraph) |
| **Issue** | `api.get()` resolves after component unmount → state update on unmounted component |
| **Impact** | Memory leak + React console warnings |
| **Fix** | Use cleanup flag or AbortController |

```javascript
useEffect(() => {
  let active = true;
  api.get(`/api/wiki/${slug}`).then(data => {
    if (active) { setPage(data); setLoading(false); }
  }).catch(() => { if (active) setLoading(false); });
  return () => { active = false; };
}, [slug]);
```

### [Low] TOCTOU Race Condition

| | |
|:---|:---|
| **Location** | `server.py:508-509` |
| **Issue** | `if not md_file.exists(): md_file.write_text(...)` is not atomic |
| **Impact** | Concurrent regenerate calls → file corruption |
| **Fix** | Use exclusive creation flag `x` |

```python
try:
    with open(md_file, "x", encoding="utf-8") as f:
        f.write(content)
    pages_created += 1
except FileExistsError:
    pass
```

---

## Action Items

| Priority | Item | File |
|----------|------|------|
| P0 | Add CSRF protection to regenerate endpoint | server.py, app.js |
| P0 | Fix session_id TypeError crash | server.py |
| P1 | Fix O(N²) graph algorithm | app.js |
| P1 | Fix blocking I/O in get_wiki_page | server.py |
| P1 | Add unmount cleanup for async state | app.js |
| P2 | Sanitize session_id for path traversal | server.py |
| P2 | Add UnicodeDecodeError handling | server.py |
| P2 | Fix TOCTOU race in file creation | server.py |

---

*Review by: Gemini CLI (gemini-2.5-pro)*
*Date: 2026-05-18*
