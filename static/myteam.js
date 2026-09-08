/* My Team: a reader picks their team once, in their own browser.
 *
 * The site is static, so the build cannot know whose browser this is. It
 * renders every team's card and this reveals one. That costs a few kilobytes
 * of hidden markup and buys personalisation with no account, no server, no
 * cookie and nothing sent anywhere. The choice lives in localStorage under
 * one key per league and can be cleared from the page that set it.
 *
 * Everything here degrades to nothing without JavaScript: the cards start
 * hidden, the chooser starts hidden, and a reader who never picks a team
 * sees exactly the league-wide site that existed before.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var league = root.getAttribute("data-league");
  if (!league) return;
  var KEY = "leaguepage:myteam:" + league;

  function read() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function write(slug) {
    try {
      if (slug) window.localStorage.setItem(KEY, slug);
      else window.localStorage.removeItem(KEY);
    } catch (e) { /* private window, blocked storage: the page still works */ }
  }

  function each(sel, fn) {
    Array.prototype.forEach.call(document.querySelectorAll(sel), fn);
  }

  /* A team slug from the build, or null. Never trust the stored value: a
   * team can be renamed between visits, which changes its slug, and a stale
   * one must fall back to the unpersonalised page rather than to nothing.
   *
   * The cards only exist on the home page, so checking for one meant the
   * nav shortcut worked there and nowhere else. Every page ships the
   * league's slug list on the shortcut itself; the cards are the check only
   * on the page that has them. */
  function known(slug) {
    if (!slug) return null;
    if (document.querySelector('[data-team="' + CSS.escape(slug) + '"]')) return slug;
    var nav = document.querySelector("[data-myteam-nav][data-teams]");
    if (!nav) return null;
    var all = (nav.getAttribute("data-teams") || "").split(/\s+/);
    return all.indexOf(slug) >= 0 ? slug : null;
  }

  /* Once a reader has told us which team is theirs, their team is the most
   * relevant thing on the page and it was sitting at 46% of the scroll
   * depth -- below the league-wide summary, below the standings. Move it
   * up to just under the lede.
   *
   * Only once chosen. A first-time reader gets the newspaper in the order
   * the Commissioner wrote it, not a team-picker above the news. */
  function promote() {
    var card = document.getElementById("myteam");
    var main = card && card.closest("main");
    if (!card || !main) return;
    var first = main.querySelector("section.module:not(.myteam)");
    if (first && first !== card.nextElementSibling) main.insertBefore(card, first);
  }

  function apply(slug) {
    var chosen = known(slug);
    if (chosen) promote();
    each("[data-team]", function (el) { el.hidden = el.getAttribute("data-team") !== chosen; });
    each("[data-myteam-empty]", function (el) { el.hidden = !!chosen; });
    each("[data-myteam-set]", function (el) { el.hidden = !chosen; });
    each("[data-myteam-nav]", function (el) {
      var href = el.getAttribute("data-href-template");
      if (chosen && href) {
        el.href = href.replace("__SLUG__", chosen);
        el.hidden = false;
      } else {
        el.hidden = true;
      }
    });
    each("[data-myteam-name]", function (el) {
      var card = chosen && document.querySelector('[data-team="' + CSS.escape(chosen) + '"]');
      el.textContent = card ? (card.getAttribute("data-team-name") || "") : "";
    });
  }

  document.addEventListener("click", function (ev) {
    var pick = ev.target.closest("[data-pick-team]");
    if (pick) {
      ev.preventDefault();
      write(pick.getAttribute("data-pick-team"));
      apply(read());
      var card = document.getElementById("myteam");
      if (card) card.scrollIntoView({ block: "nearest" });
      return;
    }
    if (ev.target.closest("[data-forget-team]")) {
      ev.preventDefault();
      write(null);
      apply(null);
      var chooser = document.getElementById("myteam-choose");
      if (chooser) chooser.open = true;
    }
  });

  apply(read());

  // On a phone the nav is one horizontally scrolling row, so the page you
  // are on can start off-screen -- and the current item is the one piece
  // of orientation a nav owes you. Nudge it into view without moving the
  // page itself.
  try {
    var here = document.querySelector('nav.leaguenav a[aria-current="page"]');
    var strip = here && here.parentElement;
    if (here && strip && strip.scrollWidth > strip.clientWidth) {
      strip.scrollLeft = Math.max(
        0, here.offsetLeft - (strip.clientWidth - here.offsetWidth) / 2);
    }
  } catch (e) { /* orientation is a nicety; never break the nav over it */ }
})();
