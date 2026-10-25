"""Build jobdesk/engine/data/skills_library.json from two open sources.

    py -m pip install wordfreq        (once; only this script needs it)
    py scripts/build_skills_library.py [--cache DIR]

Sources, both CC BY 4.0 (attribution in engine/data/SOURCES.md):

- O*NET 31.0 "Software Skills": the software named in 1,000 US occupations,
  about 8,700 products, each flagged Hot Technology or In Demand when
  employers ask for it most.
- GitHub's topic list (github/explore): about 1,300 curated topics with their
  display names and aliases, which is where "k8s" for Kubernetes comes from.

Neither is used as is. A posting writes "Excel", not "Microsoft Excel", and
"SSO", not "Single sign-on SSO", so each name is split into the spellings a
posting uses. Then every spelling is checked against how common it is in
English (wordfreq): a name that is also an everyday word would turn every
"teams" and "access" into a missing skill. A rare word matches in any case.
A common one matches only capitalized, and only when it is a product name.
An everyday word is dropped.

Hand-written terms live in skills_extra.toml and win over this file; fix a
bad generated match with its `drop` list rather than by editing the output.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

try:
    from wordfreq import zipf_frequency
except ImportError:
    sys.exit("This script needs wordfreq: py -m pip install wordfreq")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "jobdesk" / "engine" / "data" / "skills_library.json"

ONET_VERSION = "31.0"
ONET_URL = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/software_skills.csv"
EXPLORE_URL = "https://codeload.github.com/github/explore/tar.gz/refs/heads/main"

# How common a word may be and still name a skill. Zipf 3.3 is about one use
# in two million words: "terraform" is 2.0, "kafka" 3.0, "agile" 3.4,
# "excel" 3.7, "teams" 5.0.
RARE = 3.3
# A capitalized product name may be commoner than that ("Spark", "Epic"),
# matched only as written. Past this it is too ordinary even capitalized:
# "Teams" and "Access" start sentences.
PROPER = 4.4

# First words of O*NET product names that are the vendor, not the product.
# "Microsoft Excel" is asked for as "Excel".
VENDORS = [
    "Amazon Web Services", "Dassault Systemes", "Thomson Reuters", "Henry Schein", "Red Hat",
    "Guidance Software", "Unreal Technology", "Microsoft", "Oracle", "IBM", "Autodesk", "Adobe",
    "SAP", "Sage", "Google", "Apache", "Apple", "Siemens", "ESRI", "Esri", "Bentley", "Cadence",
    "Corel", "Intuit", "Atlassian", "Salesforce", "Cisco", "VMware", "Amazon", "Meta", "Facebook",
    "Epic", "Cerner", "Trimble", "PTC", "TechSmith", "McNeel", "Elite", "Infor", "Blackbaud",
    "Citrix", "Dell", "HP", "Hewlett Packard Enterprise", "Hewlett-Packard", "Symantec",
    "McAfee", "Quest", "SolarWinds", "Splunk", "Tableau", "Teradata", "Informatica",
    "MathWorks", "Wolfram", "Minitab", "SPSS", "SAS", "Qlik", "Alteryx", "Workday", "Kronos",
    "ADP", "Ceridian", "Deltek", "Epicor", "NetSuite", "QuickBooks", "Xero", "Zoho", "HubSpot",
    "Hootsuite", "Smartsheet", "Asana", "Slack", "Zoom", "Dropbox", "Box", "DocuSign",
    "LexisNexis", "Westlaw", "Bloomberg", "FactSet", "Moody's", "Morningstar", "Nuance",
    "Allscripts", "athenahealth", "eClinicalWorks", "NextGen", "MEDITECH", "Meditech",
    "GE", "Philips", "Rockwell", "Schneider Electric", "Honeywell", "Emerson", "ABB",
    "JetBrains", "GitHub", "GitLab", "Docker", "HashiCorp", "Elastic", "MongoDB", "Redis",
    "Snowflake", "Databricks", "Pivotal", "Progress", "Embarcadero", "Unity Technologies",
]
GENERIC_SUFFIXES = re.compile(r"\s+(software|programming language|language|platform|"
                              r"framework|library|system|systems|suite|tools?)$", re.I)
ACRONYM = re.compile(r"^[A-Z][A-Z0-9&+]{1,7}s?$")


def fetch(url: str, cache: Path) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1])
    if path.exists():
        return path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "JobDesk skills library build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    path.write_bytes(data)
    return data


def freq(text: str) -> float:
    """How common the rarest word of `text` is. A phrase is as rare as its rarest word."""
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    return min((zipf_frequency(w, "en") for w in words), default=0.0)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "-", text.lower()).strip("-")


def spellings(name: str, product: bool) -> tuple[set[str], set[str], str]:
    """(case-blind aliases, exact-case aliases, label) for one name."""
    blind: set[str] = set()
    exact: set[str] = set()
    name = " ".join(name.split())
    words = name.replace("(", " ").replace(")", " ").split()
    # A trailing acronym is the product's short name: "Single sign-on SSO",
    # "Human resource information system (HRIS)". Two letters with no
    # parentheses are part of the name: "Power BI" is not "Power" and "BI".
    if len(words) > 1 and ACRONYM.match(words[-1]) and (len(words[-1]) > 2 or "(" in name):
        exact.add(words[-1])
        words = words[:-1]
    base = GENERIC_SUFFIXES.sub("", " ".join(words)) or " ".join(words)
    candidates = {base}
    for vendor in VENDORS:
        if base.startswith(vendor + " ") and len(base) > len(vendor) + 2:
            candidates.add(base[len(vendor) + 1:])
            break
    # The label keeps the vendor: "Google Slides", not "Slides".
    label = base
    for c in candidates | {name}:
        c = c.strip()
        if len(c) < 2:
            continue
        multi = " " in c
        # What is left of "Splunk Enterprise" or "Adobe Connect" once the
        # vendor goes is an ordinary word, not the product.
        if not multi and c != name and freq(c) >= RARE and not ACRONYM.match(c):
            continue
        if ACRONYM.match(c):
            exact.add(c)
        elif multi or freq(c) < RARE:
            # A long phrase is safe in any case; a single word only if rare.
            if not multi and len(c) < 3:
                continue
            blind.add(c.lower())
        elif product and c[:1].isupper() and freq(c) < PROPER:
            exact.add(c)
    # A phrase made only of everyday words is a description, not a name:
    # "Web server", "Data entry". Kept only when one of its words is rare,
    # or when it is written as a name: "Amazon Web Services".
    proper = {c.lower() for c in candidates | {name}
              if all(w[:1].isupper() or len(w) <= 3 for w in c.split())}
    blind = {b for b in blind if " " not in b or freq(b) < RARE + 1.2 or b in proper}
    return blind, exact, label


def onet(cache: Path) -> list[dict]:
    rows = csv.DictReader(io.StringIO(fetch(ONET_URL, cache).decode("utf-8")))
    products: dict[str, dict] = {}
    for row in rows:
        name = row["Workplace Example"].strip()
        entry = products.setdefault(name, {"hot": False, "jobs": 0})
        entry["hot"] |= row["Hot Technology"] == "Y" or row["In Demand"] == "Y"
        entry["jobs"] += 1
    terms = []
    for name, info in products.items():
        blind, exact, label = spellings(name, product=True)
        if not info["hot"]:
            # Obscure products keep only their full multi-word name: a
            # posting that names "RockWare RockWorks" means it.
            blind = {b for b in blind if " " in b}
            exact = {e for e in exact if len(e) >= 4}
        if blind or exact:
            terms.append({"id": "onet:" + slug(name), "label": label, "kind": "tool",
                          "ci": sorted(blind), "cs": sorted(exact), "hot": info["hot"],
                          "src": "onet"})
    return terms


def github_topics(cache: Path) -> list[dict]:
    data = fetch(EXPLORE_URL, cache)
    terms = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if len(parts) != 4 or parts[1] != "topics" or parts[3] != "index.md":
                continue
            text = tar.extractfile(member).read().decode("utf-8", "replace")
            head = text.split("---")[1] if text.startswith("---") else ""
            meta = {}
            for line in head.splitlines():
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"')
            topic = meta.get("topic") or parts[2]
            display = meta.get("display_name") or topic
            # A topic with a home page, a repository or a release date is a
            # piece of software. The rest are subjects ("chemistry",
            # "climate change") that a requirement line almost never means.
            product = any(meta.get(k) for k in ("github_url", "released", "created_by"))
            if not product:
                continue
            names = {display, topic.replace("-", " ")}
            names |= {a.strip() for a in meta.get("aliases", "").split(",") if a.strip()}
            blind: set[str] = set()
            exact: set[str] = set()
            for n in names:
                b, e, _ = spellings(n, product=True)
                blind |= b
                exact |= e
            if blind or exact:
                terms.append({"id": "gh:" + topic, "label": display, "kind": "tool",
                              "ci": sorted(blind), "cs": sorted(exact), "src": "github"})
    return terms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "jobdesk-skills-sources")
    args = parser.parse_args()
    terms = onet(args.cache) + github_topics(args.cache)
    # One alias, one term: the first source to claim it keeps it, O*NET's hot
    # products first since employers named them.
    terms.sort(key=lambda t: (not t.get("hot"), t["src"] != "onet", t["label"].lower()))
    claimed: set[str] = set()
    kept = []
    for term in terms:
        term["ci"] = [a for a in term["ci"] if a not in claimed]
        term["cs"] = [a for a in term["cs"] if a.lower() not in claimed]
        claimed.update(term["ci"])
        claimed.update(a.lower() for a in term["cs"])
        if term["ci"] or term["cs"]:
            kept.append(term)
    OUT.write_text(json.dumps({
        "built_by": "scripts/build_skills_library.py",
        "sources": [f"O*NET {ONET_VERSION} Software Skills (CC BY 4.0)",
                    "github/explore topics (CC BY 4.0)"],
        "terms": kept,
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    hot = sum(1 for t in kept if t.get("hot"))
    print(f"{len(kept)} terms ({hot} O*NET hot or in-demand) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
