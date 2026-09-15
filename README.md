<img src="docs/jobdesk-icon.png" width="88" align="right" alt="">

# JobDesk

One desktop app for finding jobs, scoring them, tailoring a resume to the ones
worth it, and assembling a folder you can apply from.

No account, no API key, no model. Everything below runs on your machine
against your own files. The scoring is arithmetic you can read in
`radar/score.py`, and the resume tailoring is selection from content you wrote,
never generation.

**It never submits an application.** JobDesk builds the packet and opens the
posting. A human clicks submit. That is a permanent property of this program,
not a default you can turn off.

## Install

Windows, Python 3.11 or newer.

Download or clone this repository, then double-click **`Install.cmd`**. It
checks for Python and tells you where to get it if it is missing, builds a
private environment inside the folder, installs the dependencies, and puts a
JobDesk icon on your Desktop. It is safe to run again if something breaks.

JobDesk opens on its Setup tab the first time. Drop in your resume and it
fills in what it can read; the rest is six short screens about what you are
looking for. Nothing touches a job board until that is done.

To remove it:

```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

That deletes the environment and the shortcut. Your profile, your data and
your packets stay where they are, and the script prints the paths.

## What the tabs do

| Tab | |
|---|---|
| **Jobs** | Everything the radar found, scored 0-100 with a letter tier. Newest first by default. Filter by score range, search, sort any column. Open a row for the full posting, and build a packet from it. |
| **Applied** | The submission log. What you sent, when, what came back, and which sources actually reply. |
| **Archive** | Every posting ever seen, including the ones that scored too low to surface. Which employers post constantly and never score is a useful thing to know. |
| **Criteria** | Your targeting, as a form: titles by tier, geography, salary floor, the employers you want watched, the words that disqualify a posting. |
| **Console** | Whatever is running, streaming. Discovery runs, engine builds, packet assembly. |
| **Setup** | The first-run wizard, the phone pairing, the Desktop shortcut, the logon task. |
| **Settings** | The gear at the top right. What the Jobs tab opens with, theme, text size, row spacing, and where packets get written. |

## On your phone

The Setup tab has a switch for it. Turning it on mints an access token, binds
an address your phone can reach (your tailnet if you have one, your LAN
otherwise), registers the app to come back at logon, and draws a QR code.
Scan it and JobDesk opens on the phone, full queue, full packets.

Add it to the home screen and it gets its own icon and its own window.

Anything that is not this machine needs the token. There is no password, no
account and no cloud in the path -- the phone is talking to the computer on
your desk. If a device turns up that should not have, `Rotate` in the same
panel invalidates every paired device at once.

## How a posting compares

The Market column holds a green or red arrow and a percentage. It answers one
question: out of everything else hiring for this kind of role in this kind of
place, where does this one sit?

The peer group is the posting's job family and region, and the comparison uses
four measures, all read from the snapshot history already on your disk:

| | weight | |
|---|---|---|
| Pay | 40% | the midpoint against the group's published bands |
| Competition | 25% | how many other postings share the title, across how many employers |
| Exposure | 15% | how many boards carry it |
| Freshness | 20% | days up against the group's typical |

Three of the four are inverted so an up arrow always means better for you.
Exposure drops out, and the other weights grow to fill the gap, when every
posting in the group sits on the same number of boards, which is most groups.

Open the row and each measure is broken out with its percentile and a sentence
saying what the number came from. Under eight comparable postings there is no
arrow at all, and between eight and twenty-five it is drawn faintly, because a
percentile over eleven postings is a number pretending to be a measurement.

**Estimated salary bands.** Most postings publish no salary. A posting without
one gets the medians of its peer group's published bands, shown in a different
colour and flagged as an estimate everywhere it travels. It is the local
snapshot corpus doing the work, not a third-party salary service, so no
posting you are looking at leaves your machine.

## Your profile

`profile/` is the only place your own details live, and it is git-ignored.
Seven TOML files: who you are and what you have done (`master.toml`), what you
want (`targeting.toml`), how a posting's words map to yours
(`vocabulary.toml`), your cover letter paragraphs (`letter.toml`), your ATS
answers (`answers.toml`), the employers you want watched (`employers.toml`),
and where finished files get copied (`delivery.toml`, optional).

The setup wizard writes these files. It does not write a parallel format of
its own, so everything it produces stays editable in Notepad and nothing is
lost by outgrowing the form.

`profile.example/` is a complete working profile for a fictional Denver staff
accountant, shipped so a fresh clone runs the whole pipeline before you have
written a word. It is deliberately in accounting rather than analytics: an
example built in the author's own field would let those assumptions hide
inside the machinery.

Nothing in the code knows your name, your metro or your job titles.

## The resume engine will not invent a claim

A tailored resume is a **subset** of `master.toml`, reordered and reworded for
one posting. It may drop a bullet, promote a project, or match a posting's
vocabulary to a tool you actually use. It will never add a skill you did not
list, extend a date, or renumber an achievement.

The ATS simulator exists for the same reason: it throws away the layout,
re-reads the PDF the way a parser would, and shows you the document the
employer's software will actually see. Those two are usually not the same, and
nothing else on your screen tells you when they diverge.

## Layout

```
jobdesk/
  jobdesk/
    paths.py       one root, one answer, for all three packages
    profile.py     resolves profile/, honouring $JOBDESK_PROFILE
    radar/         discovery, scoring, dedupe, delivery
    engine/        tailoring, ATS simulation, rendering
    apply/         packets, letters, guard rules, the submission log
    app/           the window: server, API, static page, setup, phone access
  profile/         yours, git-ignored
  profile.example/ a complete working one, shipped
  tests/
  scripts/
