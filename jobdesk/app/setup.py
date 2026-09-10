"""The setup wizard: a form in, a working `profile/` out.

Step 3 of the JobDesk plan, and the step that turns this from one person's
tool into a product. Before it, using JobDesk meant copying seven TOML files
and editing 1,500 lines of someone else's answers. After it, first launch is a
form.

The design rule is that **the wizard writes the same files a person would
edit**, never a parallel format of its own. There is no wizard database and no
JSON side-car. It copies `profile.example/` -- comments, documentation and all
-- and then patches in the answers from the form. So every screen has a file
behind it that stays editable in Notepad, and the wizard is a convenience over
the file rather than a layer on top of it. Someone who outgrows the form loses
nothing by opening the TOML.

What it does not do is invent content. `master.toml` is a person's factual
claims about their own career, and the engine's whole ethical basis is that a
tailored resume is a subset of that file. So the wizard fills in the identity
block, imports the resume's raw text for reference, and leaves the claims
themselves to the user. It will not write a bullet nobody said.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import paths, profile
from . import tomlpatch
from .resume_import import Parsed

EXAMPLE = paths.ROOT / "profile.example"
TARGET = paths.ROOT / "profile"

# The three files the form has no opinions about. They are copied from the
# example and left for the user to edit, with a header saying so, because a
# blank vocabulary matches nothing and a blank letter file builds no letter --
# a starting point someone edits beats an empty file they have to invent.
CARRIED = ("vocabulary.toml", "letter.toml", "answers.toml")

_WORK_MODES = ("remote", "hybrid", "onsite", "any")


class SetupError(RuntimeError):
    """The answers cannot produce a valid profile, and why."""


@dataclass
class Answers:
    """One completed run of the form.

    Every field maps to something in `targeting.toml` or `master.toml`. Where
    a field is left blank the example's value stays, which is why there are so
    few required ones: a profile that is 80% example and 20% yours still runs.
    """
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    city: str = ""
    state: str = ""

    titles_1: list[str] = None          # strongest fit today
    titles_2: list[str] = None          # reachable with positioning
    titles_3: list[str] = None          # stretch

    work_mode: str = "any"
    radius_miles: int = 30
    salary_floor: int = 0
    salary_target: int = 0
    dealbreakers: list[str] = None
    employers: list[dict] = None

    resume_path: str = ""
    resume_text: str = ""
    delivery_resumes: str = ""
    delivery_packets: str = ""

    def __post_init__(self):
        for name in ("titles_1", "titles_2", "titles_3",
                     "dealbreakers", "employers"):
            if getattr(self, name) is None:
                setattr(self, name, [])


def _clean_titles(titles: list[str]) -> list[str]:
    """Lowercased, trimmed, de-duplicated, in the order given.

    Titles are matched as substrings against a posting's title, so case never
    matters and a stray capital would only make the file look inconsistent.
    """
    out: list[str] = []
    seen: set[str] = set()
    for title in titles:
        value = " ".join(str(title).lower().split())
        if len(value) < 3 or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def validate(answers: Answers) -> list[str]:
    """Everything that would stop this profile working, in plain sentences.

    Run before anything is written. A half-written profile is worse than no
    profile, because `profile/` existing is what makes JobDesk stop falling
    back to the example.
    """
    problems: list[str] = []
    if not answers.name.strip():
        problems.append("A name is needed -- it goes on the resume.")
    if not answers.email.strip():
        problems.append("An email is needed -- it goes on the resume.")
    elif "@" not in answers.email:
        problems.append(f"{answers.email} does not look like an email address.")
    if not _clean_titles(answers.titles_1):
        problems.append(
            "Pick at least one tier 1 title. Nothing scores above zero without "
            "one, so the radar would return an empty list every morning.")
    if not answers.city.strip():
        problems.append(
            "A city is needed. Without it every posting reads as out of area "
            "and local roles score the same as ones across the country.")
    if answers.work_mode not in _WORK_MODES:
        problems.append(f"Work mode must be one of: {', '.join(_WORK_MODES)}.")
    if answers.salary_floor and answers.salary_target:
        if answers.salary_target < answers.salary_floor:
            problems.append(
                f"The target ({answers.salary_target:,}) is below the floor "
                f"({answers.salary_floor:,}). The floor is the walk-away "
                f"number and the target is what you are aiming at.")
    if answers.salary_floor and answers.salary_floor > 500_000:
        problems.append(
            f"{answers.salary_floor:,} as a salary floor would reject every "
            f"posting. Is that a yearly figure?")
    for field in ("delivery_resumes", "delivery_packets"):
        raw = getattr(answers, field).strip()
        if raw and not Path(raw).expanduser().parent.exists():
            problems.append(f"{raw} is inside a folder that does not exist.")
    return problems


# ---------------------------------------------------------------------------
# Building each file
# ---------------------------------------------------------------------------

def _targeting_changes(answers: Answers) -> dict[str, object]:
    """The form's answers as `targeting.toml` keys."""
    city = answers.city.strip()
    state = answers.state.strip().upper()
    metro = f"{city}, {state}" if state else city

    changes: dict[str, object] = {
        "home_metro": metro,
        "tier_1_titles": _clean_titles(answers.titles_1),
        "local_terms": _local_terms(city, state),
    }
    if answers.titles_2:
        changes["tier_2_titles"] = _clean_titles(answers.titles_2)
    if answers.titles_3:
        changes["tier_3_titles"] = _clean_titles(answers.titles_3)
    if answers.salary_floor:
        changes["salary_floor"] = int(answers.salary_floor)
    if answers.salary_target:
        changes["salary_target"] = int(answers.salary_target)
    if answers.dealbreakers:
        # Appended, never replaced. The example's list is the generic set
        # (unpaid, commission-only, MLM) that applies to everyone, and a user
        # adding "no night shift" did not mean to stop screening out those.
        base = _example_list("targeting.toml", "hard_disqualifiers")
        extra = [d.strip().lower() for d in answers.dealbreakers if d.strip()]
        changes["hard_disqualifiers"] = base + [e for e in extra if e not in base]

    # Work mode is expressed by which location terms still score, because that
    # is how the scorer already reads it. "remote" means a posting has to say
    # so; "onsite" means the metro terms carry the weight.
    if answers.work_mode == "remote":
        changes["hybrid_terms"] = []
    elif answers.work_mode == "onsite":
        changes["remote_terms"] = []
    return changes


