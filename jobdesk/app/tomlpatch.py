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

Deliberately narrow. `patch` handles top-level and `[section]` scalars and
arrays, which is every field the setup wizard writes. `patch_entry` edits one
`[[table]]` entry picked by its id, for the Criteria tab's "About you" cards.
Both raise rather than guess if a key is missing or ambiguous, because the
failure mode of a silent no-op here is a page that says "saved" and did not.
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

# A bare or quoted key at the start of a `key = value` line inside a table.
_TABLE_KEY = re.compile(
    r"""^(\s*)("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*=\s*(.*)$""")


def patch_table(text: str, section: str, mapping: dict[str, object]) -> str:
    """Make the flat table `[section]` hold exactly `mapping`, keeping comments.

    For `[core_skills]` and `[supporting_skills]`, where the keys are the data
    ("sql" = 6) rather than fixed setting names. `patch` cannot do this: it
    refuses a key that is not already in the file, and here adding and
    removing keys is the whole point.

    Existing keys are updated where they sit. Keys no longer in `mapping` are
    removed. New keys go after the last existing one, so a comment above the
    table or between two of its lines stays where it was. Every value has to
    fit on one line, which is true of any weight table; a multi-line value
    raises rather than being half-rewritten.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    floor, limit = _span(lines, section)

    remaining = dict(mapping)
    keep: list[str] = []
    last_key = -1
    indent = ""
    for line in lines[floor:limit]:
        hit = _TABLE_KEY.match(line)
        if not hit or line.lstrip().startswith("#"):
            keep.append(line)
            continue
        indent, raw_key, rest = hit.group(1), hit.group(2), hit.group(3)
        if rest.count('"""') == 1 or rest.count("'''") == 1:
            raise PatchError(f"[{section}] has a multi-line value; edit it by hand")
        _value_end([line], 0)            # raises on a bracket left open
        key = raw_key[1:-1] if raw_key[0] in "\"'" else raw_key
        if key not in remaining:
            continue
        keep.append(f"{indent}{dump_value(key)} = {dump_value(remaining.pop(key))}")
        last_key = len(keep) - 1

    added = [f"{indent}{dump_value(k)} = {dump_value(v)}"
             for k, v in remaining.items()]
    if last_key < 0:
        # No surviving key to follow, so the new ones open the table.
        keep[0:0] = added
    else:
        keep[last_key + 1:last_key + 1] = added
    lines[floor:limit] = keep
    return newline.join(lines)


# -- [[table]] entries -------------------------------------------------------

def _entries(data: dict, header: str) -> list[dict]:
    """Every parsed entry an `[[a.b]]` header produces, in file order."""
    nodes: list = [data]
    for part in header.split("."):
        found: list = []
        for node in nodes:
            value = node.get(part) if isinstance(node, dict) else None
            if isinstance(value, list):
                found += value
            elif isinstance(value, dict):
                found.append(value)
        nodes = found
    return [n for n in nodes if isinstance(n, dict)]


def _key_end(lines: list[str], start: int) -> int:
    """The last line of the value on `start`, triple-quoted strings included."""
    rest = lines[start].split("=", 1)[1].lstrip()
    for quote in ('"""', "'''"):
        if rest.startswith(quote) and rest.count(quote) == 1:
            for i in range(start + 1, len(lines)):
                if quote in lines[i]:
                    return i
            raise PatchError("a multi-line string is never closed")
    if rest.startswith(('"""', "'''")):
        return start
    return _value_end(lines, start)


