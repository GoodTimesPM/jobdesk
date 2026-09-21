# Changelog

Dates are when the work landed, not when it was published.

## 0.4.1 — 2026-09-21

A resume that runs to two pages is a resume nobody reads to the end of, and
this one had been shipping two pages for a third of the postings in the store.
It did it quietly: the fitter threw away four bullets, gained nothing, ran out
of bullets it was allowed to touch, and returned the overflowing document
anyway. Then the cover letter dropped its best paragraph because the page it
had just failed to fit no longer mentioned a number that paragraph used.

### Changed

- **The page budget is spent on whitespace before it is spent on content.**
  The renderer now has a layout with three dials on it -- leading, section
  spacing, and type size -- and the fitter walks a ladder of progressively
  tighter settings before it drops a single bullet. Across the 188 stored job
  descriptions this gets every resume onto one page with nothing cut and the
  type size untouched. Type only starts moving when air runs out, and stops at
  90%, because a resume small enough to squint at is a resume that gets
  skipped. The .docx is rendered at whatever the PDF settled on, so the two
  files are the same document.
- **One page is not negotiable.** When the ladder runs out the fitter drops
  optional bullets, and when those run out it crosses the `min_bullets` floors
  rather than return a second page. A floor exists so a job does not appear
  with nothing under it. That is worth defending against a coverage score and
  it is not worth defending against page two. Crossing one is reported.
- **The summary is gone.** Four lines that a reader skips, at the top of the
  one page where space is the binding constraint, and they buy two bullets.
  `render.summary` in master.toml sets it, SUMMARY moved from the expected
  sections to the optional ones so an ATS parse is no longer docked ten points
  for its absence, and the `--summary` flag still overrides per posting.
- **Cover letter paragraphs are ordered by what they have to do with the
  posting**, not by where they sit in letter.toml. A paragraph that names the
  employer being applied to outranks everything else: if you did a project for
  this company, that is the most applicable evidence you own. Applying to
  CarMax as a Strategy Analyst, the paragraph about a CarMax dataset used to
  lose to whatever had been typed above it.

### Fixed

- **The letter gate was checking numbers against the wrong document.** It read
  the tailored resume, which is one page selected for one posting, so a true
  claim became unsupported the moment its bullet lost a page-fit contest. The
  CarMax paragraph was being held back for saying "65" about a 65-column
  dataset. Numbers are now checked against every claim confirmed in
  master.toml. The rule has not loosened -- a number still has to be one you
  approved, and drafts do not count -- it is being asked of the right corpus.

## 0.4.0 — 2026-09-21

Three things in one release, and they turn out to be the same thing: a board
that is wrong is worse than a board that is empty. A posting whose body never
arrived, an employer that is really a hundred employers, a description stored
as one grey paragraph -- each of them looks like data and answers like data,
and each one quietly produces a worse application than doing nothing would
have.

### Added

- **`py -m jobdesk.radar.gather`** builds a metro's employer list instead of
  charging you an afternoon of reading. It collects names from public
  directories near the metro in `targeting.toml`, and it only keeps a name
  that cost something to learn: one that turns up somewhere that had to check
  it. The seed file then probes each for an ATS board.
- **`py -m jobdesk.radar.candidates --repair-partials`** goes back for the
  postings that reached the board with no body, or with 500 characters of one,
  and fetches the real thing. It reads one host at a time at the rate that
  host tolerates, and when a host answers with a bot check it says so by name
  and stops rather than guessing at an empty page. A posting it cannot read
  stays unread and says so; the app has a paste button for those.
- **Colour schemes, fonts and seven text sizes** in Settings. `theme` is light
  or dark and `scheme` is which colours, so every scheme has both. Slate,
  Ocean, Forest, Plum, Sand, and a high-contrast scheme that moves the ink as
  well, for eyes the default greys do not serve. Fonts come from what the
  machine already has, because a font that has to be downloaded is a font that
  is missing the first time the window opens without internet.
