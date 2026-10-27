/* JobDesk's page logic. Plain JavaScript, no modules and no build step.

   Six screens over one server: the job queue, the application log, the full
   archive, the criteria panel, the console, and the setup wizard. Everything
   the three packages do is reachable from here -- a radar sweep, a packet
   build, an ATS check -- so there is no step in the workflow that sends you
   back to a terminal.

   The one thing no button on this page does is submit an application. "Build
   packet" writes a folder; "Mark applied" records that you went and did it.
   There is nothing in between, on purpose.

   Tables hold their rows in memory and sort and filter there, so typing in a
   filter box never waits on a request. The archive is the exception: 24,000
   rows are searched server-side, because shipping them here to filter would be
   megabytes of JSON to answer a question that touches forty of them. */

"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* Every node on this page is built with this rather than with innerHTML. Job
   titles and company names come off other people's websites, and a page that
   parses them as markup is a page that runs whatever they contain. */
function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "on") Object.entries(v).forEach(([e, fn]) => node.addEventListener(e, fn));
    else if (k in node) node[k] = v;
    else node.setAttribute(k, v);
  });
  kids.flat().forEach((kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    node.append(kid);
  });
  return node;
}

/* A group of buttons where one is picked gets one shape under the picked
   button, and the shape slides when the pick changes. It watches the
   group's own aria state, so the code that picks a button does not have to
   know the pill is there. */
function pillTrack(box) {
  if (!box || box.classList.contains("has-pill")) return;
  box.classList.add("has-pill");
  const move = () => {
    const on = box.querySelector('[aria-pressed="true"], [aria-checked="true"], [aria-current="true"]');
    box.classList.toggle("pill-off", !on || !on.offsetWidth);
    if (!on || !on.offsetWidth) return;
    box.style.setProperty("--pill-x", on.offsetLeft + "px");
    box.style.setProperty("--pill-y", on.offsetTop + "px");
    box.style.setProperty("--pill-w", on.offsetWidth + "px");
    box.style.setProperty("--pill-h", on.offsetHeight + "px");
  };
  new MutationObserver(move).observe(box, { subtree: true, attributes: true,
    attributeFilter: ["aria-pressed", "aria-checked", "aria-current", "hidden"] });
  new ResizeObserver(move).observe(box);
  move();
  // Placed first, then allowed to move, so it does not slide in from the
  // corner when the page opens.
  requestAnimationFrame(() => requestAnimationFrame(() => box.classList.add("pill-ready")));
}

async function call(path, options) {
  const res = await fetch(path, options);
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(data.error || `${res.status} from ${path}`);
  return data;
}

const get = (path) => call(path);

// A posting URL comes from a job board, so only http(s) ones become links.
const webUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : null);
const post = (path, body) => call(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

/* The message after a packet build is a receipt. It has been read by the time
   the folder opens, and then it sits there taking a line off the top of every
   tab until something else replaces it. So it leaves on its own after eight
   seconds, and there is an X for the times eight is seven too many.

   Errors stay put. An error is the one message you may want to read twice, and
   a warning that vanishes while you are reading it is worse than no warning.
   `sticky` is for the standing notices -- "you are on the example profile" is
   a fact about the whole session, not news. */
const BANNER_SECONDS = 8;
let bannerTimer = null;

function banner(text, bad, sticky, action) {
  const box = $("#banner");
  clearTimeout(bannerTimer);
  bannerTimer = null;
  $("#banner-text").textContent = text || "";
  box.hidden = !text;
  box.classList.toggle("bad", !!bad);

  /* A rule with no way past it gets worked around outside the tool, where
     nothing is written down. So a block that a person can legitimately
     disagree with offers the override here, next to the reason, and the
     packet records which rules were waived. */
  const act = $("#banner-action");
  act.hidden = !action;
  if (action) {
    act.textContent = action.label;
    act.onclick = action.run;
  }
  if (text && !bad && !sticky) {
    bannerTimer = setTimeout(() => { box.hidden = true; }, BANNER_SECONDS * 1000);
  }
}

$("#banner-close").addEventListener("click", () => banner(""));

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return isNaN(d) ? iso : d.toLocaleDateString();
}

/* ---------------------------------------------------------------- views */

function show(view) {
  $$(".view").forEach((v) => { v.hidden = v.id !== "view-" + view; });
  document.documentElement.dataset.view = view;
  $$(".tab").forEach((t) => t.setAttribute("aria-current",
    String(t.dataset.view === view)));
  $("#gear").setAttribute("aria-current", String(view === "settings"));
  if (view === "jobs" && !state.jobs.length) loadJobs();
  if (view === "applied") loadApplications();
  if (view === "archive" && !archive.loaded) searchArchive(0);
  if (view === "growth") loadGrowth();
  if (view === "criteria") { loadCriteria(); loadEvidence(); if (critPane === "about") loadAbout(); }
  if (view === "console") loadRuns();
  // Every field in the phone panel is a live reading -- the address changes
  // with the network, the firewall rule can be added in another window, and
  // "has a device arrived" is true only after one has. Re-read on each visit
  // rather than caching, the way the other tabs do with their data.
  if (view === "settings") { loadSettings(); loadPhone(); }
}

$("#tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (tab) show(tab.dataset.view);
});

/* ================================================================= growth */

// What the postings keep asking for, and what the profile has nothing for.
// The server does the reading (skillgap.py) and caches it; this lays it out
// for one of two sets of postings at a time.
const growthState = { data: null, scope: "applied", focus: "", loading: null, title: "" };

const growthUrl = () => "/api/growth" + (growthState.title ? "?title=" + encodeURIComponent(growthState.title) : "");

function loadGrowth(focus) {
  if (focus !== undefined) growthState.focus = focus;
  if (!growthState.data) $("#learn-list").replaceChildren(
    el("li", { class: "note", text: "Reading every requirements line…" }));
  if (growthState.loading) return growthState.loading;
  const asked = growthState.title;
  growthState.loading = get(growthUrl()).then((data) => {
    // A title picked while this was loading has its own request; this
    // answer is for the old one.
    if (growthState.title !== asked) return;
    growthState.data = data;
    if (!data.applied.n && !data.title && growthState.scope === "applied") growthState.scope = "listed";
    drawGrowth();
  }).catch((err) => {
    $("#learn-list").replaceChildren(el("li", { class: "note", text: err.message }));
  }).finally(() => { growthState.loading = null; });
  return growthState.loading;
}

