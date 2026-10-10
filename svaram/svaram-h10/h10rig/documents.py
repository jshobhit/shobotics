"""Attached session documents and ID auto-fill. Pure Python, no Qt.

`load_document` reads a file once, hashes it and keeps its bytes, so the session
package contains exactly what was checked at attach time even if the original
file later moves or changes.

`extract_ids` looks for participant (p), session (s) and facilitator (f) IDs:
  * CSV / TXT: a header row plus the first data row, or rows that hold a
    key cell followed by its value (e.g. "participant_id,PT-025", or the
    survey export layout "Section,Field,Question,Answer").
  * JSON: every key at any depth.
  * PDF: raw bytes plus inflated FlateDecode streams, with split TJ text
    fragments joined, then the ID patterns.
  * Always: the file name, with underscores and dots read as spaces.
Field and pattern rules live in config.py.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import re
import zlib

from .config import ID_FIELDS, ID_PATTERNS, ID_WORD

MAX_PDF_STREAMS = 300  # stop scanning very large PDFs after this many streams
TJ_SPLIT = re.compile(rb"\)\s*-?[\d.]*\s*\(")  # "(PT-)-20(025)" -> "(PT-025)"


@dataclass
class Document:
    name: str
    size: int
    type: str
    sha256: str
    added_at: str  # ISO 8601, UTC
    data: bytes = field(repr=False)

    def meta(self) -> dict:
        """Metadata written to session.json (no file contents)."""
        return {
            "name": self.name,
            "size_bytes": self.size,
            "type": self.type,
            "sha256": self.sha256,
            "attached_at": self.added_at,
        }


def load_document(path: str | Path) -> Document:
    path = Path(path)
    data = path.read_bytes()
    return Document(
        name=path.name,
        size=len(data),
        type=mimetypes.guess_type(path.name)[0] or "",
        sha256=hashlib.sha256(data).hexdigest(),
        added_at=datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        data=data,
    )


# --------------------------------------------------------------------------
# ID extraction
# --------------------------------------------------------------------------


def extract_ids(name: str, data: bytes) -> dict[str, str]:
    """Return the IDs found in one document, e.g. {"p": "PT-025", "s": ...}."""
    found: dict[str, str] = {}

    def scan(text: str) -> None:
        for k, pattern in ID_PATTERNS.items():
            if k not in found and (m := pattern.search(text)):
                found[k] = m.group(0).upper()

    def from_field(key: str, value) -> None:
        value = "" if value is None else str(value).strip().strip('"')
        if not value or not ID_WORD.search(key):
            return
        for k, rule in ID_FIELDS.items():
            if k not in found and rule.search(key):
                found[k] = value

    lower = name.lower()
    try:
        if lower.endswith(".json"):
            _walk(json.loads(data.decode("utf-8-sig")), from_field)
        elif lower.endswith((".csv", ".txt")):
            text = data.decode("utf-8-sig", errors="replace")
            rows = [r for r in csv.reader(io.StringIO(text)) if any(r)]
            if len(rows) >= 2:  # header row + first data row
                for key, value in zip(rows[0], rows[1]):
                    from_field(key, value)
            for row in rows:  # key cell followed by its value
                _from_row(row, from_field)
            scan(text)
        elif lower.endswith(".pdf"):
            scan(pdf_text(data))
    except (ValueError, csv.Error, UnicodeDecodeError):
        pass  # unreadable content: fall back to the file name
    scan(re.sub(r"[_.]+", " ", name))
    return found


def _walk(node, from_field) -> None:
    """Visit every key/value pair in nested JSON."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                _walk(v, from_field)
            else:
                from_field(str(k), v)
    elif isinstance(node, list):
        for v in node:
            _walk(v, from_field)


def _from_row(row: list[str], from_field) -> None:
    """Treat the first ID-like cell as a key and the row's last cell as its value.

    For a two-column "key,value" row this is the plain pair. For the survey
    export ("About you,participant_id,Participant code,002") the value is the
    answer in the last column. Rows whose last cell is itself a key name are
    header rows and are skipped.
    """
    keys = [_is_key(cell) for cell in row]
    if len(row) < 2 or keys[-1]:  # a header row, not a key/value row
        return
    for cell, is_key in zip(row[:-1], keys):
        if is_key:
            from_field(cell, row[-1])
            return


def _is_key(cell: str) -> bool:
    return bool(ID_WORD.search(cell)) and any(
        r.search(cell) for r in ID_FIELDS.values()
    )


def pdf_text(data: bytes) -> str:
    """Raw PDF text plus every inflated FlateDecode content stream."""
    parts = [data]
    i = count = 0
    while (i := data.find(b"stream", i)) != -1 and count < MAX_PDF_STREAMS:
        count += 1
        start = i + 6
        if data[start : start + 1] == b"\r":
            start += 1
        if data[start : start + 1] == b"\n":
            start += 1
        end = data.find(b"endstream", start)
        if end < 0:
            break
        stop = end
        while stop > start and data[stop - 1 : stop] in (b"\r", b"\n"):
            stop -= 1
        if b"FlateDecode" in data[max(0, i - 200) : i]:
            try:
                # decompressobj tolerates a truncated or padded stream.
                parts.append(zlib.decompressobj().decompress(data[start:stop]))
            except zlib.error:
                pass
        i = end + 9
    return TJ_SPLIT.sub(b"", b"\n".join(parts)).decode("latin-1")
