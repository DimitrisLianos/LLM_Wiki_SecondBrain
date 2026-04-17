import { LitElement, html, css } from "lit";
import { server } from "../lib/api.js";
import { getCurrentRoute, onRouteChange, navigate } from "../lib/router.js";
import { icons } from "../lib/icons.js";

export class AppShell extends LitElement {
  static properties = {
    _route: { state: true },
    _sidebarCollapsed: { state: true },
    _llmRunning: { state: true },
    _embedRunning: { state: true },
  };

  static styles = css`
    :host {
      display: flex;
      width: 100%;
      height: 100dvh;
      overflow: hidden;
    }

    /* --- sidebar. --- */
    nav {
      width: var(--sidebar-width);
      min-width: var(--sidebar-width);
      height: 100dvh;
      background: var(--bg-surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      transition: width var(--duration-normal) var(--ease-out),
                  min-width var(--duration-normal) var(--ease-out);
      overflow: hidden;
      z-index: 10;
    }
    nav.collapsed {
      width: var(--sidebar-collapsed);
      min-width: var(--sidebar-collapsed);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-5) var(--sp-5);
      border-bottom: 1px solid var(--border);
      cursor: pointer;
    }
    .brand-icon {
      flex-shrink: 0;
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
    }
    .brand-icon svg {
      width: 100%;
      height: 100%;
    }
    .brand-text {
      font-family: var(--font-heading);
      font-size: var(--text-xl);
      color: var(--accent);
      white-space: nowrap;
      overflow: hidden;
    }
    nav.collapsed .brand-text { display: none; }

    .nav-items {
      flex: 1;
      padding: var(--sp-3) var(--sp-2);
      overflow-y: auto;
    }

    .nav-section {
      margin-bottom: var(--sp-4);
    }
    .nav-section-label {
      font-size: var(--text-xs);
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      padding: var(--sp-2) var(--sp-3);
      white-space: nowrap;
      overflow: hidden;
    }
    nav.collapsed .nav-section-label { display: none; }

    .nav-item {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-2) var(--sp-3);
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
      text-decoration: none;
      white-space: nowrap;
    }
    .nav-item:hover {
      background: var(--bg-card);
      color: var(--text-primary);
    }
    .nav-item.active {
      background: oklch(75% 0.15 70 / 0.1);
      color: var(--accent);
    }
    .nav-item-icon {
      flex-shrink: 0;
      width: 20px;
      height: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: currentColor;
      opacity: 0.85;
      transition: opacity var(--duration-fast) var(--ease-out);
    }
    .nav-item-icon svg {
      width: 100%;
      height: 100%;
    }
    .nav-item:hover .nav-item-icon,
    .nav-item.active .nav-item-icon {
      opacity: 1;
    }
    .nav-item-label {
      font-size: var(--text-sm);
      font-weight: 500;
    }
    nav.collapsed .nav-item-label { display: none; }

    /* --- status footer. --- */
    .status-bar {
      padding: var(--sp-3) var(--sp-4);
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: var(--sp-1);
    }
    nav.collapsed .status-bar { padding: var(--sp-3) var(--sp-2); }

    .status-dot {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .dot.on  { background: var(--success); box-shadow: 0 0 6px var(--success); }
    .dot.off { background: var(--error); }
    nav.collapsed .status-label { display: none; }

    /* --- main content. --- */
    main {
      flex: 1;
      overflow-y: auto;
      padding: clamp(var(--sp-4), 3vw, var(--sp-10));
      background: var(--bg-deep);
    }

    @media (max-width: 768px) {
      nav { width: var(--sidebar-collapsed); min-width: var(--sidebar-collapsed); }
      nav .brand-text,
      nav .nav-section-label,
      nav .nav-item-label,
      nav .status-label { display: none; }
      main { padding: var(--sp-3); }
    }

    @media (min-width: 1440px) {
      main { padding: var(--sp-10) clamp(var(--sp-8), 5vw, 6rem); }
    }
  `;

  constructor() {
    super();
    this._route = getCurrentRoute();
    this._sidebarCollapsed = false;
    this._llmRunning = false;
    this._embedRunning = false;
  }