// From a gap chip in the reader: open Growth on that skill.
function openGrowth(id) {
  show("growth");
  loadGrowth(id).then(() => {
    const row = document.querySelector(`[data-term="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}

$("#growth-scope").addEventListener("click", (e) => {
  const btn = e.target.closest(".seg-btn");
  if (!btn) return;
  growthState.scope = btn.dataset.value;
  drawGrowth();
});
pillTrack($("#growth-scope"));

// Narrow Growth to one job title family, or back to all of them. The old
// report stays on screen, dimmed, until the new one arrives.
function pickGrowthTitle(title) {
  if (title === growthState.title) return;
  growthState.title = title;
  drawTitles();
  $$("#growth-stats, .growth-grid").forEach((n) => n.classList.add("busy"));
  get(growthUrl()).then((data) => {
    if (growthState.title !== title) return;
    growthState.data = data;
    drawGrowth();
  }).catch((err) => { $("#growth-basis").textContent = err.message; }).finally(() => {
    if (growthState.title === title) $$("#growth-stats, .growth-grid").forEach((n) => n.classList.remove("busy"));
  });
}

function drawTitles() {
  const data = growthState.data;
  const box = $("#growth-titles");
  box.textContent = "";
  if (!data || !data.titles || !data.titles.length) return;
  const scope = growthState.scope;
  const chip = (key, label, n) => box.append(el("button", { class: "title-chip", type: "button",
    "aria-pressed": String(growthState.title === key), on: { click: () => pickGrowthTitle(key) } },
    label, n == null ? null : el("span", { class: "n", text: String(n) })));
  chip("", "All titles");
  data.titles.filter((t) => t[scope] > 0 || t.key === growthState.title)
    .forEach((t) => chip(t.key, capital(t.key), t[scope]));
}

function drawGrowth() {
  const data = growthState.data;
  if (!data) return;
  const scope = growthState.scope;
  const set = data[scope];
  $$("#growth-scope .seg-btn").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.value === scope));
    const n = data[b.dataset.value].n;
    b.textContent = (b.dataset.value === "applied" ? "Applied to" : "Jobs list") + ` (${n})`;
  });
  const named = growthState.title ? ` ${growthState.title}` : "";
  $("#growth-basis").textContent = set.n
    ? `${set.with_requirements} of ${set.n}${named} postings have a requirements section to read`
    : `No${named} postings in this set yet.`;
  drawTitles();

  const any = (t) => t[scope + "_any"];
  const req = (t) => t[scope + "_required"];
  const terms = data.terms.filter((t) => any(t) > 0);
  // Industries are counted apart from skills. "Healthcare" tops every list
  // in Richmond, and it is not a thing to study over a weekend.
  const missing = terms.filter((t) => !t.have && t.kind !== "domain");
  const industries = terms.filter((t) => t.kind === "domain");
  const strong = terms.filter((t) => t.have && t.kind !== "domain");

  // The summary row.
  const stats = $("#growth-stats");
  stats.textContent = "";
  const stat = (value, label, cls) => stats.append(
    el("div", { class: "stat" + (cls ? " " + cls : "") }, el("b", { text: value }), el("span", { text: label })));
  stat(set.coverage == null ? "—" : Math.round(set.coverage) + "%", "of required skills you have, on average",
       set.coverage >= 80 ? "offer" : set.coverage != null && set.coverage < 60 ? "dead" : "live");
  stat(String(set.clear), `postings where you have every required skill`);
  stat(String(missing.length), "skills asked for that you don't have");

  // Learn next. Demand first, then how many postings it alone stands between
  // you and a full set of required skills.
  const unlocks = (t) => t["unlocks_" + scope];
  const rank = (t) => any(t) + 2 * req(t) + (set.n ? 100 * unlocks(t) / set.n : 0);
  const learn = missing.slice().sort((a, b) => rank(b) - rank(a)).slice(0, 12);
  if (learn[0]) stat(learn[0].label, `the top skill to learn, named in ${pctText(any(learn[0]))} of postings`, "dead");
  const list = $("#learn-list");
  list.textContent = "";
  if (!learn.length) list.append(el("li", { class: "note",
    text: "Nothing to learn here: every skill these postings name is already in your profile." }));
  learn.forEach((t) => {
    const li = el("li", { class: "learn" + (t.id === growthState.focus ? " focus" : ""), "data-term": t.id });
    li.append(el("div", { class: "learn-head" },
      el("b", { text: t.label }),
      el("span", { class: "kind", text: KIND_NAMES[t.kind] || t.kind })));
    const facts = [`named in ${pctText(any(t))} of these postings`];
    if (req(t)) facts.push(`required in ${pctText(req(t))}`);
    if (unlocks(t)) facts.push(`the only required skill you lack in ${plural(unlocks(t), "posting")}`);
    li.append(el("p", { class: "learn-facts", text: facts.join(" · ") }));
    li.append(el("div", { class: "learn-bar", "aria-hidden": "true" },
      el("i", { class: "any", style: `width:${Math.min(100, any(t))}%` }),
      el("i", { class: "req", style: `width:${Math.min(100, req(t))}%` })));
    li.append(el("p", { class: "learn-how", text: HOW_TO_CLOSE[t.kind] || HOW_TO_CLOSE.method }));
    if (t.examples.length) {
      const ex = el("div", { class: "learn-ex" }, el("span", { class: "note", text: "For example:" }));
      t.examples.slice(0, 3).forEach((j) => {
        const name = `${j.title} at ${j.company}`;
        if (j.uid && state.jobs.some((x) => x.uid === j.uid)) {
          ex.append(el("button", { class: "linkish", type: "button", text: name,
            on: { click: () => openJobFromGrowth(j.uid) } }));
        } else ex.append(el("span", { text: name }));
      });
      li.append(ex);
    }
    list.append(li);
  });

  // Most asked for: the top of the demand list, whether you have it or not.
  const chart = $("#demand");
  chart.textContent = "";
  terms.filter((t) => t.kind !== "domain").sort((a, b) => any(b) - any(a)).slice(0, 16).forEach((t) => {
    chart.append(el("div", { class: "dm-row" + (t.have ? " have" : " miss"), "data-term": t.id },
      el("span", { class: "dm-name", text: t.label }),
      el("span", { class: "dm-track" },
        el("i", { class: "dm-any", style: `width:${Math.min(100, any(t))}%` }),
        el("i", { class: "dm-req", style: `width:${Math.min(100, req(t))}%` })),
      el("span", { class: "dm-pct", text: pctText(any(t)) })));
  });

  const strongBox = $("#strong-list");
  strongBox.textContent = "";
  strong.slice(0, 14).forEach((t) => strongBox.append(el("span", { class: "gap-chip have",
    text: `${t.label} · ${pctText(any(t))}` })));
  if (!strong.length) strongBox.append(el("p", { class: "note", text: "None yet." }));

  const ind = $("#industry-list");
  ind.textContent = "";
  industries.slice().sort((a, b) => any(b) - any(a)).slice(0, 8).forEach((t) => {
    ind.append(el("div", { class: "dm-row" + (t.have ? " have" : " miss"), "data-term": t.id },
      el("span", { class: "dm-name", text: capital(t.label) }),
      el("span", { class: "dm-track" },
        el("i", { class: "dm-any", style: `width:${Math.min(100, any(t))}%` }),
        el("i", { class: "dm-req", style: `width:${Math.min(100, req(t))}%` })),
      el("span", { class: "dm-pct", text: pctText(any(t)) })));
  });
  if (!industries.length) ind.append(el("p", { class: "note", text: "No industry stands out in these postings." }));

  const bar = $("#barrier-list");
  bar.textContent = "";
  const barriers = data.barriers[scope] || [];
  if (!barriers.length) bar.append(el("p", { class: "note", text: "None of these postings ask for more years or a higher degree than you have." }));
  barriers.forEach((b) => bar.append(el("div", { class: "dm-row miss" },
    el("span", { class: "dm-name", text: b.label }),
    el("span", { class: "dm-track" }, el("i", { class: "dm-any", style: `width:${Math.min(100, b.pct)}%` })),
    el("span", { class: "dm-pct", text: `${b.count}` }))));

  const unknown = $("#unknown-list");
  unknown.textContent = "";
  data.unrecognized.forEach((u) => unknown.append(el("span", { class: "gap-chip other",
    text: `${u.label} · ${u.count}` })));
  if (!data.unrecognized.length) unknown.append(el("p", { class: "note", text: "None that come up more than once." }));
}

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);

const pctText = (n) => (n >= 10 || n === 0 ? Math.round(n) : n.toFixed(1)) + "%";

// What closing each kind of gap honestly takes. The point of the tab is a
// study decision, so the advice is about getting the skill, never about
// getting the word onto the page.
const HOW_TO_CLOSE = {
  tool: "A tool. A short course plus one small project that uses it gives you a true bullet to tag it on.",
  method: "A method. Look for it in work you have already done first; if it is there, a bullet that says so closes the gap.",
  domain: "Industry experience. Coursework, a certificate or a project on that industry's data is the way in short of a job there.",
  cert: "A certification or clearance. It is an exam or a credential, not a bullet: list it on the resume once you hold it, and check whether the posting takes it as preferred.",
  soft: "A soft skill. Show it with a bullet about a time you did it.",
};

function openJobFromGrowth(uid) {
  show("jobs");
  state.open = uid;
  draw();
}

/* =================================================================== runs */

const runs = { list: [], watching: null, next: 0, timer: null };

async function loadRuns() {
  try {
    const data = await get("/api/runs");
    runs.list = data.runs;
    drawRuns();
    const live = data.active || data.runs[0];
    if (live && !runs.watching) watch(live.id);
    badge(data.active);
  } catch (err) { banner(err.message, true); }
}

function badge(active) {
  const chip = $("#runbadge");
  chip.hidden = !active;
  if (active) chip.textContent = `● ${active.label}`;
}

function drawRuns() {
  const box = $("#runs");
  box.textContent = "";
  runs.list.forEach((run) => {
    box.append(el("li", {
      "aria-current": String(run.id === runs.watching),
      on: { click: () => watch(run.id) },
    },
      el("span", { class: "label", text: run.label }),
      el("span", { class: "meta", text: `${run.state} · ${run.started.slice(11)}` +
        (run.error ? ` · ${run.error}` : "") })));
    box.lastChild.querySelector(".meta").dataset.state = run.state;
    box.lastChild.dataset.id = run.id;
  });
  if (!runs.list.length) box.append(el("li", { class: "empty", text: "no runs yet" }));
  drawRunNow();
}

function drawRunNow() {
  const run = runs.list.find((r) => r.id === runs.watching);
  const card = $("#run-now");
  card.hidden = !run;
  $("#term-title").textContent = run ? run.label : "Output";
  if (!run) return;
  card.textContent = "";
  card.dataset.state = run.state;
  card.append(
    el("span", { class: "run-state" }, el("i", { class: "pulse" }), run.state),
    el("b", { text: run.label }),
    el("span", { class: "note", text: `started ${run.started.slice(11)}`
      + (run.error ? ` \u00b7 ${run.error}` : "") }));
}

/* Poll one run's output. The server is asked for lines after the last one
   already on screen, so a sweep that prints a thousand lines is sent once. */
function watch(runId) {
  if (runs.timer) clearTimeout(runs.timer);
  runs.watching = runId;
  runs.next = 0;
  $("#out").textContent = "";
  drawRuns();
  poll();
}

async function poll() {
  if (!runs.watching) return;
  let data;
  try {
    data = await get(`/api/run?id=${encodeURIComponent(runs.watching)}&after=${runs.next}`);
  } catch (err) {
    $("#out").append("\n" + err.message);
    return;
  }
  if (data.missing) { $("#out").textContent = "that run has scrolled out of the list"; return; }

  if (data.lines.length) {
    runs.next = data.next;
    const out = $("#out");
    // append() adds a text node. `textContent +=` re-reads and re-parses the
    // whole log on every poll, which gets slow on a long sweep.
    out.append(data.lines.join("\n") + "\n");
    if ($("#follow").checked) out.scrollTop = out.scrollHeight;
  }
  if (!data.done) {
    runs.timer = setTimeout(poll, 700);
    return;
  }
  await loadRuns();
  finishedRun(data);
}

/* What the rest of the page does once a run ends. A sweep changed the queue
   and a packet build changed the application log, so the tables that show
   either are reloaded rather than left stale behind a finished console. */
function finishedRun(data) {
  if (data.state === "failed") {
    banner(`${data.label} failed: ${data.error}`, true);
    return;
  }
  const result = data.result || {};
  if (data.kind === "sweep") {
    loadJobs();
    banner(`Sweep finished. ${plural(result.jobs || 0, "posting")} in the queue.`);
  } else if (data.kind === "packet") {
    loadJobs();
    loadApplications();
    if (result.blocked) {
      const why = (result.problems || []).join(" ");
      const again = lastPacket;
      banner(`Packet blocked: ${why}`, true, true, again && {
        label: "Build it anyway",
        run: () => {
          if (!confirm(`This is what the guard said:\n\n${why}\n\n`
                     + `Build the packet regardless? The rules you are `
                     + `overriding are written into APPLY.md and into the `
                     + `application record.`)) return;
          startRun(Object.assign({}, again, { force: true }),
                   "Building anyway. The override is recorded.");
        },
      });
    } else if (result.ok) {
      banner(`Packet built. Open APPLY.md, work through it, then mark it `
           + `applied on the Applied tab.`);
    } else {
      banner((result.problems || ["the packet did not build"]).join(" "), true);
    }
  }
}

let lastPacket = null;   // the body of the most recent packet request, so
                         // an override can repeat it with force set

function startRun(body, message) {
  if (body && body.kind === "packet") lastPacket = body;
  banner(message || "");
  return post("/api/run/start", body).then((run) => {
    show("console");
    loadRuns();
    watch(run.id);
    return run;
  }).catch((err) => banner(err.message, true));
}

$("#runbadge").addEventListener("click", () => show("console"));
$("#run-sweep").addEventListener("click", () => startRun({ kind: "sweep" }));
$("#run-dry").addEventListener("click", () => startRun({ kind: "sweep", dry: true }));
$("#run-check").addEventListener("click", () => startRun({ kind: "engine", which: "check" }));
$("#run-gap").addEventListener("click", () => startRun({ kind: "engine", which: "gap" }));
$("#sweep").addEventListener("click", () => startRun({ kind: "sweep" }));

/* ============================================================ jobs table */

// Newest first until Settings says otherwise. A posting that went up this
// morning has a few dozen applicants; the same posting next week has a few
// hundred, and the recruiter reads them in the order they came in. So the
// thing to see first when the window opens is what is new, and the score
// breaks the tie between two postings from the same day.
const state = { jobs: [], sort: "age_days", dir: "asc", open: null,
                market: {}, marketIndex: null, starsGone: [],
                lastRun: null, details: {}, pasted: {}, partMax: {},
                tier: "", rows: [], readerKey: "" };

// The two orders the "Order" menu and Settings offer, as a column and a
// direction. Clicking a column header picks a third, and the menu says so.
const ORDERS = { newest: ["age_days", "asc"], score: ["score", "desc"] };

function setOrder(order) {
  const [key, dir] = ORDERS[order] || ORDERS.newest;
  state.sort = key;
  state.dir = dir;
  $("#order").value = ORDERS[order] ? order : "newest";
  markSort();
}

function markSort() {
  $$(".grid#jobs th.sortable").forEach((th) => {
    if (th.dataset.key === state.sort) th.dataset.dir = state.dir;
    else th.removeAttribute("data-dir");
  });
}

// Between two rows that sort level: the better score first, and between two
// equal scores, the newer posting. Without it a table sorted by source is a
// different shuffle every time it draws.
function tiebreak(a, b) {
  if (state.sort !== "score" && (b.score || 0) !== (a.score || 0)) {
    return (b.score || 0) - (a.score || 0);
  }
  const x = a.age_days, y = b.age_days;
  const bx = x === null || x === undefined, by = y === null || y === undefined;
  if (bx !== by) return bx ? 1 : -1;
  return bx ? 0 : x - y;
}

async function loadJobs() {
  try {
    const data = await get("/api/jobs");
    state.jobs = data.jobs;
    state.starsGone = data.stars_gone || [];
    state.lastRun = data.last_run || null;
    stamp(data);
    drawStarsGone();
    drawSources();
    draw();
    loadMarket();
  } catch (err) {
    banner(err.message, true);
  }
}

/* ------------------------------------------------------------ the radar ran

   This window stays open for days, and the radar runs to a schedule behind
   it -- 7:00, 12:30 and 17:00 by default. A page that loaded its rows once
   is showing yesterday by breakfast, and the only sign is a timestamp under
   the filters that nobody reads until the table looks wrong.

   So ask. `/api/pulse` stats one file and answers in bytes, which is cheap
   enough to do on a timer; the rows are only refetched when the answer
   changes. The tab also asks the moment it comes back to the front, because
   the most likely minute for the table to be stale is the one where you
   have just walked back to it. */

const PULSE_SECONDS = 60;
let pulsing = false;

async function checkRadar() {
  // Not while the previous check is still out, and not while the page is
  // hidden: a minimized window polling every minute all night is a battery
  // cost for an answer nobody is looking at.
  if (pulsing || document.hidden || !state.lastRun) return;
  pulsing = true;
  try {
    const beat = await get("/api/pulse");
    if (beat.last_run && beat.last_run !== state.lastRun) await refreshJobs();
  } catch {
    // The server restarting is not news. The next beat will find it.
  } finally {
    pulsing = false;
  }
}

/* Reload in place. Every filter, the sort, the open row and the stars are
   held in `state` and on the controls, so redrawing keeps all of them; what
   moves is the rows. Say how many are new rather than refreshing silently,
   because a table that changes under you with no explanation reads as a
   bug. */
async function refreshJobs() {
  const before = new Set(state.jobs.map((j) => j.uid));
  await loadJobs();
  const added = state.jobs.filter((j) => !before.has(j.uid)).length;
  const ran = state.lastRun ? new Date(state.lastRun) : null;
  const at = ran && !isNaN(ran)
    ? ran.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  banner(added
    ? `The radar ran${at ? " at " + at : ""}. ${plural(added, "new posting")}.`
    : `The radar ran${at ? " at " + at : ""}. Nothing new above your floor.`);
}

setInterval(checkRadar, PULSE_SECONDS * 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) checkRadar();
});
window.addEventListener("focus", checkRadar);

// How a posting compares with the rest of its corner of the market, and a
// salary band for the ones that never printed one. Both come from the same
// index of past snapshots, which the server folds up in the background, so
// this is a second request that the table does not wait for. While it is
// still building, ask again; ten tries is about twenty seconds, and after
// that the column simply stays empty.
async function loadMarket(attempt) {
  const tries = attempt || 0;
  try {
    const data = await get("/api/market");
    state.marketIndex = data.index || null;
    if (!data.ready) {
      if (tries < 10) setTimeout(() => loadMarket(tries + 1), 2000);
      return;
    }
    state.market = data.jobs || {};
    draw();
  } catch {
    // A table that works without it stays a table that works without it.
  }
}

const marketOf = (job) => (state.market[job.uid] || {}).market || null;
const estimateOf = (job) => (state.market[job.uid] || {}).salary_estimate || null;

function stamp(data) {
  const ran = data.last_run ? new Date(data.last_run) : null;
  const tiers = data.tiers || {};
  const parts = ["A", "B", "C", "D"].map((t) => `${t} ${tiers[t] || 0}`);
  const floor = data.floor;
  // The F rows are kept for the market data and never shown, so counting
  // them made a rescore that screened out fourteen postings look like it
  // changed nothing.
  const out = state.jobs.filter((j) => j.tier === "F").length;
  const box = $("#counts");
  box.textContent = "";
  const total = ["A", "B", "C", "D"].reduce((sum, t) => sum + (tiers[t] || 0), 0);
  const COLORS = { A: "var(--good)", B: "var(--accent)", C: "var(--warn)", D: "var(--ink-faint)" };
  if (total) {
    box.append(el("div", { class: "tierbar", role: "img", "aria-label": parts.join(", ") },
      ...["A", "B", "C", "D"].filter((t) => tiers[t]).map((t) => {
        const seg = el("i", { class: t, title: `${t}: ${tiers[t]}` });
        seg.style.flexGrow = String(tiers[t]);
        return seg;
      })));
  }
  const key = el("div", { class: "tier-key" },
    el("span", {}, el("b", { text: String(state.jobs.length - out) }), " postings"),
    ...["A", "B", "C", "D"].map((t) => {
      const dot = el("span", { class: "dot" });
      dot.style.background = COLORS[t];
      return el("span", {}, dot, `${t} `, el("b", { text: String(tiers[t] || 0) }));
    }));
  if (out) key.append(el("span", { text: `${out} screened out` }));
  if (floor && floor.capped) {
    key.append(el("span", { text: `nothing under ${floor.lowest} made the cache (floor is ${floor.configured})` }));
  }
  if (ran) key.append(el("span", { text: `scored ${ran.toLocaleString()}` }));
  box.append(key);
}

// Reads the two score boxes. Blank means "no bound on this end" rather than
// zero, so clearing the low box shows everything instead of nothing, and the
// pair is swapped when they are entered backwards -- typing 89 then 75, which
// is the order a person says it out loud, is not an error worth a message.
function scoreRange() {
  const read = (sel, fallback) => {
    const raw = $(sel).value.trim();
    if (raw === "") return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  };
  const lo = read("#score-min", 0);
  const hi = read("#score-max", 100);
  return lo <= hi ? [lo, hi] : [hi, lo];
}

/* Whether one posting passes one set of filters, in the shape Settings keeps
   a saved view in. The list uses it with what is on screen, and the sidebar
   uses it with each saved view to count what that view would show, so the
   two can never disagree about what a filter means. A view saved before a
   filter existed has no key for it and lets everything through. */
function matches(j, f) {
  const score = j.score || 0;
  const lo = Math.min(f.score_min, f.score_max), hi = Math.max(f.score_min, f.score_max);
  if (score < lo || score > hi) return false;
  if (f.hide_applied && j.applied) return false;
  if (f.hide_prepared && j.prepared) return false;
  if (f.remote_only && !j.remote) return false;
  if (f.starred_only && !j.starred) return false;
  if (f.has_pay && !(j.salary_min || j.salary_max)) return false;
  if (f.market_up && (marketOf(j) || {}).direction !== "up") return false;
  if (f.tier && j.tier !== f.tier) return false;
  if (f.source && j.source !== f.source) return false;
  if (f.posted_within && f.posted_within !== "any") {
    if (j.age_days === null || j.age_days === undefined) return false;
    if (j.age_days > Number(f.posted_within)) return false;
  }
  const q = (f.search || "").toLowerCase();
  if (!q) return true;
  return `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q);
}

function visible() {
  const f = currentFilters();
  const rows = state.jobs.filter((j) => matches(j, f));
  return sortRows(rows, state.sort, state.dir, (row, key) => {
    // Salary sorts on what the cell shows, estimate included, so the column
    // never appears to be out of order. Market sorts on the arrow's number.
    if (key === "salary") {
      const est = estimateOf(row);
      return row.salary_min || row.salary_max
        || (est ? est.salary_min : null) || null;
    }
    if (key === "market") {
      const m = marketOf(row);
      return m ? m.delta : null;
    }
    return row[key];
  }, tiebreak);
}

// A missing value sorts last in both directions. It used to count as -1,
// which put every posting with no date at the top of "newest first" -- the
// exact rows the order was meant to push down.
function sortRows(rows, key, dir, pick, tie) {
  const sign = dir === "desc" ? -1 : 1;
  const value = pick || ((row, k) => row[k]);
  const blank = (v) => v === null || v === undefined || v === "";
  const level = (a, b) => (tie ? tie(a, b) : 0);
  return rows.slice().sort((a, b) => {
    const x = value(a, key), y = value(b, key);
    if (blank(x) || blank(y)) {
      if (blank(x) && blank(y)) return level(a, b);
      return blank(x) ? 1 : -1;
    }
    const d = typeof x === "string" || typeof y === "string"
      ? String(x).localeCompare(String(y))
      : x - y;
    return d ? sign * d : level(a, b);
  });
}

// The arrow. Green up means this posting is friendlier to an applicant than
// its peers -- better paid, fewer people chasing it, or newly posted. Red
// down is the opposite. The number is the distance from the middle of the
// group, so ▲12 reads as "twelve points better than the median posting in
// this job family and region".
function marketCell(job) {
  const m = marketOf(job);
  const td = el("td", { class: "mkt" });
  if (!m) return td;
  const glyph = m.direction === "up" ? "▲" : (m.direction === "down" ? "▼" : "•");
  const text = m.direction === "flat" ? "even" : glyph + Math.abs(m.delta);
  td.append(el("span", {
    class: "mktv " + m.direction + (m.confident ? "" : " thin"),
    text: text,
    title: (m.direction === "flat" ? "" : `${Math.abs(m.delta)} points `
             + `${m.direction === "up" ? "above" : "below"} the middle. `)
           + `Better than ${m.percentile}% of the ${m.peers} postings in `
           + `this group: ${m.group}.`
           + (m.confident ? "" : " A small group, so read it loosely."),
  }));
  return td;
}

function salaryCell(job) {
  const estimate = estimateOf(job);
  if (!estimate) return cell(money(job), "salary");
  return el("td", { class: "salary est" }, el("span", {
    text: money(estimate),
    title: estimate.basis,
  }));
}

// Roughly one posting in twenty quotes an hourly rate in the same fields as
// the annual ones. Rounded to thousands those all read "$0k-$0k", which is
// what the table used to show for every hourly job on it. Nothing in this
// dataset pays under $1,000 a year or over $1,000 an hour, so the boundary
// does the sorting.
function money(job) {
  const hourly = (n) => n > 0 && n < 1000;
  const fmt = (n) => (hourly(n) ? "$" + Math.round(n) + "/hr"
                                : "$" + Math.round(n / 1000) + "k");
  const lo = job.salary_min || 0, hi = job.salary_max || 0;
  if (lo && hi) return lo === hi ? fmt(lo) : fmt(lo) + "-" + fmt(hi);
  if (lo) return fmt(lo) + "+";
  if (hi) return "to " + fmt(hi);
  return "—";
}

// 1st, 2nd, 3rd, 4th... 11th through 13th are the exceptions everyone's first
// attempt gets wrong, which is why this is a function and not a "th".
function ordinal(n) {
  const last = n % 10, pair = n % 100;
  if (last === 1 && pair !== 11) return n + "st";
  if (last === 2 && pair !== 12) return n + "nd";
  if (last === 3 && pair !== 13) return n + "rd";
  return n + "th";
}

function age(job) {
  if (job.age_days === null || job.age_days === undefined) return "—";
  if (job.age_days === 0) return "today";
  return job.age_days + "d";
}

const cell = (text, cls) => el("td", { class: cls, text: text });

/* The star. A filled one is yellow, an empty one only appears on hover or
   focus, because 300 gray outlines down the left of a table is noise. The
   button carries its own label so a screen reader gets "Star" rather than a
   character it has to guess at. */
function starCell(job) {
  const on = !!job.starred;
  const button = el("button", {
    class: "starbtn" + (on ? " on" : ""),
    type: "button",
    title: on ? "Starred. Click to unstar." : "Star this to come back to it",
    "aria-pressed": on ? "true" : "false",
    "aria-label": on ? "Starred" : "Star this posting",
    text: on ? "\u2605" : "\u2606",
  });
  return el("td", { class: "star" }, button);
}

/* Written on the server, so a star set on the phone is on the row in the
   window. The row updates before the round trip finishes and puts itself
   back if the write fails -- a star is not worth a spinner, but it is worth
   telling the truth about. */
async function toggleStar(job) {
  const want = !job.starred;
  job.starred = want;
  draw();
  try {
    await post("/api/star", { uid: job.uid, starred: want, title: job.title,
      company: job.company, url: job.url, source: job.source });
  } catch (err) {
    job.starred = !want;
    draw();
    banner(err.message, true);
  }
}

/* A star outlives the posting it is on: the radar keeps thirty days, and a
   starred row that ages out would otherwise disappear without a word. The
   file kept the title and the link, so say so and keep the link alive. */
function drawStarsGone() {
  const note = $("#stars-gone");
  note.textContent = "";
  note.hidden = state.starsGone.length === 0;
  if (note.hidden) return;
  note.append(document.createTextNode(
    plural(state.starsGone.length, "starred posting") +
    " aged out of the list: "));
  state.starsGone.forEach((s, i) => {
    if (i) note.append(document.createTextNode(", "));
    const label = `${s.title || "a posting"}${s.company ? " at " + s.company : ""}`;
    note.append(webUrl(s.url)
      ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer",
                  text: label })
      : document.createTextNode(label));
  });
}

/* Phone width, asked once and cached by the browser. The listener redraws
   rather than reloads, because the only thing that changes is which column
   the market arrow sits in. */
const PHONE = window.matchMedia("(max-width: 700px)");
const narrow = () => PHONE.matches;

/* Star, score and title always lead. The rest follow Settings, and on a
   phone the market arrow moves up beside the title: the sixth column there
   is far enough into a sideways scroll that it reads as missing. */
const JOB_COLUMNS = ["company", "location", "age_days", "market", "salary",
                     "source"];

function jobColumns() {
  const chosen = (prefs.job_columns || JOB_COLUMNS)
    .filter((k) => JOB_COLUMNS.includes(k));
  if (!narrow() || !chosen.includes("market")) return chosen;
  return ["market", ...chosen.filter((k) => k !== "market")];
}

function orderJobsHead() {
  const head = $("#jobs thead tr");
  const shown = jobColumns();
  ["score", "title", ...shown].forEach((key) =>
    head.append(head.querySelector(`th[data-key="${key}"]`)));
  JOB_COLUMNS.forEach((key) => {
    const th = head.querySelector(`th[data-key="${key}"]`);
    th.hidden = !shown.includes(key);
    if (th.hidden) head.append(th);
  });
}

PHONE.addEventListener("change", () => {
  orderJobsHead(); applyLayout(); applyJobsStyle(); draw();
});

/* -- rows or tiles -- */

/* Held upright, a phone shows two or three columns of a row and the rest is
   a sideways scroll per row. Tiles stack each row into a card, every field
   labeled, so a posting reads top to bottom. The table markup stays the
   same and the stylesheet does the stacking, which keeps sorting, the star
   and the expanded posting working without a second renderer.

   The choice is per device, so it lives in localStorage and not in the
   settings file the phone and the desktop share. A phone starts in tiles and
   a desktop in rows; the button overrides either. `layoutChoice` covers a
   browser that refuses localStorage, so the button still works for the
   visit. */
const LAYOUT_KEY = "jobdesk.layout";
let layoutChoice = null;
try { layoutChoice = localStorage.getItem(LAYOUT_KEY); } catch { /* private mode */ }

function layout() {
  if (layoutChoice === "rows" || layoutChoice === "tiles") return layoutChoice;
  return narrow() ? "tiles" : "rows";
}

function applyLayout() {
  const now = layout();
  document.documentElement.dataset.layout = now;
  $$(".layout-toggle").forEach((b) => {
    b.textContent = now === "tiles" ? "Rows" : "Tiles";
    b.title = now === "tiles" ? "Show these as a table" : "Show each row as a card";
  });
}

$$(".layout-toggle").forEach((b) => b.addEventListener("click", () => {
  layoutChoice = layout() === "tiles" ? "rows" : "tiles";
  try { localStorage.setItem(LAYOUT_KEY, layoutChoice); } catch { /* private mode */ }
  applyLayout();
}));

applyLayout();

/* -- how the Jobs list is drawn -- */

/* Four ways to read the same rows, per device like Rows and Tiles:
   List is Mailroom's two-line item, Compact is the dense table with every
   column (and the posting opening under its row), Tiles is cards, and
   Timeline is the list grouped by the day each posting went up. A phone
   starts on Timeline and a desktop on List. theme.js sets the attribute
   before the first paint, from the same key. */
const JOBS_KEY = "jobdesk.jobs";
const JOBS_STYLES = ["list", "compact", "tiles", "timeline"];
let jobsChoice = null;
try { jobsChoice = localStorage.getItem(JOBS_KEY); } catch { /* private mode */ }

function jobsStyle() {
  if (JOBS_STYLES.includes(jobsChoice)) return jobsChoice;
  return narrow() ? "timeline" : "list";
}

function applyJobsStyle() {
  const now = jobsStyle();
  document.documentElement.dataset.jobs = now;
  $$("#styles .style-btn").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.style === now)));
}

pillTrack($("#styles"));
pillTrack($("#tabs"));

$("#styles").addEventListener("click", (e) => {
  const b = e.target.closest(".style-btn");
  if (!b) return;
  jobsChoice = b.dataset.style;
  try { localStorage.setItem(JOBS_KEY, jobsChoice); } catch { /* private mode */ }
  applyJobsStyle();
  draw();
});

applyJobsStyle();

/* The open posting reads beside the list on a desktop, in the reader pane,
   and in place under its row on a phone and in Compact, where the point is
   to see every column and a second pane would halve them. */
const readerMode = () => !narrow() && jobsStyle() !== "compact";

/* -- the menu folds to icons -- */

// Per device, like the list style: a laptop may want the room and a big
// monitor not. Folded, an open posting splits the window half and half.
const SIDE_KEY = "jobdesk.side";

function applySide(folded) {
  document.documentElement.dataset.side = folded ? "folded" : "open";
  const b = $("#side-fold");
  b.setAttribute("aria-expanded", String(!folded));
  b.title = folded ? "Show the menu" : "Fold the menu to icons";
  b.setAttribute("aria-label", b.title);
}

$("#side-fold").addEventListener("click", () => {
  const folded = document.documentElement.dataset.side !== "folded";
  try { localStorage.setItem(SIDE_KEY, folded ? "folded" : "open"); } catch { /* private mode */ }
  applySide(folded);
});

applySide(document.documentElement.dataset.side === "folded");

/* A tile has no header row above it, so every cell carries its column's
   name for the stylesheet to print. The star, the score and the lead line
   explain themselves. Hidden columns have no cell, so only the shown
   headers are counted. */
function labelCells(tableSel) {
  const names = $$(tableSel + " thead th").filter((th) => !th.hidden)
    .map((th) => th.textContent.trim());
  $$(tableSel + " tbody > tr:not(.detail)").forEach((tr) => {
    Array.from(tr.children).forEach((td, i) => {
      if (td.matches(".star, .score, .lead, .item, .day") || !names[i]) return;
      td.dataset.label = names[i];
    });
  });
}


