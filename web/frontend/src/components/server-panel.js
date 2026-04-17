import { LitElement, html, css } from "lit";
import { admin, server } from "../lib/api.js";

/**
 * @element server-panel
 * Server control dashboard for LLM and Embedding servers.
 * Shows status cards, start/stop controls, and editable configuration.
 */
export class ServerPanel extends LitElement {
  static properties = {
    _llmStatus:     { state: true },
    _embedStatus:   { state: true },
    _config:        { state: true },
    _configDraft:   { state: true },
    _configOpen:    { state: true },
    _loading:       { state: true },
    _actionTarget:  { state: true },
    _error:         { state: true },
    _memEstimate:   { state: true },
    _starting:      { state: true },  // Set<string> of targets currently starting up.
    _logsTarget:    { state: true },  // "llm" | "embed" | null
    _logsLines:     { state: true },
    _logsLoading:   { state: true },
    // --- danger zone. ---
    _dangerOpen:        { state: true },
    _resetPreviews:     { state: true }, // { wiki: {...}, full: {...} } | null
    _resetModalMode:    { state: true }, // "wiki" | "full" | null
    _resetConfirmInput: { state: true },
    _resetting:         { state: true },
    _resetResult:       { state: true }, // { ok, message } | null
  };

  static styles = css`
    /* --- layout. --- */
    :host {
      display: block;
      max-width: 960px;
      margin: 0 auto;
    }

    .panel-header {
      margin-bottom: var(--sp-8);
    }
    .panel-header h1 {
      font-family: var(--font-heading);
      font-size: var(--text-3xl);
      color: var(--text-primary);
      margin-bottom: var(--sp-2);
    }
    .panel-header p {
      font-size: var(--text-sm);
      color: var(--text-muted);
      line-height: 1.5;
    }

    /* --- error banner. --- */
    .error-banner {
      background: oklch(65% 0.15 25 / 0.12);
      border: 1px solid oklch(65% 0.15 25 / 0.3);
      border-radius: var(--radius-md);
      padding: var(--sp-3) var(--sp-4);
      margin-bottom: var(--sp-6);
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      font-size: var(--text-sm);
      color: var(--error);
    }
    .error-banner button {
      margin-left: auto;
      background: none;
      border: none;
      color: var(--error);
      cursor: pointer;
      font-size: var(--text-lg);
      line-height: 1;
      padding: var(--sp-1);
    }

    /* --- status cards grid. --- */
    .cards-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--sp-5);
      margin-bottom: var(--sp-6);
    }
    @media (max-width: 640px) {
      .cards-row { grid-template-columns: 1fr; }
    }

    .status-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: var(--sp-6);
      transition: border-color var(--duration-fast) var(--ease-out),
                  box-shadow var(--duration-fast) var(--ease-out);
    }
    .status-card:hover {
      border-color: var(--border-light);
      box-shadow: var(--shadow-md);
    }
    .status-card.running {
      border-color: oklch(70% 0.12 165 / 0.3);
    }
    .status-card.starting {
      border-color: oklch(75% 0.15 70 / 0.3);
    }

    .status-text.starting { color: var(--warning); }
    .status-dot.starting {
      background: var(--warning);
      box-shadow: 0 0 8px var(--warning);
      animation: blink 1.2s ease-in-out infinite;
    }
    @keyframes blink {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.3; }
    }

    .btn-restart {
      background: oklch(75% 0.15 70 / 0.12);
      color: var(--accent);
      border-color: oklch(75% 0.15 70 / 0.25);
    }
    .btn-restart:hover:not(:disabled) {
      background: oklch(75% 0.15 70 / 0.2);
    }

    .card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--sp-4);
    }
    .card-title {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-primary);
    }

    .status-indicator {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      transition: background var(--duration-fast) var(--ease-out),
                  box-shadow var(--duration-fast) var(--ease-out);
    }
    .status-dot.on {
      background: var(--success);
      box-shadow: 0 0 8px var(--success);
    }
    .status-dot.off {
      background: var(--error);
    }
    .status-text.on { color: var(--success); }
    .status-text.off { color: var(--error); }

    .card-meta {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
      margin-bottom: var(--sp-5);
    }
    .meta-row {
      display: flex;
      justify-content: space-between;
      font-size: var(--text-sm);
    }
    .meta-label { color: var(--text-muted); }
    .meta-value { color: var(--text-secondary); font-weight: 500; }
    .meta-value.reasoning-on { color: var(--accent, #7ab6ff); }
    .meta-value.reasoning-off { color: #f0c674; }

    .slot-bar-track {
      height: 6px;
      background: var(--bg-input);
      border-radius: 3px;
      overflow: hidden;
      margin-top: var(--sp-1);
    }
    .slot-bar-fill {
      height: 100%;
      background: var(--accent);
      border-radius: 3px;
      transition: width var(--duration-normal) var(--ease-out);
    }

    /* --- buttons. --- */
    button {
      font-family: var(--font-body);
      cursor: pointer;
      transition: all var(--duration-fast) var(--ease-out);
    }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: var(--sp-2);
      padding: var(--sp-2) var(--sp-4);
      font-size: var(--text-sm);
      font-weight: 500;
      border-radius: var(--radius-md);
      border: 1px solid transparent;
      white-space: nowrap;
    }
    .btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }

    .btn-start {
      background: oklch(70% 0.12 165 / 0.15);
      color: var(--success);
      border-color: oklch(70% 0.12 165 / 0.25);
    }
    .btn-start:hover:not(:disabled) {
      background: oklch(70% 0.12 165 / 0.25);
    }

    .btn-stop {
      background: oklch(65% 0.15 25 / 0.12);
      color: var(--error);
      border-color: oklch(65% 0.15 25 / 0.25);
    }
    .btn-stop:hover:not(:disabled) {
      background: oklch(65% 0.15 25 / 0.25);
    }

    .btn-primary {
      background: var(--accent);
      color: var(--bg-deep);
      border-color: var(--accent);
      font-weight: 600;
    }
    .btn-primary:hover:not(:disabled) {
      background: var(--accent-hover);
      box-shadow: var(--shadow-glow);
    }

    .btn-ghost {
      background: transparent;
      color: var(--text-secondary);
      border-color: var(--border);
    }
    .btn-ghost:hover:not(:disabled) {
      background: var(--bg-card);
      color: var(--text-primary);
      border-color: var(--border-light);
    }

    .card-actions {
      display: flex;
      gap: var(--sp-3);
    }

    .global-actions {
      display: flex;
      justify-content: flex-end;
      margin-bottom: var(--sp-6);
    }

    /* --- spinner. --- */
    .spinner {
      width: 14px;
      height: 14px;
      border: 2px solid transparent;
      border-top-color: currentColor;
      border-radius: 50%;
      animation: spin 0.6s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    /* --- configuration section. --- */
    .config-section {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
    }

    .config-toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding: var(--sp-5) var(--sp-6);
      background: none;
      border: none;
      color: var(--text-primary);
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out);
    }
    .config-toggle:hover {
      background: var(--bg-card-hover);
    }
    .config-toggle-icon {
      font-size: var(--text-sm);
      color: var(--text-muted);
      transition: transform var(--duration-fast) var(--ease-out);
    }
    .config-toggle-icon.open {
      transform: rotate(180deg);
    }

    .config-body {
      padding: 0 var(--sp-6) var(--sp-6);
    }

    .config-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--sp-5);
      margin-bottom: var(--sp-6);
    }
    @media (max-width: 640px) {
      .config-grid { grid-template-columns: 1fr; }
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
    }
    .field-label-row {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
    }
    .field-label {
      font-size: var(--text-sm);
      font-weight: 500;
      color: var(--text-secondary);
    }

    /* --- tooltip. --- */
    .tooltip-wrap {
      position: relative;
      display: inline-flex;
    }
    .tooltip-icon {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: help;
      transition: border-color var(--duration-fast) var(--ease-out),
                  color var(--duration-fast) var(--ease-out);
    }
    .tooltip-icon:hover {
      border-color: var(--accent-dim);
      color: var(--accent);
    }
    .tooltip-bubble {
      display: none;
      position: absolute;
      bottom: calc(100% + 8px);
      left: 50%;
      transform: translateX(-50%);
      background: var(--bg-surface);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-md);
      padding: var(--sp-3) var(--sp-4);
      font-size: var(--text-xs);
      color: var(--text-secondary);
      line-height: 1.5;
      width: 260px;
      box-shadow: var(--shadow-lg);
      z-index: 50;
      pointer-events: none;
    }
    .tooltip-bubble::after {
      content: "";
      position: absolute;
      top: 100%;
      left: 50%;
      transform: translateX(-50%);
      border: 6px solid transparent;
      border-top-color: var(--border-light);
    }
    .tooltip-wrap:hover .tooltip-bubble {
      display: block;
    }

    /* --- inputs. --- */
    input[type="number"],
    select {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-2) var(--sp-3);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      color: var(--text-primary);
      transition: border-color var(--duration-fast) var(--ease-out);
      width: 100%;
    }
    input[type="number"]:focus,
    select:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px oklch(75% 0.15 70 / 0.1);
    }
    select {
      cursor: pointer;
      -webkit-appearance: none;
      appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='none' stroke='%23888' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 5l3 3 3-3'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
      padding-right: var(--sp-8);
    }

    /* --- memory estimate. --- */
    .mem-estimate {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-4) var(--sp-5);
      display: flex;
      align-items: center;
      gap: var(--sp-4);
      margin-bottom: var(--sp-5);
    }
    .mem-label {
      font-size: var(--text-sm);
      color: var(--text-muted);
    }
    .mem-value {
      font-family: var(--font-mono);
      font-size: var(--text-xl);
      font-weight: 600;
      color: var(--accent);
    }
    .mem-detail {
      font-size: var(--text-xs);
      color: var(--text-muted);
      margin-left: auto;
    }

    .config-actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--sp-3);
    }

    /* --- log viewer. --- */
    .logs-section {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      margin-top: var(--sp-6);
    }
    .logs-header {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-4) var(--sp-5);
      border-bottom: 1px solid var(--border);
      background: var(--bg-surface);
    }
    .logs-header-title {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-primary);
    }
    .logs-tab {
      padding: var(--sp-1) var(--sp-3);
      font-size: var(--text-xs);
      font-weight: 500;
      background: none;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-muted);
      cursor: pointer;
      font-family: var(--font-body);
      transition: all var(--duration-fast);
    }
    .logs-tab:hover { color: var(--text-primary); border-color: var(--border-light); }
    .logs-tab.active {
      background: var(--accent);
      color: var(--bg-deep);
      border-color: var(--accent);
    }
    .logs-refresh {
      margin-left: auto;
    }
    .logs-body {
      max-height: 400px;
      overflow-y: auto;
      padding: var(--sp-4) var(--sp-5);
      font-family: var(--font-mono);
      font-size: var(--text-xs);
      line-height: 1.7;
      color: var(--text-secondary);
      white-space: pre-wrap;
      word-break: break-all;
    }
    .logs-empty {
      padding: var(--sp-8);
      text-align: center;
      font-size: var(--text-sm);
      color: var(--text-muted);
    }

    /* --- danger zone. --- */
    .danger-section {
      margin-top: var(--sp-8);
      border: 1px solid oklch(65% 0.15 25 / 0.35);
      border-radius: var(--radius-lg);
      background: oklch(65% 0.15 25 / 0.04);
      overflow: hidden;
    }
    .danger-toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding: var(--sp-5) var(--sp-6);
      background: none;
      border: none;
      color: var(--error);
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out);
    }
    .danger-toggle:hover { background: oklch(65% 0.15 25 / 0.08); }
    .danger-toggle-left {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
    }
    .danger-glyph {
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: oklch(65% 0.15 25 / 0.18);
      color: var(--error);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--text-sm);
      font-weight: 700;
    }
    .danger-toggle-icon {
      font-size: var(--text-sm);
      color: var(--error);
      transition: transform var(--duration-fast) var(--ease-out);
    }
    .danger-toggle-icon.open { transform: rotate(180deg); }

    .danger-body {
      padding: 0 var(--sp-6) var(--sp-6);
      display: flex;
      flex-direction: column;
      gap: var(--sp-5);
    }
    .danger-intro {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.6;
    }
    .danger-cards {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--sp-5);
    }
    @media (max-width: 640px) {
      .danger-cards { grid-template-columns: 1fr; }
    }

    .danger-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-5);
      display: flex;
      flex-direction: column;
      gap: var(--sp-4);
    }
    .danger-card.full {
      border-color: oklch(65% 0.15 25 / 0.4);
    }
    .danger-card-title {
      font-family: var(--font-heading);
      font-size: var(--text-base);
      color: var(--text-primary);
    }
    .danger-card-desc {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.55;
      flex: 1;
    }
    .danger-stats {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-2);
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .danger-stat {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 2px var(--sp-2);
      font-family: var(--font-mono);
    }
    .danger-stat.kept {
      border-color: oklch(70% 0.12 165 / 0.3);
      color: var(--success);
    }
    .danger-stat.deleted {
      border-color: oklch(65% 0.15 25 / 0.3);
      color: var(--error);
    }

    .btn-danger {
      background: oklch(65% 0.15 25 / 0.15);
      color: var(--error);
      border-color: oklch(65% 0.15 25 / 0.35);
    }
    .btn-danger:hover:not(:disabled) {
      background: oklch(65% 0.15 25 / 0.25);
    }
    .btn-danger-strong {
      background: var(--error);
      color: var(--bg-deep);
      border-color: var(--error);
      font-weight: 600;
    }
    .btn-danger-strong:hover:not(:disabled) {
      filter: brightness(1.1);
    }

    /* --- modal. --- */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: oklch(0% 0 0 / 0.6);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: var(--sp-4);
    }
    .modal {
      background: var(--bg-surface);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-lg);
      padding: var(--sp-6);
      max-width: 520px;
      width: 100%;
      box-shadow: var(--shadow-lg);
      display: flex;
      flex-direction: column;
      gap: var(--sp-5);
      max-height: 90vh;
      overflow-y: auto;
    }
    .modal-title {
      font-family: var(--font-heading);
      font-size: var(--text-xl);
      color: var(--error);
      display: flex;
      align-items: center;
      gap: var(--sp-3);
    }
    .modal-body {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.6;
      display: flex;
      flex-direction: column;
      gap: var(--sp-3);
    }
    .modal-list {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-3) var(--sp-4);
      font-size: var(--text-sm);
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
    }
    .modal-list-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: var(--sp-3);
    }
    .modal-list-label {
      color: var(--text-secondary);
    }
    .modal-list-value {
      font-family: var(--font-mono);
      font-size: var(--text-xs);
    }
    .modal-list-value.deleted { color: var(--error); }
    .modal-list-value.kept { color: var(--success); }

    .modal-confirm-row {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
    }
    .modal-confirm-label {
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .modal-confirm-input {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-2) var(--sp-3);
      font-family: var(--font-mono);
      font-size: var(--text-sm);
      color: var(--text-primary);
      letter-spacing: 0.1em;
    }
    .modal-confirm-input:focus {
      outline: none;
      border-color: var(--error);
      box-shadow: 0 0 0 3px oklch(65% 0.15 25 / 0.15);
    }

    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--sp-3);
    }

    .modal-result {
      font-size: var(--text-sm);
      padding: var(--sp-3) var(--sp-4);
      border-radius: var(--radius-md);
    }
    .modal-result.ok {
      background: oklch(70% 0.12 165 / 0.12);
      border: 1px solid oklch(70% 0.12 165 / 0.3);
      color: var(--success);
    }
    .modal-result.err {
      background: oklch(65% 0.15 25 / 0.12);
      border: 1px solid oklch(65% 0.15 25 / 0.3);
      color: var(--error);
    }
  `;

