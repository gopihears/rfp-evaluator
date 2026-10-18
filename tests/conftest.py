from __future__ import annotations

from pathlib import Path

import pytest

from scripts import make_synthetic_pdfs


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fresh copy of the generated synthetic RFP PDFs (also exercises the generator)."""
    out = tmp_path_factory.mktemp("synthetic_rfps")
    make_synthetic_pdfs.main(["make_synthetic_pdfs", str(out)])
    return out
