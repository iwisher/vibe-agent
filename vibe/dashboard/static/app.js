const { useState, useEffect, useRef, useCallback } = React;
const { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } = Recharts;

// ─── API Client ───
const API_BASE = '';
const api = {
  get: async (path) => {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return res.json();
  }
};

// ─── Icons (simple SVG components) ───
const Icons = {
  Activity: () => React.createElement('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('polyline', { points: '22 12 18 12 15 21 9 3 6 12 2 12' })),
  BookOpen: () => React.createElement('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z' }),
    React.createElement('path', { d: 'M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z' })),
  Wrench: () => React.createElement('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z' })),
  AlertTriangle: () => React.createElement('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z' }),
    React.createElement('line', { x1: 12, y1: 9, x2: 12, y2: 13 }),
    React.createElement('line', { x1: 12, y1: 17, x2: 12.01, y2: 17 })),
  Clock: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('circle', { cx: 12, cy: 12, r: 10 }),
    React.createElement('polyline', { points: '12 6 12 12 16 14' })),
  MessageSquare: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' })),
  Cpu: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('rect', { x: 4, y: 4, width: 16, height: 16, rx: 2 }),
    React.createElement('rect', { x: 9, y: 9, width: 6, height: 6 }),
    React.createElement('line', { x1: 9, y1: 1, x2: 9, y2: 4 }),
    React.createElement('line', { x1: 15, y1: 1, x2: 15, y2: 4 }),
    React.createElement('line', { x1: 9, y1: 20, x2: 9, y2: 23 }),
    React.createElement('line', { x1: 15, y1: 20, x2: 15, y2: 23 }),
    React.createElement('line', { x1: 20, y1: 9, x2: 23, y2: 9 }),
    React.createElement('line', { x1: 20, y1: 14, x2: 23, y2: 14 }),
    React.createElement('line', { x1: 1, y1: 9, x2: 4, y2: 9 }),
    React.createElement('line', { x1: 1, y1: 14, x2: 4, y2: 14 })),
  Shield: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z' })),
  Wifi: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M5 12.55a11 11 0 0 1 14.08 0' }),
    React.createElement('path', { d: 'M1.42 9a16 16 0 0 1 21.16 0' }),
    React.createElement('path', { d: 'M8.53 16.11a6 6 0 0 1 6.95 0' }),
    React.createElement('line', { x1: 12, y1: 20, x2: 12.01, y2: 20 })),
  RefreshCw: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('polyline', { points: '23 4 23 10 17 10' }),
    React.createElement('polyline', { points: '1 20 1 14 7 14' }),
    React.createElement('path', { d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15' })),
  Zap: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' })),
  Globe: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('circle', { cx: 12, cy: 12, r: 10 }),
    React.createElement('line', { x1: 2, y1: 12, x2: 22, y2: 12 }),
    React.createElement('path', { d: 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z' })),
  Server: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('rect', { x: 2, y: 2, width: 20, height: 8, rx: 2 }),
    React.createElement('rect', { x: 2, y: 14, width: 20, height: 8, rx: 2 }),
    React.createElement('line', { x1: 6, y1: 6, x2: 6.01, y2: 6 }),
    React.createElement('line', { x1: 6, y1: 18, x2: 6.01, y2: 18 })),
  Network: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('circle', { cx: 12, cy: 5, r: 3 }),
    React.createElement('circle', { cx: 19, cy: 12, r: 3 }),
    React.createElement('circle', { cx: 5, cy: 12, r: 3 }),
    React.createElement('line', { x1: 12, y1: 8, x2: 12, y2: 21 }),
    React.createElement('line', { x1: 5, y1: 15, x2: 19, y2: 15 })),
  Lock: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('rect', { x: 3, y: 11, width: 18, height: 11, rx: 2 }),
    React.createElement('path', { d: 'M7 11V7a5 5 0 0 1 10 0v4' })),
  Eye: () => React.createElement('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z' }),
    React.createElement('circle', { cx: 12, cy: 12, r: 3 })),
  ArrowLeft: () => React.createElement('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M19 12H5M12 19l-7-7 7-7' })),
  Tag: () => React.createElement('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
    React.createElement('path', { d: 'M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z' }),
    React.createElement('line', { x1: 7, y1: 7, x2: 7.01, y2: 7 })),
};

