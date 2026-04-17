import { LitElement, html, css } from "lit";
import { wiki } from "../lib/api.js";

const SUBDIRS = ["sources", "entities", "concepts", "synthesis"];

export class WikiBrowser extends LitElement {
  static properties = {
    _pages:       { state: true },
    _filter:      { state: true },
    _search:      { state: true },
    _loading:     { state: true },
    _error:       { state: true },
    _stats:       { state: true },
  };

  static styles = css`
    :host {
      display: block;
      max-width: 1200px;
      margin: 0 auto;
    }

    /* --- header. --- */
    .header {
      margin-bottom: var(--sp-8);
    }
    .header h1 {
      font-family: var(--font-heading);
      font-size: var(--text-4xl);
      color: var(--text-primary);
      margin-bottom: var(--sp-1);
    }
    .header p {
      color: var(--text-muted);
      font-size: var(--text-sm);
    }

    /* --- filter bar. --- */
    .filter-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--sp-3);
      margin-bottom: var(--sp-6);
    }
    .search-input {
      flex: 1;
      min-width: 200px;
      padding: var(--sp-2) var(--sp-4);
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      color: var(--text-primary);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      transition: border-color var(--duration-fast) var(--ease-out);
    }
    .search-input::placeholder { color: var(--text-muted); }
    .search-input:focus {
      outline: none;
      border-color: var(--accent);
    }

    .filter-buttons {
      display: flex;
      gap: var(--sp-1);
    }
    .filter-btn {
      padding: var(--sp-1) var(--sp-3);
      font-size: var(--text-xs);
      font-weight: 500;
      font-family: var(--font-body);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      background: transparent;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .filter-btn:hover {
      background: var(--bg-card);
      color: var(--text-primary);
    }
    .filter-btn.active {
      background: oklch(75% 0.15 70 / 0.12);
      border-color: var(--accent-dim);
      color: var(--accent);
    }

    /* --- stats bar. --- */
    .stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-4);
      padding: var(--sp-3) var(--sp-4);
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      margin-bottom: var(--sp-6);
    }
    .stat {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-sm);
      color: var(--text-secondary);
    }
    .stat-count {
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }
    .stat-label { color: var(--text-muted); }

    /* --- grid. --- */
    .page-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: var(--sp-4);
    }
    @media (max-width: 1024px) {
      .page-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 640px) {
      .page-grid { grid-template-columns: 1fr; }
    }

    /* --- card. --- */
    .page-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-5);
      transition: background var(--duration-fast) var(--ease-out),
                  box-shadow var(--duration-fast) var(--ease-out),
                  transform var(--duration-fast) var(--ease-out);
      display: flex;
      flex-direction: column;
      gap: var(--sp-3);
    }
    .page-card:hover {
      background: var(--bg-card-hover);
      box-shadow: var(--shadow-md);
      transform: translateY(-2px);
    }

    .card-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--sp-2);
    }
    .card-name {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-primary);
      text-decoration: none;
      line-height: 1.3;
      transition: color var(--duration-fast) var(--ease-out);
    }
    .card-name:hover {
      color: var(--accent);
    }

    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 500;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .badge-sources   { background: oklch(70% 0.12 250 / 0.15); color: var(--color-sources); }
    .badge-entities  { background: oklch(70% 0.14 165 / 0.15); color: var(--color-entities); }
    .badge-concepts  { background: oklch(72% 0.12 300 / 0.15); color: var(--color-concepts); }
    .badge-synthesis { background: oklch(75% 0.14 70 / 0.15);  color: var(--color-synthesis); }

    .card-meta {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .card-type {
      font-style: italic;
    }
    .card-date {
      margin-left: auto;
      font-variant-numeric: tabular-nums;
    }

    .card-tags {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-1);
    }
    .tag {
      padding: 1px var(--sp-2);
      font-size: 0.65rem;
      font-weight: 500;
      border-radius: var(--radius-sm);
      background: oklch(28% 0.015 260 / 0.6);
      color: var(--text-secondary);
      letter-spacing: 0.03em;
    }

    /* --- states. --- */
    .loading, .error, .empty {
      text-align: center;
      padding: var(--sp-16) var(--sp-8);
      color: var(--text-muted);
    }
    .error {
      color: var(--error);
    }
    .empty-icon {
      font-size: 3rem;
      margin-bottom: var(--sp-4);
      opacity: 0.4;
    }
  `;

