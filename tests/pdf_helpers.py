"""Build small PDFs in memory for document-tool tests."""

from __future__ import annotations

import pymupdf

# text, font size, font name ("helv" or "hebo"), optionally an absolute y position
Line = tuple[str, float, str] | tuple[str, float, str, float]


def make_pdf(
    pages: list[list[Line]],
    *,
    toc: list[list[object]] | None = None,
    encrypt_pw: str | None = None,
) -> bytes:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 120.0
        for line in lines:
            text, size, font = line[0], line[1], line[2]
            if len(line) == 4:
                page.insert_text((50, float(line[3])), text, fontsize=size, fontname=font)
                continue
            page.insert_text((50, y), text, fontsize=size, fontname=font)
            y += size * 1.6
    if toc:
        doc.set_toc(toc)  # type: ignore[arg-type]
    if encrypt_pw:
        data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=encrypt_pw,
                           owner_pw=encrypt_pw + "-owner")
    else:
        data = doc.tobytes()
    doc.close()
    return data


def body(prefix: str, n: int = 6, size: float = 11) -> list[Line]:
    """n plain body lines, each ~60 characters, so a page holds well over 200 characters."""
    return [(f"{prefix} line {i} describes the proposed approach in some detail.", size, "helv")
            for i in range(n)]


def blank_page() -> list[Line]:
    return []
