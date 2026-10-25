# Where the skills library comes from

`skills_library.json` is built by `scripts/build_skills_library.py` from two
open data sets. Both are licensed CC BY 4.0, which asks for the credit below.
`skills_extra.toml` is written by hand for JobDesk.

## O*NET 31.0 Database, Software Skills

This product includes information from the O*NET 31.0 Database by the U.S.
Department of Labor, Employment and Training Administration (USDOL/ETA).
Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.
JobDesk has modified the data: product names are split into the spellings
postings use, vendor prefixes are removed, and names that are everyday words
are dropped or matched only as written. USDOL/ETA has not approved, endorsed,
or tested these modifications.

- https://www.onetcenter.org/database.html
- https://www.onetcenter.org/license_db.html

## GitHub Explore topics

Topic names and aliases from https://github.com/github/explore, licensed
CC BY 4.0. Only topics that are software (they have a home page, a
repository or a release date) are used.

- https://github.com/github/explore/blob/main/LICENSE

## Rebuilding

    py -m pip install wordfreq
    py scripts/build_skills_library.py

`wordfreq` (https://github.com/rspeer/wordfreq) is needed only to build the
file. It judges how common a word is in English. JobDesk itself does not
import it.
