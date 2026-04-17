/**
 * secondbrain frontend — entry point.
 * loads global styles and registers all web components.
 */

// global styles (design tokens + reset).
import "./styles/tokens.css";
import "./styles/global.css";

// app shell (contains router + sidebar).
import "./components/app-shell.js";

// panel components (lazy-ish: all loaded upfront since bundle is small).
import "./components/query-panel.js";
import "./components/search-panel.js";
import "./components/wiki-browser.js";
import "./components/page-viewer.js";
import "./components/ingest-panel.js";
import "./components/server-panel.js";
import "./components/lint-panel.js";
import "./components/dedup-panel.js";
import "./components/graph-view.js";
