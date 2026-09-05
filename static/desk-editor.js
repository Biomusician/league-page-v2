/*  Editing behaviour for the Commissioner's issue surfaces.
 *
 *  Loaded by both the Issue Room and the older long-form editor so the
 *  two are one implementation: autosave with conflict detection,
 *  approval, history and restore, proposals, generated-copy resets and
 *  the deliberate change of authorship. Every call goes to the same
 *  endpoints; nothing here decides anything on its own.
 *
 *  The page defines `EDIT` (the issue's edit base URL) before loading
 *  this file. CSRF comes from the meta tag via static/desk.js.
 */
const dirty = new Map();   // element -> true
let saveTimer = null;

function setState(cls, text) {
  const el = document.getElementById("savestate");
  el.className = cls; el.textContent = text;
}

function syncGhost(ta) {
  const wrap = ta.closest(".editwrap");
  if (wrap) wrap.classList.toggle("has-content", ta.value.trim().length > 0);
}

/* Bold and italic, without a WYSIWYG editor.
 *
 * The source stays Markdown -- that is what publishes, what diffs and what
 * he can still fix by hand. These buttons only type the asterisks for him,
 * around the selection or at the cursor, and Ctrl/Cmd+B and Ctrl/Cmd+I do
 * the same thing because that is what every hand already tries first.
 *
 * Toggling: wrapping text that is already wrapped unwraps it, so pressing
 * the button twice returns what he started with instead of nesting markers
 * he then has to go delete.
 */
function wrapSelection(ta, marker) {
  const {selectionStart: a, selectionEnd: b, value: v} = ta;
  const n = marker.length;
  const inside = v.slice(a, b);
  const wrappedInside = inside.length >= 2 * n
        && inside.startsWith(marker) && inside.endsWith(marker);
  const wrappedOutside = v.slice(a - n, a) === marker && v.slice(b, b + n) === marker;
  let start, end, text;
  if (wrappedInside) {
    text = inside.slice(n, -n);
    start = a; end = b; 
  } else if (wrappedOutside) {
    text = inside;
    start = a - n; end = b + n;
  } else {
    text = marker + inside + marker;
    start = a; end = b;
  }
  ta.setRangeText(text, start, end, "select");
  if (a === b) {                       // no selection: park the caret inside
    const caret = start + (wrappedInside || wrappedOutside ? 0 : n);
    ta.setSelectionRange(caret, caret);
  }
  ta.focus();
  ta.dispatchEvent(new Event("input", {bubbles: true}));   // marks it dirty
}

function addFormatBar(ta) {
  const bar = document.createElement("div");
  bar.className = "formatbar";
  for (const [label, marker, title] of [["B", "**", "Bold (Ctrl+B)"],
                                        ["I", "*", "Italic (Ctrl+I)"]]) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.title = title;
    b.style.fontStyle = marker === "*" ? "italic" : "normal";
    b.style.fontWeight = marker === "**" ? "bold" : "normal";
    b.onclick = () => wrapSelection(ta, marker);
    bar.append(b);
  }
  ta.parentElement.before(bar);
}

document.querySelectorAll("textarea.autosave").forEach(ta => {
  addFormatBar(ta);
  ta.addEventListener("keydown", e => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k !== "b" && k !== "i") return;
    e.preventDefault();
    wrapSelection(ta, k === "b" ? "**" : "*");
  });
  syncGhost(ta);
  ta.addEventListener("input", () => {
    syncGhost(ta);   // first keystroke hides the ghost; emptying restores it
    dirty.set(ta, true);
    setState("dirty", "Unsaved changes…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveAll, 1500);
  });
});

async function saveOne(ta) {
  const body = {
    section: ta.dataset.section,
    text: ta.value,
    base_sha: ta.dataset.sha,
  };
  if (ta.dataset.chunkIndex !== undefined) {
    body.chunk_index = parseInt(ta.dataset.chunkIndex);
    body.chunk_count = parseInt(ta.dataset.chunkCount);
  }
  const r = await fetch(EDIT + "/save", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (r.status === 409) { setState("error", "Conflict: this section changed elsewhere. Reload the page."); return false; }
  if (!r.ok) { setState("error", "Error saving — try Save All again."); return false; }
  const data = await r.json();
  ta.dataset.sha = data.sha;
  dirty.delete(ta);
  const chip = document.querySelector(`[data-role="state-chip"][data-section="${ta.dataset.section}"]`);
  if (chip && data.state) { chip.textContent = data.state; chip.classList.add("edited"); }
  return true;
}

async function saveAll() {
  const pending = [...dirty.keys()];
  if (!pending.length) { setState("", "All changes saved"); return; }
  setState("dirty", "Saving…");
  let ok = true;
  for (const ta of pending) ok = (await saveOne(ta)) && ok;
  if (ok) setState("", "Saved " + new Date().toLocaleTimeString());
}

window.addEventListener("beforeunload", (e) => {
  if (dirty.size) { e.preventDefault(); e.returnValue = ""; }
});

async function approve(section, on) {
  await saveAll();
  const r = await fetch(EDIT + "/approve", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section, action: on ? "approve" : "unapprove"})});
  /* Parse before the status check and a non-JSON error body -- which is
     what a 500 returns -- throws out of the handler, so the alert below
     never runs and the button silently does nothing. That is how a broken
     approve endpoint presented to the Commissioner as a dead button. */
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert("Cannot approve: " + (data.error || ("server error " + r.status))); return; }
  const chip = document.querySelector(`[data-role="approve-chip"][data-section="${section}"]`);
  if (chip) {
    chip.textContent = on ? "approved" : (section.startsWith("matchup:") ? "edited" : "not approved");
    chip.classList.toggle("approved", on);
    refreshParentCount(chip);
  }
}

