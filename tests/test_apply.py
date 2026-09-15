"""Self-checks for Assisted Apply.

    py tests/test_apply.py            everything
    py tests/test_apply.py --letter   just the cover-letter gate

No test framework -- same choice as the other two sub-projects. These run in
under a second, touch no network, and write only to a temp directory.

Pinned to `profile.example/` before anything is imported, for the same reason
the engine suite is: which paragraph a letter selects and which answer a
question matches are claims about one profile's content. The candidate below
is Wren Adeyemi, who does not exist.

The letter tests carry the most weight: they are what stands between "the
cover letter is assembled from approved paragraphs" being true and it being a
comment in a TOML file.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["JOBDESK_PROFILE"] = str(ROOT / "profile.example")

from jobdesk.apply import answers as answers_mod
from jobdesk.apply import candidates as candidates_mod
from jobdesk.apply import config, discord as discord_mod, guard, jdtext, letter as letter_mod, packet
from jobdesk.apply import main as main_mod
from jobdesk.apply.applog import Application, Log, role_key

PASS, FAIL = 0, 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f"  ({detail})" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


# -- fixtures ---------------------------------------------------------------

RESUME = """Wren A. Adeyemi
Denver, CO | wren.adeyemi@example.com | 303-555-0142 | linkedin.com/in/wren-adeyemi-example

SUMMARY
Accounting graduate with two years of full-cycle accounting experience across
general ledger, reconciliations, and month-end close.

EDUCATION
Bachelor of Science: Accounting - University of Colorado Denver

SKILLS
Accounting Systems: QuickBooks (Online and Desktop), NetSuite, Sage Intacct, Bill.com
Core Accounting: General Ledger, Accounts Payable, Accounts Receivable, Reconciliation
Reporting & Analysis: Excel (Pivot Tables, Power Query), SQL, Power BI
Other: Google Workspace

EXPERIENCE

Accounting Assistant | Front Range Property Group - Denver, CO | June 2024 - Present
- Own the month-end close for 6 property entities, posting recurring and adjusting journal entries and reconciling 22 balance sheet accounts to a 5-business-day deadline.
- Reconcile 9 operating and escrow bank accounts monthly, researching and clearing an average of 15 exceptions per cycle before close.
- Process roughly 400 vendor invoices monthly in Bill.com, matching each against its purchase order and receiving record before releasing it into the weekly check run.
- Build the monthly budget-to-actual package for 6 properties in Excel, writing the variance narrative for any line more than 5% off plan.
- Maintain a rolling 13-week cash forecast updated weekly from the AP aging and lease receipts.
- Prepare the annual audit schedules; the external auditors' follow-up list fell from 31 items in 2024 to 12 in 2025.
- Supported the migration from QuickBooks Desktop to NetSuite, validating opening balances for 6 entities and correcting 140 miscoded historical transactions before cutover.

Accounts Payable Clerk | Meridian Dental Partners - Lakewood, CO | August 2023 - June 2024
- Processed 250 to 300 invoices monthly across 11 clinic locations, coding each to its own general ledger.
- Handled vendor inquiries for 180 suppliers directly, including pricing and short-ship disputes.
- Ran the annual 1099 process for 64 contractors, filing on time both years.

PROJECTS

Vendor Statement Reconciliation - Power Query workbook
- Replaced a line-by-line comparison that took most of a day with one match that outputs only the differences.

VITA Volunteer Tax Preparation - IRS certified, 2 seasons
- Prepared and filed roughly 60 individual returns a season, every one passing second-reviewer quality check.
"""

JD = """Staff Accountant

About Us
Meadowlark Health Partners is a Denver-based physician group. We have been
running clinics here since 2004 and our team is about 300 people.

What You'll Do
- Own the month-end close and post journal entries in NetSuite
- Reconcile balance sheet and bank accounts every period
- Build reporting in Excel for the practice managers

Requirements
- Bachelor's degree in Accounting
- 2 years of general ledger experience
- Strong Excel and reconciliation skills

