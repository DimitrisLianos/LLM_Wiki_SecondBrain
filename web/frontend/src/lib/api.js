/**
 * api client — typed fetch wrapper for all backend endpoints.
 * returns parsed json on success, throws with a user-friendly message on failure.
 */

const BASE = "";  // same origin; vite proxies /api to :3000.

async function request(path, options = {}) {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!res.ok) {
    let msg;
    try {
      const body = await res.json();
      msg = body.detail || body.message || body.error || JSON.stringify(body);
    } catch {
      msg = res.statusText;
    }

    // translate http codes to plain language.
    const friendly = {
      503: "The LLM server is not running. Start it from the Server panel.",
      409: "Another operation is already in progress. Please wait.",
      404: "Not found.",
      400: msg,
    };
    throw new Error(friendly[res.status] || msg || `Request failed (${res.status})`);
  }

  return res.json();
}

// --- server. ---

export const server = {
  status:     () => request("/api/server/status"),
  config:     () => request("/api/server/config"),
  start:      (target = "llm") => request(`/api/server/start?target=${target}`, { method: "POST" }),
  stop:       (target = "llm") => request(`/api/server/stop?target=${target}`, { method: "POST" }),
  setConfig:  (updates) => request("/api/server/config", { method: "POST", body: JSON.stringify(updates) }),
  logs:       (target = "llm", tail = 200) => request(`/api/server/logs/${target}?tail=${tail}`),
};

// --- search. ---

export const search = {
  query:   (q, topK = 20) => request(`/api/search?q=${encodeURIComponent(q)}&top_k=${topK}`),
  rebuild: () => request("/api/search/rebuild", { method: "POST" }),
};

// --- wiki. ---

export const wiki = {
  pages:  (subdir = "") =>
    request(`/api/wiki/pages${subdir ? `?subdir=${encodeURIComponent(subdir)}` : ""}`),
  page:   (subdir, name) =>
    request(`/api/wiki/page/${encodeURIComponent(subdir)}/${encodeURIComponent(name)}`),
  graph:  () => request("/api/wiki/graph"),
  stats:  () => request("/api/wiki/stats"),
};

// --- query. ---

export const query = {
  ask: (question, save = false) => request("/api/query", {
    method: "POST",
    body: JSON.stringify({ question, save }),
  }),

  /** save a previously generated answer as a synthesis page. */
  saveAnswer: (question, answer, sources = []) => request("/api/query/save", {
    method: "POST",
    body: JSON.stringify({ question, answer, sources }),
  }),

  /**
   * multi-turn chat stream via POST + SSE.
   *
   * EventSource only supports GET, so we use fetch with ReadableStream
   * and parse SSE events manually. returns an async generator that
   * yields {event, data} objects. pass an AbortSignal to cancel
   * the request and reader cleanly.
   *
   * @param {string} question
   * @param {{ role: string, content: string }[]} history
   * @param {object} [opts]
   * @param {AbortSignal} [signal]
   * @returns {AsyncGenerator<{ event: string, data: object }>}
   */
  async *chatStream(question, history = [], opts = {}, signal) {
    const {
      reasoning = true,
      web_search = false,
      web_results_count = 10,
      save = false,
      search_engine = "duckduckgo",
    } = opts;
    const res = await fetch(`${BASE}/api/query/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question, history, reasoning, web_search,
        web_results_count, save, search_engine,
      }),
      signal,
    });

    if (!res.ok) {
      let msg;
      try {
        const body = await res.json();
        const detail = body.detail || body.message || body.error;
        // pydantic returns detail as an array of validation error objects.
        msg = Array.isArray(detail)
          ? detail.map((e) => e.msg || JSON.stringify(e)).join("; ")
          : detail || `Error ${res.status}`;
      } catch {
        msg = res.statusText || `Request failed (${res.status})`;
      }
      const friendly = {
        503: "The LLM server is not running. Start it from the Server panel.",
        400: msg,
      };
      throw new Error(friendly[res.status] || msg);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.trim()) continue;
          const eventMatch = part.match(/^event:\s*(.+)$/m);
          // support multi-line data fields per SSE spec.
          const dataLines = [...part.matchAll(/^data:\s*(.*)$/gm)].map((m) => m[1]);
          if (eventMatch && dataLines.length) {
            try {
              yield { event: eventMatch[1], data: JSON.parse(dataLines.join("\n")) };
            } catch {
              /* skip malformed events. */
            }
          }
        }
      }
    } finally {
      reader.cancel();
    }
  },
};

// --- ingest. ---

export const ingest = {
  files:  () => request("/api/ingest/files"),
  status: () => request("/api/ingest/status"),
  cancel: () => request("/api/ingest/cancel", { method: "POST" }),

  start: (filename, overwrite = false, useEmbeddings = false) => request("/api/ingest", {
    method: "POST",
    body: JSON.stringify({ filename, overwrite, use_embeddings: useEmbeddings }),
  }),

  startAll: (overwrite = false, useEmbeddings = false) => request("/api/ingest/all", {
    method: "POST",
    body: JSON.stringify({ overwrite, use_embeddings: useEmbeddings }),
  }),

  startBatch: (filenames, overwrite = false, useEmbeddings = false) => request("/api/ingest/batch", {
    method: "POST",
    body: JSON.stringify({ filenames, overwrite, use_embeddings: useEmbeddings }),
  }),

  /** upload a file to raw/. surfaces server's `detail`/`message` so the ui
   *  can explain what went wrong (duplicate, unsupported type, quota, …). */
  async upload(file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/ingest/upload`, { method: "POST", body: form });
    if (!res.ok) {
      let msg;
      try {
        const body = await res.json();
        const detail = body.detail || body.message || body.error;
        // pydantic validation errors come back as an array.
        msg = Array.isArray(detail)
          ? detail.map((e) => e.msg || JSON.stringify(e)).join("; ")
          : detail || res.statusText || `Upload failed (${res.status})`;
      } catch {
        msg = res.statusText || `Upload failed (${res.status})`;
      }
      throw new Error(msg);
    }
    return res.json();
  },

  /** sse stream for ingest progress. */
  progress(taskId) {
    return new EventSource(`${BASE}/api/ingest/progress/${taskId}`);
  },
};

// --- lint. ---

export const lint = {
  run: () => request("/api/lint", { method: "POST" }),
  deletePages: (pages) => request("/api/lint/delete", {
    method: "POST",
    body: JSON.stringify({ pages }),
  }),
};

// --- dedup. ---

export const dedup = {
  plan:  () => request("/api/dedup/plan", { method: "POST" }),
  apply: () => request("/api/dedup/apply", { method: "POST" }),
  applySelected: (clusters) => request("/api/dedup/apply-selected", {
    method: "POST",
    body: JSON.stringify({ clusters }),
  }),
};

// --- admin (destructive ops). ---

export const admin = {
  /**
   * preview what a reset would delete. mode: "wiki" or "full".
   * the returned counts drive the confirmation modal's summary.
   */
  resetPreview: (mode) => request(`/api/admin/reset/preview?mode=${encodeURIComponent(mode)}`),

  /**
   * execute a reset. caller MUST pass the typed confirmation token "RESET"
   * — backend rejects anything else. mode: "wiki" or "full".
   */
  reset: (mode, confirm) => request("/api/admin/reset", {
    method: "POST",
    body: JSON.stringify({ mode, confirm }),
  }),
};
