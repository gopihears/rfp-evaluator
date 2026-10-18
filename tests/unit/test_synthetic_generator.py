from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
