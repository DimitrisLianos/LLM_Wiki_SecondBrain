import { LitElement, html, css } from "lit";
import { search } from "../lib/api.js";

const DEBOUNCE_MS = 300;

export class SearchPanel extends LitElement {
  static properties = {
    _query:       { state: true },
    _results:     { state: true },
    _total:       { state: true },
    _elapsed:     { state: true },
    _loading:     { state: true },
    _error:       { state: true },
    _rebuilding:  { state: true },
    _confirmRebuild: { state: true },
  };

  static styles = css`
    :host {
      display: block;
      max-width: 860px;
      width: 100%;
    }

    /* ----- header. ----- */
    .header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--sp-4);
      margin-bottom: var(--sp-6);
    }
    .title {
      font-family: var(--font-heading);
      font-size: var(--text-3xl);
      color: var(--text-primary);
      font-weight: 400;
    }

    /* ----- search bar. ----- */
    .search-bar {
      position: relative;
      margin-bottom: var(--sp-6);
    }
    .search-icon {
      position: absolute;
      left: var(--sp-4);
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      font-size: var(--text-lg);
      pointer-events: none;
    }
    .search-input {
      width: 100%;
      padding: var(--sp-4) var(--sp-4) var(--sp-4) var(--sp-10);
      background: var(--bg-card);
      color: var(--text-primary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      font-family: var(--font-body);
      font-size: var(--text-lg);
      transition: border-color var(--duration-fast) var(--ease-out),
                  box-shadow var(--duration-fast) var(--ease-out);
    }
    .search-input::placeholder {
      color: var(--text-muted);
    }
    .search-input:focus {
      outline: none;
      border-color: var(--accent-dim);
      box-shadow: 0 0 0 3px oklch(75% 0.15 70 / 0.08);
    }
    .search-loading {
      position: absolute;
      right: var(--sp-4);
      top: 50%;
      transform: translateY(-50%);
      width: 16px;
      height: 16px;
      border: 2px solid var(--border-light);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: translateY(-50%) rotate(360deg); }
    }

    /* ----- meta row. ----- */
    .meta-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--sp-5);
      padding: 0 var(--sp-1);
    }
    .meta-stats {
      font-size: var(--text-sm);
      color: var(--text-muted);
    }
    .meta-stats strong {
      color: var(--text-secondary);
      font-weight: 600;
    }

    /* ----- results. ----- */
    .results-list {
      display: flex;
      flex-direction: column;
      gap: var(--sp-3);
    }
    .result-card {
      display: grid;
      grid-template-columns: 1fr auto;
      grid-template-rows: auto auto;
      gap: var(--sp-1) var(--sp-4);
      padding: var(--sp-5) var(--sp-6);
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      transition: border-color var(--duration-fast) var(--ease-out),
                  background var(--duration-fast) var(--ease-out),
                  transform var(--duration-fast) var(--ease-out);
    }
    .result-card:hover {
      border-color: var(--border-light);
      background: var(--bg-card-hover);
      transform: translateY(-1px);
    }
    .result-top {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      grid-column: 1;
      grid-row: 1;
    }
    .result-name {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--link);
      text-decoration: none;
      transition: color var(--duration-fast) var(--ease-out);
    }
    .result-name:hover {
      color: var(--link-hover);
    }
    .result-badge {
      display: inline-flex;
      align-items: center;
      padding: 2px var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 600;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      flex-shrink: 0;
    }
    .badge-sources {
      background: oklch(70% 0.12 250 / 0.12);
      color: var(--color-sources);
    }
    .badge-entities {
      background: oklch(70% 0.14 165 / 0.12);
      color: var(--color-entities);
    }
    .badge-concepts {
      background: oklch(72% 0.12 300 / 0.12);
      color: var(--color-concepts);
    }
    .badge-synthesis {
      background: oklch(75% 0.14 70 / 0.12);
      color: var(--color-synthesis);
    }

    .result-score {
      grid-column: 2;
      grid-row: 1;
      font-family: var(--font-mono);
      font-size: var(--text-xs);
      color: var(--text-muted);
      align-self: center;
      white-space: nowrap;
    }

    .result-snippet {
      grid-column: 1 / -1;
      grid-row: 2;
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.6;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    /* ----- rebuild. ----- */
    .rebuild-section {
      margin-top: var(--sp-8);
      padding-top: var(--sp-6);
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: var(--sp-4);
    }
    .rebuild-btn {
      padding: var(--sp-2) var(--sp-5);
      background: var(--bg-card);
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out),
                  border-color var(--duration-fast) var(--ease-out),
                  color var(--duration-fast) var(--ease-out);
    }
    .rebuild-btn:hover:not(:disabled) {
      background: var(--bg-card-hover);
      border-color: var(--border-light);
      color: var(--text-primary);
    }
    .rebuild-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .rebuild-btn--confirm {
      background: oklch(65% 0.15 25 / 0.1);
      border-color: var(--error);
      color: var(--error);
    }
    .rebuild-btn--confirm:hover:not(:disabled) {
      background: oklch(65% 0.15 25 / 0.2);
      color: var(--error);
    }
    .rebuild-label {
      font-size: var(--text-sm);
      color: var(--text-muted);
    }
    .rebuild-spinner {
      width: 14px;
      height: 14px;
      border: 2px solid var(--border-light);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin-flat 0.8s linear infinite;
      flex-shrink: 0;
    }
    @keyframes spin-flat {
      to { transform: rotate(360deg); }
    }

    /* ----- empty / error. ----- */
    .empty-state {
      text-align: center;
      padding: var(--sp-12) var(--sp-8);
      color: var(--text-muted);
    }
    .empty-icon {
      font-size: 2.5rem;
      margin-bottom: var(--sp-4);
      opacity: 0.5;
    }
    .empty-text {
      font-size: var(--text-sm);
      max-width: 320px;
      margin: 0 auto;
      line-height: 1.6;
    }

    .error-msg {
      padding: var(--sp-4) var(--sp-5);
      background: oklch(65% 0.15 25 / 0.08);
      border: 1px solid oklch(65% 0.15 25 / 0.25);
      border-radius: var(--radius-md);
      color: var(--error);
      font-size: var(--text-sm);
      line-height: 1.5;
      margin-bottom: var(--sp-5);
    }
  `;

