import { LitElement, html, css } from "lit";
import { unsafeHTML } from "lit/directives/unsafe-html.js";
import { query } from "../lib/api.js";
import { renderMarkdown } from "../lib/markdown.js";
import { icons } from "../lib/icons.js";

/* ------------------------------------------------------------------ */
/*  persistence helpers.                                               */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = "sb_chat";
const MAX_CONVERSATIONS = 30;
const MAX_MESSAGES_PER_CONV = 100;

/** @returns {{ active: string, conversations: object[] }} */
function loadStore() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : { active: "", conversations: [] };
  } catch {
    return { active: "", conversations: [] };
  }
}

/** @param {{ active: string, conversations: object[] }} store */
function saveStore(store) {
  const trimmed = {
    ...store,
    conversations: store.conversations.slice(0, MAX_CONVERSATIONS).map((conv) => ({
      ...conv,
      messages: conv.messages.slice(-MAX_MESSAGES_PER_CONV),
    })),
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch (e) {
    // localStorage quota exceeded — trim oldest conversations.
    trimmed.conversations = trimmed.conversations.slice(0, 10);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
    } catch {
      /* silent — user loses persistence but app keeps working. */
    }
  }
}

function uid() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * default settings applied to brand-new conversations. existing conversations
 * without a `settings` object fall back to these values, and the active
 * conversation's settings override the ui state when a chat is opened.
 */
const DEFAULT_CONV_SETTINGS = Object.freeze({
  reasoning: true,
  webSearch: false,
  webResultsCount: 10,
  searchEngine: "duckduckgo",
});

function newConversation() {
  return {
    id: uid(),
    title: "",
    messages: [],
    created: Date.now(),
    updated: Date.now(),
    settings: { ...DEFAULT_CONV_SETTINGS },
  };
}

/* ------------------------------------------------------------------ */
/*  component.                                                         */
/* ------------------------------------------------------------------ */

export class QueryPanel extends LitElement {
  static properties = {
    _conversations:   { state: true },
    _activeId:        { state: true },
    _input:           { state: true },
    _phase:           { state: true },
    _error:           { state: true },
    _sidebarOpen:     { state: true },
    _expandedSources: { state: true },
    _saveNext:        { state: true },
    _reasoning:       { state: true },
    _webSearch:       { state: true },
    _webResultsCount: { state: true },
    _searchEngine:    { state: true },
    _savingMsgId:     { state: true },
  };

  /* ---------- styles. ---------- */

