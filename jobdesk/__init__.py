"""JobDesk -- one desk for finding, scoring and applying to jobs.

Three packages sit under here. `radar` finds and scores postings, `engine`
tailors a resume to one of them, `apply` assembles the application packet.
They do not import each other; `app` (the front door) is the only place
allowed to know about all three.

Nothing in this package calls a model or an API you need an account for.
"""

__version__ = "0.1.0"
