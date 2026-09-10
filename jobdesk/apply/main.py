"""Assisted Apply -- CLI.

The app is the front door now; this is the machinery behind it, and JobDesk
calls these same functions. It stays a working CLI because a scheduled task
has no window to click in, and because a flow you cannot run headless is a
flow you cannot debug.

    py -m apply.main                    where each of these lives in the app
    py -m apply.main list               the whole live queue, best first
    py -m apply.main list --today       only what Radar first saw today
    py -m apply.main prep 3             build a packet for candidate #3
    py -m apply.main prep <url>         build a packet for any posting
    py -m apply.main auto               build + sync OG tracker for Notion's newly "Date Applied" rows
    py -m apply.main submitted <id>     record that you actually applied
    py -m apply.main status <id> interview
    py -m apply.main rejected <id>       log a rejection with stage + reason
    py -m apply.main log                the application log + funnel
    py -m apply.main weekly             item 8's standing funnel/rejection review
    py -m apply.main answers "salary"   pull an answer out of the bank
    py -m apply.main check | gap        the Resume Engine, without remembering it

Console output is pure ASCII. Job Radar and the Resume Engine both lost lines
to UnicodeEncodeError on the Windows console codepage; a dropped line in a
guard warning is worse than an ugly one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

from .. import noconsole
from . import answers as answers_mod
from . import candidates as candidates_mod
from . import config, discord, engine, guard, jdtext, notion_sync, packet
from .applog import Application, Log, STATUSES


def echo(message: str = "") -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"))


def rule(char: str = "-") -> None:
    echo(char * 72)


# ---------------------------------------------------------------------------
# shared flows -- the subcommands and the app both go through these
# ---------------------------------------------------------------------------

def show_candidates(rows: list[candidates_mod.Candidate], note: str,
                    log: Log | None = None) -> None:
    echo(note)
    if not rows:
        return
    built = log.auto_prepared() if log else {}
    echo("")
    for index, row in enumerate(rows, 1):
        tier = row.tier or "-"
        echo(f"{index:3d}. [{row.score:3d}/{tier}] {row.title}")
        echo(f"      {row.company} - {row.one_line()}")
        if row.uid in built:
            echo(f"      PACKET READY (auto-built): {built[row.uid].id}")
        if row.flags:
            echo(f"      flags: {', '.join(row.flags)}")


def load_queue(log: Log, limit: int = 25):
    return candidates_mod.load(exclude_uids=log.queue_hidden_uids(), limit=limit)


def load_today(log: Log, day: str = ""):
    """Everything Job Radar first saw today, at any score.

    No score floor and no cut: a low score is Job Radar's opinion, and the
    reason to look at this list at all is to disagree with it. If today is
    empty, fall back to the newest day the cache knows about rather than
    showing nothing -- an empty screen reads as "the tool is broken".
    """
    day = day or date.today().isoformat()
    rows, note = candidates_mod.load(exclude_uids=log.queue_hidden_uids(),
                                     limit=500, found_on=day)
    if rows:
        return rows, note

    dates = candidates_mod.run_dates()
    newer = [d for d in dates if d < day]
    if not newer:
        return [], (f"Nothing first seen on {day}, and the cache has no older "
                    f"sightings either. Run Job Radar (option 8).")
    fallback = newer[0]
    rows, note = candidates_mod.load(exclude_uids=log.queue_hidden_uids(),
                                     limit=500, found_on=fallback)
    return rows, (f"Nothing new on {day} -- Job Radar may not have run yet.\n"
                  f"{note}")


def prep(candidate: candidates_mod.Candidate, log: Log, *, note: str = "",
         include_draft: bool = False, force: bool = False,
         interactive: bool = True, allow_fetch: bool = True,
         jd_file: str = "", auto: bool = False) -> packet.Result:
    """Guard, get the JD, build the packet. The whole point of the tool."""
    rule("=")
    echo(f"{candidate.company} -- {candidate.title}")
    if candidate.url:
        echo(candidate.url)
    rule("=")

    checks = guard.run(log, company=candidate.company, role=candidate.title,
                       url=candidate.url, uid=candidate.uid,
                       dedupe_key=candidate.dedupe_key, flags=candidate.flags)
    if checks:
        echo("")
        for check in checks:
            echo(f"  {check}")
        echo("")
    blocking = guard.blocking(checks)
    if blocking and not force:
        echo("BLOCKED by the rules above. These are the ones that actually get")
        echo("a candidate remembered badly (item 6), so they stop the build.")
        if interactive:
            reply = input("Override anyway? Type OVERRIDE to continue: ").strip()
            if reply != "OVERRIDE":
                echo("Stopped. Nothing was built.")
                return packet.Result(ok=False, problems=["blocked by guard rules"])
        else:
            echo("Re-run with --force if you have a reason.")
            return packet.Result(ok=False, problems=["blocked by guard rules"])

    cached = candidate.description
    if jd_file:
        # A JD already saved to disk beats everything -- it is what you
        # actually read, and it costs no request.
        cached = Path(jd_file).read_text(encoding="utf-8-sig", errors="replace")

    jd_text, how = jdtext.obtain(cached=cached, url=candidate.url,
                                 allow_fetch=allow_fetch,
                                 allow_paste=interactive, echo=echo)
    if not jd_text:
        echo("No job description text, so there is nothing to tailor against.")
        return packet.Result(ok=False, problems=["no JD text"])
    if jd_file and jd_text == cached:
        how = f"{Path(jd_file).name} ({len(jd_text)} chars)"
    echo(f"  JD source: {how}")

    if interactive and not note:
        echo("")
        echo(f"COVER LETTER -- why {candidate.company}? (optional, Enter to skip)")
        echo("Write one sentence, in your own words, about what this company")
        echo("does or why you want to work there. It is pasted into the letter")
        echo("verbatim -- it is the one part no template can write for you.")
        echo(f'  e.g. "{candidate.company} is one of the few places doing X, '
             f'and that is the work I want."')
        note = input("> ").strip()

    result = packet.build(candidate, jd_text, log=log, note=note,
                          include_draft=include_draft, checks=checks,
                          auto=auto, echo=echo)

    echo("")
    for step in result.steps:
        echo(f"  + {step}")
    for problem in result.problems:
        echo(f"  ! {problem}")
    if result.ok:
        echo("")
        echo(f"  PACKET: {result.folder}")
        if result.delivered:
            echo(f"  COPIED: {result.delivered}")
        echo(f"  Open APPLY.md first. When you have actually submitted:")
        echo(f"    py -m apply.main submitted {result.application.id}")
    return result


def manual_candidate(url: str = "", company: str = "", role: str = "",
                     interactive: bool = True) -> candidates_mod.Candidate | None:
    if interactive and not url:
        url = input("Posting URL (blank if you only have the text): ").strip()
    if interactive and not company:
        company = input("Company: ").strip()
    if interactive and not role:
        role = input("Role title (exactly as posted): ").strip()
    if not company or not role:
        echo("Company and role are both required -- they name the packet and "
             "drive the guard rules.")
        return None
    return candidates_mod.Candidate(title=role, company=company, url=url,
                                    source="manual", origin="manual")


def print_log(log: Log) -> None:
    if not log.rows:
        echo("No applications logged yet.")
        return
    rule("=")
    echo(f"{len(log.rows)} application(s)")
    rule("=")
    for row in sorted(log.rows, key=lambda r: r.prepared_on, reverse=True):
        marker = "!" if row.follow_up_overdue else " "
        echo(f"{marker} {row.status:11s} {row.prepared_on}  {row.company} -- {row.role}")
        echo(f"    id: {row.id}")
        if row.applied_on:
            echo(f"    applied {row.applied_on}, follow up {row.follow_up_due}"
                 + ("  <-- DUE" if row.follow_up_overdue else ""))
        if row.agency:
            echo(f"    via agency: {row.agency}")
    echo("")
    funnel = log.funnel()
    echo("Funnel: " + ", ".join(f"{k} {v}" for k, v in funnel.items() if v))
    rejections = log.rejection_report()
    if rejections:
        echo("Rejections by stage reached: "
             + ", ".join(f"{k} {v}" for k, v in rejections.items()))
    by_source = log.by_source()
    if by_source:
        echo("By source (sent -> responses):")
        for source, (sent, responses) in by_source.items():
            rate = f"{responses / sent:.0%}" if sent else "-"
            echo(f"  {source:20s} {sent:3d} -> {responses:3d}  ({rate})")
    due = log.follow_ups_due()
    if due:
        echo("")
        echo(f"{len(due)} follow-up(s) due:")
        for row in due:
            echo(f"  {row.company} -- {row.role} (applied {row.applied_on})")


WEEKLY_MIN_APPLICATIONS = 20


def print_weekly(log: Log) -> None:
    """Item 8's standing report: by_source() + rejection_report() in one
    place, instead of read out of the full `log` dump by eye. Same honest
    caveat PROJECT.md and the proposal both already state -- it says nothing
    useful below ~20 logged applications, so that caveat is printed, not
    silently skipped, until there is enough to say something with.
    """
    rule("=")
    echo("WEEKLY FUNNEL REVIEW")
    rule("=")

    by_source = log.by_source()
    total_sent = sum(sent for sent, _ in by_source.values())
    if total_sent < WEEKLY_MIN_APPLICATIONS:
        echo(f"Only {total_sent} application(s) logged past the draft stage -- "
             f"this needs roughly {WEEKLY_MIN_APPLICATIONS} before the numbers "
             "below mean anything. Showing what's here anyway.")
        echo("")

    if by_source:
        echo("By source (sent -> responses):")
        for source, (sent, responses) in by_source.items():
            rate = f"{responses / sent:.0%}" if sent else "-"
            echo(f"  {source:20s} {sent:3d} -> {responses:3d}  ({rate})")
    else:
        echo("Nothing sent yet -- nothing to summarize by source.")

    echo("")
    rejections = log.rejection_report()
    if rejections:
        echo("Rejections by stage reached:")
        for stage, count in rejections.items():
            echo(f"  {stage:20s} {count}")
    else:
        echo("No rejections logged yet.")


def push_follow_ups(log: Log) -> None:
    """Push overdue follow-ups to Discord, once per due date.

    A no-op if DISCORD_WEBHOOK_URL isn't set -- `log` still lists them on
    screen either way. Called from `auto` (the scheduled task, three times a
    day) so a follow-up gets pushed without you having to remember to run
    `log`, and from `log` itself so checking by hand also clears the ping.
    """
    due = log.unpinged_follow_ups_due()
    if not due:
        return
    for row in discord.push(due, echo=echo):
        log.mark_follow_up_pinged(row)


def run_radar(dry: bool = False) -> None:
    if not (config.ROOT / "jobdesk" / "radar" / "main.py").exists():
        echo("Radar not found -- expected jobdesk/radar/main.py")
        return
    args = [sys.executable, "-m", "jobdesk.radar.main"] + (["--dry-run"] if dry else [])
    echo("Running the radar -- this takes a few minutes (7,000+ postings).")
    subprocess.run(args, cwd=str(config.ROOT), check=False, **noconsole.flags())


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_list(args) -> int:
    log = Log()
    if args.today or args.on:
        rows, note = load_today(log, day=args.on or "")
    else:
        rows, note = load_queue(log, limit=args.limit)
    show_candidates(rows, note, log)
    return 0


def cmd_prep(args) -> int:
    log = Log()
    target = (args.target or "").strip()
    candidate = None

    if target.isdigit():
        rows, note = load_queue(log)
        index = int(target)
        if not 1 <= index <= len(rows):
            echo(f"{note}\nNo candidate #{index}.")
            return 1
        candidate = rows[index - 1]
    elif target.lower().startswith("http"):
        candidate = manual_candidate(url=target, company=args.company,
                                     role=args.role, interactive=not args.no_input)
    elif target:
        rows, _ = load_queue(log, limit=200)
        hits = [r for r in rows if target.lower() in r.title.lower()
                or target.lower() in r.company.lower() or r.uid == target]
        if not hits:
            echo(f"Nothing in the candidate queue matches {target!r}.")
            return 1
        candidate = hits[0]
    else:
        candidate = manual_candidate(company=args.company, role=args.role,
                                     interactive=not args.no_input)
    if candidate is None:
        return 1
    if args.company:
        candidate.company = args.company
    if args.role:
        candidate.title = args.role

    result = prep(candidate, log, note=args.note, include_draft=args.include_draft,
                  force=args.force, interactive=not args.no_input,
                  jd_file=args.jd or "")
    if result.ok and not args.no_open and result.folder:
        packet.open_folder(result.delivered or result.folder)
    return 0 if result.ok else 1


def cmd_auto(args) -> int:
    """Build a packet, unattended, for every posting you just marked
    'Date Applied' on in the Job Radar Tracker -- and sync the matching
    fields to the OG Job & Internship Tracker off the same event.

    The trigger moved off "this posting scored >= 80" on 2026-08-20. A score
    is Job Radar's opinion; grinding out drafts for every 80+ posting decided
    nothing, because you still had to look at each one before applying.
    Applied Date is your own hand on the go signal, read straight out of
    Notion, so a packet only appears once you have actually chosen to apply --
    and the two trackers stop drifting apart in the same motion.

    What it does *not* do is finish the job. An auto packet ships with the
    `why-company` placeholder unanswered, because that is the one thing a bank
    cannot write, and the letter gate may have dropped paragraphs. It is a
    draft that saves the twenty minutes, not a packet to submit unread -- and
    plan item 6 still holds: nothing here submits anything.
    """
    log = Log()
    push_follow_ups(log)

    if not config.is_enabled():
        echo("SWITCH.txt is OFF - exiting without building anything.")
        return 0

    rows, note = notion_sync.fetch_newly_applied()
    echo(note)
    if not rows:
        return 0

    if len(rows) > args.max:
        echo(f"{len(rows)} to process, capping at {args.max}. Re-run for more.")
        rows = rows[:args.max]

    if args.dry_run:
        echo("")
        for row in rows:
            echo(f"  {row['company']} -- {row['role']}  (applied {row['applied']})")
        echo(f"\n{len(rows)} would be synced to the OG tracker and built.")
        return 0

    log = Log()
    synced_ids: list[str] = []
    unsynced: list[dict] = []
    built, blocked, failed = [], [], []
    for index, row in enumerate(rows, 1):
        echo("")
        echo(f"### {index}/{len(rows)}  {row['company']} -- {row['role']}")
        og_ok = notion_sync.sync_to_og_tracker(row, echo=echo)

        candidate = notion_sync.enrich_with_cache(row)
        # allow_fetch stays off: an unattended loop hitting job boards is
        # exactly the pattern the JD ladder's politeness rule exists to avoid.
        result = prep(candidate, log, interactive=False, allow_fetch=False,
                      auto=True)
        if result.ok:
            built.append(result)
        elif any("blocked by guard" in p for p in result.problems):
            blocked.append(candidate)
        else:
            failed.append((candidate, result.problems))

        # A row is marked consumed only when the OG tracker sync it exists to
        # perform actually happened. This line used to be unconditional, and
        # on 2026-08-24 the Home Depot row was marked synced by a run whose
        # OG sync had just printed "Could not read the OG tracker's schema".
        # The row was then invisible to every later run forever: the packet
        # was built, the OG tracker was never touched, and the next four
        # scheduled runs reported "0 newly-applied row(s)" because the only
        # rows that needed work had already been eaten.
        #
        # A guard-blocked or failed packet does not hold the row back. The
        # packet is a draft and a guard block is a real answer; the tracker
        # sync is the part that has to land.
        if og_ok:
            synced_ids.append(row["page_id"])
        else:
            unsynced.append(row)

    notion_sync.mark_synced(synced_ids)

    rule("=")
    echo(f"{len(built)} packet(s) built, {len(blocked)} blocked by the guard "
         f"rules, {len(failed)} failed. {len(synced_ids)} row(s) synced to the "
         f"OG tracker, {len(unsynced)} still waiting.")
    rule("=")
    for row in unsynced:
        echo(f"  !!   {row['company']} -- {row['role']}: OG tracker not "
             f"updated. The row stays in the queue and the next run retries "
             f"it.")
    for result in built:
        app = result.application
        echo(f"  {app.company} -- {app.role}")
        echo(f"       {result.delivered or result.folder}")
    for candidate in blocked:
        echo(f"  --   {candidate.company} -- {candidate.title} (guard)")
    for candidate, problems in failed:
        echo(f"  !!   {candidate.company} -- {candidate.title}")
        for problem in problems[:2]:
            echo(f"       {problem}")
    if built:
        echo("")
        echo("These are DRAFTS. Each still needs the company sentence in the "
             "cover letter and the `why-company` answer before it goes out.")
    # Exit non-zero when there was work and none of it landed. `auto` used to
    # return 0 after failing every OG sync it attempted, which is how a run
    # that touched nothing was read as a success by anything checking the exit
    # code -- the scheduled task, and Colony Dash's run verifier, which caught
    # it only because it counts rows as well as reading the status.
    return 1 if (unsynced and not synced_ids) else 0


def cmd_submitted(args) -> int:
    log = Log()
    app = log.get(args.id) or (log.find(args.id) or [None])[0]
    if app is None:
        echo(f"No application matching {args.id!r}. Try `py -m apply.main log`.")
        return 1
    log.mark_applied(app, when=args.date or "")
    if args.agency:
        app.agency = args.agency
        app.touch(f"submitted through agency: {args.agency}")
        log.save()
    echo(f"Recorded: {app.company} -- {app.role} applied {app.applied_on}.")
    echo(f"Follow-up due {app.follow_up_due} (day {config.FOLLOW_UP_DAYS}).")
    echo(f"{len(log.open_at(app.company))} of {config.MAX_OPEN_PER_COMPANY} "
         f"concurrent slots now used at {app.company}.")
    return 0


def cmd_rejected(args) -> int:
    log = Log()
    app = log.get(args.id) or (log.find(args.id) or [None])[0]
    if app is None:
        echo(f"No application matching {args.id!r}.")
        return 1
    log.mark_rejected(app, stage=args.stage or "", reason=args.reason or "")
    echo(f"{app.company} -- {app.role}: rejected at {app.rejected_stage}"
         + (f" ({args.reason})" if args.reason else ""))
    return 0


def cmd_status(args) -> int:
    log = Log()
    app = log.get(args.id) or (log.find(args.id) or [None])[0]
    if app is None:
        echo(f"No application matching {args.id!r}.")
        return 1
    try:
        log.set_status(app, args.status, note=args.note or "")
    except ValueError as exc:
        echo(str(exc))
        return 1
    echo(f"{app.company} -- {app.role}: {app.status}")
    return 0


def cmd_log(args) -> int:
    log = Log()
    push_follow_ups(log)
    print_log(log)
    return 0


def cmd_weekly(args) -> int:
    log = Log()
    print_weekly(log)
    return 0


def cmd_backfill_locations(args) -> int:
    notion_sync.backfill_og_locations(echo=echo)
    return 0


def cmd_answers(args) -> int:
    bank = answers_mod.load()
    if args.unconfirmed:
        pending = answers_mod.unconfirmed(bank)
        if not pending:
            echo("Every answer in the bank is confirmed.")
            return 0
        echo(f"{len(pending)} answer(s) you have not confirmed:")
        for entry in pending:
            echo("")
            echo(f"  [{entry.id}] {entry.question}")
            echo(f"    {entry.answer}")
            if entry.note:
                echo(f"    NOTE: {entry.note}")
        echo("")
        echo(f"Edit {config.ANSWERS_FILE} and delete the `confirm = true` line "
             f"to confirm one.")
        return 0
    if not args.question:
        for entry in bank:
            mark = " [CONFIRM]" if entry.needs_confirmation else ""
            echo(f"  {entry.id:20s} {entry.question}{mark}")
        return 0
    ranked = answers_mod.match(args.question, bank)
    if not ranked:
        echo("Nothing in the bank matches that. Add it to "
             f"{config.ANSWERS_FILE} once you have written it.")
        return 1
    for score, entry in ranked[:3]:
        rule()
        echo(f"{entry.question}   (match {score})")
        rule()
        echo(entry.answer)
        if entry.needs_confirmation:
            echo(f"\n  CONFIRM FIRST: {entry.note or 'unconfirmed draft'}")
        echo("")
    return 0


def cmd_check(args) -> int:
    code, output = engine.check()
    echo(output.strip())
    return code


def cmd_gap(args) -> int:
    code, output = engine.gap()
    echo(output.strip())
    return code


def cmd_radar(args) -> int:
    run_radar(dry=args.dry_run)
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apply",
        description="Assisted Apply -- per-job application packets, guard "
                    "rules, and the submission log.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list", help="candidates waiting, best first")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--today", action="store_true",
                   help="only what Job Radar first saw today, at any score")
    p.add_argument("--on", default="",
                   help="only what Job Radar first saw on YYYY-MM-DD")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("prep", help="build an application packet")
    p.add_argument("target", nargs="?", default="",
                   help="candidate number from `list`, a posting URL, or text "
                        "to match against the queue")
    p.add_argument("--company")
    p.add_argument("--role")
    p.add_argument("--jd", help="path to a JD you already saved, instead of "
                                "fetching or pasting it")
    p.add_argument("--note", default="",
                   help="one sentence about the company, in your own words")
    p.add_argument("--include-draft", action="store_true",
                   help="allow the Resume Engine's unconfirmed draft bullets")
    p.add_argument("--force", action="store_true",
                   help="build even if a guard rule blocks it")
    p.add_argument("--no-input", action="store_true",
                   help="never prompt (no JD paste fallback)")
    p.add_argument("--no-open", action="store_true",
                   help="don't open the packet folder when it's done")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("auto", help="build packets + sync the OG tracker for "
                                    "every posting Notion shows as newly "
                                    "'Date Applied', unattended")
    p.add_argument("--max", type=int, default=10,
                   help="most postings to process in one run, default 10")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be synced/built, do nothing")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("submitted", help="record that you actually applied")
    p.add_argument("id")
    p.add_argument("--date", help="YYYY-MM-DD, defaults to today")
    p.add_argument("--agency", help="agency name, if not applied direct")
    p.set_defaults(func=cmd_submitted)

    p = sub.add_parser("status", help="move an application along the funnel")
    p.add_argument("id")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rejected", help="log a rejection with the stage "
                                        "reached and why, for the win/loss read")
    p.add_argument("id")
    p.add_argument("--stage", choices=STATUSES,
                   help="stage reached before the no (defaults to whatever "
                        "status it was already at)")
    p.add_argument("--reason", help="anything you were told, verbatim")
    p.set_defaults(func=cmd_rejected)

    p = sub.add_parser("log", help="the application log and funnel")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("weekly", help="item 8's standing funnel + rejection "
                                      "review: by_source() and "
                                      "rejection_report() in one report")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("backfill-locations", help="one-time: normalize every "
                                                   "existing OG tracker row's "
                                                   "Location to REMOTE / your "
                                                   "home city / HYBRID / ON-SITE")
    p.set_defaults(func=cmd_backfill_locations)

    p = sub.add_parser("answers", help="the ATS free-text answer bank")
    p.add_argument("question", nargs="?", default="")
    p.add_argument("--unconfirmed", action="store_true",
                   help="list the answers you have never confirmed")
    p.set_defaults(func=cmd_answers)

    p = sub.add_parser("check", help="Resume Engine: validate master content")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("gap", help="Resume Engine: keyword gap report")
    p.set_defaults(func=cmd_gap)

    p = sub.add_parser("radar", help="run Job Radar now")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_radar)
    return parser


_MOVED = """
  Assisted Apply is a JobDesk tab now. Open the app:

      JobDesk.cmd                       or the desktop shortcut

  where every menu item lives:

    today's candidates, the queue    the Jobs tab
    build a packet from a URL        Jobs tab, "Build from a link"
    "I submitted one"                Applied tab, the status column
    application log, funnel, weekly  Applied tab
    the answer bank                  inside each packet, as ANSWERS.md
    resume engine, run the radar     the Console tab
    open the packets folder          the button on any built packet

  Every subcommand below still works and is what the app calls under the
  hood -- `py -m jobdesk.apply.main --help` lists them.
"""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        print(_MOVED)
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
