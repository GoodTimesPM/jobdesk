"""The front door: one local page for the whole pipeline.

`app` is the only package allowed to import `radar`, `engine` and `apply`.
Those three still never import each other -- see tests/test_apply.py, which
asserts it -- and this package exists precisely so they don't have to. It is
the one place that is allowed to know all three exist.

Nothing here is a framework. The server is `http.server` from the standard
library, the page is one HTML file with its own CSS and JS, and the whole
thing is served on 127.0.0.1 to one person. A job search tool that needs a
build step before it will show you a job is a worse tool.
"""
