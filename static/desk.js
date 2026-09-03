/* Commissioner's Desk: attach the CSRF token to everything that mutates.
 *
 * The server has demanded `x-csrf-token` or a `csrf_token` field on every
 * POST since auth landed, and nothing in the Desk ever sent one. With
 * LEAGUEPAGE_AUTH_MODE=required that is a 403 on every button in the
 * building -- the single thing standing between this code and authenticated
 * remote authoring.
 *
 * Doing it centrally rather than in twenty-one templates means a new form
 * or a new fetch cannot forget. When auth is off the meta tag is empty, the
 * server short-circuits the check, and this is a no-op either way.
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute("content") || "" : "";
  if (!token) return;

  /* Forms: a hidden field, stamped at submit time rather than at load, so a
   * form built by script later in the page is covered too. */
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || form.tagName !== "FORM") return;
    if ((form.method || "get").toLowerCase() === "get") return;
    if (form.querySelector('[name="csrf_token"]')) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = token;
    form.appendChild(input);
  }, true);

  /* fetch: the header, on same-origin mutating requests only. A token sent
   * to a third party is a token given away. */
  var MUTATES = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };
  var original = window.fetch;
  if (typeof original !== "function") return;

  window.fetch = function (input, init) {
    init = init || {};
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var method = (init.method ||
      (typeof input === "object" && input && input.method) || "GET").toUpperCase();
    var sameOrigin = true;
    try {
      sameOrigin = new URL(url, location.href).origin === location.origin;
    } catch (e) { /* a relative path that URL cannot parse is same-origin */ }

    if (MUTATES[method] && sameOrigin) {
      var headers = new Headers(init.headers ||
        (typeof input === "object" && input && input.headers) || {});
      if (!headers.has("x-csrf-token")) headers.set("x-csrf-token", token);
      init = Object.assign({}, init, { headers: headers, method: method });
    }
    return original.call(this, input, init);
  };
})();