/* Long lists draw a page at a time, and the next page when the end of the
   last one comes near the screen. Jobs holds 800-odd rows and Applied a few
   hundred; drawn all at once that was 17,000 elements, rebuilt on every
   filter change, and a phone froze on it or dropped the tab. `until` is a
   row that must be drawn now, such as the open one. */
const PAGE_ROWS = 60;

function pageRows(body, items, drawOne, opts = {}) {
  if (body._pager) body._pager.disconnect();
  const span = opts.span || 1;
  const until = Math.max(opts.until || 0, 0);
  const sentinel = el("tr", { class: "more-rows", "aria-hidden": "true" },
    el("td", { colSpan: span }));
  let at = 0;
  let pager = null;
  const step = (min) => {
    sentinel.remove();
    const end = Math.min(items.length, Math.max(at + PAGE_ROWS, min + 1));
    for (; at < end; at++) drawOne(items[at], at);
    if (opts.after) opts.after();
    // Observed again each time: the observer only reports a change, and a
    // sentinel taken out and put back within one task has not changed.
    pager.unobserve(sentinel);
    if (at < items.length) { body.append(sentinel); pager.observe(sentinel); }
    else pager.disconnect();
  };
  pager = new IntersectionObserver((seen) => {
    if (seen.some((e) => e.isIntersecting)) step(0);
  // Against the screen, not the table's own scroll box: on a phone that box
  // grows to fit, so the sentinel would always look visible to it and every
  // page would load at once. Clipping by the box still counts either way.
  }, { rootMargin: "900px 0px" });
  body._pager = pager;
  step(until);
}

/* The search box redraws after typing stops, not on every key. A queue of
   a few hundred rows is a few hundred rows of DOM each time. */
let drawTimer = null;
function drawSoon() {
  clearTimeout(drawTimer);
  drawTimer = setTimeout(draw, 150);
}

// The Timeline's day headings. Past a week the exact day stops mattering
// and a heading per day is just more headings.
function dayLabel(days) {
  if (days === null || days === undefined) return "Date unknown";
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return `${days} days ago`;
  if (days <= 14) return "Last week";
  return "Older";
}

// Mailroom's item: score and tier on the left, then the title with its age,
// "company · location", and a line of pills for pay, the market arrow, the
// packet and the source. One cell, so the table's sorting, star and click
// handling carry on unchanged.
function itemCell(job, colSpan) {
  const td = el("td", { class: "item", colSpan });
  const score = el("div", { class: "it-score" },
    el("b", { text: String(job.score) }),
    el("span", { class: "tier " + job.tier, text: job.tier }));
  if (job.was !== undefined && job.was !== job.score) {
    const up = job.score > job.was;
    score.append(el("span", { class: "moved " + (up ? "up" : "down"),
      text: (up ? "▲" : "▼") + Math.abs(job.score - job.was) }));
  }
  const pills = el("div", { class: "it-pills" });
  const pay = job.salary_min || job.salary_max ? money(job) : null;
  const est = !pay && estimateOf(job);
  if (pay) pills.append(el("span", { class: "pl pay", text: pay }));
  else if (est) pills.append(el("span", { class: "pl est", text: "~" + money(est),
                                          title: est.basis }));
  const m = marketOf(job);
  if (m && m.direction !== "flat") {
    pills.append(el("span", { class: "pl mkt " + m.direction,
      text: (m.direction === "up" ? "▲" : "▼") + Math.abs(m.delta) + " market" }));
  }
  if (job.applied) pills.append(el("span", { class: "pl status", text: job.app_status }));
  else if (job.prepared) pills.append(el("span", { class: "pl status", text: "packet ready" }));
  if (job.remote) pills.append(el("span", { class: "pl", text: "Remote" }));
  if (job.source) pills.append(el("span", { class: "pl faint", text: job.source }));

  const main = el("div", { class: "it-main" },
    el("div", { class: "it-top" },
      el("span", { class: "it-title", text: job.title }),
      el("span", { class: "it-age", text: age(job) })),
    el("div", { class: "it-sub", text: [job.company,
      job.remote ? "Remote" : job.location].filter(Boolean).join(" · ") }),
    pills);
  td.append(score, main, starCell(job).firstChild);
  return td;
}

function draw() {
  clearTimeout(drawTimer);
  let rows = visible();
  const columns = jobColumns();
  const span = 3 + columns.length;
  const style = jobsStyle();
  const items = style === "list" || style === "timeline";
  const reading = readerMode();
  // The Timeline groups by day, and the chosen order still decides who
  // comes first within one. Array.sort is stable, so that survives.
  if (style === "timeline") {
    const at = (j) => (j.age_days === null || j.age_days === undefined ? 1e9 : j.age_days);
    rows = rows.slice().sort((a, b) => at(a) - at(b));
  }
  state.rows = rows;
  const body = $("#rows");
  body.textContent = "";
  // The rows cascade in when the list itself changes (a filter, a search,
  // another style) and stay still when one row does (a star, a status).
  const listKey = style + "|" + rows.map((j) => j.uid).join(",");
  body.classList.toggle("enter", listKey !== state.listKey);
  state.listKey = listKey;
  let day = null;
  let n = 0;

  pageRows(body, rows, (job) => {
    if (style === "timeline" && dayLabel(job.age_days) !== day) {
      day = dayLabel(job.age_days);
      body.append(el("tr", { class: "day" }, el("td", { class: "day", colSpan: span, text: day })));
    }
    const open = state.open === job.uid;
    const tr = el("tr", { class: "row" + (job.applied ? " applied" : "")
                                  + (open && reading ? " is-open" : "") });
    tr.dataset.uid = job.uid;
    if (n < 16) tr.style.setProperty("--n", String(n++));

    if (items) {
      tr.append(itemCell(job, span));
    } else {
      const score = el("td", { class: "score", text: String(job.score) });
      score.append(el("span", { class: "tier " + job.tier, text: job.tier }));
      if (job.was !== undefined && job.was !== job.score) {
        const up = job.score > job.was;
        score.append(el("span", { class: "moved " + (up ? "up" : "down"),
          text: (up ? "▲" : "▼") + Math.abs(job.score - job.was) }));
      }

      const title = el("td", { class: "lead" }, job.title);
      if (job.applied) title.append(el("span", { class: "pill", text: job.app_status }));
      else if (job.prepared) title.append(el("span", { class: "pill", text: "packet ready" }));

      const cells = {
        company: () => cell(job.company),
        location: () => cell(job.remote ? "Remote" : (job.location || "—")),
        age_days: () => cell(age(job)),
        market: () => marketCell(job),
        salary: () => salaryCell(job),
        source: () => cell(job.source),
      };
      tr.append(starCell(job), score, title, ...columns.map((k) => cells[k]()));
    }
    body.append(tr);

    if (open && !reading) body.append(detailRow(job));
  }, { span, until: rows.findIndex((j) => j.uid === state.open),
       after: () => labelCells("#jobs") });
  drawReader(reading ? state.jobs.find((j) => j.uid === state.open) : null);

  drawMoreCount();
  drawChips();
  $("#ct-jobs").textContent = state.jobs.length ? String(rows.length) : "";
  if (state.jobs.length) drawSideViews();
  $("#jobs-empty").hidden = rows.length > 0;
  $("#jobs-empty").textContent = state.jobs.length
    ? "Nothing matches those filters."
    : "No postings yet. Press “Run the radar” in the menu.";
}

/* -- the reader pane -- */

/* Built again only when something it shows has changed, not on every draw:
   a draw happens on each keystroke in the search box, and rebuilding would
   throw away a half-pasted description and the scroll position with it. */
function drawReader(job) {
  const pane = $("#reader");
  $("#jobs-split").classList.toggle("reading", !!job);
  pane.hidden = !job;
  if (!job) { pane.textContent = ""; state.readerKey = ""; pane._job = null; return; }
  const cached = state.details[job.uid] || {};
  const key = [job.uid, job.score, job.tier, job.starred, job.prepared, job.applied,
               job.app_status, job.jd_chars, !!state.market[job.uid], cached.error || ""]
    .join("|");
  if (key === state.readerKey && pane.firstChild && pane._job === job) return;
  state.readerKey = key;
  pane.classList.toggle("fresh", !pane._job || pane._job.uid !== job.uid);
  pane._job = job;
  pane.textContent = "";

  const m = marketOf(job);
  const pills = el("div", { class: "rd-pills" });
  const pay = job.salary_min || job.salary_max ? money(job) : null;
  const est = !pay && estimateOf(job);
  if (pay) pills.append(el("span", { class: "pl pay", text: pay }));
  else if (est) pills.append(el("span", { class: "pl est", text: "~" + money(est) + " estimated",
                                          title: est.basis }));
  pills.append(el("span", { class: "pl", text: job.age_days === 0 ? "posted today"
    : job.age_days ? `posted ${job.age_days}d ago` : "date unknown" }));
  if (job.source) pills.append(el("span", { class: "pl", text: job.source }));
  if (job.remote) pills.append(el("span", { class: "pl", text: "Remote" }));
  if (job.applied) pills.append(el("span", { class: "pl status", text: job.app_status }));
  else if (job.prepared) pills.append(el("span", { class: "pl status", text: "packet ready" }));

  const star = el("button", { class: "ghost rd-star" + (job.starred ? " on" : ""), type: "button",
    "aria-pressed": String(!!job.starred),
    text: job.starred ? "★ Starred" : "☆ Star",
    on: { click: () => toggleStar(job) } });
  const close = el("button", { class: "ghost rd-close", type: "button",
    "aria-label": "Close this posting", title: "Close (Esc)", text: "×",
    on: { click: () => { state.open = null; draw(); } } });

  const head = el("header", { class: "rd-head" },
    el("div", { class: "rd-score" },
      el("b", { text: String(job.score) }),
      el("span", { class: "tier " + job.tier, text: job.tier }),
      m ? el("span", { class: "mktv " + m.direction,
        text: m.direction === "flat" ? "even with market"
          : (m.direction === "up" ? "▲" : "▼") + Math.abs(m.delta) + " market" }) : ""),
    el("div", { class: "rd-tools" }, star, close));
  pane.append(head,
    el("h2", { class: "rd-title", text: job.title }),
    el("div", { class: "rd-sub", text: [job.company, job.remote ? "Remote" : job.location]
      .filter(Boolean).join(" · ") }),
    pills);
  const body = el("div", { class: "rd-body" });
  fillPosting(job, body);
  pane.append(body,
    el("div", { class: "rd-keys note" },
      "↑ ↓ or J K move · S star · O open the posting · Esc close · / search"));
  pane.scrollTop = 0;
}

/* -- the expanded posting -- */

function jdBlocks(blocks) {
  const box = el("div", { class: "jd" });
  blocks.forEach((block) => {
    if (block.kind === "end") return;   // a section break with nothing to show
    if (block.kind === "heading") box.append(el("h4", { text: block.text }));
    else if (block.kind === "list") {
      box.append(el("ul", {}, block.items.map((i) => el("li", { text: i }))));
    } else box.append(el("p", { text: block.text }));
  });
  return box;
}

function detailRow(job) {
  const td = el("td", { colSpan: 3 + jobColumns().length });
  fillPosting(job, td);
  const tr = el("tr", { class: "detail" });
  tr.append(td);
  return tr;
}

/* The posting itself: the buttons, the guard lines, why it scored what it
   did, the market, and the description. The same parts whether they open
   under the row or in the reader pane. */
function fillPosting(job, td) {
  const actions = el("div", { class: "actions" });

  if (webUrl(job.url)) {
    actions.append(el("a", { href: job.url, target: "_blank",
      rel: "noopener noreferrer", text: "Open the posting ↗" }));
  }

  // Pasting beats fetching whenever someone bothers to do it: a careers site
  // that hands a script a login page hands this textarea the real posting.
  // What was pasted is kept per posting, because starring a row or typing in
  // the search box redraws the table and would otherwise throw it away.
  const kept = state.pasted[job.uid] || (state.pasted[job.uid] = { text: "", open: false });
  const box = el("textarea", {
    rows: 8, placeholder: "Paste the job description here, then build.",
  });
  box.value = kept.text;
  box.addEventListener("input", () => { kept.text = box.value; });
  const paste = el("div", { class: "jd-paste" }, box);
  paste.hidden = !kept.open;

  actions.append(el("button", {
    class: job.prepared || job.applied ? "ghost" : "",
    text: job.prepared || job.applied ? "Rebuild the packet" : "Build the packet",
    on: { click: (e) => {
      e.stopPropagation();
      buildPacket(job, paste.querySelector("textarea").value.trim());
    } },
  }));

  actions.append(el("button", { class: "ghost", text: "Paste the description",
    on: { click: (e) => { e.stopPropagation(); paste.hidden = !paste.hidden;
                          kept.open = !paste.hidden;
                          if (!paste.hidden) box.focus(); } } }));

  if (job.application) {
    actions.append(el("button", { class: "ghost", text: "Open the folder",
      on: { click: (e) => { e.stopPropagation(); openPacket(job.application); } } }));
  }
  if (job.application && !job.applied) {
    actions.append(el("button", { class: "ghost", text: "I applied to this",
      on: { click: (e) => { e.stopPropagation(); markApplied(job.application); } } }));
  }
  actions.append(el("span", { class: "grow" }));
  actions.append(el("span", { class: "note",
    text: job.jd_chars ? `${job.jd_chars.toLocaleString()} characters of description`
                       : "" }));
  td.append(actions);

  const guards = el("div", { class: "guards" });
  td.append(guards);

  const why = el("div", { class: "why" },
    (job.reasons || []).map((r) => el("span", { text: r })));
  td.append(why);
  const gapBox = el("div", { hidden: true });
  td.append(gapBox);

  const breakdown = marketPanel(job);
  if (breakdown) td.append(breakdown);

  td.append(paste);

  if (td.matches(".rd-body")) td.append(el("h3", { class: "rd-section", text: "The posting" }));
  const jd = el("div", { class: "jd", text: "Loading the description…" });
  td.append(jd);

  // One fetch per posting per load of the list. A redraw reuses it; a new
  // list (after a sweep or a rescore) has new row objects and fetches again.
  let cached = state.details[job.uid];
  if (!cached || cached.job !== job) {
    cached = state.details[job.uid] = {
      job, fetch: get("/api/job?uid=" + encodeURIComponent(job.uid)) };
    cached.fetch.catch(() => { delete state.details[job.uid]; });
  }
  cached.fetch.then((full) => {
    if (full.points && full.points.length) why.replaceWith(scoreParts(full));
    if (full.gaps) gapBox.replaceWith(gapPanel(full.gaps));
    if (full.blocks && full.blocks.length) jd.replaceWith(jdBlocks(full.blocks));
    else if (webUrl(job.url) && !cached.tried) readPosting(job, cached, jd);
    else {
      jd.classList.add("raw");
      jd.textContent = "This posting arrived without a description. Open it "
        + "above, copy the text, and paste it in with the button."
        + (cached.error ? " " + cached.error : "");
    }
    (full.guards || []).forEach((g) => {
      const line = el("div", { class: "guard", text: g.message });
      line.dataset.level = g.level;
      guards.append(line);
    });
  }).catch((err) => { jd.textContent = err.message; });
}

// How the number was reached, one line per part, in the order the scorer
// adds them. The caps, the squeeze into 90-100 and the evergreen penalty are
// lines too, so the column sums to the score in the table.
function scoreParts(job) {
  const box = el("div", { class: "parts" });
  box.append(el("div", { class: "parts-head" },
    el("b", {}, "How this scored"),
    el("span", { class: "note", text: "points per part, out of the most it can add" })));
  let sum = 0;
  job.points.forEach(([label, pts, why]) => {
    sum += pts;
    const max = state.partMax[label];
    const sign = pts > 0 ? "up" : pts < 0 ? "down" : "flat";
    box.append(el("div", { class: "part" },
      el("span", { class: "part-name", text: label }),
      el("span", { class: "part-pts " + sign,
        text: (pts > 0 ? "+" : "") + pts + (max && pts >= 0 ? ` / ${max}` : "") }),
      el("span", { class: "part-why", text: why.join("; ") })));
  });
  box.append(el("div", { class: "part total" },
    el("span", { class: "part-name", text: "Score" }),
    el("span", { class: "part-pts", text: String(job.score) }),
    el("span", { class: "part-why",
      text: `tier ${job.tier}` + (sum !== job.score ? ` (the parts add to ${sum})` : "") })));
  return box;
}

// What this posting's requirement lines name that the profile cannot back up.
// The server reads the lines (skillgap.py); this only lays them out. Required
// gaps come first, because a missing gate matters more than a missing wish.
function gapPanel(g) {
  const box = el("div", { class: "gaps" });
  const head = el("div", { class: "gaps-head" }, el("b", {}, "Missing qualifications and requirements"));
  box.append(head);
  if (g.error) {
    box.append(el("p", { class: "note", text: "Could not compare this posting with your profile: " + g.error }));
    return box;
  }
  if (!g.found) {
    box.append(el("p", { class: "note",
      text: "This posting has no requirements or qualifications section to read, "
        + "so there is nothing to compare line by line." }));
    return box;
  }
  head.append(el("span", { class: "note",
    text: g.asked ? `you have ${g.have} of the ${g.asked} skills it names` : "it names no skill the app knows" }));
  if (g.asked) {
    const meter = el("div", { class: "gap-meter", role: "img",
      "aria-label": `${g.have} of ${g.asked} skills covered` });
    meter.append(el("i", { style: `width:${Math.round(100 * g.have / g.asked)}%` }));
    box.append(meter);
  }

  const group = (section, title) => {
    const terms = g.missing.filter((t) => t.section === section);
    const flags = g.flags.filter((f) => f.section === section);
    const others = g.others.filter((o) => o.section === section);
    if (!terms.length && !flags.length && !others.length) return;
    const row = el("div", { class: "gap-group " + section },
      el("span", { class: "gap-title", text: title }));
    const chips = el("div", { class: "gap-chips" });
    flags.forEach((f) => chips.append(el("span", { class: "gap-chip flag", text: f.label, title: f.note })));
    terms.forEach((t) => chips.append(el("button", { class: "gap-chip", type: "button",
      text: t.kind === "domain" ? capital(t.label) + " experience" : t.label, title: `${KIND_NAMES[t.kind] || t.kind}. See how often it is asked for in Growth.`,
      on: { click: () => openGrowth(t.id) } })));
    others.forEach((o) => chips.append(el("span", { class: "gap-chip other", text: o.word,
      title: "A name the vocabulary has no term for yet, and nowhere in your profile" })));
    row.append(chips);
    box.append(row);
  };
  group("required", "Required");
  group("preferred", "Preferred");
  if (!g.missing.length && !g.flags.length && !g.others.length) {
    box.append(el("p", { class: "gap-none",
      text: "Nothing missing. Every skill these lines name is in your profile." }));
  }

  // Line by line, for the reader who wants to see what each mark came from.
  const lines = el("details", { class: "gap-lines" },
    el("summary", { text: `Line by line (${g.lines.length})` }));
  g.lines.forEach((l) => {
    const marks = el("span", { class: "gap-marks" });
    l.flags.forEach((f) => marks.append(el("span", { class: "gm miss", text: f.label })));
    l.missing.forEach((t) => marks.append(el("span", { class: "gm miss", text: labelOf(g, t) })));
    l.others.filter((o) => !o.have).forEach((o) => marks.append(el("span", { class: "gm other", text: o.word })));
    l.have.forEach((t) => marks.append(el("span", { class: "gm have", text: labelOf(g, t) })));
    const read = l.have.length || l.missing.length || l.flags.length || l.others.length;
    if (!read) marks.append(el("span", { class: l.checked ? "gm have" : "gm none",
      text: l.checked ? "you meet this" : "no skill named" }));
    lines.append(el("div", { class: "gap-line " + l.section },
      el("span", { class: "gap-sec", text: l.section === "required" ? "Req" : "Pref" }),
      el("span", { class: "gap-text", text: l.text }), marks));
  });
  box.append(lines);
  return box;
}

const KIND_NAMES = { tool: "A tool", method: "A method", domain: "Industry experience",
                     cert: "A certification", soft: "A soft skill" };

function labelOf(g, id) {
  return (g.labels && g.labels[id]) || id;
}