def _local_terms(city: str, state: str) -> list[str]:
    """The strings a posting uses to mean "where you live".

    A posting says "Richmond", "Richmond, VA", "RVA" or names a suburb, and
    all of those are local. The suburbs are the user's to add later -- this
    writes the forms that can be derived from the city and state alone, which
    is the part nobody should have to type.
    """
    terms = [city.lower()]
    if state:
        terms += [f"{city.lower()}, {state.lower()}",
                  f"{city.lower()} {state.lower()}"]
        terms.append(state.lower())
    return terms


def _example_list(filename: str, key: str) -> list[str]:
    """One list out of the shipped example, for merging against."""
    with (EXAMPLE / filename).open("rb") as handle:
        return list(tomllib.load(handle).get(key, []))


def _employers_toml(answers: Answers) -> str:
    """`employers.toml`, written fresh rather than patched.

    The example's eleven Denver employers are the one thing in a profile that
    is pure example data: nobody in Richmond wants Denver's hospital system
    watched. So this file is the exception to the copy-and-patch rule and gets
    rewritten from the form, with the example's instructions kept at the top
    because they explain how to add the next one.
    """
    header = (EXAMPLE / "employers.toml").read_text(encoding="utf-8")
    header = header.split("[[employer]]", 1)[0].rstrip()
    header = header.replace(
        "This is the example profile's list: eleven Denver-area employers",
        "Written by the setup wizard from the companies you named")

    if not answers.employers:
        return header + (
            "\n\n# No employers yet. The radar still works without any: the job"
            "\n# boards below cover the general search. Add one with"
            "\n#     py scripts/radar/discover_ats.py <slug>\n")

    blocks = [header, ""]
    for employer in answers.employers:
        name = str(employer.get("name", "")).strip()
        if not name:
            continue
        blocks.append("[[employer]]")
        blocks.append(f'name = {tomlpatch.dump_value(name)}')
        ats = str(employer.get("ats", "")).strip()
        slug = str(employer.get("slug", "")).strip()
        if ats and slug:
            blocks.append(f'ats = {tomlpatch.dump_value(ats)}')
            blocks.append(f'slug = {tomlpatch.dump_value(slug)}')
        else:
            # Named but not yet located. Commented out so the file stays
            # valid and the radar does not try to fetch a board with no
            # coordinates, with the command that finds them sitting right
            # there.
            blocks.append("# No job board found for this one yet. Run:")
            blocks.append(f"#     py scripts/radar/discover_ats.py "
                          f"{_slugify(name)}")
            blocks.append("# and paste the ats and slug it prints.")
            blocks.append('ats = ""')
            blocks.append('slug = ""')
        blocks.append("")
    return "\n".join(blocks)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _delivery_toml(answers: Answers) -> str:
    """`delivery.toml`, with the two paths uncommented if they were given."""
    text = (EXAMPLE / "delivery.toml").read_text(encoding="utf-8")
    for key, value in (("resumes", answers.delivery_resumes),
                       ("packets", answers.delivery_packets)):
        value = value.strip()
        if not value:
            continue
        pattern = re.compile(r"^#\s*" + key + r"\s*=.*$", re.M)
        line = f"{key} = {tomlpatch.dump_value(value)}"
        text = pattern.sub(line, text, count=1)
    return text