/* A matchup preview is a child of Common Tactical Picture, so approving one
 * moves the parent's count. Recomputing it from the children in the page
 * beats re-rendering: the number he reads is the number of chips he can see.
 */
function refreshParentCount(chip) {
  const parent = chip.closest("details.sec.child")?.parentElement?.closest("details.sec");
  if (!parent) return;
  const counter = parent.querySelector(':scope > summary [data-role="child-count"]');
  if (!counter) return;
  const kids = [...parent.querySelectorAll('details.sec.child [data-role="approve-chip"]')];
  const done = kids.filter(c => c.classList.contains("approved")).length;
  counter.textContent = `${done} / ${kids.length} approved`;
  counter.classList.toggle("approved", done === kids.length);
  counter.classList.toggle("edited", done !== kids.length);
}

async function previewSection(section) {
  await saveAll();
  const box = document.getElementById("preview-" + section);
  const r = await fetch(EDIT + `/preview-section?section=${encodeURIComponent(section)}`);
  const data = await r.json().catch(() => ({}));
  box.innerHTML = data.ok ? data.html : "<em>preview unavailable</em>";
  box.hidden = !box.hidden ? true : false;
}

async function showRevisions(section) {
  const box = document.getElementById("revs-" + section);
  if (!box.hidden) { box.hidden = true; return; }
  const r = await fetch(EDIT + `/revisions?section=${encodeURIComponent(section)}`);
  const data = await r.json().catch(() => ({revisions: []}));
  if (!(data.revisions || []).length) { box.innerHTML = "<em>no saved revisions yet</em>"; }
  else {
    box.innerHTML = "<ul>" + data.revisions.map(rev =>
      `<li><b>${rev.created_at}</b> (${rev.source}, ${rev.chars} chars) ` +
      `<button type="button" onclick="restoreRev('${section}', ${rev.id})">Restore</button>` +
      `<br><span class="meta">${rev.preview.replace(/</g, "&lt;")}…</span></li>`).join("") + "</ul>";
  }
  box.hidden = false;
}

async function restoreRev(section, id) {
  if (!confirm("Restore this earlier version? Your current text is snapshotted first.")) return;
  const r = await fetch(EDIT + "/restore", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section, revision_id: id})});
  if (r.ok) location.reload(); else alert("Restore failed.");
}

/* Copy a prompt; do not call an API.
 *
 * There is no model key anywhere in this product and there is not going to
 * be one. Drafting happens in a Claude Code session he starts himself, so
 * the Desk's job is to hand him the exact prompt for this section and get
 * out of the way. The prompt names files on this machine rather than
 * pasting their contents, so nothing private travels with it.
 */
async function copyPrompt(section, btn) {
  const r = await fetch(EDIT + "/claude-prompt?section=" + encodeURIComponent(section));
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.prompt) { alert("Could not build the prompt: " + (data.error || r.status)); return; }
  const label = btn ? btn.textContent : null;
  let copied = false;
  try {
    await navigator.clipboard.writeText(data.prompt);
    copied = true;
  } catch (e) { /* no clipboard permission -- fall through to showing it */ }
  if (copied && btn) {
    btn.textContent = "Copied ✓";
    setTimeout(() => { btn.textContent = label; }, 2000);
    return;
  }
  showPromptToCopy(data.prompt);
}

/* The clipboard can be refused, and a button that silently did nothing is
   the failure mode this Desk has already been bitten by once. Show the text
   and select it so Ctrl+C still works. */