// A row the radar never got a body for. One read of the posting page, once
// per load of the list; on success the row takes the new score and pay.
function readPosting(job, cached, jd) {
  cached.tried = true;
  jd.textContent = "Reading the posting…";
  post("/api/job/read", { uid: job.uid }).then((full) => {
    ["score", "tier", "reasons", "flags", "points", "salary_min", "salary_max",
     "jd_chars"].forEach((k) => { if (k in full) job[k] = full[k]; });
    const market = state.market[job.uid];
    if (market) market.salary_estimate = full.salary_estimate || null;
    cached.fetch = Promise.resolve(full);
    draw();
  }).catch((err) => {
    cached.error = err.message;
    draw();
  });
}

// Why the arrow points where it points. Four measures, each one a place in
// the peer group rather than a raw number, because $95k means one thing for a
// support role in Richmond and another for an engineer in San Francisco.
function marketPanel(job) {
  const m = marketOf(job);
  const estimate = estimateOf(job);
  if (!m && !estimate) return null;

  const box = el("div", { class: "market" });
  if (m) {
    const head = el("div", { class: "market-head" });
    head.append(el("span", { class: "mktv " + m.direction,
      text: (m.direction === "up" ? "▲" : m.direction === "down" ? "▼" : "•")
            + (m.direction === "flat" ? " even" : Math.abs(m.delta) + " pts") }));
    head.append(el("span", { class: "note",
      text: `against ${m.peers} postings in ${m.group}`
            + (m.confident ? "" : ", a small group to judge by") }));
    box.append(head);

    m.factors.forEach((f) => {
      const line = el("div", { class: "factor" });
      line.append(el("span", { class: "factor-name", text: f.label }));
      const track = el("div", { class: "factor-bar" });
      track.append(el("i", { style: `width:${Math.max(2, f.percentile)}%` }));
      line.append(track);
      line.append(el("span", { class: "factor-pct", text: ordinal(f.percentile) }));
      line.append(el("span", { class: "factor-why", text: f.detail }));
      line.append(el("span", { class: "factor-share", text: f.share + "% of the arrow" }));
      box.append(line);
    });
  }
  if (estimate) {
    box.append(el("div", { class: "market-est" },
      el("strong", { text: money(estimate) + " estimated" }),
      el("span", { class: "note", text: " " + estimate.basis })));
  }
  return box;
}

function buildPacket(job, jd) {
  startRun({ kind: "packet", uid: job.uid, jd: jd || "" },
    `Building a packet for ${job.company}. Nothing is submitted.`);
}

$("#new-posting").addEventListener("click", () => {
  $("#posting").hidden = !$("#posting").hidden;
});
$("#posting-cancel").addEventListener("click", () => { $("#posting").hidden = true; });
$("#posting-build").addEventListener("click", async () => {
  const form = $("#posting");
  const body = { kind: "packet" };
  ["company", "title", "url", "jd"].forEach((k) => {
    body[k] = form.elements[k].value.trim();
  });
  if (!body.company || !body.title) {
    $("#posting-status").textContent =
      "A company and a role title, at least. They name the folder.";
    return;
  }
  if (!body.jd && !body.url) {
    $("#posting-status").textContent =
      "Either a link to read or the description pasted in.";
    return;
  }
  $("#posting-status").textContent = "";
  const run = await startRun(body,
    `Building a packet for ${body.company}. Nothing is submitted.`);
  if (run) {
    form.reset();
    form.hidden = true;
  }
});

async function openPacket(appId) {
  try {
    const data = await get("/api/packet?id=" + encodeURIComponent(appId));
    await post("/api/open", { path: data.folder });
  } catch (err) { banner(err.message, true); }
}

async function markApplied(appId) {
  try {
    await post("/api/application/status", { id: appId, status: "applied" });
    banner("Marked applied. A follow-up date is now on the Applied tab.");
    loadJobs();
    loadApplications();
  } catch (err) { banner(err.message, true); }
}

$("#rows").addEventListener("click", (e) => {
  const row = e.target.closest("tr.row");
  if (!row) return;
  // The star is a button inside a row that opens on click. Starring a
  // posting should not also expand it.
  if (e.target.closest(".starbtn")) {
    const job = state.jobs.find((j) => j.uid === row.dataset.uid);
    if (job) toggleStar(job);
    return;
  }
  state.open = state.open === row.dataset.uid ? null : row.dataset.uid;
  draw();
});

/* The keys from Command. Only on the Jobs tab and never while typing, so a
   J in the search box is still a J. Enter does nothing: building a packet
   starts a run, and that should take a click. */
document.addEventListener("keydown", (e) => {
  if ($("#view-jobs").hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) {
    if (e.key === "Escape" && e.target.id === "search") e.target.blur();
    return;
  }
  const rows = state.rows;
  const at = rows.findIndex((j) => j.uid === state.open);
  const job = at >= 0 ? rows[at] : null;
  const move = (d) => {
    if (!rows.length) return;
    const next = at < 0 ? (d > 0 ? 0 : rows.length - 1)
                        : Math.max(0, Math.min(rows.length - 1, at + d));
    state.open = rows[next].uid;
    draw();
    const tr = $(`#rows tr.row[data-uid="${CSS.escape(state.open)}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  };
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (key === "ArrowDown" || key === "j") move(1);
  else if (key === "ArrowUp" || key === "k") move(-1);
  else if (key === "Escape" && job) { state.open = null; draw(); }
  else if (key === "s" && job) toggleStar(job);
  else if (key === "o" && job && webUrl(job.url)) window.open(job.url, "_blank", "noopener");
  else if (key === "/") $("#search").focus();
  else return;
  e.preventDefault();
});

function sortable(tableSel, onSort) {
  $$(tableSel + " th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      $$(tableSel + " th").forEach((h) => h.removeAttribute("data-dir"));
      const dir = onSort(th.dataset.key);
      th.dataset.dir = dir;
    });
  });
}

// A second click on the same column flips it. A first click picks the
// direction a person means by that column: newest for Posted, highest for
// everything numeric, A to Z for text.
const FIRST_DIR = { age_days: "asc", title: "asc", company: "asc",
                    location: "asc", source: "asc" };

sortable(".grid#jobs", (key) => {
  if (state.sort === key) state.dir = state.dir === "desc" ? "asc" : "desc";
  else state.dir = FIRST_DIR[key] || "desc";
  state.sort = key;
  const named = Object.keys(ORDERS).find((o) =>
    ORDERS[o][0] === state.sort && ORDERS[o][1] === state.dir);
  $("#order").value = named || "column";
  draw();
  return state.dir;
});

$("#order").addEventListener("change", (ev) => {
  if (ev.target.value === "column") return;
  setOrder(ev.target.value);
  draw();
});

$("#reset-filters").addEventListener("click", () => {
  $("#search").value = "";
  applyFilters(prefs);
  leftView();
  draw();
});

/* -- saved views and the More menu -- */

const VIEW_BOXES = { hide_applied: "#hide-applied", hide_prepared: "#hide-prepared",
                     remote_only: "#remote-only", starred_only: "#starred-only",
                     has_pay: "#has-pay", market_up: "#market-up" };

// The filters on screen now, in the shape Settings stores a view in.
function currentFilters() {
  const clamp = (n) => Math.max(0, Math.min(100, Math.round(n)));
  const [lo, hi] = scoreRange();
  const f = { search: $("#search").value.trim().slice(0, 200),
              score_min: clamp(lo), score_max: clamp(hi),
              tier: state.tier, posted_within: $("#posted-within").value,
              source: $("#source").value.slice(0, 60) };
  Object.entries(VIEW_BOXES).forEach(([k, sel]) => { f[k] = $(sel).checked; });
  if (ORDERS[$("#order").value]) f.jobs_sort = $("#order").value;
  return f;
}

/* -- the chips over the list -- */

// The tier chips are a filter of their own. The yes/no chips are the boxes
// in the More menu, pressed from outside it, so the two always agree.
function setTier(tier) {
  state.tier = ["A", "B", "C", "D"].includes(tier) ? tier : "";
  drawChips();
}

function drawChips() {
  $$("#filter-chips .chip[data-tier]").forEach((c) =>
    c.setAttribute("aria-pressed", String(c.dataset.tier === state.tier)));
  $$("#filter-chips .chip[data-box]").forEach((c) =>
    c.setAttribute("aria-pressed", String($("#" + c.dataset.box).checked)));
}

$("#filter-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  if (chip.dataset.box !== undefined) {
    const box = $("#" + chip.dataset.box);
    box.checked = !box.checked;
    box.dispatchEvent(new Event("input"));
    return;
  }
  setTier(chip.dataset.tier);
  leftView();
  draw();
});

// The Source menu lists what the postings on hand came from, busiest
// first, and keeps a pick that no posting has any more rather than
// silently dropping it.
function drawSources() {
  const select = $("#source");
  const keep = select.value;
  const counts = {};
  state.jobs.forEach((j) => { if (j.source && j.tier !== "F") counts[j.source] = (counts[j.source] || 0) + 1; });
  const names = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  if (keep && !names.includes(keep)) names.push(keep);
  select.textContent = "";
  select.append(el("option", { value: "", text: "all" }));
  names.forEach((n) => select.append(el("option", { value: n,
    text: counts[n] ? `${n} (${counts[n]})` : n })));
  select.value = keep;
}

function setSource(source) {
  const select = $("#source");
  if (source && !Array.from(select.options).some((o) => o.value === source)) {
    select.append(el("option", { value: source, text: source }));
  }
  select.value = source || "";
}

/* -- saved views in the sidebar -- */

// Each view with the number of postings it would show right now, so the
// menu says which one has something new in it before you open it.
function drawSideViews() {
  const box = $("#side-views");
  const views = prefs.views || [];
  $("#side-views-box").hidden = !views.length;
  box.textContent = "";
  const picked = $("#view").value;
  views.forEach((v, i) => {
    const f = Object.assign({}, prefs, v.filters, { search: v.filters.search || "" });
    const n = state.jobs.filter((j) => matches(j, f)).length;
    const b = el("button", { class: "side-view", type: "button", title: v.name,
      "aria-current": String(picked === String(i)),
      on: { click: () => {
        const select = $("#view");
        select.value = String(i);
        select.dispatchEvent(new Event("change"));
        show("jobs");
      } } },
      el("span", { class: "side-dot", "aria-hidden": "true" }),
      el("span", { class: "tab-label", text: v.name }),
      el("span", { class: "ct", text: state.jobs.length ? String(n) : "" }));
    box.append(b);
  });
}

function drawViews(selected) {
  const select = $("#view");
  const views = prefs.views || [];
  select.textContent = "";
  select.append(el("option", { value: "", text: views.length ? "none" : "none saved" }));
  views.forEach((v, i) => select.append(el("option", { value: String(i), text: v.name })));
  select.value = selected === undefined || selected < 0 ? "" : String(selected);
  $("#delete-view").hidden = select.value === "";
  drawSideViews();
}

// Any filter change after picking a view means the view is no longer what
// is on screen, so the menu stops claiming it is.
function leftView() {
  $("#view").value = "";
  $("#delete-view").hidden = true;
  $$("#side-views .side-view").forEach((b) => b.setAttribute("aria-current", "false"));
}

// How many of the filters tucked inside More are narrowing the list, so a
// closed menu still says something is on.
function drawMoreCount() {
  const on = Object.values(VIEW_BOXES).filter((sel) => $(sel).checked).length;
  $("#more-count").textContent = on ? String(on) : "";
  $("#more-count").title = on ? plural(on, "filter") + " on in here" : "";
}

async function saveViews(views, selected) {
  const data = await post("/api/settings", { settings: { views } });
  prefs = data.settings;
  drawViews(selected);
}

$("#view").addEventListener("change", (ev) => {
  const view = (prefs.views || [])[Number(ev.target.value)];
  $("#delete-view").hidden = !view;
  if (!view) return;
  $("#search").value = view.filters.search || "";
  applyFilters(Object.assign({}, prefs, view.filters));
  $$("#side-views .side-view").forEach((b, i) =>
    b.setAttribute("aria-current", String(String(i) === ev.target.value)));
  draw();
});

$("#save-view").addEventListener("click", async () => {
  const name = $("#view-name").value.trim();
  if (!name) { $("#view-name").focus(); return; }
  const views = (prefs.views || []).slice();
  let at = views.findIndex((v) => v.name.toLowerCase() === name.toLowerCase());
  if (at < 0 && views.length >= 12) {
    banner("Twelve views is the most JobDesk keeps. Delete one first.", true);
    return;
  }
  const view = { name, filters: currentFilters() };
  if (at < 0) { views.push(view); at = views.length - 1; } else views[at] = view;
  try {
    await saveViews(views, at);
    $("#view-name").value = "";
  } catch (err) { banner(err.message, true); }
});

$("#delete-view").addEventListener("click", async () => {
  const at = Number($("#view").value);
  const views = (prefs.views || []).filter((_, i) => i !== at);
  try { await saveViews(views); } catch (err) { banner(err.message, true); }
});

["#search", "#score-min", "#score-max", "#tier", "#order", "#posted-within",
 "#source", ...Object.values(VIEW_BOXES)].forEach((sel) =>
  $(sel).addEventListener("input", leftView));

// A click anywhere else closes the menu, the way a menu is expected to.
document.addEventListener("click", (e) => {
  const more = $("#jobs-more");
  if (more.open && !more.contains(e.target)) more.open = false;
});

$("#search").addEventListener("input", drawSoon);
["#score-min", "#score-max"].forEach((sel) =>
  $(sel).addEventListener("input", drawSoon));
["#posted-within", "#source", ...Object.values(VIEW_BOXES)]
  .forEach((sel) => $(sel).addEventListener("input", draw));

// The preset writes the two boxes and then gets out of the way. It is a
// shortcut for typing the numbers, not a third filter that can disagree with
// them -- so it resets itself, and the boxes stay the only thing draw() reads.
$("#tier").addEventListener("change", (ev) => {
  const value = ev.target.value;
  if (!value) return;
  const [lo, hi] = value.split(",");
  $("#score-min").value = lo;
  $("#score-max").value = hi;
  ev.target.value = "";
  draw();
});

$("#rescore").addEventListener("click", async () => {
  const button = $("#rescore");
  button.disabled = true;
  try {
    const data = await post("/api/rescore");
    state.jobs = data.jobs;
    stamp(data);
    draw();
    banner(`${plural(data.moved, "posting")} changed score. ${data.note || ""}`.trim());
  } catch (err) {
    banner(err.message, true);
  } finally {
    button.disabled = false;
  }
});

/* =========================================================== applied tab */

const apps = { rows: [], statuses: [], sort: "applied_on", dir: "desc",
               open: null, due: [] };

async function loadApplications() {
  try {
    const data = await get("/api/applications");
    apps.rows = data.applications;
    apps.statuses = data.statuses;
    apps.due = data.due || [];
    drawAppCount();
    drawFunnel(data);
    drawReport(data);
    if ($("#app-status").options.length <= 1) {
      apps.statuses.forEach((s) =>
        $("#app-status").append(el("option", { value: s, text: s })));
    }
    drawApps();
  } catch (err) { banner(err.message, true); }
}

// The tab's count is every application, the same number the table's own
// count ends on. It used to leave out the dead ones, which made it a number
// that matched nothing on the screen.
function drawAppCount() {
  $("#ct-applied").textContent = apps.rows.length ? String(apps.rows.length) : "";
}

function drawFunnel(data) {
  const box = $("#funnel");
  box.textContent = "";
  const sent = apps.rows.filter((r) => r.applied_on).length;
  const tiles = [["prepared", data.funnel.prepared || 0],
                 ["applied", sent]];
  ["screening", "interview", "offer", "rejected", "ghosted"].forEach((s) => {
    tiles.push([s, data.funnel[s] || 0]);
  });
  // Same four colors as the status dropdowns, so a tile and the rows it
  // counts read as the same thing. A tile at zero stays gray: the color is
  // there to pull your eye to a number that exists.
  tiles.forEach(([label, n]) => {
    box.append(el("div", { class: `stat ${statusKind(label)}` + (n ? "" : " zero") },
      el("b", { text: String(n) }), el("span", { text: label })));
  });

  const overdue = apps.due.length;
  $("#due").hidden = !overdue;
  $("#due").textContent = overdue
    ? `${plural(overdue, "application")} overdue for a follow-up. `
      + `They are marked in the table below.`
    : "";
}

function drawReport(data) {
  const sources = Object.entries(data.by_source || {});
  const body = $("#by-source");
  body.textContent = "";
  sources.sort((a, b) => b[1].sent - a[1].sent).forEach(([name, n]) => {
    const rate = n.sent ? Math.round((n.responses / n.sent) * 100) : 0;
    body.append(el("tr", {},
      el("td", { text: name }),
      el("td", { class: "num", text: String(n.sent) }),
      el("td", { class: "num", text: String(n.responses) }),
      el("td", { class: "num", text: `${rate}%` })));
  });
  $("#by-source-empty").hidden = sources.length > 0;
  $("#by-source-empty").textContent =
    "Nothing sent yet, so there is nothing to compare. This fills in as you "
    + "mark applications applied.";

  const stages = Object.entries(data.rejections || {});
  const rej = $("#rejections");
  rej.textContent = "";
  stages.sort((a, b) => b[1] - a[1]).forEach(([stage, n]) => {
    rej.append(el("tr", {},
      el("td", { text: stage }),
      el("td", { class: "num", text: String(n) })));
  });
  $("#rejections-empty").hidden = stages.length > 0;
  $("#rejections-empty").textContent = "No rejections logged. Long may it last.";
}

function visibleApps() {
  const q = $("#app-search").value.trim().toLowerCase();
  const status = $("#app-status").value;
  const sentOnly = $("#sent-only").checked;
  const rows = apps.rows.filter((r) => {
    if (status && r.status !== status) return false;
    if (sentOnly && !r.applied_on) return false;
    if (!q) return true;
    return `${r.company} ${r.role}`.toLowerCase().includes(q);
  });
  // Prepared rows first, whatever the sort: they are the ones still waiting on
  // you to send them. The column sort orders each group, and Array.sort is
  // stable, so it survives the second pass.
  return sortRows(rows, apps.sort, apps.dir).sort((a, b) =>
    (b.status === "prepared") - (a.status === "prepared"));
}

// Four states worth telling apart by color, which is fewer than there are
// statuses. "screening" and "interview" differ in how far along you are, not
// in what you should do about them, and a nine-color legend is not a legend.
const DEAD = ["rejected", "ghosted", "withdrawn"];

function statusKind(status) {
  if (status === "prepared") return "prepared";
  if (status === "offer") return "offer";
  return DEAD.includes(status) ? "dead" : "live";
}

function drawApps() {
  const rows = visibleApps();
  const body = $("#app-rows");
  body.textContent = "";

  pageRows(body, rows, (row) => {
    const tr = el("tr", { class: "row" + (row.follow_up_overdue ? " overdue" : "") });
    tr.dataset.id = row.id;

    const company = el("td", { class: "lead" }, row.company);

    // The color used to be a badge in this cell, next to the company name,
    // which meant the row said "rejected" twice: once in a red bubble and
    // once in the dropdown right beside it. The dropdown is the thing you
    // actually change, so it is the thing that carries the color now -- and
    // every option in the open menu carries its own, so the list reads as a
    // legend before you pick from it.
    const status = el("td", {},
      el("select", {
        class: "status " + statusKind(row.status),
        on: {
          click: (e) => e.stopPropagation(),
          change: (e) => changeStatus(row.id, e.target.value),
        },
      }, apps.statuses.map((s) =>
        el("option", { value: s, text: s, class: statusKind(s),
                       selected: s === row.status }))));

    tr.append(company, cell(row.role), status,
      cell(when(row.applied_on), "when"),
      cell(row.days_since === null ? "—" : row.days_since + "d", "num"),
      cell(row.follow_up_due ? when(row.follow_up_due) : "—", "when"),
      cell(row.source || "—"),
      cell(row.score === null || row.score === undefined ? "—" : String(row.score), "num"));
    body.append(tr);

    if (apps.open === row.id) body.append(appDetail(row));
  }, { span: $$("#apps thead th").length, until: rows.findIndex((r) => r.id === apps.open),
       after: () => labelCells("#apps") });

  $("#app-counts").textContent =
    `${rows.length} of ${plural(apps.rows.length, "application")}`;
  $("#apps-empty").hidden = rows.length > 0;
  $("#apps-empty").textContent = apps.rows.length
    ? "Nothing matches those filters."
    : "No applications yet. Build a packet from the Jobs tab.";
}

// The packet, in the page rather than in a folder.
//
// "Open the folder" was the whole answer to "can I see what got built", and it
// is the one answer that is worthless on a phone -- the file manager it opens
// is on a machine in another room. So every text file in the packet is read
// here, and the resume and the letter come down as downloads.
//
// APPLY.md is open and the rest are shut. It is the only file that tells you
// what to do next; TAILORING.md and ats_report.md are for the times you want
// to know why the resume says what it says, which is not most times.
const PACKET_TITLES = {
  "APPLY.md": "What to do, in order",
  "ANSWERS.md": "The questions the form will ask",
  "COVER_LETTER.md": "The cover letter",
  "COVER_LETTER.txt": "The cover letter, as plain text to paste",
  "TAILORING.md": "What was changed for this posting, and why",
  "ats_report.md": "How an applicant tracking system reads it",
  "jd.txt": "The posting itself, as it was read",
};

/* Markdown, the small part of it these files actually use.

   A packet file arrived here as one `<p>`, so every newline collapsed and
   APPLY.md -- a list of things to do in order, with a table at the bottom --
   read as one gray paragraph you had to re-find your place in twice. The files
   are written as markdown by the packet builder. This reads them back as
   markdown.

   Headings, bullets, checkboxes, tables, fenced code, bold, italic, inline
   code, links. No images, no nesting, no HTML passthrough. A packet is
   generated by this project, and anything fancier than the list above means
   the generator changed and this should be looked at rather than guessed at.

   Still no innerHTML. The posting's own text ends up in jd.txt and in the
   cover letter, and a renderer that trusts markup is a renderer that runs
   whatever an employer put on their careers page. */

const MD_INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g;

function mdInline(text) {
  return String(text).split(MD_INLINE).filter(Boolean).map((part) => {
    if (part.length > 1 && part.startsWith("`") && part.endsWith("`")) {
      return el("code", { text: part.slice(1, -1) });
    }
    if (part.startsWith("**")) return el("strong", {}, mdInline(part.slice(2, -2)));
    if (part.startsWith("*")) return el("em", {}, mdInline(part.slice(1, -1)));
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      // Only the two schemes a job posting has any business using. Anything
      // else, javascript: and data: included, keeps its text and loses its link.
      return /^(https?:|mailto:)/i.test(link[2])
        ? el("a", { href: link[2], target: "_blank", rel: "noopener noreferrer",
                    text: link[1] })
        : document.createTextNode(link[1]);
    }
    return document.createTextNode(part);
  });
}

const mdCells = (line) =>
  line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

function markdown(text) {
  const out = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    const head = /^(#{1,6})\s+(.*)$/.exec(line);
    if (head) {
      // One level down from where the file puts them. The panel's own "The
      // packet" is the h4 above all of this, and a packet's `#` title at the
      // same weight makes the page look like it starts over halfway down.
      out.append(el("h" + Math.min(head[1].length + 3, 6), {}, mdInline(head[2])));
      i += 1; continue;
    }

    if (/^\s*```/.test(line)) {
      const buf = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i += 1; }
      i += 1;  // the closing fence, or the end of the file if it is missing
      out.append(el("pre", { text: buf.join("\n") }));
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i]); i += 1;
      }
      // Row two is the ---|--- rule. It carries the alignment, which nothing
      // in these files ever sets, so it is dropped rather than read.
      const rest = rows.slice(1).filter((r) => !/^[\s|:-]+$/.test(r));
      out.append(el("table", { class: "md-table" },
        el("thead", {}, el("tr", {},
          mdCells(rows[0]).map((c) => el("th", {}, mdInline(c))))),
        el("tbody", {}, rest.map((r) =>
          el("tr", {}, mdCells(r).map((c) => el("td", {}, mdInline(c))))))));
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quoted = [];
      while (i < lines.length) {
        if (/^\s*>\s?/.test(lines[i])) {
          quoted.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; continue;
        }
        // A blank line between two quoted blocks is a paragraph break inside
        // one quote, not the end of it. ANSWERS.md puts a blank line between
        // its warning and the list the warning is about, and two boxes with a
        // hairline gap between them read as two separate warnings.
        if (!lines[i].trim() && /^\s*>/.test(lines[i + 1] || "")) {
          quoted.push(""); i += 1; continue;
        }
        break;
      }
      // The marker comes off and what is left goes through this same function.
      // The warning block at the top of ANSWERS.md is a quoted bullet list, and
      // a blockquote that cannot hold a list would print its own dashes.
      out.append(el("blockquote", { class: "md-quote" }, markdown(quoted.join("\n"))));
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, "");
        i += 1;
        // A wrapped item continues on an indented line. It is joined back into
        // one item, because a hard wrap in the source is not a new bullet.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) &&
               !/^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
          item += " " + lines[i].trim(); i += 1;
        }
        const box = /^\[([ xX])\]\s*/.exec(item);
        items.push(box
          ? el("li", { class: "md-task" },
              el("span", { class: "md-box", text: box[1] === " " ? "\u2610" : "\u2611" }),
              el("span", {}, mdInline(item.slice(box[0].length))))
          : el("li", {}, mdInline(item)));
      }
      out.append(el(ordered ? "ol" : "ul", { class: "md-list" }, items));
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() &&
           !/^\s*(#{1,6}\s|```|\||>)/.test(lines[i]) &&
           !/^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
      para.push(lines[i].trim()); i += 1;
    }
    // A newline is a line break here, which is not what markdown normally says.
    // Nothing writing these files hard-wraps: one line is one paragraph of the
    // cover letter. Folding them together the way a strict reader would is what
    // turned a five-paragraph letter into one eleven-line block.
    out.append(el("p", {}, para.flatMap((ln, n) =>
      n ? [el("br"), ...mdInline(ln)] : mdInline(ln))));
  }
  return out;
}