// ─── Custom Tooltip for Recharts ───
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return React.createElement('div', { className: 'custom-tooltip' },
    React.createElement('div', { className: 'custom-tooltip-label' }, label),
    payload.map((p, i) =>
      React.createElement('div', { key: i, className: 'custom-tooltip-value', style: { color: p.color } },
        `${p.name}: ${p.value}`
      )
    )
  );
}

// ─── StatCard ───
function StatCard({ title, value, icon, color, delta, onClick }) {
  const colorMap = {
    blue: 'stat-icon blue',
    purple: 'stat-icon purple',
    green: 'stat-icon green',
    red: 'stat-icon red',
  };
  return React.createElement('div', { 
    className: `stat-card ${color === 'red' ? 'error' : ''} ${onClick ? 'clickable' : ''}`,
    onClick: onClick,
    style: onClick ? { cursor: 'pointer' } : undefined
  },
    React.createElement('div', { className: 'stat-header' },
      React.createElement('span', { className: 'stat-label' }, title),
      React.createElement('div', { className: colorMap[color] || 'stat-icon blue' }, icon)
    ),
    React.createElement('div', { className: 'stat-value', style: { color: color === 'red' ? 'var(--accent-red)' : undefined } }, value),
    delta !== undefined && React.createElement('div', { className: `stat-delta ${delta < 0 ? 'negative' : ''}` },
      `${delta >= 0 ? '+' : ''}${delta}% from last 24h`
    )
  );
}

