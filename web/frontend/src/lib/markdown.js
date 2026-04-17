/**
 * markdown renderer with wikilink support.
 * transforms [[Page Name]] into clickable links that navigate within the app.
 *
 * output is sanitised with DOMPurify (a real HTML parser) rather than
 * regex string-replacement: regex sanitisers are trivially bypassable
 * via SVG event handlers, unicode variants, nested tags, etc. — DOMPurify
 * is the community-standard solution.
 */

import { Marked } from "marked";
import DOMPurify from "dompurify";

const marked = new Marked();

/** HTML-escape a string so it can't break out of an attribute or element body. */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// wikilink extension: [[Page Name]] or [[Page Name|Display Text]]
const wikilinkExtension = {
  name: "wikilink",
  level: "inline",
  start(src) {
    return src.indexOf("[[");
  },
  tokenizer(src) {
    const match = src.match(/^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/);
    if (match) {
      return {
        type: "wikilink",
        raw: match[0],
        target: match[1].trim(),
        display: (match[2] || match[1]).trim(),
      };
    }
  },
  renderer(token) {
    // escape every attribute and body: the target / display come from
    // untrusted wiki content and could contain quotes, angle brackets,
    // or javascript:-style URLs.
    const safeTarget = escapeHtml(token.target);
    const safeDisplay = escapeHtml(token.display);
    const href = `#/page/_/${encodeURIComponent(token.target)}`;
    return `<a class="wikilink" href="${escapeHtml(href)}" data-page="${safeTarget}">${safeDisplay}</a>`;
  },
};

marked.use({ extensions: [wikilinkExtension] });

// DOMPurify configuration for rendered markdown.
// allow the standard markdown output + our wikilink anchor (class + data-*),
// but block anything that can execute script: style attrs, event handlers,
// objects, embeds, iframes, forms, etc. ADD_ATTR includes target+rel for
// external links emitted by marked.
//
// FORBID_ATTR covers every on* handler we might realistically see; DOMPurify
// also strips them by default, but being explicit documents intent.
// ALLOWED_URI_REGEXP is tightened to an allowlist of safe schemes + relative
// links, which rejects data:/javascript:/vbscript: and other exotic URI
// handlers without relying on DOMPurify's built-in blocklist.
const PURIFY_CONFIG = {
  ADD_ATTR: ["target", "rel"],
  FORBID_TAGS: ["style", "iframe", "object", "embed", "form", "input", "script"],
  FORBID_ATTR: [
    "style",
    "onerror", "onclick", "onload", "onmouseover", "onmouseout",
    "onfocus", "onblur", "onchange", "onsubmit", "onkeydown",
    "onkeyup", "onkeypress", "onanimationstart", "onanimationend",
    "onanimationiteration", "ontransitionend", "onpointerdown",
    "onpointerup", "onpointermove",
  ],
  // relative paths (starting with /, #, ?) + http(s), mailto, tel only.
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|tel:|#|\/|\?)/i,
};

/**
 * render markdown to html with wikilink support.
 * strips yaml frontmatter if present, then sanitises via DOMPurify.
 */
export function renderMarkdown(text) {
  if (!text) return "";

  // strip frontmatter.
  let body = text;
  if (body.startsWith("---")) {
    const end = body.indexOf("---", 3);
    if (end !== -1) {
      body = body.substring(end + 3).trim();
    }
  }

  const rawHtml = marked.parse(body);
  return DOMPurify.sanitize(rawHtml, PURIFY_CONFIG);
}

/**
 * extract frontmatter fields from markdown text.
 */
export function parseFrontmatter(text) {
  if (!text || !text.startsWith("---")) return {};

  const end = text.indexOf("---", 3);
  if (end === -1) return {};

  const block = text.substring(3, end);
  const fm = {};

  for (const line of block.split("\n")) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.substring(0, idx).trim();
    let val = line.substring(idx + 1).trim();

    if (val.startsWith("[") && val.endsWith("]")) {
      fm[key] = val.slice(1, -1).split(",").map((v) => v.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean);
    } else {
      fm[key] = val;
    }
  }

  return fm;
}
