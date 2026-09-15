# Changelog

Dates are when the work landed, not when it was published.

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
