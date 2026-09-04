/* Sortable tables — shared, dependency-free, progressive enhancement.
 *
 * Opt in with <table data-sortable>. The first row is the header; each th
 * is sortable unless it carries data-nosort. Optional th attributes:
 *   data-sort-type="number" | "text"   (default: auto-detect)
 *   data-sort-dir="asc" | "desc"       (first-click direction; default asc)
 * Cells may carry data-sort-value with the machine value ("" = missing);
 * otherwise the cell text is parsed (leading '#', trailing '%', '+' and
 * commas stripped; "—", "N/A", "Too Early" count as missing).
 *
 * Click cycle: default direction → reversed → original order. Missing
 * values always sort to the bottom. Sorting is stable (original index is
 * the tiebreak). Headers are keyboard-operable and expose aria-sort.
 * Without JavaScript the table simply stays in its canonical order.
 */
(function () {
  "use strict";

  var MISSING = ["", "—", "-", "n/a", "na", "too early", "?"];

  function parseCell(cell) {
    if (!cell) return { missing: true, num: null, text: "" };
    var raw = cell.getAttribute("data-sort-value");
    if (raw === null) raw = cell.textContent;
    raw = raw.trim();
    if (MISSING.indexOf(raw.toLowerCase()) !== -1)
      return { missing: true, num: null, text: "" };
    var cleaned = raw.replace(/^#/, "").replace(/%$/, "")
      .replace(/,/g, "").replace(/^\+/, "");
    var num = Number(cleaned);
    if (cleaned !== "" && !isNaN(num))
      return { missing: false, num: num, text: raw.toLowerCase() };
    return { missing: false, num: null, text: raw.toLowerCase() };
  }

  var tableSeq = 0;

  function initTable(table) {
    var rows = Array.prototype.slice.call(table.rows);
    if (rows.length < 2) return;
    // per-page sort memory so browser Back doesn't lose a comparison;
    // sessionStorage only, and every access is fail-safe
    var stateKey = "sortable:" + location.pathname + "#" + (tableSeq++);
    var headRow = rows[0];
    var dataRows = rows.slice(1);
    var originalOrder = dataRows.slice();
    var headers = Array.prototype.slice.call(headRow.cells);
    var state = { col: null, dir: null }; // dir: "asc" | "desc" | null

    // One polite announcer per table, visually hidden.
    var live = document.createElement("div");
    live.setAttribute("aria-live", "polite");
    live.setAttribute("role", "status");
    live.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden;" +
      "clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap";
    table.parentNode.insertBefore(live, table);

    function apply(order) {
      var parent = dataRows[0].parentNode;
      order.forEach(function (r) { parent.appendChild(r); });
    }

    function setAria(activeIdx) {
      headers.forEach(function (h, i) {
        if (!h.classList.contains("sortable")) return;
        if (i === activeIdx && state.dir)
          h.setAttribute("aria-sort",
            state.dir === "asc" ? "ascending" : "descending");
        else h.setAttribute("aria-sort", "none");
      });
      // aria-sort sits on the th while focus is on the button inside it, so
      // a screen reader gets no confirmation that up to 229 rows just
      // reordered. Say what happened.
      if (!live) return;
      if (activeIdx < 0 || !state.dir) {
        live.textContent = "Sort cleared. Original order restored.";
      } else {
        var name = (headers[activeIdx].textContent || "column").trim();
        live.textContent = "Sorted by " + name + ", " +
          (state.dir === "asc" ? "ascending" : "descending") + ".";
      }
    }

    function sortBy(idx) {
      var th = headers[idx];
      var firstDir = th.getAttribute("data-sort-dir") === "desc" ? "desc" : "asc";
      var next;
      if (arguments.length > 1 && arguments[1]) next = arguments[1];
      else if (state.col !== idx) next = firstDir;
      else if (state.dir === firstDir) next = firstDir === "asc" ? "desc" : "asc";
      else next = null; // third click: restore canonical order
      state.col = next === null ? null : idx;
      state.dir = next;
      try {
        if (next === null) sessionStorage.removeItem(stateKey);
        else sessionStorage.setItem(stateKey, idx + ":" + next);
      } catch (e) { /* storage unavailable: sorting still works */ }
      if (next === null) { apply(originalOrder); setAria(-1); return; }

      var type = th.getAttribute("data-sort-type");
      var decorated = dataRows.map(function (row, i) {
        return { row: row, i: i, v: parseCell(row.cells[idx]) };
      });
      var numeric = type === "number" || (type !== "text" &&
        decorated.some(function (d) { return d.v.num !== null; }));
      decorated.sort(function (a, b) {
        if (a.v.missing !== b.v.missing) return a.v.missing ? 1 : -1;
        var cmp = 0;
        if (numeric) {
          var an = a.v.num, bn = b.v.num;
          if (an === null && bn === null) cmp = 0;
          else if (an === null) return 1;   // unparsable sinks like missing
          else if (bn === null) return -1;
          else cmp = an - bn;
        } else {
          cmp = a.v.text < b.v.text ? -1 : a.v.text > b.v.text ? 1 : 0;
        }
        if (next === "desc") cmp = -cmp;
        return cmp !== 0 ? cmp : a.i - b.i; // stable tiebreak
      });
      apply(decorated.map(function (d) { return d.row; }));
      setAria(idx);
    }

    headers.forEach(function (th, idx) {
      if (th.hasAttribute("data-nosort")) return;
      th.classList.add("sortable");
      // The control goes INSIDE the th. Putting role="button" on the th
      // itself overrode its implicit columnheader role, which silently
      // voided every scope="col" association in the column and made
      // aria-sort invalid -- on 194 headers across the site.
      var btn = document.createElement("button");
      btn.type = "button";
      while (th.firstChild) btn.appendChild(th.firstChild);
      th.appendChild(btn);
      th.setAttribute("aria-sort", "none");
      btn.addEventListener("click", function () { sortBy(idx); });
    });

    try {
      var saved = sessionStorage.getItem(stateKey);
      if (saved) {
        var parts = saved.split(":");
        var col = parseInt(parts[0], 10);
        if (headers[col] && headers[col].classList.contains("sortable") &&
            (parts[1] === "asc" || parts[1] === "desc"))
          sortBy(col, parts[1]);
      }
    } catch (e) { /* storage unavailable */ }
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll("table[data-sortable]"), initTable);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