  static styles = css`
    :host {
      display: grid;
      grid-template-columns: 1fr;
      height: calc(100dvh - var(--header-height) - var(--sp-8));
      max-width: min(960px, 100%);
      width: 100%;
    }
    :host([sidebar-open]) {
      grid-template-columns: 1fr 240px;
      gap: var(--sp-4);
    }

    /* --- main column. --- */
    .chat-column {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }

    /* --- header. --- */
    .chat-header {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding-bottom: var(--sp-4);
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }
    .chat-title {
      font-family: var(--font-heading);
      font-size: var(--text-2xl);
      color: var(--text-primary);
      font-weight: 400;
      flex: 1;
    }
    .header-btn {
      font-size: var(--text-sm);
      color: var(--text-muted);
      background: none;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: var(--sp-1) var(--sp-3);
      cursor: pointer;
      transition: color var(--duration-fast) var(--ease-out),
                  border-color var(--duration-fast) var(--ease-out),
                  background var(--duration-fast) var(--ease-out);
    }
    .header-btn:hover {
      color: var(--text-primary);
      border-color: var(--border-light);
      background: var(--bg-card);
    }

    /* --- message area. --- */
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: var(--sp-6) 0;
      display: flex;
      flex-direction: column;
      gap: var(--sp-5);
      scroll-behavior: smooth;
    }
    .messages::-webkit-scrollbar { width: 6px; }
    .messages::-webkit-scrollbar-track { background: transparent; }
    .messages::-webkit-scrollbar-thumb {
      background: var(--border);
      border-radius: 3px;
    }

    .empty-state {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--sp-4);
      color: var(--text-muted);
    }
    .empty-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 64px;
      height: 64px;
      color: var(--accent);
      opacity: 0.35;
    }
    .empty-icon svg {
      width: 100%;
      height: 100%;
    }
    .empty-text {
      font-size: var(--text-lg);
      font-family: var(--font-heading);
    }
    .empty-hint {
      font-size: var(--text-sm);
      max-width: 360px;
      text-align: center;
      line-height: 1.5;
    }

    /* --- user message. --- */
    .msg-user {
      display: flex;
      justify-content: flex-end;
    }
    .msg-user-bubble {
      max-width: 75%;
      padding: var(--sp-3) var(--sp-5);
      background: oklch(75% 0.15 70 / 0.12);
      border: 1px solid oklch(75% 0.15 70 / 0.2);
      border-radius: var(--radius-lg) var(--radius-lg) var(--radius-sm) var(--radius-lg);
      color: var(--text-primary);
      font-size: var(--text-base);
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
    }

    /* --- assistant message. --- */
    .msg-assistant {
      display: flex;
      flex-direction: column;
      gap: var(--sp-2);
    }
    .msg-meta {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding-left: var(--sp-1);
    }
    .msg-meta-label {
      font-size: var(--text-xs);
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .source-badge {
      display: inline-flex;
      align-items: center;
      padding: 2px var(--sp-2);
      font-size: var(--text-xs);
      font-weight: 600;
      border-radius: 999px;
      letter-spacing: 0.03em;
    }
    .source-wiki {
      background: oklch(72% 0.12 250 / 0.15);
      color: oklch(72% 0.12 250);
    }
    .source-model {
      background: oklch(75% 0.14 70 / 0.15);
      color: oklch(75% 0.14 70);
    }
    .source-hybrid {
      background: oklch(72% 0.12 300 / 0.15);
      color: oklch(72% 0.12 300);
    }
    .source-none {
      background: oklch(50% 0.01 260 / 0.15);
      color: var(--text-muted);
    }
    .source-web {
      background: oklch(70% 0.12 165 / 0.15);
      color: oklch(70% 0.12 165);
    }

    .msg-body {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-5) var(--sp-6);
      font-size: var(--text-base);
      line-height: 1.75;
      color: var(--text-primary);
    }
    .msg-body h1, .msg-body h2, .msg-body h3 {
      margin-top: var(--sp-5); margin-bottom: var(--sp-2);
    }
    .msg-body h1 { font-size: var(--text-xl); }
    .msg-body h2 { font-size: var(--text-lg); }
    .msg-body p  { margin-bottom: var(--sp-3); }
    .msg-body ul, .msg-body ol {
      margin-bottom: var(--sp-3); padding-left: var(--sp-6);
    }
    .msg-body li { margin-bottom: var(--sp-1); }
    .msg-body code {
      font-family: var(--font-mono);
      font-size: 0.88em;
      background: var(--bg-input);
      padding: 2px 6px;
      border-radius: var(--radius-sm);
    }
    .msg-body pre {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: var(--sp-3);
      overflow-x: auto;
      margin-bottom: var(--sp-3);
    }
    .msg-body pre code { background: none; padding: 0; }
    .msg-body a {
      color: var(--link);
      text-decoration: underline;
      text-decoration-color: oklch(72% 0.12 250 / 0.4);
      text-underline-offset: 3px;
    }
    .msg-body a:hover { color: var(--link-hover); }
    .msg-body blockquote {
      border-left: 3px solid var(--accent-dim);
      margin: var(--sp-3) 0;
      padding: var(--sp-2) var(--sp-4);
      color: var(--text-secondary);
      background: oklch(75% 0.15 70 / 0.03);
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    }

    /* --- sources (per message). --- */
    .msg-sources {
      border-top: 1px solid var(--border);
      margin-top: var(--sp-2);
      padding-top: var(--sp-3);
    }
    .sources-toggle {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
      background: none;
      border: none;
      color: var(--text-muted);
      font-family: var(--font-body);
      font-size: var(--text-xs);
      cursor: pointer;
      padding: 0;
      transition: color var(--duration-fast);
    }
    .sources-toggle:hover { color: var(--text-secondary); }
    .sources-chevron {
      font-size: 0.6rem;
      transition: transform var(--duration-fast) var(--ease-out);
    }
    .sources-toggle[aria-expanded="true"] .sources-chevron {
      transform: rotate(180deg);
    }
    .sources-chips {
      display: flex;
      flex-wrap: wrap;
      gap: var(--sp-1);
      padding-top: var(--sp-2);
    }
    .source-chip {
      display: inline-flex;
      padding: 2px var(--sp-2);
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      font-size: var(--text-xs);
      color: var(--link);
      text-decoration: none;
      transition: background var(--duration-fast), border-color var(--duration-fast);
    }
    .source-chip:hover {
      background: var(--bg-card-hover);
      border-color: var(--link);
    }

    /* --- thinking indicator. --- */
    .thinking {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      padding: var(--sp-3) var(--sp-5);
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      align-self: flex-start;
    }
    .thinking-dots {
      display: flex;
      gap: 4px;
    }
    .thinking-dot {
      width: 6px;
      height: 6px;
      background: var(--accent);
      border-radius: 50%;
      animation: pulse 1.2s ease-in-out infinite;
    }
    .thinking-dot:nth-child(2) { animation-delay: 0.15s; }
    .thinking-dot:nth-child(3) { animation-delay: 0.3s; }
    @keyframes pulse {
      0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
      40% { opacity: 1; transform: scale(1); }
    }
    .thinking-label {
      font-size: var(--text-sm);
      color: var(--text-muted);
    }
    .thinking-step {
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .thinking-step.active { color: var(--accent); font-weight: 500; }
    .thinking-step.done { color: var(--success); }

    /* --- error. --- */
    .error-bubble {
      padding: var(--sp-3) var(--sp-5);
      background: oklch(65% 0.15 25 / 0.08);
      border: 1px solid oklch(65% 0.15 25 / 0.25);
      border-radius: var(--radius-md);
      color: var(--error);
      font-size: var(--text-sm);
    }

    /* --- input area. --- */
    .input-area {
      flex-shrink: 0;
      padding-top: var(--sp-4);
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: var(--sp-3);
    }
    .input-row {
      display: flex;
      gap: var(--sp-3);
      align-items: flex-end;
    }
    textarea {
      flex: 1;
      height: 48px;
      resize: none;
      overflow-y: auto;
      background: var(--bg-input);
      color: var(--text-primary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: var(--sp-3) var(--sp-4);
      font-family: var(--font-body);
      font-size: var(--text-base);
      line-height: 1.5;
      transition: border-color var(--duration-fast) var(--ease-out);
    }
    textarea::placeholder { color: var(--text-muted); }
    textarea:focus {
      outline: none;
      border-color: var(--accent-dim);
    }
    .send-btn {
      padding: var(--sp-3) var(--sp-5);
      background: var(--accent);
      color: var(--bg-deep);
      border: none;
      border-radius: var(--radius-md);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      font-weight: 600;
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out),
                  transform var(--duration-fast) var(--ease-out);
      white-space: nowrap;
    }
    .send-btn:hover:not(:disabled) {
      background: var(--accent-hover);
      transform: translateY(-1px);
    }
    .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

    .stop-btn {
      padding: var(--sp-3) var(--sp-5);
      background: oklch(65% 0.15 25 / 0.15);
      color: var(--error);
      border: 1px solid oklch(65% 0.15 25 / 0.3);
      border-radius: var(--radius-md);
      font-family: var(--font-body);
      font-size: var(--text-sm);
      font-weight: 600;
      cursor: pointer;
      transition: background var(--duration-fast) var(--ease-out);
      white-space: nowrap;
    }
    .stop-btn:hover {
      background: oklch(65% 0.15 25 / 0.25);
    }

    .input-options {
      display: flex;
      align-items: center;
      gap: var(--sp-3);
      flex-wrap: wrap;
    }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: var(--sp-2);
    }
    .toggle-label {
      font-size: var(--text-xs);
      color: var(--text-muted);
      cursor: pointer;
      user-select: none;
    }
    .toggle-check {
      width: 14px;
      height: 14px;
      accent-color: var(--accent);
      cursor: pointer;
    }

    /* --- pill toggles for reasoning / web search. --- */
    .pill-toggle {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px var(--sp-3);
      font-size: var(--text-xs);
      font-weight: 500;
      color: var(--text-muted);
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 999px;
      cursor: pointer;
      user-select: none;
      transition: color var(--duration-fast), background var(--duration-fast),
                  border-color var(--duration-fast);
    }
    .pill-toggle:hover {
      color: var(--text-secondary);
      border-color: var(--border-light);
    }
    .pill-toggle[aria-pressed="true"] {
      color: var(--bg-deep);
      border-color: transparent;
    }
    .pill-toggle.pill-reasoning[aria-pressed="true"] {
      background: oklch(72% 0.12 300);
    }
    .pill-toggle.pill-web[aria-pressed="true"] {
      background: oklch(70% 0.12 165);
    }
    .pill-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
      opacity: 0.5;
    }
    .pill-toggle[aria-pressed="true"] .pill-dot {
      opacity: 1;
      box-shadow: 0 0 4px currentColor;
    }

    /* --- web results count selector. --- */
    .web-count-row {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: var(--text-xs);
      color: var(--text-muted);
    }
    .web-count-row select {
      background: var(--bg-input);
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 1px 4px;
      font-size: var(--text-xs);
      font-family: var(--font-body);
      cursor: pointer;
    }

    /* --- post-answer actions. --- */
    .msg-actions {
      display: flex;
      gap: var(--sp-2);
      margin-top: var(--sp-2);
      padding-top: var(--sp-2);
    }
    .msg-action-btn {
      font-size: var(--text-xs);
      color: var(--text-muted);
      background: none;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 2px var(--sp-2);
      cursor: pointer;
      font-family: var(--font-body);
      transition: color var(--duration-fast), border-color var(--duration-fast),
                  background var(--duration-fast);
    }
    .msg-action-btn:hover {
      color: var(--text-primary);
      border-color: var(--border-light);
      background: var(--bg-input);
    }
    .msg-action-btn.saved {
      color: var(--success);
      border-color: oklch(70% 0.12 165 / 0.3);
    }

    .hint {
      font-size: var(--text-xs);
      color: var(--text-muted);
      margin-left: auto;
    }

    /* --- sidebar. --- */
    .sidebar {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: var(--sp-4);
      overflow-y: auto;
      align-self: start;
      position: sticky;
      top: 0;
      max-height: calc(100dvh - var(--header-height) - var(--sp-12));
    }
    .sidebar-title {
      font-family: var(--font-heading);
      font-size: var(--text-lg);
      color: var(--text-primary);
      margin-bottom: var(--sp-3);
      padding-bottom: var(--sp-2);
      border-bottom: 1px solid var(--border);
    }
    .conv-item {
      display: block;
      width: 100%;
      padding: var(--sp-2) var(--sp-3);
      border-radius: var(--radius-sm);
      cursor: pointer;
      border: none;
      background: none;
      text-align: left;
      font-family: var(--font-body);
      transition: background var(--duration-fast);
    }
    .conv-item:hover { background: var(--bg-card); }
    .conv-item.active { background: var(--bg-card); border-left: 2px solid var(--accent); }
    .conv-item + .conv-item { margin-top: 2px; }
    .conv-title {
      font-size: var(--text-sm);
      color: var(--text-primary);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      line-height: 1.3;
    }
    .conv-meta {
      font-size: var(--text-xs);
      color: var(--text-muted);
      margin-top: 2px;
    }
    .sidebar-empty {
      font-size: var(--text-sm);
      color: var(--text-muted);
      font-style: italic;
    }
    .sidebar-clear {
      display: block;
      margin-top: var(--sp-3);
      padding-top: var(--sp-2);
      border-top: 1px solid var(--border);
      font-size: var(--text-xs);
      color: var(--text-muted);
      background: none;
      border: none;
      border-top: 1px solid var(--border);
      cursor: pointer;
      width: 100%;
      text-align: center;
    }
    .sidebar-clear:hover { color: var(--error); }
  `;

