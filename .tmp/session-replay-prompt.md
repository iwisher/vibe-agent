# Session Replay Feature Implementation

## Context
The vibe-agent dashboard needs a session replay feature that shows the full conversation history from session checkpoints stored in SQLite.

## Files to Modify

### 1. vibe/dashboard/server.py (BACKEND - ALREADY DONE)
The `/api/sessions/{session_id}/messages` endpoint has been added. It returns:
```json
[
  {"role": "user", "content": "...", "tool_calls": null, "tool_call_id": null},
  {"role": "assistant", "content": "...", "tool_calls": [...], "tool_call_id": null},
  {"role": "tool", "content": "...", "tool_calls": null, "tool_call_id": "call_123"}
]
```

### 2. vibe/dashboard/static/app.js (FRONTEND)
Add a `SessionReplay` React component and wire it up:

- Add `SessionReplay` component that:
  - Takes `sessionId` and `onBack` props
  - Fetches `/api/sessions/{sessionId}/messages`
  - Displays messages in a chat-like format
  - User messages on the right (styled differently)
  - Assistant messages on the left
  - Tool messages indented with tool_call_id shown
  - Tool calls in assistant messages are collapsible (click to expand)
  - Auto-scrolls to bottom on load

- Modify `SessionList` component:
  - Add `onSessionClick` prop
  - Make each session row clickable
  - Pass the session_id when clicked

- Modify `App` component:
  - Add `selectedSessionId` state
  - Add `view` state value `'session-replay'`
  - Add `handleSessionClick` callback
  - Render `SessionReplay` when view is 'session-replay'

- Add icons to `Icons` object if needed:
  - `ChevronDown`, `ChevronRight` for expand/collapse

### 3. vibe/dashboard/static/style.css (STYLES)
Add CSS for:
- `.session-replay` container
- `.session-replay-header` with back button and title
- `.session-replay-messages` scrollable message list
- `.message` base styles
- `.message-user` (right-aligned, different background)
- `.message-assistant` (left-aligned, different background)
- `.message-tool` (indented, muted)
- `.message-role` badges
- `.message-content` text styling
- `.tool-calls` container
- `.tool-call` individual tool call card
- `.tool-call-header` with name and id
- `.tool-call-args` preformatted JSON
- `.tool-toggle` button styling
- `.tool-result-meta` for tool call_id reference

## Existing Code Patterns
- The dashboard uses React 18 with `React.createElement` (no JSX)
- Icons are simple SVG components in the `Icons` object
- API client is the `api.get()` helper
- Styling uses CSS classes defined in style.css
- Dark theme with colors: #0f172a (bg), #1a2236 (card), #3b82f6 (accent blue), #64748b (muted text), #f0f4f8 (text)

## Testing
Add a test in `tests/dashboard/test_server.py` (create if doesn't exist) that:
- Creates a mock checkpoint with messages_json
- Calls the `/api/sessions/{id}/messages` endpoint
- Verifies the response contains the full messages

## IMPORTANT RULES
1. Match existing code style exactly
2. Use React.createElement, not JSX
3. Follow the existing icon pattern
4. Keep CSS consistent with dark theme
5. Tool calls should be collapsible with a toggle button
6. Auto-scroll to bottom when messages load
