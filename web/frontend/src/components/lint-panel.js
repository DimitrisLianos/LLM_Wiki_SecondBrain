import { LitElement, html, css } from "lit";
import { lint } from "../lib/api.js";

export class LintPanel extends LitElement {
  static properties = {
    _results:    { state: true },
    _loading:    { state: true },
    _error:      { state: true },
    _collapsed:  { state: true },
    _deleting:   { state: true },
  };

  static styles = css`
    :host {
      display: block;
      max-width: 960px;
      margin: 0 auto;
    }

    /* --- header. --- */
    .header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: var(--sp-4);
      margin-bottom: var(--sp-8);
    }
    .header-text h1 {
      font-family: var(--font-heading);
      font-size: var(--text-4xl);
      color: var(--text-primary);
      margin-bottom: var(--sp-1);
    }
    .header-text p {
      color: var(--text-muted);
      font-size: var(--text-sm);
    }
    .run-btn {
      padding: var(--sp-2) var(--sp-5);
      font-size: var(--text-sm);
      font-weight: 600;
      font-family: var(--font-body);
      color: var(--bg-deep);
      background: var(--accent);
      border: none;
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
      white-space: nowrap;
      flex-shrink: 0;
    }
    .run-btn:hover {
      background: var(--accent-hover);
      box-shadow: var(--shadow-glow);
    }
    .run-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      box-shadow: none;
    }

    /* --- stats bar. --- */
    .stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-4);
      padding: var(--sp-4) var(--sp-5);
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
    }
    .stat-count {
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .stat-label {
      color: var(--text-muted);
      font-size: var(--text-xs);
    }

    /* --- section. --- */
    .section {
      margin-bottom: var(--sp-4);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      overflow: hidden;
    }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--sp-3) var(--sp-5);
      background: var(--bg-surface);
      cursor: pointer;
      user-select: none;
      transition: background var(--duration-fast) var(--ease-out);
    }
    .section-header:hover {
      background: var(--bg-card);
    }
    .section-title {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      font-family: var(--font-heading);
      font-size: var(--text-lg);
    }
    .section-chevron {
      font-size: var(--text-sm);
      color: var(--text-muted);
      transition: transform var(--duration-fast) var(--ease-out);
    }
    .section-chevron.open {
      transform: rotate(90deg);
    }
    .section-body {
      display: none;
      padding: var(--sp-3);
    }
    .section-body.open {
      display: block;
    }

    /* --- issue badges in headers. --- */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 600;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-error   { background: oklch(65% 0.15 25 / 0.15);  color: var(--error); }
    .badge-warning { background: oklch(75% 0.14 70 / 0.15);  color: var(--warning); }
    .badge-info    { background: oklch(70% 0.10 250 / 0.15); color: var(--info); }

    /* --- issue card. --- */
    .issue-card {
      display: flex;
      align-items: flex-start;
      gap: var(--sp-3);
      padding: var(--sp-3) var(--sp-4);
      background: var(--bg-card);
      border-radius: var(--radius-sm);
      margin-bottom: var(--sp-2);
      transition: background var(--duration-fast) var(--ease-out);
    }
    .issue-card:last-child { margin-bottom: 0; }
    .issue-card:hover {
      background: var(--bg-card-hover);
    }
    .issue-indicator {
      width: 4px;
      border-radius: 2px;
      align-self: stretch;
      flex-shrink: 0;
    }
    .issue-indicator.error   { background: var(--error); }
    .issue-indicator.warning { background: var(--warning); }
    .issue-indicator.info    { background: var(--info); }

    .issue-content {
      flex: 1;
      min-width: 0;
    }
    .issue-message {
      font-size: var(--text-sm);
      color: var(--text-primary);
      line-height: 1.5;
      margin-bottom: var(--sp-1);
    }
    .issue-page {
      font-size: var(--text-xs);
      color: var(--link);
      text-decoration: none;
      transition: color var(--duration-fast) var(--ease-out);
    }
    .issue-page:hover {
      color: var(--link-hover);
    }

    /* --- states. --- */
    .loading {
      text-align: center;
      padding: var(--sp-16) var(--sp-8);
      color: var(--text-muted);
    }
    .error-msg {
      text-align: center;
      padding: var(--sp-8);
      color: var(--error);
    }
    .empty-state {
      text-align: center;
      padding: var(--sp-16) var(--sp-8);
      color: var(--text-muted);
    }
    .empty-icon {
      font-size: 3rem;
      margin-bottom: var(--sp-4);
      opacity: 0.4;
    }
    .success-state {
      text-align: center;
      padding: var(--sp-12) var(--sp-8);
      color: var(--success);
      font-size: var(--text-lg);
    }

    /* --- bodyless section. --- */
    .bodyless-section {
      margin-bottom: var(--sp-4);
      border: 1px solid oklch(65% 0.15 25 / 0.25);
      border-radius: var(--radius-md);
      overflow: hidden;
    }
    .bodyless-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--sp-3) var(--sp-5);
      background: oklch(65% 0.15 25 / 0.06);
      cursor: pointer;
      user-select: none;
    }
    .bodyless-header:hover {
      background: oklch(65% 0.15 25 / 0.1);
    }
    .bodyless-actions {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
    }

    .btn-delete {
      padding: var(--sp-1) var(--sp-3);
      font-size: var(--text-xs);
      font-weight: 500;
      font-family: var(--font-body);
      color: var(--error);
      background: oklch(65% 0.15 25 / 0.1);
      border: 1px solid oklch(65% 0.15 25 / 0.25);
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
    }
    .btn-delete:hover:not(:disabled) {
      background: oklch(65% 0.15 25 / 0.2);
    }
    .btn-delete:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .btn-delete-all {
      padding: var(--sp-2) var(--sp-4);
      font-size: var(--text-xs);
      font-weight: 600;
    }
    .issue-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--sp-3);
    }
  `;