  constructor() {
    super();
    this._query = "";
    this._results = [];
    this._total = 0;
    this._elapsed = 0;
    this._loading = false;
    this._error = "";
    this._rebuilding = false;
    this._confirmRebuild = false;
    this._debounceTimer = null;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearTimeout(this._debounceTimer);
  }

  _onInput(e) {
    this._query = e.target.value;

    clearTimeout(this._debounceTimer);

    if (!this._query.trim()) {
      this._results = [];
      this._total = 0;
      this._elapsed = 0;
      this._error = "";
      return;
    }

    this._debounceTimer = setTimeout(() => this._runSearch(), DEBOUNCE_MS);
  }

  async _runSearch() {
    const q = this._query.trim();
    if (!q) return;

    this._loading = true;
    this._error = "";

    const t0 = performance.now();

    try {
      const data = await search.query(q);
      this._elapsed = performance.now() - t0;
      this._results = data.results || data || [];
      this._total = data.total ?? this._results.length;
    } catch (err) {
      this._error = err.message;
      this._results = [];
      this._total = 0;
    } finally {
      this._loading = false;
    }
  }

  async _handleRebuild() {
    if (!this._confirmRebuild) {
      this._confirmRebuild = true;
      return;
    }

    this._confirmRebuild = false;
    this._rebuilding = true;

    try {
      await search.rebuild();
      // re-run current search if there is one.
      if (this._query.trim()) {
        await this._runSearch();
      }
    } catch (err) {
      this._error = err.message;
    } finally {
      this._rebuilding = false;
    }
  }

  _cancelRebuild() {
    this._confirmRebuild = false;
  }

  /**
   * Build a link for a result based on its subdir.
   * @param {{ name: string, subdir?: string, page?: string }} r
   * @returns {string}
   */
  _resultHref(r) {
    const name = (r.name || r.page || "").replace(/\.md$/, "");
    const subdir = r.subdir || "_";
    return `#/page/${encodeURIComponent(subdir)}/${encodeURIComponent(name)}`;
  }