  /* ---------- lifecycle. ---------- */

  constructor() {
    super();
    const store = loadStore();
    this._conversations = store.conversations;
    this._activeId = store.active;
    this._input = "";
    this._phase = "idle";
    this._error = "";
    this._sidebarOpen = false;
    this._expandedSources = new Set();
    this._saveNext = false;
    this._savingMsgId = null;
    this._abortController = null;

    // ensure at least one conversation exists.
    if (!this._conversations.length || !this._activeId) {
      const conv = newConversation();
      this._conversations = [conv];
      this._activeId = conv.id;
      this._persist();
    }

    // restore settings from the active conversation. legacy conversations
    // without a `settings` object fall back to defaults.
    this._loadSettingsFromActive();
  }

  updated(changed) {
    if (changed.has("_sidebarOpen")) {
      this._sidebarOpen
        ? this.setAttribute("sidebar-open", "")
        : this.removeAttribute("sidebar-open");
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._abort();
  }

  /* ---------- state helpers. ---------- */

  get _active() {
    return this._conversations.find((c) => c.id === this._activeId) || this._conversations[0];
  }

  get _messages() {
    return this._active?.messages || [];
  }

  get _isLoading() {
    return (
      this._phase !== "idle" &&
      this._phase !== "done" &&
      this._phase !== "error"
    );
  }

  _persist() {
    saveStore({ active: this._activeId, conversations: this._conversations });
  }

  /**
   * copy settings from the active conversation into ui state. legacy
   * conversations without a `settings` object fall back to the defaults.
   */
  _loadSettingsFromActive() {
    const conv = this._active;
    const settings = { ...DEFAULT_CONV_SETTINGS, ...(conv?.settings || {}) };
    this._reasoning = settings.reasoning;
    this._webSearch = settings.webSearch;
    this._webResultsCount = settings.webResultsCount;
    this._searchEngine = settings.searchEngine;
  }

  /**
   * snapshot current ui settings back onto the active conversation so they
   * persist across tab switches, reloads, and conversation switches.
   */
  _saveSettingsToActive() {
    const conv = this._active;
    if (!conv) return;
    conv.settings = {
      reasoning: this._reasoning,
      webSearch: this._webSearch,
      webResultsCount: this._webResultsCount,
      searchEngine: this._searchEngine,
    };
    this._conversations = [...this._conversations];
    this._persist();
  }

  _addMessage(role, content, meta = {}) {
    const msg = { id: uid(), role, content, timestamp: Date.now(), ...meta };
    const conv = this._active;
    conv.messages = [...conv.messages, msg];
    conv.updated = Date.now();
    if (!conv.title && role === "user") {
      conv.title = content.length > 60 ? content.slice(0, 57) + "…" : content;
    }
    this._conversations = [...this._conversations];
    this._persist();
    return msg;
  }

  _newChat() {
    const conv = newConversation();
    this._conversations = [conv, ...this._conversations];
    this._activeId = conv.id;
    this._phase = "idle";
    this._error = "";
    this._input = "";
    this._persist();
    // new chat resets ui back to defaults (matches the fresh conv.settings).
    this._loadSettingsFromActive();
  }

  _switchTo(id) {
    if (id === this._activeId) return;
    this._abort();
    this._activeId = id;
    this._phase = "idle";
    this._error = "";
    this._persist();
    // restore the settings this conversation was last using.
    this._loadSettingsFromActive();
    this.requestUpdate();
    requestAnimationFrame(() => this._scrollToBottom());
  }

  _deleteConversation(id) {
    this._conversations = this._conversations.filter((c) => c.id !== id);
    if (this._activeId === id) {
      if (this._conversations.length === 0) {
        const conv = newConversation();
        this._conversations = [conv];
        this._activeId = conv.id;
      } else {
        this._activeId = this._conversations[0].id;
      }
      this._loadSettingsFromActive();
    }
    this._persist();
  }

  _clearAll() {
    const conv = newConversation();
    this._conversations = [conv];
    this._activeId = conv.id;
    this._phase = "idle";
    this._persist();
    this._loadSettingsFromActive();
  }

  /* ---------- scroll. ---------- */

  _scrollToBottom() {
    const el = this.renderRoot?.querySelector(".messages");
    if (el) el.scrollTop = el.scrollHeight;
  }

  /* ---------- submission. ---------- */

  _abort() {
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
      this._phase = "idle";
    }
  }

