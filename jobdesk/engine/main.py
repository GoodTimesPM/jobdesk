"""CLI entry point.

    py -m engine.main check
    py -m engine.main tailor --jd jd.txt --company "CarMax" --role "Business Analyst"
    py -m engine.main ats out/.../Your_Name_CarMax_Business_Analyst.pdf
    py -m engine.main gap

Console output is pure ASCII on purpose. Job Radar lost log lines to
UnicodeEncodeError on the Windows console codepage; the same trap is here, and
a dropped line in a truthfulness report is worse than an ugly one.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from .. import profile
from . import ats, config, gap, jd as jd_mod, master as master_mod, tailor, verify
from . import render_docx, render_pdf, render_txt
from .vocab import load as load_vocab


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "Role"


def _load(strict: bool = True):
    vocab = load_vocab()
    master = master_mod.load()
    problems = master_mod.check(master, vocab)
    if problems and strict:
        print("Master content has problems -- fix these before tailoring:\n")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    return vocab, master


# --------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    vocab, master = _load(strict=False)
    problems = master_mod.check(master, vocab)
    drafts = master_mod.drafts(master)

    live = [b for b in master.bullets if not b.draft]
    variants = sum(len(b.variants) for b in master.bullets)
    print(f"master.toml: {len(live)} live bullets ({variants} variants), "
          f"{len(drafts)} draft, {len(master.skills)} skills, "
          f"{len(master.coursework)} courses")
    print(f"vocabulary.toml: {len(vocab.terms)} terms")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nContent checks passed.")

    if drafts:
        print(f"\n{len(drafts)} draft bullet(s) -- excluded from every resume "
              f"until confirmed:")
        for b in drafts:
            print(f"\n  [{b.id}] {b.text}")
            if b.needs:
                print(f"    NEEDS: {b.needs}")
    return 1 if problems else 0


def cmd_tailor(args: argparse.Namespace) -> int:
    vocab, master = _load()

    raw = jd_mod.read_source(args.jd)
    posting = jd_mod.parse(raw, args.company, args.role, vocab)
    stored = jd_mod.store(posting)

    plan = tailor.build(master, posting, vocab,
                        include_draft=args.include_draft,
                        summary_mode=args.summary)

    stem = f"{profile.file_stem()}_{_safe(args.company)}_{_safe(args.role)}"
    out_dir = config.OUT / f"{date.today().isoformat()}_{posting.slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{stem}.pdf"

    fit = render_pdf.render_fitted(plan, pdf_path)
    pages, slack, dropped = fit.pages, fit.slack, fit.dropped
    render_docx.render(plan, out_dir / f"{stem}.docx", fit.layout)
    render_txt.render(plan, out_dir / f"{stem}.txt")

    # -- the truthfulness gate -------------------------------------------
    report = ats.simulate(pdf_path, posting, vocab)
    problems = verify.verify_plan(plan, args.include_draft)
    problems += verify.verify_pdf(plan, report.text)
    if problems:
        print("VERIFICATION FAILED -- the generated resume was NOT delivered:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    cover = tailor.coverage(plan, vocab)
    (out_dir / "ats_report.md").write_text(ats.to_markdown(report), encoding="utf-8")
    (out_dir / "TAILORING.md").write_text(
        _tailoring_report(plan, cover, report, fit, stored),
        encoding="utf-8",
    )

    delivered = None
    if not args.no_deliver:
        delivered = _deliver(out_dir, stem)

    # -- console ----------------------------------------------------------
    print(f"{args.company} -- {args.role}")
    print(f"  JD sections read : {', '.join(sorted(posting.sections)) or 'none'}")
    if posting.years_required:
        print(f"  Years required   : {posting.years_required}")
    print(f"  Bullets selected : {plan.bullet_count()}"
          + (f"  (dropped for page fit: {', '.join(dropped)})" if dropped else ""))
    print(f"  Page fit         : {pages} page(s), {slack:.0f}mm slack"
          + (f", set at {fit.layout.type:.0%} type" if fit.layout.type < 1 else ""))
    for line in _squeeze_advice(plan, fit):
        print(f"  ROOM TO GAIN     : {line}")
    print(f"  Required terms   : {cover.required_rate:.0%} covered "
          f"({len(cover.required_hit)}/{len(cover.required_hit) + len(cover.required_missed)})")
    print(f"  ATS parse score  : {report.parse_score}/100 ({report.verdict})")
    if report.keyword_rate is not None:
        print(f"  ATS keyword match: {report.keyword_rate:.0%}")
    if cover.required_missed:
        print(f"  NOT SAID         : {', '.join(cover.required_missed)}")
    for note in plan.notes:
        print(f"  NOTE             : {note}")
    print(f"  Written to       : {out_dir}")
    if delivered:
        print(f"  Copied to        : {delivered}")
    return 0


def cmd_ats(args: argparse.Namespace) -> int:
    vocab, _ = _load(strict=False)
    posting = None
    if args.jd:
        posting = jd_mod.parse(jd_mod.read_source(args.jd), args.company or "",
                               args.role or "", vocab)
    report = ats.simulate(Path(args.pdf), posting, vocab)
    print(ats.to_markdown(report))
    return 0


def cmd_gap(args: argparse.Namespace) -> int:
    vocab, master = _load(strict=False)
    path = gap.write_report(master, vocab)
    rows, unmapped, total = gap.analyze(master, vocab)
    print(f"{total} stored JD(s) analyzed -> {path}")
    for row in [r for r in rows if not r.covered and r.required_in][:10]:
        print(f"  GAP  {row.label:28s} required in {row.required_in}/{total}")
    return 0


# --------------------------------------------------------------------------

def _deliver(out_dir: Path, stem: str) -> Path | None:
    """Copy the finished resume where the user keeps their application material.

    Skipped silently when profile/delivery.toml sets no path, or the drive
    isn't there. Generating into out/ always works, and delivery is a
    convenience on top of it.
    """
    destination = config.delivery_dir()
    if destination is None:
        return None
    try:
        target = destination / out_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for name in (f"{stem}.pdf", f"{stem}.docx", f"{stem}.txt",
                     "TAILORING.md", "ats_report.md"):
            source = out_dir / name
            if source.exists():
                shutil.copy2(source, target / name)
        return target
    except OSError as exc:
        print(f"  (delivery copy skipped: {exc})")
        return None


def _squeeze_advice(plan: tailor.Plan, fit: render_pdf.Fit) -> list[str]:
    """What the user could give back, when the page had to take something.

    Only says anything when the fit actually cost something visible: smaller
    type, a dropped bullet, or a crossed floor. The point is that the page is
    a budget and the user is the one who decides what it is spent on -- but
    they can only decide that if they are told what it cost and what they are
    still holding.
    """
    if fit.layout.type >= 1.0 and not fit.dropped and not fit.broke_floors:
        return []
    out: list[str] = []
    if plan.summary:
        out.append("The summary is about four lines of this page. "
                   'Set `summary = "none"` under `[render]` in master.toml to '
                   "spend them on bullets instead.")
    ceiling = int(plan.master.render.get("total_bullet_ceiling", 12))
    if plan.bullet_count() >= ceiling:
        out.append(f"The bullet ceiling is {ceiling} and the plan is at it. "
                   f"Lowering `total_bullet_ceiling` puts the cut back where "
                   f"it is made on coverage and reported, instead of here.")
    return out


def _tailoring_report(plan: tailor.Plan, cover: tailor.Coverage,
                      report: ats.AtsReport, fit: render_pdf.Fit,
                      stored: Path) -> str:
    posting = plan.jd
    dropped, slack = fit.dropped, fit.slack
    lines = [
        f"# Tailoring report -- {posting.company}, {posting.title}",
        "",
        f"Generated {date.today().isoformat()}. JD stored at `{stored.name}`.",
        "",
        "Every line below came out of `content/master.toml` unchanged. This "
        "report exists so the choices are reviewable: what was picked, what was "
        "left out, and what the JD asked for that the resume does not say.",
        "",
        "## Numbers",
        "",
        f"- Required-term coverage: **{cover.required_rate:.0%}**",
        f"- ATS parse score: **{report.parse_score}/100** ({report.verdict})",
        f"- Page fit: {report.pages} page(s), {slack:.0f}mm of slack",
        f"- Target family read from the title: `{posting.family()}`",
    ]
    if posting.years_required:
        lines.append(f"- Binding experience requirement: **{posting.years_required} years**")
    # Tightened leading is a rendering detail and it happens on nearly every
    # posting, so only a change the reader can see is worth a line here.
    if fit.layout.type < 1.0:
        lines.append(f"- Set smaller to hold one page: {fit.layout.describe()}")
    for line in _squeeze_advice(plan, fit):
        lines.append(f"- {line}")
    if dropped:
        lines.append(f"- Dropped to fit the page: {', '.join(dropped)}")
    if fit.broke_floors:
        lines.append("- A section's `min_bullets` floor was crossed. One page "
                     "outranks it.")
    if plan.summary:
        lines += ["", "## Summary line used", "", f"> {plan.summary}", ""]
    else:
        lines.append("")

    lines += ["## Bullets selected", ""]
    for section in (*plan.experience, *plan.projects):
        entry = section.entry
        name = getattr(entry, "name", None) or f"{entry.title} -- {entry.company}"
        lines += [f"### {name}", ""]
        for chosen in section.chosen:
            mark = " *(rephrased variant)*" if chosen.rephrased else ""
            lines.append(f"- **{chosen.variant.id}**{mark} -- value {chosen.value:.0f}, "
                         f"new coverage {chosen.gain:.0f}: {chosen.reason}")
            lines.append(f"  > {chosen.text}")
        lines.append("")

    if cover.required_missed:
        lines += [
            "## Required terms the resume does not say", "",
            "Not a rendering bug -- these are things the JD requires that "
            "your true content cannot claim. The only honest responses are "
            "to learn the skill, add a real bullet to master.toml, or accept "
            "the gap.", "",
        ]
        lines += [f"- {t}" for t in cover.required_missed] + [""]
    if cover.wanted_missed:
        lines += ["## Preferred terms not covered", "",
                  ", ".join(cover.wanted_missed), ""]
    if cover.draft_would_help:
        lines += [
            "## Draft bullets that would have covered a gap", "",
            "These exist in master.toml but are unconfirmed, so they were "
            "excluded. Confirming one closes the gap it lists.", "",
        ]
        for bid, helps in cover.draft_would_help:
            lines.append(f"- `{bid}` -> would cover: {', '.join(helps)}")
        lines.append("")
    if cover.unmapped:
        lines += ["## Words this JD used that the vocabulary doesn't know", "",
                  ", ".join(f"{t} ({n})" for t, n in cover.unmapped), ""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine", description="Master resume system, tailoring, ATS simulator."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="validate master content and list drafts")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("tailor", help="build a tailored packet for one posting")
    p.add_argument("--jd", required=True, help="path to the JD text, or - for stdin")
    p.add_argument("--company", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--include-draft", action="store_true",
                   help="allow unconfirmed draft bullets (review the output)")
    p.add_argument("--summary", choices=("full", "short", "none"), default="",
                   help="how much of the summary to keep, overriding the "
                        "profile's `render.summary`. 'short' drops the closing "
                        "boilerplate (~2 lines back); 'none' omits the section "
                        "entirely, which is about 4 lines and buys two bullets")
    p.add_argument("--no-deliver", action="store_true",
                   help="skip the copy to the WORK folder")
    p.set_defaults(func=cmd_tailor)

    p = sub.add_parser("ats", help="simulate an ATS parse of any PDF")
    p.add_argument("pdf")
    p.add_argument("--jd", help="optional JD to measure keyword match against")
    p.add_argument("--company", default="")
    p.add_argument("--role", default="")
    p.set_defaults(func=cmd_ats)

    p = sub.add_parser("gap", help="keyword gap report across every stored JD")
    p.set_defaults(func=cmd_gap)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
