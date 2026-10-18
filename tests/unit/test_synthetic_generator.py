from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import make_synthetic_pdfs


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_lists_every_file(synthetic_dir: Path) -> None:
    manifest = json.loads((synthetic_dir / "manifest.json").read_text(encoding="utf-8"))
    names = {f["file"] for f in manifest["files"]}
    assert names == {p.name for p in synthetic_dir.glob("*.pdf")}
    assert {"apex_systems.pdf", "brightpath_tech.pdf", "nexaworks.pdf",
            "orbit_digital.pdf"} <= names
    brief = [f for f in manifest["files"] if f["role"] == "brief"]
    assert len(brief) == 4 and all(2 <= f["pages"] <= 4 for f in brief)
    assert all(0 <= f["experience_rating"] <= 5 for f in manifest["files"])


def test_needles_planted_start_middle_end(synthetic_dir: Path) -> None:
    manifest = json.loads((synthetic_dir / "manifest.json").read_text(encoding="utf-8"))
    long_doc = next(f for f in manifest["files"] if f["file"] == "long_apex_80p.pdf")
    pages = long_doc["needle_pages"]
    assert pages["NEEDLE-START"] < pages["NEEDLE-MIDDLE"] < pages["NEEDLE-END"]
    assert pages["NEEDLE-START"] <= 2 and pages["NEEDLE-END"] >= long_doc["pages"] - 5
    assert 25 <= pages["NEEDLE-MIDDLE"] <= long_doc["pages"] - 25


def test_generation_is_reproducible(synthetic_dir: Path, tmp_path: Path) -> None:
    make_synthetic_pdfs.main(["x", str(tmp_path)])
    for name in ("apex_systems.pdf", "nexaworks.pdf", "corrupt_co.pdf"):
        assert digest(synthetic_dir / name) == digest(tmp_path / name)
    assert ((synthetic_dir / "manifest.json").read_text(encoding="utf-8")
            == (tmp_path / "manifest.json").read_text(encoding="utf-8"))


def test_price_totals_match_line_items(synthetic_dir: Path) -> None:
    from core.tools.document_tool import extract
    from scripts.synthetic_content import BASE_PROPOSALS

    for proposal in BASE_PROPOSALS:
        text = extract((synthetic_dir / f"{proposal.supplier.lower().replace(' ', '_')}.pdf")
                       .read_bytes()).text
        total = sum(amount for _, amount in proposal.price_items)
        assert f"{total:,}" in text


def _four_grams(text: str) -> set[tuple[str, ...]]:
    import re

    words = re.findall(r"[a-z0-9]+", re.sub(r"\[p\.\d+\]", " ", text.lower()))
    return {tuple(words[i : i + 4]) for i in range(len(words) - 3)}


def test_brief_proposals_are_textually_distinct(synthetic_dir: Path) -> None:
    """Guards against the four suppliers collapsing into near-copies of each other."""
    import itertools

    from core.tools.document_tool import extract

    names = ["apex_systems", "brightpath_tech", "nexaworks", "orbit_digital"]
    grams = {n: _four_grams(extract((synthetic_dir / f"{n}.pdf").read_bytes()).text)
             for n in names}
    for a, b in itertools.combinations(names, 2):
        overlap = len(grams[a] & grams[b]) / len(grams[a] | grams[b])
        assert overlap < 0.10, f"{a} and {b} are too similar ({overlap:.2f})"


# ---------------------------------------------------------------- diversity and ground truth


def _norm(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text).strip()


def _extract_all(synthetic_dir: Path) -> dict[str, Any]:
    from core.tools.document_tool import extract

    names = ["apex_systems", "brightpath_tech", "nexaworks", "orbit_digital",
             "realistic_nexaworks_20p", "long_apex_80p"]
    return {n: extract((synthetic_dir / f"{n}.pdf").read_bytes()) for n in names}


def test_brief_proposals_differ_in_structure(synthetic_dir: Path) -> None:
    docs = _extract_all(synthetic_dir)
    brief = ["apex_systems", "brightpath_tech", "nexaworks", "orbit_digital"]
    assert len({docs[n].pages for n in brief}) >= 3  # lengths vary
    assert max(docs[n].pages for n in brief) == 4 and min(docs[n].pages for n in brief) == 2
    order = {n: [s.title for s in docs[n].sections] for n in brief}
    assert len({tuple(t) for t in order.values()}) == 4  # different section lists
    shared = set.intersection(*(set(t.lower().split(". ", 1)[-1] for t in titles)
                                for titles in order.values()))
    assert not shared  # no section title common to all four
    fonts = {}
    import pymupdf

    for n in brief:
        with pymupdf.open(synthetic_dir / f"{n}.pdf") as pdf:
            fonts[n] = {s["font"] for p in pdf for b in p.get_text("dict")["blocks"]
                        if b["type"] == 0 for ln in b["lines"] for s in ln["spans"]}
    assert any("Times" in f for fs in fonts.values() for f in fs)
    assert any("Helvetica" in f for fs in fonts.values() for f in fs)


