/**
 * icons — inline svg icon set.
 *
 * outline-style, stroke-based icons at 24×24 that inherit `currentColor`
 * so they pick up whatever text colour the surrounding element uses
 * (active nav item → accent, inactive → muted, etc.).
 *
 * paths are derived from lucide.dev (MIT license), adjusted slightly so
 * the stroke weight matches the editorial / dark-luxury aesthetic of the
 * rest of the UI (1.75 stroke, round caps + joins).
 *
 * implementation note: lit exports *two* tagged templates — `html` and
 * `svg`. svg fragments (bare <path>, <circle>, …) MUST be built with the
 * `svg` tag so lit tags their nodes with the SVG namespace when the
 * template is parsed. using `html` for them produces HTML-namespace
 * elements that browsers won't paint even when they are inserted inside
 * an <svg> parent, which is exactly why the icons were rendering as
 * empty boxes before. the root <svg> element itself is created with
 * `html` because <svg> is a valid HTML element and its internal parser
 * switches to SVG mode automatically for its descendants.
 *
 * usage:
 *   import { icons } from "../lib/icons.js";
 *   html`<span class="icon">${icons.brand()}</span>`
 */

import { html, svg as svgFragment } from "lit";

/** wrap a set of <path>/<circle>/<line> elements in a standard 24×24 svg. */
function wrap(children, { size = 24, strokeWidth = 1.75 } = {}) {
  return html`<svg
    xmlns="http://www.w3.org/2000/svg"
    width="${size}"
    height="${size}"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="${strokeWidth}"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >${children}</svg>`;
}

export const icons = {
  /** brand mark — stylised brain-circuit. used in the sidebar header. */
  brand: () => wrap(svgFragment`
    <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/>
    <path d="M9 13a4.5 4.5 0 0 0 3-4"/>
    <path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/>
    <path d="M3.477 10.896a4 4 0 0 1 .585-.396"/>
    <path d="M6 18a4 4 0 0 1-1.967-.516"/>
    <path d="M12 13h4"/>
    <path d="M12 18h6a2 2 0 0 1 2 2v1"/>
    <path d="M12 8h8"/>
    <path d="M16 8V5a2 2 0 0 1 2-2"/>
    <circle cx="16" cy="13" r=".5" fill="currentColor"/>
    <circle cx="18" cy="3" r=".5" fill="currentColor"/>
    <circle cx="20" cy="21" r=".5" fill="currentColor"/>
    <circle cx="20" cy="8" r=".5" fill="currentColor"/>
  `),

  /** query — sparkles, suggests llm-assisted generation. */
  query: () => wrap(svgFragment`
    <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>
    <path d="M20 3v4"/>
    <path d="M22 5h-4"/>
    <path d="M4 17v2"/>
    <path d="M5 18H3"/>
  `),

  /** search — magnifying glass. */
  search: () => wrap(svgFragment`
    <circle cx="11" cy="11" r="8"/>
    <path d="m21 21-4.3-4.3"/>
  `),

  /** browse — library / shelved books. */
  browse: () => wrap(svgFragment`
    <path d="m16 6 4 14"/>
    <path d="M12 6v14"/>
    <path d="M8 8v12"/>
    <path d="M4 4v16"/>
  `),

  /** graph — waypoints / connected network. */
  graph: () => wrap(svgFragment`
    <circle cx="12" cy="4.5" r="2.5"/>
    <path d="m10.2 6.3-3.9 3.9"/>
    <circle cx="4.5" cy="12" r="2.5"/>
    <path d="M7 12h10"/>
    <circle cx="19.5" cy="12" r="2.5"/>
    <path d="m13.8 17.7 3.9-3.9"/>
    <circle cx="12" cy="19.5" r="2.5"/>
  `),

  /** ingest — file entering a container (file-input). */
  ingest: () => wrap(svgFragment`
    <path d="M4 22h14a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v4"/>
    <polyline points="14 2 14 8 20 8"/>
    <path d="M2 15h10"/>
    <path d="m9 18 3-3-3-3"/>
  `),

  /** health — heart pulse / diagnostic waveform. */
  health: () => wrap(svgFragment`
    <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/>
    <path d="M3.22 12H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/>
  `),

  /** dedup — merge two branches into one. */
  dedup: () => wrap(svgFragment`
    <path d="m8 6 4-4 4 4"/>
    <path d="M12 2v10.3a4 4 0 0 1-1.172 2.872L4 22"/>
    <path d="m20 22-5-5"/>
  `),

  /** server — stacked racks. */
  server: () => wrap(svgFragment`
    <rect width="20" height="8" x="2" y="2" rx="2" ry="2"/>
    <rect width="20" height="8" x="2" y="14" rx="2" ry="2"/>
    <line x1="6" x2="6.01" y1="6" y2="6"/>
    <line x1="6" x2="6.01" y1="18" y2="18"/>
  `),

  /** message — empty-state icon for chat / query panel. */
  message: () => wrap(svgFragment`
    <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>
  `, { strokeWidth: 1.5 }),
};
