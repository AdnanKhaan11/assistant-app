/* Keep local previews on FastAPI so cookie auth and API requests share one origin. */
(function routeAccountPage(window) {
  "use strict";

  const apiHost = "http://127.0.0.1:8000/";
  const localHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  const isFilePage = window.location.protocol === "file:";
  const isLocalPreview = localHosts.has(window.location.hostname) && window.location.port !== "8000";
  if (isFilePage || isLocalPreview) window.location.replace(apiHost);
})(window);
