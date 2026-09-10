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
  if (view === "jobs" && !state.jobs.length) loadJobs();
  if (view === "applied") loadApplications();
  if (view === "archive" && !archive.loaded) searchArchive(0);
  if (view === "criteria") loadCriteria();
  if (view === "console") loadRuns();
  // Every field in the phone panel is a live reading -- the address changes
  // with the network, the firewall rule can be added in another window, and
  // "has a device arrived" is true only after one has. Re-read on each visit
  // rather than caching, the way the other tabs do with their data.
  if (view === "setup") loadPhone();
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

const state = { jobs: [], sort: "score", dir: "desc", open: null };

async function loadJobs() {
  try {
    const data = await get("/api/jobs");
    state.jobs = data.jobs;
    stamp(data);
    draw();
  } catch (err) {
    banner(err.message, true);
  }
}

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
  return sortRows(rows, state.sort, state.dir, (row, key) =>
    key === "salary" ? (row.salary_min || row.salary_max || 0) : row[key]);
}

function sortRows(rows, key, dir, pick) {
  const sign = dir === "desc" ? -1 : 1;
  const value = pick || ((row, k) => row[k]);
  return rows.slice().sort((a, b) => {
    let x = value(a, key), y = value(b, key);
    if (x === null || x === undefined) x = typeof y === "number" ? -1 : "";
    if (y === null || y === undefined) y = typeof x === "number" ? -1 : "";
    if (typeof x === "string" || typeof y === "string") {
      return sign * String(x).localeCompare(String(y));
    }
    return sign * (x - y);
  });
}

function money(job) {
  const fmt = (n) => "$" + Math.round(n / 1000) + "k";
  if (job.salary_min && job.salary_max) return fmt(job.salary_min) + "-" + fmt(job.salary_max);
  if (job.salary_min) return fmt(job.salary_min) + "+";
  if (job.salary_max) return "to " + fmt(job.salary_max);
  return "—";
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
      cell(age(job)), cell(money(job)), cell(job.source));
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
  const td = el("td", { colSpan: 7 });
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

sortable(".grid#jobs", (key) => {
  state.dir = state.sort === key && state.dir === "desc" ? "asc" : "desc";
  state.sort = key;
  draw();
  return state.dir;
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

let criteriaTypes = {};

async function loadCriteria() {
  const form = $("#criteria");
  try {
    const data = await get("/api/targeting");
    criteriaTypes = data.types;
    $("#criteria-file").textContent = data.file;
    form.textContent = "";
    Object.entries(data.values).forEach(([key, value]) => {
      const label = el("label", {},
        el("span", { text: key.replace(/_/g, " ") }),
        el("span", { class: "key", text: key }));

      let input;
      if (data.types[key] === "list") {
        input = el("textarea", {
          value: (value || []).join("\n"),
          rows: Math.min(10, Math.max(3, (value || []).length)),
        });
      } else {
        input = el("input", {
          type: data.types[key] === "int" ? "number" : "text",
          value: value === null || value === undefined ? "" : value,
        });
      }
      input.name = key;
      label.append(input);
      form.append(label);
    });
  } catch (err) {
    form.textContent = "";
    $("#criteria-file").textContent = err.message;
  }
}

$("#save-criteria").addEventListener("click", async () => {
  const changes = {};
  Array.from($("#criteria").elements).forEach((input) => {
    if (!input.name) return;
    if (criteriaTypes[input.name] === "list") {
      changes[input.name] = input.value.split("\n").map((s) => s.trim()).filter(Boolean);
    } else if (criteriaTypes[input.name] === "int") {
      changes[input.name] = input.value === "" ? 0 : Number(input.value);
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
    show("jobs");
  } catch (err) {
    status.textContent = err.message;
  }
});

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
  boot();
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

/* ================================================================== boot */

async function boot() {
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
    show(s.configured ? "jobs" : "setup");
    drawSteps();
  } catch (err) {
    banner(err.message, true);
    show("setup");
    drawSteps();
  }
  loadRuns();
}

boot();