Preferred
- Bill.com
- Sage Intacct exposure
"""


def temp_log() -> Log:
    folder = Path(tempfile.mkdtemp(prefix="assisted-apply-test-"))
    return Log(folder / "applications.json")


def app(**kwargs) -> Application:
    base = dict(id="x", company="Acme Health", role="Business Systems Analyst",
                url="https://acme.example/jobs/1", uid="uid1",
                dedupe_key="acmehealthbusinesssystemsanalyst")
    base.update(kwargs)
    return Application(**base)


# -- tests ------------------------------------------------------------------

def test_letter() -> None:
    section("Cover letter -- the gate")

    check("numbers are normalized across commas",
          letter_mod.numbers_in("about 2,000 records") == {"2000"},
          str(letter_mod.numbers_in("about 2,000 records")))
    check("spelled numbers fold to digits",
          "4" in letter_mod.numbers_in("four clinic locations"))

    skills = letter_mod.resume_skills(RESUME)
    check("skills are read off the resume",
          "NetSuite" in skills and "Bill.com" in skills, str(skills))
    check("parenthetical fragments are not skills",
          not any(x.startswith("Power Query") or x.startswith("Online and")
                  for x in skills),
          str(skills))
    check("EXPERIENCE is not swallowed into SKILLS",
          not any("Front Range" in x for x in skills), str(skills))

    tools = letter_mod.pick_tools(RESUME, JD)
    check("tools are resume-and-JD intersection", set(tools) <= set(skills), str(tools))
    check("tools the JD asked for are chosen",
          "NetSuite" in tools or "Excel" in tools, str(tools))
    check("a skill the JD never mentions is not named",
          "Power BI" not in tools, str(tools))

    name, header = letter_mod.resume_contact(RESUME)
    check("contact comes from the resume, not a second copy",
          name == "Wren A. Adeyemi" and "303-555-0142" in header[1])

    built = letter_mod.build(company="Meadowlark Health", role="Staff Accountant",
                             family="accounting", resume_text=RESUME, jd_text=JD)
    problems = letter_mod.verify(built, RESUME, JD)
    check("a normal letter passes verification", not problems, str(problems))
    check("it has an opening, evidence, a bridge and a close",
          len(built.paragraphs) >= 4, f"{len(built.paragraphs)} paragraphs")
    check("the company name is interpolated",
          "Meadowlark Health" in built.body)
    check("the role title is interpolated",
          "Staff Accountant" in built.body)

    # The close is the story an accounting letter is built around, and it may
    # only be told when the resume shipping with it carried the numbers.
    check("an accounting letter tells the close story",
          any(p.id.startswith("ev.close") for p in built.paragraphs),
          str([p.id for p in built.paragraphs]))

    # The core rule: a claim the resume does not make cannot appear. Checked on
    # the billing family, where the AP paragraph is the one in play.
    thin_resume = RESUME.replace("roughly 400 vendor invoices", "vendor invoices")
    thin = letter_mod.build(company="Meadowlark Health", role="AP Specialist",
                            family="billing", resume_text=thin_resume, jd_text=JD)
    check("an unsupported-number paragraph is dropped, not sent",
          any(pid == "ev.ap.frpg" for pid, _ in thin.dropped),
          str(thin.dropped))
    check("the letter still builds after a drop",
          not letter_mod.verify(thin, thin_resume, JD))

    # Every family must be able to open. The number gate folds spelled-out
    # numbers to digits, so a number word used as a pronoun -- "rather than
    # feeding one" -- reads as a claim about the number 1, no resume states 1,
    # and that family's only opening paragraph gets dropped. The letter then
    # fails to build at all, which is what happened to the accounting family
    # in the shipped example profile. Cheap to check, silent when it breaks.
    for family in ("accounting", "billing", "payroll", "audit",
                   "analysis", "operations"):
        letter = letter_mod.build(company="Meadowlark Health", role="Staff Accountant",
                                  family=family, resume_text=RESUME, jd_text=JD)
        check(f"the {family} family keeps an opening paragraph",
              any(p.id.startswith("open.") for p in letter.paragraphs),
              str(letter.dropped))

    # Tampering with rendered text must fail even though the template exists.
    tampered = letter_mod.build(company="Meadowlark Health", role="Staff Accountant",
                                family="accounting", resume_text=RESUME, jd_text=JD)
    tampered.paragraphs[0].text += " I have seven years of accounting experience."
    problems = letter_mod.verify(tampered, RESUME, JD)
    check("edited paragraph text fails the template check",
          any("approved template" in p for p in problems), str(problems))
    check("and the invented number is caught separately",
          any("7" in p and "does not say" in p for p in problems), str(problems))

    # A tool that is on neither side must be rejected.
    bad = letter_mod.build(company="Acme", role="Staff Accountant",
                           family="accounting", resume_text=RESUME, jd_text=JD)
    bad.tools.append("Snowflake")
    problems = letter_mod.verify(bad, RESUME, JD)
    check("a tool on neither the resume nor the JD is rejected",
          len(problems) == 2, str(problems))

    # A user-written note is exempt from the template check but still shipped.
    noted = letter_mod.build(company="Meadowlark Health", role="Staff Accountant",
                             family="accounting", resume_text=RESUME, jd_text=JD,
                             note="I closed the books for a clinic group before.")
    check("a user note is included",
          "clinic group" in noted.body)
    check("a user note does not fail verification",
          not letter_mod.verify(noted, RESUME, JD))

    # Two paragraphs about the same job read as one story told twice.
    ev_ids = [p.id for p in built.paragraphs if p.id.startswith("ev.")]
    employers = [e.get("employer") for e in letter_mod.load_content()["evidence"]
                 if e["id"] in ev_ids]
    check("evidence does not name the same employer twice",
          len(employers) == len(set(employers)), str(ev_ids))
    # ...but when only one employer's paragraphs match, a repeat beats a short
    # letter, so the deferred one must come back rather than vanish.
    only = letter_mod.build(company="A", role="B", family="frpg-only",
                            resume_text=RESUME, jd_text=JD,
                            content=_frpg_only_content())
    check("but a repeat beats a short letter when nothing else qualifies",
          len([p for p in only.paragraphs if p.id.startswith("ev.")]) == 2,
          str([p.id for p in only.paragraphs]))

    # Skills worth a keyword match, not worth a sentence.
    office_jd = JD + "\nProficiency with Google Workspace required.\n"
    check("a stoplisted skill is never named in prose",
          "Google Workspace" not in letter_mod.pick_tools(
              RESUME, office_jd, stoplist=["Google Workspace"]),
          str(letter_mod.pick_tools(RESUME, office_jd,
                                    stoplist=["Google Workspace"])))
    check("the live content file carries a stoplist",
          "Google Workspace" in letter_mod.load_content()["meta"]["tool_stoplist"])
    check("and the built letter does not name it",
          "Google Workspace" not in letter_mod.build(
              company="Meadowlark Health", role="Staff Accountant",
              family="accounting", resume_text=RESUME, jd_text=office_jd).body)

    # The PDF is the copy that gets uploaded, so check the shipped artifact the
    # way the Resume Engine checks its own: read the text layer back out.
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = letter_mod.write_pdf(built, Path(tmp) / "COVER_LETTER.pdf")
        check("the letter renders to PDF", pdf_path is not None and pdf_path.exists())
        if pdf_path:
            import fitz
            doc = fitz.open(str(pdf_path))
            text = letter_mod.normalize_pdf_text("".join(p.get_text() for p in doc))
            check("the PDF is one page", doc.page_count == 1, str(doc.page_count))
            check("every paragraph survives into the PDF text",
                  all(letter_mod.normalize_pdf_text(p.text) in text
                      for p in built.paragraphs))
            check("the signature block is there",
                  built.sign_off in text and built.name in text)
            meta = doc.metadata
            check("the PDF names no toolchain in its properties",
                  not any(meta.get(k) for k in
                          ("producer", "creator", "author", "title", "subject",
                           "keywords")),
                  str(meta))
            doc.close()

        docx_path = letter_mod.write_docx(built, Path(tmp) / "COVER_LETTER.docx")
        check("the letter renders to DOCX",
              docx_path is not None and docx_path.exists())
        if docx_path:
            import zipfile
            with zipfile.ZipFile(docx_path) as archive:
                core = archive.read("docProps/core.xml").decode("utf-8")
                app = archive.read("docProps/app.xml").decode("utf-8")
            # python-docx's default template signs every document it makes.
            check("the DOCX does not say python-docx wrote it",
                  "python-docx" not in core, core)
            check("and does not carry the template's 2013 date",
                  "2013" not in core, core)
            check("and does not claim to be Word",
                  "Microsoft" not in app, app)
            check("the author is the candidate", built.name in core)

    # Family selection changes the opening.
    billing = letter_mod.build(company="Acme", role="AP Specialist",
                               family="billing", resume_text=RESUME, jd_text=JD)
    check("family picks a different opening",
          billing.paragraphs[0].id == "open.billing",
          billing.paragraphs[0].id)
    check("every paragraph id in the letter is from the content file",
          all(p.id in {e["id"] for key in ("opening", "evidence", "bridge", "close")
                       for e in letter_mod.load_content()[key]} or p.user_written
              for p in built.paragraphs))


def _frpg_only_content() -> dict:
    """The real content with every evidence paragraph pinned to one employer."""
    content = letter_mod.load_content()
    content["evidence"] = [dict(e, families=["frpg-only"], employer="frpg")
                           for e in content["evidence"]
                           if e.get("employer") == "frpg"]
    for section in ("opening", "bridge", "close"):
        content[section] = [dict(e, families=["*"]) for e in content[section]]
    return content


def test_guard() -> None:
    section("Guard rules -- what stops an application")

    log = temp_log()
    clear = guard.run(log, company="Acme Health", role="Business Systems Analyst",
                      uid="uid1")
    check("a clean slate fires nothing", not clear, str(clear))

    applied = app(id="2026-08-11_Acme_BSA")
    log.add(applied)
    log.mark_applied(applied)

    same = guard.run(log, company="Acme Health", role="Business Systems Analyst",
                     uid="uid1", dedupe_key="acmehealthbusinesssystemsanalyst")
    check("the same req is blocked",
          any(c.rule == "same-req" and c.level == guard.BLOCK for c in same),
          str(same))

    other = guard.run(log, company="Acme Health", role="Data Analyst", uid="uid2")
    check("a different role at the same company is allowed",
          not guard.blocking(other), str(other))
    check("but it notes the open application",
          any(c.rule == "concurrency" for c in other), str(other))

    second = app(id="2026-08-11_Acme_DA", role="Data Analyst", uid="uid2",
                 dedupe_key="acmehealthdataanalyst")
    log.add(second)
    log.mark_applied(second)
    third = guard.run(log, company="Acme Health", role="Support Specialist", uid="uid3")
    check("the concurrency cap blocks a third",
          any(c.rule == "concurrency" and c.level == guard.BLOCK for c in third),
          str(third))

    check("role_key collapses level suffixes",
          role_key("Business Analyst II") == role_key("Business Analyst 2"))

    # Cooldown on the same role after a rejection.
    log2 = temp_log()
    rejected = app(id="old", uid="old-uid", dedupe_key="old-key")
    log2.add(rejected)
    log2.mark_applied(rejected, when=(date.today() - timedelta(days=30)).isoformat())
    log2.set_status(rejected, "rejected")
    cooldown = guard.run(log2, company="Acme Health",
                         role="Business Systems Analyst", uid="new-uid")
    check("a rejected role inside the cooldown is blocked",
          any(c.rule == "role-cooldown" and c.level == guard.BLOCK for c in cooldown),
          str(cooldown))

    log3 = temp_log()
    old = app(id="ancient", uid="a1", dedupe_key="k1")
    log3.add(old)
    log3.mark_applied(old, when=(date.today() - timedelta(days=400)).isoformat())
    log3.set_status(old, "rejected")
    stale = guard.run(log3, company="Acme Health", role="Business Systems Analyst",
                      uid="a2")
    check("past the cooldown it does not block",
          not any(c.rule == "role-cooldown" for c in stale), str(stale))

    agency = guard.run(temp_log(), company="Acme Health", role="Analyst",
                       flags=["agency-posting", "fresh"])
    check("an agency flag is surfaced",
          any(c.rule == "agency" for c in agency), str(agency))

    log4 = temp_log()
    direct = app(id="direct", uid="d1", dedupe_key="dk1")
    log4.add(direct)
    log4.mark_applied(direct)
    dupe = guard.run(log4, company="Acme Health", role="Data Analyst",
                     uid="d2", flags=["agency-posting"])
    check("an agency submission on top of a direct one is blocked",
          any(c.rule == "agency" and c.level == guard.BLOCK for c in dupe), str(dupe))


def test_applog() -> None:
    section("Application log")

    log = temp_log()
    row = log.add(app(id="p1"))
    check("a prepared packet is not an open application", not row.is_open)
    check("prepared_on is stamped", row.prepared_on == date.today().isoformat())

    log.mark_applied(row)
    check("applying opens it", row.is_open)
    expected = (date.today() + timedelta(days=config.FOLLOW_UP_DAYS)).isoformat()
    check("the follow-up date is day 7", row.follow_up_due == expected,
          row.follow_up_due)
    check("it is not overdue today", not row.follow_up_overdue)

    row.applied_on = (date.today() - timedelta(days=10)).isoformat()
    row.follow_up_due = (date.today() - timedelta(days=3)).isoformat()
    check("an old application is flagged overdue", row.follow_up_overdue)
    check("and shows up in the due list", log.follow_ups_due() == [row])

    # Item 10's push path: a follow-up fires once per due date.
    check("an unpushed overdue follow-up needs a ping",
          log.unpinged_follow_ups_due() == [row])
    log.mark_follow_up_pinged(row)
    check("once pinged, it drops off the un-pinged list",
          log.unpinged_follow_ups_due() == [])
    check("but it still shows up as due, just already pinged",
          log.follow_ups_due() == [row])
    reloaded_ping = Log(log.path)
    check("the pinged marker survives a round trip through disk",
          reloaded_ping.rows[0].follow_up_pinged == row.follow_up_due)

    # A resubmission moves follow_up_due forward, which is a new due date --
    # the old ping should not silence it.
    log.mark_applied(row, when=(date.today() - timedelta(days=1)).isoformat())
    check("a moved-forward due date is not the one that was pinged",
          row.follow_up_pinged != row.follow_up_due)

    log.set_status(row, "interview", note="phone screen Thursday")
    check("status moves", row.status == "interview")
    check("history is kept", len(row.history) >= 3, str(row.history))
    check("notes accumulate", "Thursday" in row.notes)

    reloaded = Log(log.path)
    check("it round-trips through disk", reloaded.rows[0].status == "interview")
    check("history survives the round trip",
          reloaded.rows[0].history == row.history)

    by_source = reloaded.by_source()
    check("a sourceless row is bucketed, not dropped", "unknown" in by_source,
          str(by_source))
    sent, responses = by_source.get("unknown", (0, 0))
    check("the funnel counts a response", (sent, responses) == (1, 1),
          f"{sent}/{responses}")
    check("prepared packets are not counted as applications sent",
          Log(log.path).by_source().get("unknown", (0, 0))[0] == 1)

    bad = temp_log()
    bad.path.write_text("{ not json", encoding="utf-8")
    recovered = Log(bad.path)
    check("a corrupt log does not crash, and is moved aside",
          recovered.rows == [] and bad.path.with_suffix(".corrupt.json").exists())

    rej_log = temp_log()
    interview_row = rej_log.add(app(id="r1", company="Gamma Co"))
    rej_log.mark_rejected(interview_row, stage="interview",
                          reason="went with an internal candidate")
    check("a rejection records the stage reached",
          interview_row.rejected_stage == "interview")
    check("...and the reason", "internal candidate" in interview_row.rejected_reason)
    check("...and today's date",
          interview_row.rejected_on == date.today().isoformat())
    check("status moves to rejected", interview_row.status == "rejected")

    default_stage_row = rej_log.add(app(id="r2", company="Delta Co"))
    rej_log.mark_applied(default_stage_row)
    rej_log.mark_rejected(default_stage_row)
    check("rejection defaults the stage to whatever status it was already at",
          default_stage_row.rejected_stage == "applied",
          default_stage_row.rejected_stage)

    check("rejection report groups by stage reached",
          rej_log.rejection_report() == {"interview": 1, "applied": 1},
          str(rej_log.rejection_report()))

    # A posting that closes before you reach the form leaves a packet behind
    # and no application. The row has to be able to go, or the funnel counts a
    # submission that never happened and the guard holds a slot against the
    # company forever.
    gone = temp_log()
    keep = gone.add(app(id="k1", company="Keep Co"))
    drop = gone.add(app(id="d1", company="Drop Co"))
    gone.remove(drop)
    check("removing a row takes it out of the log",
          [r.id for r in gone.rows] == [keep.id],
          str([r.id for r in gone.rows]))
    check("the removal is written to disk, not just to memory",
          [r.id for r in Log(gone.path).rows] == [keep.id])
    check("a removed row stops being found by id", gone.get("d1") is None)
    check("...and stops counting against the funnel",
          gone.funnel()["prepared"] == 1, str(gone.funnel()))


def test_notion_sync() -> None:
    section("Notion sync -- Job Radar Tracker Applied Date -> OG tracker + auto")

    from jobdesk.apply import notion_sync

    # 2026-08-26: exactly REMOTE / <home city> / HYBRID / ON-SITE on the OG
    # tracker's Location column, not raw scraped text. The city is read from
    # the active profile rather than written in here, so this test says the
    # same thing whichever profile it runs against.
    home = notion_sync.home_label()
    for raw, want in [
        ("Remote", "REMOTE"), ("Remote-USA", "REMOTE"), ("Remote US", "REMOTE"),
        (f"{home} VA", home), (f"{home.title()}, Virginia", home),
        (f"Hybrid - {home.title()}, VA", "HYBRID"), ("Hybrid", "HYBRID"),
        ("United States", "ON-SITE"), ("USA", "ON-SITE"), ("VA", "ON-SITE"),
        ("San Francisco", "ON-SITE"), ("California", "ON-SITE"), ("", "ON-SITE"),
    ]:
        check(f"normalize_location({raw!r}) == {want!r}",
              notion_sync.normalize_location(raw) == want,
              notion_sync.normalize_location(raw))

    # Popping the variables is not enough on its own. Every entry point in
    # notion_sync calls _load_env() first, which reads .env.apply, .env.radar
    # and .env back in -- so on a machine that HAS credentials these two
    # checks used to authenticate for real and hit the live tracker. The
    # subject here is "nothing is configured", so the file loader is stubbed
    # out for the length of the block and restored after.
    saved_load_env = notion_sync._load_env
    saved_notion_env = {k: os.environ.pop(k, None) for k in
                        ("NOTION_API_KEY", "NOTION_JOBS_DB",
                         "NOTION_OG_TRACKER_DB")}
    notion_sync._load_env = lambda: None
    try:
        rows, note = notion_sync.fetch_newly_applied()
        check("an unconfigured Notion connection returns no rows, not a crash",
              rows == [], str(rows))
        check("...and says why", "NOTION_API_KEY" in note, note)

        row = {"page_id": "p1", "role": "Business Systems Analyst",
              "company": "Acme Health", "url": "https://acme.example/jobs/1",
              "location": "Remote", "salary": "$60,000",
              "applied": date.today().isoformat(), "agency": "",
              "score": 87, "tier": "A"}
        check("syncing to the OG tracker with no DB id set is a no-op, "
              "not a crash",
              notion_sync.sync_to_og_tracker(row, echo=lambda *_: None) is False)
    finally:
        notion_sync._load_env = saved_load_env
        for key, value in saved_notion_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    candidate = notion_sync.enrich_with_cache(row)
    check("a row with nothing in the local cache still yields a buildable "
          "candidate",
          candidate.company == "Acme Health"
          and candidate.title == "Business Systems Analyst")

    # 2026-08-26: "0 newly-applied row(s)" reads the same whether Notion has
    # no rows with Date Applied set or every one of them is already in
    # notion_sync_state.json -- and three live runs in a row landed on that
    # same unexplained zero. This pins the message down for the case that was
    # actually ambiguous: rows exist, all already synced.
    saved_state_file = notion_sync.STATE_FILE
    saved_requests = sys.modules.get("requests")
    tmp_state = Path(tempfile.mkdtemp(prefix="notion-sync-test-")) / "state.json"
    notion_sync.STATE_FILE = tmp_state
    os.environ["NOTION_API_KEY"] = "test-key"
    os.environ["NOTION_JOBS_DB"] = "test-db"
    try:
        notion_sync.mark_synced(["already-synced-1"])

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"results": [{"id": "already-synced-1", "properties": {}}],
                        "has_more": False, "next_cursor": None}

        sys.modules["requests"] = types.SimpleNamespace(
            post=lambda *a, **k: FakeResponse())
        rows, note = notion_sync.fetch_newly_applied()
        check("all-already-synced rows return zero new rows",
              rows == [], str(rows))
        check("...but the note says they're already synced, not that Notion "
              "has nothing",
              "already in notion_sync_state.json" in note, note)
    finally:
        for key in ("NOTION_API_KEY", "NOTION_JOBS_DB"):
            os.environ.pop(key, None)
        notion_sync.STATE_FILE = saved_state_file
        if saved_requests is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved_requests


def test_discord() -> None:
    section("Discord push -- overdue follow-ups")

    saved_env = os.environ.pop("DISCORD_WEBHOOK_URL", None)
    saved_requests = sys.modules.get("requests")
    row = app(id="p1", company="Acme Health")
    row.applied_on = (date.today() - timedelta(days=10)).isoformat()
    row.follow_up_due = (date.today() - timedelta(days=3)).isoformat()

    try:
        check("unset webhook is a no-op, no network touched",
              discord_mod.push([row]) == [])
        check("is_configured says no with nothing set",
              not discord_mod.is_configured())

        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.example/webhook"
        check("is_configured says yes once the env var is set",
              discord_mod.is_configured())
        check("an empty due list sends nothing even when configured",
              discord_mod.push([]) == [])

        calls = []

        class FakeResponse:
            status_code = 200

        def fake_post(url, json=None, timeout=None):
            calls.append((url, json))
            return FakeResponse()

        fake_requests = types.SimpleNamespace(post=fake_post)
        sys.modules["requests"] = fake_requests

        sent = discord_mod.push([row])
        check("a configured webhook sends the overdue follow-up",
              sent == [row], str(sent))
        check("the message names the company and the due date",
              "Acme Health" in calls[0][1]["content"]
              and row.follow_up_due in calls[0][1]["content"],
              str(calls))

        class FailResponse:
            status_code = 500

        sys.modules["requests"] = types.SimpleNamespace(
            post=lambda *a, **k: FailResponse())
        check("a failed post is not counted as sent",
              discord_mod.push([row]) == [])
    finally:
        if saved_env is None:
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
        else:
            os.environ["DISCORD_WEBHOOK_URL"] = saved_env
        if saved_requests is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved_requests


def test_auto() -> None:
    section("Unattended builds (`auto`)")

    log = temp_log()
    auto_row = log.add(app(id="a1", uid="uid-auto", auto=True))
    hand_row = log.add(app(id="h1", uid="uid-hand", company="Beta Co"))

    hidden = log.queue_hidden_uids()
    check("an unopened auto packet stays in the candidate queue",
          "uid-auto" not in hidden, str(hidden))
    check("a hand-built packet drops out of the queue", "uid-hand" in hidden)
    check("the queue can name the packet already waiting",
          log.auto_prepared().get("uid-auto") is auto_row)

    log.mark_applied(auto_row)
    check("submitting an auto packet drops it out of the queue",
          "uid-auto" in log.queue_hidden_uids())
    check("and it stops being advertised as waiting",
          "uid-auto" not in log.auto_prepared())

    # The funnel is the thing auto builds could quietly poison: hundreds of
    # machine-built drafts must not read as hundreds of applications sent.
    fresh = temp_log()
    for index in range(5):
        fresh.add(app(id=f"auto{index}", uid=f"u{index}", source="himalayas",
                      auto=True))
    check("auto drafts count as zero applications sent",
          fresh.by_source().get("himalayas", (0, 0))[0] == 0,
          str(fresh.by_source()))
    check("auto drafts occupy no concurrency slots",
          len(fresh.open_at("Acme Health")) == 0)
    check("the guard warns rather than blocks on an existing prepared packet",
          all(c.level != guard.BLOCK for c in guard.run(
              fresh, company="Acme Health", role="Business Systems Analyst",
              uid="u0")),
          guard.summarize(guard.run(fresh, company="Acme Health",
                                    role="Business Systems Analyst", uid="u0")))

    check("`auto` survives a round trip through disk",
          Log(fresh.path).rows[0].auto is True)
    check("a log written before `auto` existed still loads",
          Application(**{"id": "old", "company": "C", "role": "R"}).auto is False)


def test_weekly() -> None:
    section("Weekly funnel review (item 8)")

    def captured(log: Log) -> str:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            main_mod.print_weekly(log)
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    thin = temp_log()
    thin.add(app(id="w1", uid="w1", source="himalayas"))
    thin.mark_applied(thin.get("w1"))
    out = captured(thin)
    check("under-20 caveat prints instead of a bare report",
          "roughly 20" in out, out)
    check("the by-source line still shows what's there",
          "himalayas" in out and "1 ->" in out, out)
    check("no rejections yet reads as none, not blank", "No rejections" in out)

    full = temp_log()
    for index in range(20):
        row = full.add(app(id=f"w{index}", uid=f"wf{index}",
                           source="linkedin" if index % 2 else "indeed"))
        full.mark_applied(row)
    out_full = captured(full)
    check("20+ applications drops the under-20 caveat",
          "roughly 20" not in out_full, out_full)
    check("both sources show up", "linkedin" in out_full and "indeed" in out_full,
          out_full)

    rej = temp_log()
    row = rej.add(app(id="wr1", uid="wr1"))
    rej.mark_applied(row)
    rej.mark_rejected(row, stage="interview", reason="went internal")
    out_rej = captured(rej)
    check("a logged rejection is grouped by stage", "interview" in out_rej,
          out_rej)


def test_answers() -> None:
    section("Answer bank")

    bank = answers_mod.load()
    check("the bank loads", len(bank) >= 10, str(len(bank)))
    check("ids are unique", len({a.id for a in bank}) == len(bank))

    best = answers_mod.best("What are your salary expectations for this role?", bank)
    check("a salary question finds the salary answer", best and best.id == "salary",
          best.id if best else "none")
    best = answers_mod.best("Do you now or will you in the future require "
                            "visa sponsorship?", bank)
    check("a sponsorship question matches", best and best.id == "sponsorship",
          best.id if best else "none")
    best = answers_mod.best("Please explain any gaps in your employment history.",
                            bank)
    check("the career gap question matches", best and best.id == "career-gap",
          best.id if best else "none")

    pending = answers_mod.unconfirmed(bank)
    check("unconfirmed drafts are tracked", len(pending) >= 2, str(len(pending)))
    check("the answers nobody has said out loud are among them",
          {"salary", "why-company"} <= {a.id for a in pending})

    markdown = answers_mod.to_markdown(bank, company="Acme", role="BSA",
                                       about="We build claims software.")
    check("the packet markdown warns before it answers",
          markdown.index("Read these first") < markdown.index("## Why do you"),
          "warning is not at the top")
    check("the employer's own words are quoted for the 'why us' answer",
          "claims software" in markdown)


def test_candidates_and_jd() -> None:
    section("Candidates and JD text")

    digest = """# Job Radar - 2026-08-10 17:05