  constructor() {
    super();
    this._llmStatus = { running: false, model: "Gemma 4 26B-A4B", slots_used: 0, slots_total: 2 };
    this._embedStatus = { running: false, model: "BGE-M3", slots_used: 0, slots_total: 1 };
    this._config = null;
    this._configDraft = null;
    this._configOpen = false;
    this._loading = null;
    this._actionTarget = null;
    this._error = null;
    this._memEstimate = null;
    this._starting = new Set();
    this._logsTarget = null;
    this._logsLines = [];
    this._logsLoading = false;
    // --- danger zone. ---
    this._dangerOpen = false;
    this._resetPreviews = null;
    this._resetModalMode = null;
    this._resetConfirmInput = "";
    this._resetting = false;
    this._resetResult = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._fetchAll();
    this._pollInterval = setInterval(() => this._fetchStatus(), 5000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._pollInterval);
  }

  async _fetchAll() {
    await Promise.all([this._fetchStatus(), this._fetchConfig()]);
  }

  async _fetchStatus() {
    try {
      const data = await server.status();
      this._llmStatus = {
        running: data.llm_server?.running ?? false,
        model: data.llm_server?.model ?? "Gemma 4 26B-A4B",
        slots_used: data.llm_server?.slots_used ?? 0,
        slots_total: data.llm_server?.slots_total ?? 2,
        host: data.llm_server?.host ?? null,
        port: data.llm_server?.port ?? null,
        pid: data.llm_server?.pid ?? null,
        reasoning: data.llm_server?.reasoning ?? null,
      };
      this._embedStatus = {
        running: data.embed_server?.running ?? false,
        model: data.embed_server?.model ?? "BGE-M3",
        slots_used: data.embed_server?.slots_used ?? 0,
        slots_total: data.embed_server?.slots_total ?? 1,
        host: data.embed_server?.host ?? null,
        port: data.embed_server?.port ?? null,
        pid: data.embed_server?.pid ?? null,
      };
    } catch (err) {
      // routine status-poll failures (llm/embed server down) are expected.
      // log at debug level so devtools users can still see them without
      // flooding the console on a normal "stopped" state.
      if (import.meta.env?.DEV) {
        console.debug("server-panel status poll failed:", err);
      }
    }
  }

