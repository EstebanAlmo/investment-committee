"""Minimal, deterministic .xlsx writer (standard library: zipfile + XML).

Enough for plain tables: one header row (bold, frozen) per sheet, then
strings, integers and Decimals. Numbers are written as numbers, text as
inline strings. The same input always produces the same bytes (fixed zip
timestamps, stable order), so regenerating a report changes nothing unless
the data changed. Excel is an OUTPUT only - the data live in SQLite.
"""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

_FIXED_TIME = (2026, 1, 1, 0, 0, 0)
_INVALID_SHEET_CHARS = set('[]:*?/\\')


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value: Any, style: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, Decimal, float)):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    text = escape(str(value)).replace("\n", "&#10;")
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
           "<sheetData>"]
    for r, row in enumerate([header, *rows], start=1):
        cells = "".join(_cell(f"{_column(c)}{r}", v, 1 if r == 1 else 0) for c, v in enumerate(row))
        out.append(f'<row r="{r}">{cells}</row>')
    out += ["</sheetData>", "</worksheet>"]
    return "".join(out)


def write_xlsx(path: Path, sheets: Sequence[tuple[str, Sequence[str], Sequence[Sequence[Any]]]]) -> None:
    """Write (name, header, rows) sheets; replaces the file atomically."""
    names = [name for name, _, _ in sheets]
    for name in names:
        if not name or len(name) > 31 or set(name) & _INVALID_SHEET_CHARS:
            raise ValueError(f"invalid sheet name: {name!r}")
    if len(set(names)) != len(names):
        raise ValueError("sheet names must be unique")
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, len(sheets) + 1))
            + "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, start=1))
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(sheets) + 1))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            "</styleSheet>"
        ),
    }
    for i, (_, header, rows) in enumerate(sheets, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _sheet(header, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    temporary.replace(path)