function packetBox(appId, data) {
  const box = el("div", { class: "packet" });
  box.append(el("h4", { text: "The packet" }));
  box.append(el("p", { class: "note", text: data.folder }));

  const downloads = el("div", { class: "downloads" });
  data.files.filter((f) => !f.readable).forEach((f) => {
    downloads.append(el("a", {
      class: "chip",
      href: `/api/packet/file?id=${encodeURIComponent(appId)}&name=${encodeURIComponent(f.name)}`,
      text: `${f.name} · ${Math.round(f.size / 1024)} KB`,
      on: { click: (e) => e.stopPropagation() },
    }));
  });
  if (downloads.children.length) {
    box.append(el("p", { class: "note", text: "Attach these to the application. Nothing here submits anything." }));
    box.append(downloads);
  }

  const names = Object.keys(data.texts).sort((a, b) =>
    (a !== "APPLY.md") - (b !== "APPLY.md") || a.localeCompare(b));
  names.forEach((name) => {
    const open = name === "APPLY.md";
    // .txt files are in a packet precisely because something wanted them
    // unformatted: the paste-into-a-box resume, the posting as it was read.
    // Rendering those would undo the only thing they are for.
    const body = name.endsWith(".md")
      ? el("div", { class: "md" }, markdown(data.texts[name]))
      : el("p", { class: "raw", text: data.texts[name] });
    body.hidden = !open;
    // The caret and the label are separate nodes so the title is never rebuilt.
    // One triangle, always the same character: CSS turns it when the section
    // opens, so the row moves rather than flickering a different glyph in.
    const caret = el("span", { class: "caret", text: "▶" });
    const head = el("button", { class: "disclose" + (open ? " open" : ""),
      "aria-expanded": open ? "true" : "false",
      on: { click: (e) => {
        e.stopPropagation();
        body.hidden = !body.hidden;
        head.classList.toggle("open", !body.hidden);
        head.setAttribute("aria-expanded", body.hidden ? "false" : "true");
      } },
    }, caret, el("span", { class: "label", text: PACKET_TITLES[name] || name }));
    box.append(head, body);
  });
  return box;
}

function appDetail(row) {
  const td = el("td", { colSpan: 8 });
  const actions = el("div", { class: "actions" });

  if (webUrl(row.url)) {
    actions.append(el("a", { href: row.url, target: "_blank",
      rel: "noopener noreferrer", text: "Open the posting ↗" }));
  }
  actions.append(el("button", { class: "ghost desktop-only",
    text: "Open the folder",
    on: { click: (e) => { e.stopPropagation(); openPacket(row.id); } } }));
  if (!row.applied_on) {
    actions.append(el("button", { text: "I applied to this",
      on: { click: (e) => { e.stopPropagation(); markApplied(row.id); } } }));
  }
  if (row.status !== "rejected") {
    // The stage a rejection landed at is the one number that says whether the
    // resume is the problem or something after it is, so it is asked for here
    // rather than filled in as "unknown".
    const stage = el("select", { on: { click: (e) => e.stopPropagation() } },
      ["no answer at all", "screening", "interview", "offer"].map((s) =>
        el("option", { value: s === "no answer at all" ? "" : s, text: s })));
    actions.append(stage, el("button", { class: "ghost", text: "They said no",
      on: { click: (e) => { e.stopPropagation(); reject(row, stage.value); } } }));
  }
  // A posting can close between the packet being built and you getting to the
  // form, and then there is a row saying you applied to something you never
  // got to apply to. Every number on this tab is wrong by one until it goes.
  //
  // Two clicks rather than a confirm dialog, because the second click is in
  // the same place as the first and a dialog on a phone is a modal you have to
  // aim at. The button says what it will not do, since "remove" next to a
  // folder full of tailored documents reads like it takes the folder too.
  const forget = el("button", { class: "ghost danger", text: "Remove this row",
    on: { click: (e) => {
      e.stopPropagation();
      if (forget.dataset.armed) return forgetApp(row);
      forget.dataset.armed = "1";
      forget.textContent = "Really remove it? The packet folder stays.";
    } } });
  actions.append(forget);
  td.append(actions);

  if (row.notes) td.append(el("p", { class: "note", text: row.notes }));
  if (row.rejected_on) {
    td.append(el("p", { class: "note",
      text: `Rejected ${when(row.rejected_on)}`
          + (row.rejected_stage ? ` at ${row.rejected_stage}` : "")
          + (row.rejected_reason ? `: ${row.rejected_reason}` : "") }));
  }

  const history = el("div", { class: "jd" });
  (row.history || []).forEach((h) => {
    const line = typeof h === "string" ? h : `${h.at || ""} ${h.what || ""}`.trim();
    history.append(el("p", { text: line }));
  });
  td.append(history);

  const files = el("div", { class: "jd", text: "Reading the packet…" });
  td.append(files);
  get("/api/packet?id=" + encodeURIComponent(row.id)).then((data) => {
    files.replaceWith(packetBox(row.id, data));
  }).catch((err) => { files.textContent = err.message; });

  const tr = el("tr", { class: "detail" });
  tr.append(td);
  return tr;
}

async function changeStatus(id, status) {
  try {
    await post("/api/application/status", { id, status });
    await loadApplications();
    loadJobs();
  } catch (err) { banner(err.message, true); }
}

async function forgetApp(row) {
  try {
    await post("/api/application/forget", { id: row.id });
    apps.open = null;
    await loadApplications();
    // The posting is hidden from the queue while a row claims it, so the queue
    // has to be reloaded or it stays missing from both tabs at once.
    loadJobs();
    banner(`Removed ${row.company} · ${row.role}. The packet folder is still `
         + `on disk.`);
  } catch (err) { banner(err.message, true); }
}

async function reject(row) {
  const stage = prompt(`How far did ${row.company} get before the no?\n`
    + `screening, interview, offer, or leave blank for "no answer at all".`, "screening");
  if (stage === null) return;
  try {
    await post("/api/application/status", { id: row.id, status: "rejected", stage });
    await loadApplications();
  } catch (err) { banner(err.message, true); }
}

$("#app-rows").addEventListener("click", (e) => {
  const row = e.target.closest("tr.row");
  if (!row) return;
  apps.open = apps.open === row.dataset.id ? null : row.dataset.id;
  drawApps();
});

sortable(".grid#apps", (key) => {
  apps.dir = apps.sort === key && apps.dir === "desc" ? "asc" : "desc";
  apps.sort = key;
  drawApps();
  return apps.dir;
});

["#app-search", "#app-status", "#sent-only"]
  .forEach((sel) => $(sel).addEventListener("input", drawApps));

$("#log-manual").addEventListener("click", () => {
  $("#manual").hidden = !$("#manual").hidden;
});
$("#manual-cancel").addEventListener("click", () => { $("#manual").hidden = true; });
$("#manual-save").addEventListener("click", async () => {
  const form = $("#manual");
  const body = {};
  ["company", "role", "url", "when", "source", "notes"].forEach((k) => {
    body[k] = form.elements[k].value.trim();
  });
  $("#manual-status").textContent = "Logging…";
  try {
    await post("/api/application/manual", body);
    form.reset();
    form.hidden = true;
    $("#manual-status").textContent = "";
    await loadApplications();
  } catch (err) { $("#manual-status").textContent = err.message; }
});

/* =========================================================== archive tab */

const archive = { loaded: false, offset: 0, limit: 200, matched: 0 };

async function searchArchive(offset) {
  const params = new URLSearchParams({
    q: $("#arc-q").value.trim(),
    min_score: $("#arc-score").value || "0",
    since: $("#arc-since").value,
    limit: String(archive.limit),
    offset: String(offset || 0),
  });
  try {
    const data = await get("/api/archive?" + params.toString());
    archive.loaded = true;
    archive.offset = data.offset;
    archive.matched = data.matched;
    drawArchive(data);
    if (!$("#arc-company-rows").children.length) loadCompanies();
  } catch (err) { banner(err.message, true); }
}

function drawArchive(data) {
  const s = data.summary || {};
  $("#archive-summary").textContent = s.total
    ? `${s.total.toLocaleString()} distinct postings from `
      + `${s.companies.toLocaleString()} companies, first seen ${when(s.since)}. `
      + `${s.scored.toLocaleString()} of them ever cleared the score floor. `
      + `Read ${(s.readings || 0).toLocaleString()} times between them; the last `
      + `sweep saw ${(s.last_read || 0).toLocaleString()} of these again and `
      + `found ${(s.last_new || 0).toLocaleString()} new.`
    : "Nothing archived yet.";
  const top = s.most_read;
  $("#archive-reads").textContent = top && top.times > 1
    ? `Each row is one posting, counted once however often it is seen. The `
      + `last sweep read ${(s.last_read || 0).toLocaleString()} listings and `
      + `${(s.last_new || 0).toLocaleString()} were new, because a posting `
      + `stays up for weeks: ${top.company}'s ${top.title} has been read `
      + `${top.times.toLocaleString()} times. The new count is what the radar `
      + `is actually finding.`
    : "";

  const body = $("#arc-rows");
  body.textContent = "";
  data.rows.forEach((row) => {
    const title = el("td", { class: "lead" },
      webUrl(row.url) ? el("a", { href: row.url, target: "_blank",
        rel: "noopener noreferrer", text: row.title || "(untitled)" })
              : (row.title || "(untitled)"));
    body.append(el("tr", {},
      cell(String(row.score), "num"), title, cell(row.company || "—"),
      cell(when(row.first_seen), "when"), cell(when(row.last_seen), "when"),
      cell(row.live_days === null ? "—" : row.live_days + "d", "num")));
  });
  labelCells("#arc");

  const from = data.matched ? archive.offset + 1 : 0;
  const to = Math.min(archive.offset + archive.limit, data.matched);
  $("#arc-counts").textContent =
    `${from.toLocaleString()}–${to.toLocaleString()} of `
    + `${data.matched.toLocaleString()} matching`;
  $("#arc-page").textContent = `page ${Math.floor(archive.offset / archive.limit) + 1}`;
  $("#arc-prev").disabled = archive.offset === 0;
  $("#arc-next").disabled = to >= data.matched;
}

async function loadCompanies() {
  try {
    const data = await get("/api/archive/companies?limit=60");
    const body = $("#arc-company-rows");
    body.textContent = "";
    data.companies.forEach((c) => {
      const name = el("td", {}, el("a", {
        href: "#", text: c.company,
        on: { click: (e) => { e.preventDefault(); $("#arc-q").value = c.company;
                              searchArchive(0); } },
      }));
      body.append(el("tr", {}, name, cell(String(c.scored), "num"),
        cell(String(c.postings), "num")));
    });
  } catch (err) { banner(err.message, true); }
}

$("#arc-go").addEventListener("click", () => searchArchive(0));
$("#arc-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); searchArchive(0); }
});
$("#arc-prev").addEventListener("click", () =>
  searchArchive(Math.max(0, archive.offset - archive.limit)));
$("#arc-next").addEventListener("click", () =>
  searchArchive(archive.offset + archive.limit));

/* ========================================================== criteria tab */
/*
 * Drawn from what /api/targeting sends: sections, each with a plain title, a
 * sentence or two of explanation, and fields that say what they are worth.
 * The wording lives in `criteria.py` next to the validation, so the page and
 * the server cannot disagree about what a box means.
 */

const UNITS = { money: ["$", "per year"], years: ["", "years"],
                days: ["", "days"], int: ["", ""] };
let criteriaFile = "";

/* A list as tags. The textarea is still the field, and Save still reads
   it; the tags are drawn from its lines and write back to them, so "Edit as
   text" can show it for a long paste and nothing is kept in two places. */
function tagEditor(area, kind) {
  const tags = el("div", { class: "tags" });
  const add = el("input", { type: "text", class: "tag-add", spellcheck: false,
    placeholder: kind === "weights" ? "Add a skill, like sql: 6" : "Add one, then Enter",
    "aria-label": "Add a line" });
  const lines = () => area.value.split("\n").map((t) => t.trim()).filter(Boolean);
  const write = (list) => {
    area.value = list.join("\n");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const take = () => {
    const text = add.value.trim();
    add.value = "";
    if (text) write([...lines(), text]);
  };
  const draw = () => {
    const focused = document.activeElement === add;
    tags.textContent = "";
    lines().forEach((line, i) => {
      let name = line, pts = null;
      const m = kind === "weights" && line.match(/^(.*?)\s*[:=]\s*(-?\d+)\s*$/);
      if (m) { name = m[1]; pts = m[2]; }
      tags.append(el("span", { class: "tag" + (pts !== null && Number(pts) < 0 ? " minus" : "") },
        el("span", { text: name }),
        pts !== null ? el("b", { class: "tag-pts", text: pts }) : null,
        el("button", { type: "button", class: "tag-x", text: "\u00d7",
          "aria-label": `Remove ${name}`,
          on: { click: () => { const list = lines(); list.splice(i, 1); write(list); } } })));
    });
    tags.append(add);
    if (focused) add.focus();
  };
  add.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); take(); add.focus(); }
    if (e.key === "Backspace" && !add.value && lines().length) write(lines().slice(0, -1));
  });
  add.addEventListener("blur", take);
  area.addEventListener("input", draw);
  const box = el("div", { class: "tag-editor" }, tags, area);
  box.append(el("button", { type: "button", class: "ghost small tag-raw", text: "Edit as text",
    on: { click: (e) => {
      const raw = box.classList.toggle("as-text");
      e.target.textContent = raw ? "Show as tags" : "Edit as text";
      if (raw) area.focus();
    } } }));
  draw();
  return box;
}

function criteriaInput(field) {
  const { key, kind, value } = field;
  if (kind === "list" || kind === "weights") {
    const lines = kind === "list"
      ? (value || [])
      : Object.entries(value || {}).map(([skill, pts]) => `${skill}: ${pts}`);
    return tagEditor(el("textarea", {
      name: key, value: lines.join("\n"),
      rows: Math.min(10, Math.max(3, lines.length)),
      placeholder: kind === "weights" ? "sql: 6\npython: 4" : "one per line",
      spellcheck: false,
    }), kind);
  }
  if (kind === "text") {
    return el("input", { type: "text", name: key, value: value || "" });
  }
  const [before, after] = UNITS[kind] || ["", ""];
  return el("span", { class: "unit" },
    before ? el("span", { text: before }) : null,
    el("input", { type: "number", name: key, min: 0, inputmode: "numeric",
                  step: kind === "money" ? 1000 : 1,
                  value: value === null || value === undefined ? "" : value }),
    after ? el("span", { text: after }) : null);
}

