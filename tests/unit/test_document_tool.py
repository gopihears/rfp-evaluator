from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

from core.tools.document_tool import DocumentError, extract
from tests.pdf_helpers import blank_page, body, make_pdf


def err(data: bytes, **kw: int) -> str:
    with pytest.raises(DocumentError) as e:
        extract(data, **kw)
    return e.value.code


def test_d01_text_pdf_has_page_markers() -> None:
    doc = extract(make_pdf([body("Alpha"), body("Beta")]))
    assert doc.pages == 2
    assert "[p.1]" in doc.text and "[p.2]" in doc.text
    assert "Alpha line 0" in doc.text and doc.text.index("[p.2]") < doc.text.index("Beta line 0")


def test_d02_not_a_pdf() -> None:
    assert err(b"MZ\x90\x00 definitely an exe") == "E_NOT_PDF"
    assert err(b"%PDF-1.7 garbage that is not a real file") == "E_NOT_PDF"
    assert err(b"") == "E_NOT_PDF"


def test_too_large() -> None:
    assert err(b"%PDF" + b"0" * 2000, max_bytes=1000) == "E_TOO_LARGE"


def test_d03_encrypted() -> None:
    assert err(make_pdf([body("Secret")], encrypt_pw="pw")) == "E_ENCRYPTED"


def test_d04_image_only_pdf_is_rejected() -> None:
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 400, 400))
    data = doc.tobytes()
    assert err(data) == "E_NO_TEXT"


def test_d05_one_blank_page_warns() -> None:
    doc = extract(make_pdf([body("A"), body("B"), blank_page(), body("D"), body("E")]))
    assert [w.code for w in doc.warnings] == ["W_EMPTY_PAGE"]
    assert "Page 3" in doc.warnings[0].message


def test_mostly_blank_is_rejected() -> None:
    assert err(make_pdf([body("A"), blank_page(), blank_page(), blank_page()])) == "E_NO_TEXT"


def test_d06_too_many_pages() -> None:
    data = make_pdf([body(str(i)) for i in range(4)])
    assert err(data, max_pages=3) == "E_TOO_MANY_PAGES"
    assert extract(data, max_pages=4).pages == 4


def test_d07_repeated_header_and_footer_removed() -> None:
    pages = []
    for i in range(5):
        lines = [("ACME CONFIDENTIAL DRAFT", 8.0, "helv", 30.0)] + body(f"Unique{i}")
        lines.append((f"Page {i + 1} of 5", 8.0, "helv", 810.0))
        pages.append(lines)
    doc = extract(make_pdf(pages))
    assert "ACME CONFIDENTIAL" not in doc.text and "of 5" not in doc.text
    assert "Unique3 line 2" in doc.text


def test_repeated_body_lines_are_not_mistaken_for_headers() -> None:
    pages = [body(str(i)) for i in range(6)]  # same text apart from digits, in the body area
    doc = extract(make_pdf(pages))
    assert doc.text.count("describes the proposed approach") == 36


def test_d08_hyphenated_line_breaks_are_joined() -> None:
    lines = [("The platform is highly scal-", 11, "helv"),
             ("able and secure by design for every tenant.", 11, "helv")] + body("Pad")
    doc = extract(make_pdf([lines]))
    assert "scalable and secure" in doc.text


def test_d09_unicode_preserved() -> None:
    lines = [("Café naïve résumé coöperate – done", 11, "helv")] + body("Pad")
    assert "Café naïve résumé" in extract(make_pdf([lines])).text


def test_d10_token_estimate() -> None:
    doc = extract(make_pdf([body("Alpha")]))
    assert doc.est_tokens == math.ceil(len(doc.text) / 4) and doc.chars == len(doc.text)


def test_table_cells_on_one_row_are_joined() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(6):
        y = 60 + i * 18
        page.insert_text((50, y), f"Item {i} with a longer description of the work", fontsize=11)
        page.insert_text((400, y), f"{(i + 1) * 1000:,}", fontsize=11)
    extracted = extract(doc.tobytes())
    assert "Item 2 with a longer description of the work | 3,000" in extracted.text


def test_d12_outline_used_when_no_headings_detected() -> None:
    data = make_pdf([body("One"), body("Two"), body("Three")],
                    toc=[[1, "Alpha", 1], [1, "Beta", 2], [1, "Gamma", 3]])
    doc = extract(data)
    assert [(s.title, s.start_page, s.end_page) for s in doc.sections] == [
        ("Alpha", 1, 1), ("Beta", 2, 2), ("Gamma", 3, 3)]


def test_d13_numbered_bold_headings_detected_at_body_size() -> None:
    page = ([("1. Overview", 11, "hebo")] + body("Intro", 4)
            + [("2. Delivery Plan", 11, "hebo")] + body("Plan", 4))
    doc = extract(make_pdf([page]))
    assert [s.title for s in doc.sections] == ["1. Overview", "2. Delivery Plan"]
    assert "Plan line 1" in doc.sections[1].text and "Intro" not in doc.sections[1].text


def test_larger_font_headings_detected() -> None:
    page = ([("Scope of Work", 16, "helv")] + body("Scope", 5)
            + [("Pricing Summary", 16, "helv")] + body("Price", 5))
    assert [s.title for s in extract(make_pdf([page])).sections] == ["Scope of Work",
                                                                    "Pricing Summary"]


def test_d14_appendix_sections_flagged() -> None:
    page = ([("1. Main Body", 11, "hebo")] + body("Main", 4)
            + [("Appendix A - Certificates", 11, "hebo")] + body("Cert", 4))
    sections = extract(make_pdf([page])).sections
    assert [s.is_appendix for s in sections] == [False, True]


def test_fixed_windows_when_no_structure() -> None:
    data = make_pdf([body(f"P{i}") for i in range(7)])
    titles = [s.title for s in extract(data).sections]
    assert titles == ["Pages 1-5", "Pages 6-7"]


# ---- against the generated synthetic documents

@pytest.mark.parametrize("name", ["apex_systems", "brightpath_tech", "nexaworks", "orbit_digital"])
def test_brief_documents_extract_with_required_sections(synthetic_dir: Path, name: str) -> None:
    doc = extract((synthetic_dir / f"{name}.pdf").read_bytes())
    assert 2 <= doc.pages <= 4
    assert len(doc.sections) >= 6  # section names differ per supplier, but all are structured
    lowered = doc.text.lower()
    for topic in ("security", "support", "week", "total"):
        assert topic in lowered
    assert "usd" in lowered


def test_d11_realistic_20_page_document(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "realistic_nexaworks_20p.pdf").read_bytes())
    assert 15 <= doc.pages <= 30 and 12_000 <= doc.est_tokens <= 25_000
    assert len(doc.sections) >= 7
    assert any(s.is_appendix for s in doc.sections)


def test_long_document_exceeds_full_context_threshold(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "long_apex_80p.pdf").read_bytes())
    assert doc.pages >= 70 and doc.est_tokens > 60_000
    assert "NEEDLE-START" in doc.text and "NEEDLE-END" in doc.text


def test_corrupt_supplier_is_rejected(synthetic_dir: Path) -> None:
    assert err((synthetic_dir / "corrupt_co.pdf").read_bytes()) == "E_NO_TEXT"


def test_injection_probe_hidden_text_is_extracted(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "injection_probe.pdf").read_bytes())
    assert "ignore all previous instructions" in doc.text.lower()
