"""Job Radar entry point.

Run-once model, same as the news bot: check the switch, collect, score,
deliver, exit. Costs nothing the rest of the day.

    py -m radar.main            # a real run
    py -m radar.main --dry-run  # no Notion write, no seen.json update

IMPORTANT -- never use print() in this module. The scheduled task runs it
under pythonw.exe where sys.stdout is None, so print() raises and the run
dies silently. Use log(), which writes to logs/run.log and only touches
stdout if it actually exists. This exact bug killed the news bot's first
live run.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime

from . import (candidates, config, dedupe, learn, mysql_store, plugins,
               render, score, sources)
from .models import Job

_LOG_PATH = config.LOGS / "run.log"


def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    # Only write to the console when there IS one (pythonw.exe has none).
    if sys.stdout is not None:
        try:
            print(line)
        except (OSError, ValueError, UnicodeEncodeError):
            pass


def run(dry_run: bool = False, only: str | None = None) -> int:
    log("=" * 60)
    if not config.is_enabled():
        log("SWITCH.txt is OFF - exiting without doing anything")
        return 0

    config.load_env()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    log("collecting...")
    jobs, stats = sources.collect(log=log, only=only)
    log(f"collected {len(jobs)} raw postings")
    if not jobs:
        log("nothing collected - every source was empty or failed")
        return 1

    # Score once on the title/location alone, so the detail fetch can be
    # spent only on postings that are already plausible.
    for job in jobs:
        score.score_job(job)
    sources.fetch_details(jobs, log=log)

    # Richmond Job Market Dashboard's raw layer (plan item 11): the full,
    # pre-dedupe pull, exactly as each source returned it. Must happen here,
    # before dedupe.collapse below discards the duplicate copies that make
    # this data "raw" in the first place.
    #
    # --dry-run promises to write nothing persistent, and this was the one
    # write that ignored it -- a dry run appended nine thousand rows to the
    # dataset the dashboard is built on, which is about as persistent as it
    # gets. Every other write below already checks the flag.
    if dry_run:
        log(f"mysql: skipped {len(jobs)} raw posting(s) (dry run)")
    else:
        mysql_store.write_raw_postings(jobs, stamp, log=log)

    jobs, collapsed = dedupe.collapse(jobs)
    log(f"{len(jobs)} after collapsing {collapsed} duplicate(s)")

    # Before scoring, not after: freshness is worth points, and a posting
    # whose date came from a sitemap `<lastmod>` can claim to be newer than
    # the run that first saw it. The store is the only thing that can say so.
    store = dedupe.SeenStore()
    for job in jobs:
        store.settle_posted(job)

    jobs = score.score_all(jobs)          # rescore now that bodies are in

    new_jobs: list[Job] = []        # new AND worth reporting
    first_seen: list[Job] = []      # new at any score -- the dataset delta
    for job in jobs:
        was_new = store.is_new(job)
        store.record(job)
        store.ghost_check(job)
        if was_new:
            first_seen.append(job)
            if job.score >= config.MIN_SCORE_TO_REPORT:
                new_jobs.append(job)
    new_jobs.sort(key=lambda j: -j.score)
    log(f"{len(new_jobs)} new posting(s) at or above score "
        f"{config.MIN_SCORE_TO_REPORT} (store holds {len(store)})")

    # Today's good postings become tomorrow's watched companies. Runs after
    # scoring because the score is the whole qualification, and off the full
    # list rather than `new_jobs` -- a company is worth watching whether or
    # not this particular req was seen yesterday. Wrapped because this is the
    # one step whose failure should cost nothing: the digest is already
    # decided by here, and a network fault while probing must not lose it.
    if dry_run:
        log("dry run - skipping employer discovery")
    else:
        try:
            for row in learn.run(jobs):
                log(f"learned employer: {row['name']} via {row['ats']} "
                    f"(confirmed by \"{row['confirmed_by']}\")")
        except Exception:
            log("employer discovery failed\n" + traceback.format_exc())

    digest_path = config.DIGESTS / f"digest_{stamp}.md"
    digest_path.write_text(render.markdown(jobs, stats, new_jobs), encoding="utf-8")
    log(f"digest written to {digest_path.name}")

    # The market dataset for the Richmond Job Market Dashboard (plan item
    # 11). These files are committed on purpose, so size discipline matters:
    #
    #   * only postings seen for the FIRST time are written. The union of all
    #     snapshots is the full market history; re-recording the same 797
    #     postings twice a day would be ~270 MB/year of pure redundancy.
    #   * no JD bodies -- they are ~95% of the bytes and the analysis wants
    #     the structured fields, not the prose. The signal in them (salary,
    #     years required, stack overlap, geography) is already parsed out.
    #   * no scoring prose -- it lives in the digest and the Notion row.
    #
    # Full run: 797 rows / 383 KB. Typical run after that: a few dozen rows.
    snapshot = config.snapshots_dir() / f"jobs_{stamp}.json"
    slim = []
    for job in first_seen:
        record = job.to_dict()
        # `description` is the bulk; `reasons` is scoring prose that already
        # lives in the digest and the Notion row. Neither is analysis input.
        record.pop("description", None)
        record.pop("reasons", None)
        record["required_years"] = score.required_years(job)
        slim.append(record)
    if slim:
        snapshot.write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
        log(f"snapshot: {len(slim)} newly-seen posting(s) -> {snapshot.name}")

    if dry_run:
        log("dry run - skipping Notion write and seen.json update")
        if sys.stdout is not None:
            print(render.console(jobs, stats, new_jobs))
        return 0

    # The working set for Assisted Apply (plan items 5, 6): everything above
    # the threshold right now, JD bodies included. Deliberately not limited to
    # `new_jobs` -- a posting found yesterday and not applied to yet is still a
    # candidate today, and this is the file that answers "what can I apply to".
    candidates.write(jobs, log=log)

    plugins.deliver(jobs, stats, new_jobs, log=log)
    store.save()
    log("done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Radar - one discovery cycle")
    parser.add_argument("--dry-run", action="store_true",
                        help="collect and score, but write nothing persistent")
    parser.add_argument("--only", metavar="NAME",
                        help="run only sources whose name contains NAME")
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run, only=args.only)
    except Exception:
        # discord.py-style silent-swallow protection: log our own traceback,
        # because nothing else is listening under pythonw.exe.
        log("FATAL\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
