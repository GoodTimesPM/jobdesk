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

async function call(path, options) {
  const res = await fetch(path, options);
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(data.error || `${res.status} from ${path}`);
  return data;
}

const get = (path) => call(path);
const post = (path, body) => call(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

function banner(text, bad) {
  const box = $("#banner");
  box.textContent = text || "";
  box.hidden = !text;
  box.classList.toggle("bad", !!bad);
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return isNaN(d) ? iso : d.toLocaleDateString();
}

/* ---------------------------------------------------------------- views */

function show(view) {
  $$(".view").forEach((v) => { v.hidden = v.id !== "view-" + view; });
  $$(".tab").forEach((t) => t.setAttribute("aria-current",
    String(t.dataset.view === view)));
  $("#gear").setAttribute("aria-current", String(view === "settings"));
  if (view === "jobs" && !state.jobs.length) loadJobs();
  if (view === "applied") loadApplications();
  if (view === "archive" && !archive.loaded) searchArchive(0);
  if (view === "criteria") loadCriteria();
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
  if (!runs.list.length) box.append(el("li", { text: "no runs yet" }));
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
    $("#out").textContent += "\n" + err.message;
    return;
  }
  if (data.missing) { $("#out").textContent = "that run has scrolled out of the list"; return; }

  if (data.lines.length) {
    runs.next = data.next;
    const out = $("#out");
    out.textContent += data.lines.join("\n") + "\n";
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
      banner(`Packet blocked: ${(result.problems || []).join(" ")}`, true);
    } else if (result.ok) {
      banner(`Packet built. Open APPLY.md, work through it, then mark it `
           + `applied on the Applied tab.`);
    } else {
      banner((result.problems || ["the packet did not build"]).join(" "), true);
    }
  }
}

function startRun(body, message) {
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
                market: {}, marketIndex: null };

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
    stamp(data);
    draw();
    loadMarket();
  } catch (err) {
    banner(err.message, true);
  }
}

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
  $("#counts").textContent =
    `${state.jobs.length} postings · ${parts.join(" · ")}` +
    (floor && floor.capped
      ? ` · nothing under ${floor.lowest} made the cache (floor is ${floor.configured})`
      : "") +
    (ran ? ` · scored ${ran.toLocaleString()}` : "");
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

