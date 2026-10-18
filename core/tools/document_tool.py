"""Document tool: PDF bytes -> clean, page-tagged text plus detected sections.

Deterministic. Rejections raise DocumentError with a stable error code; softer problems
are returned as warnings on the ExtractedDoc.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from core.models import RunWarning

DEFAULT_MAX_PAGES = 150
DEFAULT_MAX_BYTES = 25_000_000
MIN_TEXT_CHARS = 200
EMPTY_PAGE_CHARS = 20
REPEAT_LINE_SHARE = 0.6
MARGIN_BAND = 0.1  # top/bottom 10% of a page is the header/footer zone
HEADING_SIZE_RATIO = 1.15
WINDOW_PAGES = 5

_NUMBERED = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+\S")
_APPENDIX = re.compile(r"^(?:appendix|annex|attachment|schedule)\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")


class DocumentError(Exception):
    """A document that cannot be used. `code` is a stable error code (E_*)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Section:
    title: str
    start_page: int
    end_page: int
    text: str
    is_appendix: bool = False


@dataclass
class ExtractedDoc:
    text: str
    pages: int
    chars: int
    est_tokens: int
    sections: list[Section] = field(default_factory=list)
    warnings: list[RunWarning] = field(default_factory=list)


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    y0: float
    x0: float
    page: int
    single: bool = True  # False for table rows merged from several cells
    margin: bool = False  # inside the top/bottom page margin (header/footer zone)


# ------------------------------------------------------------------ extraction


def _page_lines(page: pymupdf.Page, page_no: int) -> list[_Line]:
    data = page.get_text("dict", sort=True)
    height = float(page.rect.height)
    raw: list[_Line] = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            raw.append(_Line(
                text=text,
                size=max(float(s["size"]) for s in spans),
                bold=any((int(s["flags"]) & 16) or "bold" in str(s["font"]).lower()
                         for s in spans),
                y0=float(line["bbox"][1]),
                x0=float(line["bbox"][0]),
                page=page_no,
                margin=not MARGIN_BAND * height <= float(line["bbox"][1])
                <= (1 - MARGIN_BAND) * height,
            ))
    return _merge_rows(raw)


def _merge_rows(lines: list[_Line]) -> list[_Line]:
    """Join text fragments on the same visual row (table cells) as 'a | b | c'."""
    rows: list[list[_Line]] = []
    for ln in sorted(lines, key=lambda x: (x.y0, x.x0)):
        if rows and abs(ln.y0 - rows[-1][0].y0) <= 2.5:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    merged: list[_Line] = []
    for row in rows:
        row.sort(key=lambda x: x.x0)
        if len(row) == 1:
            merged.append(row[0])
        else:
            merged.append(_Line(
                text=" | ".join(r.text for r in row),
                size=max(r.size for r in row),
                bold=all(r.bold for r in row),
                y0=row[0].y0,
                x0=row[0].x0,
                page=row[0].page,
                single=False,
                margin=row[0].margin,
            ))
    return merged


def _strip_repeated(pages: list[list[_Line]]) -> list[list[_Line]]:
    """Drop headers/footers: short margin-zone lines present on most pages (digits ignored)."""
    if len(pages) < 3:
        return pages

    def key(ln: _Line) -> str:
        return _DIGITS.sub("#", ln.text.lower())

    def eligible(ln: _Line) -> bool:
        return ln.margin and len(ln.text) <= 100

    seen: Counter[str] = Counter()
    for lines in pages:
        seen.update({key(ln) for ln in lines if eligible(ln)})
    limit = REPEAT_LINE_SHARE * len(pages)
    return [[ln for ln in lines if not (eligible(ln) and seen[key(ln)] > limit)]
            for lines in pages]


def _dehyphenate(lines: list[_Line]) -> list[_Line]:
    out: list[_Line] = []
    skip = False
    for i, ln in enumerate(lines):
        if skip:
            skip = False
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if (nxt is not None and ln.single and nxt.single and ln.text.endswith("-")
                and len(ln.text) > 1 and ln.text[-2].isalpha() and nxt.text[:1].islower()):
            out.append(_Line(ln.text[:-1] + nxt.text, ln.size, ln.bold, ln.y0, ln.x0,
                             ln.page, True))
            skip = True
        else:
            out.append(ln)
    return out


# -------------------------------------------------------------------- sections


def _body_size(lines: list[_Line]) -> float:
    weight: Counter[float] = Counter()
    for ln in lines:
        weight[round(ln.size, 1)] += len(ln.text)
    return weight.most_common(1)[0][0] if weight else 0.0


