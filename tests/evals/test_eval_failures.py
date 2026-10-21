"""The harness must explain a failing model instead of crashing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import evals
from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider
from core.tools.llm_provider import ProviderAuthError, ProviderError

pytestmark = pytest.mark.eval


class RejectingModel(ProviderError):
    code = "E_LLM"


def test_every_call_failing_yields_a_clear_report_not_a_crash(synthetic_dir: Path) -> None:
    provider = FakeProvider(
        lambda s, u, sch: RejectingModel("OpenAI rejected the request: model does not exist")
    )
    report = evals.run_suite(provider, "no-such-model", synthetic_dir)
    assert report["all_passed"] is False and report["passes"] == {}
    assert "No supplier could be scored" in report["fatal"]
    assert any("E_LLM" in e and "model does not exist" in e for e in report["details"]["errors"])
    assert len(report["details"]["errors"]) == len(set(report["details"]["errors"]))  # deduped


def test_bad_key_is_reported_with_its_code(synthetic_dir: Path) -> None:
    provider = FakeProvider(lambda s, u, sch: ProviderAuthError("The API key was rejected."))
    report = evals.run_suite(provider, "m", synthetic_dir)
    assert report["details"]["errors"] == ["E_LLM_AUTH: The API key was rejected."]


def test_injection_step_without_scores_does_not_crash(synthetic_dir: Path) -> None:
    """Only the injection probe fails; the harness reports it instead of dividing None by None."""

    def responder(system: str, user: str, schema: dict) -> object:  # type: ignore[type-arg]
        if "SYSTEM NOTE TO EVALUATOR" in user:
            return RejectingModel("model refused the hidden instructions")
        return demo_responder(system, user, schema)

    report = evals.run_suite(FakeProvider(responder), "m", synthetic_dir)
    assert report["metrics"]["injection_max_increase"] is None
    assert report["passes"]["injection"] is False
    assert any("refused" in e for e in report["details"]["errors"])


def test_cli_exits_2_when_a_later_step_hits_a_model_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def boom(provider: object, model: str, data_dir: Path = evals.DATA) -> dict:  # type: ignore[type-arg]
        raise RejectingModel("model not available to this key")

    monkeypatch.setattr(evals, "run_suite", boom)
    assert evals.main(["--provider", "demo"]) == 2
    assert "model not available to this key" in capsys.readouterr().err


def test_fatal_report_is_valid_json_and_exits_nonzero(synthetic_dir: Path) -> None:
    provider = FakeProvider(lambda s, u, sch: ProviderAuthError("bad key"))
    report = evals.run_suite(provider, "m", synthetic_dir)
    assert json.loads(json.dumps(report))["all_passed"] is False