## A - apply today (1)

### [84] [FP&A Analyst](https://himalayas.app/companies/x/jobs/y)
*MyFitnessPal - United States - $70,000-$110,000 - 0d old - himalayas*

- remote
"""
    folder = Path(tempfile.mkdtemp(prefix="digest-test-"))
    (folder / "digest_2026-08-10_1705.md").write_text(digest, encoding="utf-8")
    original = config.RADAR_DIGESTS
    candidates_mod.config.RADAR_DIGESTS = folder
    try:
        parsed = candidates_mod._from_digest()
    finally:
        candidates_mod.config.RADAR_DIGESTS = original
    check("a digest parses when there is no cache", len(parsed) == 1, str(parsed))
    if parsed:
        row = parsed[0]
        check("title, company, score and tier come through",
              row.title == "FP&A Analyst" and row.company == "MyFitnessPal"
              and row.score == 84 and row.tier == "A", str(row))
        check("the source is the last meta field", row.source == "himalayas")

    cache = Path(tempfile.mkdtemp(prefix="cache-test-")) / "candidates.json"
    cache.write_text(json.dumps([{
        "title": "Business Analyst", "company": "CarMax", "url": "https://x.example",
        "score": 71, "tier": "B", "uid": "abc", "description": "x" * 900,
        "source": "workday", "unknown_field": "ignored",
    }]), encoding="utf-8")
    original = config.RADAR_CANDIDATES
    candidates_mod.config.RADAR_CANDIDATES = cache
    try:
        rows = candidates_mod._from_cache()
        filtered, note = candidates_mod.load(exclude_uids={"abc"})
    finally:
        candidates_mod.config.RADAR_CANDIDATES = original
    check("the cache loads and ignores unknown fields", len(rows) == 1, str(rows))
    check("already-applied candidates are excluded", not filtered, str(filtered))
    check("the note says where the list came from", "cache" in note, note)

    html = ("<html><head><style>.a{color:red}</style></head><body>"
            "<h2>Requirements</h2><ul><li>SQL</li><li>Excel &amp; Power BI</li>"
            "</ul><script>var x=1;</script><p>Apply now</p></body></html>")
    text = jdtext.html_to_text(html)
    check("scripts and styles are stripped",
          "var x" not in text and "color:red" not in text, text)
    check("list items become bullets", "- SQL" in text, text)
    check("entities are decoded", "Excel & Power BI" in text, text)

    check("a short page is refused as a JD",
          config.MIN_JD_CHARS > 100)


def test_packet_pieces() -> None:
    section("Packet assembly")

    check("folder names are filesystem-safe",
          packet.safe("FP&A Analyst / Sr.") == "FP_A_Analyst_Sr")

    about = packet.about_section(JD)
    check("the About section is found by its heading",
          "Denver-based physician group" in about, about[:80])

    no_heading = "We do things.\n\n" + ("A long paragraph about the role. " * 8)
    check("with no About heading it falls back to the first real paragraph",
          "long paragraph" in packet.about_section(no_heading))

    tailoring = """# Tailoring report -- Acme, BSA