async function loadCriteria() {
  const form = $("#criteria");
  try {
    const data = await get("/api/targeting");
    criteriaFile = data.file;
    $("#criteria-file").textContent = data.file;
    form.textContent = "";
    const jump = $("#crit-jump");
    jump.textContent = "";
    setSaveBar("Everything here is saved.");
    data.sections.forEach((s) => {
      const box = el("fieldset", { class: "crit-section", id: `crit-${s.id}` },
        el("h3", { text: s.title }),
        s.intro ? el("p", { class: "note", text: s.intro }) : null);
      jump.append(el("button", { type: "button", class: "chip", text: s.title,
        on: { click: () => box.scrollIntoView({ behavior: "smooth", block: "start" }) } }));
      const grid = el("div", { class: "crit-fields" });
      s.fields.forEach((f) => {
        // A tag list is a div, not a label: a label hands any click on its
        // text to the first button inside it, which would delete a tag.
        const tagged = f.kind === "list" || f.kind === "weights";
        const field = el(tagged ? "div" : "label", { class: "crit-field" },
          el("span", { class: "label", text: f.label }),
          el("span", { class: "help", text: f.help }),
          criteriaInput(f));
        field.dataset.kind = f.kind;
        grid.append(field);
      });
      box.append(grid);
      if (s.outro) box.append(el("p", { class: "outro", text: s.outro }));
      // Read-only: the search follows the lists above, so there is nothing
      // separate to edit, only how often each title goes out.
      if (s.id === "titles" && data.search) {
        box.append(el("div", { class: "crit-search" },
          el("span", { class: "label", text: "What JobDesk searches for" }),
          ...data.search.text.map((line) => el("p", { text: line }))));
      }
      form.append(box);
    });
    $("#save-criteria").disabled = false;
  } catch (err) {
    form.textContent = "";
    form.append(el("p", { class: "note", text: err.message }));
    $("#save-criteria").disabled = true;
  }
}

// "sql: 6" -> {sql: 6}. A line with no number sends a blank, and the server
// fills in that group's default, so the page does not need to know it.
function parseWeights(text) {
  const out = {};
  text.split("\n").forEach((line) => {
    const m = line.trim().match(/^(.*?)(?:\s*[:=]\s*(-?\d+))?\s*$/);
    if (!m || !m[1].trim()) return;
    out[m[1].trim()] = m[2] === undefined ? "" : Number(m[2]);
  });
  return out;
}

/* -- documents that add to the profile -- */

// Each file is scanned on its own and gets its own card, so a slow image
// does not hold up the PDF dropped beside it. Nothing is kept until Keep.
const evDrop = $("#ev-drop");
evDrop.addEventListener("dragover", (e) => { e.preventDefault(); evDrop.classList.add("over"); });
evDrop.addEventListener("dragleave", () => evDrop.classList.remove("over"));
evDrop.addEventListener("drop", (e) => {
  e.preventDefault();
  evDrop.classList.remove("over");
  [...e.dataTransfer.files].forEach(scanDocument);
});
$("#ev-file").addEventListener("change", (e) => {
  [...e.target.files].forEach(scanDocument);
  e.target.value = "";
});

async function scanDocument(file) {
  const card = el("div", { class: "ev-card" },
    el("div", { class: "ev-head" }, el("b", { text: file.name }),
      el("span", { class: "note", text: "Reading…" })));
  $("#ev-results").prepend(card);
  const status = card.querySelector(".note");
  if (file.size > 6 * 1024 * 1024) {
    status.textContent = "Over 6 MB. Drop in a smaller export of it.";
    card.classList.add("failed");
    return;
  }
  let result;
  try {
    result = await post("/api/evidence/scan", { filename: file.name, content: await base64(file) });
  } catch (err) {
    status.textContent = err.message;
    card.classList.add("failed");
    return;
  }
  const fresh = result.items.filter((i) => !i.have);
  const had = result.items.filter((i) => i.have);
  status.textContent = `read as ${result.read_as}, ${plural(result.chars, "character")}. `
    + (fresh.length ? `${plural(fresh.length, "skill")} your profile does not show yet.`
                    : "Nothing new: your profile already shows every skill in it.");
  const picks = [];
  const pick = (item, checked, kind) => {
    const box = el("input", { type: "checkbox" });
    box.checked = checked;
    picks.push({ box, item, kind });
    return el("label", { class: "ev-item" + (kind === "skill" && item.kind === "name" ? " name" : "") },
      box,
      el("span", { class: "ev-label", text: kind === "cert" ? item : item.label }),
      kind === "skill" ? el("span", { class: "ev-kind",
        text: item.kind === "name" ? "a name the app does not know" : (KIND_NAMES[item.kind] || item.kind) }) : null,
      kind === "skill" && item.snippet ? el("span", { class: "ev-line", text: "“" + item.snippet + "”" }) : null);
  };
  if (fresh.length) {
    const list = el("div", { class: "ev-list" });
    // Known skills start ticked; names the vocabulary does not know start
    // unticked, since a capitalized word is only sometimes a skill.
    fresh.forEach((i) => list.append(pick(i, i.kind !== "name", "skill")));
    card.append(list);
  }
  if (result.certificates.length) {
    const list = el("div", { class: "ev-list" },
      el("span", { class: "ev-sub", text: "Lines that look like a certificate or license" }));
    result.certificates.forEach((c) => list.append(pick(c, false, "cert")));
    card.append(list);
  }
  if (had.length) {
    card.append(el("details", { class: "ev-had" },
      el("summary", { text: `Already in your profile (${had.length})` }),
      el("div", { class: "gap-chips" }, ...had.map((i) => el("span", { class: "gap-chip have", text: i.label })))));
  }
  if (!fresh.length && !result.certificates.length) return;
  const keep = el("button", { type: "button", text: "Keep the ticked ones" });
  const stack = el("button", { type: "button", class: "ghost", text: "Also add them to your stack",
    title: "Puts the ticked skills in “Skills you also have” below, unsaved, so they count in the score once you press Save" });
  const note = el("span", { class: "note" });
  keep.addEventListener("click", async () => {
    const skills = picks.filter((p) => p.kind === "skill" && p.box.checked)
      .map((p) => ({ term: p.item.term || "", word: p.item.word || "", snippet: p.item.snippet, source: file.name }));
    const certificates = picks.filter((p) => p.kind === "cert" && p.box.checked)
      .map((p) => ({ text: p.item, source: file.name }));
    if (!skills.length && !certificates.length) { note.textContent = "Tick at least one first."; return; }
    keep.disabled = true;
    try {
      drawEvidence(await post("/api/evidence", { skills, certificates }));
      note.textContent = `Kept ${plural(skills.length + certificates.length, "item")}.`;
      card.classList.add("kept");
      growthState.data = null;
    } catch (err) { note.textContent = err.message; keep.disabled = false; }
  });
  stack.addEventListener("click", () => {
    const area = $('#criteria textarea[name="supporting_skills"]');
    if (!area) { note.textContent = "The criteria have not loaded yet."; return; }
    const have = new Set(area.value.split("\n").map((l) => l.split(/[:=]/)[0].trim().toLowerCase()));
    const add = picks.filter((p) => p.kind === "skill" && p.box.checked)
      .map((p) => p.item.stack).filter((w) => w && !have.has(w.toLowerCase()));
    if (!add.length) { note.textContent = "They are already in your stack."; return; }
    area.value = [area.value.trim(), ...add].filter(Boolean).join("\n");
    area.dispatchEvent(new Event("input", { bubbles: true }));
    note.textContent = `Added ${add.join(", ")} below. Press Save and rescore to count them.`;
    area.closest(".crit-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  card.append(el("div", { class: "ev-actions" }, keep, stack, note));
}

function drawEvidence(data) {
  const box = $("#ev-kept");
  box.textContent = "";
  const skills = data.skill || [];
  const certs = data.certificate || [];
  if (!skills.length && !certs.length) return;
  box.append(el("h3", { text: "Kept from your documents" }));
  const chips = el("div", { class: "gap-chips" });
  const forget = async (body) => {
    try { drawEvidence(await post("/api/evidence/forget", body)); growthState.data = null; }
    catch (err) { banner(err.message, true); }
  };
  skills.forEach((s) => chips.append(el("span", { class: "gap-chip have ev-chip",
    title: (s.source ? `From ${s.source}` : "") + (s.snippet ? `: “${s.snippet}”` : "") },
    el("span", { text: s.label || s.term || s.word }),
    el("button", { type: "button", class: "tag-x", text: "\u00d7", "aria-label": `Forget ${s.label}`,
      on: { click: () => forget({ term: s.term || "", word: s.word || "" }) } }))));
  certs.forEach((c) => chips.append(el("span", { class: "gap-chip have ev-chip cert",
    title: c.source ? `From ${c.source}` : "" },
    el("span", { text: c.text }),
    el("button", { type: "button", class: "tag-x", text: "\u00d7", "aria-label": "Forget this certificate",
      on: { click: () => forget({ text: c.text }) } }))));
  box.append(chips);
}

function loadEvidence() {
  get("/api/evidence").then(drawEvidence).catch(() => {});
}

function setSaveBar(text, dirty) {
  $("#criteria-status").textContent = text;
  $("#save-bar").classList.toggle("dirty", !!dirty);
}
$("#criteria").addEventListener("input", () => setSaveBar("Unsaved changes", true));

$("#save-criteria").addEventListener("click", async () => {
  const changes = {};
  $$("#criteria .crit-field").forEach((field) => {
    const input = field.querySelector("[name]");
    const kind = field.dataset.kind;
    if (kind === "list") {
      changes[input.name] = input.value.split("\n").map((s) => s.trim()).filter(Boolean);
    } else if (kind === "weights") {
      changes[input.name] = parseWeights(input.value);
    } else {
      changes[input.name] = input.value;
    }
  });
  const status = $("#criteria-status");
  status.textContent = "Saving…";
  try {
    const data = await post("/api/targeting", { changes });
    state.jobs = data.jobs;
    stamp(data);
    draw();
    setSaveBar(`Saved. ${plural(data.moved, "posting")} changed score.`);
    banner(`Criteria saved. ${plural(data.moved, "posting")} changed score; `
         + "the arrows next to each score show which way.");
    show("jobs");
  } catch (err) {
    status.textContent = err.message;
  }
});

$("#open-criteria-file").addEventListener("click", () => openPath(criteriaFile));

/* ------------------------------------------------- criteria: about you */
/*
 * The other half of the Criteria tab: master.toml and letter.toml as cards,
 * drawn from /api/about. The server decides which keys a card can change and
 * checks the file still builds a resume before it keeps an edit, so a refusal
 * here arrives as a sentence saying which rule the edit broke.
 */

let critPane = "scoring";
let aboutFiles = {};

function showCrit(pane) {
  critPane = pane;
  $("#crit-scoring").hidden = pane !== "scoring";
  $("#crit-about").hidden = pane !== "about";
  $$(".crit-switch .chip").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.crit === pane)));
  if (pane === "about") loadAbout();
}

$$(".crit-switch .chip").forEach((b) =>
  b.addEventListener("click", () => showCrit(b.dataset.crit)));
pillTrack($(".crit-switch"));

async function loadAbout() {
  const box = $("#about");
  try {
    drawAbout(await get("/api/about"));
  } catch (err) {
    box.textContent = "";
    box.append(el("p", { class: "note", text: err.message }));
  }
}

function aboutValue(field) {
  if (field.kind === "tags") {
    const names = field.names || field.value;
    if (!names.length) return el("span", { class: "faint", text: "none" });
    return el("span", { class: "about-tags" },
      names.map((n) => el("span", { class: "pill", text: n })));
  }
  const text = String(field.value ?? "");
  return text ? el("span", { text }) : el("span", { class: "faint", text: "blank" });
}

function aboutInput(field) {
  const name = field.key;
  if (field.kind === "long") {
    return el("textarea", { name, value: field.value || "", spellcheck: true,
                            rows: Math.min(10, Math.max(3, Math.ceil((field.value || "").length / 45) + 1)) });
  }
  if (field.kind === "int") {
    return el("input", { type: "number", name, min: 0, max: 100, inputmode: "numeric",
                         value: field.value ?? "" });
  }
  if (field.kind === "tags") {
    return el("textarea", { name, value: (field.value || []).join(", "), rows: 2,
                            spellcheck: false });
  }
  return el("input", { type: "text", name, value: field.value || "" });
}

function aboutCard(card) {
  const node = el("div", { class: "about-card" });
  const fields = el("dl", { class: "about-fields" });
  card.fields.forEach((f) => fields.append(el("dt", { text: f.label }),
                                          el("dd", {}, aboutValue(f))));
  const edit = el("button", { type: "button", class: "ghost small", text: "Edit" });
  node.append(
    el("div", { class: "about-head" },
      el("div", { class: "about-name" },
        el("strong", { text: card.title }),
        card.sub ? el("span", { class: "sub", text: card.sub }) : null,
        card.badges.map((b) => el("span", { class: "pill warn", text: b }))),
      edit),
    fields);
  edit.addEventListener("click", () => editCard(node, card, fields, edit));

  const others = card.children.filter((c) => c.header === "bullet.variant");
  const kids = card.children.filter((c) => c.header !== "bullet.variant");
  if (others.length) {
    const more = el("details", { class: "about-others" },
      el("summary", { text: others.length === 1
        ? "1 other way to say it" : `${others.length} other ways to say it` }));
    others.forEach((c) => more.append(aboutCard(c)));
    node.append(more);
  }
  if (kids.length) {
    node.append(el("div", { class: "about-kids" }, kids.map(aboutCard)));
  }
  return node;
}

function editCard(node, card, fields, button) {
  button.hidden = true;
  const status = el("span", { class: "note" });
  const form = el("form", { class: "about-form" });
  card.fields.forEach((f) => form.append(el("label", { class: "crit-field" },
    el("span", { class: "label", text: f.label }),
    f.help ? el("span", { class: "help", text: f.help }) : null,
    aboutInput(f))));
  const save = el("button", { type: "submit", text: "Save" });
  const cancel = el("button", { type: "button", class: "ghost", text: "Cancel" });
  form.append(el("div", { class: "about-actions" }, save, cancel, status));
  fields.replaceWith(form);
  form.querySelector("[name]").focus();

  cancel.addEventListener("click", () => {
    form.replaceWith(fields);
    button.hidden = false;
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    card.fields.forEach((f) => {
      const input = form.querySelector(`[name="${f.key}"]`);
      const before = f.kind === "tags" ? (f.value || []).join(", ") : String(f.value ?? "");
      if (input.value !== before) changes[f.key] = input.value;
    });
    if (!Object.keys(changes).length) { cancel.click(); return; }
    save.disabled = true;
    status.textContent = "Saving…";
    try {
      const data = await post("/api/about",
        { file: card.file, header: card.header, id: card.id, changes });
      drawAbout(data);
      banner("Saved. The next resume or letter you build uses it.");
    } catch (err) {
      save.disabled = false;
      status.textContent = err.message;
      status.classList.add("bad");
    }
  });
}

function drawAbout(data) {
  aboutFiles = data.files;
  $("#about-file").textContent = data.files.master;
  const box = $("#about");
  const opened = new Set($$("#about > details[open]").map((d) => d.dataset.id));
  const othersOpen = new Set($$("#about .about-others[open]").map((d) => d.dataset.id));
  const first = !$("#about > details");
  const nav = $("#about-jump");
  box.textContent = "";
  nav.textContent = "";

  data.sections.forEach((s, i) => {
    const groups = s.groups || [];
    const count = s.cards.length + groups.reduce((n, g) => n + g.cards.length, 0);
    const sec = el("details", { class: "crit-section about-section", id: "about-" + s.id,
                                open: first ? i === 0 : opened.has(s.id) },
      el("summary", {}, el("h3", { text: s.title }),
        el("span", { class: "faint", text: plural(count, "entry", "entries") })),
      s.intro ? el("p", { class: "note", text: s.intro }) : null);
    sec.dataset.id = s.id;
    s.cards.forEach((c) => sec.append(aboutCard(c)));
    groups.forEach((g) => {
      sec.append(el("h4", { class: "about-group", text: g.title }));
      g.cards.forEach((c) => sec.append(aboutCard(c)));
    });
    box.append(sec);
    nav.append(el("button", { type: "button", class: "chip", text: s.title, on: {
      click: () => { sec.open = true; sec.scrollIntoView({ block: "start" }); } } }));
  });
  $$("#about .about-others").forEach((d) => {
    const owner = d.closest(".about-card").querySelector(".sub");
    d.dataset.id = owner ? owner.textContent : "";
    if (othersOpen.has(d.dataset.id)) d.open = true;
  });
}

$("#open-about-file").addEventListener("click", () => openPath(aboutFiles.master));

async function openPath(path) {
  if (!path) return;
  try { await post("/api/open", { path }); } catch (err) { banner(err.message, true); }
}

/* ============================================================= setup tab */

const wizard = { step: 0, parsed: null, resumeText: "" };
const STEPS = 7;

function drawSteps() {
  $$("#steps li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.dataset.state = n === wizard.step ? "now" : (n < wizard.step ? "done" : "");
  });
  $("#steps").style.setProperty("--progress", String(wizard.step / (STEPS - 1)));
  $$("#wizard .step").forEach((f) => {
    f.hidden = Number(f.dataset.step) !== wizard.step;
  });
  $("#back").disabled = wizard.step === 0;
  $("#next").hidden = wizard.step === STEPS - 1;
  $("#finish").hidden = wizard.step !== STEPS - 1;
}

$("#back").addEventListener("click", () => {
  wizard.step = Math.max(0, wizard.step - 1);
  drawSteps();
});
$("#next").addEventListener("click", () => {
  wizard.step = Math.min(STEPS - 1, wizard.step + 1);
  drawSteps();
});

/* -- resume upload -- */

const drop = $("#drop");
drop.addEventListener("click", () => $("#resume").click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});
$("#resume").addEventListener("change", (e) => {
  if (e.target.files.length) upload(e.target.files[0]);
});

function base64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("the file could not be read"));
    reader.readAsDataURL(file);
  });
}

async function upload(file) {
  const status = $("#resume-status");
  status.textContent = `Reading ${file.name}…`;
  try {
    const parsed = await post("/api/setup/resume", {
      filename: file.name,
      content: await base64(file),
    });
    wizard.parsed = parsed;
    wizard.resumeText = parsed.text || "";
    applyParsed(parsed);
    status.textContent =
      `Read ${parsed.characters.toLocaleString()} characters. `
      + `Filled in: ${parsed.filled.join(", ") || "nothing it was sure about"}. `
      + `Check each screen before moving on.`;
  } catch (err) {
    status.textContent = err.message;
  }
}

function applyParsed(parsed) {
  const form = $("#wizard");
  ["name", "email", "phone", "linkedin", "github", "city", "state"]
    .forEach((key) => {
      if (parsed[key] && !form.elements[key].value) form.elements[key].value = parsed[key];
    });

  const chips = $("#title-chips");
  chips.textContent = "";
  (parsed.titles || []).forEach((title) => {
    const chip = el("button", { type: "button", class: "chip", text: title });
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => {
      const on = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!on));
      const box = form.elements.titles_1;
      const lines = box.value.split("\n").map((s) => s.trim()).filter(Boolean);
      const lower = title.toLowerCase();
      box.value = (on ? lines.filter((l) => l.toLowerCase() !== lower)
                      : lines.concat(lower)).join("\n");
    });
    chips.append(chip);
  });
  $("#title-picks").hidden = !(parsed.titles || []).length;
}

/* -- finish -- */

function answers() {
  const form = $("#wizard");
  const lines = (name) => form.elements[name].value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  return {
    name: form.elements.name.value,
    email: form.elements.email.value,
    phone: form.elements.phone.value,
    linkedin: form.elements.linkedin.value,
    github: form.elements.github.value,
    city: form.elements.city.value,
    state: form.elements.state.value,
    titles_1: lines("titles_1"),
    titles_2: lines("titles_2"),
    titles_3: lines("titles_3"),
    work_mode: form.elements.work_mode.value,
    radius_miles: form.elements.radius_miles.value,
    salary_floor: form.elements.salary_floor.value,
    salary_target: form.elements.salary_target.value,
    dealbreakers: lines("dealbreakers"),
    employers: lines("employers").map((n) => ({ name: n })),
    resume_path: wizard.parsed ? wizard.parsed.filename : "",
    resume_text: wizard.resumeText,
    delivery_resumes: form.elements.delivery_resumes.value,
    delivery_packets: form.elements.delivery_packets.value,
  };
}

