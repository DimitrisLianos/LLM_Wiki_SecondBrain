/**
 * minimal hash-based spa router.
 * routes are defined as { pattern, component } pairs.
 * the component tag name is rendered into the main content area.
 */

const routes = [
  { pattern: /^#\/query$/,                   view: "query-panel" },
  { pattern: /^#\/search$/,                  view: "search-panel" },
  { pattern: /^#\/browse$/,                  view: "wiki-browser" },
  { pattern: /^#\/page\/(\w+)\/(.+)$/,       view: "page-viewer", params: ["subdir", "name"] },
  { pattern: /^#\/ingest$/,                  view: "ingest-panel" },
  { pattern: /^#\/server$/,                  view: "server-panel" },
  { pattern: /^#\/lint$/,                    view: "lint-panel" },
  { pattern: /^#\/dedup$/,                   view: "dedup-panel" },
  { pattern: /^#\/graph$/,                   view: "graph-view" },
];

const DEFAULT_ROUTE = "#/query";

export function getCurrentRoute() {
  const hash = window.location.hash || DEFAULT_ROUTE;

  for (const route of routes) {
    const match = hash.match(route.pattern);
    if (match) {
      const params = {};
      if (route.params) {
        route.params.forEach((name, i) => {
          params[name] = decodeURIComponent(match[i + 1]);
        });
      }
      return { view: route.view, params, hash };
    }
  }

  return { view: "query-panel", params: {}, hash: DEFAULT_ROUTE };
}

export function navigate(path) {
  window.location.hash = path;
}

export function onRouteChange(callback) {
  const _trackPanel = () => {
    const route = getCurrentRoute();
    // remember the last non-page-viewer panel so page-viewer can navigate back.
    if (route.view !== "page-viewer") {
      sessionStorage.setItem("sb_last_panel", route.hash);
    }
    return route;
  };
  window.addEventListener("hashchange", () => callback(_trackPanel()));
  // fire immediately for initial load.
  callback(_trackPanel());
}

/** return the last panel route the user was on (for back navigation). */
export function getLastPanel() {
  return sessionStorage.getItem("sb_last_panel") || "#/browse";
}
