"""`py -m jobdesk.app` -- open JobDesk.

A window by default, because that is what a person double-clicking a desktop
icon expects. `--browser` serves the same app to a browser tab instead, and
`--serve` runs it headless for a scheduled task. `--shortcut` writes the
desktop icon and exits.
"""

from __future__ import annotations

import argparse

from . import access, desktop, net, server, shortcut


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobdesk.app", description="The JobDesk window.")
    parser.add_argument("--port", type=int, default=server.DEFAULT_PORT,
                        help=f"default {server.DEFAULT_PORT}; the next free "
                             f"port is used if it is busy")
    parser.add_argument("--browser", action="store_true",
                        help="open a browser tab instead of the app window")
    parser.add_argument("--serve", action="store_true",
                        help="run headless and print the address")
    parser.add_argument("--shortcut", action="store_true",
                        help="write the desktop shortcut and exit")
    parser.add_argument("--host", default=None,
                        help="the address to bind. Loopback when not given. "
                             "`auto` picks this machine's tailnet address, or "
                             "its private LAN address if there is no tailnet. "
                             "Anything but loopback needs JOBDESK_ACCESS_TOKEN "
                             "in .env, and the server refuses to start without "
                             "it.")
    args = parser.parse_args(argv)

    if args.shortcut:
        try:
            print(f"shortcut written to {shortcut.create()}")
        except shortcut.ShortcutError as exc:
            print(f"the shortcut could not be written: {exc}")
            return 1
        return 0

    if args.browser or args.serve:
        try:
            server.serve(port=args.port, open_browser=args.browser,
                         host=args.host)
        except (access.Unconfigured, net.NoAddress) as exc:
            # Both carry the fix in the message, and both are a decision the
            # person at the keyboard has to make. Printing and stopping beats
            # a traceback, and beats starting on loopback and letting them
            # find out from the phone.
            print()
            print(exc)
            print()
            return 2
        return 0
    return desktop.launch(port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