  async _fetchConfig() {
    try {
      const cfg = await server.config();
      this._config = cfg;
      this._configDraft = { ...cfg };
      this._recalcMemory();
    } catch (err) {
      // config fetch failure is non-fatal for the ui (falls back to
      // last-known values), but we still want visibility in dev.
      if (import.meta.env?.DEV) {
        console.debug("server-panel config fetch failed:", err);
      }
    }
  }

  /** rough ram estimate: model weight + kv cache. */
  _recalcMemory() {
    const d = this._configDraft;
    if (!d) { this._memEstimate = null; return; }

    const modelGb = 16;
    const ctxSize = d.context_size ?? 65536;
    const slots = d.parallel ?? 2;

    const kvBitsKey = { f16: 16, q8_0: 8, q4_0: 4, turbo4: 4.2 }[d.kv_type_k] ?? 8;
    const kvBitsVal = { f16: 16, q8_0: 8, q4_0: 4, turbo4: 4.2 }[d.kv_type_v] ?? 4.2;
    const layers = 52;
    const headDim = 256;
    const kvHeads = 8;

    const bytesPerToken = layers * kvHeads * headDim * (kvBitsKey + kvBitsVal) / 8;
    const kvGb = (ctxSize * slots * bytesPerToken) / (1024 ** 3);
    const totalGb = modelGb + kvGb;

    this._memEstimate = {
      total: totalGb.toFixed(1),
      model: modelGb.toFixed(1),
      kv: kvGb.toFixed(1),
    };
  }

