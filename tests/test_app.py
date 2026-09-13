"""Self-checks for the JobDesk window (plan items 3 and 4).

    py tests/test_app.py

Four things are worth pinning down here, and they are the four that would be
expensive to find out about later:

1. `tomlpatch` never loses a comment. The comments in a profile are its
   documentation, and a wizard that eats them has made the file worse.
2. `setup.write` produces a profile that parses, and refuses to overwrite one
   that already exists. Someone's answers are not recoverable from anything.
3. The API answers over a real socket, including the traversal guard, because
   "it works when I call the function" is not the same claim.
4. Nothing under /api/ writes a posting or submits anything. Rescore reads.

No network and no framework, same as the other three suites. The server runs
on a loopback port for the length of the test and is shut down after.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from base64 import b64encode
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["JOBDESK_PROFILE"] = str(ROOT / "profile.example")

from jobdesk import paths
from jobdesk.app import (access, actions, api, archive, jdstruct, market, net,
                         phone, resume_import, runner, server, setup,
                         tomlpatch)

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


def comments(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


# -- tomlpatch --------------------------------------------------------------

def test_tomlpatch() -> None:
    section("tomlpatch")
    source = (ROOT / "profile.example" / "targeting.toml").read_text(encoding="utf-8")

    patched = tomlpatch.patch(source, {
        "home_metro": "Richmond, VA",
        "salary_floor": 60000,
        "tier_1_titles": ["operations analyst", "business analyst"],
    })
    data = tomllib.loads(patched)

    check("values land", data["home_metro"] == "Richmond, VA"
          and data["salary_floor"] == 60000
          and data["tier_1_titles"] == ["operations analyst", "business analyst"])
    check("every comment survives", comments(patched) == comments(source),
          f"{comments(source)} -> {comments(patched)}")
    check("the rest of the file is untouched",
          len(patched.splitlines()) - len(source.splitlines()) < 8)
    check("arrays of tables are left alone",
          len(data["function_families"]) == len(tomllib.loads(source)["function_families"]))

    # The one that matters most: a key inside a [[table]] must never be
    # mistaken for a top-level setting, or a wizard aiming at `salary_floor`
    # could rewrite a scoring family's `points`.
    try:
        tomlpatch.patch(source, {"points": 5})
        check("refuses a key that is not top-level", False, "it patched something")
    except tomlpatch.PatchError:
        check("refuses a key that is not top-level", True)

    master = (ROOT / "profile.example" / "master.toml").read_text(encoding="utf-8")
    identity = tomlpatch.patch(master, {"name": "Wren A. Adeyemi"},
                               section="identity")
    check("section-scoped patch works",
          tomllib.loads(identity)["identity"]["name"] == "Wren A. Adeyemi")
    check("section-scoped patch keeps comments",
          comments(identity) == comments(master))

    check("a Windows path reads back as typed",
          tomlpatch.dump_value(r"D:\Job Search") == r"'D:\Job Search'")
    check("a long list wraps", "\n" in tomlpatch.dump_value(
        [f"title number {n}" for n in range(12)]))


# -- resume import ----------------------------------------------------------

RESUME = """Wren A. Adeyemi
Aurora, CO | wren.adeyemi@example.com | (720) 555-0142
linkedin.com/in/wren-adeyemi

SUMMARY
Operations analyst with four years in logistics reporting.

EXPERIENCE

Inventory Analyst
Ridgeline Distribution, Aurora, CO | Mar 2023 - Present
- Built the weekly stock report that replaced a manual spreadsheet, cutting
  three hours a week of hand entry
- Tracked 1,400 SKUs across two warehouses

Operations Coordinator
Foothill Freight | Jun 2021 - Feb 2023
- Scheduled 40 outbound loads a day