- **A blocked packet has a way past, and it leaves a mark.** The guard has
  always been able to be overridden and the page never offered it, which is
  the worst version of a rule: the work still happens, in a browser tab, with
  nothing written down. The banner now carries the override, names the rules
  being waived, and records them in APPLY.md and on the application row.

### Fixed

- **A job board is not an employer.** "Commonwealth of Virginia" is one
  sitemap and about a hundred agencies. The two-open-applications cap counted
  them as one company, so an open req at the Department of Accounts blocked a
  packet for the Department of Professional and Occupational Regulation, two
  organizations whose only connection is a domain name. The cap now counts
  against the agency named inside the posting. Where that is unknown the count
  is the worst case, and the worst case is not "all of them": two applications
  at two different agencies cannot both be at whichever agency this posting
  belongs to, so it counts the unknowns plus the biggest single named agency.
  A softer ceiling across the whole board warns at six, because the
  Commonwealth runs one applicant system even though the agencies do not share
  a hiring manager.
- **A 500-character snippet was being tailored against.** Adzuna cuts every
  description at 500 characters, and what it cuts is the bottom of the
  posting, where the years requirement lives. Scoring paid for silence there.
  A snippet is now a fact the posting carries, the packet builder refuses to
  tailor against one, and the repair command goes and gets the rest.
- **A posting with no line breaks gave up its agency and its pay.** The
  sitemap fetcher flattens a posting to one unbroken line. The header parser
  wanted its labels at the start of a line, which is how the posting looks in
  a browser and how every test fed it, so it read nothing at all off the real
  cache while passing everything. One Fair Housing Investigator posting was
  showing an $86k-$131k guess over a stated range of $57,000-$72,000.
- **Bullets that survived without their line breaks** arrived as one grey
  paragraph with dots in it. Splitting on a dot bullet reconstructs the list;
  the hyphen and the asterisk stay out of it, because inside a line they are
  ranges and footnotes, not bullets.
- **The dismiss X on the notification bar** had been pushed off the right edge
  by the button added beside it.

### Changed

- **Nothing in the code decides where you live or what you do.** The last
  hard-coded metro and the last hard-coded job family came out, and the setup
  wizard writes the two keys it had been leaving for you to find.
- **A company cannot confirm its own board by its own name.** A board is
  confirmed from the company's own website now. Six wrong boards had been
  accepted on a name match alone, and a wrong board is the failure this whole
  program is built around.
- **The detail budget is spent on the best postings**, not on whichever ones
  came back first.

## 0.3.1 — 2026-09-17

A coverage release, and the price of it. Adzuna adds 222 Richmond postings a
run for 16 of a 250-call daily budget. Nearly all the work was not adding the
source. It was making the board safe to read afterwards.

### Fixed

- **A carried posting is re-scored.** `candidates.json` keeps a posting for 30
  days past the last run that saw it, and until now a carried row kept
  whatever number the code of the day gave it. The morning after 0.3.0 took
  the top score from 100 to 96, 46 rows scored by the old code were still
  claiming a perfect 100 and sitting above everything found that day. The file
  is sorted by score and the Jobs tab reads it in order, so the board was
  showing two scales at once with the obsolete one on top. The general form of
  that bug is worse than the symptom: every scoring change ever shipped only
  reached postings found after it shipped. Freshness never decayed either, so
  a row kept its +10 for a month. Re-scoring costs no network, and the score
  floor now applies to carried rows too, so a posting today's rules would
  disqualify finally leaves.
- **`--dry-run` was writing a snapshot** into the market history — a few
  hundred rows a run. The last write that ignored the flag.

### Changed

- **A truncated posting cannot claim to be a known quantity.** Adzuna cuts
  every description at 500 characters: measured 222 of 222, minimum 498,
  median 500, all ending in an ellipsis. What is cut is the bottom of the
  posting, where the years requirement lives. Scoring paid +6 for "no explicit
  years requirement", which on a snippet rewards silence and would have put
  five-year reqs at the top of a board built to keep them off. Truncation is
  now a fact the posting carries: silence earns nothing and says so, and the
  score is capped at 78. That clears the A floor of 75, so a strong local fit
  still surfaces and still reads as one. It just cannot outrank a posting
  somebody read end to end.