  _onDraftChange(field, value) {
    this._configDraft = { ...this._configDraft, [field]: value };
    this._recalcMemory();
  }

  async _handleStart(target) {
    this._error = null;
    try {
      await server.start(target);
      // mark as starting and poll aggressively until healthy.
      this._starting = new Set([...this._starting, target]);
      this._pollUntilHealthy(target);
    } catch (e) {
      this._error = e.message;
    }
  }

  async _handleStop(target) {
    this._loading = "stop";
    this._actionTarget = target;
    this._error = null;
    try {
      await server.stop(target);
      // clear any starting state for this target.
      const next = new Set(this._starting);
      next.delete(target);
      this._starting = next;
      await this._fetchStatus();
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = null;
      this._actionTarget = null;
    }
  }

  async _handleRestart(target) {
    this._error = null;
    this._starting = new Set([...this._starting, target]);
    try {
      await server.stop(target);
      await this._fetchStatus();
      await server.start(target);
      this._pollUntilHealthy(target);
    } catch (e) {
      this._error = e.message;
      const next = new Set(this._starting);
      next.delete(target);
      this._starting = next;
    }
  }

  async _handleStartBoth() {
    this._error = null;
    this._starting = new Set(["llm", "embed"]);
    try {
      await Promise.all([server.start("llm"), server.start("embed")]);
      this._pollUntilHealthy("llm");
      this._pollUntilHealthy("embed");
    } catch (e) {
      this._error = e.message;
      this._starting = new Set();
    }
  }