def _is_heading(ln: _Line, body: float) -> bool:
    text = ln.text
    if not ln.single or len(text) > 90 or not any(ch.isalpha() for ch in text):
        return False
    big = body > 0 and ln.size >= body * HEADING_SIZE_RATIO
    if big:
        return True
    return ln.bold and bool(_NUMBERED.match(text) or _APPENDIX.match(text))


def _sections_from_headings(lines: list[_Line], body: float) -> list[Section]:
    sections: list[Section] = []
    title, start = "Preamble", lines[0].page if lines else 1
    buf: list[str] = []
    last_page = start

    def close() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(
                Section(title, start, max(start, last_page), text, bool(_APPENDIX.match(title)))
            )

    for ln in lines:
        if _is_heading(ln, body):
            close()
            title, start, buf = ln.text, ln.page, []
        else:
            buf.append(ln.text)
        last_page = ln.page
    close()
    return sections


def _sections_from_outline(
    toc: list[list[object]], page_texts: list[str]
) -> list[Section]:
    entries = [(str(t[1]).strip(), int(str(t[2]))) for t in toc if int(str(t[0])) == 1]
    out: list[Section] = []
    for i, (title, start) in enumerate(entries):
        nxt = entries[i + 1][1] if i + 1 < len(entries) else len(page_texts) + 1
        end = min(max(start, nxt - 1), len(page_texts))
        start = min(max(start, 1), len(page_texts))
        out.append(Section(title, start, end, "\n".join(page_texts[start - 1 : end]).strip(),
                           bool(_APPENDIX.match(title))))
    return out


def _sections_from_windows(page_texts: list[str]) -> list[Section]:
    out = []
    for start in range(0, len(page_texts), WINDOW_PAGES):
        end = min(start + WINDOW_PAGES, len(page_texts))
        out.append(Section(f"Pages {start + 1}-{end}", start + 1, end,
                           "\n".join(page_texts[start:end]).strip()))
    return out


# ------------------------------------------------------------------------ main


def extract(
    pdf_bytes: bytes,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ExtractedDoc:
    """Extract page-tagged text and sections. Raises DocumentError for unusable files."""
    if len(pdf_bytes) > max_bytes:
        raise DocumentError("E_TOO_LARGE", f"File exceeds the {max_bytes // 1_000_000} MB limit.")
    if b"%PDF" not in pdf_bytes[:1024]:
        raise DocumentError("E_NOT_PDF", "This file is not a PDF.")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentError(
            "E_NOT_PDF", "This PDF could not be opened (it may be damaged)."
        ) from exc
    with doc:
        if doc.needs_pass:
            raise DocumentError("E_ENCRYPTED", "This PDF is password protected.")
        if doc.page_count > max_pages:
            raise DocumentError(
                "E_TOO_MANY_PAGES",
                f"This PDF has {doc.page_count} pages; the limit is {max_pages}.",
            )
        pages = [_page_lines(doc[i], i + 1) for i in range(doc.page_count)]
        toc = doc.get_toc(simple=True)

    pages = [_dehyphenate(lines) for lines in _strip_repeated(pages)]
    page_texts = ["\n".join(ln.text for ln in lines).strip() for lines in pages]
    text_chars = sum(len(t) for t in page_texts)
    empty = [i + 1 for i, t in enumerate(page_texts) if len(t) < EMPTY_PAGE_CHARS]

    if text_chars < MIN_TEXT_CHARS or len(empty) / max(len(pages), 1) > 0.5:
        raise DocumentError(
            "E_NO_TEXT",
            "Almost no readable text was found; the PDF is probably scanned images. "
            "Please upload a text-based PDF.",
        )

    warnings = [
        RunWarning.make("W_EMPTY_PAGE", f"Page {n} has no extractable text.") for n in empty
    ]
    full_text = "\n\n".join(f"[p.{i + 1}]\n{t}" for i, t in enumerate(page_texts))

    all_lines = [ln for lines in pages for ln in lines]
    body = _body_size(all_lines)
    sections = _sections_from_headings(all_lines, body)
    if len(sections) <= 1 and toc:
        sections = _sections_from_outline(toc, page_texts) or sections
    if len(sections) <= 1 and len(pages) > WINDOW_PAGES:
        sections = _sections_from_windows(page_texts)

    return ExtractedDoc(
        text=full_text,
        pages=len(pages),
        chars=len(full_text),
        est_tokens=math.ceil(len(full_text) / 4),
        sections=sections,
        warnings=warnings,
    )