  async _handleSubmit() {
    const q = this._input.trim();
    if (!q || this._isLoading) return;

    this._input = "";
    this._error = "";
    this._phase = "routing";

    this._addMessage("user", q);
    await this.updateComplete;
    this._scrollToBottom();

    // send full conversation history (excluding the message we just added).
    // the backend applies its own budget-based truncation so we send everything.
    const history = this._messages
      .slice(0, -1)
      .map(({ role, content }) => ({ role, content }));

    this._abortController = new AbortController();

    try {
      let finalSource = "";
      let finalRoute = "";
      let finalSources = [];

      const signal = this._abortController.signal;
      const opts = {
        reasoning: this._reasoning,
        web_search: this._webSearch,
        web_results_count: this._webResultsCount,
        save: this._saveNext,
        search_engine: this._searchEngine,
      };

      for await (const { event, data } of query.chatStream(q, history, opts, signal)) {
        switch (event) {
          case "route_start":
            this._phase = "routing";
            break;

          case "route_complete":
            finalRoute = data.route || "";
            break;

          case "search_start":
            this._phase = "searching";
            break;

          case "search_complete":
            break;

          case "web_search_start":
            this._phase = "web_searching";
            break;

          case "web_search_complete":
            break;

          case "generation_start":
            this._phase = "generating";
            finalSource = data.source || "";
            finalRoute = data.route || finalRoute;
            break;

          case "generation_complete":
            finalSource = data.source || finalSource;
            finalRoute = data.route || finalRoute;
            finalSources = data.sources || [];
            this._addMessage("assistant", data.answer || "", {
              source: finalSource,
              route: finalRoute,
              sources: finalSources,
              saved_path: data.saved_path || null,
            });
            this._phase = "done";
            break;

          case "error":
            this._error = data.message || "An error occurred during generation.";
            this._phase = "error";
            break;
        }

        await this.updateComplete;
        this._scrollToBottom();
      }

      if (this._phase !== "done" && this._phase !== "error") {
        this._phase = "done";
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        this._error = err.message;
        this._phase = "error";
      }
    } finally {
      this._abortController = null;
      this._saveNext = false;
      await this.updateComplete;
      this._scrollToBottom();
    }
  }