  /** poll every 3s for up to 90s until the target server becomes healthy. */
  _pollUntilHealthy(target) {
    let attempts = 0;
    const maxAttempts = 30; // 30 × 3s = 90s timeout.
    const iv = setInterval(async () => {
      attempts++;
      await this._fetchStatus();
      const isHealthy = target === "llm" ? this._llmStatus.running : this._embedStatus.running;
      if (isHealthy || attempts >= maxAttempts) {
        clearInterval(iv);
        const next = new Set(this._starting);
        next.delete(target);
        this._starting = next;
        if (!isHealthy) {
          this._error = `${target === "llm" ? "LLM" : "Embed"} server did not start within 90 seconds.`;
        }
      }
    }, 3000);
  }

  async _handleSaveRestart() {
    this._loading = "save";
    this._error = null;
    try {
      await server.setConfig(this._configDraft);
      this._config = { ...this._configDraft };

      const targets = [];
      if (this._llmStatus.running) targets.push("llm");
      if (this._embedStatus.running) targets.push("embed");

      for (const t of targets) {
        await server.stop(t);
      }
      await this._fetchStatus();
      this._starting = new Set(targets);
      for (const t of targets) {
        await server.start(t);
        this._pollUntilHealthy(t);
      }
    } catch (e) {
      this._error = e.message;
      this._starting = new Set();
    } finally {
      this._loading = null;
    }
  }

  async _fetchLogs(target) {
    this._logsTarget = target;
    this._logsLoading = true;
    try {
      const data = await server.logs(target, 200);
      this._logsLines = data.lines || [];
    } catch (err) {
      if (import.meta.env?.DEV) {
        console.debug("server-panel log fetch failed:", err);
      }
      this._logsLines = ["Failed to fetch logs."];
    } finally {
      this._logsLoading = false;
      await this.updateComplete;
      const el = this.renderRoot?.querySelector(".logs-body");
      if (el) el.scrollTop = el.scrollHeight;
    }
  }

  _isLoading(kind, target) {
    if (this._loading === kind && (!target || this._actionTarget === target)) return true;
    if (this._loading === "start-both" && kind === "start") return true;
    return false;
  }

  // --- danger zone handlers. ---

  async _toggleDanger() {
    const willOpen = !this._dangerOpen;
    this._dangerOpen = willOpen;
    if (willOpen && this._resetPreviews === null) {
      await this._fetchResetPreviews();
    }
  }

  async _fetchResetPreviews() {
    try {
      const [wikiPreview, fullPreview] = await Promise.all([
        admin.resetPreview("wiki"),
        admin.resetPreview("full"),
      ]);
      this._resetPreviews = { wiki: wikiPreview, full: fullPreview };
    } catch (e) {
      this._error = `Failed to load reset preview: ${e.message}`;
    }
  }

  async _openResetModal(mode) {
    this._resetModalMode = mode;
    this._resetConfirmInput = "";
    this._resetResult = null;
    // guarantee preview is available even if toggle didn't finish loading yet.
    if (this._resetPreviews === null) {
      await this._fetchResetPreviews();
    }
  }

  _closeResetModal() {
    if (this._resetting) return;
    this._resetModalMode = null;
    this._resetConfirmInput = "";
    this._resetResult = null;
  }

  async _executeReset() {
    if (this._resetConfirmInput !== "RESET") return;
    if (!this._resetModalMode) return;

    this._resetting = true;
    this._resetResult = null;
    try {
      const result = await admin.reset(this._resetModalMode, "RESET");
      this._resetResult = { ok: true, message: result.message || "Reset complete." };
      // refresh previews so the numbers drop to zero.
      await this._fetchResetPreviews();
    } catch (e) {
      this._resetResult = { ok: false, message: e.message };
    } finally {
      this._resetting = false;
    }
  }

  _renderTooltip(text) {
    return html`
      <span class="tooltip-wrap">
        <span class="tooltip-icon">?</span>
        <span class="tooltip-bubble">${text}</span>
      </span>
    `;
  }

  // --- danger zone rendering. ---

