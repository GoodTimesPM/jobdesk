"""What to ask a keyword board for.

`search_queries` in targeting.toml is a short list on purpose: every entry is
a round trip per board per run, and these boards are national, so a broad
query buries the local signal. The cost of keeping it short is that a board
only ever hears the handful of words the user happened to write down, and
"Insights Analyst" is not one of them.

The synonym table already says which other words mean the same job. This
reads it back out as extra queries, so widening the search and widening the
scoring are one edit in one file rather than two lists that drift apart.

Order matters and is deliberate: alternates for the terms the user actually
declared come first, then alternates for the rest of tier 1. A budget that
runs out should run out on the far end.
"""

from __future__ import annotations

from . import profile


def widen(seeds: list[str], budget: int) -> list[str]:
    """Extra queries for `seeds`, in priority order, at most `budget` of them.

    Never returns a seed back, and never the same phrase twice. An empty
    budget, an empty synonym table or `synonyms_enabled = false` all give the
    same answer: nothing, and the caller sends exactly what it used to.
    """
    if budget <= 0:
        return []
    table = profile.synonyms()
    if not table:
        return []

    taken = {s.strip().lower() for s in seeds}
    out: list[str] = []

    def drain(anchors: list[str]) -> None:
        """One alternate per anchor, then a second, and so on.

        Round-robin rather than anchor by anchor. A budget of four spent
        depth-first goes entirely to the first seed's four alternates and the
        other six seeds get nothing, which is the opposite of widening.
        """
        lists = [table.get(a.strip().lower(), []) for a in anchors]
        for rank in range(max((len(x) for x in lists), default=0)):
            for alts in lists:
                if len(out) >= budget:
                    return
                if rank < len(alts) and alts[rank] not in taken:
                    taken.add(alts[rank])
                    out.append(alts[rank])

    drain(seeds)
    if len(out) < budget:
        # Titles the user ranked first but did not turn into a query. Their
        # synonyms are still better guesses than anything further down.
        drain(list(profile.TIER_1_TITLES))
    return out