  _handleKeydown(e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      this._handleSubmit();
    }
  }

  _toggleSources(msgId) {
    const next = new Set(this._expandedSources);
    next.has(msgId) ? next.delete(msgId) : next.add(msgId);
    this._expandedSources = next;
  }

  /* ---------- post-answer save. ---------- */

  async _handleSaveAnswer(msg) {
    if (this._savingMsgId || msg.saved_path) return;

    // find the user question that preceded this assistant message.
    const msgs = this._messages;
    const idx = msgs.findIndex(m => m.id === msg.id);
    const userMsg = msgs.slice(0, idx).reverse().find(m => m.role === "user");
    if (!userMsg) return;

    this._savingMsgId = msg.id;
    try {
      const res = await query.saveAnswer(userMsg.content, msg.content, msg.sources || []);
      // update the message with saved_path.
      msg.saved_path = res.saved_path;
      this._conversations = [...this._conversations];
      this._persist();
    } catch (err) {
      this._error = err.message;
    } finally {
      this._savingMsgId = null;
    }
  }

  /* ---------- display helpers. ---------- */

  _sourceLabel(source) {
    const labels = {
      wiki: "From your documents",
      model: "From the model",
      "wiki + model": "Documents + model",
      "wiki + web": "Documents + web",
      "model + web": "Model + web",
      "wiki + model + web": "All sources",
      none: "Declined",
    };
    return labels[source] || "";
  }

  _sourceBadgeClass(source) {
    const classes = {
      wiki: "source-wiki",
      model: "source-model",
      "wiki + model": "source-hybrid",
      "wiki + web": "source-web",
      "model + web": "source-web",
      "wiki + model + web": "source-web",
      none: "source-none",
    };
    return classes[source] || "";
  }

  _phaseLabel() {
    const labels = {
      routing: "Classifying intent…",
      searching: "Searching wiki…",
      web_searching: "Searching the web…",
      generating: "Generating answer…",
    };
    return labels[this._phase] || "Thinking…";
  }

  _relativeTime(ts) {
    const diff = Date.now() - ts;
    if (diff < 60_000) return "just now";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  /* ---------- render. ---------- */

  render() {
    return html`
      <div class="chat-column">
        ${this._renderHeader()}
        ${this._messages.length === 0 && !this._isLoading
          ? this._renderEmpty()
          : this._renderMessages()}
        ${this._renderInput()}
      </div>
      ${this._sidebarOpen ? this._renderSidebar() : ""}
    `;
  }

  _renderHeader() {
    return html`
      <div class="chat-header">
        <h1 class="chat-title">Chat</h1>
        <button class="header-btn" @click=${this._newChat}>New chat</button>
        <button class="header-btn" @click=${() => { this._sidebarOpen = !this._sidebarOpen; }}>
          ${this._sidebarOpen ? "Hide" : "History"}
        </button>
      </div>
    `;
  }

  _renderEmpty() {
    return html`
      <div class="empty-state">
        <div class="empty-icon" aria-hidden="true">${icons.message()}</div>
        <div class="empty-text">Ask your wiki anything</div>
        <div class="empty-hint">
          Questions about your documents search the wiki.
          General questions go directly to Gemma.
          The router decides automatically.
        </div>
      </div>
    `;
  }

  _renderMessages() {
    return html`
      <div class="messages">
        ${this._messages.map((msg) =>
          msg.role === "user"
            ? this._renderUserMsg(msg)
            : this._renderAssistantMsg(msg),
        )}
        ${this._isLoading ? this._renderThinking() : ""}
        ${this._phase === "error" ? html`
          <div class="error-bubble">${this._error}</div>
        ` : ""}
      </div>
    `;
  }

  _renderUserMsg(msg) {
    return html`
      <div class="msg-user">
        <div class="msg-user-bubble">${msg.content}</div>
      </div>
    `;
  }

  _renderAssistantMsg(msg) {
    const hasSources = msg.sources?.length > 0;
    const isExpanded = this._expandedSources.has(msg.id);

    return html`
      <div class="msg-assistant">
        <div class="msg-meta">
          <span class="msg-meta-label">Assistant</span>
          ${msg.source ? html`
            <span class="source-badge ${this._sourceBadgeClass(msg.source)}">
              ${this._sourceLabel(msg.source)}
            </span>
          ` : ""}
        </div>
        <div class="msg-body">
          <!-- renderMarkdown already runs the result through DOMPurify with
               a strict allowlist, so unsafeHTML here is only "unsafe" in
               the lit sense (bypasses text escaping), not in the XSS sense. -->
          <div>${unsafeHTML(renderMarkdown(msg.content))}</div>
          ${hasSources ? html`
            <div class="msg-sources">
              <button
                class="sources-toggle"
                aria-expanded="${isExpanded}"
                @click=${() => this._toggleSources(msg.id)}
              >
                <span>Sources (${msg.sources.length})</span>
                <span class="sources-chevron">▼</span>
              </button>
              ${isExpanded ? html`
                <div class="sources-chips">
                  ${msg.sources.map((src) => {
                    const name = typeof src === "string" ? src : (src.name || "");
                    const clean = name.replace(/\.md$/, "");
                    return html`
                      <a class="source-chip" href="#/page/_/${encodeURIComponent(clean)}">
                        ${clean}
                      </a>
                    `;
                  })}
                </div>
              ` : ""}
            </div>
          ` : ""}
          <div class="msg-actions">
            ${msg.saved_path
              ? html`<span class="msg-action-btn saved">Saved</span>`
              : html`<button class="msg-action-btn"
                  ?disabled=${this._savingMsgId === msg.id}
                  @click=${() => this._handleSaveAnswer(msg)}>
                  ${this._savingMsgId === msg.id ? "Saving…" : "Save to wiki"}
                </button>`
            }
          </div>
        </div>
      </div>
    `;
  }

  _renderThinking() {
    const p = this._phase;
    const phases = ["routing", "searching", "web_searching", "generating"];
    const idx = phases.indexOf(p);

    const step = (label, phaseKey) => {
      const i = phases.indexOf(phaseKey);
      const cls = p === phaseKey ? "active" : idx > i ? "done" : "";
      return html`<span class="thinking-step ${cls}">${label}</span>`;
    };

    return html`
      <div class="thinking">
        <div class="thinking-dots">
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
        </div>
        <span class="thinking-label">${this._phaseLabel()}</span>
        ${step("Route", "routing")}
        ${step("Wiki", "searching")}
        ${this._webSearch ? step("Web", "web_searching") : ""}
        ${step("Generate", "generating")}
      </div>
    `;
  }

  _renderInput() {
    return html`
      <div class="input-area">
        <div class="input-row">
          <textarea
            .value=${this._input}
            @input=${(e) => { this._input = e.target.value; }}
            @keydown=${this._handleKeydown}
            placeholder="Ask anything…"
            ?disabled=${this._isLoading}
            rows="1"
          ></textarea>
          ${this._isLoading
            ? html`<button class="stop-btn" @click=${() => this._abort()}>Stop</button>`
            : html`<button
                class="send-btn"
                ?disabled=${!this._input.trim()}
                @click=${this._handleSubmit}
              >Send</button>`
          }
        </div>
        <div class="input-options">
          <button
            class="pill-toggle pill-reasoning"
            role="switch"
            aria-pressed="${this._reasoning}"
            @click=${() => {
              this._reasoning = !this._reasoning;
              this._saveSettingsToActive();
            }}
            ?disabled=${this._isLoading}
            title="When on, the model reasons step-by-step before answering (slower but higher quality). Saved per conversation."
          >
            <span class="pill-dot"></span>
            Reasoning
          </button>
          <button
            class="pill-toggle pill-web"
            role="switch"
            aria-pressed="${this._webSearch}"
            @click=${() => {
              this._webSearch = !this._webSearch;
              this._saveSettingsToActive();
            }}
            ?disabled=${this._isLoading}
            title="When on, searches the web to enrich the answer with up-to-date information. Saved per conversation."
          >
            <span class="pill-dot"></span>
            Web search
          </button>
          ${this._webSearch ? html`
            <span class="web-count-row">
              <select
                .value=${this._searchEngine}
                @change=${(e) => {
                  this._searchEngine = e.target.value;
                  this._saveSettingsToActive();
                }}
                ?disabled=${this._isLoading}
                title="Search engine"
              >
                <option value="duckduckgo" ?selected=${this._searchEngine === "duckduckgo"}>DuckDuckGo</option>
                <option value="google" ?selected=${this._searchEngine === "google"}>Google</option>
                <option value="bing" ?selected=${this._searchEngine === "bing"}>Bing</option>
              </select>
              <select
                .value=${String(this._webResultsCount)}
                @change=${(e) => {
                  this._webResultsCount = parseInt(e.target.value, 10);
                  this._saveSettingsToActive();
                }}
                ?disabled=${this._isLoading}
              >
                ${[3, 5, 10, 15, 20, 30].map(n => html`
                  <option value="${n}" ?selected=${this._webResultsCount === n}>${n}</option>
                `)}
              </select>
              results
            </span>
          ` : ""}
          <div class="toggle-row">
            <input
              type="checkbox"
              class="toggle-check"
              id="save-toggle"
              .checked=${this._saveNext}
              @change=${(e) => { this._saveNext = e.target.checked; }}
              ?disabled=${this._isLoading}
            />
            <label class="toggle-label" for="save-toggle">Save to wiki</label>
          </div>
          <span class="hint">⌘+Enter to send</span>
        </div>
      </div>
    `;
  }

  _renderSidebar() {
    return html`
      <aside class="sidebar">
        <div class="sidebar-title">Conversations</div>
        ${this._conversations.length === 0
          ? html`<div class="sidebar-empty">No conversations yet.</div>`
          : this._conversations.map((conv) => html`
              <button
                class="conv-item ${conv.id === this._activeId ? "active" : ""}"
                @click=${() => this._switchTo(conv.id)}
              >
                <div class="conv-title">${conv.title || "New conversation"}</div>
                <div class="conv-meta">
                  ${conv.messages.length} messages · ${this._relativeTime(conv.updated)}
                </div>
              </button>
            `)
        }
        ${this._conversations.length > 1 ? html`
          <button class="sidebar-clear" @click=${this._clearAll}>
            Clear all
          </button>
        ` : ""}
      </aside>
    `;
  }
}

customElements.define("query-panel", QueryPanel);