- **One requisition farmed out across twenty staffing firms is one row.** A
  single Virginia SCC Power BI contract came back from twenty shops and
  reached the digest as 23 separate A-tier jobs. Dedupe could not see it: it
  keys on company plus title, and a farm differs on the company every time.
  Two rules were measured and thrown away first — collapsing identical titles
  across companies would have merged five real ABA clinics and two real
  data-analyst openings, and collapsing on the requisition number reached only
  4 of 28 copies, because the 500-character cut takes the number with it. What
  holds: four or more companies on one title, and at least a quarter of the
  group already flagged as agency. The shops decorate, so a span comes off
  only when all of it is decoration ("(Hybrid)" goes, "(Federal Grants & eRA
  Systems)" stays), and the place names come from the targeting profile rather
  than being hardcoded to one city. The groups are mixtures, so a company
  already on the watch list is never dropped. Live: 36 of 222 collapsed,
  A-tier from 50 to 30, with Markel, GovCIO and all ten behaviour-analyst
  postings kept.
- **A contract shop is recognised by how it writes.** `staffing_agencies` only
  ever knew the firms somebody thought to type, and one pull produced fourteen
  shops advertising a single state req between them, none of them a name
  anyone would have listed in advance. The new phrases were picked by
  measuring 44 known-bodyshop postings against 12 known-real ones and keeping
  only those that hit every shop and no employer. `learn.py` reads the flag as
  well as the name list; without it, one pull qualified 65 contract shops as
  employers to probe and watch every morning.

### Known

Nearly every Adzuna row in the A-tier says "years requirement not visible in
the snippet". The cap keeps those off the top of the board, but the tier is
now largely made of postings whose requirements nobody has read. Closing that
means fetching the employer's own page, not another scoring rule.

## 0.3.0 — 2026-09-17

A scoring release. The complaint that started it: postings with "Director" in
the title were scoring 100, reqs asking for five years were reading as if they
asked for two, and 100 turned up often enough that it had stopped meaning
anything. All three were true.

### Changed

- **100 means perfect again.** The raw total a posting can reach now sits
  above 100, and the last stretch of it is compressed, so the final ten points
  cost several times what the first ten did. Nothing below the linear point
  moves at all. On the live board this took the top score from 100 to 96 and
  the count of perfect scores from common to none, without reordering
  anything: the scale is monotonic, so it changes what a number means and not
  which posting won.
- **Seniority is read as a rank, not as a word.** "Associate Director" is a
  director, "Senior Associate" is not early-career, and "FSP Associate
  Manager" is neither. A junior word sitting on a senior noun used to count as
  an early-career signal and hand the posting points for being exactly the
  thing it was not. A slash list still disarms the block, because "Associate
  Data Engineer / Data Engineer II / Senior Data Engineer" is one req with
  three rungs and the bottom one is real.
- **A requisition number is not a level.** "Lead Budget Analyst 00151" was
  reading as level 1. "Accounting Analyst 1" still is.
- **The highest floor in a posting wins, and a range is a band.** A req asking
  for "2 years of SQL and 5 years of financial reporting" is a five-year req,
  and it was being scored as a two. "3-5 years" no longer passes as a three
  either: a band that tops out past the stretch is scored on where it tops
  out. Tools stayed flexible, which is the trade the user asked for -- be
  generous about which software, strict about the years.

### Added

- **Synonyms, marked experimental.** `targeting.toml` grew a `[[synonym]]`
  section saying which other words mean the same job or the same tool. One
  table, read three times: the title score, the skill score, and the queries
  the boards are sent, so widening what you find and widening what scores well
  is one edit in one file rather than two lists that drift apart. A title
  synonym lands a notch below the term it stands in for, because it is a guess
  about wording; a tool synonym earns full weight, since a posting asking for
  DAX is asking for Power BI and there is no judgment call in that. Measured
  over the live board: 90 postings gained points, none lost any, and nothing
  returned to 100.
