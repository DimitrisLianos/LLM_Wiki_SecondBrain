import { LitElement, html, css } from "lit";
import { wiki } from "../lib/api.js";

/**
 * force-directed graph visualiser with obsidian-like controls.
 *
 * features:
 * - drag nodes to reposition and pin them in place
 * - double-click a pinned node to unpin, or double-click empty space to fit view
 * - scroll wheel to zoom, drag background to pan
 * - settings panel: force parameters, label threshold, node size
 * - zoom controls: +, -, fit-to-view
 * - search/filter nodes by name
 */

const SUBDIR_COLORS = {
  sources:   "oklch(70% 0.12 250)",
  entities:  "oklch(70% 0.14 165)",
  concepts:  "oklch(72% 0.12 300)",
  synthesis: "oklch(75% 0.14 70)",
};

/* distinct palette for user-defined groups (up to 16). */
const GROUP_PALETTE = [
  "oklch(65% 0.20 25)",   /* red */
  "oklch(75% 0.16 70)",   /* amber */
  "oklch(72% 0.18 130)",  /* lime */
  "oklch(70% 0.14 165)",  /* green */
  "oklch(70% 0.14 195)",  /* teal */
  "oklch(70% 0.12 250)",  /* blue */
  "oklch(65% 0.16 280)",  /* indigo */
  "oklch(68% 0.18 310)",  /* purple */
  "oklch(70% 0.16 340)",  /* pink */
  "oklch(75% 0.14 55)",   /* orange */
  "oklch(72% 0.12 150)",  /* emerald */
  "oklch(68% 0.14 230)",  /* sky */
  "oklch(62% 0.20 300)",  /* violet */
  "oklch(72% 0.16 80)",   /* yellow */
  "oklch(68% 0.10 210)",  /* cyan */
  "oklch(65% 0.15 350)",  /* rose */
];

const SUBDIR_LABELS = {
  sources:   "Sources",
  entities:  "Entities",
  concepts:  "Concepts",
  synthesis: "Synthesis",
};

function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

function nodeRadius(linkCount) {
  const MIN_R = 4;
  const MAX_R = 20;
  return MIN_R + Math.min(linkCount / 30, 1) * (MAX_R - MIN_R);
}