SKILLS
Excel, SQL, Power BI, Tableau
"""


def test_resume_import() -> None:
    section("resume import")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wren.txt"
        path.write_text(RESUME, encoding="utf-8")
        parsed = resume_import.parse(path)

    check("name", parsed.name == "Wren A. Adeyemi", parsed.name)
    check("email", parsed.email == "wren.adeyemi@example.com", parsed.email)
    check("phone", "555-0142" in parsed.phone, parsed.phone)
    check("city and state", parsed.city == "Aurora" and parsed.state == "CO",
          f"{parsed.city}/{parsed.state}")
    check("linkedin", "wren-adeyemi" in parsed.linkedin, parsed.linkedin)

    titles = [t.lower() for t in parsed.titles]
    check("finds the job titles", "inventory analyst" in titles
          and "operations coordinator" in titles, str(parsed.titles))
    # The failure this guards against is the parser reading a wrapped bullet
    # as a title. "three hours a week of hand entry" is the continuation line.
    check("no bullet fragments among the titles",
          not any("hand entry" in t or "warehouses" in t for t in titles),
          str(parsed.titles))
    check("skills", "sql" in [s.lower() for s in parsed.skills], str(parsed.skills))
    check("says what it filled in", "email" in parsed.filled and "name" in parsed.filled)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0 legacy word")
        try:
            resume_import.parse(path)
            check(".doc is refused with advice", False, "it parsed")
        except resume_import.UnreadableResume as exc:
            check(".doc is refused with advice", "docx" in str(exc).lower(), str(exc))


# -- setup ------------------------------------------------------------------

def answers() -> setup.Answers:
    return setup.Answers(
        name="Wren A. Adeyemi", email="wren.adeyemi@example.com",
        phone="720-555-0142", city="Aurora", state="co",
        titles_1=["Inventory Analyst", "operations analyst", "  "],
        titles_2=["business analyst"],
        work_mode="hybrid", salary_floor=60000, salary_target=85000,
        dealbreakers=["night shift"],
        employers=[{"name": "Ridgeline Distribution",
                    "ats": "greenhouse", "slug": "ridgeline"},
                   {"name": "Foothill Freight"}],
        resume_text=RESUME,
    )


def test_validation() -> None:
    section("setup validation")
    check("a complete form passes", setup.validate(answers()) == [])

    blank = setup.Answers(name="Wren", email="wren@example.com", city="Aurora")
    check("no tier 1 title is refused",
          any("tier 1" in p for p in setup.validate(blank)))

    no_city = setup.Answers(name="Wren", email="w@example.com",
                            titles_1=["analyst"])
    check("no city is refused", any("city" in p for p in setup.validate(no_city)))

    upside_down = answers()
    upside_down.salary_floor, upside_down.salary_target = 90000, 60000
    check("a target under the floor is refused",
          any("floor" in p for p in setup.validate(upside_down)))

    check("a bad email is refused",
          setup.validate(setup.Answers(name="W", email="not-an-email",
                                       city="Aurora", titles_1=["analyst"])))


def test_write() -> None:
    section("setup writes a profile")
    original = setup.TARGET
    tmp = Path(tempfile.mkdtemp())
    setup.TARGET = tmp / "profile"
    try:
        result = setup.write(answers())
        written = tmp / "profile"

        check("the directory exists", written.is_dir())
        for name in ("targeting.toml", "master.toml", "employers.toml",
                     "vocabulary.toml", "letter.toml", "answers.toml",
                     "delivery.toml"):
            check(f"{name} is there and parses",
                  (written / name).exists()
                  and tomllib.loads((written / name).read_text(encoding="utf-8")) is not None)

        targeting = tomllib.loads((written / "targeting.toml").read_text(encoding="utf-8"))
        check("titles are lowercased and blanks dropped",
              targeting["tier_1_titles"] == ["inventory analyst", "operations analyst"],
              str(targeting["tier_1_titles"]))
        check("the metro is the city and state", targeting["home_metro"] == "Aurora, CO")
        check("local terms cover the forms a posting uses",
              "aurora, co" in targeting["local_terms"])
        check("hybrid keeps both term lists",
              targeting["remote_terms"] and targeting["hybrid_terms"])
        check("the dealbreaker was added, not substituted",
              "night shift" in targeting["hard_disqualifiers"]
              and len(targeting["hard_disqualifiers"]) > 1)

        example = (ROOT / "profile.example" / "targeting.toml").read_text(encoding="utf-8")
        check("the generated file keeps the documentation",
              comments((written / "targeting.toml").read_text(encoding="utf-8"))
              == comments(example))

        master = tomllib.loads((written / "master.toml").read_text(encoding="utf-8"))
        check("identity is filled in", master["identity"]["name"] == "Wren A. Adeyemi")
        # The rule that matters: the wizard fills in contact details and does
        # not touch a single claim about anyone's career.
        example_master = tomllib.loads(
            (ROOT / "profile.example" / "master.toml").read_text(encoding="utf-8"))
        check("no career claim was invented or dropped",
              json.dumps(master.get("experience")) == json.dumps(example_master.get("experience")))

        employers = tomllib.loads((written / "employers.toml").read_text(encoding="utf-8"))
        names = [e["name"] for e in employers.get("employer", [])]
        check("both companies are listed", names == ["Ridgeline Distribution",
                                                     "Foothill Freight"], str(names))
        check("the one without a board is left blank rather than guessed",
              employers["employer"][1]["slug"] == "")
        check("and says how to find it",
              "discover_ats" in (written / "employers.toml").read_text(encoding="utf-8"))

        check("the resume text is kept for reference",
              (written / "resume-as-imported.txt").exists())
        check("files copied from the example say so",
              "NOT YOURS YET" in (written / "letter.toml").read_text(encoding="utf-8"))
        check("the result names what it wrote", "targeting.toml" in result["files"])

        try:
            setup.write(answers())
            check("refuses to overwrite an existing profile", False, "it overwrote")
        except setup.SetupError as exc:
            check("refuses to overwrite an existing profile",
                  "already exists" in str(exc))

        check("no staging directory is left behind",
              not (tmp / "profile.new").exists())
    finally:
        setup.TARGET = original
        shutil.rmtree(tmp, ignore_errors=True)


# -- the API over a real socket ---------------------------------------------

class Live:
    """The server, on a free loopback port, for the length of a `with`."""

    def __enter__(self):
        self.port = server._free_port(8900)
        self.httpd = ThreadingHTTPServer((server.HOST, self.port), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://{server.HOST}:{self.port}"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        return self._open(urllib.request.Request(self.base + path))

    def post(self, path, payload):
        return self._open(urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST"))

    def raw(self, path):
        """A static file, which is not JSON and must not be parsed as it."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def _open(self, request):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))