  /**
   * @param {string} text
   * @param {string} q
   * @returns {import("lit").TemplateResult}
   */
  _highlightSnippet(text, q) {
    if (!text || !q) return html`${text || ""}`;

    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return html`${text}`;

    // build a regex matching any of the search terms.
    const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const re = new RegExp(`(${escaped.join("|")})`, "gi");

    const parts = text.split(re);
    return html`${parts.map((part) =>
      terms.includes(part.toLowerCase())
        ? html`<strong style="color: var(--accent);">${part}</strong>`
        : part
    )}`;
  }

  _formatElapsed(ms) {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  render() {
    return html`
      <div class="header">
        <h1 class="title">Search</h1>
      </div>

      <div class="search-bar">
        <span class="search-icon">\u2315</span>
        <input
          class="search-input"
          type="text"
          placeholder="Search the wiki..."
          .value=${this._query}
          @input=${this._onInput}
          autofocus
        />
        ${this._loading ? html`<div class="search-loading"></div>` : ""}
      </div>

      ${this._error ? html`<div class="error-msg">${this._error}</div>` : ""}

      ${this._renderBody()}

      ${this._renderRebuild()}
    `;
  }

  _renderBody() {
    if (!this._query.trim()) {
      return html`
        <div class="empty-state">
          <div class="empty-icon">\u2315</div>
          <div class="empty-text">
            Full-text search across all wiki pages. Start typing to find entities, concepts, sources, and synthesis pages.
          </div>
        </div>
      `;
    }

    if (this._results.length === 0 && !this._loading) {
      return html`
        <div class="empty-state">
          <div class="empty-icon">\u2205</div>
          <div class="empty-text">
            No results for "${this._query}". Try different keywords or rebuild the index.
          </div>
        </div>
      `;
    }

    return html`
      ${this._total > 0 ? html`
        <div class="meta-row">
          <span class="meta-stats">
            <strong>${this._total}</strong> result${this._total !== 1 ? "s" : ""}
            in ${this._formatElapsed(this._elapsed)}
          </span>
        </div>
      ` : ""}

      <div class="results-list">
        ${this._results.map((r) => this._renderResult(r))}
      </div>
    `;
  }

  _renderResult(r) {
    const name = (r.name || r.page || "").replace(/\.md$/, "");
    const subdir = r.subdir || "wiki";
    const snippet = r.snippet || r.excerpt || r.content || "";
    const score = r.score ?? r.rank ?? null;
    const badgeClass = `result-badge badge-${subdir}`;

    return html`
      <div class="result-card">
        <div class="result-top">
          <a class="result-name" href="${this._resultHref(r)}">${name}</a>
          <span class="${badgeClass}">${subdir}</span>
        </div>

        ${score !== null ? html`
          <span class="result-score">${typeof score === "number" ? score.toFixed(2) : score}</span>
        ` : ""}

        ${snippet ? html`
          <div class="result-snippet">
            ${this._highlightSnippet(snippet, this._query)}
          </div>
        ` : ""}
      </div>
    `;
  }

  _renderRebuild() {
    return html`
      <div class="rebuild-section">
        ${this._rebuilding
          ? html`
              <div class="rebuild-spinner"></div>
              <span class="rebuild-label">Rebuilding index\u2026</span>
            `
          : this._confirmRebuild
            ? html`
                <button
                  class="rebuild-btn rebuild-btn--confirm"
                  @click=${this._handleRebuild}
                >Confirm rebuild</button>
                <button
                  class="rebuild-btn"
                  @click=${this._cancelRebuild}
                >Cancel</button>
                <span class="rebuild-label">This may take a moment.</span>
              `
            : html`
                <button
                  class="rebuild-btn"
                  @click=${this._handleRebuild}
                  ?disabled=${this._rebuilding}
                >Rebuild Index</button>
                <span class="rebuild-label">Re-index all wiki pages for search.</span>
              `
        }
      </div>
    `;
  }
}

customElements.define("search-panel", SearchPanel);