- **Alternates match on word boundaries.** The tier lists and the skill table
  match bare substrings, which is fine for terms you chose and can fix. A
  synonym list is long enough that a short entry eventually collides, and
  "elt" inside "skeleton" is not a data pipeline. The boundary is what makes
  three-letter tool names writable at all, so this made the layer tighter
  rather than looser.
- **`scripts/radar/check_profile.py`.** TOML tells you the file parses. It
  does not tell you `for = "data analsyt"` is a typo, and neither does the
  scorer: an anchor matching no tier list and no skill key is silently worth
  nothing, which looks exactly like a synonym that never fires. It found ten
  real problems on the first run against the profile it was written for.
- **The boards get asked for the other words too.** No amount of scoring fixes
  a posting the board never sent. The synonym table is read back out as extra
  queries, spread round-robin across the seeds rather than draining the first
  one's alternates and leaving the rest nothing. Workday gets half the budget
  of the keyword boards, because that query list goes to every tenant on the
  employer list and one more query there is 27 more calls.
- **Workday phrase queries.** `workday_queries` insisted on single words on
  the grounds that Workday ANDs a phrase and returns nothing. Measured against
  a live tenant, `searchText` "analyst" returns 298 postings, "business
  intelligence" 223 and "data analyst" 209. The search is OR-ish and ranked by
  relevance, and since each query takes one page of twenty, a phrase returns a
  different top twenty than the bare word. The belief was costing coverage for
  no reason.
- **Employers discover themselves.** The watch list was 91 companies because
  someone typed them in, while a single run surfaced 442 postings from 203
  companies and discarded 155 of those companies the moment the digest went
  out, several scoring in the nineties. Now any company whose best posting of
  the run clears 75 gets its ATS probed, and confirmed boards are watched
  directly from then on. 104 companies on the current board qualify, which at
  five a run is three weeks of new employers out of data already on disk.
- **A discovered board has to prove it is the right company.** A slug is just
  a string and these APIs hand back somebody else's board for it -- Ashby's
  `solstice` is a New York AI startup, not Solstice Advanced Materials of
  Chesterfield. So a board counts only if it is currently advertising a title
  that company was already seen posting; a collision board would have to be
  running the same req. Anything short of that is a miss, remembered for 30
  days so the same names do not burn the probe budget every morning. Learned
  employers are written to `data/radar/employers.learned.toml` and never into
  the hand-curated profile.

### Fixed

- **The age test no longer fails for one hour every night.** `test_age` built
  each case as "N hours before now" off the real clock, so between midnight
  and 1am "an hour ago" landed on yesterday and the check went red. It is
  anchored at midday now.

## 0.2.7 — 2026-09-16

### Added

- **The installer installs Python.** It used to find Python or stop, which
  put the one genuinely hard step of a first run on the person least equipped
  to do it: read this paragraph, pick the right download of the three on the
  page, remember to tick a checkbox nobody explains, then come back and start
  again in a new window. It now offers to do it, and Enter is yes. winget
  where there is winget, python.org's installer where there is not, for the
  current account only so it never needs an administrator. 3.12 rather than
  the newest, because PyMuPDF and pywebview publish prebuilt wheels for it.
  `-NoPythonInstall` keeps the old behaviour for anyone who wants to manage
  their own.
- **Python is looked for where it is, not only where the PATH says.** A
  Python installed sixty seconds ago is not on this process's PATH and cannot
  be: a process is handed its environment at birth. So the search also reads
  the stored PATH back out of the registry and sweeps the directories the
  official installer actually writes to. Without that, the installer would
  have installed Python and then failed to find it.

### Fixed

- **"today" in the Posted column means today.** It was counting elapsed hours
  and dividing by 24, so a posting that went up at nine last night still read
  as "today" at eight this morning, and "1d" covered part of today as well as
  part of the day before. The column was describing two different days at once
  at exactly the hour you check what the overnight run brought in: of the 446
  postings on the board, twelve claimed to be from today and only three were.
  It now compares calendar dates, so today is the date on the calendar and
  "1d" is yesterday. A posting whose feed gave a bare date with no time of day
  is read as that date rather than being shifted between timezones, which
  would have pushed half the board back a day.