  constructor() {
    super();
    this._loading = false;
    this._error = null;
    this._deleting = new Set();

    // restore last results from session storage so tab-switching keeps state.
    const cached = sessionStorage.getItem("sb_lint");
    if (cached) {
      try {
        const parsed = JSON.parse(cached);
        this._results = parsed.results;
        this._collapsed = parsed.collapsed;
        return;
      } catch { /* fall through */ }
    }
    this._results = null;
    this._collapsed = { errors: false, warnings: true, info: true, bodyless: false };
  }

  _persistResults() {
    if (this._results) {
      sessionStorage.setItem("sb_lint", JSON.stringify({
        results: this._results,
        collapsed: this._collapsed,
      }));
    }
  }

  async _runLint() {
    this._loading = true;
    this._error = null;
    this._results = null;

    try {
      const data = await lint.run();
      this._results = data;
      // auto-expand errors if any exist.
      this._collapsed = {
        errors: false,
        warnings: this._issueCount(data, "error") === 0,
        info: true,
        bodyless: false,
      };
      this._persistResults();
    } catch (err) {
      this._error = err.message;
    } finally {
      this._loading = false;
    }
  }

  _toggle(section) {
    this._collapsed = {
      ...this._collapsed,
      [section]: !this._collapsed[section],
    };
    this._persistResults();
  }

  _categorize(results) {
    return {
      errors: results.errors || [],
      warnings: results.warnings || [],
      info: results.info || [],
    };
  }

  _issueCount(results, level) {
    if (!results) return 0;
    const { errors, warnings, info } = this._categorize(results);
    if (level === "error") return errors.length;
    if (level === "warning") return warnings.length;
    return info.length;
  }

  _totalPages() {
    if (!this._results) return 0;
    return this._results.stats?.total_pages || 0;
  }

  _pageHref(pageName) {
    return `#/page/_/${encodeURIComponent(pageName || "")}`;
  }

  async _deleteOne(page, subdir) {
    const name = page;
    this._deleting = new Set([...this._deleting, name]);
    try {
      await lint.deletePages([{ name, subdir }]);
      // remove from results.
      const bodyless = (this._results.bodyless || []).filter((b) => b.page !== name);
      const warnings = (this._results.warnings || []).filter((w) => w.page !== name);
      this._results = { ...this._results, bodyless, warnings };
      this._persistResults();
    } catch (e) {
      this._error = e.message;
    } finally {
      const next = new Set(this._deleting);
      next.delete(name);
      this._deleting = next;
    }
  }

  async _deleteAllBodyless() {
    const bodyless = this._results?.bodyless || [];
    if (!bodyless.length) return;
    if (!confirm(`Delete ${bodyless.length} body-less pages? This cannot be undone.`)) return;

    const pages = bodyless.map((b) => ({ name: b.page, subdir: b.subdir }));
    const names = new Set(pages.map((p) => p.name));
    this._deleting = new Set([...this._deleting, ...names]);
    try {
      const result = await lint.deletePages(pages);
      const deleted = new Set(result.deleted || []);
      const remaining = bodyless.filter((b) => !deleted.has(b.page));
      const warnings = (this._results.warnings || []).filter(
        (w) => !w.bodyless || !deleted.has(w.page),
      );
      this._results = { ...this._results, bodyless: remaining, warnings };
      this._persistResults();
    } catch (e) {
      this._error = e.message;
    } finally {
      this._deleting = new Set();
    }
  }