$("#finish").addEventListener("click", async () => {
  const problems = $("#wizard-problems");
  const status = $("#wizard-status");
  problems.hidden = true;
  status.textContent = "Checking…";
  const payload = answers();

  try {
    const check = await post("/api/setup/check", payload);
    if (!check.ok) {
      problems.textContent = "";
      check.problems.forEach((p) => problems.append(el("p", { text: p })));
      problems.hidden = false;
      status.textContent = "";
      return;
    }
    status.textContent = "Writing your profile…";
    finished(await post("/api/setup/save", payload));
  } catch (err) {
    status.textContent = "";
    problems.textContent = err.message;
    problems.hidden = false;
  }
});

function finished(done) {
  $("#wizard").hidden = true;
  $("#steps").hidden = true;
  const box = $("#setup-done");
  box.hidden = false;
  box.textContent = "";
  box.append(
    el("h2", { text: "Your profile is written" }),
    el("p", { class: "note", text: done.directory }),
    el("ul", {}, done.files.map((f) => el("li", {
      text: f + (done.carried.includes(f)
        ? " — copied from the example, yours to edit" : "") }))),
    el("p", { text: done.next }));
  boot("setup");
}

/* ================================================================= phone */
/*
 * The panel is written to be read when it is NOT working, because that is the
 * only time anyone opens it twice. Every line answers one question a phone
 * sitting on a blank tab raises, in the order the packet travels: is there an
 * address, is JobDesk answering on it, does the firewall let anything through,
 * and has any device off this machine actually arrived.
 */

let phoneState = null;

function phoneFact(label, value, tone) {
  return el("div", { class: "fact" + (tone ? " " + tone : "") },
    el("span", { class: "fact-k", text: label }),
    el("span", { class: "fact-v", text: value }));
}

function drawPhone(p) {
  phoneState = p;
  const qr = $("#phone-qr");
  const facts = $("#phone-facts");
  qr.innerHTML = "";
  facts.innerHTML = "";

  if (p.svg) {
    qr.innerHTML = p.svg;
    qr.append(el("p", { class: "note", text: "Scan this with the phone's camera. It pairs on the first open, and the link stops working for anyone who did not scan it." }));
  } else {
    qr.append(el("p", { class: "note", text: p.problem || "Turn phone access on and the code appears here." }));
  }

  if (p.address) {
    const kind = p.kind === "tailnet"
      ? "a tailnet address, which works off your wifi too"
      : "your local network only, so wifi and not cellular";
    facts.append(phoneFact("Address", `${p.address}:${p.port}`, ""));
    facts.append(phoneFact("Network", kind, p.kind === "tailnet" ? "good" : "warn"));
  }
  facts.append(phoneFact("Answering", p.serving ? "yes" : "not yet",
                         p.serving ? "good" : "warn"));
  facts.append(phoneFact("Firewall",
    p.firewall === "open" ? "the port is open"
      : p.firewall === "blocked" ? "the port is closed, so the phone's request is dropped rather than refused"
      : "cannot tell without admin rights",
    p.firewall === "open" ? "good" : p.firewall === "blocked" ? "bad" : "warn"));
  facts.append(phoneFact("Comes back at logon", p.on
    ? (p.unlimited ? "yes" : "yes, but the task has a three-day time limit and will be killed")
    : "no", p.on && p.unlimited ? "good" : "warn"));

  // The one fact that is about the phone rather than about this machine. A
  // desktop can report every line above as healthy and still be unreachable.
  const arrivals = p.arrivals || [];
  if (arrivals.length) {
    const first = arrivals[0];
    facts.append(phoneFact("Devices that reached JobDesk",
      `${arrivals.length} · ${first.host} · ${first.accepted} allowed, ${first.refused} turned away`,
      first.refused && !first.accepted ? "bad" : "good"));
  } else if (p.on) {
    facts.append(phoneFact("Devices that reached JobDesk",
      "none yet since this server started", "warn"));
  }

  if (p.firewall === "blocked") {
    facts.append(el("p", { class: "note" },
      el("span", { text: "Open PowerShell as administrator and run this once: " }),
      el("code", { text: p.firewall_fix })));
  }

  $("#phone-toggle").textContent = p.on ? "Turn phone access off" : "Turn phone access on";
  $("#phone-rotate").hidden = !p.token_set;
}

async function loadPhone() {
  try {
    drawPhone(await get("/api/phone"));
  } catch (err) {
    $("#phone-status").textContent = err.message;
  }
}

$("#phone-toggle").addEventListener("click", async () => {
  const button = $("#phone-toggle");
  const status = $("#phone-status");
  const turningOn = !(phoneState && phoneState.on);
  button.disabled = true;
  status.textContent = turningOn ? "Setting it up…" : "Turning it off…";
  try {
    drawPhone(await post("/api/phone", { on: turningOn }));
    status.textContent = turningOn
      ? "On. Scan the code with the phone."
      : "Off. JobDesk stops coming up on the network at logon. Any paired phone keeps its token.";
  } catch (err) {
    status.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

$("#phone-rotate").addEventListener("click", async () => {
  // Destructive and quiet about it otherwise: every paired device stops
  // working the moment this returns, and the only way back is another scan.
  if (!confirm("A new token logs out every phone that is already paired. They have to scan the new code. Continue?")) return;
  const status = $("#phone-status");
  status.textContent = "Minting…";
  try {
    drawPhone(await post("/api/phone/rotate", {}));
    status.textContent = "New token. Scan the code again on every phone you use.";
  } catch (err) {
    status.textContent = err.message;
  }
});

$("#make-shortcut").addEventListener("click", async () => {
  const status = $("#shortcut-status");
  status.textContent = "Writing…";
  try {
    const data = await post("/api/shortcut");
    status.textContent = `Done: ${data.path}`;
  } catch (err) { status.textContent = err.message; }
});

/* ============================================================== settings */
/*
 * Every control carries `data-pref="<key>"` and saves itself on change, one
 * key at a time. The server keeps the file (data/settings.json) so the phone
 * and the desktop window share it; the three appearance keys are also left in
 * localStorage for theme.js, which applies them before the first paint.
 */

const LOOK_KEYS = ["theme", "scheme", "font", "text_size", "density", "accent", "custom"];
let prefs = {
  jobs_sort: "newest", score_min: 45, score_max: 100, hide_applied: true,
  hide_prepared: false, remote_only: false, starred_only: false,
  has_pay: false, market_up: false, tier: "", posted_within: "any", source: "",
  start_tab: "jobs",
  theme: "system", scheme: "mailroom", font: "system", text_size: 100,
  density: "normal", accent: "",
  job_columns: ["company", "location", "age_days", "market", "salary", "source"],
  views: [],
  custom: { on: false, id: "", bg: [216, 33, 97], ac: [221, 83, 53], fade: false,
            a: [256, 45, 14], b: [190, 60, 22], fade_style: "diagonal", over: {} },
  themes: [],
};

const HEX = /^#[0-9a-f]{6}$/i;

// Dark text on a light accent, white on a dark one, by WCAG luminance. The
// crossover near 0.18 is where the two give the same contrast.
function accentInk(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.18 ? "#10161a" : "#ffffff";
}
let prefDefaults = Object.assign({}, prefs);   // replaced by the server's copy

/* -- custom colors -- */
/*
 * Built the way FitNext builds its custom themes: a background and an accent,
 * each picked by hue, saturation and lightness, and every other color worked
 * out from those two, so any pair stays readable. Cards lift off the page,
 * text contrasts with it, and the lines sit between. Fade swaps the one
 * background for two colors the page fades between, and Fine-tune lets any
 * single color be picked by hand on top of the worked-out one.
 */

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function hsl(h, s, l) {
  s = clamp(s, 0, 100) / 100;
  l = clamp(l, 0, 100) / 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return "#" + [f(0), f(8), f(4)].map((x) =>
    Math.round(x * 255).toString(16).padStart(2, "0")).join("");
}

function toHsl(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2, d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  const s = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
  return [Math.round((h * 60 + 360) % 360), Math.round(s * 100), Math.round(l * 100)];
}

// Halfway between two colors, the short way round the hue circle. A fade's
// cards and text are worked out from this, so they suit both ends.
function midHsl(a, b) {
  const d = ((b[0] - a[0] + 540) % 360) - 180;
  return [Math.round((a[0] + d / 2 + 360) % 360),
          Math.round((a[1] + b[1]) / 2), Math.round((a[2] + b[2]) / 2)];
}

function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [n >> 16, (n >> 8) & 255, n & 255].reduce((sum, c, i) => {
    c /= 255;
    c = c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    return sum + c * [0.2126, 0.7152, 0.0722][i];
  }, 0);
}

const FADES = {
  diagonal: (a, b) => `linear-gradient(135deg, ${a}, ${b})`,
  vertical: (a, b) => `linear-gradient(180deg, ${a}, ${b})`,
  horizontal: (a, b) => `linear-gradient(90deg, ${a}, ${b})`,
  glow: (a, b, ground) => `radial-gradient(60% 55% at 12% 8%, ${a} 0%, transparent 72%), `
    + `radial-gradient(55% 60% at 92% 88%, ${b} 0%, transparent 72%), ${ground}`,
};

// Every token a custom palette sets, from its background and accent.
function palette(c) {
  const [h, s, l] = c.fade ? midHsl(c.a, c.b) : c.bg;
  const dark = l < 45;
  const t = (dl) => hsl(h, s, clamp(l + dl, 0, 100));
  const accent = hsl(...c.ac);
  const tokens = {
    ground: hsl(h, s, l),
    card: dark ? t(5) : l > 93 ? "#ffffff" : t(Math.min(8, 100 - l)),
    "line-soft": dark ? t(9) : t(-5),
    line: dark ? t(14) : t(-10),
    ink: dark ? hsl(h, Math.min(s, 20), 93) : hsl(h, Math.min(s + 20, 60), 12),
    "ink-soft": dark ? hsl(h, Math.min(s, 18), 66) : hsl(h, Math.min(s + 10, 25), 40),
    "ink-faint": dark ? hsl(h, Math.min(s, 15), 47) : hsl(h, Math.min(s + 5, 20), 60),
    accent, "accent-ink": accentInk(accent),
    good: dark ? "#4cc38a" : "#1f8a4c",
    warn: dark ? "#e0b25a" : "#b7791f",
    bad: dark ? "#f07a6a" : "#c0392b",
  };
  const over = c.over || {};
  Object.keys(over).forEach((k) => { if (k in tokens && HEX.test(over[k])) tokens[k] = over[k]; });
  // A hand-picked accent still gets readable text on it unless that was
  // picked by hand too.
  if (over.accent && !over["accent-ink"]) tokens["accent-ink"] = accentInk(tokens.accent);
  const isDark = over.ground ? luminance(tokens.ground) < 0.18 : dark;
  const backdrop = c.fade
    ? (FADES[c.fade_style] || FADES.diagonal)(hsl(...c.a), hsl(...c.b), tokens.ground) : "";
  return { dark: isDark, tokens, backdrop };
}

// The inline properties a custom palette puts on the root, so turning it off
// can take them back, whether app.js or theme.js put them there. The accent
// pair is left to applyLook's own accent code, which runs first.
const PAINTED = ["ground", "card", "line", "line-soft", "ink", "ink-soft", "ink-faint",
  "good", "warn", "bad"].map((k) => `--${k}-fixed`).concat(["--backdrop", "--shadow"]);

function paintCustom(c) {
  const root = document.documentElement;
  PAINTED.forEach((prop) => root.style.removeProperty(prop));
  root.style.removeProperty("color-scheme");
  if (!c) return null;
  const { dark, tokens, backdrop } = palette(c);
  const vars = {};
  Object.keys(tokens).forEach((k) => { vars[`--${k}-fixed`] = tokens[k]; });
  if (backdrop) vars["--backdrop"] = backdrop;
  vars["--shadow"] = dark ? "none"
    : "0 1px 2px rgba(20, 25, 35, .05), 0 6px 18px rgba(20, 25, 35, .06)";
  Object.keys(vars).forEach((prop) => root.style.setProperty(prop, vars[prop]));
  root.style.colorScheme = dark ? "dark" : "light";
  return { scheme: c.fade ? "fade-custom" : "custom", vars, dark };
}

function applyLook(p) {
  const root = document.documentElement;
  const custom = p.custom && p.custom.on ? p.custom : null;
  if (p.theme === "light" || p.theme === "dark") root.dataset.theme = p.theme;
  else delete root.dataset.theme;
  root.dataset.font = p.font || "system";
  root.dataset.density = p.density;
  /* A number, not an attribute, so the size can be any percentage the server
     will accept rather than one of a handful the stylesheet knows by name. */
  root.style.setProperty("--zoom", String((Number(p.text_size) || 100) / 100));
  // A custom accent overrides the scheme's in both light and dark, and over
  // a one-mode scheme's own. Custom colors bring their own accent.
  const accent = !custom && HEX.test(p.accent || "") ? p.accent : "";
  ["light", "dark", "fixed"].forEach((mode) => {
    if (accent) {
      root.style.setProperty(`--accent-${mode}`, accent);
      root.style.setProperty(`--accent-ink-${mode}`, accentInk(accent));
    } else {
      root.style.removeProperty(`--accent-${mode}`);
      root.style.removeProperty(`--accent-ink-${mode}`);
    }
  });
  const paint = paintCustom(custom);
  root.dataset.scheme = paint ? paint.scheme : (p.scheme || "mailroom");
  const shown = accent || getComputedStyle(root).getPropertyValue("--accent").trim();
  if (HEX.test(shown)) $("#accent").value = shown.toLowerCase();
  const look = {};
  LOOK_KEYS.forEach((k) => { look[k] = p[k]; });
  // theme.js cannot work a palette out, so it is handed the result.
  look.paint = paint;
  try { localStorage.setItem("jobdesk.look", JSON.stringify(look)); } catch { /* private mode */ }
  // The phone's status bar takes this color, so it follows the theme too.
  const meta = document.querySelector('meta[name="theme-color"]');
  const card = getComputedStyle(root).getPropertyValue("--card").trim();
  if (meta && card) meta.content = card;
  drawThemeNote(p);
}

/* -- the scheme picker -- */

// Which group of the Colors menu a scheme sits in: "both" for the ones with
// a light and a dark mode, anything else for one that is always one or the
// other. The menu's own markup says, so there is no second list to keep.
function schemeMode(id) {
  const option = $(`#scheme option[value="${id}"]`);
  return option ? option.parentElement.dataset.mode : "both";
}

function customOn(p) { return !!(p.custom && p.custom.on); }

function drawThemeNote(p) {
  const on = customOn(p);
  const fixed = on || schemeMode(p.scheme) !== "both";
  const theme = $('[data-pref="theme"]');
  theme.disabled = fixed;
  const note = $("#theme-note");
  note.hidden = !fixed;
  if (fixed) {
    const mine = on && (p.themes || []).find((t) => t.id === p.custom.id);
    const name = on ? (mine ? mine.name : "Custom colors")
      : $(`#scheme option[value="${p.scheme}"]`).textContent;
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    note.textContent = `${name} ${on && !mine ? "are" : "is"} always ${dark ? "dark" : "light"}, `
      + `so Theme does nothing while ${on && !mine ? "they are" : "it is"} picked.`;
  }
  $$("#swatches .swatch").forEach((b) => {
    const pressed = b.dataset.mine
      ? on && b.dataset.mine === p.custom.id
      : !on && b.dataset.scheme === p.scheme;
    b.setAttribute("aria-pressed", String(pressed));
  });
}

// A saved theme's swatch paints itself from its own palette, inline, which
// outranks the reset every swatch starts from.
function mineSwatch(theme) {
  const { tokens, backdrop } = palette(theme);
  const b = el("button", { class: "swatch mine", type: "button",
    title: theme.name, "aria-label": theme.name,
    on: { click: () => pickMine(theme) } },
    el("span", { class: "sw-l" }, el("i"), el("b")),
    el("span", { class: "sw-d" }, el("i"), el("b")),
    el("span", { class: "sw-name", text: theme.name }));
  b.dataset.mine = theme.id;
  ["ground", "card", "line", "accent"].forEach((k) =>
    b.style.setProperty(`--${k}-fixed`, tokens[k]));
  if (backdrop) b.style.setProperty("--backdrop", backdrop);
  const x = el("span", { class: "swatch-x", role: "button", tabindex: "0",
    title: `Delete ${theme.name}`, "aria-label": `Delete ${theme.name}`, text: "×",
    on: {
      click: (e) => { e.stopPropagation(); deleteMine(theme); },
      keydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); deleteMine(theme); } },
    } });
  b.append(x);
  return b;
}

/* One button per scheme, grouped the way the menu is. Each carries its own
   `data-scheme`, so the stylesheet paints it from the same block the page
   would use: the left half is the light mode, the right half the dark one,
   and a one-mode scheme is the same on both. Saved custom colors come first,
   under Yours. */
function drawSwatches() {
  const box = $("#swatches");
  box.textContent = "";
  const mine = prefs.themes || [];
  if (mine.length) {
    box.append(el("h3", { class: "swatch-head", text: "Yours" }),
      el("div", { class: "swatch-row" }, ...mine.map(mineSwatch)));
  }
  $$("#scheme optgroup").forEach((group) => {
    const row = el("div", { class: "swatch-row" });
    Array.from(group.children).forEach((option) => {
      const b = el("button", { class: "swatch", type: "button",
        title: option.textContent, "aria-label": option.textContent,
        on: { click: () => {
          const select = $("#scheme");
          select.value = option.value;
          select.dispatchEvent(new Event("change"));
        } } },
        el("span", { class: "sw-l" }, el("i"), el("b")),
        el("span", { class: "sw-d" }, el("i"), el("b")),
        el("span", { class: "sw-name", text: option.textContent }));
      b.dataset.scheme = option.value;
      row.append(b);
    });
    box.append(el("h3", { class: "swatch-head", text: group.label }), row);
  });
  drawThemeNote(prefs);
}
drawSwatches();

/* -- the custom colors editor -- */

// The palette being edited. Moving a slider previews it on the whole page
// at once; letting go saves it. Once moved, it is no longer the saved theme
// it started from, so that one stays as it was until saved again.
let draft = null;
let customSyncs = [];
let customTimer = null;

const PALETTE_KEYS = ["bg", "ac", "fade", "a", "b", "fade_style", "over"];
const FINE_TUNE = [["ground", "Page"], ["card", "Cards"], ["line", "Lines"],
  ["ink", "Text"], ["ink-soft", "Quiet text"], ["accent", "Accent"],
  ["accent-ink", "Text on the accent"], ["good", "Good, tier A"],
  ["warn", "Warning, tier C"], ["bad", "Bad"]];

function freshDraft() {
  return JSON.parse(JSON.stringify(prefs.custom || prefDefaults.custom));
}

function previewCustom() {
  applyLook(Object.assign({}, prefs, { custom: draft }));
  customSyncs.forEach((sync) => sync());
}

function editCustom(change, now) {
  change(draft);
  draft.on = true;
  draft.id = "";
  previewCustom();
  clearTimeout(customTimer);
  if (now) commitCustom();
}

async function commitCustom() {
  clearTimeout(customTimer);
  const sending = JSON.parse(JSON.stringify(draft));
  await saveSetting("custom", sending);
  applyLook(prefs);
  drawThemeNote(prefs);
}

async function pickMine(theme) {
  draft = Object.assign({ on: true, id: theme.id },
    JSON.parse(JSON.stringify(Object.fromEntries(PALETTE_KEYS.map((k) => [k, theme[k]])))));
  previewCustom();
  await commitCustom();
}

async function deleteMine(theme) {
  const themes = (prefs.themes || []).filter((t) => t.id !== theme.id);
  const body = { themes };
  // The colors stay on the page; they just stop being that saved theme.
  if (prefs.custom && prefs.custom.id === theme.id) body.custom = Object.assign({}, prefs.custom, { id: "" });
  try {
    const data = await post("/api/settings", { settings: body });
    prefs = data.settings;
    draft = freshDraft();
    drawSwatches();
    customSyncs.forEach((sync) => sync());
    saved(`Deleted ${theme.name}.`);
  } catch (err) { saved(err.message); }
}