function showPromptToCopy(text) {
  const box = document.createElement("div");
  box.className = "card";
  box.style.cssText = "position:fixed;inset:10% 10% auto;z-index:20;max-height:70vh;overflow:auto";
  const ta = document.createElement("textarea");
  ta.className = "prose"; ta.rows = 12; ta.readOnly = true; ta.value = text;
  const close = document.createElement("button");
  close.textContent = "Close"; close.onclick = () => box.remove();
  box.append(Object.assign(document.createElement("p"), {
    className: "meta", textContent: "Copy this into a Claude Code session:"}), ta, close);
  document.body.append(box);
  ta.focus(); ta.select();
}

async function askRewrite(section) {
  const note = prompt("What should Claude change? (e.g. 'shorten by 25%', 'different joke')");
  if (!note) return;
  const r = await fetch(EDIT + "/request-rewrite", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({section, note})});
  if (r.ok) location.reload(); else alert("Could not record the request.");
}

async function proposal(section, action) {
  if (action === "accept" &&
      !confirm("Replace your current text with Claude's proposal? Current text goes to History.")) return;
  const r = await fetch(EDIT + "/proposal", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({section, action})});
  if (r.ok) location.reload(); else alert("Proposal action failed.");
}

/* Put the generated version back. The Lowdown's comes from the Claude rough
 * draft on disk; a section with a composed default has it rebuilt from the
 * results it is made of. Either way the current text is snapshotted to
 * History first, so this is reversible and says so. */
async function resetGenerated(section) {
  const what = section === "lowdown" ? "the Lowdown" : "this section";
  if (!confirm("Replace " + what + " with the generated version? Your current "
             + "text goes to History and can be restored from there, and "
             + "approval is cleared.")) return;
  const r = await fetch(EDIT + "/reset-generated", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: section, confirm: "yes"})});
  if (r.ok) { location.reload(); return; }
  let msg = "Reset failed.";
  try { msg = (await r.json()).error || msg; } catch (e) { /* keep default */ }
  alert(msg);
}

/* Replace with my copy: the deliberate change of authorship. The generated
 * text goes to History, the box empties, and what he writes next is his in
 * origin (with the AI draft counted as assistance, because he read it). */
async function replaceOrigin(section) {
  if (!confirm("Set this section aside as a generated draft and start your own copy? "
             + "The generated text goes to History; the box will be empty; approval is cleared.")) return;
  const r = await fetch(EDIT + "/replace-origin", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: section, confirm: "yes"})});
  if (r.ok) { location.reload(); return; }
  let msg = "Could not change the origin.";
  try { msg = (await r.json()).error || msg; } catch (e) { /* keep default */ }
  alert(msg);
}

function expandAll(open) {
  document.querySelectorAll("details.sec").forEach(d => d.open = open);
}
/* Twenty-six clicks a week to approve an issue section by section.
 *
 * This does NOT get its own endpoint. It calls the same per-section approve
 * the individual buttons call, once each, so every section passes exactly
 * the gate it would have passed on its own -- empty sections and blocked
 * markers are still refused, and the refusals are reported rather than
 * swallowed. A bulk endpoint that approved in one shot would be a second
 * path to approval, and approval is the one thing on this Desk that must
 * have only one.
 */
async function approveAllReady() {
  /* Deepest first. A parent whose gate is "every child is approved" has to
     be asked after its children, or it is refused for a reason that stopped
     being true one iteration later. */
  const depth = el => { let d = 0; for (let n = el; n; n = n.parentElement) if (n.tagName === "DETAILS") d++; return d; };
  const chips = [...document.querySelectorAll('[data-role="approve-chip"]')]
    .filter(c => !c.classList.contains("approved"))
    .sort((a, b) => depth(b) - depth(a));
  if (!chips.length) { alert("Everything included is already approved."); return; }
  if (!confirm(`Approve ${chips.length} section(s)? Each is checked the same `
             + `way as approving it on its own, and anything that fails is `
             + `left alone.`)) return;
  await saveAll();
  const done = [], refused = [];
  for (const chip of chips) {
    const section = chip.dataset.section;
    const r = await fetch(EDIT + "/approve", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({section, action: "approve"})});
    let data = {};
    try { data = await r.json(); } catch (e) { /* keep the status */ }
    if (r.ok && data.approved) {
      chip.textContent = "approved";
      chip.classList.add("approved");
      refreshParentCount(chip);
      done.push(section);
    } else {
      refused.push(`${section}: ${data.error || r.status}`);
    }
  }
  alert(`Approved ${done.length}.`
        + (refused.length ? "\n\nLeft alone (" + refused.length + "):\n" + refused.join("\n")
                          : ""));
}

function collapseApproved() {
  document.querySelectorAll("details.sec").forEach(d => {
    const chip = d.querySelector('[data-role="approve-chip"]');
    if (chip && chip.classList.contains("approved")) d.open = false;
  });
}