export class GraphView extends LitElement {
  static properties = {
    _loading:        { state: true },
    _error:          { state: true },
    _searchText:     { state: true },
    _nodeCount:      { state: true },
    _edgeCount:      { state: true },
    _settingsOpen:   { state: true },
    _repulsion:      { state: true },
    _springRest:     { state: true },
    _gravity:        { state: true },
    _damping:        { state: true },
    _labelThreshold: { state: true },
    _nodeScale:      { state: true },
    _colorMode:      { state: true },
    _groups:         { state: true },
  };

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      width: 100%;
      height: 100%;
      min-height: 0;
      gap: var(--sp-4);
    }

    /* --- toolbar. --- */
    .toolbar {
      display: flex;
      align-items: center;
      gap: var(--sp-4);
      flex-wrap: wrap;
    }
    h1 {
      font-family: var(--font-heading);
      font-size: var(--text-3xl);
      color: var(--text-primary);
      margin: 0;
      flex-shrink: 0;
    }
    .search-box {
      flex: 1;
      min-width: 200px;
      max-width: 360px;
      padding: var(--sp-2) var(--sp-4);
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      color: var(--text-primary);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      outline: none;
      transition: border-color var(--duration-fast) var(--ease-out);
    }
    .search-box::placeholder { color: var(--text-muted); }
    .search-box:focus { border-color: var(--accent); }
    .stats {
      font-family: var(--font-mono);
      font-size: var(--text-xs);
      color: var(--text-muted);
      white-space: nowrap;
    }

    /* --- legend. --- */
    .legend {
      display: flex;
      gap: var(--sp-5);
      flex-wrap: wrap;
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-xs);
      color: var(--text-secondary);
    }
    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    /* --- canvas. --- */
    .canvas-wrap {
      flex: 1;
      min-height: 0;
      position: relative;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
    }
    canvas {
      display: block;
      width: 100%;
      height: 100%;
      cursor: grab;
    }
    canvas.dragging { cursor: grabbing; }

    /* --- tooltip. --- */
    .tooltip {
      position: absolute;
      pointer-events: none;
      padding: var(--sp-2) var(--sp-3);
      background: var(--bg-card);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-sm);
      color: var(--text-primary);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      box-shadow: var(--shadow-md);
      white-space: nowrap;
      opacity: 0;
      transition: opacity var(--duration-fast) var(--ease-out);
      z-index: 10;
    }
    .tooltip.visible { opacity: 1; }
    .tooltip-subdir {
      font-size: var(--text-xs);
      color: var(--text-muted);
      margin-top: 2px;
    }

    /* --- settings toggle. --- */
    .settings-toggle {
      position: absolute;
      top: var(--sp-3);
      right: var(--sp-3);
      width: 32px;
      height: 32px;
      border-radius: var(--radius-sm);
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 5;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .settings-toggle:hover {
      background: var(--bg-card-hover);
      color: var(--text-primary);
    }
    .settings-toggle.active {
      background: var(--accent);
      color: var(--bg-deep);
      border-color: var(--accent);
    }

    /* --- settings panel. --- */
    .settings-panel {
      position: absolute;
      top: 0;
      right: 0;
      width: 240px;
      height: 100%;
      background: oklch(15% 0.015 260 / 0.95);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-left: 1px solid var(--border);
      padding: var(--sp-5);
      z-index: 4;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: var(--sp-5);
    }
    .settings-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-bottom: var(--sp-3);
      border-bottom: 1px solid var(--border);
    }
    .settings-header span {
      font-size: var(--text-sm);
      font-weight: 600;
      color: var(--text-primary);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .close-btn {
      width: 24px;
      height: 24px;
      border: none;
      background: none;
      color: var(--text-muted);
      font-size: 18px;
      cursor: pointer;
      border-radius: var(--radius-sm);
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .close-btn:hover {
      background: var(--bg-card);
      color: var(--text-primary);
    }
    .settings-group {
      display: flex;
      flex-direction: column;
      gap: var(--sp-3);
    }
    .settings-group-title {
      font-size: var(--text-xs);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
    }
    .settings-row {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .settings-label {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: var(--text-xs);
      color: var(--text-secondary);
    }
    .settings-value {
      font-variant-numeric: tabular-nums;
      color: var(--accent-dim);
      font-family: var(--font-mono);
      font-size: var(--text-xs);
    }
    input[type="range"] {
      -webkit-appearance: none;
      width: 100%;
      height: 4px;
      background: var(--border);
      border-radius: 2px;
      outline: none;
      margin: var(--sp-1) 0;
    }
    input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: var(--accent);
      cursor: pointer;
      border: 2px solid var(--bg-deep);
    }
    .settings-actions {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
      padding-top: var(--sp-3);
      border-top: 1px solid var(--border);
    }
    .settings-btn {
      padding: var(--sp-2) var(--sp-3);
      font-size: var(--text-xs);
      font-weight: 500;
      font-family: var(--font-body);
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-secondary);
      cursor: pointer;
      text-align: center;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .settings-btn:hover {
      background: var(--bg-card-hover);
      color: var(--text-primary);
    }

    /* --- color mode & groups. --- */
    .color-mode-row {
      display: flex;
      gap: 2px;
      margin-bottom: var(--sp-2);
    }
    .color-mode-btn {
      flex: 1;
      padding: var(--sp-1) var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 500;
      font-family: var(--font-body);
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-muted);
      cursor: pointer;
      text-align: center;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .color-mode-btn.active {
      background: var(--accent);
      color: var(--bg-deep);
      border-color: var(--accent);
      font-weight: 600;
    }
    .color-mode-btn:hover:not(.active) {
      background: var(--bg-card-hover);
      color: var(--text-primary);
    }
    .groups-list {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
      margin-top: var(--sp-2);
    }
    .group-row {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
    }
    .group-input {
      flex: 1;
      padding: var(--sp-1) var(--sp-2);
      font-size: var(--text-xs);
      font-family: var(--font-body);
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-primary);
      outline: none;
    }
    .group-input:focus { border-color: var(--accent); }
    .group-dot {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .group-remove {
      width: 20px;
      height: 20px;
      padding: 0;
      background: none;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-muted);
      cursor: pointer;
      font-size: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .group-remove:hover { color: var(--error); border-color: var(--error); }
    .new-group-btn {
      background: oklch(60% 0.12 280 / 0.2);
      border-color: oklch(60% 0.12 280 / 0.4);
      color: oklch(78% 0.10 280);
    }
    .new-group-btn:hover {
      background: oklch(60% 0.12 280 / 0.35);
    }

    /* --- zoom controls. --- */
    .zoom-controls {
      position: absolute;
      bottom: var(--sp-4);
      right: var(--sp-4);
      display: flex;
      flex-direction: column;
      gap: 1px;
      z-index: 5;
    }
    .zoom-btn {
      width: 32px;
      height: 32px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .zoom-btn:first-child { border-radius: var(--radius-sm) var(--radius-sm) 0 0; }
    .zoom-btn:last-child { border-radius: 0 0 var(--radius-sm) var(--radius-sm); }
    .zoom-btn:hover {
      background: var(--bg-card-hover);
      color: var(--text-primary);
    }

    /* --- states. --- */
    .loading, .error-msg {
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
      font-family: var(--font-body);
      font-size: var(--text-lg);
    }
    .loading { color: var(--text-muted); }
    .error-msg { color: var(--error); }
  `;

  constructor() {
    super();
    this._loading = true;
    this._error = "";
    this._searchText = "";
    this._nodeCount = 0;
    this._edgeCount = 0;

    /* settings. */
    this._settingsOpen = false;
    this._repulsion = 2250;
    this._springRest = 65;
    this._gravity = 0.002;
    this._damping = 0.52;
    this._labelThreshold = 2.7;
    this._nodeScale = 1.7;

    /* coloring: "none" = uniform grey, "type" = subdir colors, "groups" = user-defined. */
    this._colorMode = "none";
    /* user-defined groups: [{ name: string, color: string }]. */
    this._groups = this._loadGroups();

    /* simulation arrays. */
    /** @type {{ x: number, y: number, vx: number, vy: number, radius: number, node: any }[]} */
    this._simNodes = [];
    /** @type {{ si: number, ti: number }[]} */
    this._simEdges = [];
    /** @type {Map<string, number>} */
    this._nameIndex = new Map();
    /** @type {Map<number, Set<number>>} */
    this._adjacency = new Map();

    this._animFrame = 0;
    this._iterCount = 0;
    this._settled = false;

    /* camera. */
    this._camX = 0;
    this._camY = 0;
    this._camZoom = 1;

    /* interaction. */
    this._dragging = false;
    this._potentialNodeDrag = -1;
    this._isDraggingNode = false;
    this._dragNodeIdx = -1;
    this._didDrag = false;
    this._mouseDownPos = null;
    this._dragStartX = 0;
    this._dragStartY = 0;
    this._camStartX = 0;
    this._camStartY = 0;
    this._hoveredIndex = -1;
    /** @type {Set<number>} */
    this._pinnedNodes = new Set();

    /* bound handlers. */
    this._onResize = this._handleResize.bind(this);
    this._onMouseDown = this._handleMouseDown.bind(this);
    this._onMouseMove = this._handleMouseMove.bind(this);
    this._onMouseUp = this._handleMouseUp.bind(this);
    this._onWheel = this._handleWheel.bind(this);
    this._onClick = this._handleClick.bind(this);
    this._onDblClick = this._handleDblClick.bind(this);
    this._clickTimer = 0;
  }

  /* ------------------------------------------------------------------ */
  /*  lifecycle.                                                         */
  /* ------------------------------------------------------------------ */

  connectedCallback() {
    super.connectedCallback();
    this._loadGraph();
    window.addEventListener("resize", this._onResize);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    cancelAnimationFrame(this._animFrame);
    window.removeEventListener("resize", this._onResize);
    this._detachCanvasListeners();
  }

  async _loadGraph() {
    this._loading = true;
    this._error = "";
    try {
      const data = await wiki.graph();
      this._buildSimulation(data.nodes, data.edges);
      this._loading = false;
      await this.updateComplete;
      this._initCanvas();
    } catch (err) {
      this._error = err.message || "Failed to load graph data.";
      this._loading = false;
    }
  }

  /* ------------------------------------------------------------------ */
  /*  simulation setup.                                                  */
  /* ------------------------------------------------------------------ */

  _buildSimulation(nodes, edges) {
    this._nameIndex.clear();
    this._adjacency.clear();
    this._pinnedNodes.clear();

    const spread = Math.sqrt(nodes.length) * 40;

    this._simNodes = nodes.map((node, i) => {
      this._nameIndex.set(node.name, i);
      this._adjacency.set(i, new Set());
      const angle = (i / nodes.length) * Math.PI * 2;
      const r = spread * (0.3 + Math.random() * 0.7);
      return {
        x: Math.cos(angle) * r + (Math.random() - 0.5) * 60,
        y: Math.sin(angle) * r + (Math.random() - 0.5) * 60,
        vx: 0,
        vy: 0,
        radius: nodeRadius(node.link_count),
        node,
      };
    });

    this._simEdges = [];
    for (const edge of edges) {
      const si = this._nameIndex.get(edge.source);
      const ti = this._nameIndex.get(edge.target);
      if (si !== undefined && ti !== undefined && si !== ti) {
        this._simEdges.push({ si, ti });
        this._adjacency.get(si).add(ti);
        this._adjacency.get(ti).add(si);
      }
    }

    this._nodeCount = this._simNodes.length;
    this._edgeCount = this._simEdges.length;
    this._iterCount = 0;
    this._settled = false;
  }

  /* ------------------------------------------------------------------ */
  /*  canvas setup.                                                      */
  /* ------------------------------------------------------------------ */

  _initCanvas() {
    const canvas = this.renderRoot.querySelector("canvas");
    if (!canvas) return;
    this._canvas = canvas;
    this._ctx = canvas.getContext("2d");
    this._sizeCanvas();
    this._centerCamera();
    this._attachCanvasListeners();
    /* cancel any prior animation loop before starting a new one. */
    cancelAnimationFrame(this._animFrame);
    this._tick();
  }

  _sizeCanvas() {
    if (!this._canvas) return;
    const rect = this._canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this._canvas.width = rect.width * dpr;
    this._canvas.height = rect.height * dpr;
    this._dpr = dpr;
    this._viewW = rect.width;
    this._viewH = rect.height;
  }

  _centerCamera() {
    this._camX = 0;
    this._camY = 0;
    this._camZoom = 1;
  }

  _attachCanvasListeners() {
    const c = this._canvas;
    if (!c) return;
    c.addEventListener("mousedown", this._onMouseDown);
    c.addEventListener("mousemove", this._onMouseMove);
    c.addEventListener("mouseup", this._onMouseUp);
    c.addEventListener("mouseleave", this._onMouseUp);
    c.addEventListener("wheel", this._onWheel, { passive: false });
    c.addEventListener("click", this._onClick);
    c.addEventListener("dblclick", this._onDblClick);
  }

  _detachCanvasListeners() {
    const c = this._canvas;
    if (!c) return;
    c.removeEventListener("mousedown", this._onMouseDown);
    c.removeEventListener("mousemove", this._onMouseMove);
    c.removeEventListener("mouseup", this._onMouseUp);
    c.removeEventListener("mouseleave", this._onMouseUp);
    c.removeEventListener("wheel", this._onWheel);
    c.removeEventListener("click", this._onClick);
    c.removeEventListener("dblclick", this._onDblClick);
  }

  _handleResize() {
    if (!this._canvas) return;
    this._sizeCanvas();
    this._draw();
  }

  /* ------------------------------------------------------------------ */
  /*  force simulation.                                                  */
  /* ------------------------------------------------------------------ */

  _tick() {
    if (!this._settled) {
      this._stepSimulation();
      this._iterCount++;
    }
    this._draw();
    this._animFrame = requestAnimationFrame(() => this._tick());
  }

  _stepSimulation() {
    const nodes = this._simNodes;
    const edges = this._simEdges;
    const n = nodes.length;
    if (n === 0) return;

    const REPULSION = this._repulsion;
    const SPRING_K = 0.008;
    const SPRING_REST = this._springRest;
    const GRAVITY = this._gravity;
    const DAMPING = this._damping;
    const MAX_ITERS = 600;
    const VEL_THRESHOLD = 0.15;
    const CUTOFF_SQ = 500 * 500;

    /* repulsion: every pair (with distance cutoff). */
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distSq = dx * dx + dy * dy;
        if (distSq > CUTOFF_SQ) continue;
        if (distSq < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; distSq = 1; }
        const force = REPULSION / distSq;
        const len = Math.sqrt(distSq);
        const fx = (dx / len) * force;
        const fy = (dy / len) * force;
        a.vx -= fx;
        a.vy -= fy;
        b.vx += fx;
        b.vy += fy;
      }
    }

    /* spring attraction along edges. */
    for (const { si, ti } of edges) {
      const a = nodes[si];
      const b = nodes[ti];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const displacement = dist - SPRING_REST;
      const force = SPRING_K * displacement;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }

    /* center gravity + velocity integration. pinned nodes stay fixed. */
    let totalKE = 0;
    for (let i = 0; i < n; i++) {
      if (this._pinnedNodes.has(i)) {
        nodes[i].vx = 0;
        nodes[i].vy = 0;
        continue;
      }
      const nd = nodes[i];
      nd.vx -= nd.x * GRAVITY;
      nd.vy -= nd.y * GRAVITY;
      nd.vx *= DAMPING;
      nd.vy *= DAMPING;
      nd.x += nd.vx;
      nd.y += nd.vy;
      totalKE += nd.vx * nd.vx + nd.vy * nd.vy;
    }

    const unpinned = n - this._pinnedNodes.size;
    const avgKE = unpinned > 0 ? totalKE / unpinned : 0;
    if (avgKE < VEL_THRESHOLD || this._iterCount >= MAX_ITERS) {
      this._settled = true;
    }
  }

  _restartSimulation() {
    this._iterCount = 0;
    this._settled = false;
  }

  /* ------------------------------------------------------------------ */
  /*  coordinate transforms.                                             */
  /* ------------------------------------------------------------------ */

  _worldToScreen(wx, wy) {
    return [
      (wx - this._camX) * this._camZoom + this._viewW * 0.5,
      (wy - this._camY) * this._camZoom + this._viewH * 0.5,
    ];
  }

  _screenToWorld(sx, sy) {
    return [
      (sx - this._viewW * 0.5) / this._camZoom + this._camX,
      (sy - this._viewH * 0.5) / this._camZoom + this._camY,
    ];
  }

  /* ------------------------------------------------------------------ */
  /*  rendering.                                                         */
  /* ------------------------------------------------------------------ */

  _draw() {
    const ctx = this._ctx;
    if (!ctx) return;

    const dpr = this._dpr;
    const w = this._viewW;
    const h = this._viewH;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "oklch(12% 0.02 260)";
    ctx.fillRect(0, 0, w, h);

    const nodes = this._simNodes;
    const edges = this._simEdges;
    const hovered = this._hoveredIndex;
    const search = this._searchText.toLowerCase();
    const hasSearch = search.length > 0;
    const scale = this._nodeScale;
    const showAllLabels = this._camZoom >= this._labelThreshold;

    /* precompute hover neighbors. */
    const hoverNeighbors = hovered >= 0 ? this._adjacency.get(hovered) : null;

    /* precompute search matches. */
    let matchSet = null;
    if (hasSearch) {
      matchSet = new Set();
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].node.name.toLowerCase().includes(search)) matchSet.add(i);
      }
    }

    /* --- edges. --- */
    for (const { si, ti } of edges) {
      const [sx1, sy1] = this._worldToScreen(nodes[si].x, nodes[si].y);
      const [sx2, sy2] = this._worldToScreen(nodes[ti].x, nodes[ti].y);

      if ((sx1 < -50 && sx2 < -50) || (sx1 > w + 50 && sx2 > w + 50) ||
          (sy1 < -50 && sy2 < -50) || (sy1 > h + 50 && sy2 > h + 50)) continue;

      let alpha = 0.15;
      if (hovered >= 0) {
        alpha = (si === hovered || ti === hovered) ? 0.6 : 0.05;
      }
      if (hasSearch && matchSet) {
        alpha = (matchSet.has(si) || matchSet.has(ti)) ? 0.4 : 0.04;
      }

      ctx.beginPath();
      ctx.moveTo(sx1, sy1);
      ctx.lineTo(sx2, sy2);
      ctx.strokeStyle = `oklch(60% 0.01 260 / ${alpha})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    /* --- nodes. --- */
    for (let i = 0; i < nodes.length; i++) {
      const sn = nodes[i];
      const [sx, sy] = this._worldToScreen(sn.x, sn.y);
      const baseR = sn.radius * scale;
      const screenR = baseR * this._camZoom;

      if (sx + screenR < -10 || sx - screenR > w + 10 ||
          sy + screenR < -10 || sy - screenR > h + 10) continue;

      const color = this._nodeColor(sn.node);
      const isHovered = i === hovered;
      const isNeighbor = hoverNeighbors ? hoverNeighbors.has(i) : false;
      const isMatch = matchSet ? matchSet.has(i) : true;
      const isPinned = this._pinnedNodes.has(i);

      let alpha = 1;
      if (hovered >= 0 && !isHovered && !isNeighbor) alpha = 0.15;
      if (hasSearch && !isMatch) alpha = 0.1;

      ctx.beginPath();
      ctx.arc(sx, sy, screenR, 0, Math.PI * 2);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.fill();

      if (isHovered) {
        ctx.strokeStyle = "oklch(92% 0.01 80)";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      /* pin indicator. */
      if (isPinned && screenR > 3) {
        const pinSz = Math.max(2.5, screenR * 0.2);
        ctx.beginPath();
        ctx.arc(sx + screenR * 0.6, sy - screenR * 0.6, pinSz, 0, Math.PI * 2);
        ctx.fillStyle = "oklch(75% 0.14 70)";
        ctx.globalAlpha = alpha;
        ctx.fill();
      }

      ctx.globalAlpha = 1;

      /* labels. */
      const shouldLabel =
        isHovered ||
        (isNeighbor && this._camZoom > 0.6) ||
        (hasSearch && isMatch && this._camZoom > 0.8) ||
        (showAllLabels && screenR > 2);

      if (shouldLabel) {
        const isImportant = isHovered || (hasSearch && isMatch);
        const weight = isImportant ? "600" : "400";
        const size = isImportant ? 13 : 11;
        ctx.font = `${weight} ${size}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        const labelAlpha = isImportant ? 1 : 0.7;
        ctx.fillStyle = `oklch(92% 0.01 80 / ${labelAlpha})`;
        ctx.fillText(sn.node.name, sx, sy - screenR - 4);
      }
    }
  }

  /* ------------------------------------------------------------------ */
  /*  interaction handlers.                                              */
  /* ------------------------------------------------------------------ */

  _canvasCoords(e) {
    const rect = this._canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  _hitTest(sx, sy) {
    const [wx, wy] = this._screenToWorld(sx, sy);
    const nodes = this._simNodes;
    const scale = this._nodeScale;
    let bestDist = Infinity;
    let bestIdx = -1;

    for (let i = 0; i < nodes.length; i++) {
      const sn = nodes[i];
      const dx = sn.x - wx;
      const dy = sn.y - wy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const hitR = Math.max(sn.radius * scale, 8) / this._camZoom;
      if (dist < hitR && dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  _handleMouseDown(e) {
    if (e.button !== 0) return;
    const [sx, sy] = this._canvasCoords(e);
    this._mouseDownPos = [sx, sy];
    this._didDrag = false;

    const hit = this._hitTest(sx, sy);

    if (hit >= 0) {
      /* potential node drag — promote to actual drag on mousemove. */
      this._potentialNodeDrag = hit;
      this._canvas.classList.add("dragging");
      return;
    }

    /* canvas panning. */
    this._dragging = true;
    this._dragStartX = sx;
    this._dragStartY = sy;
    this._camStartX = this._camX;
    this._camStartY = this._camY;
    this._canvas.classList.add("dragging");
  }

  _handleMouseMove(e) {
    const [sx, sy] = this._canvasCoords(e);

    /* detect drag threshold. */
    if (this._mouseDownPos) {
      const [ox, oy] = this._mouseDownPos;
      if (Math.abs(sx - ox) > 3 || Math.abs(sy - oy) > 3) {
        this._didDrag = true;

        /* promote potential node drag to actual drag. */
        if (this._potentialNodeDrag >= 0 && !this._isDraggingNode) {
          this._isDraggingNode = true;
          this._dragNodeIdx = this._potentialNodeDrag;
          this._pinnedNodes.add(this._dragNodeIdx);
          this._settled = false;
        }
      }
    }

    if (this._isDraggingNode) {
      const [wx, wy] = this._screenToWorld(sx, sy);
      const sn = this._simNodes[this._dragNodeIdx];
      sn.x = wx;
      sn.y = wy;
      sn.vx = 0;
      sn.vy = 0;
      this._settled = false;
      return;
    }

    if (this._dragging) {
      const dx = (sx - this._dragStartX) / this._camZoom;
      const dy = (sy - this._dragStartY) / this._camZoom;
      this._camX = this._camStartX - dx;
      this._camY = this._camStartY - dy;
      if (this._settled) this._draw();
      return;
    }

    /* hover detection. */
    const prevHovered = this._hoveredIndex;
    this._hoveredIndex = this._hitTest(sx, sy);

    if (this._hoveredIndex !== prevHovered) {
      this._updateTooltip(sx, sy);
      if (this._settled) this._draw();
    } else if (this._hoveredIndex >= 0) {
      this._moveTooltip(sx, sy);
    }
  }

  _handleMouseUp() {
    this._potentialNodeDrag = -1;
    this._mouseDownPos = null;

    if (this._isDraggingNode) {
      this._isDraggingNode = false;
      this._dragNodeIdx = -1;
    }
    if (this._dragging) {
      this._dragging = false;
    }
    this._canvas.classList.remove("dragging");
  }

  _handleWheel(e) {
    e.preventDefault();
    const [sx, sy] = this._canvasCoords(e);
    const [wxBefore, wyBefore] = this._screenToWorld(sx, sy);

    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    this._camZoom = clamp(this._camZoom * zoomFactor, 0.1, 8);

    const [wxAfter, wyAfter] = this._screenToWorld(sx, sy);
    this._camX -= (wxAfter - wxBefore);
    this._camY -= (wyAfter - wyBefore);

    if (this._settled) this._draw();
  }

  _handleClick(e) {
    if (this._didDrag) return;
    const [sx, sy] = this._canvasCoords(e);
    const hit = this._hitTest(sx, sy);
    if (hit < 0) return;

    /* delay single-click to allow dblclick to cancel it. */
    clearTimeout(this._clickTimer);
    const sn = this._simNodes[hit];
    this._clickTimer = setTimeout(() => {
      window.location.hash = `#/page/${sn.node.subdir}/${encodeURIComponent(sn.node.name)}`;
    }, 250);
  }

  _handleDblClick(e) {
    e.preventDefault();
    clearTimeout(this._clickTimer);
    const [sx, sy] = this._canvasCoords(e);
    const hit = this._hitTest(sx, sy);

    if (hit >= 0) {
      /* toggle pin on double-click. */
      if (this._pinnedNodes.has(hit)) {
        this._pinnedNodes.delete(hit);
        this._settled = false;
      }
      /* center camera on node. */
      const sn = this._simNodes[hit];
      this._camX = sn.x;
      this._camY = sn.y;
      this._camZoom = clamp(this._camZoom < 2 ? 2 : this._camZoom, 0.1, 8);
      if (this._settled) this._draw();
    } else {
      /* double-click on empty space = fit to view. */
      this._fitToView();
    }
  }

  /* ------------------------------------------------------------------ */
  /*  zoom controls.                                                     */
  /* ------------------------------------------------------------------ */

  _zoomBy(factor) {
    this._camZoom = clamp(this._camZoom * factor, 0.1, 8);
    if (this._settled) this._draw();
  }

  _fitToView() {
    const nodes = this._simNodes;
    if (nodes.length === 0) return;

    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    for (const sn of nodes) {
      const r = sn.radius * this._nodeScale;
      minX = Math.min(minX, sn.x - r);
      maxX = Math.max(maxX, sn.x + r);
      minY = Math.min(minY, sn.y - r);
      maxY = Math.max(maxY, sn.y + r);
    }

    const worldW = (maxX - minX) || 100;
    const worldH = (maxY - minY) || 100;
    const pad = 60;

    this._camX = (minX + maxX) / 2;
    this._camY = (minY + maxY) / 2;
    this._camZoom = Math.min(
      (this._viewW - pad * 2) / worldW,
      (this._viewH - pad * 2) / worldH,
      3,
    );
    if (this._settled) this._draw();
  }

  _resetLayout() {
    this._pinnedNodes.clear();
    const spread = Math.sqrt(this._simNodes.length) * 40;
    for (let i = 0; i < this._simNodes.length; i++) {
      const sn = this._simNodes[i];
      const angle = (i / this._simNodes.length) * Math.PI * 2;
      const r = spread * (0.3 + Math.random() * 0.7);
      sn.x = Math.cos(angle) * r + (Math.random() - 0.5) * 60;
      sn.y = Math.sin(angle) * r + (Math.random() - 0.5) * 60;
      sn.vx = 0;
      sn.vy = 0;
    }
    this._restartSimulation();
    this._centerCamera();
  }

  _unpinAll() {
    this._pinnedNodes.clear();
    this._restartSimulation();
  }

  /* ------------------------------------------------------------------ */
  /*  color groups (Obsidian-style keyword-based node coloring).         */
  /* ------------------------------------------------------------------ */

  _loadGroups() {
    try {
      const raw = localStorage.getItem("sb_graph_groups");
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  }

  _saveGroups() {
    localStorage.setItem("sb_graph_groups", JSON.stringify(this._groups));
  }

  _addGroup() {
    const idx = this._groups.length % GROUP_PALETTE.length;
    this._groups = [...this._groups, { name: "", color: GROUP_PALETTE[idx] }];
    this._saveGroups();
  }

  _removeGroup(i) {
    this._groups = this._groups.filter((_, j) => j !== i);
    this._saveGroups();
    if (this._settled) this._draw();
  }

  _updateGroupName(i, name) {
    this._groups = this._groups.map((g, j) => j === i ? { ...g, name } : g);
    this._saveGroups();
    if (this._settled) this._draw();
  }

  /** resolve node color based on current color mode. */
  _nodeColor(node) {
    if (this._colorMode === "none") return "oklch(68% 0.04 260)";
    if (this._colorMode === "type") return SUBDIR_COLORS[node.subdir] || "oklch(60% 0.05 260)";

    /* groups mode: first matching group wins. */
    const nameLower = node.name.toLowerCase();
    for (const group of this._groups) {
      if (!group.name) continue;
      const keywords = group.name.toLowerCase().split(/\s+/).filter(Boolean);
      if (keywords.some((kw) => nameLower.includes(kw))) return group.color;
    }
    return "oklch(68% 0.04 260)"; /* unmatched = neutral grey. */
  }

  /* ------------------------------------------------------------------ */
  /*  tooltip.                                                           */
  /* ------------------------------------------------------------------ */

  _updateTooltip(sx, sy) {
    const tip = this.renderRoot.querySelector(".tooltip");
    if (!tip) return;

    if (this._hoveredIndex < 0) {
      tip.classList.remove("visible");
      return;
    }

    const sn = this._simNodes[this._hoveredIndex];
    const isPinned = this._pinnedNodes.has(this._hoveredIndex);
    tip.querySelector(".tooltip-name").textContent = sn.node.name;
    tip.querySelector(".tooltip-subdir").textContent =
      `${SUBDIR_LABELS[sn.node.subdir] || sn.node.subdir} \u00b7 ${sn.node.link_count} links${isPinned ? " \u00b7 pinned" : ""}`;
    tip.classList.add("visible");
    this._moveTooltip(sx, sy);
  }

  _moveTooltip(sx, sy) {
    const tip = this.renderRoot.querySelector(".tooltip");
    if (!tip) return;
    const OFFSET = 14;
    const wrap = this._canvas.parentElement.getBoundingClientRect();
    const tipW = tip.offsetWidth;
    const tipH = tip.offsetHeight;

    let left = sx + OFFSET;
    let top = sy + OFFSET;
    if (left + tipW > wrap.width - 8) left = sx - tipW - OFFSET;
    if (top + tipH > wrap.height - 8) top = sy - tipH - OFFSET;

    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  /* ------------------------------------------------------------------ */
  /*  search.                                                            */
  /* ------------------------------------------------------------------ */

  _onSearchInput(e) {
    this._searchText = e.target.value;
    if (this._settled) this._draw();
  }

  /* ------------------------------------------------------------------ */
  /*  settings panel.                                                    */
  /* ------------------------------------------------------------------ */

  _renderSlider(label, value, min, max, step, onChange, format) {
    const display = format ? format(value) : String(value);
    return html`
      <div class="settings-row">
        <div class="settings-label">
          <span>${label}</span>
          <span class="settings-value">${display}</span>
        </div>
        <input type="range"
          min=${min} max=${max} step=${step}
          .value=${String(value)}
          @input=${(e) => onChange(+e.target.value)}>
      </div>
    `;
  }

  _renderSettings() {
    const pinCount = this._pinnedNodes.size;
    return html`
      <div class="settings-panel">
        <div class="settings-header">
          <span>Settings</span>
          <button class="close-btn" @click=${() => { this._settingsOpen = false; }}>&times;</button>
        </div>

        <div class="settings-group">
          <div class="settings-group-title">Forces</div>
          ${this._renderSlider("Repulsion", this._repulsion, 100, 3000, 50,
            (v) => { this._repulsion = v; this._restartSimulation(); })}
          ${this._renderSlider("Link distance", this._springRest, 20, 300, 5,
            (v) => { this._springRest = v; this._restartSimulation(); })}
          ${this._renderSlider("Center force", this._gravity * 1000, 0, 100, 1,
            (v) => { this._gravity = v / 1000; this._restartSimulation(); },
            (v) => (v / 1000).toFixed(3))}
          ${this._renderSlider("Damping", this._damping * 100, 50, 99, 1,
            (v) => { this._damping = v / 100; this._restartSimulation(); },
            (v) => (v / 100).toFixed(2))}
        </div>

        <div class="settings-group">
          <div class="settings-group-title">Display</div>
          ${this._renderSlider("Label zoom", this._labelThreshold * 10, 5, 40, 1,
            (v) => { this._labelThreshold = v / 10; if (this._settled) this._draw(); },
            (v) => (v / 10).toFixed(1))}
          ${this._renderSlider("Node size", this._nodeScale * 10, 5, 30, 1,
            (v) => { this._nodeScale = v / 10; if (this._settled) this._draw(); },
            (v) => (v / 10).toFixed(1))}
        </div>

        <div class="settings-group">
          <div class="settings-group-title">Coloring</div>
          <div class="color-mode-row">
            ${["none", "type", "groups"].map((mode) => html`
              <button
                class="color-mode-btn ${this._colorMode === mode ? "active" : ""}"
                @click=${() => { this._colorMode = mode; if (this._settled) this._draw(); }}
              >${mode === "none" ? "None" : mode === "type" ? "By type" : "Groups"}</button>
            `)}
          </div>
          ${this._colorMode === "groups" ? html`
            <div class="groups-list">
              ${this._groups.map((g, i) => html`
                <div class="group-row">
                  <input class="group-input" type="text"
                    placeholder="keyword…"
                    .value=${g.name}
                    @input=${(e) => this._updateGroupName(i, e.target.value)}>
                  <span class="group-dot" style="background: ${g.color}"></span>
                  <button class="group-remove" @click=${() => this._removeGroup(i)}>&times;</button>
                </div>
              `)}
              <button class="settings-btn new-group-btn" @click=${() => this._addGroup()}>New group</button>
            </div>
          ` : ""}
        </div>

        <div class="settings-actions">
          <button class="settings-btn" @click=${this._resetLayout}>Reset layout</button>
          <button class="settings-btn" @click=${this._unpinAll}>
            Unpin all${pinCount > 0 ? ` (${pinCount})` : ""}
          </button>
          <button class="settings-btn" @click=${this._fitToView}>Fit to view</button>
        </div>
      </div>
    `;
  }

  /* ------------------------------------------------------------------ */
  /*  render.                                                            */
  /* ------------------------------------------------------------------ */

  render() {
    if (this._loading) {
      return html`
        <div class="toolbar"><h1>Graph</h1></div>
        <div class="loading">Loading graph data\u2026</div>
      `;
    }

    if (this._error) {
      return html`
        <div class="toolbar"><h1>Graph</h1></div>
        <div class="error-msg">${this._error}</div>
      `;
    }

    const pinCount = this._pinnedNodes.size;

    return html`
      <div class="toolbar">
        <h1>Graph</h1>
        <input
          class="search-box"
          type="text"
          placeholder="Filter nodes\u2026"
          .value=${this._searchText}
          @input=${this._onSearchInput}
        />
        <span class="stats">
          ${this._nodeCount} nodes, ${this._edgeCount} edges${pinCount > 0 ? html`, <b>${pinCount}</b> pinned` : ""}
        </span>
      </div>

      <div class="legend">
        ${this._colorMode === "type" ? Object.entries(SUBDIR_COLORS).map(([key, color]) => html`
          <span class="legend-item">
            <span class="legend-dot" style="background: ${color}"></span>
            ${SUBDIR_LABELS[key]}
          </span>
        `) : ""}
        ${this._colorMode === "groups" ? this._groups.filter((g) => g.name).map((g) => html`
          <span class="legend-item">
            <span class="legend-dot" style="background: ${g.color}"></span>
            ${g.name}
          </span>
        `) : ""}
      </div>

      <div class="canvas-wrap">
        <canvas></canvas>

        <button
          class="settings-toggle ${this._settingsOpen ? "active" : ""}"
          @click=${() => { this._settingsOpen = !this._settingsOpen; }}
          title="Display settings"
        >&#9881;</button>

        ${this._settingsOpen ? this._renderSettings() : ""}

        <div class="zoom-controls">
          <button class="zoom-btn" @click=${() => this._zoomBy(1.3)} title="Zoom in">+</button>
          <button class="zoom-btn" @click=${() => this._zoomBy(0.7)} title="Zoom out">&#8722;</button>
          <button class="zoom-btn" @click=${this._fitToView} title="Fit all nodes">&#8982;</button>
        </div>

        <div class="tooltip">
          <div class="tooltip-name"></div>
          <div class="tooltip-subdir"></div>
        </div>
      </div>
    `;
  }
}

customElements.define("graph-view", GraphView);