def test_missing_information_is_really_missing(synthetic_dir: Path) -> None:
    docs = _extract_all(synthetic_dir)
    bright = docs["brightpath_tech"].text.lower()
    assert "risk" not in bright and "soc 2" not in bright
    assert "reference" not in bright  # silently omitted rather than declared
    orbit = docs["orbit_digital"].text.lower()
    assert "risk register" not in orbit and "raci" not in orbit
    assert "soc 2" not in orbit
    apex = docs["apex_systems"].text.lower()
    assert "contact details can be supplied on request" in _norm(apex)


def test_long_documents_use_their_own_voice(synthetic_dir: Path) -> None:
    import re

    docs = _extract_all(synthetic_dir)

    def grams(text: str) -> set[tuple[str, ...]]:
        w = re.findall(r"[a-z0-9]+", re.sub(r"\[p\.\d+\]", " ", text.lower()))
        return {tuple(w[i : i + 4]) for i in range(len(w) - 3)}

    nexa20, apex80 = grams(docs["realistic_nexaworks_20p"].text), grams(docs["long_apex_80p"].text)
    assert len(nexa20 & apex80) / len(nexa20 | apex80) < 0.02
    for name in ("realistic_nexaworks_20p", "long_apex_80p"):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", docs[name].text) if len(s) > 40]
        assert len(set(sentences)) / len(sentences) >= 0.90


@pytest.fixture(scope="module")
def truth(synthetic_dir: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (synthetic_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return loaded


def test_truth_bands_are_valid(truth: dict[str, Any]) -> None:
    assert set(truth["criteria"]) == {"1", "2", "3", "4", "5"}
    assert sum(c["weight"] for c in truth["criteria"].values()) == 100
    assert len(truth["suppliers"]) == 4
    for info in truth["suppliers"].values():
        assert set(info["expected_bands"]) == set(truth["criteria"])
        for lo, hi in info["expected_bands"].values():
            assert 0 <= lo <= hi <= 10 and hi - lo <= 3
        assert set(info["rationale"]) == set(info["expected_bands"])


def test_truth_tiers_match_weighted_band_midpoints(truth: dict[str, Any]) -> None:
    weights = {k: v["weight"] for k, v in truth["criteria"].items()}
    score = {}
    for file, info in truth["suppliers"].items():
        score[file] = sum((lo + hi) / 2 / 10 * weights[cid]
                          for cid, (lo, hi) in info["expected_bands"].items())
    ranked = sorted(score, key=lambda f: -score[f])
    tiers = truth["expected_tiers"]
    assert set(ranked[:2]) == set(tiers["top"])
    assert ranked[2:3] == tiers["middle"] and ranked[3:] == tiers["bottom"]
    assert score[ranked[1]] - score[ranked[2]] > 15  # clear gap below the top tier


def test_truth_profiles_match_the_brief(truth: dict[str, Any]) -> None:
    bands = {f.split(".")[0]: {int(k): v for k, v in i["expected_bands"].items()}
             for f, i in truth["suppliers"].items()}
    mid = {s: {c: sum(b) / 2 for c, b in bs.items()} for s, bs in bands.items()}
    # Apex: strong technical design and security
    assert mid["apex_systems"][1] >= 8 and mid["apex_systems"][4] >= 8
    # BrightPath: weak compliance and limited experience
    assert mid["brightpath_tech"][4] <= 3 and mid["brightpath_tech"][5] <= 4
    # NexaWorks: strongest implementation plan and support model among non-experience leaders
    assert mid["nexaworks"][2] == max(m[2] for m in mid.values())
    assert mid["nexaworks"][5] >= max(m[5] for s, m in mid.items() if s != "orbit_digital")
    # Orbit: strong experience, vague technical/integration story
    assert mid["orbit_digital"][5] >= 7 and mid["orbit_digital"][1] <= 5


def test_truth_facts_appear_in_the_documents(synthetic_dir: Path, truth: dict[str, Any]) -> None:
    import re

    from core.tools.document_tool import extract

    for file, info in truth["suppliers"].items():
        text = _norm(extract((synthetic_dir / file).read_bytes()).text)
        for fact in info["key_facts"]:
            assert _norm(fact) in text, f"{file}: key fact not found: {fact}"
        assert f"{info['facts']['total_price_usd']:,}" in text
        weeks = info["facts"]["timeline_weeks"]
        assert re.search(rf"\b{weeks}[- ]weeks?\b", text), f"{file}: {weeks} weeks not stated"


def test_truth_special_cases_exist(synthetic_dir: Path, truth: dict[str, Any]) -> None:
    for file in list(truth["negative_cases"]) + list(truth["extended_documents"]):
        assert (synthetic_dir / file).exists()
    assert truth["negative_cases"]["corrupt_co.pdf"]["expect_error"] == "E_NO_TEXT"