```

`radar`, `engine` and `apply` never import each other. `apply` reads what
`radar` writes and drives `engine` through its CLI, and `tests/test_apply.py`
asserts it. `app/` is the one package allowed to import all three.

## Running the pieces directly

The app is the front door, but everything under it is still a CLI, and the
scheduled task and the app itself both call these:

```
JobDesk.cmd                            # the app, with a console for debugging
JobDesk.cmd --shortcut                 # write the desktop shortcut, then exit

py -m jobdesk.app                      # the same app, as a window
py -m jobdesk.radar.main --dry-run     # one discovery cycle, writes nothing
py -m jobdesk.engine.main check        # validate master content
py -m jobdesk.apply.main --help        # the packet builder's subcommands
```

## Tests

No framework, on purpose. Each file runs in about a second and touches no
network.

```
py tests/run_all.py                    # every suite plus the secret scan
```

Or one at a time:

```
py scripts/check_secrets.py
py tests/test_radar.py --scoring
py tests/test_engine.py
py tests/test_apply.py
py tests/test_app.py
```

`test_radar.py --scoring` holds twelve synthetic postings to the verdict each
one was added to prove: the equivalency escape hatch opens at five years and
not at ten, "2014 year over year" is not a fourteen-year requirement, safety
compliance stays capped, a volunteer posting scores zero. It exits non-zero
when one of those stops being true.

`test_app.py` runs the window over a real socket, and five of its checks exist
only to assert the program cannot submit an application: no `submit(` in the
page, no `form.submit`, no route that sends one.

`scripts/check_secrets.py` scans every git-tracked file for live credential
shapes. It exists because one got committed to a `.env.example`, the file
whose entire job is to show the shape of a secret without being one. It also
checks for your own contact details, read out of the git-ignored `profile/` so
the scanner never has to name them.

## Configuration

Optional. Copy `.env.radar.example` to `.env.radar` and `.env.apply.example`
to `.env.apply` if you want Discord notifications or Notion sync. Every value
in both is optional, and JobDesk works with neither.

`SWITCH.radar.txt` and `SWITCH.apply.txt` are kill switches for the scheduled
runs. Both fail open: a missing or unreadable file means the run proceeds.

## What it will not do

No LinkedIn or Indeed scraping, and no automated connection requests or
messages. Those get accounts restricted, and an account restriction costs more
than the postings are worth. The job-board sources are public APIs and
employer ATS endpoints that are meant to be read.

And it does not submit. Every path in this program ends at a folder and a
checklist.

## Version

0.2.3. `CHANGELOG.md` has what changed and why.

## License

MIT. See `LICENSE`.
