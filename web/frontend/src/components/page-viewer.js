import { LitElement, html, css } from "lit";
import { unsafeHTML } from "lit-html/directives/unsafe-html.js";
import { wiki } from "../lib/api.js";
import { renderMarkdown, parseFrontmatter } from "../lib/markdown.js";
import { getLastPanel } from "../lib/router.js";

const SUBDIRS = ["sources", "entities", "concepts", "synthesis"];

export class PageViewer extends LitElement {
  static properties = {
    subdir:  { type: String },
    name:    { type: String },
    _page:   { state: true },
    _fm:     { state: true },
    _html:   { state: true },
    _loading: { state: true },
    _error:   { state: true },
  };

  static styles = css`
    :host {
      display: block;
      max-width: 1100px;
      margin: 0 auto;
    }

    /* --- back nav. --- */
    .back-btn {
      display: inline-flex;
      align-items: center;
      gap: var(--sp-2);
      padding: var(--sp-2) var(--sp-3);
      font-size: var(--text-sm);
      font-family: var(--font-body);
      color: var(--text-secondary);
      background: transparent;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
      margin-bottom: var(--sp-6);
    }
    .back-btn:hover {
      background: var(--bg-card);
      color: var(--text-primary);
      border-color: var(--border-light);
    }

    /* --- layout. --- */
    .page-layout {
      display: grid;
      grid-template-columns: 1fr 260px;
      gap: var(--sp-8);
      align-items: start;
    }
    @media (max-width: 860px) {
      .page-layout {
        grid-template-columns: 1fr;
      }
    }

    /* --- frontmatter badges. --- */
    .meta-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--sp-2);
      margin-bottom: var(--sp-6);
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
    }
    .badge-sources   { background: oklch(70% 0.12 250 / 0.15); color: var(--color-sources); }
    .badge-entities  { background: oklch(70% 0.14 165 / 0.15); color: var(--color-entities); }
    .badge-concepts  { background: oklch(72% 0.12 300 / 0.15); color: var(--color-concepts); }
    .badge-synthesis { background: oklch(75% 0.14 70 / 0.15);  color: var(--color-synthesis); }

    .badge-type {
      background: oklch(75% 0.15 70 / 0.12);
      color: var(--accent);
    }
    .badge-tag {
      background: oklch(28% 0.015 260 / 0.6);
      color: var(--text-secondary);
    }
    .badge-source-ref {
      background: oklch(70% 0.12 250 / 0.10);
      color: var(--color-sources);
    }
    .badge-date {
      background: transparent;
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      font-size: var(--text-xs);
      text-transform: none;
      letter-spacing: normal;
    }
    .meta-sep {
      width: 1px;
      height: 16px;
      background: var(--border);
      flex-shrink: 0;
    }

    /* --- content area. --- */
    .content-area {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: var(--sp-8) var(--sp-8) var(--sp-10);
      min-width: 0;
    }

    /* markdown styles */
    .md-body h1 {
      font-family: var(--font-heading);
      font-size: var(--text-3xl);
      color: var(--text-primary);
      margin-bottom: var(--sp-6);
      padding-bottom: var(--sp-3);
      border-bottom: 1px solid var(--border);
    }
    .md-body h2 {
      font-family: var(--font-heading);
      font-size: var(--text-2xl);
      color: var(--text-primary);
      margin-top: var(--sp-8);
      margin-bottom: var(--sp-4);
    }
    .md-body h3 {
      font-family: var(--font-heading);
      font-size: var(--text-xl);
      color: var(--text-primary);
      margin-top: var(--sp-6);
      margin-bottom: var(--sp-3);
    }
    .md-body h4 {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-secondary);
      margin-top: var(--sp-5);
      margin-bottom: var(--sp-2);
    }
    .md-body p {
      margin-bottom: var(--sp-4);
      line-height: 1.75;
      color: var(--text-primary);
    }
    .md-body ul, .md-body ol {
      margin-bottom: var(--sp-4);
      padding-left: var(--sp-6);
    }
    .md-body li {
      margin-bottom: var(--sp-1);
      line-height: 1.65;
    }
    .md-body code {
      font-family: var(--font-mono);
      font-size: 0.88em;
      background: var(--bg-input);
      padding: 2px 6px;
      border-radius: var(--radius-sm);
    }
    .md-body pre {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-4);
      margin-bottom: var(--sp-4);
      overflow-x: auto;
    }
    .md-body pre code {
      background: none;
      padding: 0;
    }
    .md-body a {
      color: var(--link);
      text-decoration: none;
      transition: color var(--duration-fast) var(--ease-out);
    }
    .md-body a:hover {
      color: var(--link-hover);
    }
    .md-body a.wikilink {
      color: var(--link);
      border-bottom: 1px dashed oklch(72% 0.12 250 / 0.4);
    }
    .md-body a.wikilink:hover {
      color: var(--link-hover);
      border-bottom-color: var(--link-hover);
    }
    .md-body blockquote {
      border-left: 3px solid var(--accent-dim);
      padding: var(--sp-2) var(--sp-4);
      margin: var(--sp-4) 0;
      color: var(--text-secondary);
      background: oklch(17% 0.015 260 / 0.5);
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    }
    .md-body hr {
      border: none;
      height: 1px;
      background: var(--border);
      margin: var(--sp-8) 0;
    }
    .md-body table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: var(--sp-4);
      font-size: var(--text-sm);
    }
    .md-body th, .md-body td {
      padding: var(--sp-2) var(--sp-3);
      border: 1px solid var(--border);
      text-align: left;
    }
    .md-body th {
      background: var(--bg-surface);
      font-weight: 600;
    }

    /* --- sidebar. --- */
    .sidebar {
      position: sticky;
      top: var(--sp-4);
    }
    .sidebar-section {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-4) var(--sp-5);
      margin-bottom: var(--sp-4);
    }
    .sidebar-heading {
      font-family: var(--font-heading);
      font-size: var(--text-base);
      color: var(--text-secondary);
      margin-bottom: var(--sp-3);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: var(--text-xs);
    }
    .link-list {
      list-style: none;
      padding: 0;
      margin: 0;
    }
    .link-list li {
      margin-bottom: var(--sp-1);
    }
    .link-list a {
      display: block;
      padding: var(--sp-1) 0;
      font-size: var(--text-sm);
      color: var(--link);
      text-decoration: none;
      transition: color var(--duration-fast) var(--ease-out);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .link-list a:hover {
      color: var(--link-hover);
    }
    .no-links {
      font-size: var(--text-xs);
      color: var(--text-muted);
      font-style: italic;
    }

    /* --- states. --- */
    .loading, .error {
      text-align: center;
      padding: var(--sp-16) var(--sp-8);
      color: var(--text-muted);
    }
    .error { color: var(--error); }
  `;

