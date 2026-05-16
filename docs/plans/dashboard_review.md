# Review: React Trace Dashboard (v0.3.4)

## 1. API Design Completeness
- **Current Assessment**: Good baseline for a read-only dashboard.
- **Improvements**:
  - **Live Updates**: Missing an endpoint for real-time trace streaming (e.g., Server-Sent Events (SSE) or WebSockets on `/api/sessions/stream`).
  - **Configuration Visibility**: Add `GET /api/config` to expose current Vibe configuration (active models, environment).
  - **Pagination/Filtering**: Ensure `GET /api/sessions` accepts query parameters (`?limit=50&offset=0&status=error`) natively in the FastAPI route definition.

## 2. Frontend Architecture Feasibility
- **Current Assessment**: The "static files" approach (`app.js`, `style.css`) implies a vanilla JS or simple build output. Mixing React and D3.js can be tricky due to DOM manipulation conflicts.
- **Improvements**:
  - **Bundling**: Explicitly use a modern bundler like **Vite** to compile the React application into the `static/` directory.
  - **React + D3**: Recommend using a wrapper library like **Recharts** or **Nivo** for standard telemetry charts. For the complex `WikiGraph`, encapsulate D3 inside a `useEffect` hook with a dedicated ref to prevent React from interfering with D3's DOM nodes.

## 3. Data Access Patterns
- **Current Assessment**: The plan correctly identifies the target components (`TraceStore`, `LLMWiki`, `TelemetryCollector`, `SkillInstaller`).
- **Improvements**:
  - **Sync/Async Mismatch**: `TraceStore.get_sessions()` and `TelemetryCollector.get_summary()` are synchronous, whereas `LLMWiki.list_pages()` is asynchronous (`async def list_pages`). `DashboardDataSource` and FastAPI endpoints must be designed to properly orchestrate sync and async calls (e.g., using `async def` for FastAPI routes and running sync methods in `run_in_threadpool` if they are blocking).

## 4. Missing Security Considerations
- **Current Assessment**: "No background processes" is good, but exposing a local web server introduces new vectors.
- **Improvements**:
  - **Network Binding**: The server must bind to `127.0.0.1` by default, NOT `0.0.0.0`, to prevent unauthorized access from other devices on the local network.
  - **CORS & CSRF**: Implement strict CORS policies. Since it's a local dashboard, restrict origins to the specific local port. This prevents malicious websites from exfiltrating session data or telemetry via cross-site requests.
  - **API Token (Optional)**: If the dashboard eventually supports write operations, consider auto-generating a temporary bearer token printed to the CLI and passed via URL to authenticate the frontend.

## 5. Testing Coverage
- **Current Assessment**: Backend API and data layer testing are included, which is solid.
- **Improvements**:
  - **Frontend Unit Testing**: Missing UI tests. Add Jest or Vitest for testing React components (`SessionTimeline`, `SkillWaterfall`).
  - **E2E Testing**: Add a basic Playwright or Cypress suite to ensure the static files are served correctly by FastAPI and that D3 graphs render without crashing.