  _renderDangerZone() {
    const previews = this._resetPreviews;
    const wikiPages = previews?.wiki?.wiki?.pages_total ?? "—";
    const rawFiles = previews?.full?.raw?.files ?? "—";
    const rawAssets = previews?.full?.raw?.assets ?? "—";
    const dbPresent = previews?.wiki?.db?.files_present ?? "—";
    const ingestRunning = previews?.wiki?.ingest_running ?? false;

    return html`
      <div class="danger-section">
        <button class="danger-toggle" @click=${() => this._toggleDanger()}>
          <span class="danger-toggle-left">
            <span class="danger-glyph">!</span>
            <span>Danger Zone</span>
          </span>
          <span class="danger-toggle-icon ${this._dangerOpen ? "open" : ""}">&#9660;</span>
        </button>

        ${this._dangerOpen ? html`
          <div class="danger-body">
            <p class="danger-intro">
              Reset the wiki to a clean state. These actions cannot be undone.
              ${ingestRunning ? html`
                <br><strong style="color: var(--warning);">
                  An ingest is currently running — reset is disabled until it finishes or is cancelled.
                </strong>
              ` : ""}
            </p>

            <div class="danger-cards">
              <!-- wiki reset card. -->
              <div class="danger-card">
                <div class="danger-card-title">Reset Wiki Only</div>
                <div class="danger-card-desc">
                  Delete the generated wiki (sources, entities, concepts, synthesis),
                  the search index, and all runtime caches. <strong>Keeps raw sources</strong> so
                  you can re-ingest into a clean wiki.
                </div>
                <div class="danger-stats">
                  <span class="danger-stat deleted">${wikiPages} wiki pages</span>
                  <span class="danger-stat deleted">${dbPresent} db files</span>
                  <span class="danger-stat kept">raw/ kept</span>
                </div>
                <button class="btn btn-danger"
                  ?disabled=${ingestRunning || this._resetting}
                  @click=${() => this._openResetModal("wiki")}>
                  Reset Wiki
                </button>
              </div>

              <!-- full reset card. -->
              <div class="danger-card full">
                <div class="danger-card-title">Factory Reset</div>
                <div class="danger-card-desc">
                  Everything above, <strong>and</strong> delete every file in <code>raw/</code>
                  (assets included). The vault returns to an empty baseline — nothing left.
                </div>
                <div class="danger-stats">
                  <span class="danger-stat deleted">${wikiPages} wiki pages</span>
                  <span class="danger-stat deleted">${dbPresent} db files</span>
                  <span class="danger-stat deleted">${rawFiles} raw files</span>
                  <span class="danger-stat deleted">${rawAssets} assets</span>
                </div>
                <button class="btn btn-danger-strong"
                  ?disabled=${ingestRunning || this._resetting}
                  @click=${() => this._openResetModal("full")}>
                  Factory Reset
                </button>
              </div>
            </div>
          </div>
        ` : ""}
      </div>
    `;
  }

  _renderResetModal() {
    const mode = this._resetModalMode;
    if (!mode) return "";
    const preview = this._resetPreviews?.[mode];
    const loading = !preview;

    const wikiTotal = preview?.wiki?.pages_total ?? 0;
    const wikiSubdirs = preview?.wiki?.pages_per_subdir ?? {};
    const dbPresent = preview?.db?.files_present ?? 0;
    const rawFiles = preview?.raw?.files ?? 0;
    const rawAssets = preview?.raw?.assets ?? 0;
    const title = mode === "wiki" ? "Reset Wiki" : "Factory Reset";
    const canConfirm = this._resetConfirmInput === "RESET" && !this._resetting && !loading;
    const succeeded = this._resetResult?.ok === true;

    return html`
      <div class="modal-backdrop" @click=${(e) => {
        if (e.target === e.currentTarget) this._closeResetModal();
      }}>
        <div class="modal" @click=${(e) => e.stopPropagation()}>
          <h2 class="modal-title">
            <span class="danger-glyph">!</span>
            ${title}
          </h2>

          <div class="modal-body">
            <p>This will permanently delete:</p>
            <div class="modal-list">
              <div class="modal-list-row">
                <span class="modal-list-label">Wiki pages (total)</span>
                <span class="modal-list-value deleted">${wikiTotal}</span>
              </div>
              ${Object.entries(wikiSubdirs).map(([sub, n]) => html`
                <div class="modal-list-row">
                  <span class="modal-list-label" style="padding-left: var(--sp-4)">
                    &nbsp;&nbsp;${sub}/
                  </span>
                  <span class="modal-list-value deleted">${n}</span>
                </div>
              `)}
              <div class="modal-list-row">
                <span class="modal-list-label">Runtime db files</span>
                <span class="modal-list-value deleted">${dbPresent} of 6</span>
              </div>
              ${mode === "full" ? html`
                <div class="modal-list-row">
                  <span class="modal-list-label">Raw source files</span>
                  <span class="modal-list-value deleted">${rawFiles}</span>
                </div>
                <div class="modal-list-row">
                  <span class="modal-list-label">Raw assets</span>
                  <span class="modal-list-value deleted">${rawAssets}</span>
                </div>
              ` : html`
                <div class="modal-list-row">
                  <span class="modal-list-label">Raw source files</span>
                  <span class="modal-list-value kept">${rawFiles} kept</span>
                </div>
              `}
            </div>

            ${!succeeded ? html`
              <div class="modal-confirm-row">
                <label class="modal-confirm-label">
                  Type <strong>RESET</strong> to confirm:
                </label>
                <input
                  class="modal-confirm-input"
                  type="text"
                  autocomplete="off"
                  spellcheck="false"
                  .value=${this._resetConfirmInput}
                  ?disabled=${this._resetting}
                  @input=${(e) => { this._resetConfirmInput = e.target.value; }} />
              </div>
            ` : ""}

            ${this._resetResult ? html`
              <div class="modal-result ${this._resetResult.ok ? "ok" : "err"}">
                ${this._resetResult.message}
              </div>
            ` : ""}
          </div>

          <div class="modal-actions">
            <button class="btn btn-ghost"
              ?disabled=${this._resetting}
              @click=${() => this._closeResetModal()}>
              ${succeeded ? "Close" : "Cancel"}
            </button>
            ${!succeeded ? html`
              <button class="btn btn-danger-strong"
                ?disabled=${!canConfirm}
                @click=${() => this._executeReset()}>
                ${this._resetting ? html`<span class="spinner"></span>` : ""}
                ${this._resetting ? "Resetting…" : (mode === "wiki" ? "Reset Wiki" : "Factory Reset")}
              </button>
            ` : ""}
          </div>
        </div>
      </div>
    `;
  }