def test_api() -> None:
    section("the API over a socket")
    with Live() as live:
        status, body = live.get("/api/status")
        check("status answers", status == 200 and "configured" in body, str(body)[:120])
        check("status admits it is the example profile", body["using_example"] is True)

        status, body = live.get("/api/jobs")
        check("jobs answers", status == 200 and isinstance(body.get("jobs"), list))
        check("the list drops the JD bodies",
              all("description" not in row for row in body["jobs"]),
              "a description came back in the list")
        if body["jobs"]:
            uid = body["jobs"][0]["uid"]
            code, one = live.get("/api/job?uid=" + uid)
            check("one posting comes back structured",
                  code == 200 and isinstance(one.get("blocks"), list)
                  and "description" not in one,
                  "the JD should arrive as blocks, not as a wall of text")
            check("a posting says what the log knows about it",
                  "applied" in one and "prepared" in one and "guards" in one)
        code, missing = live.get("/api/job?uid=nope")
        check("an unknown posting is a readable 400",
              code == 400 and "no posting" in missing["error"])

        code, body = live.post("/api/rescore", {})
        check("rescore answers", code == 200 and "moved" in body)
        check("rescore says it wrote nothing", "Nothing was written" in body["note"])

        # The example profile is shared code; the panel must refuse to edit it.
        code, body = live.get("/api/targeting")
        check("the criteria panel refuses the example profile",
              code == 400 and "example" in body["error"])

        code, body = live.post("/api/setup/resume",
                               {"filename": "wren.txt",
                                "content": b64encode(RESUME.encode()).decode()})
        check("a resume uploads and parses",
              code == 200 and body["email"] == "wren.adeyemi@example.com")

        code, body = live.post("/api/setup/check", {"name": "", "email": ""})
        check("an empty form comes back with reasons",
              code == 200 and body["ok"] is False and body["problems"])

        code, body = live.get("/api/applications")
        check("the application log answers",
              code == 200 and isinstance(body.get("applications"), list)
              and "funnel" in body and "statuses" in body)
        code, body = live.post("/api/application/status", {"id": "", "status": "applied"})
        check("marking applied needs an id", code == 400)
        code, body = live.post("/api/application/manual", {"company": "", "role": ""})
        check("a manual application needs a company and a role", code == 400)

        code, body = live.get("/api/archive?q=analyst&limit=5")
        check("the archive answers",
              code == 200 and len(body.get("rows", [])) <= 5
              and "summary" in body)
        code, body = live.get("/api/archive/companies?limit=3")
        check("the archive ranks companies",
              code == 200 and len(body.get("companies", [])) <= 3)

        code, body = live.get("/api/runs")
        check("the console lists runs", code == 200 and "runs" in body)
        code, body = live.post("/api/run/start", {"kind": "nonsense"})
        check("an unknown kind of run is refused", code == 400)
        code, body = live.get("/api/run?id=nope&after=0")
        check("an unknown run is not an error", code == 200 and body.get("missing"))
        code, body = live.get("/api/packet?id=nope")
        check("an unknown packet is a readable 400",
              code == 400 and "no packet folder" in body["error"])
        code, body = live.post("/api/open", {"path": str(ROOT.parent)})
        check("Explorer will not open a path outside the project", code == 400)

        code, body = live.get("/api/nothing")
        check("an unknown route is a 404", code == 404)

        code, body = live.get("/api/setup/save")
        check("a POST route rejects a GET", code == 404)

        # Loopback is not a reason to serve arbitrary files.
        code, body = live.get("/../jobdesk/profile.py")
        check("path traversal is refused", code == 404, str(code))

        status, page = live.raw("/app.js")
        check("static files are served", status == 200 and b"JobDesk" in page)
        status, page = live.raw("/")
        check("the page itself is served", status == 200 and b"<title>" in page)


