import { LitElement, html, css } from "lit";
import { ingest, server } from "../lib/api.js";

/**
 * @element ingest-panel
 * File management and ingestion interface.
 * Lists raw/ files, supports drag-and-drop upload, single/batch ingest,
 * and streams live progress via SSE.
 */
export class IngestPanel extends LitElement {
  static properties = {
    _files:          { state: true },
    _loading:        { state: true },
    _error:          { state: true },
    _useEmbeddings:  { state: true },
    _dragOver:       { state: true },
    _uploading:      { state: true },
    _activeTask:     { state: true },
    _progressLog:    { state: true },
    _summary:        { state: true },
    _ingestTarget:   { state: true },
    _selected:       { state: true },   // Set<string> of selected filenames for batch ops.
    _toasts:         { state: true },
    _showEmbedPrompt: { state: true },  // post-ingest: offer to stop manually-started embed server.
    _showReasoningGuard:  { state: true },  // pre-ingest: reasoning=on modal.
    _restartingLlm:       { state: true },  // spinner while llm restarts with reasoning off.
    _showEmbedSpinPrompt: { state: true },  // pre-toggle: confirm spinning up embed server.
    _cancelling:          { state: true },  // cancel button loading state.
  };

  /* checkbox column. */

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

    /* --- toolbar. --- */
    .toolbar {
      display: flex;
      align-items: center;
      gap: var(--sp-4);
      margin-bottom: var(--sp-5);
      flex-wrap: wrap;
    }

    .toggle-row {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      font-size: var(--text-sm);
      color: var(--text-secondary);
    }