  _renderStatusCard(label, status, target) {
    const running = status.running;
    const starting = this._starting.has(target);
    const pct = status.slots_total > 0
      ? Math.round((status.slots_used / status.slots_total) * 100)
      : 0;

    const stateClass = running ? "running" : starting ? "starting" : "";
    const dotClass = running ? "on" : starting ? "starting" : "off";
    const stateText = running ? "Running" : starting ? "Starting…" : "Stopped";

    return html`
      <div class="status-card ${stateClass}">
        <div class="card-head">
          <span class="card-title">${label}</span>
          <span class="status-indicator">
            <span class="status-dot ${dotClass}"></span>
            <span class="status-text ${dotClass}">${stateText}</span>
          </span>
        </div>

        <div class="card-meta">
          <div class="meta-row">
            <span class="meta-label">Model</span>
            <span class="meta-value">${status.model}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Endpoint</span>
            <span class="meta-value">${status.host || "—"}:${status.port || "—"}</span>
          </div>
          ${status.pid ? html`
            <div class="meta-row">
              <span class="meta-label">PID</span>
              <span class="meta-value">${status.pid}</span>
            </div>
          ` : ""}
          <div class="meta-row">
            <span class="meta-label">Slots</span>
            <span class="meta-value">${status.slots_used} / ${status.slots_total}</span>
          </div>
          <div class="slot-bar-track">
            <div class="slot-bar-fill" style="width: ${pct}%"></div>
          </div>
          ${target === "llm" && status.reasoning ? html`
            <div class="meta-row">
              <span class="meta-label">Reasoning</span>
              <span class="meta-value ${status.reasoning === "on" ? "reasoning-on" : "reasoning-off"}">
                ${status.reasoning === "on" ? "on (chat quality)" : "off (ingest ready)"}
              </span>
            </div>
          ` : ""}
        </div>

        <div class="card-actions">
          ${starting
            ? html`
              <button class="btn btn-start" disabled>
                <span class="spinner"></span>
                Starting…
              </button>`
            : running
              ? html`
                <button class="btn btn-stop"
                  ?disabled=${this._loading !== null || this._starting.size > 0}
                  @click=${() => this._handleStop(target)}>
                  ${this._isLoading("stop", target) ? html`<span class="spinner"></span>` : ""}
                  Stop
                </button>
                <button class="btn btn-restart"
                  ?disabled=${this._loading !== null || this._starting.size > 0}
                  @click=${() => this._handleRestart(target)}>
                  Restart
                </button>`
              : html`
                <button class="btn btn-start"
                  ?disabled=${this._loading !== null || this._starting.size > 0}
                  @click=${() => this._handleStart(target)}>
                  Start
                </button>`
          }
        </div>
      </div>
    `;
  }