// Three sliders and a color well for one color. The slider tracks show what
// each one would do from where the other two are.
function colorGroup(title, key) {
  const get = () => draft[key];
  const well = el("input", { type: "color", "aria-label": `${title} color`,
    on: {
      input: (e) => editCustom((d) => { d[key] = toHsl(e.target.value); }),
      change: () => commitCustom(),
    } });
  const rows = [["Hue", 0, 359], ["Color", 1, 100], ["Light", 2, 100]].map(([label, i, max]) => {
    const range = el("input", { type: "range", min: 0, max, "aria-label": `${title} ${label.toLowerCase()}`,
      on: {
        input: (e) => editCustom((d) => { d[key] = d[key].slice(); d[key][i] = Number(e.target.value); }),
        change: () => commitCustom(),
      } });
    const num = el("span", { class: "slider-num" });
    const row = el("label", { class: "slider" }, el("span", { class: "slider-label", text: label }), range, num);
    return { i, range, num, row };
  });
  const box = el("div", { class: "color-group" },
    el("div", { class: "color-head" }, el("b", { text: title }), well),
    ...rows.map((r) => r.row));
  customSyncs.push(() => {
    const [h, s, l] = get();
    well.value = hsl(h, s, l);
    rows.forEach((r) => {
      r.range.value = get()[r.i];
      r.num.textContent = String(get()[r.i]);
    });
    rows[0].range.style.background = `linear-gradient(90deg, ${[0, 60, 120, 180, 240, 300, 359]
      .map((x) => hsl(x, Math.max(s, 50), 50)).join(", ")})`;
    rows[1].range.style.background = `linear-gradient(90deg, ${hsl(h, 0, l)}, ${hsl(h, 100, l)})`;
    rows[2].range.style.background = `linear-gradient(90deg, #000, ${hsl(h, s, 50)}, #fff)`;
  });
  return box;
}

// Every slider of the background in use (one color, or both fade ends) and
// the accent gets any value at all. Light or dark is whatever falls out;
// cards and text are worked out from the result, so it stays readable.
function randomize(d) {
  const any = () => [Math.floor(Math.random() * 360), Math.floor(Math.random() * 101),
    Math.floor(Math.random() * 101)];
  if (d.fade) { d.a = any(); d.b = any(); } else d.bg = any();
  d.ac = any();
}

// A row of buttons that works like a radio group, with a pill that slides to
// the one picked.
function segmented(label, options, get, set) {
  const box = el("div", { class: "seg", role: "radiogroup", "aria-label": label });
  options.forEach(([value, text]) => box.append(el("button", {
    type: "button", class: "seg-btn", role: "radio", text,
    on: { click: () => set(value) } })));
  box.querySelectorAll(".seg-btn").forEach((b, i) => { b.dataset.value = options[i][0]; });
  pillTrack(box);
  customSyncs.push(() => {
    box.querySelectorAll(".seg-btn").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.value === get())));
  });
  return box;
}

function switchBox(label, get, set) {
  const input = el("input", { type: "checkbox", role: "switch",
    on: { change: (e) => set(e.target.checked) } });
  customSyncs.push(() => { input.checked = !!get(); });
  return el("label", { class: "switch" }, input, el("span", { class: "switch-track" }), el("span", { text: label }));
}

// A small JobDesk, painted from the draft: a menu, a row with a score, two
// chips and the accent button. It reads the draft directly, so it shows the
// colors even while they are off on the page.
function customPreview() {
  const box = el("div", { class: "custom-preview", "aria-hidden": "true" },
    el("div", { class: "cp-side" }, el("i", { class: "cp-mark" }), el("i"), el("i", { class: "on" }), el("i")),
    el("div", { class: "cp-main" },
      el("div", { class: "cp-chips" }, el("span", { class: "on", text: "All" }), el("span", { text: "A" }), el("span", { text: "Remote" })),
      el("div", { class: "cp-card" },
        el("span", { class: "cp-score", text: "93" }), el("span", { class: "cp-tier", text: "A" }),
        el("span", { class: "cp-lines" }, el("b", { text: "Data Analyst" }), el("small", { text: "Scale Media · Remote" }))),
      el("div", { class: "cp-card" },
        el("span", { class: "cp-score", text: "71" }), el("span", { class: "cp-tier b", text: "B" }),
        el("span", { class: "cp-lines" }, el("b", { text: "Ops Analyst" }), el("small", { text: "CarMax · Richmond" }))),
      el("span", { class: "cp-btn", text: "Build the packet" })));
  customSyncs.push(() => {
    const { tokens, backdrop } = palette(draft);
    Object.keys(tokens).forEach((k) => box.style.setProperty(`--p-${k}`, tokens[k]));
    box.style.setProperty("--p-backdrop", backdrop || tokens.ground);
    box.classList.toggle("glass", !!draft.fade);
  });
  return box;
}

function fineTune() {
  const list = el("div", { class: "fine" });
  FINE_TUNE.forEach(([key, label]) => {
    const well = el("input", { type: "color", "aria-label": label,
      on: {
        input: (e) => editCustom((d) => { d.over = Object.assign({}, d.over, { [key]: e.target.value }); }),
        change: () => commitCustom(),
      } });
    const auto = el("button", { type: "button", class: "ghost small", text: "Auto",
      title: "Go back to the worked-out color",
      on: { click: () => editCustom((d) => {
        d.over = Object.assign({}, d.over);
        delete d.over[key];
      }, true) } });
    const row = el("div", { class: "fine-row" }, well, el("span", { class: "fine-label", text: label }), auto);
    customSyncs.push(() => {
      well.value = palette(draft).tokens[key];
      const picked = !!(draft.over && draft.over[key]);
      row.classList.toggle("picked", picked);
      auto.hidden = !picked;
    });
    list.append(row);
  });
  return el("details", { class: "fine-box" },
    el("summary", {}, el("span", { text: "Fine-tune each color" }),
      el("span", { class: "note", text: "Anything picked here wins over the worked-out color." })),
    list);
}

function drawCustom() {
  const box = $("#custom-editor");
  box.textContent = "";
  customSyncs = [];
  draft = freshDraft();
  const solid = colorGroup("Background", "bg");
  const from = colorGroup("Fade from", "a");
  const to = colorGroup("Fade to", "b");
  const style = segmented("Fade direction",
    [["diagonal", "Diagonal"], ["vertical", "Top to bottom"], ["horizontal", "Side to side"], ["glow", "Two glows"]],
    () => draft.fade_style, (v) => editCustom((d) => { d.fade_style = v; }, true));
  const fadeBox = el("div", { class: "fade-box" }, el("div", { class: "color-pair" }, from, to), style,
    el("p", { class: "note fade-warn" }));
  const name = el("input", { type: "text", maxlength: 40, placeholder: "Name these colors",
    "aria-label": "Name for these colors" });
  const save = el("button", { type: "submit", text: "Save as a theme" });
  const form = el("form", { class: "save-theme",
    on: { submit: (e) => { e.preventDefault(); saveMine(name); } } }, name, save);
  box.append(
    el("div", { class: "custom-top" },
      customPreview(),
      el("div", { class: "custom-switches" },
        switchBox("Use my colors", () => draft.on, async (on) => {
          draft.on = on;
          previewCustom();
          await commitCustom();
        }),
        el("div", { class: "bg-row" },
          segmented("Background", [["solid", "One color"], ["fade", "Fade"]],
            () => (draft.fade ? "fade" : "solid"),
            (v) => editCustom((d) => { d.fade = v === "fade"; }, true)),
          el("button", { type: "button", class: "ghost randomize", text: "Randomize",
            title: "Any hue, color and light for every slider, light or dark",
            on: { click: () => editCustom(randomize, true) } })),
        el("p", { class: "note", text: "Cards, text and lines follow from the background, "
          + "so every pair stays readable." }))),
    el("div", { class: "custom-groups" },
      el("div", { class: "solid-box" }, solid), fadeBox, colorGroup("Accent", "ac")),
    fineTune(),
    form);
  customSyncs.push(() => {
    solid.parentElement.hidden = !!draft.fade;
    fadeBox.hidden = !draft.fade;
    const far = Math.abs(draft.a[2] - draft.b[2]) > 45;
    const warn = fadeBox.querySelector(".fade-warn");
    warn.hidden = !far;
    warn.textContent = far ? "The two ends are far apart in lightness, so text over one end "
      + "may be hard to read. Cards stay readable either way." : "";
    name.placeholder = `My theme ${(prefs.themes || []).length + 1}`;
  });
  customSyncs.forEach((sync) => sync());
}

async function saveMine(nameInput) {
  const themes = prefs.themes || [];
  const id = "my-" + Date.now().toString(36);
  const theme = Object.assign({ id, name: nameInput.value.trim() || `My theme ${themes.length + 1}` },
    JSON.parse(JSON.stringify(Object.fromEntries(PALETTE_KEYS.map((k) => [k, draft[k]])))));
  const custom = Object.assign({}, draft, { on: true, id });
  try {
    const data = await post("/api/settings", { settings: {
      themes: [theme, ...themes].slice(0, 24), custom } });
    prefs = data.settings;
    nameInput.value = "";
    draft = freshDraft();
    applyLook(prefs);
    drawSwatches();
    customSyncs.forEach((sync) => sync());
    saved(`Saved ${theme.name}. It is first under Yours.`);
  } catch (err) { saved(err.message); }
}

function applyFilters(p) {
  $("#score-min").value = p.score_min;
  $("#score-max").value = p.score_max;
  $("#hide-applied").checked = !!p.hide_applied;
  $("#hide-prepared").checked = !!p.hide_prepared;
  $("#remote-only").checked = !!p.remote_only;
  $("#starred-only").checked = !!p.starred_only;
  $("#has-pay").checked = !!p.has_pay;
  $("#market-up").checked = !!p.market_up;
  $("#posted-within").value = p.posted_within || "any";
  setSource(p.source);
  setTier(p.tier);
  setOrder(p.jobs_sort);
}

function drawPrefs() {
  $$("[data-pref]").forEach((input) => {
    const value = prefs[input.dataset.pref];
    if (input.type === "checkbox") { input.checked = !!value; return; }
    input.value = String(value);
    /* A saved number the menu no longer lists -- a text size from when the
       steps were 90/115/130 -- leaves a select showing nothing at all, which
       reads as a broken setting rather than an old one. Show the nearest
       step it does offer, without saving over what is on disk. */
    if (input.tagName === "SELECT" && input.selectedIndex === -1) {
      const n = Number(value);
      const options = Array.from(input.options);
      const numeric = options.filter((o) => o.value !== "" && !isNaN(Number(o.value)));
      if (!isNaN(n) && numeric.length) {
        input.value = numeric.reduce((best, o) =>
          Math.abs(Number(o.value) - n) < Math.abs(Number(best.value) - n) ? o : best).value;
      } else if (options.length) {
        input.selectedIndex = 0;
      }
    }
  });
}

let savedTimer = null;
function saved(text) {
  const note = $("#settings-status");
  note.textContent = text;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { note.textContent = ""; }, 2500);
}

$$("[data-pref]").forEach((input) => input.addEventListener("change", async () => {
  const key = input.dataset.pref;
  let value = input.type === "checkbox" ? input.checked : input.value;
  if (input.type === "number" || key === "text_size") value = Number(value);
  const body = { [key]: value };
  // Picking one of the built-in schemes puts custom colors away, but keeps
  // them, so the switch in Custom colors brings them straight back.
  if (key === "scheme" && customOn(prefs)) body.custom = Object.assign({}, prefs.custom, { on: false });
  try {
    const data = await post("/api/settings", { settings: body });
    prefs = data.settings;
    drawPrefs();
    if (LOOK_KEYS.includes(key)) {
      applyLook(prefs);
      if (body.custom) drawCustom();
    }
    else if (key !== "start_tab") { applyFilters(prefs); draw(); }
    saved("Saved.");
  } catch (err) {
    drawPrefs();
    saved(err.message);
  }
}));

/* -- accent color -- */

async function saveSetting(key, value) {
  try {
    const data = await post("/api/settings", { settings: { [key]: value } });
    prefs = data.settings;
    saved("Saved.");
  } catch (err) {
    saved(err.message);
  }
}

// Dragging through the picker previews; letting go saves.
$("#accent").addEventListener("input", (e) =>
  applyLook(Object.assign({}, prefs, { accent: e.target.value })));
$("#accent").addEventListener("change", async (e) => {
  await saveSetting("accent", e.target.value);
  applyLook(prefs);
});
$("#accent-reset").addEventListener("click", async () => {
  await saveSetting("accent", "");
  applyLook(prefs);
});

/* -- which columns the Jobs table shows -- */

function drawColumnList() {
  const list = $("#column-list");
  list.textContent = "";
  const shown = (prefs.job_columns || JOB_COLUMNS).filter((k) => JOB_COLUMNS.includes(k));
  const order = [...shown, ...JOB_COLUMNS.filter((k) => !shown.includes(k))];
  const save = async (columns) => {
    await saveSetting("job_columns", columns);
    drawColumnList();
    orderJobsHead();
    draw();
  };
  order.forEach((key, i) => {
    const on = shown.includes(key);
    const name = $(`#jobs th[data-key="${key}"]`).textContent;
    const box = el("input", { type: "checkbox", checked: on });
    box.addEventListener("change", () => save(box.checked
      ? [...shown, key] : shown.filter((k) => k !== key)));
    const move = (d) => {
      const next = shown.slice();
      [next[i], next[i + d]] = [next[i + d], next[i]];
      save(next);
    };
    list.append(el("li", {},
      el("label", { class: "check" }, box, " " + name),
      el("button", { class: "ghost", type: "button", text: "↑",
        disabled: !on || i === 0, "aria-label": `Move ${name} left`,
        on: { click: () => move(-1) } }),
      el("button", { class: "ghost", type: "button", text: "↓",
        disabled: !on || i === shown.length - 1, "aria-label": `Move ${name} right`,
        on: { click: () => move(1) } })));
  });
}

let folderInfo = null;

function drawFolders(f) {
  folderInfo = f;
  const box = $("#folders");
  box.textContent = "";
  const row = (key, label, builtin, example) => el("div", { class: "folder" },
    el("span", { class: "label", text: label }),
    el("div", { class: "row" },
      el("input", { type: "text", name: key, value: f[key] || "",
                    placeholder: `No copy. For example ${example}`,
                    disabled: !f.editable, spellcheck: false }),
      el("button", { class: "ghost desktop-only", type: "button", text: "Open",
                     on: { click: () => openPath(f[key] || builtin) } })),
    el("span", { class: "help",
      text: `JobDesk's own copy is always in ${builtin}.`
        + (f[key] ? " A second copy goes to the folder above." : "") }));
  box.append(
    row("packets", "Application packets", f.packets_builtin, "D:\\Job Search\\Applications"),
    row("resumes", "Tailored resumes", f.resumes_builtin, "D:\\Job Search\\Resumes"));
  $("#save-folders").disabled = !f.editable;
  $("#folders-status").textContent = f.editable ? ""
    : "Finish setup first. Until then JobDesk is using the example profile.";
  $("#profile-dir").textContent = f.profile_dir || "No profile yet.";
  $("#open-profile").disabled = !f.profile_dir;
}

async function loadSettings() {
  try {
    const data = await get("/api/settings");
    prefs = data.settings;
    prefDefaults = data.defaults;
    drawPrefs();
    applyLook(prefs);
    drawSwatches();
    drawCustom();
    drawColumnList();
    drawFolders(data.folders);
  } catch (err) {
    saved(err.message);
  }
}

$("#save-folders").addEventListener("click", async () => {
  const status = $("#folders-status");
  const body = {};
  $$("#folders input[name]").forEach((input) => { body[input.name] = input.value; });
  status.textContent = "Saving…";
  try {
    const data = await post("/api/settings/folders", body);
    drawFolders(data.folders);
    status.textContent = "Saved. The next packet or resume is copied there.";
  } catch (err) {
    status.textContent = err.message;
  }
});

$("#open-profile").addEventListener("click", () =>
  openPath(folderInfo && folderInfo.profile_dir));

$("#reset-settings").addEventListener("click", async () => {
  if (!confirm("Put every setting on this page back to its default?")) return;
  try {
    // Saved themes are things made, not settings, so they stay.
    const reset = Object.assign({}, prefDefaults);
    delete reset.themes;
    const data = await post("/api/settings", { settings: reset });
    prefs = data.settings;
    drawPrefs();
    applyLook(prefs);
    drawSwatches();
    drawCustom();
    applyFilters(prefs);
    draw();
    $("#reset-status").textContent = "Done.";
  } catch (err) {
    $("#reset-status").textContent = err.message;
  }
});

$("#gear").addEventListener("click", () => show("settings"));

/* ================================================================== boot */

// `stay` keeps the page where it is, for the end of the wizard: its "your
// profile is written" message is on the Setup screen and should be read.
// The tier floors and point caps come from the scorer, so a re-weighted rule
// cannot leave this page describing the old one.
const TIER_WORDS = { A: "is worth applying to today", B: "is a good fit",
                     C: "is worth a look" };

// One color per part of the score, in the bar and in its key. Decoration
// only: the key says the same thing in words.
const PART_COLORS = ["#3b82f6", "#22a06b", "#8b5cf6", "#e0a32b", "#14b8a6",
                     "#ec4899", "#64748b", "#f97316"];

function drawScoring(scoring) {
  const parts = $("#score-parts");
  const bar = $("#points-bar");
  parts.textContent = "";
  bar.textContent = "";
  state.partMax = Object.fromEntries(scoring.parts.map((p) => [p.label, p.max]));
  scoring.parts.forEach((p, i) => {
    const color = PART_COLORS[i % PART_COLORS.length];
    const seg = el("i", { title: `${p.label}, up to ${p.max}` });
    seg.style.flexGrow = String(Math.max(1, p.max));
    seg.style.background = color;
    seg.style.animationDelay = `${i * 60}ms`;
    bar.append(seg);
    const dot = el("span", { class: "dot" });
    dot.style.background = color;
    parts.append(el("li", {}, dot,
      el("b", {}, p.label), ` up to ${p.max}${p.label === "Pay" ? ", when listed" : ""}`));
  });

  const legend = $("#tier-legend");
  legend.textContent = "";
  const select = $("#tier");
  select.length = 1;
  let ceiling = 100;
  scoring.tiers.forEach(({ tier, floor }) => {
    const range = ceiling === 100 ? `${floor} and up` : `${floor} to ${ceiling}`;
    if (TIER_WORDS[tier]) {
      legend.append(el("div", { class: `tier-card t-${tier}` },
        el("span", { class: `tier ${tier}`, text: tier }),
        el("b", { text: range }),
        el("span", { class: "note", text: TIER_WORDS[tier].replace(/^is /, "") })));
      select.append(el("option", { value: `${floor},${ceiling}` },
                       `${tier}, ${range}`));
    }
    ceiling = floor - 1;
  });
  const shown = scoring.tiers.find((t) => t.tier === "C");
  if (shown) {
    legend.append(el("div", { class: "tier-card t-hidden" },
      el("span", { class: "tier D", text: "D" }),
      el("b", { text: `Below ${shown.floor}` }),
      el("span", { class: "note", text: "hidden unless you ask for it" })));
    select.append(el("option", { value: `${shown.floor},100` }, "everything scored"));
  }
  select.append(el("option", { value: "0,100" }, "everything, F-tier included"));
}

async function boot(stay) {
  orderJobsHead();
  try {
    const s = await get("/api/status");
    $("#who").textContent = s.name
      ? `${s.name} · ${s.job_count} postings`
      : "no profile yet";
    if (s.problems.length) {
      banner(s.problems.join(" "), true);
    } else if (s.using_example) {
      banner("Running on the example profile. Every score below belongs to a "
           + "made-up candidate until you finish setup.", false, true);
    } else if (s.leftovers && s.leftovers.length) {
      banner("Packets are off until your profile is yours. "
           + s.leftovers.join(" "), true, true);
    } else {
      banner("");
    }
    if (s.settings) prefs = s.settings;
    if (s.scoring) drawScoring(s.scoring);
    applyLook(prefs);
    applyFilters(prefs);
    orderJobsHead();
    drawViews();
    // Setup is a one-time wizard. Once it has written a profile its tab only
    // invites someone to overwrite that profile, so it goes; the phone and
    // shortcut panels that used to sit under it are in Settings now.
    $('.tab[data-view="setup"]').hidden = s.configured;
    show(stay || (s.configured ? prefs.start_tab : "setup"));
    drawSteps();
  } catch (err) {
    banner(err.message, true);
    show("setup");
    drawSteps();
  }
  loadRuns();
  if ($("#view-applied").hidden) loadApplications();
}

boot();
