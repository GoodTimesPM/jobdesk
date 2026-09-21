"""Self-checks. `py tests/test_engine.py` runs everything; sections run alone.

    py tests/test_engine.py            all
    py tests/test_engine.py --vocab    alias false-positive regressions
    py tests/test_engine.py --jd       section splitting, years, families
    py tests/test_engine.py --tailor   selection, variants, summary assembly
    py tests/test_engine.py --truth    the truthfulness guards
    py tests/test_engine.py --render   PDF/DOCX/TXT output and the ATS simulator

Everything here runs against `profile.example/`, pinned below before anything
is imported, and never against whatever profile happens to be active. These
are assertions about content: which bullet a posting selects, which alias must
not fire, which summary clause a family gets. Pointed at someone else's
targeting file they would be assertions about nothing, and they would go red
the first time a user edited their own vocabulary.

That pinning is also what keeps the repo free of the author's resume. The
candidate below is Wren Adeyemi, who does not exist.

Every case in --vocab is a real false positive caught on a real posting, with
the posting named in the comment. That list only grows -- an alias that fired
wrongly once will fire wrongly again the moment someone "improves" the
vocabulary, which is exactly what a regression test is for.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["JOBDESK_PROFILE"] = str(ROOT / "profile.example")

from jobdesk.engine import ats, gap, jd as jd_mod, master as master_mod, tailor, verify
from jobdesk.engine import render_docx, render_pdf, render_txt
from jobdesk.engine.vocab import load as load_vocab

VOCAB = load_vocab()
MASTER = master_mod.load()

_failures: list[str] = []


def ok(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        _failures.append(label)


def parse(text: str, company: str = "Acme", title: str = "Accountant"):
    return jd_mod.parse(text, company, title, VOCAB)


# --------------------------------------------------------------------------
ACCOUNTING_JD = """
Senior Accountant is not this role -- this is a Staff Accountant opening.

What you will do:
Own the month-end close and post recurring and adjusting journal entries
Reconcile balance sheet and bank accounts in QuickBooks and NetSuite
Prepare financial statements and explain variances to the controller

What you will need:
1-2 years of experience in an accounting role
Strong reconciliation and Excel skills
Bachelor of Science in Accounting

Preferred qualifications:
SAP experience
Exposure to Sage Intacct
"""

BILLING_JD = """
About the role:
Accounts Payable Specialist processing invoices for 11 locations.

Responsibilities:
Process vendor invoices and run the weekly check run
Apply three-way match against purchase orders and receiving records
Handle vendor inquiries and onboard new suppliers
Maintain the AP aging and clear past due balances

Requirements:
0-2 years of accounts payable or billing experience
Familiarity with Bill.com and QuickBooks
Excellent communication and attention to detail
"""

AUDIT_JD = """
Internal Auditor, Compliance

What we are looking for:
5+ years of audit experience
Knowledge of internal controls, SOX, and risk assessment
Experience testing approval workflows and segregation of duties
Strong documentation skills

Nice to have:
Experience with process improvement
"""


FIXED_ASSET_JD = """
Fixed Asset Accountant

Responsibilities:
Maintain the fixed asset register and the depreciation schedule
Reconcile the asset subledger to the general ledger each quarter
Review capitalization decisions against policy

