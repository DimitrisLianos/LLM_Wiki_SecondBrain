import { LitElement, html, css } from "lit";
import { dedup } from "../lib/api.js";

/**
 * dedup merge panel with user-selectable canonicals.
 *
 * when a merge plan is generated, each cluster shows ALL pages as
 * radio-button candidates. the user picks which page to keep as
 * canonical for each cluster, then applies.
 */
export class DedupPanel extends LitElement {
  static properties = {
    _plan:       { state: true },
    _applied:    { state: true },
    _loading:    { state: true },
    _applying:   { state: true },
    _error:      { state: true },
    _selections: { state: true },
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

    .action-row {
      display: flex;
      gap: var(--sp-3);
      flex-shrink: 0;
    }

    .btn {
      padding: var(--sp-2) var(--sp-5);
      font-size: var(--text-sm);
      font-weight: 600;
      font-family: var(--font-body);
      border: none;
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
      white-space: nowrap;
    }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary {
      color: var(--bg-deep);
      background: var(--accent);
    }
    .btn-primary:hover:not(:disabled) {
      background: var(--accent-hover);
      box-shadow: var(--shadow-glow);
    }
    .btn-danger {
      color: var(--text-primary);
      background: oklch(45% 0.12 25);
      border: 1px solid oklch(55% 0.14 25 / 0.4);
    }
    .btn-danger:hover:not(:disabled) {
      background: oklch(50% 0.14 25);
    }

    /* --- stats bar. --- */
    .stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-5);
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
      color: var(--accent);
    }
    .stat-label {
      color: var(--text-muted);
      font-size: var(--text-xs);
    }

    /* --- cluster. --- */
    .cluster-list {
      display: flex;
      flex-direction: column;
      gap: var(--sp-4);
    }
    .cluster-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-5);
      transition: background var(--duration-fast) var(--ease-out);
    }
    .cluster-card:hover {
      background: var(--bg-card-hover);
    }

    .cluster-header {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      margin-bottom: var(--sp-4);
      padding-bottom: var(--sp-3);
      border-bottom: 1px solid var(--border);
    }
    .cluster-badge {
      font-size: var(--text-xs);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
    }
    .cluster-group-key {
      font-size: var(--text-xs);
      color: var(--text-muted);
      font-family: var(--font-mono);
    }

    /* --- candidate list with radio buttons. --- */
    .candidate-label-text {
      font-size: var(--text-xs);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--accent);
      margin-bottom: var(--sp-3);
    }
    .candidates-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
    }
    .candidate-item {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-2) var(--sp-3);
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out);
    }
    .candidate-item:hover {
      background: oklch(25% 0.02 260 / 0.5);
    }
    .candidate-item.selected {
      background: oklch(75% 0.15 70 / 0.06);
    }

    /* custom radio button. */
    .radio-outer {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      border: 2px solid var(--border-light);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: border-color var(--duration-fast) var(--ease-out);
    }
    .candidate-item.selected .radio-outer {
      border-color: var(--accent);
    }
    .radio-inner {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: transparent;
      transition: background var(--duration-fast) var(--ease-out);
    }
    .candidate-item.selected .radio-inner {
      background: var(--accent);
    }

    .candidate-name {
      font-size: var(--text-sm);
      color: var(--link);
      text-decoration: none;
      flex: 1;
      transition: color var(--duration-fast) var(--ease-out);
    }
    .candidate-name:hover {
      color: var(--link-hover);
    }
    .candidate-tag {
      font-size: var(--text-xs);
      padding: 1px var(--sp-2);
      border-radius: var(--radius-sm);
      font-weight: 500;
    }
    .tag-keep {
      background: oklch(70% 0.12 165 / 0.15);
      color: var(--success);
    }
    .tag-merge {
      background: oklch(75% 0.14 70 / 0.10);
      color: var(--text-muted);
    }
    .tag-default {
      background: oklch(72% 0.12 250 / 0.15);
      color: oklch(72% 0.12 250);
      font-size: 10px;
      letter-spacing: 0.04em;
    }

    /* --- result summary. --- */
    .result-card {
      background: oklch(70% 0.12 165 / 0.08);
      border: 1px solid oklch(70% 0.12 165 / 0.25);
      border-radius: var(--radius-md);
      padding: var(--sp-6);
      margin-top: var(--sp-6);
      text-align: center;
    }
    .result-card h3 {
      font-family: var(--font-heading);
      font-size: var(--text-2xl);
      color: var(--success);
      margin-bottom: var(--sp-3);
    }
    .result-card p {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.6;
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
      background: oklch(65% 0.15 25 / 0.06);
      border: 1px solid oklch(65% 0.15 25 / 0.15);
      border-radius: var(--radius-md);
      margin-bottom: var(--sp-4);
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
    .no-dupes {
      text-align: center;
      padding: var(--sp-12) var(--sp-8);
      color: var(--success);
      font-size: var(--text-lg);
    }
  `;

  constructor() {
    super();
    this._plan = null;
    this._applied = null;
    this._loading = false;
    this._applying = false;
    this._error = null;
    /** @type {Object<string, string>} group_key → selected canonical */
    this._selections = {};
  }

  async _generatePlan() {
    this._loading = true;
    this._error = null;
    this._plan = null;
    this._applied = null;
    this._selections = {};

    try {
      const data = await dedup.plan();
      this._plan = data;

      // initialise selections with the script's default canonicals.
      const sel = {};
      for (const c of this._clusters(data)) {
        const key = c.group_key || c.canonical || "";
        sel[key] = c.canonical;
      }
      this._selections = sel;
    } catch (err) {
      this._error = err.message;
    } finally {
      this._loading = false;
    }
  }

  async _applyMerges() {
    const clusters = this._clusters();
    if (clusters.length === 0) return;

    const ok = window.confirm(
      "This will merge duplicate pages. Merged pages will be deleted and their content folded into the canonical page you selected. Continue?"
    );
    if (!ok) return;

    this._applying = true;
    this._error = null;

    try {
      // build clusters with user-selected canonicals.
      const payload = clusters.map((c) => {
        const key = c.group_key || c.canonical || "";
        const selectedCanonical = this._selections[key] || c.canonical;
        const candidates = c.candidates || [c.canonical, ...(c.merge_from || [])];
        const mergeFrom = candidates.filter((name) => name !== selectedCanonical);

        return {
          canonical: selectedCanonical,
          merge_from: mergeFrom,
        };
      });

      const result = await dedup.applySelected(payload);
      this._applied = result;
      this._plan = null;
      this._selections = {};
    } catch (err) {
      this._error = err.message;
    } finally {
      this._applying = false;
    }
  }

  _clusters(plan) {
    const p = plan || this._plan;
    if (!p) return [];
    return p.clusters || p.groups || [];
  }

  _totalMerges() {
    return this._clusters().reduce(
      (sum, c) => sum + (c.merge_from || c.duplicates || []).length,
      0,
    );
  }

  _selectCanonical(groupKey, candidateName) {
    this._selections = { ...this._selections, [groupKey]: candidateName };
  }

  _pageHref(name) {
    return `#/page/_/${encodeURIComponent(name || "")}`;
  }

  /* ------------------------------------------------------------------ */
  /*  render.                                                            */
  /* ------------------------------------------------------------------ */

  render() {
    const clusters = this._clusters();
    const hasPlan = this._plan && clusters.length > 0;

    return html`
      <div class="header">
        <div class="header-text">
          <h1>Duplicate Merge</h1>
          <p>Detect and merge duplicate wiki pages. Choose which page to keep for each cluster.</p>
        </div>
        <div class="action-row">
          <button
            class="btn btn-primary"
            ?disabled=${this._loading || this._applying}
            @click=${this._generatePlan}
          >${this._loading ? "Scanning\u2026" : "Generate Merge Plan"}</button>
          ${hasPlan
            ? html`
                <button
                  class="btn btn-danger"
                  ?disabled=${this._applying}
                  @click=${this._applyMerges}
                >${this._applying ? "Applying\u2026" : "Apply Merges"}</button>
              `
            : ""
          }
        </div>
      </div>

      ${this._error ? html`<div class="error-msg">${this._error}</div>` : ""}

      ${this._loading ? html`<div class="loading">Analyzing duplicates\u2026</div>` : ""}

      ${this._plan && !this._loading ? this._renderPlan(clusters) : ""}

      ${this._applied ? this._renderResult() : ""}

      ${!this._plan && !this._applied && !this._loading && !this._error
        ? html`
            <div class="empty-state">
              <div class="empty-icon">&#128279;</div>
              <div>Click "Generate Merge Plan" to scan for duplicates</div>
            </div>
          `
        : ""
      }
    `;
  }

  _renderPlan(clusters) {
    if (clusters.length === 0) {
      return html`<div class="no-dupes">No duplicate clusters found. Wiki is clean.</div>`;
    }

    return html`
      <div class="stats-bar">
        <div class="stat">
          <span class="stat-count">${clusters.length}</span>
          <span class="stat-label">clusters</span>
        </div>
        <div class="stat">
          <span class="stat-count">${this._totalMerges()}</span>
          <span class="stat-label">pages to merge</span>
        </div>
      </div>

      <div class="cluster-list">
        ${clusters.map((cluster) => this._renderCluster(cluster))}
      </div>
    `;
  }

  _renderCluster(cluster) {
    const groupKey = cluster.group_key || cluster.canonical || "";
    const rawCandidates = cluster.candidates || [cluster.canonical, ...(cluster.merge_from || [])];
    // deduplicate to prevent two radio buttons matching the same name.
    const candidates = [...new Set(rawCandidates)];
    const defaultCanonical = cluster.canonical || "";
    const selectedCanonical = this._selections[groupKey] || defaultCanonical;

    return html`
      <div class="cluster-card">
        <div class="cluster-header">
          <span class="cluster-badge">${cluster.subdirs || "wiki"}</span>
          <span class="cluster-group-key">${groupKey}</span>
        </div>

        <div class="candidate-label-text">
          Select which page to keep as canonical:
        </div>

        <ul class="candidates-list" role="radiogroup" aria-label="Select canonical page">
          ${candidates.map((name) => {
            const isSelected = name === selectedCanonical;
            const isDefault = name === defaultCanonical;
            return html`
              <li
                class="candidate-item ${isSelected ? "selected" : ""}"
                role="radio"
                tabindex="0"
                aria-checked="${isSelected}"
                @click=${() => this._selectCanonical(groupKey, name)}
                @keydown=${(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    this._selectCanonical(groupKey, name);
                  }
                }}
              >
                <div class="radio-outer">
                  <div class="radio-inner"></div>
                </div>
                <a
                  class="candidate-name"
                  href=${this._pageHref(name)}
                  @click=${(e) => e.stopPropagation()}
                >${name}</a>
                ${isSelected
                  ? html`<span class="candidate-tag tag-keep">keep</span>`
                  : html`<span class="candidate-tag tag-merge">merge</span>`
                }
                ${isDefault && !isSelected
                  ? html`<span class="candidate-tag tag-default">suggested</span>`
                  : ""
                }
              </li>
            `;
          })}
        </ul>
      </div>
    `;
  }

  _renderResult() {
    const r = this._applied;
    const merged = r.merged || r.clusters_merged || 0;
    const deleted = r.deleted || r.pages_deleted || 0;
    const message = r.message || r.summary || `Merged ${merged} clusters, removed ${deleted} duplicate pages.`;

    return html`
      <div class="result-card">
        <h3>Merge Complete</h3>
        <p>${message}</p>
      </div>
    `;
  }
}

customElements.define("dedup-panel", DedupPanel);