- **A posting cannot have gone up after the run that found it.** The sitemap
  lane reads `<lastmod>`, which is the day the employer's page last changed,
  not the day the job went up. A statewide board that regenerates a posting
  stamps it with today, so five reqs first seen on the afternoon of the 15th
  came back on the 16th reading "today", sorted to the top, and scored as
  fresh. The seen-store knows better: it already had them the day before. A
  post date later than the first sighting is now capped at the first sighting,
  in the radar before scoring and in the Jobs table before it draws. It is a
  ceiling, not a guess at the real date.

## 0.2.6 — 2026-09-16

### Fixed

- **The Jobs tab notices that the radar ran.** The window stays open for days
  and the radar runs to a schedule behind it, but the page loaded its rows
  once and never asked again, so a window left open overnight showed
  yesterday evening's scoring all morning. It now asks `/api/pulse` once a
  minute, and again the moment the window comes back to the front. When the
  answer changes it reloads the rows in place, keeps your filters, sort, open
  row and stars, and says how many postings are new.
- **`/api/pulse`.** One `stat` of the candidate file, no parse. `/api/status`
  answered the same question by reading and decoding two and a half megabytes
  of JSON, which is not a request anything can afford to make every minute.
- **Nothing the server sends is cached.** Every response now carries
  `Cache-Control: no-store`. The files are on the same disk as the process
  reading them, so a cache saves nothing worth having, and what it costs is a
  page that keeps serving the old copy after an update and a job list from
  before the last run. Neither failure announces itself.
- **One JobDesk, however many times you open the icon.** A second launch used
  to start a second server: it found the port busy, quietly took the next one,
  and served its own copy of the code and its own read of the data. A window
  left open for a week then kept answering out of the week-old process while
  the files on disk moved on underneath it, with nothing on screen saying
  which one you were looking at. A second launch now raises the window that is
  already open and exits.
- **The port search probes the address it is about to bind.** It always probed
  loopback, whatever it was really binding, so the headless server going onto
  the tailnet address stepped over a port that was free on that address
  because the desktop window happened to hold it on 127.0.0.1 -- and landed on
  a port nobody had been told about. That is how two servers on "the same
  port" ended up being two different ports.
- **`X-JobDesk-Version` on every response.** How one JobDesk tells "something
  is on my port" from "I am already running", and which build is answering.
  It rides on the refusals too, because a server behind the phone-access token
  still has to be identifiable to the machine it is sitting on.

## 0.2.5 — 2026-09-15

### Added

- **A star on every row in the Jobs tab.** A posting worth coming back to,
  marked in one click. The stars live in `data/stars.json` on the server
  rather than in the browser, because JobDesk is one app opened from a
  desktop window and a phone, and a star put on a row at lunch should be on
  that row in the evening. `Starred only` is a filter next to the others and
  a default in Settings, and the star column sorts like any other.
- **A star outlives the posting it is on.** The radar keeps thirty days and
  then drops a posting, which would turn a star into an id pointing at
  nothing. So each star also keeps the title, the company and the link as
  they were when it was set, and the Jobs tab says "2 starred postings aged
  out of the list" underneath the table with both still linked. Losing one
  quietly is the failure this is here to prevent.

### Fixed

- **`Install.cmd` survives being downloaded.** Everything extracted from a ZIP
  carries a mark saying it came from the internet, and Windows refuses to run
  a marked script. The symptom was not an error anyone could act on: a window
  opened, something mentioned a digital signature, and nothing installed. The
  installer now clears the mark on the way in.
- **Running it from inside the ZIP says so.** Double-clicking a `.cmd` without
  extracting first does not fail; Windows copies that one file somewhere else
  and runs it there, alone, away from everything it needs. The error that
  followed was about a missing `requirements.txt`, which sends you looking for
  the wrong problem. The installer now recognises the temporary folder
  Explorer uses and tells you to extract first.

## 0.2.4 — 2026-09-15

A salary on every row, the right icon on the window, and a survey of the
other forty-nine states.

### Added

