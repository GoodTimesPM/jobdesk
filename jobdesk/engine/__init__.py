"""Engine -- master content, per-job tailoring, ATS simulation.

One of JobDesk's three packages, and the offline one: it reads local files,
writes local files, and never talks to a job board. It selects among bullets
the user wrote; it does not generate text and cannot invent a number.

Deliberately isolated from `radar` and `apply` -- it shares the profile
directory with them, not code, config, or assumptions.
"""

__version__ = "0.1.0"