- Target family read from the title: `billing`

## Required terms the resume does not say

Not a rendering bug.

- research
- snowflake

## Preferred terms not covered
"""
    check("the family is read out of the Resume Engine's own report",
          packet._family_from_report(tailoring) == "billing")
    check("family falls back rather than crashing",
          packet._family_from_report("nothing here") == "analysis")
    check("the missing-requirements list is lifted verbatim",
          packet._missing_required(tailoring) == ["research", "snowflake"],
          str(packet._missing_required(tailoring)))
    check("no missing section means an empty list",
          packet._missing_required("# nothing") == [])


def test_wiring() -> None:
    section("Wiring to the sibling packages")

    from jobdesk.apply import engine

    pkg = config.ROOT / "jobdesk"
    check("the engine is where this expects it", engine.available(),
          str(pkg / "engine"))
    check("the radar is where this expects it",
          (pkg / "radar" / "main.py").exists(), str(pkg / "radar"))
    check("the letter content file exists", config.LETTER_FILE.exists())
    check("the answer bank exists", config.ANSWERS_FILE.exists())
    check("the launcher exists", (config.ROOT / "Apply.cmd").exists())

    # The isolation rule, amended for one repo: radar, engine and apply still
    # never import each other. Only app/ -- the front door -- may import all
    # three, which is exactly what a front door is for.
    forbidden = {"radar": ("engine", "apply"),
                 "engine": ("radar", "apply"),
                 "apply": ("radar", "engine")}
    patterns = (r"^\s*from (?:jobdesk\.)?{0}[. ]",
                r"^\s*import (?:jobdesk\.)?{0}",
                r"^\s*from \.\.{0}")
    offenders = []
    for owner, banned in forbidden.items():
        for path in (pkg / owner).rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            for other in banned:
                if any(re.search(pat.format(other), body, re.M) for pat in patterns):
                    offenders.append(f"{owner}/{path.name} -> {other}")
    check("no sibling imports between radar, engine and apply",
          not offenders, ", ".join(offenders))


def main() -> int:
    which = sys.argv[1].lstrip("-") if len(sys.argv) > 1 else "all"
    tests = {
        "letter": test_letter, "guard": test_guard, "applog": test_applog,
        "auto": test_auto, "weekly": test_weekly, "discord": test_discord,
        "answers": test_answers, "candidates": test_candidates_and_jd,
        "packet": test_packet_pieces, "wiring": test_wiring,
        "notion_sync": test_notion_sync,
    }
    for name, func in tests.items():
        if which in ("all", name):
            func()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