// ─── SessionList ───
function SessionList() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/api/sessions')
      .then(data => { setSessions(data || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return React.createElement('div', { className: 'loading' },
    React.createElement('div', { className: 'loading-spinner' }),
    'Loading sessions...'
  );

  if (!sessions.length) return React.createElement('div', { className: 'empty-state' },
    'No sessions yet. Start a conversation to see it here.'
  );

  return React.createElement('div', { className: 'session-list' },
    sessions.map(s => {
      const isSuccess = s.success === true || s.state === 'COMPLETED';
      return React.createElement('div', { key: s.session_id || s.id, className: 'session-item' },
        React.createElement('div', { className: `session-avatar ${isSuccess ? 'success' : 'error'}` },
          isSuccess ? '✓' : '!'
        ),
        React.createElement('div', { className: 'session-info' },
          React.createElement('div', { className: 'session-id' }, (s.session_id || s.id || 'unknown').slice(0, 12) + '...'),
          React.createElement('div', { className: 'session-meta' },
            React.createElement('span', null, React.createElement(Icons.Cpu), ' ', s.model || 'unknown'),
            React.createElement('span', null, React.createElement(Icons.MessageSquare), ' ', s.message_count || 0, ' msgs'),
            React.createElement('span', null, React.createElement(Icons.Clock), ' ', (s.duration_seconds || 0).toFixed(1), 's')
          )
        ),
        React.createElement('span', { className: `session-badge ${isSuccess ? 'success' : 'error'}` },
          isSuccess ? 'Success' : 'Failed'
        )
      );
    })
  );
}

// ─── WikiPageDetail ───
function WikiPageDetail({ slug, onBack }) {
  const [page, setPage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get(`/api/wiki/${slug}`)
      .then(data => { setPage(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [slug]);

  if (loading) return React.createElement('div', { className: 'loading' },
    React.createElement('div', { className: 'loading-spinner' }),
    'Loading page...'
  );

  if (!page || page.error) return React.createElement('div', { className: 'empty-state' },
    page?.error || 'Page not found.'
  );

  return React.createElement('div', { className: 'wiki-detail' },
    React.createElement('div', { className: 'wiki-detail-header' },
      React.createElement('button', { className: 'back-button', onClick: onBack },
        React.createElement(Icons.ArrowLeft),
        ' Back to Dashboard'
      ),
      React.createElement('h2', { className: 'wiki-detail-title' }, page.title),
      React.createElement('div', { className: 'wiki-detail-meta' },
        page.tags?.length > 0 && React.createElement('div', { className: 'wiki-tags' },
          page.tags.map(tag =>
            React.createElement('span', { key: tag, className: 'wiki-tag' },
              React.createElement(Icons.Tag),
              ' ',
              tag
            )
          )
        ),
        React.createElement('span', { className: `wiki-status wiki-status-${page.verification_status}` }, page.verification_status),
        React.createElement('span', { className: 'wiki-wordcount' }, page.word_count, ' words')
      )
    ),
    React.createElement('div', { className: 'wiki-detail-content' },
      page.content.split('\n').map((line, i) => {
        if (line.startsWith('# ')) {
          return React.createElement('h1', { key: i }, line.slice(2));
        } else if (line.startsWith('## ')) {
          return React.createElement('h2', { key: i }, line.slice(3));
        } else if (line.startsWith('### ')) {
          return React.createElement('h3', { key: i }, line.slice(4));
        } else if (line.startsWith('- ')) {
          return React.createElement('li', { key: i }, line.slice(2));
        } else if (line.trim() === '') {
          return React.createElement('br', { key: i });
        } else {
          return React.createElement('p', { key: i }, line);
        }
      })
    )
  );
}

// ─── WikiGraph (D3.js) ───
function WikiGraph({ onPageClick }) {
  const svgRef = useRef(null);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);

  const loadGraph = useCallback(() => {
    setLoading(true);
    api.get('/api/wiki')
      .then(data => {
        const pages = data || [];
        const nodes = pages.map((p, i) => ({ 
          id: i, 
          label: p.title || p.slug || 'untitled', 
          slug: p.slug,
          x: 0, 
          y: 0 
        }));
        const edges = [];
        for (let i = 0; i < pages.length; i++) {
          for (let j = i + 1; j < pages.length; j++) {
            const shared = (pages[i].tags || []).filter(t => (pages[j].tags || []).includes(t));
            if (shared.length > 0) {
              edges.push({ source: i, target: j });
            }
          }
        }
        setGraph({ nodes, edges });
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    if (!graph.nodes.length || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const width = svgRef.current.clientWidth || 600;
    const height = 380;

    const simulation = d3.forceSimulation(graph.nodes)
      .force("link", d3.forceLink(graph.edges).id(d => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-400))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(30));

    const link = svg.append("g")
      .selectAll("line")
      .data(graph.edges)
      .join("line")
      .attr("stroke", "#3b82f6")
      .attr("stroke-opacity", 0.4)
      .attr("stroke-width", 1.5);

    const node = svg.append("g")
      .selectAll("g")
      .data(graph.nodes)
      .join("g")
      .style("cursor", onPageClick ? "pointer" : "default")
      .on("click", (event, d) => {
        event.stopPropagation();
        if (onPageClick && d.slug) {
          onPageClick(d.slug);
        }
      })
      .call(d3.drag()
        .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
        .on("end", (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

    node.append("circle")
      .attr("r", 22)
      .attr("fill", "#1a2236")
      .attr("stroke", "#3b82f6")
      .attr("stroke-width", 2)
      .attr("class", "graph-node");

    node.append("text")
      .text(d => d.label)
      .attr("x", 0)
      .attr("y", 4)
      .attr("text-anchor", "middle")
      .attr("fill", "#f0f4f8")
      .attr("font-size", "11px")
      .attr("font-family", "'JetBrains Mono', monospace");

    simulation.on("tick", () => {
      link
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }, [graph, onPageClick]);

  if (loading) return React.createElement('div', { className: 'graph-container' },
    React.createElement('div', { className: 'loading' },
      React.createElement('div', { className: 'loading-spinner' }),
      'Loading graph...'
    )
  );

  if (!graph.nodes.length) return React.createElement('div', { className: 'graph-container' },
    React.createElement('div', { className: 'graph-empty' },
      React.createElement('div', { className: 'icon' }, '\uD83D\uDD2E'),
      'No wiki entities yet. Start a conversation to build the knowledge graph.'
    )
  );

  return React.createElement('div', { className: 'graph-container' },
    React.createElement('svg', { ref: svgRef, width: '100%', height: 380 })
  );
}

// ─── TelemetryChart ───
function TelemetryChart() {
  const [data, setData] = useState([]);

  useEffect(() => {
    api.get('/api/telemetry')
      .then(t => {
        const metrics = t.metrics || [];
        const byName = {};
        metrics.forEach(m => {
          if (!byName[m.metric_name]) byName[m.metric_name] = 0;
          byName[m.metric_name]++;
        });
        setData([
          { name: 'Sessions', value: byName['session'] || 0 },
          { name: 'Compactions', value: byName['compaction'] || 0 },
          { name: 'Errors', value: byName['error'] || 0 },
          { name: 'Tool Calls', value: byName['tool_call'] || 0 },
        ]);
      })
      .catch(() => {});
  }, []);

  if (!data.length) return React.createElement('div', { className: 'loading' },
    React.createElement('div', { className: 'loading-spinner' }),
    'Loading telemetry...'
  );

  return React.createElement('div', { className: 'chart-container' },
    React.createElement(ResponsiveContainer, { width: '100%', height: 280 },
      React.createElement(BarChart, { data: data, barSize: 40 },
        React.createElement(CartesianGrid, { strokeDasharray: '3 3', stroke: 'rgba(255,255,255,0.06)', vertical: false }),
        React.createElement(XAxis, { dataKey: 'name', stroke: '#64748b', tick: { fontSize: 12 }, axisLine: false, tickLine: false }),
        React.createElement(YAxis, { stroke: '#64748b', tick: { fontSize: 12 }, axisLine: false, tickLine: false }),
        React.createElement(Tooltip, { content: React.createElement(CustomTooltip) }),
        React.createElement(Bar, { dataKey: 'value', fill: '#3b82f6', radius: [6, 6, 0, 0] })
      )
    )
  );
}

// ─── SystemInfo ───
function SystemInfo() {
  const [config, setConfig] = useState(null);

  useEffect(() => {
    api.get('/api/config').then(setConfig).catch(() => {});
  }, []);

  const rows = [
    { label: 'Dashboard Status', value: 'Online', icon: Icons.Wifi },
    { label: 'Network Binding', value: '127.0.0.1', icon: Icons.Globe },
    { label: 'CORS Policy', value: 'Same-origin only', icon: Icons.Shield },
    { label: 'API Mode', value: 'Read-only', icon: Icons.Lock },
    { label: 'Auto Refresh', value: 'Every 5s (SSE)', icon: Icons.RefreshCw },
    { label: 'Version', value: config?.version || '0.3.5', icon: Icons.Zap },
  ];

  return React.createElement('div', { className: 'info-grid' },
    rows.map((row, i) =>
      React.createElement('div', { key: i, className: 'info-row' },
        React.createElement('span', { className: 'info-label' },
          React.createElement(row.icon),
          ' ',
          row.label
        ),
        React.createElement('span', { className: 'info-value' }, row.value)
      )
    )
  );
}

// ─── Main App ───
function App() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState('dashboard');
  const [selectedWikiSlug, setSelectedWikiSlug] = useState(null);

  useEffect(() => {
    Promise.all([
      api.get('/api/sessions').catch(() => []),
      api.get('/api/wiki').catch(() => []),
      api.get('/api/skills').catch(() => []),
      api.get('/api/telemetry').catch(() => ({ metrics: [] })),
    ])
      .then(([sessions, wiki, skills, telemetry]) => {
        const metrics = telemetry.metrics || [];
        const recentErrors = metrics.filter(m => m.metric_name === 'error').length;
        setStats({
          total_sessions: (sessions || []).length,
          total_wiki_pages: (wiki || []).length,
          total_skills: (skills || []).length,
          recent_errors: recentErrors,
        });
      })
      .catch(e => setError(e.message));
  }, []);

  const handleWikiPageClick = useCallback((slug) => {
    console.log('Wiki page clicked:', slug);
    setSelectedWikiSlug(slug);
    setView('wiki-detail');
  }, []);

  const handleBackToDashboard = useCallback(() => {
    setView('dashboard');
    setSelectedWikiSlug(null);
  }, []);

  if (error) return React.createElement('div', { className: 'error' },
    React.createElement(Icons.AlertTriangle),
    'Error: ', error
  );

  if (!stats) return React.createElement('div', { className: 'loading' },
    React.createElement('div', { className: 'loading-spinner' }),
    'Loading dashboard...'
  );

  // ─── Wiki Detail View ───
  if (view === 'wiki-detail' && selectedWikiSlug) {
    return React.createElement('div', { className: 'dashboard' },
      React.createElement(WikiPageDetail, { 
        slug: selectedWikiSlug, 
        onBack: handleBackToDashboard 
      })
    );
  }

  // ─── Dashboard View ───
  return React.createElement('div', { className: 'dashboard' },
    // Header
    React.createElement('header', { className: 'header' },
      React.createElement('div', { className: 'header-brand' },
        React.createElement('div', { className: 'header-logo' }, '\u25C8'),
        React.createElement('div', null,
          React.createElement('h1', { className: 'header-title' }, 'Vibe Agent Dashboard',
            React.createElement('span', null, 'v0.3.5')
          )
        )
      ),
      React.createElement('div', { className: 'header-meta' },
        React.createElement('span', { className: 'status-badge' }, 'Live'),
        React.createElement('span', { className: 'version-tag' }, 'v0.3.5')
      )
    ),

    // Stats
    React.createElement('div', { className: 'stats-grid' },
      React.createElement(StatCard, { title: 'Total Sessions', value: stats.total_sessions, icon: React.createElement(Icons.Activity), color: 'blue', delta: 12 }),
      React.createElement(StatCard, { 
        title: 'Wiki Pages', 
        value: stats.total_wiki_pages, 
        icon: React.createElement(Icons.BookOpen), 
        color: 'purple', 
        delta: 5,
        onClick: () => {
          console.log('Wiki stat card clicked');
          setView('wiki-list');
        }
      }),
      React.createElement(StatCard, { title: 'Skills Installed', value: stats.total_skills, icon: React.createElement(Icons.Wrench), color: 'green', delta: 0 }),
      React.createElement(StatCard, { title: 'Recent Errors (24h)', value: stats.recent_errors, icon: React.createElement(Icons.AlertTriangle), color: 'red', delta: -8 })
    ),

    // Content
    React.createElement('div', { className: 'content-grid' },
      // Sessions
      React.createElement('div', { className: 'panel' },
        React.createElement('div', { className: 'panel-header' },
          React.createElement('div', { className: 'panel-title' },
            React.createElement('span', { className: 'icon' }, React.createElement(Icons.Activity)),
            'Recent Sessions'
          ),
          React.createElement('span', { className: 'panel-action' }, 'View All')
        ),
        React.createElement('div', { className: 'panel-body' },
          React.createElement(SessionList, null)
        )
      ),

      // Wiki Graph
      React.createElement('div', { className: 'panel' },
        React.createElement('div', { className: 'panel-header' },
          React.createElement('div', { className: 'panel-title' },
            React.createElement('span', { className: 'icon' }, React.createElement(Icons.Network)),
            'Wiki Knowledge Graph'
          ),
          React.createElement('span', { 
            className: 'panel-action', 
            onClick: (e) => {
              e.stopPropagation();
              console.log('Regenerate clicked - calling API');
              fetch('/api/wiki/regenerate', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                  console.log('Regenerate result:', data);
                  alert(`Regenerated! Created ${data.pages_created} new wiki pages.`);
                  window.location.reload();
                })
                .catch(err => {
                  console.error('Regenerate failed:', err);
                  alert('Regeneration failed: ' + err.message);
                });
            }
          }, 'Regenerate')
        ),
        React.createElement('div', { className: 'panel-body' },
          React.createElement(WikiGraph, { onPageClick: handleWikiPageClick })
        )
      ),

      // Telemetry
      React.createElement('div', { className: 'panel' },
        React.createElement('div', { className: 'panel-header' },
          React.createElement('div', { className: 'panel-title' },
            React.createElement('span', { className: 'icon' }, React.createElement(Icons.Server)),
            'Telemetry (24h)'
          )
        ),
        React.createElement('div', { className: 'panel-body' },
          React.createElement(TelemetryChart, null)
        )
      ),

      // System Info
      React.createElement('div', { className: 'panel' },
        React.createElement('div', { className: 'panel-header' },
          React.createElement('div', { className: 'panel-title' },
            React.createElement('span', { className: 'icon' }, React.createElement(Icons.Shield)),
            'System Info'
          )
        ),
        React.createElement('div', { className: 'panel-body' },
          React.createElement(SystemInfo, null)
        )
      )
    )
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(React.createElement(App));