def test_jdstruct() -> None:
    section("job descriptions get their shape back")
    # A Workday posting, in the shape Workday returns one: every newline
    # collapsed, so the whole thing arrives as a single line. The sections here
    # are long enough to cross the 200-character floor under which a run of
    # sentences is left as a paragraph rather than split into a list.
    flat = ("Position Summary The analyst supports the integration team. "
            "Primary Responsibilities Design, build and support integrations "
            "between the claims platform and the general ledger. Monitor the "
            "nightly loads and resolve failures before the business day opens. "
            "Answer questions from business partners about data lineage and "
            "reconciliations. Required Qualifications 3+ years writing SQL "
            "against a relational warehouse. A bachelor's degree in a "
            "quantitative field, or equivalent experience. Familiarity with "
            "Workday Integration Cloud or a comparable platform.")
    blocks = jdstruct.structure(flat)
    kinds = [b["kind"] for b in blocks]
    heads = [b["text"] for b in blocks if b["kind"] == "heading"]
    check("headings are recovered from a flattened posting",
          heads == ["Position Summary", "Primary Responsibilities",
                    "Required Qualifications"], str(heads))
    check("a list heading produces a list", "list" in kinds, str(kinds))

    # The bug this guards: `re.I` over the whole heading pattern also folded
    # the [A-Z] lookahead, so a mid-sentence "requirements" became a heading
    # and cut the sentence in half.
    sentence = ("We need someone with the ability to translate business "
                "requirements into scalable integration solutions that the "
                "team can maintain without a rewrite every quarter.")
    only = jdstruct.structure(sentence)
    check("a mid-sentence keyword is not a heading",
          all(b["kind"] != "heading" for b in only),
          str([b for b in only if b["kind"] == "heading"]))

    html_jd = "<p><strong>What You Will Do</strong></p><ul><li>Ship it</li>"               "<li>Keep it up</li></ul><p>2&#43; years required.</p>"
    blocks = jdstruct.structure(html_jd)
    check("bold paragraphs in ATS markup read as headings",
          blocks[0] == {"kind": "heading", "text": "What You Will Do"}, str(blocks[:1]))
    check("list items survive the tag strip",
          {"kind": "list", "items": ["Ship it", "Keep it up"]} in blocks, str(blocks))
    check("entities are unescaped",
          any("2+ years" in b.get("text", "") for b in blocks), str(blocks))
    check("an empty description is no blocks, not a crash",
          jdstruct.structure("") == [] and jdstruct.structure("   ") == [])


def test_runner() -> None:
    section("the console runs a job and keeps its output")
    done = threading.Event()

    def work(emit):
        print("first line")
        emit("second line\n")
        done.set()
        return {"value": 7}

    run = runner.start("a test", "test", work)
    check("a run starts queued or running", run.state in ("queued", "running"))
    done.wait(10)
    for _ in range(100):
        if runner.get(run.id).state in ("done", "failed"):
            break
        time.sleep(0.05)

    tail = runner.tail(run.id, 0)
    check("both print and emit are captured",
          tail["lines"] == ["first line", "second line"], str(tail["lines"]))
    check("the run reports done", tail["done"] and tail["state"] == "done")
    check("the result comes back", tail["result"] == {"value": 7})
    check("a tail after the end is empty", runner.tail(run.id, tail["next"])["lines"] == [])

    def boom(emit):
        raise ValueError("no")

    failed = runner.start("a failing test", "test", boom)
    for _ in range(100):
        if runner.get(failed.id).state in ("done", "failed"):
            break
        time.sleep(0.05)
    tail = runner.tail(failed.id, 0)
    check("a failure is recorded, not raised",
          tail["state"] == "failed" and "ValueError" in tail["error"], str(tail))
    check("the traceback lands in the output",
          any("ValueError" in line for line in tail["lines"]))


def test_archive() -> None:
    section("the archive")
    summary = archive.summary()
    check("the summary counts something", summary["total"] >= 0)
    page = archive.search(limit=5)
    check("a page is capped at the limit", len(page["rows"]) <= 5)
    check("the page says how many matched", page["matched"] >= len(page["rows"]))
    if page["rows"]:
        row = page["rows"][0]
        check("a row has no JD body", "description" not in row, str(row.keys()))
        check("a row says how long the posting stayed up", "live_days" in row)
    listed = archive.search(text="a nonsense phrase nothing will match")
    check("a search that matches nothing is empty, not everything",
          listed["matched"] == 0 and listed["rows"] == [])
    # The count above the table gets read as "this many jobs exist", and the
    # boards' own number is several times larger, which makes the radar look
    # like it is missing most of the market. It is not: it counts a posting
    # once and the boards count it every sweep. These three fields are what
    # the tab says so with, so they have to be present and consistent.
    for field in ("readings", "last_read", "last_new"):
        check(f"the summary reports {field}", field in summary)
    check("readings counts sightings, so it is never below the row count",
          summary["readings"] >= summary["total"])
    check("the newest sweep's new postings are a subset of what it read",
          summary["last_new"] <= summary["last_read"] or not summary["total"])


