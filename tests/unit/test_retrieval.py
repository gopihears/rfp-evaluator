from __future__ import annotations

from pathlib import Path

import pytest

from core.models import Criterion
from core.tools.document_tool import ExtractedDoc, Section, extract
from core.tools.fake_provider import FakeProvider
from core.tools.retrieval import (
    Index,
    chunk_document,
    criterion_query,
    retrieve_for_criterion,
    tokenize,
)

SECURITY = Criterion(
    criterion_id=4,
    name="Security & Compliance",
    description="Controls, certifications, privacy, auditability",
    weight=20,
    rubric={
        2: "No certifications or controls described.",
        8: "Named certifications (such as SOC 2 or ISO 27001), controls, privacy handling and "
        "audit rights.",
    },
)


def doc_from(pages: list[list[str]], sections: list[Section] | None = None) -> ExtractedDoc:
    text = "\n\n".join(f"[p.{i + 1}]\n" + "\n".join(lines) for i, lines in enumerate(pages))
    return ExtractedDoc(
        text=text,
        pages=len(pages),
        chars=len(text),
        est_tokens=len(text) // 4,
        sections=sections or [],
    )


# ---------------------------------------------------------------------------- tokenising


def test_tokenize_drops_stopwords_and_stems() -> None:
    assert tokenize("The auditability of certifications, and controls") == [
        "audit",
        "certific",
        "control",
    ]
    assert tokenize("Security & Compliance!") == ["security", "compliance"]
    assert tokenize("a an the of") == []


# ------------------------------------------------------------------------------ chunking


def test_chunks_cover_all_lines_and_keep_page_markers() -> None:
    pages = [
        [f"Line {p}-{i} about vendor onboarding workflows and approval steps." for i in range(30)]
        for p in range(1, 5)
    ]
    chunks = chunk_document(doc_from(pages), target_tokens=150)
    assert len(chunks) > 4
    joined = "\n".join(c.text for c in chunks)
    for p in range(1, 5):
        for i in range(30):
            assert f"Line {p}-{i} " in joined
    for c in chunks:
        assert f"[p.{c.start_page}]" in c.text and c.start_page <= c.end_page
        assert c.est_tokens <= 150 * 2 + 40  # hard cap is twice the target (tables) plus header


def test_ut_g01_tables_are_never_split() -> None:
    table = [f"Item {i} | Basis {i} | {i * 1000:,}" for i in range(12)]
    lines = (
        [f"Intro sentence number {i} for the pricing section." for i in range(14)]
        + table
        + ["Closing remarks after the table."]
    )
    chunks = chunk_document(doc_from([lines]), target_tokens=120)
    holders = [c for c in chunks if "Item 0 |" in c.text]
    assert len(holders) >= 1
    assert all(f"Item {i} |" in holders[0].text for i in range(12))


def test_chunks_carry_section_titles() -> None:
    sections = [Section("Overview", 1, 1, ""), Section("Security", 2, 2, "")]
    pages = [["Overview text. " * 5], ["Security text. " * 5]]
    chunks = chunk_document(doc_from(pages, sections), target_tokens=10)
    assert chunks[0].section == "Overview" and "[Section: Overview]" in chunks[0].text
    assert chunks[-1].section == "Security"


def test_empty_document_has_no_chunks() -> None:
    assert chunk_document(doc_from([])) == []


# -------------------------------------------------------------------------------- search


def small_index(embed: bool = False) -> Index:
    lines = {
        1: "We hold SOC 2 Type II certification and ISO 27001 with an annual independent audit.",
        2: "Delivery plan with milestones, weekly governance and a named programme manager.",
        3: "Pricing table lists licence, implementation and support costs with assumptions.",
        4: "The platform architecture uses microservices, queues and horizontal scaling.",
    }
    chunks = chunk_document(doc_from([[v] for v in lines.values()]), target_tokens=20)
    fake = FakeProvider()
    return Index.build(chunks, fake.embed if embed else None)


def test_bm25_ranks_the_relevant_chunk_first() -> None:
    index = small_index()
    hits, weak = index.search("certifications audit security controls", 2)
    assert "SOC 2" in hits[0].text and not weak
    hits, _ = index.search("milestones governance programme manager", 1)
    assert "milestones" in hits[0].text


def test_weak_when_nothing_matches() -> None:
    hits, weak = small_index().search("zebra giraffe", 3)
    assert hits == [] and weak


def test_hybrid_search_uses_embeddings_deterministically() -> None:
    a, b = small_index(embed=True), small_index(embed=True)
    assert a.has_embeddings and not small_index().has_embeddings
    ha, _ = a.search("certifications audit", 3)
    hb, _ = b.search("certifications audit", 3)
    assert [c.id for c in ha] == [c.id for c in hb] and "SOC 2" in ha[0].text


def test_ut_g04_g05_retrieval_adds_opening_and_pricing_chunks_in_page_order() -> None:
    index = small_index()
    crit = Criterion(
        criterion_id=1, name="Technical", description="architecture microservices", weight=10
    )
    ctx = retrieve_for_criterion(index, crit, top_k=1)
    assert ctx.pages == sorted(ctx.pages)
    assert 1 in ctx.pages and 3 in ctx.pages and 4 in ctx.pages  # opening, pricing, and the hit
    assert ctx.text.index("[p.1]") < ctx.text.index("[p.3]") < ctx.text.index("[p.4]")


def test_ut_g06_weak_retrieval_is_flagged_not_silent() -> None:
    crit = Criterion(criterion_id=9, name="Zebra giraffe", weight=5)
    ctx = retrieve_for_criterion(small_index(), crit, top_k=3)
    assert ctx.weak and ctx.text  # still returns the opening and pricing passages


def test_query_includes_name_description_and_rubric() -> None:
    q = criterion_query(SECURITY)
    for word in ("Security", "auditability", "certifications", "audit rights"):
        assert word in q


# ------------------------------------------------------ planted needles in the 80-page proposal


@pytest.fixture(scope="module")
def long_index(synthetic_dir: Path) -> tuple[ExtractedDoc, Index]:
    doc = extract((synthetic_dir / "long_apex_80p.pdf").read_bytes())
    return doc, Index.build(chunk_document(doc), FakeProvider().embed)


def retrieved_text(index: Index, crit: Criterion) -> str:
    return retrieve_for_criterion(index, crit, top_k=8).text


def test_ut_g07_needle_end_found_for_the_security_criterion(
    long_index: tuple[ExtractedDoc, Index],
) -> None:
    doc, index = long_index
    assert len(index.chunks) > 100  # a real haystack
    assert "NEEDLE-END" in retrieved_text(index, SECURITY)


def test_needle_start_and_middle_found_for_matching_criteria(
    long_index: tuple[ExtractedDoc, Index],
) -> None:
    _, index = long_index
    soc = Criterion(
        criterion_id=4,
        name="Security operations centre",
        description="24x7 security operations centre staffed by certified analysts",
        weight=10,
    )
    dr = Criterion(
        criterion_id=5,
        name="Disaster recovery",
        description="recovery time objective and recovery point objective of the "
        "disaster recovery plan",
        weight=10,
    )
    assert "NEEDLE-START" in retrieved_text(index, soc)
    assert "NEEDLE-MIDDLE" in retrieved_text(index, dr)


def test_retrieval_sends_a_small_fraction_of_a_long_document(
    long_index: tuple[ExtractedDoc, Index],
) -> None:
    doc, index = long_index
    ctx = retrieve_for_criterion(index, SECURITY, top_k=8)
    assert len(ctx.text) < len(doc.text) * 0.2
    assert doc.est_tokens > 60_000