- **Salaries are read out of the JD body.** The APIs publish a band on 201 of
  435 live candidates. The rest often state one in prose and nothing was
  looking: `parse_salary` now handles Greenhouse's markup band
  (`<span>$72,000</span><span class="divider">&mdash;</span>`), Workday's
  `$111,160/yr to $138,950/yr`, hourly ranges, a lone hourly rate, and a lone
  figure sitting on a compensation cue, which is reported as a floor rather
  than inflated into a band. That is 36 more employer-stated figures, 46.2% to
  54.7%. The other half of the job is refusal: `$200B in annualized spend`,
  `$250M+ earned through our platform`, `educational assistance up to $2500`,
  Workday's unfilled `$1.00 - $1.00` template and another country's dollars
  (`CI$60,000`, a Cayman Islands job) all have to stay out, and each of them
  is a test case taken from a real posting.
- **schema.org `baseSalary` on the sitemap lane.** The sitemap sources parse a
  `JobPosting` block for the title, the location and the description and threw
  the salary away, so that lane published a band 0% of the time.
  `MonetaryAmount` now comes through, hourly and weekly and monthly converted
  to annual.
- **A survey of the other states' job indexes.** `scripts/radar/
  discover_state_boards.py` probes all 51 state and DC career sites against
  the same three gates a source has to clear to be added: robots.txt permits
  the path, the sitemap lists individual postings, a sample posting carries
  JobPosting markup. The candidate list is in `state_board_candidates.py`.
  The honest result is that one state clears all three. Ten run on NEOGOV,
  whose terms forbid harvesting, and the codebase already said so.

### Changed

- **The estimator widens instead of giving up.** A posting with no published
  band was compared against its own job family in its own region and nothing
  else, and 48 of 435 postings fell through that cell because it held fewer
  than eight published bands. It now steps out: the family anywhere, then the
  region across families, then the whole series. Every row shows a figure,
  `basis` says how wide the comparison had to go, and `estimated: true` still
  travels with it so nothing can show it as the employer's number.
- **A bot challenge is reported, not swallowed.** `fetch_details` caught every
  exception and continued, so a host that stopped returning bodies looked
  exactly like a host whose postings are all short. jobs.virginia.gov answers
  202 with an empty body, and 52 Commonwealth postings sat in the candidate
  set with no description and no salary and nothing anywhere saying why. An
  empty 2xx now raises `Challenged`, the run log names the source, and the
  rest of the detail budget stops being spent on it. The challenge itself is
  not worked around.

### Fixed

- **The window wears the JobDesk icon.** It had a Python logo. `_set_window_icon`
  found its target with `FindWindowW`, which walks every window on the desktop
  in Z-order and returns the first title match. On Windows 11 that match is
  `Windows.Internal.Shell.TabProxyWindow`, an invisible stand-in the shell
  makes for taskbar thumbnails, which copies the app title. Loading an icon
  onto it succeeds and reports success and changes nothing you can see. The
  real frame is found by `EnumWindows` filtered to this process's own visible
  unowned top-level window. `LR_DEFAULTSIZE` is gone too, because it overrode
  the 16 and 32 pixel sizes being asked for. The process also declares an
  AppUserModelID, so JobDesk gets its own taskbar button instead of sharing
  one with every other `pythonw.exe` app.

## 0.2.3 — 2026-09-14

The packet, read rather than dumped.

### Changed

- **A packet file is rendered as markdown.** APPLY.md is a checklist with a
  table at the end, and it arrived in the panel as one paragraph with every
  newline collapsed. Headings, bullets, checkboxes, tables, code, blockquotes
  and links now come through as themselves. The cover letter gets its
  paragraphs back. `.txt` files stay unformatted, since being unformatted is
  the only thing they are for. Nothing is parsed as HTML, so a posting's own
  text still cannot put markup on the page.
- **The section headers read as a contents page.** Seven of them stack up under
  a packet, so each is now a row with a visible edge instead of a line of
  coloured text. The label is plain ink, the row is what says "click here", and
  one triangle turns to say which section is open. A column of seven cyan
  headings read as seven warnings, so the accent is gone from the label and
  kept for the focus ring. Text selection got a colour too: the browser's
  default blue over a dark panel is a slab you cannot read through.

## 0.2.2 — 2026-09-14

Three things the Applied tab was missing, all of them noticed in use.

### Added

- **A row can be removed.** A packet gets built, the posting closes before you
  reach the form, and the log now holds an application that was never made.
  There was no way to take it off. "Remove this row" is at the end of the
  detail panel; it arms on the first click and removes on the second, so there
  is no dialog to aim at on a phone. The packet folder on disk is left alone.
  What goes is the claim that it was sent, which is the part that was wrong,
  and the posting goes back into the queue in case it reopens.

### Changed

- **The banner leaves on its own.** The message after a packet build is a
  receipt, and it used to sit across the top of every tab until something
  replaced it. It clears itself after eight seconds now and has an X on the
  right. Errors stay, and so does the standing "example profile" notice.
- **The funnel tiles are coloured like the status dropdowns.** "157 rejected"
  at the top of the tab and a red dropdown in the row below it are the same
  fact. Same four colours: grey prepared, blue in play, green offer, red
  closed. A tile at zero stays grey.

## 0.2.1 — 2026-09-14

Three phone fixes, all of them things the desktop layout hid.

### Fixed

- **The Market column was unreachable on a phone.** It is the sixth of eight
  columns, and the table scrolls sideways there, so the arrow sat about two
  thirds of a swipe off the right edge. It now sits second, next to the score,
  on screens under 700px. The header reorders with it, and rotating the phone
  moves it back without a reload.
- **The page sat off-centre on narrow screens.** The two report cards under the
  application table were laid out with a hard 320px minimum, so on anything
  under about 350px they refused to shrink and pushed the document wider than
  the window. Everything then rendered against a page wider than the screen,
  with a dead strip down one side.

### Changed

- **The status colour moved to the status dropdown.** A rejected row used to
  say so twice: a red bubble beside the company name and the word in the
  dropdown next to it. The bubble is gone, and the dropdown carries the colour
  instead, on the control you actually change. Every option in the open menu is
  coloured too, so the list reads as a legend before you pick from it: grey for
  prepared, blue for anything in flight, green for an offer, red for rejected,
  ghosted and withdrawn. The border is tinted as well as the text, because iOS
  ignores a colour on an `<option>`.

## 0.2.0 — 2026-09-13

### Added

- **Market column.** Every posting gets a green or red arrow and a percentage
  saying where it sits among postings for the same job family in the same
  region. Four weighted measures behind it: pay, competition, exposure and
  freshness, three of them inverted so up always favours the applicant.
  Expanding a row breaks out each measure with its percentile and a sentence
  explaining the number.
- **Estimated salary bands.** A posting that published no salary gets the
  medians of its peer group's published bands, drawn in a different colour and
  carrying an `estimated` flag everywhere it travels. Built from the local
  snapshot history; nothing is sent anywhere.
- **Settings.** A gear at the top right: what the Jobs tab opens with, theme,
  text size, row spacing, and where packets are written. Saved settings apply
  before the first paint rather than flashing the default first.
- `GET /api/market`, which the page requests after the table has already
  drawn. The index builds on a background thread at server start and nothing
  waits on it.

### Changed

- The Jobs tab opens newest first, with the score breaking ties between
  postings from the same day.
- The Criteria tab explains what each section does and how a score is reached,
  instead of presenting the fields bare.
- Tabs and the content column are centred. On a wide desktop the page used to
  leave several hundred pixels of dead space on the right.

### Fixed

- Hourly rates rendered as `$0k-$0k`, because a $30 rate rounds to zero
  thousands. They now render as `$30/hr`.
- Percentiles read `3th`, `1th` and `2th`.
- A posting that was the only one with its title read "1 posting share this
  title".
- A posting with no date sorted first under "newest first". It counted as -1;
  it now sorts last in both directions.

## 0.1.0 — 2026-09-10

First public release. Discovery across eleven sources, scoring, the resume
engine, packet assembly, the submission log, phone access, and the installer.

It has never been able to submit an application, and five checks in
`tests/test_app.py` exist to keep it that way.
