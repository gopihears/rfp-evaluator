from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import evals
from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider
from core.tools.recording import RecordingProvider, ReplayError, ReplayProvider, request_key

pytestmark = pytest.mark.eval


def test_kendall_tau() -> None:
    assert evals.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert evals.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    assert evals.kendall_tau(["a", "b", "c"], ["a", "c", "b"]) == pytest.approx(1 / 3)


def test_suite_with_the_demo_scorer_meets_every_threshold(synthetic_dir: Path) -> None:
    report = evals.run_suite(FakeProvider(demo_responder), "demo-model", synthetic_dir)
    assert report["all_passed"], report["passes"]
    m = report["metrics"]
    assert m["kendall_tau"] == 1.0 and m["top_tier_correct"] == 1.0
    assert m["injection_flagged"] == 1.0 and m["facts_exact"] == 1.0
    assert set(report["passes"]) == {
        "band_accuracy_within_1",
        "kendall_tau",
        "json_valid_without_repair",
        "evidence_grounded",
        "injection",
        "security_drop",
        "facts_exact",
        "retrieval_agreement",
    }


def test_record_then_replay_reproduces_the_same_metrics(
    synthetic_dir: Path, tmp_path: Path
) -> None:
    rec_path = tmp_path / "rec.json"
    recorder = RecordingProvider(FakeProvider(demo_responder), rec_path)
    live = evals.run_suite(recorder, "demo-model", synthetic_dir)
    recorder.save()
    assert rec_path.stat().st_size > 1000
    replayed = evals.run_suite(ReplayProvider(rec_path), "demo-model", synthetic_dir)
    assert replayed["metrics"] == live["metrics"]
    assert "sk-" not in rec_path.read_text(encoding="utf-8")


def test_replay_fails_loudly_on_an_unknown_request(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps({request_key("s", "u", {}, "m"): "{}"}), encoding="utf-8")
    replay = ReplayProvider(path)
    assert replay.complete_json(system="s", user="u", json_schema={}, model="m") == "{}"
    with pytest.raises(ReplayError):
        replay.complete_json(system="s", user="other", json_schema={}, model="m")
    assert replay.validate_key() == ["replay"]
    assert len(replay.embed(["a", "b"])) == 2


def test_cli_writes_a_report_and_exit_code(
    synthetic_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.json"
    assert evals.main(["--provider", "demo", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["all_passed"] is True
    assert '"all_passed": true' in capsys.readouterr().out


def test_cli_requires_a_key_for_openai_and_a_file_for_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        evals.main(["--provider", "openai"])
    with pytest.raises(SystemExit):
        evals.main(["--provider", "replay"])
