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
from jobdesk.app import (access, actions, api, archive, jdstruct, net, phone,
                         resume_import, runner, server, setup, tomlpatch)

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


def main() -> int:
    test_tomlpatch()
    test_resume_import()
    test_validation()
    test_write()
    test_api()
    test_jdstruct()
    test_runner()
    test_archive()
    test_access()
    test_phone()
    test_home_screen()
    test_pasted_jd()
    test_no_writes()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
