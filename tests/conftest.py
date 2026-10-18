from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import make_synthetic_pdfs


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fresh copy of the generated synthetic RFP PDFs (also exercises the generator)."""
    out = tmp_path_factory.mktemp("synthetic_rfps")
    make_synthetic_pdfs.main(["make_synthetic_pdfs", str(out)])
    return out


@pytest.fixture(scope="session")
def sample_outcome(synthetic_dir: Path) -> Iterator[Any]:
    """A real run over the four brief suppliers plus the unreadable one (demo scorer)."""
    from core import db
    from core.models import SupplierInput
    from core.orchestrator import RunOptions, SupplierUpload, collect, run_evaluation
    from core.seed import seed_criteria
    from core.tools.demo_responder import demo_responder
    from core.tools.fake_provider import FakeProvider

    conn = db.connect(":memory:", check_same_thread=False)
    db.init_db(conn)
    seed_criteria(conn)
    meta = [("apex_systems.pdf", "Apex Systems", date(2025, 3, 3), 4.5),
            ("brightpath_tech.pdf", "BrightPath Tech", date(2025, 3, 1), 2.0),
            ("nexaworks.pdf", "NexaWorks", date(2025, 3, 4), 4.0),
            ("orbit_digital.pdf", "Orbit Digital", date(2025, 3, 4), 4.5),
            ("corrupt_co.pdf", "Corrupt Co", date(2025, 3, 2), 1.0)]
    uploads = [
        SupplierUpload(
            SupplierInput(supplier_id=f"s{i}", supplier_name=name, submission_date=submitted,
                          experience_rating=rating, filename=filename),
            (synthetic_dir / filename).read_bytes())
        for i, (filename, name, submitted, rating) in enumerate(meta)
    ]
    _, outcome = collect(run_evaluation(
        uploads, provider=FakeProvider(demo_responder), conn=conn,
        options=RunOptions(model="demo-model", sensitivity_draws=60)))
    yield outcome
    conn.close()