def _dump_long(value: str) -> str:
    """A string as a one-line `\"\"\"...\"\"\"` literal, for a key that used one."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"""{escaped}"""'


def patch_entry(text: str, header: str, ident: str, changes: dict[str, object],
                id_key: str = "id") -> str:
    """Return `text` with one `[[header]]` entry's keys set to new values.

    The entry is the one whose `id_key` equals `ident`, so `[[bullet]]` with
    id "spargo.ad" or the indented `[[bullet.variant]]` under it. Exactly one
    entry has to match. A key the entry lacks is added after its last key,
    which is how an optional field like a skill's `detail` gets filled in.

    A value that was written as a `\"\"\"` string stays one, because letter
    templates use that form so a sentence can hold a quotation. The result is
    parsed again before it is returned, and anything that does not read back
    as the requested values raises instead of being written.
    """
    import tomllib

    if not changes:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    opener = re.compile(r"\s*\[\[\s*" + re.escape(header) + r"\s*\]\]\s*(#.*)?$")

    blocks: list[tuple[int, int, list[int]]] = []
    i = 0
    while i < len(lines):
        if not opener.match(lines[i]):
            i += 1
            continue
        keys, j = [], i + 1
        while j < len(lines):
            line = lines[j]
            if re.match(r"\s*\[", line):
                break
            if _TABLE_KEY.match(line) and not line.lstrip().startswith("#"):
                keys.append(j)
                j = _key_end(lines, j)
            j += 1
        blocks.append((i, j, keys))
        i = j

    def ident_of(keys: list[int]) -> object:
        for k in keys:
            hit = _TABLE_KEY.match(lines[k])
            if hit.group(2).strip("\"'") == id_key:
                try:
                    return tomllib.loads(lines[k].strip()).get(id_key)
                except tomllib.TOMLDecodeError:
                    return None
        return None

    matches = [b for b in blocks if ident_of(b[2]) == ident]
    if not matches:
        raise PatchError(f"no [[{header}]] has {id_key} = {ident!r}")
    if len(matches) > 1:
        raise PatchError(f"{len(matches)} [[{header}]] entries have "
                         f"{id_key} = {ident!r}; refusing to guess")
    _, _, keys = matches[0]

    # Work from the bottom of the entry up, so replacing a three-line list
    # with a one-line one does not move the lines still to be edited.
    where = {}
    for k in keys:
        name = _TABLE_KEY.match(lines[k]).group(2).strip("\"'")
        where[name] = k
    edits = sorted(((where.get(key, -1), key, value)
                    for key, value in changes.items()), reverse=True)
    last = max(_key_end(lines, k) for k in keys) if keys else matches[0][0]
    indent = _TABLE_KEY.match(lines[keys[0]]).group(1) if keys else ""
    added: list[str] = []
    for start, key, value in edits:
        if start < 0:
            added.append(f"{indent}{key} = {dump_value(value)}")
            continue
        hit = _TABLE_KEY.match(lines[start])
        old = hit.group(3)
        if isinstance(value, str) and old.startswith(('"""', "'''")):
            rendered = _dump_long(value)
        else:
            rendered = dump_value(value)
        if isinstance(value, str) and "\n" in rendered:
            raise PatchError(f"{key} has a line break; one line only")
        end = _key_end(lines, start)
        # A list that sat on one line stays on one line, the way every
        # `tags = [...]` in master.toml is written, however long it is.
        if isinstance(value, (list, tuple)) and end == start:
            rendered = "[" + ", ".join(dump_value(v) for v in value) + "]"
        block = [f"{hit.group(1)}{hit.group(2)} = " + rendered.split("\n")[0]]
        block += rendered.split("\n")[1:]
        if end < last:
            last -= (end - start + 1) - len(block)
        elif end == last:
            last = start + len(block) - 1
        lines[start:end + 1] = block
    if added:
        lines[last + 1:last + 1] = list(reversed(added))

    result = newline.join(lines)
    try:
        parsed = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise PatchError(f"the edit would break the file: {exc}") from exc
    entry = [e for e in _entries(parsed, header) if e.get(id_key) == ident]
    if len(entry) != 1 or any(entry[0].get(k) != v for k, v in changes.items()):
        raise PatchError(f"[[{header}]] {ident!r} did not read back as edited")
    return result
