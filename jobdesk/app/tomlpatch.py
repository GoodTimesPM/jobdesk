"""Change values in a TOML file without losing the comments around them.

`tomllib` reads TOML and cannot write it, and the obvious fix -- parse to a
dict, re-serialise -- throws away every comment in the file. That is not
acceptable here. The comments in `targeting.toml` and `letter.toml` are the
documentation: they explain what `equivalency_ceiling` means, why short
aliases are dangerous, and what broke the last time someone wrote "one" as a
pronoun. A wizard that silently deletes all of that the first time you move a
salary slider has made the file worse, and the file is the product.

So this edits text. It finds a top-level `key = value` and replaces the value
in place, leaving everything else in the file byte-for-byte identical --
including the comment above it, the blank line after it, and the file's
existing line endings.

Deliberately narrow. It handles top-level scalars and arrays, which is every
field the setup wizard writes. It does not handle `[[table]]` members, and it
raises rather than guessing if a key is missing or ambiguous, because the
failure mode of a silent no-op here is a wizard that says "saved" and did not.
"""

from __future__ import annotations

import re


class PatchError(RuntimeError):
    """A key could not be found or safely replaced, and why."""


def dump_value(value) -> str:
    """One Python value as the TOML literal for it.

    Strings are written with single quotes when they hold a backslash, because
    a Windows path in a double-quoted TOML string needs every separator
    doubled and a user reading `'D:\\Job Search'` back should see the path
    they typed.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if "\\" in value and "'" not in value and "\n" not in value:
            return f"'{value}'"
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [dump_value(v) for v in value]
        # Short lists stay on one line; long ones wrap, so a hand-editor is
        # not scrolling sideways through forty job titles.
        single = "[" + ", ".join(items) + "]"
        if len(single) <= 72:
            return single
        lines, row = [], "  "
        for item in items:
            if len(row) + len(item) + 2 > 74:
                lines.append(row.rstrip())
                row = "  "
            row += item + ", "
        lines.append(row.rstrip().rstrip(","))
        return "[\n" + "\n".join(lines) + "\n]"
    raise PatchError(f"no TOML form for {type(value).__name__}")


def _value_end(lines: list[str], start: int) -> int:
    """The index of the last line of the value beginning on `start`.

    Counts brackets rather than looking for a closing line, so a list of lists
    and a list with a `]` inside a string both end where they actually end.
    """
    depth = 0
    for i in range(start, len(lines)):
        # Strip comments and string contents before counting, so a `#` inside
        # a value and a bracket inside a quoted string are both ignored.
        text = re.sub(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'', "", lines[i])
        text = text.split("#", 1)[0]
        depth += text.count("[") + text.count("{")
        depth -= text.count("]") + text.count("}")
        if depth <= 0:
            return i
    raise PatchError("a value opens a bracket that is never closed")


def _span(lines: list[str], section: str | None) -> tuple[int, int]:
    """The half-open line range one table occupies.

    With no section that is the top-level table, which ends at the first table
    header -- so `points` inside a `[[function_families]]` block can never be
    mistaken for a top-level setting. With a section it is that header's line
    to the next header.
    """
    if section is None:
        for i, line in enumerate(lines):
            if re.match(r"\s*\[", line):
                return 0, i
        return 0, len(lines)

    opener = re.compile(r"\s*\[\s*" + re.escape(section) + r"\s*\]")
    for i, line in enumerate(lines):
        if opener.match(line):
            for j in range(i + 1, len(lines)):
                if re.match(r"\s*\[", lines[j]):
                    return i + 1, j
            return i + 1, len(lines)
    raise PatchError(f"[{section}] is not a table in this file")


def patch(text: str, changes: dict[str, object],
          section: str | None = None) -> str:
    """Return `text` with each key in `changes` set to its new value.

    `section` names a table to look in, e.g. "identity" for master.toml's
    contact block. Without it only the top-level table is searched.
    """
    if not changes:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    floor, limit = _span(lines, section)

    for key, value in changes.items():
        pattern = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=")
        hits = [i for i in range(floor, limit) if pattern.match(lines[i])]
        if not hits:
            where = f"[{section}]" if section else "the top-level table"
            raise PatchError(f"{key} is not a key in {where}")
        if len(hits) > 1:
            raise PatchError(f"{key} appears {len(hits)} times; refusing to guess")
        start = hits[0]
        end = _value_end(lines, start)
        indent = pattern.match(lines[start]).group(1)
        # Keep a trailing comment that was sitting on the same line as the
        # value: it usually says what the number means.
        tail = ""
        if end == start:
            after = lines[start].split("=", 1)[1]
            if "#" in after and not re.search(r'"[^"]*#', after):
                tail = "  " + after[after.index("#"):].strip()
        rendered = dump_value(value)
        block = [f"{indent}{key} = " + rendered.split("\n")[0]]
        block += rendered.split("\n")[1:]
        block[-1] += tail
        lines[start:end + 1] = block

    return newline.join(lines)