    /* custom toggle switch. */
    .toggle-switch {
      position: relative;
      display: inline-block;
      width: 38px;
      height: 22px;
      flex-shrink: 0;
    }
    .toggle-switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }
    .toggle-track {
      position: absolute;
      inset: 0;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 11px;
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out),
                  border-color var(--duration-fast) var(--ease-out);
    }
    .toggle-track::after {
      content: "";
      position: absolute;
      top: 2px;
      left: 2px;
      width: 16px;
      height: 16px;
      background: var(--text-muted);
      border-radius: 50%;
      transition: transform var(--duration-fast) var(--ease-out),
                  background var(--duration-fast) var(--ease-out);
    }
    .toggle-switch input:checked + .toggle-track {
      background: oklch(75% 0.15 70 / 0.2);
      border-color: var(--accent-dim);
    }
    .toggle-switch input:checked + .toggle-track::after {
      transform: translateX(16px);
      background: var(--accent);
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

    .btn-ingest {
      background: oklch(70% 0.12 165 / 0.15);
      color: var(--success);
      border-color: oklch(70% 0.12 165 / 0.25);
      padding: var(--sp-1) var(--sp-3);
      font-size: var(--text-xs);
    }
    .btn-ingest:hover:not(:disabled) {
      background: oklch(70% 0.12 165 / 0.25);
    }

    .spacer { flex: 1; }

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

    /* --- drop zone. --- */
    .drop-zone {
      border: 2px dashed var(--border);
      border-radius: var(--radius-lg);
      padding: var(--sp-10) var(--sp-6);
      text-align: center;
      margin-bottom: var(--sp-6);
      transition: border-color var(--duration-fast) var(--ease-out),
                  background var(--duration-fast) var(--ease-out);
      cursor: pointer;
      position: relative;
    }
    .drop-zone:hover {
      border-color: var(--border-light);
      background: oklch(21% 0.015 260 / 0.4);
    }
    .drop-zone.drag-over {
      border-color: var(--accent);
      background: oklch(75% 0.15 70 / 0.06);
      box-shadow: inset 0 0 30px oklch(75% 0.15 70 / 0.04);
    }
    .drop-zone input[type="file"] {
      position: absolute;
      inset: 0;
      opacity: 0;
      cursor: pointer;
    }
    .drop-icon {
      font-size: var(--text-4xl);
      margin-bottom: var(--sp-3);
      opacity: 0.5;
    }
    .drop-label {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      margin-bottom: var(--sp-1);
    }
    .drop-hint {
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .drop-zone.uploading {
      pointer-events: none;
      opacity: 0.6;
    }

    /* --- file table. --- */
    .file-table-wrap {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      margin-bottom: var(--sp-6);
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    thead th {
      font-size: var(--text-xs);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      padding: var(--sp-3) var(--sp-4);
      text-align: left;
      border-bottom: 1px solid var(--border);
      background: var(--bg-surface);
    }
    tbody tr {
      transition: background var(--duration-fast) var(--ease-out);
    }
    tbody tr:hover {
      background: var(--bg-card-hover);
    }
    tbody td {
      padding: var(--sp-3) var(--sp-4);
      font-size: var(--text-sm);
      color: var(--text-secondary);
      border-bottom: 1px solid var(--border);
    }
    tbody tr:last-child td {
      border-bottom: none;
    }

    .col-name {
      color: var(--text-primary);
      font-weight: 500;
      word-break: break-all;
    }
    .col-size {
      font-family: var(--font-mono);
      font-size: var(--text-xs);
      white-space: nowrap;
      color: var(--text-muted);
    }
    .col-action {
      text-align: right;
      white-space: nowrap;
    }

    /* --- badges. --- */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: var(--sp-1);
      padding: 2px var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 500;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-pending {
      background: oklch(75% 0.14 70 / 0.15);
      color: var(--warning);
    }
    .badge-ingested {
      background: oklch(70% 0.12 165 / 0.15);
      color: var(--success);
    }
    .badge-changed {
      background: oklch(70% 0.10 250 / 0.15);
      color: var(--info);
    }

    /* --- empty state. --- */
    .empty-state {
      text-align: center;
      padding: var(--sp-10) var(--sp-6);
      color: var(--text-muted);
    }
    .empty-state p {
      font-size: var(--text-sm);
      margin-top: var(--sp-2);
    }

    /* --- progress panel. --- */
    .progress-panel {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      margin-bottom: var(--sp-6);
    }
    .progress-header {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-4) var(--sp-5);
      border-bottom: 1px solid var(--border);
      background: var(--bg-surface);
    }
    .progress-title {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-primary);
    }
    .progress-target {
      font-size: var(--text-sm);
      color: var(--text-muted);
      font-family: var(--font-mono);
    }

    .progress-live-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 6px var(--success);
      animation: pulse 1.5s ease-in-out infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    .progress-log {
      max-height: 300px;
      overflow-y: auto;
      padding: var(--sp-4) var(--sp-5);
      font-family: var(--font-mono);
      font-size: var(--text-xs);
      line-height: 1.8;
      color: var(--text-secondary);
    }
    .progress-log .log-line {
      padding: var(--sp-1) 0;
      border-bottom: 1px solid oklch(28% 0.015 260 / 0.5);
    }
    .progress-log .log-line:last-child {
      border-bottom: none;
    }
    .log-stage {
      color: var(--accent);
      font-weight: 600;
    }
    .log-info  { color: var(--text-muted); }
    .log-done  { color: var(--success); }
    .log-error { color: var(--error); }

    /* --- summary card. --- */
    .summary-card {
      background: var(--bg-card);
      border: 1px solid oklch(70% 0.12 165 / 0.25);
      border-radius: var(--radius-lg);
      padding: var(--sp-6);
      margin-bottom: var(--sp-6);
    }
    .summary-card h3 {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--success);
      margin-bottom: var(--sp-4);
    }
    .summary-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: var(--sp-4);
    }
    .stat-item {
      display: flex;
      flex-direction: column;
      gap: var(--sp-1);
    }
    .stat-value {
      font-family: var(--font-mono);
      font-size: var(--text-xl);
      font-weight: 600;
      color: var(--text-primary);
    }
    .stat-label {
      font-size: var(--text-xs);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
    }
    .summary-dismiss {
      margin-top: var(--sp-4);
      display: flex;
      justify-content: flex-end;
    }

    /* --- checkbox column. --- */
    .col-check {
      width: 36px;
      text-align: center;
    }
    .col-check input[type="checkbox"] {
      accent-color: var(--accent);
      cursor: pointer;
    }

    /* --- batch action bar. --- */
    .batch-bar {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-3) var(--sp-4);
      margin-bottom: var(--sp-4);
      background: oklch(75% 0.15 70 / 0.06);
      border: 1px solid oklch(75% 0.15 70 / 0.15);
      border-radius: var(--radius-md);
      font-size: var(--text-sm);
      color: var(--text-secondary);
    }
    .batch-count {
      font-weight: 600;
      color: var(--text-primary);
    }

    /* --- cancel button. --- */
    .btn-cancel {
      background: oklch(65% 0.15 25 / 0.15);
      color: var(--error);
      border: 1px solid oklch(65% 0.15 25 / 0.3);
      padding: var(--sp-1) var(--sp-3);
      font-size: var(--text-xs);
    }
    .btn-cancel:hover:not(:disabled) {
      background: oklch(65% 0.15 25 / 0.25);
    }

    /* --- toast notifications. --- */
    .toast-container {
      position: fixed;
      bottom: var(--sp-6);
      right: var(--sp-6);
      display: flex;
      flex-direction: column-reverse;
      gap: var(--sp-2);
      z-index: 1000;
      pointer-events: none;
    }
    .toast {
      pointer-events: auto;
      padding: var(--sp-3) var(--sp-5);
      border-radius: var(--radius-md);
      font-size: var(--text-sm);
      max-width: 380px;
      animation: toast-in 0.3s var(--ease-out);
      box-shadow: 0 4px 12px oklch(0% 0 0 / 0.3);
    }
    .toast-info {
      background: oklch(28% 0.02 260);
      border: 1px solid var(--border-light);
      color: var(--text-primary);
    }
    .toast-success {
      background: oklch(28% 0.03 165);
      border: 1px solid oklch(70% 0.12 165 / 0.3);
      color: var(--success);
    }
    .toast-warning {
      background: oklch(28% 0.03 70);
      border: 1px solid oklch(75% 0.14 70 / 0.3);
      color: var(--warning);
    }
    @keyframes toast-in {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* --- embed server prompt. --- */
    .embed-prompt {
      background: var(--bg-card);
      border: 1px solid oklch(70% 0.12 250 / 0.3);
      border-radius: var(--radius-lg);
      padding: var(--sp-4) var(--sp-5);
      margin-bottom: var(--sp-6);
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      flex-wrap: wrap;
    }
    .embed-prompt-text {
      flex: 1;
      font-size: var(--text-sm);
      color: var(--text-secondary);
      min-width: 200px;
    }

    /* --- modal overlay (reasoning guard, embed spin-up). --- */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: oklch(0% 0 0 / 0.55);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: var(--sp-4);
    }
    .modal-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: var(--sp-6);
      max-width: 520px;
      width: 100%;
      box-shadow: 0 20px 40px oklch(0% 0 0 / 0.4);
    }
    .modal-title {
      font-family: var(--font-heading);
      font-size: var(--text-xl);
      color: var(--text-primary);
      margin: 0 0 var(--sp-3);
    }
    .modal-body {
      font-size: var(--text-sm);
      color: var(--text-secondary);
      line-height: 1.55;
      margin-bottom: var(--sp-5);
    }
    .modal-body p { margin: 0 0 var(--sp-3); }
    .modal-body p:last-child { margin-bottom: 0; }
    .modal-body code {
      font-family: var(--font-mono, ui-monospace, SFMono-Regular, monospace);
      font-size: var(--text-xs);
      padding: 0 4px;
      background: var(--bg-input);
      border-radius: 3px;
    }
    .modal-callout {
      background: oklch(28% 0.03 70);
      border-left: 3px solid var(--warning);
      padding: var(--sp-2) var(--sp-3);
      border-radius: 4px;
      margin-top: var(--sp-3);
      font-size: var(--text-xs);
      color: var(--warning);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--sp-2);
      flex-wrap: wrap;
    }
    .modal-actions .btn-ghost { margin-right: auto; }
  `;

  constructor() {
    super();
    this._files = [];
    this._loading = false;
    this._error = null;
    this._useEmbeddings = false;
    this._dragOver = false;
    this._uploading = false;
    this._activeTask = null;
    this._progressLog = [];
    this._summary = null;
    this._ingestTarget = null;
    this._eventSource = null;
    this._selected = new Set();
    this._toasts = [];
    this._showEmbedPrompt = false;
    this._embedWasManual = false;
    this._showReasoningGuard = false;
    this._restartingLlm = false;
    this._pendingIngest = null;           // { kind: "one"|"all"|"batch", args: [...] }
    this._showEmbedSpinPrompt = false;
    this._cancelling = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._fetchFiles();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._closeEventSource();
  }

  _closeEventSource() {
    if (this._eventSource) {
      this._eventSource.close();
      this._eventSource = null;
    }
  }

  async _fetchFiles() {
    this._loading = true;
    try {
      const data = await ingest.files();
      this._files = data.files ?? data ?? [];
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = false;
    }
  }

  _formatSize(bytes) {
    if (bytes == null) return "--";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  _pendingFiles() {
    return this._files.filter(f => f.status === "pending" || f.status === "changed");
  }

  /* --- drag and drop. --- */

  _onDragOver(e) {
    e.preventDefault();
    this._dragOver = true;
  }

  _onDragLeave() {
    this._dragOver = false;
  }

  async _onDrop(e) {
    e.preventDefault();
    this._dragOver = false;
    const files = [...(e.dataTransfer?.files ?? [])];
    if (files.length > 0) {
      await this._uploadFiles(files);
    }
  }

  async _onFileInput(e) {
    const files = [...(e.target.files ?? [])];
    if (files.length > 0) {
      await this._uploadFiles(files);
    }
    e.target.value = "";
  }

  async _uploadFiles(files) {
    this._uploading = true;
    this._error = null;
    try {
      for (const file of files) {
        await ingest.upload(file);
      }
      await this._fetchFiles();
    } catch (e) {
      this._error = e.message;
    } finally {
      this._uploading = false;
    }
  }

  /* --- selection helpers. --- */

  _toggleSelect(filename) {
    const next = new Set(this._selected);
    next.has(filename) ? next.delete(filename) : next.add(filename);
    this._selected = next;
  }

  _toggleSelectAll() {
    if (this._selected.size === this._files.length) {
      this._selected = new Set();
    } else {
      this._selected = new Set(this._files.map(f => f.filename));
    }
  }

  /* --- ingest single. --- */

  async _handleIngestOne(filename, overwrite = false) {
    if (!(await this._gateIngest({ kind: "one", args: [filename, overwrite] }))) return;
    await this._startIngestOne(filename, overwrite);
  }

  async _startIngestOne(filename, overwrite) {
    this._error = null;
    this._summary = null;
    this._progressLog = [];
    this._ingestTarget = filename;

    try {
      const res = await ingest.start(filename, overwrite, this._useEmbeddings);
      const taskId = res.task_id ?? res.taskId;
      if (taskId) {
        this._activeTask = taskId;
        this._listenProgress(taskId);
      } else {
        this._summary = res;
        await this._fetchFiles();
      }
    } catch (e) {
      this._error = e.message;
      this._ingestTarget = null;
    }
  }

  /* --- ingest all pending. --- */

  async _handleIngestAll() {
    if (!(await this._gateIngest({ kind: "all", args: [] }))) return;
    await this._startIngestAll();
  }

  async _startIngestAll() {
    this._error = null;
    this._summary = null;
    this._progressLog = [];
    this._ingestTarget = "all pending";

    try {
      const res = await ingest.startAll(false, this._useEmbeddings);
      const taskId = res.task_id ?? res.taskId;
      if (taskId) {
        this._activeTask = taskId;
        this._listenProgress(taskId);
      } else {
        this._summary = res;
        await this._fetchFiles();
      }
    } catch (e) {
      this._error = e.message;
      this._ingestTarget = null;
    }
  }

  /* --- ingest selected batch. --- */

  async _handleIngestSelected(overwrite = false) {
    if (this._selected.size === 0) return;
    if (!(await this._gateIngest({ kind: "batch", args: [overwrite] }))) return;
    await this._startIngestSelected(overwrite);
  }

  async _startIngestSelected(overwrite) {
    this._error = null;
    this._summary = null;
    this._progressLog = [];
    this._ingestTarget = `${this._selected.size} selected files`;

    try {
      const res = await ingest.startBatch([...this._selected], overwrite, this._useEmbeddings);
      const taskId = res.task_id ?? res.taskId;
      if (taskId) {
        this._activeTask = taskId;
        this._listenProgress(taskId);
      } else {
        this._summary = res;
        this._selected = new Set();
        await this._fetchFiles();
      }
    } catch (e) {
      this._error = e.message;
      this._ingestTarget = null;
    }
  }

  /* --- reasoning gate: ensure llm reasoning is off before ingestion. ---
     returns true if the caller may proceed immediately, false if it should
     abort (either user cancelled or we queued the action behind a modal).
  */
  async _gateIngest(pending) {
    try {
      const status = await server.status();
      const reasoning = status?.llm_server?.reasoning;
      const running = status?.llm_server?.running;
      // if server is not running, let the ingest call surface its own 503.
      if (!running) return true;
      if (reasoning === "off") return true;

      // reasoning on — park the pending action and show the guard modal.
      this._pendingIngest = pending;
      this._showReasoningGuard = true;
      return false;
    } catch {
      // if status check fails, let the ingest call surface the error.
      return true;
    }
  }

  async _resumePendingIngest() {
    const p = this._pendingIngest;
    this._pendingIngest = null;
    this._showReasoningGuard = false;
    if (!p) return;
    if (p.kind === "one") {
      await this._startIngestOne(...p.args);
    } else if (p.kind === "all") {
      await this._startIngestAll();
    } else if (p.kind === "batch") {
      await this._startIngestSelected(...p.args);
    }
  }

  /** user chose "Continue anyway" — ingest with reasoning on (degraded quality). */
  async _reasoningContinueAnyway() {
    this._addToast("Proceeding with reasoning ON — entities may be incomplete.", "warning", 6000);
    await this._resumePendingIngest();
  }

  /** user chose "Cancel" — drop the queued action. */
  _reasoningCancel() {
    this._pendingIngest = null;
    this._showReasoningGuard = false;
  }

  /** user chose "Disable & Restart" — flip the shell variable, restart llm, wait, resume. */
  async _reasoningDisableAndRestart() {
    this._restartingLlm = true;
    try {
      await server.setConfig({ reasoning: "off" });
      await server.stop("llm");
      // give the process a beat to release the port before starting again.
      await new Promise(r => setTimeout(r, 800));
      await server.start("llm");

      // poll until the new process is ready and actually reports reasoning=off.
      const deadline = Date.now() + 90_000;
      while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 1500));
        try {
          const s = await server.status();
          if (s?.llm_server?.running && s?.llm_server?.reasoning === "off") {
            this._addToast("LLM restarted with reasoning OFF.", "success", 4000);
            this._restartingLlm = false;
            await this._resumePendingIngest();
            return;
          }
        } catch {
          /* poll failure, keep trying. */
        }
      }

      this._restartingLlm = false;
      this._error = "LLM server did not come back online within 90s. Check the Server panel.";
      this._pendingIngest = null;
      this._showReasoningGuard = false;
    } catch (e) {
      this._restartingLlm = false;
      this._error = `Failed to restart LLM with reasoning off: ${e.message}`;
      this._pendingIngest = null;
      this._showReasoningGuard = false;
    }
  }

  /* --- embed spin-up gate: confirm before auto-starting a 2nd server. --- */

  async _onEmbedToggleChange(checked) {
    if (!checked) {
      // turning off is always free — just unset.
      this._useEmbeddings = false;
      return;
    }

    // turning on — if the embed server is already up, skip the prompt.
    try {
      const status = await server.status();
      if (status?.embed_server?.running) {
        this._useEmbeddings = true;
        return;
      }
    } catch {
      /* status failed, fall through to prompt so the user still sees the cost. */
    }

    this._showEmbedSpinPrompt = true;
    // keep checkbox visually reflecting the current (still off) state until confirmed.
    this._useEmbeddings = false;
  }

  _confirmEmbedSpinUp() {
    this._useEmbeddings = true;
    this._showEmbedSpinPrompt = false;
    this._addToast(
      "Embeddings enabled — the BGE-M3 server will spin up on the next ingest.",
      "info", 6000,
    );
  }

  _cancelEmbedSpinUp() {
    this._showEmbedSpinPrompt = false;
  }

  /* --- SSE progress. --- */

  _listenProgress(taskId) {
    this._closeEventSource();
    this._embedWasManual = false;
    const es = ingest.progress(taskId);
    this._eventSource = es;

    es.addEventListener("message", (e) => {
      try {
        const msg = JSON.parse(e.data);
        const evt = msg.event;

        // embed server lifecycle toasts.
        if (evt === "embed_starting") {
          this._addToast(msg.message || "Starting embedding server...", "info", 8000);
        } else if (evt === "embed_ready") {
          this._addToast("Embedding server ready.", "success", 4000);
        } else if (evt === "embed_stopping") {
          this._addToast(msg.message || "Stopping embedding server...", "info", 5000);
        } else if (evt === "embed_was_manual") {
          this._embedWasManual = true;
        }

        // add to progress log.
        this._progressLog = [...this._progressLog, msg];
        this._autoScrollLog();

        // handle terminal events.
        if (evt === "complete" || evt === "cancelled") {
          this._closeEventSource();
          this._activeTask = null;
          this._summary = msg;
          this._ingestTarget = null;
          this._useEmbeddings = false;
          this._cancelling = false;
          this._fetchFiles();
          if (this._embedWasManual) {
            this._showEmbedPrompt = true;
          }
        } else if (evt === "error") {
          this._closeEventSource();
          this._activeTask = null;
          this._error = msg.message || "Ingestion failed.";
          this._ingestTarget = null;
          this._useEmbeddings = false;
          this._cancelling = false;
        }
      } catch (parseErr) {
        // non-JSON SSE line — treat as plain-text progress. `e` is the
        // outer MessageEvent from the SSE listener (still in scope here).
        console.warn("ingest SSE: failed to parse message, falling back to text", parseErr);
        this._progressLog = [...this._progressLog, { type: "info", text: e.data }];
        this._autoScrollLog();
      }
    });

    es.addEventListener("error", () => {
      // connection closed — if task still active, clean up.
      if (this._activeTask) {
        this._closeEventSource();
        this._activeTask = null;
        this._ingestTarget = null;
        this._cancelling = false;
      }
    });
  }

  /* --- toast notifications. --- */

  _addToast(message, type = "info", duration = 5000) {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    this._toasts = [...this._toasts, { id, message, type }];
    setTimeout(() => {
      this._toasts = this._toasts.filter((t) => t.id !== id);
    }, duration);
  }

  /* --- cancel ingestion. --- */

  async _handleCancel() {
    if (this._cancelling) return;           // ignore double-clicks.
    this._cancelling = true;
    try {
      await ingest.cancel();
      this._addToast(
        "Cancellation requested. Finishing the current file before stopping...",
        "warning", 8000,
      );
    } catch (e) {
      this._error = e.message;
      this._cancelling = false;
    }
    // _cancelling is reset when the SSE stream emits complete/cancelled/error.
  }

  /* --- embed server prompt actions. --- */

  async _stopEmbedAndDismiss() {
    this._showEmbedPrompt = false;
    try {
      await server.stop("embed");
      this._addToast("Embedding server stopped.", "success");
    } catch (e) {
      this._error = e.message;
    }
  }

  _autoScrollLog() {
    requestAnimationFrame(() => {
      const el = this.shadowRoot?.querySelector(".progress-log");
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  _logLineClass(msg) {
    const t = msg.type ?? msg.level ?? "";
    if (t === "stage") return "log-stage";
    if (t === "done" || t === "complete") return "log-done";
    if (t === "error") return "log-error";
    return "log-info";
  }

  _logLineText(msg) {
    return msg.text ?? msg.message ?? JSON.stringify(msg);
  }

  render() {
    const pending = this._pendingFiles();
    const ingesting = this._activeTask !== null;

    return html`
      <div class="panel-header">
        <h1>Ingest</h1>
        <p>Upload source files and ingest them into the wiki.</p>
      </div>

      ${this._error ? html`
        <div class="error-banner">
          <span>${this._error}</span>
          <button @click=${() => { this._error = null; }}>&times;</button>
        </div>
      ` : ""}

      <!-- pre-ingest reasoning guard -->
      ${this._showReasoningGuard ? this._renderReasoningGuard() : ""}

      <!-- pre-toggle embed spin-up confirmation -->
      ${this._showEmbedSpinPrompt ? this._renderEmbedSpinPrompt() : ""}

      <!-- embed server prompt -->
      ${this._showEmbedPrompt ? html`
        <div class="embed-prompt">
          <span class="embed-prompt-text">
            The embedding server was running before ingestion. Stop it to free ~2 GB RAM?
          </span>
          <button class="btn btn-primary" @click=${this._stopEmbedAndDismiss}>Stop server</button>
          <button class="btn btn-ghost" @click=${() => { this._showEmbedPrompt = false; }}>Keep running</button>
        </div>
      ` : ""}

      <!-- summary card -->
      ${this._summary ? this._renderSummary() : ""}

      <!-- progress panel -->
      ${ingesting ? this._renderProgress() : ""}

      <!-- upload zone -->
      <div class="drop-zone ${this._dragOver ? "drag-over" : ""} ${this._uploading ? "uploading" : ""}"
        @dragover=${this._onDragOver}
        @dragleave=${this._onDragLeave}
        @drop=${this._onDrop}>
        <input type="file" multiple @change=${this._onFileInput} />
        <div class="drop-icon">${this._uploading ? html`<span class="spinner" style="width:32px;height:32px;border-width:3px"></span>` : html`&#8613;`}</div>
        <div class="drop-label">${this._uploading ? "Uploading..." : "Drop files here or click to browse"}</div>
        <div class="drop-hint">Supported: PDF, markdown, XML, plain text</div>
      </div>

      <!-- toolbar -->
      <div class="toolbar">
        <div class="toggle-row">
          <label class="toggle-switch">
            <input type="checkbox" .checked=${this._useEmbeddings}
              @change=${(e) => this._onEmbedToggleChange(e.target.checked)} />
            <span class="toggle-track"></span>
          </label>
          <span>Use Embeddings (stage 5)</span>
        </div>

        <span class="spacer"></span>

        ${pending.length > 0 ? html`
          <button class="btn btn-primary"
            ?disabled=${ingesting}
            @click=${() => this._handleIngestAll()}>
            ${ingesting && this._ingestTarget === "all pending" ? html`<span class="spinner"></span>` : ""}
            Ingest All Pending (${pending.length})
          </button>
        ` : ""}
      </div>

      <!-- batch action bar -->
      ${this._selected.size > 0 ? html`
        <div class="batch-bar">
          <span class="batch-count">${this._selected.size} selected</span>
          <button class="btn btn-primary" ?disabled=${ingesting}
            @click=${() => this._handleIngestSelected(false)}>
            Ingest Selected
          </button>
          <button class="btn btn-ghost" ?disabled=${ingesting}
            @click=${() => this._handleIngestSelected(true)}>
            Re-ingest Selected
          </button>
          <button class="btn btn-ghost" @click=${() => { this._selected = new Set(); }}>
            Clear Selection
          </button>
        </div>
      ` : ""}

      <!-- file table -->
      ${this._files.length > 0 ? html`
        <div class="file-table-wrap">
          <table>
            <thead>
              <tr>
                <th class="col-check">
                  <input type="checkbox"
                    .checked=${this._selected.size === this._files.length && this._files.length > 0}
                    @change=${() => this._toggleSelectAll()} />
                </th>
                <th>Filename</th>
                <th>Status</th>
                <th>Size</th>
                <th style="text-align:right">Action</th>
              </tr>
            </thead>
            <tbody>
              ${this._files.map(f => html`
                <tr>
                  <td class="col-check">
                    <input type="checkbox"
                      .checked=${this._selected.has(f.filename)}
                      @change=${() => this._toggleSelect(f.filename)} />
                  </td>
                  <td class="col-name">${f.filename}</td>
                  <td>
                    <span class="badge badge-${f.status ?? "pending"}">${f.status ?? "pending"}</span>
                  </td>
                  <td class="col-size">${this._formatSize(f.size)}</td>
                  <td class="col-action">
                    ${f.status !== "ingested" ? html`
                      <button class="btn btn-ingest"
                        ?disabled=${ingesting}
                        @click=${() => this._handleIngestOne(f.filename)}>
                        ${ingesting && this._ingestTarget === f.filename ? html`<span class="spinner"></span>` : "Ingest"}
                      </button>
                    ` : html`
                      <button class="btn btn-ghost" style="padding:var(--sp-1) var(--sp-3);font-size:var(--text-xs)"
                        ?disabled=${ingesting}
                        @click=${() => this._handleIngestOne(f.filename, true)}>
                        Re-ingest
                      </button>
                    `}
                  </td>
                </tr>
              `)}
            </tbody>
          </table>
        </div>
      ` : html`
        <div class="empty-state">
          <div style="font-size:var(--text-4xl);opacity:0.3">&#128194;</div>
          <p>No source files yet. Upload files above to get started.</p>
        </div>
      `}

      <!-- toast notifications -->
      ${this._toasts.length > 0 ? html`
        <div class="toast-container">
          ${this._toasts.map((t) => html`
            <div class="toast toast-${t.type}">${t.message}</div>
          `)}
        </div>
      ` : ""}
    `;
  }

  _renderProgress() {
    return html`
      <div class="progress-panel">
        <div class="progress-header">
          <span class="progress-live-dot"></span>
          <span class="progress-title">${this._cancelling ? "Cancelling" : "Ingesting"}</span>
          <span class="progress-target">${this._ingestTarget}</span>
          <span class="spacer"></span>
          <button class="btn btn-cancel"
            ?disabled=${this._cancelling}
            @click=${this._handleCancel}>
            ${this._cancelling ? html`<span class="spinner"></span>Cancelling…` : "Cancel"}
          </button>
        </div>
        <div class="progress-log">
          ${this._progressLog.length === 0
            ? html`<div class="log-line log-info">Waiting for server...</div>`
            : this._progressLog.map(msg => html`
                <div class="log-line ${this._logLineClass(msg)}">${this._logLineText(msg)}</div>
              `)}
        </div>
      </div>
    `;
  }

  _renderSummary() {
    const s = this._summary;
    return html`
      <div class="summary-card">
        <h3>Ingestion Complete</h3>
        <div class="summary-stats">
          ${s.pages_created != null ? html`
            <div class="stat-item">
              <span class="stat-value">${s.pages_created}</span>
              <span class="stat-label">Pages Created</span>
            </div>` : ""}
          ${s.pages_updated != null ? html`
            <div class="stat-item">
              <span class="stat-value">${s.pages_updated}</span>
              <span class="stat-label">Pages Updated</span>
            </div>` : ""}
          ${s.entities != null ? html`
            <div class="stat-item">
              <span class="stat-value">${s.entities}</span>
              <span class="stat-label">Entities</span>
            </div>` : ""}
          ${s.concepts != null ? html`
            <div class="stat-item">
              <span class="stat-value">${s.concepts}</span>
              <span class="stat-label">Concepts</span>
            </div>` : ""}
          ${s.duration != null ? html`
            <div class="stat-item">
              <span class="stat-value">${s.duration}s</span>
              <span class="stat-label">Duration</span>
            </div>` : ""}
          ${s.message && !s.pages_created ? html`
            <div class="stat-item">
              <span class="stat-value" style="font-size:var(--text-sm);font-weight:400">${s.message}</span>
            </div>` : ""}
        </div>
        <div class="summary-dismiss">
          <button class="btn btn-ghost" @click=${() => { this._summary = null; }}>Dismiss</button>
        </div>
      </div>
    `;
  }

  _renderReasoningGuard() {
    const restarting = this._restartingLlm;
    return html`
      <div class="modal-overlay" @click=${(e) => { if (e.target === e.currentTarget && !restarting) this._reasoningCancel(); }}>
        <div class="modal-card">
          <h3 class="modal-title">Reasoning is ON — not safe for ingestion</h3>
          <div class="modal-body">
            <p>
              The LLM server currently has <code>REASONING="on"</code>. Gemma 4's
              <code>&lt;think&gt;</code> pass burns the output token budget on reasoning
              before emitting the entity-extraction JSON, leaving pages with empty titles
              and no descriptions.
            </p>
            <p>
              For clean ingestion, disable reasoning and restart the LLM server.
              You can turn it back on from the Server panel any time.
            </p>
            ${restarting ? html`
              <div class="modal-callout">
                Restarting LLM with reasoning OFF… this can take ~30–60 seconds.
              </div>
            ` : ""}
          </div>
          <div class="modal-actions">
            <button class="btn btn-ghost"
              ?disabled=${restarting}
              @click=${() => this._reasoningCancel()}>
              Cancel
            </button>
            <button class="btn btn-ghost"
              ?disabled=${restarting}
              @click=${() => this._reasoningContinueAnyway()}>
              Continue anyway
            </button>
            <button class="btn btn-primary"
              ?disabled=${restarting}
              @click=${() => this._reasoningDisableAndRestart()}>
              ${restarting ? html`<span class="spinner"></span>Restarting…` : "Disable & Restart"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _renderEmbedSpinPrompt() {
    return html`
      <div class="modal-overlay" @click=${(e) => { if (e.target === e.currentTarget) this._cancelEmbedSpinUp(); }}>
        <div class="modal-card">
          <h3 class="modal-title">Start the embedding server?</h3>
          <div class="modal-body">
            <p>
              Stage 5 uses a second llama.cpp instance running BGE-M3 on port 8081.
              Enabling this will automatically spin it up when ingestion starts.
            </p>
            <p>
              Expected cost: <strong>~2.2 GB RAM</strong> while the server is running.
              It will shut down automatically when ingestion completes (unless you
              started it manually from the Server panel).
            </p>
          </div>
          <div class="modal-actions">
            <button class="btn btn-ghost" @click=${() => this._cancelEmbedSpinUp()}>
              Cancel
            </button>
            <button class="btn btn-primary" @click=${() => this._confirmEmbedSpinUp()}>
              Enable embeddings
            </button>
          </div>
        </div>
      </div>
    `;
  }
}

customElements.define("ingest-panel", IngestPanel);