  constructor() {
    super();
    this._pages = [];
    this._filter = "";
    this._search = "";
    this._loading = true;
    this._error = null;
    this._stats = { sources: 0, entities: 0, concepts: 0, synthesis: 0 };
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadPages();
  }

  async _loadPages() {
    this._loading = true;
    this._error = null;
    try {
      const data = await wiki.pages(this._filter);
      this._pages = Array.isArray(data) ? data : data.pages ?? [];
      this._computeStats();
    } catch (err) {
      this._error = err.message;
      this._pages = [];
    } finally {
      this._loading = false;
    }
  }

  _computeStats() {
    const counts = { sources: 0, entities: 0, concepts: 0, synthesis: 0 };
    for (const p of this._pages) {
      const sub = (p.subdir || "").toLowerCase();
      if (sub in counts) counts[sub]++;
    }
    this._stats = counts;
  }

  _onSearch(e) {
    this._search = e.target.value.toLowerCase();
  }

  _setFilter(subdir) {
    this._filter = this._filter === subdir ? "" : subdir;
    this._loadPages();
  }

  _filtered() {
    if (!this._search) return this._pages;
    return this._pages.filter((p) => {
      const name = (p.name || p.title || "").toLowerCase();
      const tags = (p.tags || []).join(" ").toLowerCase();
      return name.includes(this._search) || tags.includes(this._search);
    });
  }

  _pageHref(page) {
    const subdir = page.subdir || "_";
    const name = encodeURIComponent(page.name || page.title || "");
    return `#/page/${subdir}/${name}`;
  }

  _badgeClass(subdir) {
    const s = (subdir || "").toLowerCase();
    if (SUBDIRS.includes(s)) return `badge badge-${s}`;
    return "badge";
  }

  _total() {
    return Object.values(this._stats).reduce((a, b) => a + b, 0);
  }

  render() {
    const pages = this._filtered();

    return html`
      <div class="header">
        <h1>Browse Wiki</h1>
        <p>Explore all pages in the knowledge base</p>
      </div>

      <div class="filter-bar">
        <input
          class="search-input"
          type="text"
          placeholder="Search pages by name or tag..."
          .value=${this._search}
          @input=${this._onSearch}
        />
        <div class="filter-buttons">
          <button
            class="filter-btn ${this._filter === "" ? "active" : ""}"
            @click=${() => this._setFilter("")}
          >All</button>
          ${SUBDIRS.map((s) => html`
            <button
              class="filter-btn ${this._filter === s ? "active" : ""}"
              @click=${() => this._setFilter(s)}
            >${s}</button>
          `)}
        </div>
      </div>

      <div class="stats-bar">
        <div class="stat">
          <span class="stat-count">${this._total()}</span>
          <span class="stat-label">total</span>
        </div>
        ${SUBDIRS.map((s) => html`
          <div class="stat">
            <span class="stat-count" style="color: var(--color-${s})">${this._stats[s]}</span>
            <span class="stat-label">${s}</span>
          </div>
        `)}
      </div>

      ${this._loading
        ? html`<div class="loading">Loading pages...</div>`
        : this._error
          ? html`<div class="error">${this._error}</div>`
          : pages.length === 0
            ? html`
                <div class="empty">
                  <div class="empty-icon">&#128214;</div>
                  <div>No pages found${this._search ? ` matching "${this._search}"` : ""}</div>
                </div>
              `
            : html`
                <div class="page-grid">
                  ${pages.map((p) => this._renderCard(p))}
                </div>
              `
      }
    `;
  }

  _renderCard(page) {
    const name = page.name || page.title || "Untitled";
    const subdir = page.subdir || "";
    const type = page.type || "";
    const tags = page.tags || [];
    const created = page.created || "";

    return html`
      <div class="page-card">
        <div class="card-top">
          <a class="card-name" href=${this._pageHref(page)}>${name}</a>
          <span class=${this._badgeClass(subdir)}>${subdir}</span>
        </div>
        <div class="card-meta">
          ${type ? html`<span class="card-type">${type}</span>` : ""}
          ${created ? html`<span class="card-date">${created}</span>` : ""}
        </div>
        ${tags.length > 0
          ? html`
              <div class="card-tags">
                ${tags.map((t) => html`<span class="tag">${t}</span>`)}
              </div>
            `
          : ""
        }
      </div>
    `;
  }
}

customElements.define("wiki-browser", WikiBrowser);