def test_access() -> None:
    """The network gate, which is only correct if it fails closed.

    Every check here is the same claim from a different angle: reaching this
    machine from anywhere but this machine requires a token, and the absence of
    a token is a refusal rather than an open door. A regression that turned
    `Unconfigured` into a warning would leave the desk serving a resume, an
    address, and a phone number to the coffee shop's wifi.
    """
    section("the network gate")
    saved = os.environ.pop(access.TOKEN_ENV, None)
    # `access.token()` re-reads `.env` on every call, so popping the variable
    # is not enough on a machine where phone access has been turned on: the
    # file puts it straight back and the no-token half of this test passes for
    # the wrong reason. Point ROOT at an empty directory for the duration, and
    # the checks below are about the code rather than about this desk.
    root = paths.ROOT
    empty = Path(tempfile.mkdtemp(prefix="jobdesk-noenv-"))
    paths.ROOT = empty
    try:
        check("loopback needs no token", access.check("127.0.0.1") is False)
        try:
            access.check("10.0.0.57")
            check("a LAN address with no token refuses to serve", False,
                  "it returned instead of raising")
        except access.Unconfigured as exc:
            check("a LAN address with no token refuses to serve", True)
            check("and the refusal says how to fix it",
                  access.TOKEN_ENV in str(exc), str(exc)[:120])

        os.environ[access.TOKEN_ENV] = "k" * 32
        check("a LAN address with a token serves, gated",
              access.check("10.0.0.57") is True)
        check("loopback stays ungated even with a token set",
              access.check("127.0.0.1") is False)
        check("a token comparison rejects a near miss",
              access.matches("k" * 31 + "x") is False)
        check("and accepts the real one", access.matches("k" * 32) is True)
        check("an empty presented token is never a match",
              access.matches("") is False)
    finally:
        paths.ROOT = root
        shutil.rmtree(empty, ignore_errors=True)
        os.environ.pop(access.TOKEN_ENV, None)
        if saved is not None:
            os.environ[access.TOKEN_ENV] = saved

    check("this machine's own addresses are recognised as local",
          net.is_this_machine("127.0.0.1"))
    check("someone else's is not", not net.is_this_machine("203.0.113.9"))


def test_home_screen() -> None:
    """What a phone needs to keep JobDesk on its home screen.

    All of it is served by this app or it does not exist: there is no CDN and
    the page's own CSP forbids one. So the manifest and every icon it names
    have to come back 200 over a real socket, and the icons have to be PNGs
    rather than an HTML 404 page with a .png on the end.
    """
    section("the home-screen icon")
    with Live() as live:
        status, body = live.raw("/manifest.webmanifest")
        check("the manifest is served", status == 200, str(status))
        manifest = json.loads(body.decode("utf-8"))
        check("it opens in its own window",
              manifest["display"] == "standalone")
        check("loopback gets no token in start_url",
              access.PARAM not in manifest["start_url"], manifest["start_url"])
        check("it names a maskable icon, which Android crops to its own shape",
              any(i.get("purpose") == "maskable" for i in manifest["icons"]))
        for icon in manifest["icons"]:
            code, blob = live.raw(icon["src"])
            check(f"{icon['src']} is served", code == 200, str(code))
            check(f"{icon['src']} really is a PNG",
                  blob[:4] == bytes([0x89, 0x50, 0x4E, 0x47]))
        code, blob = live.raw("/icon-180.png")
        check("the apple-touch icon is served", code == 200, str(code))

    page = (ROOT / "jobdesk" / "app" / "static" / "index.html").read_text(
        encoding="utf-8")
    for tag in ("/manifest.webmanifest", "apple-touch-icon",
                "apple-mobile-web-app-capable", "apple-mobile-web-app-title",
                "theme-color"):
        check(f"the page asks for {tag}", tag in page)


def test_phone() -> None:
    """The panel is drawn from one payload, so the payload has to be complete.

    The QR code used to be added by the GET route and by nothing else, which
    meant `turn_on` returned a state with no code in it and the panel blanked
    the code at the exact moment someone pressed the switch. The fix was to
    move the code into `state()`; this pins it there, on the shape rather than
    on the value, because a machine running the tests may legitimately have no
    token and no reachable address.
    """
    section("the phone panel's payload")

    payload = phone.state()
    for field in ("on", "port", "token_set", "address", "url", "svg",
                  "serving", "firewall", "arrivals"):
        check(f"the phone state reports {field}", field in payload)
    check("the code is an SVG when there is one to draw",
          payload["svg"] is None or payload["svg"].startswith("<svg"))
    check("a code is drawn exactly when there is a URL to put in it",
          bool(payload["svg"]) == bool(payload["url"]))
    # `turn_on`, `turn_off` and `rotate` all return `{**state(port), ...}`, so
    # everything checked above is true of their payloads too. Checking that
    # they still do it is worth a line, because the bug this test exists for
    # was a route that added a field one of those paths did not.
    import inspect
    for name in ("turn_on", "turn_off", "rotate"):
        body = inspect.getsource(getattr(phone, name))
        check(f"{name} returns the whole state", "**state(port)" in body)


def test_pasted_jd() -> None:
    """A pasted description is the one input that reaches the build directly.

    Both checks stop before anything is written: the first has no company, the
    second has a paste too short to tailor against. A build that got past
    either of these would leave a folder on disk, which is exactly why they
    are worth pinning down.
    """
    section("a pasted job description")
    lines = []

    try:
        actions.build_packet(lines.append, company="", title="",
                             jd="x" * 900)
        check("a posting with no company is refused", False)
    except actions.ActionError as exc:
        check("a posting with no company is refused", "company" in str(exc))

    try:
        actions.build_packet(lines.append, company="Testco",
                             title="Data Analyst", jd="too short to tailor")
        check("a paste under the minimum is refused", False)
    except actions.ActionError as exc:
        check("a paste under the minimum is refused",
              "characters" in str(exc), str(exc))

    page = (ROOT / "jobdesk" / "app" / "static" / "app.js").read_text(
        encoding="utf-8")
    check("the page sends the paste along with the build", "jd:" in page)


