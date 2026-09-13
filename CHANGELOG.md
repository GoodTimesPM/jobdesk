# Changelog

Dates are when the work landed, not when it was published.

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
