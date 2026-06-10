from __future__ import annotations


_ESCAPED_PIPE_SENTINEL = "\x00"


def split_markdown_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    protected = stripped.replace("\\|", _ESCAPED_PIPE_SENTINEL)
    return [cell.strip().replace(_ESCAPED_PIPE_SENTINEL, "|") for cell in protected.strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return all(set(cell.replace(" ", "")) <= {"-", ":"} and "-" in cell for cell in cells)


def parse_markdown_tables(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    tables: list[dict[str, object]] = []
    i = 0
    while i < len(lines):
        header = split_markdown_row(lines[i])
        next_row = split_markdown_row(lines[i + 1]) if i + 1 < len(lines) else None
        if not header or not next_row or not is_separator_row(next_row):
            i += 1
            continue

        rows: list[dict[str, str]] = []
        mismatches: list[dict[str, object]] = []
        i += 2
        while i < len(lines):
            cells = split_markdown_row(lines[i])
            if not cells:
                break
            if len(cells) != len(header):
                # Keep parsing but remember the broken row; the caller decides
                # whether this table matters enough to raise (see parser._find_table).
                mismatches.append(
                    {
                        "line": i + 1,
                        "expected": len(header),
                        "got": len(cells),
                        "content": lines[i].strip(),
                    }
                )
                i += 1
                continue
            rows.append(dict(zip(header, cells)))
            i += 1
        tables.append({"header": header, "rows": rows, "mismatches": mismatches})
    return tables


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled Weft Project"