  constructor() {
    super();
    this.subdir = "";
    this.name = "";
    this._page = null;
    this._fm = {};
    this._html = "";
    this._loading = true;
    this._error = null;
  }

  willUpdate(changed) {
    if (changed.has("subdir") || changed.has("name")) {
      this._loadPage();
    }
  }

  async _loadPage() {
    if (!this.name) return;

    this._loading = true;
    this._error = null;

    try {
      if (this.subdir && this.subdir !== "_") {
        this._page = await wiki.page(this.subdir, this.name);
      } else {
        this._page = await this._findInAnySubdir(this.name);
      }

      const content = this._page.content || this._page.body || "";
      this._fm = parseFrontmatter(content);
      this._html = renderMarkdown(content);
    } catch (err) {
      this._error = err.message;
      this._page = null;
    } finally {
      this._loading = false;
    }
  }

  async _findInAnySubdir(name) {
    for (const sub of SUBDIRS) {
      try {
        return await wiki.page(sub, name);
      } catch {
        /* not in this subdir, continue */
      }
    }
    throw new Error(`Page "${name}" not found in any wiki subdirectory.`);
  }

  _wikilinkHref(pageName) {
    return `#/page/_/${encodeURIComponent(pageName)}`;
  }

  _subdirBadgeClass(type) {
    const t = (type || "").toLowerCase();
    if (SUBDIRS.includes(t)) return `badge badge-${t}`;
    return "badge badge-type";
  }

  _goBack() {
    window.location.hash = getLastPanel();
  }

  get _backLabel() {
    const panel = getLastPanel();
    const labels = {
      "#/dedup": "Back to Dedup",
      "#/lint": "Back to Health Check",
      "#/search": "Back to Search",
      "#/graph": "Back to Graph",
    };
    return labels[panel] || "Back to Browse";
  }

  render() {
    if (this._loading) {
      return html`<div class="loading">Loading page...</div>`;
    }
    if (this._error) {
      return html`
        <button class="back-btn" @click=${() => this._goBack()}>
          &#8592; ${this._backLabel}
        </button>
        <div class="error">${this._error}</div>
      `;
    }

    const fm = this._fm;
    const page = this._page || {};
    const inbound = page.inbound_links || [];
    const outbound = page.outbound_links || [];
    const fmType = fm.type || page.type || "";
    const fmTags = Array.isArray(fm.tags) ? fm.tags : [];
    const fmSources = Array.isArray(fm.sources) ? fm.sources : [];
    const created = fm.created || page.created || "";
    const updated = fm.updated || page.updated || "";

    return html`
      <button class="back-btn" @click=${() => window.location.hash = "#/browse"}>
        &#8592; Back to Browse
      </button>

      <div class="meta-bar">
        ${fmType ? html`<span class=${this._subdirBadgeClass(fmType)}>${fmType}</span>` : ""}
        ${fmTags.map((t) => html`<span class="badge badge-tag">${t}</span>`)}
        ${fmSources.length > 0 ? html`<span class="meta-sep"></span>` : ""}
        ${fmSources.map((s) => html`
          <a class="badge badge-source-ref" href=${this._wikilinkHref(s)}>${s}</a>
        `)}
        ${created || updated ? html`<span class="meta-sep"></span>` : ""}
        ${created ? html`<span class="badge badge-date">created ${created}</span>` : ""}
        ${updated ? html`<span class="badge badge-date">updated ${updated}</span>` : ""}
      </div>

      <div class="page-layout">
        <div class="content-area">
          <div class="md-body">${unsafeHTML(this._html)}</div>
        </div>

        <aside class="sidebar">
          <div class="sidebar-section">
            <div class="sidebar-heading">Outbound Links</div>
            ${outbound.length > 0
              ? html`
                  <ul class="link-list">
                    ${outbound.map((link) => html`
                      <li><a href=${this._wikilinkHref(link)}>${link}</a></li>
                    `)}
                  </ul>
                `
              : html`<div class="no-links">None</div>`
            }
          </div>

          <div class="sidebar-section">
            <div class="sidebar-heading">Inbound Links</div>
            ${inbound.length > 0
              ? html`
                  <ul class="link-list">
                    ${inbound.map((link) => html`
                      <li><a href=${this._wikilinkHref(link)}>${link}</a></li>
                    `)}
                  </ul>
                `
              : html`<div class="no-links">None</div>`
            }
          </div>
        </aside>
      </div>
    `;
  }
}

customElements.define("page-viewer", PageViewer);