function visible() {
  const q = $("#search").value.trim().toLowerCase();
  const [lo, hi] = scoreRange();
  const hideApplied = $("#hide-applied").checked;
  const hidePrepared = $("#hide-prepared").checked;
  const remoteOnly = $("#remote-only").checked;

  const rows = state.jobs.filter((j) => {
    const score = j.score || 0;
    if (score < lo || score > hi) return false;
    if (hideApplied && j.applied) return false;
    if (hidePrepared && j.prepared) return false;
    if (remoteOnly && !j.remote) return false;
    if (!q) return true;
    return `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q);
  });
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
  const text = m.direction === "flat" ? "even" : glyph + Math.abs(m.delta) + "%";
  td.append(el("span", {
    class: "mktv " + m.direction + (m.confident ? "" : " thin"),
    text: text,
    title: `Better than ${m.percentile}% of the ${m.peers} postings in `
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

function draw() {
  const rows = visible();
  const body = $("#rows");
  body.textContent = "";

  rows.forEach((job) => {
    const tr = el("tr", { class: "row" + (job.applied ? " applied" : "") });
    tr.dataset.uid = job.uid;

    const score = el("td", { class: "score", text: String(job.score) });
    score.append(el("span", { class: "tier " + job.tier, text: job.tier }));
    if (job.was !== undefined && job.was !== job.score) {
      const up = job.score > job.was;
      score.append(el("span", { class: "moved " + (up ? "up" : "down"),
        text: (up ? "▲" : "▼") + Math.abs(job.score - job.was) }));
    }

    const title = el("td", {}, job.title);
    if (job.applied) title.append(el("span", { class: "pill", text: job.app_status }));
    else if (job.prepared) title.append(el("span", { class: "pill", text: "packet ready" }));

    tr.append(score, title, cell(job.company),
      cell(job.remote ? "Remote" : (job.location || "—")),
      cell(age(job)), marketCell(job), salaryCell(job), cell(job.source));
    body.append(tr);

    if (state.open === job.uid) body.append(detailRow(job));
  });

  $("#jobs-empty").hidden = rows.length > 0;
  $("#jobs-empty").textContent = state.jobs.length
    ? "Nothing matches those filters."
    : "No postings yet. Press “Run the radar” above.";
}

/* -- the expanded posting -- */

function jdBlocks(blocks) {
  const box = el("div", { class: "jd" });
  blocks.forEach((block) => {
    if (block.kind === "heading") box.append(el("h4", { text: block.text }));
    else if (block.kind === "list") {
      box.append(el("ul", {}, block.items.map((i) => el("li", { text: i }))));
    } else box.append(el("p", { text: block.text }));
  });
  return box;
}

function detailRow(job) {
  const td = el("td", { colSpan: 8 });
  const actions = el("div", { class: "actions" });

  actions.append(el("a", { href: job.url, target: "_blank",
    rel: "noopener noreferrer", text: "Open the posting ↗" }));

  // Pasting beats fetching whenever someone bothers to do it: a careers site
  // that hands a script a login page hands this textarea the real posting.
  const paste = el("div", { class: "jd-paste" }, el("textarea", {
    rows: 8, placeholder: "Paste the job description here, then build.",
  }));
  paste.hidden = true;

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
                          if (!paste.hidden) paste.querySelector("textarea").focus(); } } }));

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

  const breakdown = marketPanel(job);
  if (breakdown) td.append(breakdown);

  td.append(paste);

  const jd = el("div", { class: "jd", text: "Loading the description…" });
  td.append(jd);

  get("/api/job?uid=" + encodeURIComponent(job.uid)).then((full) => {
    jd.replaceWith(full.blocks && full.blocks.length
      ? jdBlocks(full.blocks)
      : el("div", { class: "jd raw",
          text: "This posting arrived without a description. Open it above, "
              + "copy the text, and paste it in with the button." }));
    (full.guards || []).forEach((g) => {
      const line = el("div", { class: "guard", text: g.message });
      line.dataset.level = g.level;
      guards.append(line);
    });
  }).catch((err) => { jd.textContent = err.message; });

  const tr = el("tr", { class: "detail" });
  tr.append(td);
  return tr;
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
            + (m.direction === "flat" ? " even" : Math.abs(m.delta) + "%") }));
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
  state.open = state.open === row.dataset.uid ? null : row.dataset.uid;
  draw();
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
  draw();
});

["#search", "#score-min", "#score-max", "#hide-applied", "#hide-prepared",
 "#remote-only"].forEach((sel) => $(sel).addEventListener("input", draw));

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
    banner(`${plural(data.moved, "posting")} changed score. Nothing was written.`);
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
    drawFunnel(data);
    drawReport(data);
    if ($("#app-status").options.length <= 1) {
      apps.statuses.forEach((s) =>
        $("#app-status").append(el("option", { value: s, text: s })));
    }
    drawApps();
  } catch (err) { banner(err.message, true); }
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
  tiles.forEach(([label, n]) => {
    box.append(el("div", { class: "stat" + (n ? "" : " zero") },
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
  return sortRows(rows, apps.sort, apps.dir);
}

function drawApps() {
  const rows = visibleApps();
  const body = $("#app-rows");
  body.textContent = "";

  rows.forEach((row) => {
    const tr = el("tr", { class: "row" + (row.follow_up_overdue ? " overdue" : "") });
    tr.dataset.id = row.id;

    const company = el("td", {}, row.company);
    const kind = row.status === "prepared" ? "prepared"
      : (["rejected", "ghosted", "withdrawn"].includes(row.status) ? "dead" : "sent");
    company.append(el("span", { class: "badge " + kind, text: kind === "prepared"
      ? "not sent" : (kind === "dead" ? row.status : "sent") }));

    const status = el("td", {},
      el("select", {
        on: {
          click: (e) => e.stopPropagation(),
          change: (e) => changeStatus(row.id, e.target.value),
        },
      }, apps.statuses.map((s) =>
        el("option", { value: s, text: s, selected: s === row.status }))));

    tr.append(company, cell(row.role), status,
      cell(when(row.applied_on), "when"),
      cell(row.days_since === null ? "—" : row.days_since + "d", "num"),
      cell(row.follow_up_due ? when(row.follow_up_due) : "—", "when"),
      cell(row.source || "—"),
      cell(row.score === null || row.score === undefined ? "—" : String(row.score), "num"));
    body.append(tr);

    if (apps.open === row.id) body.append(appDetail(row));
  });

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
    const body = el("p", { class: "raw", text: data.texts[name] });
    body.hidden = !open;
    const head = el("button", {
      class: "disclose", text: (open ? "▾ " : "▸ ") + (PACKET_TITLES[name] || name),
      on: { click: (e) => {
        e.stopPropagation();
        body.hidden = !body.hidden;
        head.textContent = (body.hidden ? "▸ " : "▾ ") + (PACKET_TITLES[name] || name);
      } },
    });
    box.append(head, body);
  });
  return box;
}

function appDetail(row) {
  const td = el("td", { colSpan: 8 });
  const actions = el("div", { class: "actions" });

  if (row.url) {
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

  const body = $("#arc-rows");
  body.textContent = "";
  data.rows.forEach((row) => {
    const title = el("td", {},
      row.url ? el("a", { href: row.url, target: "_blank",
        rel: "noopener noreferrer", text: row.title || "(untitled)" })
              : (row.title || "(untitled)"));
    body.append(el("tr", {},
      cell(String(row.score), "num"), title, cell(row.company || "—"),
      cell(when(row.first_seen), "when"), cell(when(row.last_seen), "when"),
      cell(row.live_days === null ? "—" : row.live_days + "d", "num")));
  });

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

function criteriaInput(field) {
  const { key, kind, value } = field;
  if (kind === "list" || kind === "weights") {
    const lines = kind === "list"
      ? (value || [])
      : Object.entries(value || {}).map(([skill, pts]) => `${skill}: ${pts}`);
    return el("textarea", {
      name: key, value: lines.join("\n"),
      rows: Math.min(10, Math.max(3, lines.length)),
      placeholder: kind === "weights" ? "sql: 6\npython: 4" : "one per line",
      spellcheck: false,
    });
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
    data.sections.forEach((s) => {
      const box = el("fieldset", { class: "crit-section" },
        el("h3", { text: s.title }),
        s.intro ? el("p", { class: "note", text: s.intro }) : null);
      const grid = el("div", { class: "crit-fields" });
      s.fields.forEach((f) => {
        const field = el("label", { class: "crit-field" },
          el("span", { class: "label", text: f.label }),
          el("span", { class: "help", text: f.help }),
          criteriaInput(f));
        field.dataset.kind = f.kind;
        grid.append(field);
      });
      box.append(grid);
      if (s.outro) box.append(el("p", { class: "outro", text: s.outro }));
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
    status.textContent = `Saved. ${plural(data.moved, "posting")} changed score.`;
    banner(`Criteria saved. ${plural(data.moved, "posting")} changed score; `
         + "the arrows next to each score show which way.");
    show("jobs");
  } catch (err) {
    status.textContent = err.message;
  }
});

$("#open-criteria-file").addEventListener("click", () => openPath(criteriaFile));

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

const LOOK_KEYS = ["theme", "text_size", "density"];
let prefs = {
  jobs_sort: "newest", score_min: 45, score_max: 100, hide_applied: true,
  hide_prepared: false, remote_only: false, start_tab: "jobs",
  theme: "system", text_size: 100, density: "normal",
};
let prefDefaults = Object.assign({}, prefs);   // replaced by the server's copy

function applyLook(p) {
  const root = document.documentElement;
  if (p.theme === "light" || p.theme === "dark") root.dataset.theme = p.theme;
  else delete root.dataset.theme;
  root.dataset.size = String(p.text_size);
  root.dataset.density = p.density;
  const look = {};
  LOOK_KEYS.forEach((k) => { look[k] = p[k]; });
  try { localStorage.setItem("jobdesk.look", JSON.stringify(look)); } catch { /* private mode */ }
  // The phone's status bar takes this colour, so it follows the theme too.
  const meta = document.querySelector('meta[name="theme-color"]');
  const card = getComputedStyle(root).getPropertyValue("--card").trim();
  if (meta && card) meta.content = card;
}

function applyFilters(p) {
  $("#score-min").value = p.score_min;
  $("#score-max").value = p.score_max;
  $("#hide-applied").checked = !!p.hide_applied;
  $("#hide-prepared").checked = !!p.hide_prepared;
  $("#remote-only").checked = !!p.remote_only;
  setOrder(p.jobs_sort);
}

function drawPrefs() {
  $$("[data-pref]").forEach((input) => {
    const value = prefs[input.dataset.pref];
    if (input.type === "checkbox") input.checked = !!value;
    else input.value = String(value);
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
  try {
    const data = await post("/api/settings", { settings: { [key]: value } });
    prefs = data.settings;
    drawPrefs();
    if (LOOK_KEYS.includes(key)) applyLook(prefs);
    else if (key !== "start_tab") { applyFilters(prefs); draw(); }
    saved("Saved.");
  } catch (err) {
    drawPrefs();
    saved(err.message);
  }
}));

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
    const data = await post("/api/settings", { settings: prefDefaults });
    prefs = data.settings;
    drawPrefs();
    applyLook(prefs);
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
async function boot(stay) {
  try {
    const s = await get("/api/status");
    $("#who").textContent = s.name
      ? `${s.name} · ${s.job_count} postings`
      : "no profile yet";
    if (s.problems.length) {
      banner(s.problems.join(" "), true);
    } else if (s.using_example) {
      banner("Running on the example profile. Every score below belongs to a "
           + "made-up candidate until you finish setup.");
    } else {
      banner("");
    }
    if (s.settings) prefs = s.settings;
    applyLook(prefs);
    applyFilters(prefs);
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
}

boot();