  connectedCallback() {
    super.connectedCallback();
    onRouteChange((route) => { this._route = route; });
    this._pollStatus();
    this._statusInterval = setInterval(() => this._pollStatus(), 10000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._statusInterval);
  }

  async _pollStatus() {
    try {
      const data = await server.status();
      this._llmRunning = data.llm_server?.running ?? false;
      this._embedRunning = data.embed_server?.running ?? false;
    } catch (err) {
      // a failed poll is expected when the backend is down; surface it
      // at `debug` level instead of eating it silently so inspector users
      // can see why the sidebar badges went grey.
      if (import.meta.env?.DEV) {
        console.debug("sidebar status poll failed:", err);
      }
      this._llmRunning = false;
      this._embedRunning = false;
    }
  }

  _isActive(path) {
    return this._route.hash === path || window.location.hash === path;
  }

  // each nav item carries a keyed icon name; the renderer resolves the key
  // to an inline svg at render time. keeping the key (not the rendered
  // template) means the property remains a plain serialisable value.
  _navItems = [
    { section: "Interact", items: [
      { icon: "query",  label: "Query",  path: "#/query" },
      { icon: "search", label: "Search", path: "#/search" },
    ]},
    { section: "Knowledge", items: [
      { icon: "browse", label: "Browse", path: "#/browse" },
      { icon: "graph",  label: "Graph",  path: "#/graph" },
    ]},
    { section: "Manage", items: [
      { icon: "ingest", label: "Ingest", path: "#/ingest" },
      { icon: "health", label: "Health", path: "#/lint" },
      { icon: "dedup",  label: "Dedup",  path: "#/dedup" },
      { icon: "server", label: "Server", path: "#/server" },
    ]},
  ];

  render() {
    const { view, params } = this._route;

    return html`
      <nav class="${this._sidebarCollapsed ? "collapsed" : ""}">
        <div
          class="brand"
          title="Toggle sidebar"
          @click=${() => { this._sidebarCollapsed = !this._sidebarCollapsed; }}
        >
          <span class="brand-icon" aria-hidden="true">${icons.brand()}</span>
          <span class="brand-text">SecondBrain</span>
        </div>

        <div class="nav-items">
          ${this._navItems.map(section => html`
            <div class="nav-section">
              <div class="nav-section-label">${section.section}</div>
              ${section.items.map(item => html`
                <a class="nav-item ${this._isActive(item.path) ? "active" : ""}"
                   href="${item.path}"
                   title="${item.label}"
                   aria-label="${item.label}">
                  <span class="nav-item-icon" aria-hidden="true">
                    ${icons[item.icon] ? icons[item.icon]() : ""}
                  </span>
                  <span class="nav-item-label">${item.label}</span>
                </a>
              `)}
            </div>
          `)}
        </div>

        <div class="status-bar">
          <div class="status-dot">
            <span class="dot ${this._llmRunning ? "on" : "off"}"></span>
            <span class="status-label">LLM ${this._llmRunning ? "ready" : "offline"}</span>
          </div>
          <div class="status-dot">
            <span class="dot ${this._embedRunning ? "on" : "off"}"></span>
            <span class="status-label">Embed ${this._embedRunning ? "ready" : "offline"}</span>
          </div>
        </div>
      </nav>

      <main>
        ${this._renderView(view, params)}
      </main>
    `;
  }

  _renderView(view, params) {
    switch (view) {
      case "query-panel":   return html`<query-panel></query-panel>`;
      case "search-panel":  return html`<search-panel></search-panel>`;
      case "wiki-browser":  return html`<wiki-browser></wiki-browser>`;
      case "page-viewer":   return html`<page-viewer subdir="${params.subdir}" name="${params.name}"></page-viewer>`;
      case "ingest-panel":  return html`<ingest-panel></ingest-panel>`;
      case "server-panel":  return html`<server-panel></server-panel>`;
      case "lint-panel":    return html`<lint-panel></lint-panel>`;
      case "dedup-panel":   return html`<dedup-panel></dedup-panel>`;
      case "graph-view":    return html`<graph-view></graph-view>`;
      default:              return html`<query-panel></query-panel>`;
    }
  }
}

customElements.define("app-shell", AppShell);
