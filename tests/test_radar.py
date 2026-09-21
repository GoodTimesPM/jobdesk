"""The dev loop: collect, score, and PRINT. Writes nothing, needs no keys.

    py tests/test_radar.py                 # every source
    py tests/test_radar.py capital         # only sources matching "capital"
    py tests/test_radar.py --scoring       # scoring + salary self-check
    py tests/test_radar.py --salary        # just the salary reader
    py tests/test_radar.py --discord       # post the synthetic jobs to the webhook
    py tests/test_radar.py --plugins       # delivery registry self-check, no network

The scoring self-check pins itself to `profile.example/` before importing
anything, so it holds the shipped example profile to its own behaviour and
gives the same answer on every machine. A scoring expectation is a claim about
one specific targeting.toml; run against whatever profile happens to be
active, it would be a claim about nothing.

The other modes read the ACTIVE profile, because collecting is the thing you
actually want to watch against your own targeting file.

Mirrors the news bot's test_local.py, including forcing UTF-8 on the Windows
console (cp1252 chokes on anything interesting).
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "--scoring" in sys.argv:
    os.environ["JOBDESK_PROFILE"] = str(ROOT / "profile.example")

if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from jobdesk.radar import config, dedupe, render, score, sources   # noqa: E402
from jobdesk.radar import models, profile, terms              # noqa: E402
from jobdesk.radar import (candidates, companies, discover, gather, resolve,
                           seed, learn)                              # noqa: E402
from jobdesk.radar.sources import ats                               # noqa: E402
from jobdesk.radar.models import Job                                # noqa: E402


# Wren Adeyemi's world, not the author's: every posting below is written
# against profile.example/targeting.toml. The lesson each one was added to
# teach is unchanged. Only the profession moved.
SYNTHETIC = [
    Job(title="Staff Accountant", company="Test Co", url="http://x",
        source="greenhouse", location="Denver, CO",
        description="Looking for 1-2 years of experience with the general "
                    "ledger, reconciliation, QuickBooks and Excel. "
                    "$65,000 - $80,000."),
    Job(title="Senior Accounting Manager", company="Test Co", url="http://x",
        source="greenhouse", location="Denver, CO",
        description="8+ years required. CPA preferred. Lead a team of six."),
    Job(title="Billing Analyst", company="Test Co", url="http://x",
        source="remoteok", location="Remote",
        description="3-5 years in billing operations, accounts receivable, "
                    "collections and Excel."),
    Job(title="Payroll Specialist", company="Test Co", url="http://x",
        source="workday", location="Lakewood, CO",
        description="ADP, timekeeping, garnishments. Two years experience. "
                    "Requires an active security clearance."),
    Job(title="Accounts Receivable Specialist", company="Test Co", url="http://x",
        source="lever", location="Remote (Canada)",
        description="Cash application and aging reports in NetSuite. "
                    "2 years experience."),

    # --- added with the hiring.cafe-inspired pass (2026-07-25) -------------
    # Each of these was a real miss found by calibration or a live run, not a
    # hypothetical. Expected verdict is in the comment.

    # Vocabulary: a slash mid-title, with the on-target words buried in it.
    # Both were invisible to the closed tier lists. Expect: on target, not
    # capped at 40.
    Job(title="Accounts Payable/Receivable Clerk", company="Test Co", url="http://x",
        source="workday", location="Denver, CO",
        description="Invoice processing, three-way match, QuickBooks. "
                    "2 years experience."),

    # The HR escape hatch. A 5-year req with an equivalency clause is exactly
    # the kind an accounting degree opens. Expect: surfaced, NOT hard-blocked.
    Job(title="Financial Systems Analyst", company="Test Co", url="http://x",
        source="greenhouse", location="Denver, CO",
        description="Bachelor of Science in Accounting and 5 years of "
                    "experience, or an equivalent combination of education and "
                    "experience. NetSuite and Excel."),

    # ...but the escape has a ceiling. Expect: 0/F, block stands.
    Job(title="Cost Accountant", company="Test Co", url="http://x",
        source="greenhouse", location="Denver, CO",
        description="Minimum 10 years of experience required, or an equivalent "
                    "combination of education and experience."),

    # A four-digit calendar year followed by "year". A live posting read
    # "2014 year over year" as a 14-year requirement.
    # Expect: years required = None, no penalty.
    Job(title="Accounting Specialist", company="Test Co", url="http://x",
        source="workday", location="Denver, CO",
        description="Revenue has grown since 2014 year over year. Own the "
                    "month end close in Excel. Bachelor of Science required."),

    # Agency repost, crowded, with an off-hours requirement. Expect: surfaced
    # but visibly demoted. Both the staffing-agency and off-hours flags set.
    Job(title="Staff Accountant", company="Robert Half", url="http://x",
        source="remoteok", location="Remote", remote=True,
        description="Our client is seeking an accountant. Reconciliation and "
                    "Excel. 2 years experience. Weekend availability is "
                    "required during close."),

    # Safety compliance is a different profession that shares the word
    # "compliance". Surfaced at 83/A on the first live run. Expect: capped.
    Job(title="EHS Compliance Coordinator", company="Test Co", url="http://x",
        source="himalayas", location="Remote", remote=True,
        description="Manage environmental health and safety compliance. "
                    "2 years experience."),

    # Unpaid work. hiring.cafe filters this with commitmentTypes=Volunteer;
    # a volunteer nonprofit posting reached 83/A. Expect: 0/F.
    Job(title="Bookkeeper", company="Test Nonprofit", url="http://x",
        source="himalayas", location="Remote", remote=True,
        description="This is a volunteer position supporting our nonprofit. "
                    "Keep the books in QuickBooks."),
]


# What each synthetic is supposed to prove. The comments above already said
# it; this says it in a form that can go red. Written as intent, not as a
# snapshot -- a band and a flag, never an exact score, so re-weighting a rule
# by a few points does not fail a test that was never about those points.
#
#   (title, company): (tier or None, min, max, flags required, flags banned)
EXPECTED = {
    ("Staff Accountant", "Test Co"):
        ("A", 85, 100, (), ("disqualified",)),
    ("Senior Accounting Manager", "Test Co"):
        ("F", 0, 0, ("seniority-mismatch",), ()),
    ("Billing Analyst", "Test Co"):
        (None, 55, 85, (), ("disqualified",)),
    ("Payroll Specialist", "Test Co"):
        ("F", 0, 0, ("disqualified",), ()),
    ("Accounts Receivable Specialist", "Test Co"):
        ("F", 0, 0, ("remote-but-not-US",), ()),
    # The vocabulary fix: a slashed title used to cap at 40.
    ("Accounts Payable/Receivable Clerk", "Test Co"):
        (None, 60, 100, (), ("disqualified",)),
    # The equivalency escape hatch opens...
    ("Financial Systems Analyst", "Test Co"):
        (None, 50, 100, ("equivalency-accepted",), ("disqualified",)),
    # ...but not at ten years.
    ("Cost Accountant", "Test Co"):
        ("F", 0, 0, ("over-experienced-req",), ()),
    # "2014 year over year" is not a 14-year requirement.
    ("Accounting Specialist", "Test Co"):
        (None, 60, 100, (), ("disqualified",)),
    # Surfaced, but visibly demoted.
    ("Staff Accountant", "Robert Half"):
        (None, 45, 85, ("staffing-agency", "off-hours-availability"), ()),
    # Safety compliance is a different profession. Capped, not A-tier.
    ("EHS Compliance Coordinator", "Test Co"):
        (None, 0, 45, (), ()),
    ("Bookkeeper", "Test Nonprofit"):
        ("F", 0, 0, ("disqualified",), ()),
}

# Years-parsing cases that are about the parser, not the score.
EXPECTED_YEARS = {
    ("Accounting Specialist", "Test Co"): None,   # "since 2014 year over year"
    ("Senior Accounting Manager", "Test Co"): 8,
    ("Cost Accountant", "Test Co"): 10,
}


def scoring_check() -> int:
    """Score the synthetics and hold each one to what it was added to prove.

    This used to print and return. Twelve jobs' worth of expected verdicts
    lived in the comments above SYNTHETIC, which meant a regression was only
    caught if someone read the dump carefully. Now it exits non-zero.
    """
    print("Scoring self-check")
    print("=" * 72)
    failures = []
    for job in SYNTHETIC:
        score.score_job(job)
        years = score.required_years(job)
        print(f"\n[{job.score:>3}] {job.tier}  {job.title}  ({job.location})")
        print(f"       years required: {years}")
        for reason in job.reasons:
            print(f"       - {reason}")
        if job.flags:
            print(f"       flags: {', '.join(job.flags)}")

        key = (job.title, job.company)
        if key not in EXPECTED:
            failures.append(f"{key} has no entry in EXPECTED")
            continue
        tier, low, high, required, banned = EXPECTED[key]
        if tier is not None and job.tier != tier:
            failures.append(f"{job.title}: tier {job.tier}, expected {tier}")
        if not low <= job.score <= high:
            failures.append(
                f"{job.title}: score {job.score}, expected {low}-{high}")
        for flag in required:
            if flag not in job.flags:
                failures.append(f"{job.title}: missing flag {flag!r}")
        for flag in banned:
            if flag in job.flags:
                failures.append(f"{job.title}: unwanted flag {flag!r}")
        if key in EXPECTED_YEARS and years != EXPECTED_YEARS[key]:
            failures.append(
                f"{job.title}: years {years}, expected {EXPECTED_YEARS[key]}")

    print("\n" + "=" * 72)
    if failures:
        print(f"{len(failures)} scoring expectation(s) broke:")
        for line in failures:
            print("  " + line)
        return 1
    print(f"all {len(SYNTHETIC)} scoring expectations hold")
    return 0


def rules_check() -> int:
    """The scoring rules that a synthetic posting cannot pin down.

    SYNTHETIC holds whole jobs and checks the number that falls out the end.
    That is the right shape for "does this posting land in the right band" and
    the wrong shape for "does the word `associate` in `Associate Director`
    count as an early-career signal", which is one boolean three layers down.
    Every rule below was a live scoring bug, and each one is one line here.
    """
    print("Rule self-check")
    print("=" * 72)
    fails: list[str] = []

    def want(label: str, got, expected):
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            fails.append(f"{label}: got {got!r}, expected {expected!r}")

    # -- a junior word on a senior noun is a rank, not a rung ----------------
    want("'Associate Director' is not an entry-level title",
         score.entry_level_marker("Associate Director, Clinical Research"), None)
    want("neither is 'Senior Associate'",
         score.entry_level_marker("Senior Associate, Card Risk"), None)
    want("nor 'Associate Manager'",
         score.entry_level_marker("FSP Associate Manager"), None)
    want("but a plain 'Associate Analyst' still is",
         score.entry_level_marker("Associate Data Analyst"), "associate")
    want("and a rank plus a rung keeps the rung",
         score.entry_level_marker("Associate Director / Associate Analyst"),
         "associate")

    # -- a requisition number is not a level --------------------------------
    want("a req number ending in 1 is not 'level 1'",
         score.entry_level_marker("Lead Budget Analyst 00151"), None)
    want("a lone trailing 1 still is",
         score.entry_level_marker("Accounting Analyst 1"), "level 1")

    # -- the combined-level disarm needs an actual junior rung ---------------
    want("a slash list with a junior rung disarms the block",
         score.seniority_block(
             "Associate Data Engineer / Data Engineer II / Senior Data Engineer"),
         None)
    want("'associate' meaning 'employee' does not",
         score.seniority_block("Manager, Associate Relations Investigator"),
         "manager")

    # -- a years range is a band ---------------------------------------------
    def band(text: str):
        return score.required_band(
            score.Job(title="Test", company="Test Co", url="x",
                      source="test", description=text))

    want("'3-5 years' is a band, not a 3", band("3-5 years required"), (3, 5))
    want("'5+ years' has no invented ceiling", band("5+ years required"), (5, 5))
    # Through clean_text on purpose: the bug was that "5&#43; years" reached
    # the parser with the plus still spelled out as five characters, and the
    # years regex read it as no years at all.
    want("a plus sign written as an entity still parses",
         band(models.clean_text("<p>5&#43; years of experience required</p>")),
         (5, 5))
    want("the highest floor wins",
         band("1+ years of Excel. Minimum of 4 years in accounting."), (4, 4))
    want("no years stated is None", band("We value curiosity."), None)

    # -- and the band, not the floor, decides how much credit it earns -------
    def flags_for(text: str):
        pts, _why, fl = score._experience_points(
            score.Job(title="Test", company="Test Co", url="x",
                      source="test", description=text))
        return pts, tuple(fl)

    want("a band topping out past the stretch is not 'in range'",
         flags_for("3-5 years of experience required")[1],
         ("band-tops-out-high",))
    want("a band inside the stretch is demoted, not blocked",
         flags_for("2-3 years of experience required")[1],
         ("stretch-experience",))
    want("a band wholly in range gets the full bonus",
         flags_for("1-2 years of experience required"), (15, ()))

    # -- 100 costs what it says it costs -------------------------------------
    want("nothing below the linear point moves", score.scale(40), 40)
    want("the linear point itself is fixed",
         score.scale(score.SCORE_LINEAR_TO), score.SCORE_LINEAR_TO)
    want("a perfect raw total is 100", score.scale(score.SCORE_CEILING), 100)
    want("and nothing can exceed it", score.scale(score.SCORE_CEILING + 50), 100)
    want("halfway up the stretch is halfway to 100",
         score.scale((score.SCORE_LINEAR_TO + score.SCORE_CEILING) // 2), 95)
    want("the scale never goes backwards",
         all(score.scale(n) <= score.scale(n + 1) for n in range(0, 200)), True)

    # -- synonyms ------------------------------------------------------------
    syn = profile.synonyms()
    want("the example profile declares synonyms", bool(syn), True)
    want("a title synonym lands one notch below an exact hit",
         score._synonym_title("fp&a analyst")[0],
         24 - profile.SYNONYM_DISCOUNT)
    want("an unrecognised title gets nothing",
         score._synonym_title("licensed massage therapist")[0], 0)
    want("alternates match on word boundaries",
         score._said("worked on skeleton crews", "elt"), False)
    want("and do match the whole word",
         score._said("built elt pipelines", "elt"), True)
    want("a skill synonym is worth the skill's full weight",
         score._says("we use qbo daily", "quickbooks", syn["quickbooks"]), "qbo")

    # -- the same table widens what the boards are asked for -----------------
    want("an empty budget widens nothing",
         terms.widen(["staff accountant"], 0), [])
    want("the budget is a hard cap",
         len(terms.widen(["staff accountant", "bookkeeper"], 3)), 3)
    want("widening spreads across seeds instead of draining one",
         terms.widen(["staff accountant", "bookkeeper"], 2),
         ["general accountant", "full charge bookkeeper"])
    want("a seed is never handed back as its own alternate",
         "staff accountant" in terms.widen(["staff accountant"], 8), False)
    want("an unknown seed falls through to tier 1",
         bool(terms.widen(["licensed massage therapist"], 2)), True)

    # -- learning an employer from a posting ---------------------------------
    #
    # Every probe is stubbed and the learned file goes to a temp directory, so
    # this touches no network and cannot write into data/. What is under test
    # is the decision layer: who qualifies, who is confirmed, and who is left
    # alone afterwards.
    def made(company: str, title: str, points: int) -> Job:
        job = Job(title=title, company=company, url="x", source="test")
        job.score = points
        return job

    jobs = [
        made("Hitbox Analytics", "Senior Data Analyst", 92),
        made("Hitbox Analytics", "Mailroom Clerk", 20),
        made("Missing Co", "Reporting Analyst", 88),
        made("Robert Half", "Data Analyst", 99),
        made("Lowball LLC", "Data Analyst", config.LEARN_MIN_SCORE - 1),
    ]

    def stub(name, seen_titles, budget):
        if name == "Hitbox Analytics":
            return (discover.Board("greenhouse", "hitboxanalytics",
                                   list(seen_titles), 7), "greenhouse/x")
        return None, "no public board found"

    with tempfile.TemporaryDirectory() as tmp:
        real_find, real_path = discover.find, config.LEARNED_EMPLOYERS
        discover.find = stub
        config.LEARNED_EMPLOYERS = Path(tmp) / "employers.learned.toml"
        try:
            names = [n for n, _s, _t in learn.candidates(jobs, {}, date(2026, 9, 17))]
            want("a company qualifies on its best posting, not its worst",
                 "Hitbox Analytics" in names, True)
            want("a staffing agency never qualifies", "Robert Half" in names, False)
            want("neither does a company under the bar",
                 "Lowball LLC" in names, False)

            first = learn.run(jobs, today=date(2026, 9, 17))
            want("a confirmed board is kept", [r["name"] for r in first],
                 ["Hitbox Analytics"])
            want("and it is written with the title that confirmed it",
                 first[0]["confirmed_by"], "Senior Data Analyst")
            want("an unconfirmed company is not kept",
                 any(r["name"] == "Missing Co" for r in first), False)
            want("a learned employer joins the watch list",
                 any(e["name"] == "Hitbox Analytics" for e in companies.active()),
                 True)
            want("a company already learned is not probed again",
                 learn.run(jobs, today=date(2026, 9, 18)), [])

            data = learn._read(config.LEARNED_EMPLOYERS)
            miss = learn._misses(data).get("missing co")
            want("a miss is remembered", bool(miss), True)
            want("and left alone until the retry window is up",
                 [n for n, _s, _t in learn.candidates(
                     jobs, data, date(2026, 9, 18))], [])
            want("then probed once more",
                 [n for n, _s, _t in learn.candidates(
                     jobs, data,
                     date(2026, 9, 17) + timedelta(days=config.LEARN_RETRY_DAYS + 1))],
                 ["Missing Co"])

            # The seeder shares that cooldown, and has one reason to suspend
            # it: the miss is an answer under rules and a profile that have
            # since changed. Tightening a confirmation rule or pasting in 228
            # company websites makes yesterday's misses stale, and waiting
            # thirty days to find that out is the wrong trade.
            # Use a name the seeder actually draws on. "Missing Co" is a
            # posting in this fixture, not an entry in the seed list, so
            # asserting against it would pass whatever the flag did.
            seeded = seed.from_profile()[0][0]
            data = learn._read(config.LEARNED_EMPLOYERS)
            learn.write(list(data.get("employer", [])),
                        list(data.get("miss", []))
                        + [{"name": seeded, "tried_on": "2026-09-17",
                            "note": "no public board found"}])
            fresh = seed.candidates(400, date(2026, 9, 18))
            want("the seeder honours a recent miss by default",
                 any(n == seeded for n, _s in fresh), False)
            retried = seed.candidates(400, date(2026, 9, 18), retry_missed=True)
            want("and --retry-missed asks it again anyway",
                 any(n == seeded for n, _s in retried), True)

            # One employer can reach the list under two names. A seed list
            # pasted from two rosters carried both "Federal Reserve Bank
            # Richmond" and "Richmond Federal Reserve"; both resolved to
            # workday/rb, and every run then fetched that board twice.
            twice = [
                {"ats": "workday", "name": "Virginia Retirement System",
                 "host": "varetire.wd108.myworkdayjobs.com",
                 "tenant": "varetire", "site": "VRS_External_Career_Site",
                 "tier": 3, "learned_on": "2026-09-19"},
                {"ats": "workday", "name": "Virginia Retirement Investment",
                 "host": "varetire.wd108.myworkdayjobs.com",
                 "tenant": "varetire", "site": "VRS_External_Career_Site",
                 "tier": 3, "learned_on": "2026-09-19"},
                {"ats": "greenhouse", "name": "Somebody Else",
                 "slug": "somebodyelse", "tier": 3,
                 "learned_on": "2026-09-19"},
            ]
            learn.write(twice, [])
            kept = learn._read(config.LEARNED_EMPLOYERS).get("employer", [])
            want("two names for one board are written once",
                 [r["name"] for r in kept],
                 ["Virginia Retirement System", "Somebody Else"])
        finally:
            discover.find, config.LEARNED_EMPLOYERS = real_find, real_path

    # A wrong board is the one failure mode that poisons every future run, so
    # the rule that stops it gets its own line: the board has to be running a
    # req this company was already seen posting, spelled the same way.
    board = discover.Board("ashby", "solstice", ["Research Scientist, LLMs"], 4)
    want("a collision board is not confirmed by a near miss",
         discover.confirms(board, ["Process Engineer"]), None)
    want("punctuation and case do not break a confirmation",
         discover.confirms(discover.Board("lever", "x", ["Analyst II, Analytics"], 1),
                           ["analyst ii - analytics"]),
         "analyst ii - analytics")
    want("nothing confirms a board when no title was seen",
         discover.confirms(board, []), None)
    want("a legal suffix never reaches a slug",
         discover.slugs("Solstice Advanced Materials, Inc."),
         ["solsticeadvancedmaterials", "solstice-advanced-materials"])
    purse = discover.Budget(2)
    want("the probe budget is a hard stop",
         [purse.spend() for _ in range(3)], [True, True, False])

    # A seed company has posted nothing we have read, so `confirms` can never
    # fire for it and a second route has to carry the proof. These pin what
    # that route will and will not accept, because the cost of getting it
    # wrong is a wrong board on the watch list forever.
    want("a trailing word that distinguishes nobody is not a difference",
         discover.same_company("Genworth", "Genworth Financial, Inc."), True)
    want("but a word that says what the company is, is",
         discover.same_company("Solstice", "Solstice Advanced Materials"), False)
    want("and a longer name is not the same as a shorter one it starts with",
         discover.same_company("Metro", "Metropolitan Transit"), False)

    here = {"denver", "glen allen", "new kent"}
    want("a place name is matched whole, not as a substring",
         discover.in_places("New York, NY", here), None)
    want("a two-word place name still matches",
         discover.in_places("Glen Allen, VA", here), "glen allen")

    # Cities repeat across states, and the sweep put a Florida company on the
    # watch list because Richmond's profile knows about Petersburg, Virginia.
    want("a location names its state", discover.state_named("Denver, CO"), "co")
    want("a spelled-out state counts too",
         discover.state_named("Colonial Heights, Virginia, USA"), "va")
    want("west virginia is not virginia",
         discover.state_named("Richmond, West Virginia"), "wv")
    want("a bare city names no state", discover.state_named("Richmond"), "")
    want("an ordinary word is not a state code",
         discover.state_named("Denver or remote"), "")
    want("the same city in the wrong state is refused",
         discover.in_places("Glen Allen, TX", here, "co"), None)
    want("and in the right state is not",
         discover.in_places("Denver, CO", here, "co"), "denver")
    want("a location with no state at all is still allowed through",
         discover.in_places("Denver", here, "co"), "denver")

    # A guessed slug that finds a board naming that name proves the spelling,
    # not the company. A sweep let in a London design agency as Universal
    # Corp. and an Amsterdam shop as EY on exactly this.
    named = discover.Board("workable", "acme", ["Staff Accountant"], 1,
                           company="Acme Industries, Inc.")
    want("a matching name alone no longer carries a board",
         discover.confirms_local(named, "Acme", here, discover.Budget(0)), None)
    both = discover.Board("workable", "acme", ["Staff Accountant"], 1,
                          company="Acme Industries, Inc.",
                          locations=["Denver, CO"])
    want("a matching name that also hires here is believed",
         bool(discover.confirms_local(both, "Acme", here, discover.Budget(0))),
         True)
    wrong = discover.Board("workable", "acme", ["Staff Accountant"], 1,
                           company="Acme Coyote Supplies")
    want("a board that names somebody else is refused, locations or not",
         discover.confirms_local(wrong, "Acme", here, discover.Budget(0)), None)
    silent = discover.Board("ashby", "acme", ["Staff Accountant"], 1,
                            locations=["Denver, CO"])
    want("a silent board hiring in the metro is corroborated",
         bool(discover.confirms_local(silent, "Acme", here, discover.Budget(0))),
         True)
    elsewhere = discover.Board("ashby", "acme", ["Staff Accountant"], 1,
                               locations=["New York, NY"])
    want("a silent board hiring somewhere else is not",
         discover.confirms_local(elsewhere, "Acme", here, discover.Budget(0)),
         None)

    # The seeder is the generic half of this: every user sweeps their own
    # metro, so nothing about which metro may be baked in.
    want("the seeder's idea of here comes from the profile, whole",
         "denver" in seed.places(), True)
    want("a posting three time zones away is not local",
         seed.is_local("Brooklyn, New York", seed.places()), False)

    # Reading the ATS off a company's own careers page replaces guessing a
    # slug, which measured 3 hits per 88 names. These pin the parsing half of
    # it; the fetching half needs the network and belongs in `live()`.
    want("a workday tenant is read off the hostname, not guessed",
         resolve.scan("", "https://gnw.wd1.myworkdayjobs.com/en-US/careers"),
         ("workday", "gnw"))
    want("an ATS named only by its CDN still counts",
         resolve.scan('<link href="//rmkcdn.successfactors.com/a/b.css">')[0],
         "successfactors")
    want("a page that names no ATS says so",
         resolve.scan("<p>We are hiring!</p>", "https://www.acme.com/jobs"),
         ("", ""))
    want("a legal suffix is not part of a domain",
         resolve.domains("Markel Corporation"), ["markel.com"])
    want("the jobs portal outranks the page about how nice it is to work here",
         resolve.careers_links(
             '<a href="/life">Careers Culture</a>'
             '<a href="https://careers.acme.com/">Open Jobs Portal</a>',
             "https://www.acme.com/")[0],
         "https://careers.acme.com/")
    # An ATS we can name but cannot read is a different outcome from a miss,
    # and only one of the two is a request for a new handler.
    want("an ATS with a handler is readable",
         resolve.Found("hit", "workday", "gnw").readable, True)
    want("an ATS without one is found but not readable",
         resolve.Found("hit", "phenom", "VHSVHSUS").readable, False)
    # A page that links straight to the board hands over the datacenter and
    # the site id, which is what the 29-guess Workday loop exists to find.
    want("workday coordinates come off a linked URL whole",
         resolve.workday_coords("", "https://gnw.wd1.myworkdayjobs.com/en-US/GNW"),
         {"host": "gnw.wd1.myworkdayjobs.com", "tenant": "gnw", "site": "GNW"})
    want("a locale segment is not the site id",
         (resolve.workday_coords("see https://x.wd3.myworkdayjobs.com/fr-FR/Careers")
          or {}).get("site"), "Careers")
    want("a workday link with no site id yields nothing to fetch",
         resolve.workday_coords("", "https://acme.wd5.myworkdayjobs.com/"), None)

    # A seed list may carry the website beside the name, because guessing the
    # website fails on the same employers guessing a slug fails on.
    # JOBDESK_PROFILE points at profile.example here, which ships a seed list.
    pairs = seed.from_profile()
    want("the example seed list parses into name/site pairs",
         bool(pairs) and all(name for name, _ in pairs), True)
    want("an entry that supplies a website keeps it",
         any(site.startswith("http") for _, site in pairs), True)

    # The candidate cache is sorted by score and read straight by the Jobs
    # tab, so a row carrying a number from an older scoring release does not
    # merely look wrong -- it outranks everything found today. These pin the
    # re-score that stops it.
    stale = {
        "uid": "u1", "title": "Associate Director, Brand Analytics",
        "company": "Old Scale Co", "url": "x", "source": "test",
        "score": 100, "tier": "A+", "reasons": ["from a previous release"],
        "flags": [], "last_seen": "2026-09-01T00:00:00+00:00",
    }
    # On target for profile.example (Wren Adeyemi, accounting), so it should
    # survive the re-score with a real number rather than merely a lower one.
    good = {
        "uid": "u2", "title": "Staff Accountant", "company": "Still Fine Inc",
        "url": "y", "source": "test", "location": "Denver, CO",
        "description": "1-2 years with the general ledger, reconciliation, "
                       "QuickBooks and Excel.",
        "score": 100, "tier": "A+", "reasons": [], "flags": [],
        "last_seen": "2026-09-01T00:00:00+00:00",
    }
    carried = {"u1": dict(stale), "u2": dict(good), "u3": dict(good, uid="u3")}
    candidates._rescore_carried(carried, {"u3"}, log=lambda _m: None)
    want("a carried row is re-scored against today's rules",
         carried["u1"]["score"], 0)
    want("and its reasons are replaced, not left describing the old number",
         carried["u1"]["reasons"] != stale["reasons"], True)
    want("a row this run touched is left exactly as the run scored it",
         carried["u3"]["score"], 100)
    want("a row that still holds up keeps a real score",
         carried["u2"]["score"] > config.MIN_SCORE_TO_REPORT, True)

    # A source that publishes 500 characters and an ellipsis has not told us
    # what the posting requires. Scoring it as though silence were good news
    # is how a five-year req reaches the top of a board built to keep them off.
    body = ("1-2 years with the general ledger, reconciliation, QuickBooks "
            "and Excel. Denver based, hybrid.")
    full = score.score_job(Job(title="Staff Accountant", company="Test Co",
                               url="x", source="greenhouse",
                               location="Denver, CO", description=body))
    snip = score.score_job(Job(title="Staff Accountant", company="Test Co",
                               url="x", source="adzuna",
                               location="Denver, CO",
                               description=body, partial=True))
    want("a snippet never outranks a posting that was read in full",
         snip.score < full.score, True)
    want("and it says why, on the posting",
         "partial-description" in snip.flags or
         "unverified-experience" in snip.flags, True)
    want("a snippet is still worth surfacing, not buried",
         snip.score >= config.MIN_SCORE_TO_REPORT, True)

    silent = Job(title="Staff Accountant", company="Test Co", url="x",
                 source="adzuna", location="Denver, CO",
                 description="Join our team. Great benefits.", partial=True)
    want("silence in a snippet earns nothing where silence in a full body earns +6",
         "unverified-experience" in score.score_job(silent).flags, True)

    # The name list will never keep up with the contract shops, so the body
    # language has to carry it -- and learn.py must not adopt one as an
    # employer to watch every morning.
    shop = score.score_job(Job(
        title="Power BI Developer", company="Nobody Has Typed This LLC",
        url="x", source="adzuna", location="Richmond, VA",
        description="Duration: 12 months. Pay rate: hourly. Job ID: VA-811014.",
        partial=True))
    want("a contract shop gives itself away in the body",
         "staffing-agency" in shop.flags, True)
    want("and is never learned as an employer",
         [n for n, _s, _t in learn.candidates([shop], {}, date(2026, 9, 17))], [])

    # One requisition, farmed out. Four shops, a quarter of them flagged,
    # and the same title under a dozen coats of paint -- the shape that put
    # twenty three copies of one Power BI contract in a single A-tier.
    def repost(company, title, flagged, sc):
        return Job(title=title, company=company, url="u" + company,
                   source="adzuna", location="Denver, CO", score=sc,
                   flags=["staffing-agency"] if flagged else [])

    farm = [repost("Shop A", "SCC - Power BI Developer (811014)", True, 70),
            repost("Shop B", "Power BI Developer (Hybrid)", True, 78),
            repost("Shop C", "Power BI Developer | W2/1099 | Only Local", False, 61),
            repost("Shop D", "Power BI Developer in Denver, CO", False, 55)]
    kept, dropped = dedupe.collapse_reposts(farm, known=set())
    want("a farmed-out req reaches the digest once", len(kept), 1)
    want("and the copy kept is the best-scoring one", kept[0].company, "Shop B")
    want("and the decorated titles were seen as one job", dropped, 3)
    want("the survivor is marked, so learn.py leaves the shop alone",
         "staffing-agency" in kept[0].flags, True)

    # Decoration only. "(Hybrid)" is how the shop dressed the req up;
    # "(Federal Grants & eRA Systems)" is what the job actually is, and
    # GovCIO's business analyst has nothing to do with the crowd posting
    # under the bare title.
    want("a parenthetical that carries the job is not stripped",
         dedupe._title_key("Business Analyst (Federal Grants & eRA Systems)"),
         "business analyst federal grants era systems")
    want("a requisition number is not part of the title",
         dedupe._title_key("SCC - Power BI Developer (811014)"),
         "power bi developer")
    want("but a level still is",
         dedupe._title_key("Business Analyst 3"), "business analyst 3")

    # These groups are mixtures. Nine shops advertising one contract as
    # "Business Analyst" sat beside two Markel reqs that were nothing to do
    # with them, and a company on the watch list is never the farm.
    mixed = farm + [repost("Markel", "Power BI Developer", False, 90)]
    kept, _n = dedupe.collapse_reposts(mixed, known={"markel"})
    want("a company already on the watch list is never dropped",
         sorted(j.company for j in kept), ["Markel", "Shop B"])

    # The false positive this rule exists to avoid. Six separate ABA clinics
    # really do each want a behaviour analyst, and none of them writes like
    # a contract shop.
    clinics = [Job(title="Board Certified Behavior Analyst",
                   company=f"Clinic {n}", url=f"c{n}", source="adzuna",
                   location="Denver, CO", score=60) for n in range(6)]
    want("six real clinics posting one title are all left alone",
         len(dedupe.collapse_reposts(clinics, known=set())[0]), 6)

    # Three companies is a collision, not a farm, even with a shop in it.
    trio = [repost("Cardinal Health", "Data Analyst", False, 80),
            repost("Guild Mortgage", "Data Analyst", False, 79),
            repost("Some Shop", "Data Analyst", True, 70)]
    want("three companies on one title is a coincidence, not a farm",
         len(dedupe.collapse_reposts(trio, known=set())[0]), 3)

    # A company's own board carries its own reqs; a farm cannot form there.
    own = [Job(title="Power BI Developer", company=f"Co {n}", url=f"g{n}",
               source="greenhouse", location="Denver, CO", score=70,
               flags=["staffing-agency"]) for n in range(5)]
    want("an ATS board is never collapsed on title alone",
         len(dedupe.collapse_reposts(own, known=set())[0]), 5)

    # ----------------------------------------------------------------------
    # Nothing here may assume Richmond, or accounting. The profile says where
    # its user lives and what they do; every one of these was a place the code
    # had decided for itself, found by asking what this app does for a nurse
    # in Austin.
    # ----------------------------------------------------------------------
    want("a state is looked up by its code, not assumed",
         discover.state_full_name("TX"), "texas")
    want("and an unknown code names no state",
         discover.state_full_name("ZZ"), "")

    # The Workday slug parser stripped the literal "-virginia", so an Austin
    # posting came back titled "Graphic Designer Austin" in "Texas".
    want("a slug gives up its city whatever state it names",
         ats._deslug("graphic-designer-austin-texas-united-states"),
         ("Graphic Designer", "Austin, Texas, United States"))
    want("including the state whose name contains another",
         ats._deslug(
             "registered-nurse-charleston-west-virginia-united-states"),
         ("Registered Nurse", "Charleston, West Virginia, United States"))
    want("and the home metro still parses as it always did",
         ats._deslug(
             "senior-data-analyst-richmond-virginia-united-states"),
         ("Senior Data Analyst", "Richmond, Virginia, United States"))

    # 15 points for a bare "Analyst" used to be written into the scorer, which
    # paid one profession's rent and nobody else's.
    # This profile is an accountant's, and that is the assertion: the word
    # the bonus keys on is hers, not the one that used to be compiled in.
    fam = [f.lower() for f in profile.FAMILY_TITLES]
    want("the generic-title bonus comes from the profile", bool(fam), True)
    want("and an accountant's profile does not pay for 'analyst'",
         "analyst" in fam, False)
    want("a profile naming no family noun is allowed",
         profile._OPTIONAL["FAMILY_TITLES"][1], [])

    print("\n" + "=" * 72)
    if fails:
        print(f"{len(fails)} rule(s) broke:")
        for line in fails:
            print("  " + line)
        return 1
    print("all rules hold")
    return 0


def gather_check() -> int:
    """The seed gatherer, with nothing plugged in.

    Every assertion here is about a decision that cost a wrong answer once.
    None of it touches the network: the four sources are fetch-then-parse, and
    the parsing half is where the mistakes were. A suite that needs Overpass to
    be up is a suite nobody runs.
    """
    print("Seed gatherer self-check")
    print("=" * 72)
    fails: list[str] = []

    def want(label: str, got, expected):
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            fails.append(f"{label}: got {got!r}, expected {expected!r}")

    # -- domains ------------------------------------------------------------
    want("a bare host survives", gather.domain_of("https://www.vcu.edu/"),
         "vcu.edu")
    want("a university subdomain is the same employer",
         gather.domain_of("https://maps.vcu.edu/parking"), "vcu.edu")
    # Two state agencies, two payrolls. A general last-two-labels rule would
    # fold these together and lose one of them.
    want("two state agencies stay two",
         gather.domain_of("https://vdh.virginia.gov"), "vdh.virginia.gov")
    want("and the careers subdomain is still the company",
         gather.domain_of("https://careers.example.com"), "example.com")
    want("junk is not a domain", gather.domain_of("mailto:hr@x"), "")

    # -- which name on a domain is the company's ----------------------------
    # The first version took the shortest name and got the coffee shop.
    want("the domain picks the name out of forty buildings",
         gather.best_name({"Bowe House": 3, "The Depot": 2,
                           "Virginia Commonwealth University": 1}, "vcu.edu"),
         "Virginia Commonwealth University")
    # A domain is usually the short form of the name, not all of it.
    want("a name the domain abbreviates still wins",
         gather.best_name({"Amuse": 4, "Bon Secours Health System": 1},
                          "bonsecours.com"), "Bon Secours Health System")
    want("and with no echo, the most pins win",
         gather.best_name({"Acme Depot": 5, "Z": 1}, "unrelated.com"),
         "Acme Depot")

    # -- domain guesses -----------------------------------------------------
    # Dropping the last word off a two-word name leaves one generic word, and
    # a single generic word is somebody else's company. That is how london.com
    # and chesapeake.org got written into a seed file.
    two = gather.guesses("Main Street Homes")
    want("a two-word name never guesses one word",
         [g for g in two if g.startswith("main.")], [])
    want("it guesses the whole name", "mainstreethomes.com" in two, True)
    # "The London Company" is one distinctive word once the furniture is gone,
    # so it does get london.com -- and `confirms` is what stops it counting.
    want("a name that is one word after the furniture still tries it",
         "london.com" in gather.guesses("The London Company"), True)
    three = gather.guesses("Virginia Commonwealth University")
    want("three words may drop one", "virginiacommonwealth.com" in three, True)
    want("and may try initials", "vcu.com" in three, True)
    want("a one-word name still gets its word",
         gather.guesses("Phlow")[0], "phlow.com")

    # -- does this page belong to this company ------------------------------
    # Half the words was the first rule and it let Cavalier Telephone resolve
    # to brandforce.com.
    want("every distinctive word has to be in the title",
         gather.confirms("Cavalier Telephone", "Brandforce | Telephone", "",
                         "brandforce.com"), False)
    want("all of them, and it passes",
         gather.confirms("Cavalier Telephone", "Cavalier Telephone - Home",
                         "", "cavtel.com"), True)
    home = gather.home_terms()[0]
    want("a one-word name must also prove it is in this metro",
         gather.confirms("Phlow", "Phlow - Enterprise Intelligence",
                         "we are based in london", "phlow.com"), False)
    want("and passes when the page says where it is",
         gather.confirms("Phlow", "Phlow - Home",
                         f"offices in {home} since 2020", "phlow.com"), True)
    # ", VA" is a local term and every minified script on earth contains
    # ", var x", which is why this is a whole-word test and not `in`.
    want("a metro term inside another word does not count",
         gather._says_home("function f(a, variable) {}"), False)
    # A site that refuses scripted clients can still be confirmed, but only by
    # the hostname carrying the whole name.
    want("a blocked page falls back to the hostname",
         gather.confirms("CarMax", "__blocked__", "", "carmax.com"), True)
    want("and a blocked page that does not is refused",
         gather.confirms("CarMax", "__blocked__", "", "autotrader.com"), False)

    # -- reading a roster page ----------------------------------------------
    html = """<table>
      <tr><td><a href="https://www.acmehealth.com/">Acme Health System</a></td></tr>
      <tr><td><a href="/about">About this list</a></td></tr>
      <tr><td><a href="https://bigbank.com/careers">Big Bank</a></td></tr>
      <tr><td><a href="https://publisher.test/contact">Contact</a></td></tr>
    </table>"""
    rows = gather.links_to_candidates(html, "https://publisher.test/employers")
    want("a table of links is two columns with no typing",
         sorted((r.name, r.site) for r in rows),
         [("Acme Health System", "acmehealth.com"),
          ("Big Bank", "bigbank.com")])
    want("and everything it found is marked as listed",
         all(r.listed for r in rows), True)

    # -- ranking ------------------------------------------------------------
    listed = gather.Candidate(name="A", site="a.com", sources={"page"},
                              listed=True)
    mapped = gather.Candidate(name="B", site="b.com", sources={"map"}, pins=2)
    both = gather.Candidate(name="C", site="c.com", sources={"map", "wikidata"},
                            pins=1)
    want("a human's list outranks a map pin",
         listed.score() > mapped.score(), True)
    want("two sources that do not talk outrank one",
         both.score() > mapped.score(), True)
    # Penalising OSM's `brand` tag deletes CarMax, Wegmans and Truist along
    # with the fast food, so it is a note on the row and not a penalty.
    chain = gather.Candidate(name="D", site="d.com", sources={"map"}, pins=2,
                             branded=True)
    want("a chain tag costs nothing", chain.score(), mapped.score())

    # -- filling in the website column --------------------------------------
    # Rewriting this file through a TOML parser would drop every comment, and
    # the comments say where each name came from.
    text = ('companies = [\n'
            '  "Acme Health System",  # chamber list\n'
            '  { name = "Big Bank", site = "https://bigbank.com" },\n'
            ']\n')
    filled, count = gather.fill_sites(
        text, discover.Budget(9), log=lambda m: None,
        find=lambda name, budget: "acmehealth.com")
    want("the bare name gets a website", count, 1)
    want("its comment survives", "# chamber list" in filled, True)
    want("the new row is a table",
         'site = "https://acmehealth.com"' in filled, True)
    want("and the row that was already done is untouched",
         '{ name = "Big Bank", site = "https://bigbank.com" },' in filled, True)

    print("\n" + "=" * 72)
    if fails:
        print(f"{len(fails)} gatherer check(s) broke:")
        for line in fails:
            print("  " + line)
        return 1
    print("the gatherer holds")
    return 0


def dates_check() -> int:
    """A posting cannot have gone up after the run that first saw it.

    The sitemap lane reads `<lastmod>`, which is the day the page last
    changed. A statewide board that regenerates a posting stamps it with
    today, and on 2026-09-16 that put five reqs first seen the previous
    afternoon back at the top of the board reading "today".
    """
    import tempfile
    from datetime import datetime, timedelta, timezone

    print("Post-date self-check")
    print("=" * 72)
    failures = []
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    with tempfile.TemporaryDirectory() as tmp:
        store = dedupe.SeenStore(Path(tmp) / "seen.json")

        def job(posted):
            return Job(title="Contract Administrator", company="Commonwealth",
                       url="https://example.gov/job/contract-administrator",
                       source="sitemap", posted_at=posted)

        # Nothing on file yet: a first sighting has nothing to contradict.
        fresh = job(now)
        store.settle_posted(fresh)
        if fresh.posted_at != now:
            failures.append("a first sighting was second-guessed")
        store.record(fresh)

        # Now the store has seen it. Back-date that sighting to yesterday and
        # have the source claim the posting went up today.
        rec = store._data[fresh.uid]
        rec["first_seen"] = yesterday.isoformat()

        bumped = job(now)
        store.settle_posted(bumped)
        if bumped.posted_at != yesterday:
            failures.append(
                f"a bumped lastmod survived: {bumped.posted_at} != {yesterday}")

        old = now - timedelta(days=9)
        honest = job(old)
        store.settle_posted(honest)
        if honest.posted_at != old:
            failures.append("a date older than the first sighting was moved")

        undated = job(None)
        store.settle_posted(undated)
        if undated.posted_at is not None:
            failures.append("a posting with no date was given one")

    print("\n" + "=" * 72)
    if failures:
        print(f"{len(failures)} post-date expectation(s) broke:")
        for line in failures:
            print("  " + line)
        return 1
    print("post dates hold: nothing is newer than the run that found it")
    return 0


def salary_check() -> int:
    """Hold the salary reader to the shapes that actually appear in postings.

    Every case here is copied from a live body in `data/radar/candidates.json`
    on 2026-09-15, including the ones that must NOT parse. Before this, 181
    postings had a description and no salary; 63 of those had a dollar figure
    sitting in the text. The split between the two halves of this list is the
    whole job: an ad is full of money that is not pay.
    """
    print("Salary parsing self-check")
    print("=" * 72)

    def body(text: str) -> Job:
        job = Job(title="Analyst", company="Example", url="https://x/1",
                  source="greenhouse")
        job.description = text
        return job

    pays = [
        # Greenhouse renders the band as markup with an entity for the dash.
        ('<div class="pay-range"><span>$72,000</span>'
         '<span class="divider">&mdash;</span><span>$115,000 USD</span></div>',
         (72_000, 115_000), "greenhouse pay-range markup"),
        # Workday puts the unit between the number and the separator.
        ("Our cash compensation amount for this role is $111,160/yr to "
         "$138,950/yr in Denver.",
         (111_160, 138_950), "a range with /yr on both ends"),
        # An hourly band. Read as thousands it would be $14,780, below the
        # floor, and the posting would be discarded as unpaid.
        ("The pay range estimated for this position based in Virginia is "
         "$14.78-$19.00.",
         (14.78 * 2080, 19.00 * 2080), "an hourly range becomes annual"),
        # A starting salary and a raise schedule, which is not a band. Reading
        # it as $40k-$42k would invent a ceiling the employer never wrote, so
        # the floor is all that comes back and the table shows "$40k+".
        ("Compensation & Benefits $40,000, with an increase to $42,000 after "
         "the initial 90-day probationary period.",
         (40_000, None), "one figure on a cue is a floor, not a band"),
        ("Salary Range: $60,000 - $70,000", (60_000, 70_000), "the plain case"),
        ("This role pays $95k - $115k depending on experience.",
         (95_000, 115_000), "k suffixes"),
        ("Compensation: $32.50 per hour", (32.5 * 2080, 32.5 * 2080),
         "a lone hourly rate"),
        ("The base salary range is US$90,000 - US$120,000.",
         (90_000, 120_000), "US$ is still dollars"),
    ]

    does_not_pay = [
        # Workday's unfilled template. It is a real string in real postings.
        ("The annual full time base salary range for this role is "
         "$1.00 - $1.00. Specific compensation is determined through "
         "interviews.", "a $1.00 placeholder is not a salary"),
        ("We automate how over $200B in annualized spend flows in and out of "
         "70,000+ companies.", "company metrics are not pay"),
        ("Over 100,000 cleaning professionals have earned $250M+ through our "
         "platform.", "platform totals are not pay"),
        ("Educational assistance up to $2500 per year. Life insurance & AD&D: "
         "100% employer-paid coverage valued at $10,000 each.",
         "benefits are not a salary band"),
        # Cayman Islands dollars, on a Cayman Islands job. Read as USD it is a
        # plausible $60k-$80k band, which is the worst kind of wrong.
        ("SALARY: CI$60,000 - CI$80,000 pa WORKING HOURS: 40 HOURS PER WEEK",
         "another country's dollars are not a US salary"),
        ("", "an empty body says nothing"),
    ]

    failures = []
    def near(got, want):
        if want is None:
            return got is None
        return got is not None and abs(got - want) < 1

    for text, (want_low, want_high), label in pays:
        got = score.parse_salary(body(text))
        ok = near(got[0], want_low) and near(got[1], want_high)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}")
        if not ok:
            failures.append(f"{label}: got {got}, wanted "
                            f"({want_low}, {want_high})")

    for text, label in does_not_pay:
        got = score.parse_salary(body(text))
        ok = got == (None, None)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}")
        if not ok:
            failures.append(f"{label}: got {got}, wanted nothing")

    # schema.org baseSalary, which the sitemap lane reads off a posting page.
    schema = [
        ({"value": {"minValue": 48721, "maxValue": 79172, "unitText": "YEAR"}},
         (48_721, 79_172), "an annual MonetaryAmount"),
        ({"value": {"minValue": 23.50, "maxValue": 38.07, "unitText": "HOUR"}},
         (23.50 * 2080, 38.07 * 2080), "an hourly MonetaryAmount"),
        ({"value": {"value": 65000, "unitText": "YEAR"}},
         (65_000, 65_000), "a single stated figure"),
        ({"value": {"minValue": 0, "maxValue": 0, "unitText": "YEAR"}},
         (None, None), "a zeroed band is not a band"),
    ]
    for base, want, label in schema:
        got = ats._schema_salary({"baseSalary": base})
        ok = (got == want if want[0] is None else
              got[0] is not None and abs(got[0] - want[0]) < 1
              and abs(got[1] - want[1]) < 1)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}")
        if not ok:
            failures.append(f"{label}: got {got}, wanted {want}")

    # Ashby publishes the band in its own shape. The fetcher had asked for it
    # and thrown it away since the day it was written, so a Ramp posting
    # reading $128K - $180K on its own page was listed with an estimate of
    # $91k-$125k beside it.
    ashby = [
        ([{"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "USD", "minValue": 128000, "maxValue": 180000}],
         (128_000, 180_000), "an annual band, the Ramp case"),
        ([{"compensationType": "EquityPercentage", "interval": "NONE",
           "currencyCode": None, "minValue": None, "maxValue": None},
          {"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "USD", "minValue": 128000, "maxValue": 180000}],
         (128_000, 180_000), "equity beside it is not pay"),
        ([{"compensationType": "Salary", "interval": "1 HOUR",
           "currencyCode": "USD", "minValue": 30, "maxValue": 45}],
         (62_400, 93_600), "an hourly rate is annualised"),
        ([{"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "EUR", "minValue": 90000, "maxValue": 120000}],
         (None, None), "a band in euros is not a band in dollars"),
        ([{"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "USD", "minValue": 100000, "maxValue": 140000},
          {"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "USD", "minValue": 128000, "maxValue": 180000}],
         (100_000, 180_000), "per-location tiers give the widest honest band"),
    ]
    for parts, want, label in ashby:
        got_map = ats._ashby_salary({"summaryComponents": parts})
        got = (got_map.get("salary_min"), got_map.get("salary_max"))
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}")
        if not ok:
            failures.append(f"{label}: got {got}, wanted {want}")
    blank = ats._ashby_salary(None)
    print(f"  {'ok  ' if blank == {} else 'FAIL'} a posting with no "
          f"compensation block says nothing: {blank}")
    if blank != {}:
        failures.append("no compensation block should yield no kwargs")

    print("\n" + "=" * 72)
    if failures:
        print(f"{len(failures)} salary expectation(s) broke:")
        for line in failures:
            print("  " + line)
        return 1
    print(f"all {len(pays) + len(does_not_pay) + len(schema) + len(ashby) + 1} "
          f"salary expectations hold")
    return 0


def plugins_check() -> int:
    """Hold the delivery registry to its two promises, with no network.

    One: a plugin whose credentials are missing is skipped, not called. Two: a
    plugin that blows up does not take the rest of the run with it -- delivery
    happens just before seen.json is written, so an exception escaping here
    would cost the run its memory of every posting it just found.
    """
    from jobdesk.radar import plugins

    print("Delivery plugin self-check")
    print("=" * 72)
    failures = []
    calls = []

    def stub(name, ok, boom=False):
        mod = types.SimpleNamespace(NAME=name)
        mod.configured = lambda: ok
        def deliver(jobs, stats, new_only, log=print):
            calls.append(name)
            if boom:
                raise RuntimeError("webhook said no")
        mod.deliver = deliver
        return mod

    lines = []
    saved = plugins.PLUGINS
    try:
        plugins.PLUGINS = (stub("Off", False), stub("Angry", True, boom=True),
                           stub("Fine", True))
        plugins.deliver([], {}, [], log=lines.append)
    finally:
        plugins.PLUGINS = saved

    for line in lines:
        print("  " + line)

    if calls != ["Angry", "Fine"]:
        failures.append(f"expected Angry then Fine to run, got {calls}")
    if not any("Off" in ln and "not configured" in ln for ln in lines):
        failures.append("an unconfigured plugin should say so in the log")
    if not any("Angry" in ln and "webhook said no" in ln for ln in lines):
        failures.append("a plugin's exception should be logged, not swallowed silently")

    for mod in saved:
        for attr in ("NAME", "configured", "deliver"):
            if not hasattr(mod, attr):
                failures.append(f"{mod.__name__} has no {attr}")

    print()
    print("=" * 72)
    if failures:
        print(f"{len(failures)} plugin expectation(s) broke:")
        for line in failures:
            print("  " + line)
        return 1
    print(f"the registry holds, {len(saved)} plugin(s) registered")
    return 0


def discord_check() -> None:
    """Score the synthetic jobs and post them to the real webhook.

    Confirms the DISCORD_WEBHOOK_URL is set, reachable, and that the embeds
    render the way you want -- without waiting for a live run to surface an
    A-tier job. Two of the five synthetics are C-tier or better, so something
    will actually post.
    """
    from jobdesk.radar.plugins import discord
    config.load_env()
    if not discord.configured():
        print("DISCORD_WEBHOOK_URL is not set in .env - nothing to test.")
        return
    for job in SYNTHETIC:
        score.score_job(job)
    stats = {"sources": {"test": len(SYNTHETIC)}}
    sent = discord.push(SYNTHETIC, stats, SYNTHETIC, log=lambda m: print(m))
    print(f"\nDone - {sent} message(s) posted. Check the channel.")


def live(only: str | None) -> None:
    config.load_env()
    print(f"Collecting{f' (filter: {only})' if only else ''}...\n")
    jobs, stats = sources.collect(log=lambda m: print(m), only=only)
    if not jobs:
        print("\nNothing collected.")
        return

    for job in jobs:
        score.score_job(job)
    sources.fetch_details(jobs, log=lambda m: print(m))

    jobs, collapsed = dedupe.collapse(jobs)
    jobs = score.score_all(jobs)
    print(f"\n{collapsed} duplicate(s) collapsed\n")

    # No seen-store in the dev loop: show everything worth showing, every run.
    worth = [j for j in jobs if j.score >= config.MIN_SCORE_TO_REPORT]
    print(render.console(jobs, stats, worth))

    if stats.get("errors"):
        print("\nERRORS")
        for err in stats["errors"]:
            print("  " + err.replace("\n", "\n  "))


def partials_check() -> int:
    """Turning an aggregator snippet back into the posting.

    Nothing here touches the network. `partial_detail` takes one page of HTML
    and decides whether to believe it, and that decision is the whole module:
    the fetch either works or it does not, but believing the wrong page builds
    a packet against a job nobody applied for.
    """
    from jobdesk.radar.sources import ats
    from jobdesk.apply import jdtext
    from jobdesk.apply.candidates import Candidate

    print("Snippet repair self-check")
    print("=" * 72)
    fails: list[str] = []

    def want(label: str, got, expected):
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            fails.append(f"{label}: got {got!r}, expected {expected!r}")

    def page(title: str, body: str, kind="JobPosting") -> str:
        node = json.dumps({"@type": kind, "title": title, "description": body})
        return f'<script type="application/ld+json">{node}</script>'

    # Stripped, because `Job` strips what it is handed and an assertion that
    # compares against the unstripped fixture fails on a trailing space.
    full = ("Responsibilities. " * 80).strip()   # over MIN_FULL_BODY
    snippet = ("About us. " * 50).strip()        # the 500-character About Us

    # -- reading the markup -------------------------------------------------
    want("a JobPosting is found", len(ats.job_postings_in(page("BA", full))), 1)
    want("an Organization is not a posting",
         ats.job_postings_in(page("BA", full, kind="Organization")), [])
    want("a @graph wrapper is unwrapped",
         len(ats.job_postings_in(
             '<script type="application/ld+json">'
             + json.dumps({"@graph": [{"@type": "WebPage"},
                                      {"@type": "JobPosting", "title": "BA"}]})
             + "</script>")), 1)
    # Schema.org allows a list of types and several generators emit one.
    want("a list of types still counts",
         len(ats.job_postings_in(
             '<script type="application/ld+json">'
             + json.dumps({"@type": ["JobPosting", "Thing"], "title": "BA"})
             + "</script>")), 1)
    want("broken JSON is skipped, not raised",
         ats.job_postings_in('<script type="application/ld+json">{oops</script>'),
         [])

    # -- where else the same ad is readable ---------------------------------
    # Adzuna's outbound link is guarded and its own detail page is not, and
    # both carry the same ad id.
    want("an aggregator's guarded link has a fallback",
         ats._urls_to_try("https://www.adzuna.com/land/ad/123?se=x"),
         ["https://www.adzuna.com/land/ad/123?se=x",
          "https://www.adzuna.com/details/123"])
    want("and a company's own posting has nowhere else to be",
         ats._urls_to_try("https://boards.greenhouse.io/acme/jobs/1"),
         ["https://boards.greenhouse.io/acme/jobs/1"])

    # -- what gets believed -------------------------------------------------
    def try_page(html: str, title="Business Analyst", body=snippet,
                 status=200, headers=None):
        job = Job(title=title, company="Lumen", url="https://x.test/1",
                  source="adzuna", description=body, partial=True)
        saved = ats.http.get
        ats.http.get = lambda *a, **k: type(
            "R", (), {"status_code": status, "text": html,
                      "url": "https://x.test/1",
                      "headers": headers or {}})()
        try:
            return ats.partial_detail(job), job
        finally:
            ats.http.get = saved

    ok, job = try_page(page("Senior Business Analyst", full))
    want("the posting replaces the snippet", ok, True)
    want("and the snippet is gone", job.description.startswith("Responsibilities"),
         True)
    want("and it is no longer partial", job.partial, False)
    # The title the digest went out with is the one the user recognizes.
    want("the title the user read is kept", job.title, "Business Analyst")

    ok, job = try_page(page("Careers at Lumen", full))
    want("a careers index is not this job", ok, False)
    want("and the snippet survives the refusal", job.description, snippet)

    # -- a bot check is not a fact about this posting -----------------------
    # jobs.virginia.gov sits behind AWS WAF and answers a challenged request
    # with 202, no body and a header saying so. Returning False here would be
    # a lie the caller cannot see through: it reads identically to a dead
    # link, and one repair run reported 78 live Commonwealth postings as
    # expired on the strength of it.
    def challenged(html, headers):
        try:
            try_page(html, headers=headers, status=202)
        except ats.Challenged as why:
            return str(why)
        return ""

    waf = challenged("", {"x-amzn-waf-action": "challenge"})
    want("a WAF challenge is raised, not returned", bool(waf), True)
    want("and it names the WAF", "AWS WAF" in waf, True)
    want("an unlabelled empty page is a challenge too",
         bool(challenged("   ", {})), True)
    want("but a real page with no markup is only a miss",
         challenged("<html>no markup here</html>", {}), "")
    want("and it is still flagged partial", job.partial, True)

    ok, _ = try_page(page("Business Analyst", "Apply today."))
    want("a two-line stub does not beat 500 characters", ok, False)
    ok, _ = try_page("<html>no markup here</html>")
    want("a page with no markup fills nothing", ok, False)
    # An untitled node cannot be checked, so it is judged on the body alone
    # rather than refused -- the length gate is still standing.
    ok, _ = try_page('<script type="application/ld+json">'
                     + json.dumps({"@type": "JobPosting", "description": full})
                     + "</script>")
    want("an untitled posting is judged on its body", ok, True)

    # -- the flag travels with the text ------------------------------------
    def merged(winner_desc, winner_partial, loser_desc, loser_partial):
        win = Job(title="Analyst", company="Acme", url="https://acme.test/1",
                  source="greenhouse", description=winner_desc,
                  partial=winner_partial)
        lose = Job(title="Analyst", company="Acme", url="https://acme.test/1",
                   source="adzuna", description=loser_desc, partial=loser_partial)
        kept, _ = dedupe.collapse([lose, win])
        return kept[0]

    got = merged("", False, snippet, True)
    want("an empty winner takes the snippet", got.description, snippet)
    want("and is told it is a snippet", got.partial, True)
    got = merged(full, False, snippet, True)
    want("a full body is not traded for a snippet", got.description, full)
    want("and stays complete", got.partial, False)

    # -- the packet builder -------------------------------------------------
    cand = Candidate(title="BA", company="Lumen", url="https://x.test/1",
                     description=snippet, flags=["partial-description"])
    want("the candidate knows its body is a snippet",
         cand.partial_description, True)
    text, how = jdtext.obtain(cached=snippet, url="", allow_fetch=False,
                              allow_paste=False, cached_partial=True,
                              echo=lambda m: None)
    # 500 characters clears MIN_JD_CHARS of 400, which is exactly the trap.
    want("a snippet is not tailored against", text, "")
    want("and the reason says so", "snippet" in how, True)
    text, _ = jdtext.obtain(cached=full, url="", allow_fetch=False,
                            allow_paste=False, echo=lambda m: None)
    want("a real body still short-circuits the ladder", text, full)

    print()
    for line in fails:
        print("  FAILED: " + line)
    print(f"{'PASS' if not fails else 'FAIL'}  {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--plugins" in args:
        raise SystemExit(plugins_check())
    elif "--scoring" in args:
        raise SystemExit(scoring_check() or rules_check()
                         or salary_check() or dates_check()
                         or gather_check() or partials_check())
    elif "--salary" in args:
        raise SystemExit(salary_check())
    elif "--discord" in args:
        discord_check()
    else:
        live(args[0] if args else None)
