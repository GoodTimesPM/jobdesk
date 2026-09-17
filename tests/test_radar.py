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
from jobdesk.radar import companies, discover, learn                # noqa: E402
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

    print("\n" + "=" * 72)
    if fails:
        print(f"{len(fails)} rule(s) broke:")
        for line in fails:
            print("  " + line)
        return 1
    print("all rules hold")
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

    print("\n" + "=" * 72)
    if failures:
        print(f"{len(failures)} salary expectation(s) broke:")
        for line in failures:
            print("  " + line)
        return 1
    print(f"all {len(pays) + len(does_not_pay) + len(schema)} "
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


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--plugins" in args:
        raise SystemExit(plugins_check())
    elif "--scoring" in args:
        raise SystemExit(scoring_check() or rules_check()
                         or salary_check() or dates_check())
    elif "--salary" in args:
        raise SystemExit(salary_check())
    elif "--discord" in args:
        discord_check()
    else:
        live(args[0] if args else None)