  render() {
    const d = this._configDraft;

    return html`
      <div class="panel-header">
        <h1>Server</h1>
        <p>Manage the local LLM and embedding servers.</p>
      </div>

      ${this._error ? html`
        <div class="error-banner">
          <span>${this._error}</span>
          <button @click=${() => { this._error = null; }}>&times;</button>
        </div>
      ` : ""}

      <div class="global-actions">
        <button class="btn btn-primary"
          ?disabled=${this._loading !== null || this._starting.size > 0 || (this._llmStatus.running && this._embedStatus.running)}
          @click=${() => this._handleStartBoth()}>
          ${this._starting.size > 0 ? html`<span class="spinner"></span>` : ""}
          ${this._starting.size > 0 ? "Starting…" : "Start Both"}
        </button>
      </div>

      <div class="cards-row">
        ${this._renderStatusCard("LLM Server (Gemma 4)", this._llmStatus, "llm")}
        ${this._renderStatusCard("Embedding Server (BGE-M3)", this._embedStatus, "embed")}
      </div>

      <div class="config-section">
        <button class="config-toggle" @click=${() => { this._configOpen = !this._configOpen; }}>
          <span>Configuration</span>
          <span class="config-toggle-icon ${this._configOpen ? "open" : ""}">&#9660;</span>
        </button>

        ${this._configOpen && d != null ? html`
          <div class="config-body">
            <div class="config-grid">
              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">Prompt Processing Batch Size</label>
                  ${this._renderTooltip("Controls how many tokens are processed at once during prefill. Larger = faster ingestion but more peak memory.")}
                </div>
                <input type="number"
                  .value=${String(d.batch_size ?? 2048)}
                  @input=${(e) => this._onDraftChange("batch_size", Number(e.target.value))} />
              </div>

              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">Context Size</label>
                  ${this._renderTooltip("Total context window split across parallel slots.")}
                </div>
                <input type="number"
                  .value=${String(d.context_size ?? 65536)}
                  @input=${(e) => this._onDraftChange("context_size", Number(e.target.value))} />
              </div>

              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">Parallel Slots</label>
                  ${this._renderTooltip("Concurrent request slots. Each gets context_size / parallel tokens.")}
                </div>
                <input type="number"
                  .value=${String(d.parallel ?? 2)}
                  min="1" max="8"
                  @input=${(e) => this._onDraftChange("parallel", Number(e.target.value))} />
              </div>

              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">KV Cache Key Type</label>
                  ${this._renderTooltip("Data type for attention keys. Lower precision = less memory, slight quality trade-off.")}
                </div>
                <select .value=${d.kv_type_k ?? "q8_0"}
                  @change=${(e) => this._onDraftChange("kv_type_k", e.target.value)}>
                  <option value="f16">f16</option>
                  <option value="q8_0">q8_0</option>
                  <option value="q4_0">q4_0</option>
                  <option value="turbo4">turbo4</option>
                </select>
              </div>

              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">KV Cache Value Type</label>
                  ${this._renderTooltip("Data type for attention values. turbo4 = 3.8x compression with minimal quality loss.")}
                </div>
                <select .value=${d.kv_type_v ?? "turbo4"}
                  @change=${(e) => this._onDraftChange("kv_type_v", e.target.value)}>
                  <option value="f16">f16</option>
                  <option value="q8_0">q8_0</option>
                  <option value="q4_0">q4_0</option>
                  <option value="turbo4">turbo4</option>
                </select>
              </div>

              <div class="field">
                <div class="field-label-row">
                  <label class="field-label">Reasoning Mode</label>
                  ${this._renderTooltip("Gemma 4 <think> pre-answer. On improves chat quality. Off is required for ingestion (prevents burning the output budget on thinking before emitting entities).")}
                </div>
                <select .value=${d.reasoning ?? "on"}
                  @change=${(e) => this._onDraftChange("reasoning", e.target.value)}>
                  <option value="on">on (chat quality)</option>
                  <option value="off">off (ingest ready)</option>
                </select>
              </div>
            </div>

            ${this._memEstimate ? html`
              <div class="mem-estimate">
                <div>
                  <div class="mem-label">Estimated RAM</div>
                  <div class="mem-value">${this._memEstimate.total} GB</div>
                </div>
                <div class="mem-detail">
                  Model ${this._memEstimate.model} GB &nbsp;+&nbsp; KV cache ${this._memEstimate.kv} GB
                </div>
              </div>
            ` : ""}

            <div class="config-actions">
              <button class="btn btn-ghost"
                ?disabled=${this._loading !== null}
                @click=${() => { this._configDraft = { ...this._config }; this._recalcMemory(); }}>
                Reset
              </button>
              <button class="btn btn-primary"
                ?disabled=${this._loading !== null}
                @click=${() => this._handleSaveRestart()}>
                ${this._loading === "save" ? html`<span class="spinner"></span>` : ""}
                Save &amp; Restart
              </button>
            </div>
          </div>
        ` : ""}
      </div>

      <!-- log viewer -->
      <div class="logs-section">
        <div class="logs-header">
          <span class="logs-header-title">Server Logs</span>
          <button class="logs-tab ${this._logsTarget === "llm" ? "active" : ""}"
            @click=${() => this._fetchLogs("llm")}>LLM</button>
          <button class="logs-tab ${this._logsTarget === "embed" ? "active" : ""}"
            @click=${() => this._fetchLogs("embed")}>Embed</button>
          ${this._logsTarget ? html`
            <button class="btn btn-ghost logs-refresh" style="padding:var(--sp-1) var(--sp-3);font-size:var(--text-xs)"
              ?disabled=${this._logsLoading}
              @click=${() => this._fetchLogs(this._logsTarget)}>
              ${this._logsLoading ? html`<span class="spinner"></span>` : "Refresh"}
            </button>
          ` : ""}
        </div>
        ${this._logsTarget
          ? html`
            <div class="logs-body">${this._logsLines.length > 0
              ? this._logsLines.join("\n")
              : "No logs yet."}</div>`
          : html`<div class="logs-empty">Select LLM or Embed to view server logs.</div>`
        }
      </div>

      ${this._renderDangerZone()}
      ${this._resetModalMode ? this._renderResetModal() : ""}
    `;
  }
}

customElements.define("server-panel", ServerPanel);