_CARRIED_HEADER = """\
# ---------------------------------------------------------------------------
# THIS FILE CAME FROM THE EXAMPLE PROFILE. IT IS NOT YOURS YET.
#
# The setup wizard does not write this one, because it holds writing rather
# than settings and nothing should put words in your mouth. It is a working
# starting point so the pipeline runs today; edit it when you have read what
# is in it. Everything below this line is the original file, comments and all.
# ---------------------------------------------------------------------------

"""


def write(answers: Answers, *, overwrite: bool = False) -> dict:
    """Create `profile/` from a completed form. Returns what it wrote.

    Writes to a temporary directory and moves it into place at the end, so a
    failure halfway through leaves no half-profile behind. `profile/` existing
    is what stops JobDesk falling back to the example, and a broken one is
    worse than none.
    """
    problems = validate(answers)
    if problems:
        raise SetupError(" ".join(problems))
    if TARGET.exists() and not overwrite:
        raise SetupError(
            f"{TARGET} already exists. Setup would overwrite the profile you "
            f"already have. Rename it first if you meant to start over.")

    # Staging and backup are siblings of the target rather than of ROOT, so
    # pointing TARGET somewhere else (which is exactly what the test suite
    # does) moves the whole operation with it.
    staging = TARGET.parent / (TARGET.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(EXAMPLE, staging)

    written: list[str] = []

    def put(name: str, text: str) -> None:
        (staging / name).write_text(text, encoding="utf-8")
        written.append(name)

    # targeting.toml -- patched, so all 126 comments survive.
    target_src = (staging / "targeting.toml").read_text(encoding="utf-8")
    put("targeting.toml", tomlpatch.patch(target_src, _targeting_changes(answers)))

    # master.toml -- identity only. The claims stay the user's to write.
    master_src = (staging / "master.toml").read_text(encoding="utf-8")
    city, state = answers.city.strip(), answers.state.strip().upper()
    put("master.toml", tomlpatch.patch(master_src, {
        "name": answers.name.strip(),
        "location": f"{city}, {state}" if state else city,
        "email": answers.email.strip(),
        "phone": answers.phone.strip(),
        "linkedin": answers.linkedin.strip(),
        "github": answers.github.strip(),
    }, section="identity"))

    put("employers.toml", _employers_toml(answers))
    put("delivery.toml", _delivery_toml(answers))

    for name in CARRIED:
        text = (staging / name).read_text(encoding="utf-8")
        put(name, _CARRIED_HEADER + text)

    # The resume's own text, kept beside the profile for reference while the
    # user fills in master.toml. Not read by anything -- it is a working note,
    # and naming it .txt rather than .toml keeps it that way.
    if answers.resume_text.strip():
        (staging / "resume-as-imported.txt").write_text(
            answers.resume_text, encoding="utf-8")
        written.append("resume-as-imported.txt")

    # Prove it parses before it becomes the live profile.
    for name in profile.REQUIRED:
        try:
            with (staging / name).open("rb") as handle:
                tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise SetupError(f"the generated {name} is not valid TOML: {exc}")

    if TARGET.exists():
        backup = TARGET.parent / (TARGET.name + ".replaced")
        shutil.rmtree(backup, ignore_errors=True)
        TARGET.rename(backup)
    staging.rename(TARGET)

    profile._read.cache_clear()       # the live profile just changed underneath
    return {"directory": str(TARGET), "files": written,
            "carried": list(CARRIED)}