  render() {
    return html`
      <div class="header">
        <div class="header-text">
          <h1>Wiki Health</h1>
          <p>Run lint checks to find broken links, orphans, and frontmatter issues</p>
        </div>
        <button
          class="run-btn"
          ?disabled=${this._loading}
          @click=${this._runLint}
        >${this._loading ? "Running..." : "Run Health Check"}</button>
      </div>

      ${this._error ? html`<div class="error-msg">${this._error}</div>` : ""}

      ${this._loading ? html`<div class="loading">Analyzing wiki health...</div>` : ""}

      ${this._results && !this._loading ? this._renderResults() : ""}

      ${!this._results && !this._loading && !this._error
        ? html`
            <div class="empty-state">
              <div class="empty-icon">&#129658;</div>
              <div>Click "Run Health Check" to analyze wiki integrity</div>
            </div>
          `
        : ""
      }
    `;
  }

  _renderResults() {
    const { errors, warnings, info } = this._categorize(this._results);
    const bodyless = this._results.bodyless || [];
    const total = errors.length + warnings.length + info.length;

    return html`
      <div class="stats-bar">
        <div class="stat">
          <span class="stat-count">${this._totalPages()}</span>
          <span class="stat-label">pages</span>
        </div>
        <div class="stat">
          <span class="stat-count" style="color: var(--error)">${errors.length}</span>
          <span class="stat-label">errors</span>
        </div>
        <div class="stat">
          <span class="stat-count" style="color: var(--warning)">${warnings.length}</span>
          <span class="stat-label">warnings</span>
        </div>
        <div class="stat">
          <span class="stat-count" style="color: var(--info)">${info.length}</span>
          <span class="stat-label">info</span>
        </div>
        ${bodyless.length > 0 ? html`
          <div class="stat">
            <span class="stat-count" style="color: var(--error)">${bodyless.length}</span>
            <span class="stat-label">body-less</span>
          </div>
        ` : ""}
      </div>

      ${bodyless.length > 0 ? this._renderBodyless(bodyless) : ""}

      ${total === 0 && bodyless.length === 0
        ? html`<div class="success-state">All checks passed. Wiki is healthy.</div>`
        : html`
            ${this._renderSection("errors", "Errors", errors, "error")}
            ${this._renderSection("warnings", "Warnings", warnings, "warning")}
            ${this._renderSection("info", "Info", info, "info")}
          `
      }
    `;
  }

  _renderBodyless(bodyless) {
    const open = !this._collapsed.bodyless;

    return html`
      <div class="bodyless-section">
        <div class="bodyless-header" @click=${() => this._toggle("bodyless")}>
          <div class="section-title">
            <span class="badge badge-error">Body-less Pages (${bodyless.length})</span>
          </div>
          <div class="bodyless-actions">
            <button class="btn-delete btn-delete-all"
              ?disabled=${this._deleting.size > 0}
              @click=${(e) => { e.stopPropagation(); this._deleteAllBodyless(); }}>
              ${this._deleting.size > 0 ? "Deleting..." : `Delete All (${bodyless.length})`}
            </button>
            <span class="section-chevron ${open ? "open" : ""}">&#9654;</span>
          </div>
        </div>
        <div class="section-body ${open ? "open" : ""}">
          ${bodyless.map((issue) => html`
            <div class="issue-card">
              <div class="issue-indicator error"></div>
              <div class="issue-content">
                <div class="issue-row">
                  <div>
                    <div class="issue-message">${issue.message}</div>
                    <a class="issue-page" href=${this._pageHref(issue.page)}>${issue.subdir}/${issue.page}</a>
                  </div>
                  <button class="btn-delete"
                    ?disabled=${this._deleting.has(issue.page)}
                    @click=${() => this._deleteOne(issue.page, issue.subdir)}>
                    ${this._deleting.has(issue.page) ? "..." : "Delete"}
                  </button>
                </div>
              </div>
            </div>
          `)}
        </div>
      </div>
    `;
  }

  _renderSection(key, label, issues, level) {
    if (issues.length === 0) return "";

    const open = !this._collapsed[key];

    return html`
      <div class="section">
        <div class="section-header" @click=${() => this._toggle(key)}>
          <div class="section-title">
            <span class="badge badge-${level}">${label} (${issues.length})</span>
          </div>
          <span class="section-chevron ${open ? "open" : ""}">&#9654;</span>
        </div>
        <div class="section-body ${open ? "open" : ""}">
          ${issues.map((issue) => this._renderIssue(issue, level))}
        </div>
      </div>
    `;
  }

  _renderIssue(issue, level) {
    const message = issue.message || issue.msg || issue.detail || "";
    const page = issue.page || issue.file || "";

    return html`
      <div class="issue-card">
        <div class="issue-indicator ${level}"></div>
        <div class="issue-content">
          <div class="issue-message">${message}</div>
          ${page
            ? html`<a class="issue-page" href=${this._pageHref(page)}>${page}</a>`
            : ""
          }
        </div>
      </div>
    `;
  }
}

customElements.define("lint-panel", LintPanel);
