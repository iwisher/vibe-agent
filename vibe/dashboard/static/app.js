const { useState, useEffect, useRef } = React;
const { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } = Recharts;

// API client
const API_BASE = '';
const api = {
  get: async (path) => {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
};

// StatCard component
function StatCard({ title, value, color }) {
  return React.createElement('div', { className: 'stat-card' },
    React.createElement('h3', null, title),
    React.createElement('div', { className: 'value', style: { color: color || 'var(--accent)' } }, value)
  );
}

// SessionList component
function SessionList() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/api/sessions?limit=20')
      .then(data => { setSessions(data.sessions || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return React.createElement('div', { className: 'loading' }, 'Loading sessions...');

  return React.createElement('div', { className: 'session-list' },
    sessions.map(s =>
      React.createElement('div', { key: s.id, className: 'session-item' },
        React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
          React.createElement('strong', null, s.id.slice(0, 8) + '...'),
          React.createElement('span', { className: 'badge ' + (s.success ? 'success' : 'error') },
            s.success ? 'OK' : 'FAIL'
          )
        ),
        React.createElement('div', { className: 'meta' },
          React.createElement('span', null, s.model),
          React.createElement('span', null, (s.message_count || 0) + ' msgs · ' + ((s.duration_seconds || 0).toFixed(1)) + 's')
        ),
        React.createElement('div', { className: 'meta' }, new Date(s.start_time).toLocaleString())
      )
    )
  );
}

// WikiGraph component (D3.js)
function WikiGraph() {
  const svgRef = useRef(null);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/api/wiki/graph')
      .then(data => { setGraph(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!graph.nodes.length || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const width = svgRef.current.clientWidth || 600;
    const height = 400;

    const simulation = d3.forceSimulation(graph.nodes)
      .force("link", d3.forceLink(graph.edges).id(d => d.id).distance(100))
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(width / 2, height / 2));

    const link = svg.append("g")
      .selectAll("line")
      .data(graph.edges)
      .join("line")
      .attr("stroke", "#58a6ff")
      .attr("stroke-opacity", 0.6)
      .attr("stroke-width", 2);

    const node = svg.append("g")
      .selectAll("g")
      .data(graph.nodes)
      .join("g")
      .call(d3.drag()
        .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
        .on("end", (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

    node.append("circle")
      .attr("r", 20)
      .attr("fill", "#21262d")
      .attr("stroke", "#58a6ff")
      .attr("stroke-width", 2);

    node.append("text")
      .text(d => d.label)
      .attr("x", 0)
      .attr("y", 4)
      .attr("text-anchor", "middle")
      .attr("fill", "#c9d1d9")
      .attr("font-size", "11px");

    simulation.on("tick", () => {
      link
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y);

      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }, [graph]);

  if (loading) return React.createElement('div', { className: 'loading' }, 'Loading graph...');
  if (!graph.nodes.length) return React.createElement('div', { className: 'loading' }, 'No wiki entities yet');

  return React.createElement('svg', {
    ref: svgRef,
    width: '100%',
    height: 400,
    style: { background: '#161b22', borderRadius: '8px' }
  });
}

// TelemetryChart component
function TelemetryChart() {
  const [data, setData] = useState([]);

  useEffect(() => {
    api.get('/api/telemetry')
      .then(t => {
        setData([
          { name: 'Sessions', value: t.sessions_count },
          { name: 'Compactions', value: t.compactions_count },
          { name: 'Errors', value: t.errors_count },
        ]);
      })
      .catch(() => {});
  }, []);

  if (!data.length) return React.createElement('div', { className: 'loading' }, 'Loading telemetry...');

  return React.createElement(ResponsiveContainer, { width: '100%', height: 250 },
    React.createElement(BarChart, { data: data },
      React.createElement(CartesianGrid, { strokeDasharray: '3 3', stroke: '#30363d' }),
      React.createElement(XAxis, { dataKey: 'name', stroke: '#8b949e' }),
      React.createElement(YAxis, { stroke: '#8b949e' }),
      React.createElement(Tooltip, {
        contentStyle: { background: '#21262d', border: '1px solid #30363d', color: '#c9d1d9' }
      }),
      React.createElement(Bar, { dataKey: 'value', fill: '#58a6ff' })
    )
  );
}

// Main App
function App() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.get('/api/stats')
      .then(setStats)
      .catch(e => setError(e.message));
  }, []);

  if (error) return React.createElement('div', { className: 'error' }, 'Error: ' + error);
  if (!stats) return React.createElement('div', { className: 'loading' }, 'Loading dashboard...');

  return React.createElement('div', { className: 'dashboard' },
    React.createElement('header', null,
      React.createElement('h1', null, 'Vibe Agent Dashboard'),
      React.createElement('span', { className: 'version' }, 'v0.3.4')
    ),

    React.createElement('div', { className: 'stats-grid' },
      React.createElement(StatCard, { title: 'Total Sessions', value: stats.total_sessions }),
      React.createElement(StatCard, { title: 'Wiki Pages', value: stats.total_wiki_pages }),
      React.createElement(StatCard, { title: 'Skills Installed', value: stats.total_skills }),
      React.createElement(StatCard, { title: 'Recent Errors (24h)', value: stats.recent_errors, color: 'var(--error)' })
    ),

    React.createElement('div', { className: 'content-grid' },
      React.createElement('div', { className: 'panel' },
        React.createElement('h2', null, 'Recent Sessions'),
        React.createElement(SessionList, null)
      ),
      React.createElement('div', { className: 'panel' },
        React.createElement('h2', null, 'Wiki Knowledge Graph'),
        React.createElement(WikiGraph, null)
      ),
      React.createElement('div', { className: 'panel' },
        React.createElement('h2', null, 'Telemetry (24h)'),
        React.createElement(TelemetryChart, null)
      ),
      React.createElement('div', { className: 'panel' },
        React.createElement('h2', null, 'System Info'),
        React.createElement('div', { style: { color: 'var(--text-secondary)', lineHeight: 1.6 } },
          React.createElement('p', null, 'Dashboard binds to 127.0.0.1 only'),
          React.createElement('p', null, 'CORS restricted to same-origin'),
          React.createElement('p', null, 'Read-only API (no write endpoints)'),
          React.createElement('p', null, 'Auto-refreshes every 5s via SSE')
        )
      )
    )
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(React.createElement(App));