Requirements:
1-2 years of general ledger experience
"""


def test_vocab() -> None:
    print("\n[vocab] alias false-positive regressions")
    # The one every accounting vocabulary gets wrong. A bare "sheets" alias for
    # Google Workspace fires on "balance sheets", which appears in essentially
    # every posting in the field, and the tool then lands in the summary line.
    hits = VOCAB.find("reconcile the balance sheets monthly")
    ok("google-workspace" not in hits, "'balance sheets' does not match Google Sheets")
    ok("reconciliation" in hits, "...but it still matches Reconciliation")

    # "ap" as an alias fires inside "apply" and "approach". The AP term spells
    # the phrases out for exactly this reason.
    hits = VOCAB.find("apply now and approach the work with curiosity")
    ok("accounts-payable" not in hits, "'apply/approach' does not match Accounts Payable")

    # "ad hoc" is in a large share of accounting JDs; a bare "ad" alias would
    # have credited ADP for every one of them.
    hits = VOCAB.find("produce ad hoc reporting for the controller")
    ok("adp" not in hits, "'ad hoc' does not match ADP")
    ok("reporting" in hits, "'ad hoc reporting' still matches Reporting")

    # Things that must still match.
    for text, term in (
        ("proficiency in Power BI", "powerbi"),
        ("experience with QuickBooks Online", "quickbooks"),
        ("apply a three-way match before payment", "accounts-payable"),
        ("writing T-SQL stored procedures", "sql"),
        ("prepare the PBC list for the external auditors", "audit-support"),
    ):
        ok(term in VOCAB.find(text), f"{text!r} -> {term}")


def test_jd() -> None:
    print("\n[jd] sections, years, family")
    d = parse(ACCOUNTING_JD, "Acme", "Staff Accountant")
    ok("required" in d.sections and "preferred" in d.sections,
       "required and preferred split apart")
    ok("reconciliation" in d.required_terms, "reconciliation read as required")
    ok("sap" in d.weights and "sap" not in d.required_terms,
       "SAP read as preferred, not required")
    ok(d.weight_of("reconciliation") > d.weight_of("sap"),
       "a required term outweighs a preferred one")
    ok(d.family() == "accounting", "'Staff Accountant' -> accounting family")
    ok(d.years_required == 2, f"binding years read as 2 (got {d.years_required})")

    s = parse(BILLING_JD, "Acme", "Accounts Payable Specialist")
    ok(s.family() == "billing", "'Accounts Payable Specialist' -> billing family")
    ok("bill-com" in s.weights, "Bill.com found")

    a = parse(AUDIT_JD, "Acme", "Internal Auditor")
    ok(a.family() == "audit", "'Internal Auditor' -> audit family")
    ok(a.years_required == 5, f"'5+ years' read as 5 (got {a.years_required})")

    # Job Radar hit this on a live posting: a calendar year in the text parsed
    # as a 14-year requirement.
    y = parse("Requirements:\nRevenue grew in 2014 year over year\nBachelor of Science")
    ok(y.years_required is None,
       f"'2014 year over year' is not a years requirement (got {y.years_required})")

    # A sentence that merely contains the word is not a heading.
    body = parse("We have a long list of requirements for this role and they "
                 "are all negotiable.\nSage Intacct experience")
    ok("required" not in body.sections,
       "a prose sentence containing 'requirements' does not open a section")


def test_tailor() -> None:
    print("\n[tailor] selection, variants, summary")
    d = parse(ACCOUNTING_JD, "Acme", "Staff Accountant")
    plan = tailor.build(MASTER, d, VOCAB)
    ids = [c.bullet.id for c in plan.all_chosen()]
    ok(len(set(ids)) == len(ids), "no bullet selected twice")
    groups = [g for g, _ in plan.skills]
    ok(groups.index("Core Accounting") < groups.index("Process & Controls"),
       f"the JD reorders the skill groups (got {groups})")
    ok("closing the books on schedule" in plan.summary,
       "an accounting JD gets the accounting middle clause")
    ok(any(c.bullet.id == "frpg.close" for c in plan.all_chosen()),
       "an accounting JD selects the month-end close bullet")
    # A JD asking for financial statements and variance explanations gets the
    # reporting phrasing of the budget bullet, not the budget-package one.
    ok(any(c.variant.id == "frpg.variance.reporting" for c in plan.all_chosen()),
       "a reporting JD swaps in the reporting phrasing of the variance bullet")

    s = parse(BILLING_JD, "Acme", "Accounts Payable Specialist")
    splan = tailor.build(MASTER, s, VOCAB)
    ok("exceptions pile up" in splan.summary,
       "a billing JD gets the billing middle clause")
    ok("payable" in splan.summary.lower(),
       "a billing JD gets a payables opening")
    ok(any(c.bullet.id == "frpg.ap" for c in splan.all_chosen()),
       "a billing JD selects the AP bullet")
    # The base phrasing is what is on the live resume, so it is only replaced
    # when the JD gives a reason. This posting wants exactly what the base
    # bullet already says, so nothing should be swapped in.
    ok(all(c.variant.id == "frpg.ap" for c in splan.all_chosen()
           if c.bullet.id == "frpg.ap"),
       "a bullet the JD doesn't differentiate keeps its base phrasing")
    ok(any(c.bullet.id == "meridian.vendor" for c in splan.all_chosen()),
       "a billing JD selects the vendor-inquiry bullet")

    a = parse(AUDIT_JD, "Acme", "Internal Auditor")
    aplan = tailor.build(MASTER, a, VOCAB)
    ok("audit-preparation" in aplan.summary.lower(),
       "an audit JD gets the audit opening")
    ok(any(c.bullet.id == "frpg.audit" for c in aplan.all_chosen()),
       "an audit JD selects the audit-schedule bullet")
    ok(any("5 years" in n for n in aplan.notes),
       "a 5-year requirement raises a note")

    # Coverage, not top-N: a bullet's phrasings must not all appear at once.
    ok(sum(1 for c in aplan.all_chosen() if c.bullet.id == "frpg.audit") == 1,
       "greedy coverage never picks two phrasings of the same bullet")

    # Every role keeps its floor.
    for section in plan.experience:
        ok(len(section.chosen) >= section.entry.min_bullets,
           f"{section.entry.id} keeps min_bullets")

    # Skills are ordered by the JD, but never invented.
    flat = " ".join(i for _, items in plan.skills for i in items).lower()
    ok("sap" not in flat,
       "a preferred skill the candidate lacks never appears in SKILLS")
    cov = tailor.coverage(plan, VOCAB)
    ok("SAP" in cov.wanted_missed or "SAP" in cov.required_missed,
       "...it is reported as a gap instead")


def test_truth() -> None:
    print("\n[truth] the guards that keep output honest")
    problems = master_mod.check(MASTER, VOCAB)
    ok(not problems, f"live master content passes check ({problems[:2]})")

    # A variant that invents a metric must be rejected.
    tampered = master_mod.load()
    bullet = tampered.bullet("frpg.ap")
    assert bullet is not None
    bullet.variants = (master_mod.Variant(
        id="frpg.ap.bad", tags=("accounts-payable",),
        text="Process roughly 900 vendor invoices monthly in Bill.com.",
    ),)
    found = master_mod.check(tampered, VOCAB)
    ok(any("renumber" in p for p in found),
       "a variant that changes 400 invoices to 900 is rejected")

    # Drafts never reach a resume by default. FIXED_ASSET_JD is written to
    # want the one draft bullet in master and little else, so this is the
    # difference between a plan that answers the posting and one that cannot.
    fa = parse(FIXED_ASSET_JD, "Acme", "Fixed Asset Accountant")
    fa_plan = tailor.build(MASTER, fa, VOCAB)
    ok(not any(c.bullet.draft for c in fa_plan.all_chosen()),
       "no draft bullet is selected by default")

    d = parse(ACCOUNTING_JD, "Acme", "Staff Accountant")
    plan = tailor.build(MASTER, d, VOCAB)
    ok(not verify.verify_plan(plan), "verify_plan passes on a clean plan")

    # And a plan carrying text that is not in master.toml is caught.
    plan.all_chosen()[0].variant.text = "Led a team of 12 staff accountants."
    ok(any("approved phrasings" in p for p in verify.verify_plan(plan)),
       "invented bullet text fails verification")

    with_drafts = tailor.build(MASTER, fa, VOCAB, include_draft=True)
    ok(any(c.bullet.draft for c in with_drafts.all_chosen()),
       "--include-draft does let drafts through")
    ok(any("draft content" in p for p in verify.verify_plan(with_drafts)),
       "...and verify still flags them when the flag isn't passed")


def _heading(name: str, text: str) -> bool:
    """Is `name` a section heading here, rather than a word in a sentence?"""
    return any(line.strip().upper().rstrip(":") == name
               for line in text.splitlines())


def test_render() -> None:
    print("\n[render] output formats and the ATS simulator")
    d = parse(ACCOUNTING_JD, "Acme", "Staff Accountant")
    plan = tailor.build(MASTER, d, VOCAB)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        pdf_path = out / "resume.pdf"
        fit = render_pdf.render_fitted(plan, pdf_path)
        pages, slack = fit.pages, fit.slack
        ok(pages == 1, f"PDF fits one page (got {pages})")
        ok(slack >= 0, f"content ends above the bottom margin ({slack:.0f}mm)")
        ok(not fit.broke_floors,
           "and it did not have to cross a min_bullets floor to get there")

        docx_path = render_docx.render(plan, out / "resume.docx", fit.layout)
        txt_path = render_txt.render(plan, out / "resume.txt")
        ok(docx_path.exists() and docx_path.stat().st_size > 0, "DOCX written")
        ok(txt_path.exists(), "TXT written")

        from docx import Document
        doc_text = "\n".join(p.text for p in Document(str(docx_path)).paragraphs)
        ok(len(Document(str(docx_path)).tables) == 0,
           "DOCX contains no tables (the two-column trap)")
        ok(plan.all_chosen()[0].text in doc_text,
           "a selected bullet survives into the DOCX")

        # The PDF ships with empty document properties; the DOCX has to match,
        # or the properties pane is the one place the toolchain talks.
        import zipfile
        with zipfile.ZipFile(docx_path) as archive:
            core = archive.read("docProps/core.xml").decode("utf-8")
            app = archive.read("docProps/app.xml").decode("utf-8")
        ok("python-docx" not in core, "DOCX properties do not name python-docx")
        ok("2013" not in core, "DOCX carries a real date, not the template's")
        ok("Microsoft" not in app, "DOCX does not claim to be a Word build")
        ok(plan.master.identity["name"] in core, "DOCX author is the candidate")

        report = ats.simulate(pdf_path, d, VOCAB)
        ok(report.parse_score >= 90,
           f"ATS parse score {report.parse_score}/100")
        ok(not report.sections_missing,
           f"all standard headings parsed (missing: {report.sections_missing})")
        ok(report.contact.get("email") == MASTER.identity["email"],
           "email parses out of the PDF text layer")
        ok(report.contact.get("phone"), "phone parses out of the PDF text layer")
        ok(report.images == 0, "no images embedded")
        ok(not verify.verify_pdf(plan, report.text),
           "every selected bullet is present in the PDF text layer")

        # -- one page, whatever it costs -------------------------------
        # Every renderer guards on `plan.summary`, and all three have to.
        none_plan = tailor.build(MASTER, d, VOCAB, summary_mode="none")
        ok(none_plan.summary == "", "summary_mode='none' builds no summary")
        none_pdf = out / "nosummary.pdf"
        render_pdf.render_fitted(none_plan, none_pdf)
        none_report = ats.simulate(none_pdf, d, VOCAB)
        ok(not _heading("SUMMARY", none_report.text),
           "and the heading is gone from the PDF text layer")
        ok(not none_report.sections_missing,
           "a resume with no summary is not penalized for a missing section")
        none_txt = render_txt.render(none_plan, out / "nosummary.txt")
        ok(not _heading("SUMMARY", none_txt.read_text(encoding="utf-8")),
           "and gone from the TXT")
        none_docx = render_docx.render(none_plan, out / "nosummary.docx")
        from docx import Document as _Doc
        ok(not _heading("SUMMARY", "\n".join(
            par.text for par in _Doc(str(none_docx)).paragraphs)),
           "and gone from the DOCX")

        # A plan with every bullet in the profile on it does not fit at the
        # designed size. It still has to come back as one page: the fitter
        # sets the type tighter, and drops bullets only once that runs out.
        stuffed = tailor.build(MASTER, d, VOCAB)
        for section in (*stuffed.experience, *stuffed.projects):
            for bullet in MASTER.bullets_for(section.entry.id, False):
                if not any(c.bullet.id == bullet.id for c in section.chosen):
                    section.chosen.append(
                        tailor.ChosenBullet(bullet=bullet,
                                            variant=bullet.phrasings()[0],
                                            value=0.0, gain=0.0, reason="test"))
        before = stuffed.bullet_count()
        ok(before > plan.bullet_count(), f"overstuffed plan has {before} bullets")
        squeezed = render_pdf.render_fitted(stuffed, out / "stuffed.pdf")
        ok(squeezed.pages == 1,
           f"an overstuffed plan still ships one page (got {squeezed.pages})")
        ok(squeezed.layout.squeezed,
           f"and it was set tighter to do it ({squeezed.layout.describe()})")
        ok(squeezed.layout.type >= 0.90,
           f"type never shrinks past 90% ({squeezed.layout.type:.0%})")

        # The gap report tolerates an empty corpus.
        rows, unmapped, total = gap.analyze(MASTER, VOCAB, store=out)
        ok(total == 0 and "No job descriptions stored" in
           gap.to_markdown(rows, unmapped, total),
           "gap report handles an empty corpus")


def main() -> int:
    flags = set(sys.argv[1:])
    run_all = not flags
    if run_all or "--vocab" in flags:
        test_vocab()
    if run_all or "--jd" in flags:
        test_jd()
    if run_all or "--tailor" in flags:
        test_tailor()
    if run_all or "--truth" in flags:
        test_truth()
    if run_all or "--render" in flags:
        test_render()

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