def test_no_writes() -> None:
    section("the page cannot submit anything")
    text = (ROOT / "jobdesk" / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for word in ("submit(", "form.submit", "autoApply", "auto_apply"):
        check(f"no {word} in the page", word not in text)
    routes = {path for _, path in api.ROUTES}
    check("no route builds or sends an application",
          not any(word in path for path in routes
                  for word in ("apply", "submit", "send")), str(routes))


def test_patch_table() -> None:
    section("skill tables are rewritten in place")
    source = (ROOT / "profile.example" / "targeting.toml").read_text(encoding="utf-8")
    before = tomllib.loads(source)
    patched = tomlpatch.patch_table(source, "core_skills", {"sql": 7, "power bi": 3})
    after = tomllib.loads(patched)
    check("the table holds exactly what was sent",
          after["core_skills"] == {"sql": 7, "power bi": 3}, str(after["core_skills"]))
    check("the other skill table is untouched",
          after.get("supporting_skills") == before.get("supporting_skills"))
    check("top-level settings are untouched",
          after["tier_1_titles"] == before["tier_1_titles"])
    check("no comment is lost", comments(patched) == comments(source),
          f"{comments(source)} -> {comments(patched)}")
    emptied = tomllib.loads(tomlpatch.patch_table(source, "core_skills", {}))
    check("a table can be emptied", emptied["core_skills"] == {})
    crlf = source.replace("\n", "\r\n")
    check("CRLF files stay CRLF",
          "\r\n" in tomlpatch.patch_table(crlf, "core_skills", {"sql": 1})
          and "\n" not in tomlpatch.patch_table(crlf, "core_skills", {"sql": 1})
          .replace("\r\n", ""))
    try:
        tomlpatch.patch_table(source, "no_such_table", {"a": 1})
        check("a missing table is an error", False, "it patched nothing silently")
    except tomlpatch.PatchError:
        check("a missing table is an error", True)


def test_criteria() -> None:
    section("the criteria panel")
    from jobdesk.app import criteria
    from jobdesk.radar import score

    values = tomllib.loads(
        (ROOT / "profile.example" / "targeting.toml").read_text(encoding="utf-8"))
    sections = criteria.form(values)
    keys = [f["key"] for s in sections for f in s["fields"]]
    check("every field has a label and help",
          all(f["label"] and f["help"] for s in sections for f in s["fields"]))
    check("no key appears twice", len(keys) == len(set(keys)))
    check("every key is a real setting in the example",
          all(k in values for k in keys), str([k for k in keys if k not in values]))
    skills = next(f for s in sections for f in s["fields"] if f["key"] == "core_skills")
    check("skill tables arrive as skill -> points", isinstance(skills["value"], dict)
          and all(isinstance(v, int) for v in skills["value"].values()))

    top, tables = criteria.clean({
        "tier_1_titles": ["Data Analyst", "data  analyst", ""],
        "salary_floor": "$55,000", "salary_target": "70000",
        "core_skills": {"SQL": 6, "Python": ""},
    })
    check("titles are lowercased and deduped", top["tier_1_titles"] == ["data analyst"],
          str(top["tier_1_titles"]))
    check("money accepts $ and commas", top["salary_floor"] == 55000)
    check("a skill with no number gets the default",
          tables["core_skills"] == {"sql": 6, "python": criteria.DEFAULT_WEIGHT["core_skills"]},
          str(tables))

    def refused(changes, word):
        try:
            criteria.clean(changes)
            return False
        except criteria.Invalid as exc:
            return word in str(exc).lower()

    check("new cannot outlast old",
          refused({"fresh_days": 30, "stale_days": 10}, "brand new"))
    check("target pay cannot be below the floor",
          refused({"salary_floor": 90000, "salary_target": 60000}, "aiming"))
    check("a skill worth more than the whole group is refused",
          refused({"core_skills": {"sql": 40}}, "between 0 and 25"))
    check("a key the panel does not own is refused",
          refused({"equivalency_ceiling": 3}, "targeting.toml"))
    check("a number that is not one is refused",
          refused({"years_comfortable": "lots"}, "whole number"))

    # The page quotes point values. If score.py changes one, these fail and
    # the sentence in criteria.py or index.html has to change with it.
    src = (ROOT / "jobdesk" / "radar" / "score.py").read_text(encoding="utf-8")
    help_text = " ".join(f[3] for s in criteria.SECTIONS for f in s["fields"])
    for points, needle in ((35, 'listed_why = 35, f"tier-1'),
                           (24, 'listed_why = 24, f"tier-2'),
                           (14, 'listed_why = 14, f"tier-3'),
                           (25, "points += 25"), (22, "points += 22"),
                           (10, "return -10 + points"), (8, "return 8 + points")):
        check(f"{points} points is what score.py gives",
              needle in src and f"{points} " in help_text, needle)
    check("the tier lines on the page match tier_for",
          (score.tier_for(75), score.tier_for(74), score.tier_for(60),
           score.tier_for(59), score.tier_for(45), score.tier_for(44))
          == ("A", "B", "B", "C", "C", "D"))
    page = (ROOT / "jobdesk" / "app" / "static" / "index.html").read_text(encoding="utf-8")
    check("the page's explainer says the same thing",
          "75 and up" in page and "60 to 74" in page and "45 to 59" in page)


def test_settings() -> None:
    section("settings")
    from jobdesk.app import prefs

    original = prefs.FILE
    tmp = Path(tempfile.mkdtemp())
    prefs.FILE = tmp / "settings.json"
    try:
        check("no file means the defaults", prefs.load() == prefs.DEFAULTS)
        check("newest first is the default order", prefs.DEFAULTS["jobs_sort"] == "newest")
        saved = prefs.save({"theme": "dark", "score_min": "60", "text_size": 115})
        check("a change is saved and read back",
              prefs.load()["theme"] == "dark" and saved["score_min"] == 60
              and saved["text_size"] == 115)
        check("unchanged keys keep their defaults", saved["density"] == "normal")
        for bad in ({"theme": "neon"}, {"score_min": 150}, {"nope": 1},
                    {"hide_applied": "yes"}, {"text_size": 300}):
            try:
                prefs.save(bad)
                check(f"{bad} is refused", False)
            except prefs.Invalid:
                check(f"{bad} is refused", True)
        flipped = prefs.save({"score_min": 90, "score_max": 50})
        check("a backwards range is swapped, not refused",
              (flipped["score_min"], flipped["score_max"]) == (50, 90))
        prefs.FILE.write_text('{"theme": "neon", "density": "roomy", "junk": 1',
                              encoding="utf-8")
        check("a broken file falls back to the defaults", prefs.load() == prefs.DEFAULTS)
        prefs.FILE.write_text('{"theme": "neon", "density": "roomy", "junk": 1}',
                              encoding="utf-8")
        loaded = prefs.load()
        check("a bad value costs that one setting",
              loaded["theme"] == "system" and loaded["density"] == "roomy"
              and "junk" not in loaded)

        folder = prefs.check_folder(str(tmp / "copies"))
        check("a new folder under an existing one is created", Path(folder).is_dir())
        check("blank means no copy", prefs.check_folder("  ") == "")
        for bad, word in (("relative\\path", "full path"),
                          (str(tmp / "missing" / "deeper"), "does not exist")):
            try:
                prefs.check_folder(bad)
                check(f"{word}: refused", False)
            except prefs.Invalid as exc:
                check(f"{word}: refused", word in str(exc))
        try:
            prefs.set_delivery({"packets": str(tmp)})
            check("the example profile's folders cannot be changed", False)
        except prefs.Invalid:
            check("the example profile's folders cannot be changed", True)

        with Live() as live:
            code, body = live.get("/api/settings")
            check("GET /api/settings answers",
                  code == 200 and body["settings"]["density"] == "roomy"
                  and "packets_builtin" in body["folders"], str(body)[:160])
            check("the example profile's folders are not editable",
                  body["folders"]["editable"] is False)
            code, body = live.post("/api/settings", {"settings": {"density": "compact"}})
            check("POST /api/settings saves", code == 200
                  and body["settings"]["density"] == "compact")
            code, body = live.post("/api/settings", {"settings": {"theme": "neon"}})
            check("a bad setting is a readable 400", code == 400 and "theme" in body["error"])
            code, body = live.post("/api/settings/folders", {"packets": str(tmp)})
            check("folders refuse the example profile over the API", code == 400)
            code, body = live.get("/api/status")
            check("status carries the settings", body.get("settings", {}).get("density")
                  == "compact")
            code, raw = live.raw("/theme.js")
            check("theme.js is served", code == 200 and b"jobdesk.look" in raw)
    finally:
        prefs.FILE = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_delivery_edit() -> None:
    section("changing where copies go")
    from jobdesk import profile
    from jobdesk.app import prefs

    tmp = Path(tempfile.mkdtemp())
    real, example = profile.REAL, profile.EXAMPLE
    saved_env = os.environ.pop("JOBDESK_PROFILE", None)
    try:
        # A profile of our own in a temp dir, so "is it the example" is no.
        shutil.copytree(example, tmp / "profile")
        profile.REAL = tmp / "profile"
        os.environ["JOBDESK_PROFILE"] = str(tmp / "profile")
        profile._read.cache_clear()
        target = tmp / "profile" / "delivery.toml"
        before = comments(target.read_text(encoding="utf-8"))

        info = prefs.set_delivery({"packets": str(tmp / "apps")})
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        check("a path is written and uncommented", data.get("packets") == str(tmp / "apps"),
              str(data))
        check("the untouched key stays off", "resumes" not in data)
        check("what Settings shows is what was saved", info["packets"] == str(tmp / "apps"))
        check("the explanation stays", comments(target.read_text(encoding="utf-8"))
              >= before - 1)

        prefs.set_delivery({"packets": str(tmp / "other")})
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        check("a second change replaces rather than duplicates",
              data.get("packets") == str(tmp / "other"))

        prefs.set_delivery({"packets": ""})
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        check("a blank turns the copy off", "packets" not in data, str(data))
    finally:
        profile.REAL = real
        if saved_env is not None:
            os.environ["JOBDESK_PROFILE"] = saved_env
        profile._read.cache_clear()
        shutil.rmtree(tmp, ignore_errors=True)


def test_market() -> None:
    """The peer-group arrow: the maths, and what it refuses to say.

    The index itself is built from whatever snapshots are on this machine, so
    the test builds its own groups instead of asserting numbers that depend on
    a data directory. What is worth pinning is the shape of the answer and the
    two places it declines: a group too small to judge by, and a salary
    estimate for a posting that already published a band.
    """
    section("market")

    check("hourly rates become annual", market._annual(30) == 30 * 2080)
    check("annual rates pass through", market._annual(82000) == 82000)
    check("remote beats the city", market.region("Richmond, VA", True) == "Remote")
    check("a state code is the region", market.region("Richmond, VA", False) == "VA")
    check("a state name is the region",
          market.region("Austin, Texas, United States", False) == "TX")
    check("an unparseable location is Elsewhere",
          market.region("3 Locations", False) == "Elsewhere")
    check("seniority does not split a role",
          market._role_key("Senior Data Analyst II") == market._role_key("Data Analyst"))
    check("a tie sits at the median", market._percentile([5.0, 5.0, 5.0], 5.0) == 50)

    saved = market._index
    try:
        group = {
            "pay": sorted(float(v) for v in range(60000, 160000, 5000)),
            "low": sorted(float(v) for v in range(50000, 70000, 1000)),
            "high": sorted(float(v) for v in range(90000, 110000, 1000)),
            "age": sorted(float(d) for d in range(0, 40)),
            "reach": sorted(float(r) for r in [1] * 10 + [2] * 10),
            "employers": 30, "roles": {"data analyst": 4}, "count": 40,
        }
        market._index = {"groups": {"analysis/reporting\u0000VA": group},
                         "files": 1, "postings": 40, "built_at": "now"}

        rich = {"job_family": "analysis/reporting", "location": "Richmond, VA",
                "salary_min": 150000, "salary_max": 160000, "remote": False,
                "posted_at": None, "source": "greenhouse"}
        poor = dict(rich, salary_min=40000, salary_max=45000)
        up, down = market.assess(rich), market.assess(poor)
        check("a well-paid posting points up", up and up["direction"] == "up",
              str(up))
        check("an underpaid posting points down", down and down["direction"] == "down",
              str(down))
        check("the factors add up to the whole arrow",
              up and sum(f["share"] for f in up["factors"]) == 100)
        check("every factor explains itself",
              up and all(f["detail"] and f["label"] for f in up["factors"]))
        check("40 peers is enough to be confident", up and up["confident"])

        nothing = market.assess(dict(rich, job_family="nursing"))
        check("an unknown peer group says nothing", nothing is None)

        check("a published band is not re-estimated",
              market.estimate_salary(rich) is None)
        band = market.estimate_salary(dict(rich, salary_min=None, salary_max=None))
        check("a blank band is estimated from peers", bool(band), str(band))
        check("an estimate is flagged as one", band and band["estimated"] is True)
        check("an estimate says where it came from", band and "did not state" in band["basis"])

        group["count"] = 3
        check("three peers is not a market",
              market.assess(rich) is None)
    finally:
        market._index = saved


def test_market_route() -> None:
    """/api/market answers, and answers about the current list only."""
    section("market over a socket")
    with Live() as live:
        code, body = live.get("/api/market")
        check("the route answers", code == 200, str(body)[:80])
        check("it says whether the index is ready", "ready" in body)
        check("it returns a uid map", isinstance(body.get("jobs"), dict))
        if body.get("ready"):
            _, listing = live.get("/api/jobs")
            listed = {row["uid"] for row in listing["jobs"]}
            extra = set(body["jobs"]) - listed
            check("it says nothing about postings outside the list", not extra,
                  str(sorted(extra)[:3]))
            check("every entry carries something",
                  all(("market" in v or "salary_estimate" in v)
                      for v in body["jobs"].values()))


def main() -> int:
    test_tomlpatch()
    test_patch_table()
    test_criteria()
    test_resume_import()
    test_validation()
    test_write()
    test_api()
    test_settings()
    test_delivery_edit()
    test_jdstruct()
    test_runner()
    test_archive()
    test_access()
    test_phone()
    test_home_screen()
    test_pasted_jd()
    test_no_writes()
    test_market()
    test_market_route()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
