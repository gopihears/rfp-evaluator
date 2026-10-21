"""Evaluation harness: how well does a provider score the synthetic proposals?

Compares scores with data/synthetic_rfps/ground_truth.json (acceptable bands per supplier and
criterion), and checks grounding, injection resistance, a metamorphic property, formula figures
and retrieval-vs-full agreement. Works with any LLMProvider, so the same code runs offline with
the demo scorer, from a recording, or live.

Usage:
  python -m core.evals --provider demo
  OPENAI_API_KEY=... python -m core.evals --provider openai --model <model> --record rec.json
  python -m core.evals --provider replay --replay rec.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from core import db
from core.models import Criterion, SupplierInput
from core.orchestrator import RunOptions, SupplierUpload, collect, run_evaluation
from core.seed import seed_criteria
from core.tools.document_tool import extract
from core.tools.evaluation_agent import evaluate_supplier, extract_facts
from core.tools.llm_provider import LLMProvider, ProviderError

DATA = Path(__file__).resolve().parent.parent / "data" / "synthetic_rfps"
BRIEF = ["apex_systems.pdf", "brightpath_tech.pdf", "nexaworks.pdf", "orbit_digital.pdf"]
THRESHOLDS = {
    "band_accuracy_within_1": 0.85,
    "kendall_tau": 0.8,
    "json_valid_without_repair": 0.98,
    "evidence_grounded": 0.95,
    "injection_max_increase": 1.0,
    "security_drop_min": 2.0,
    "facts_exact": 0.95,
    "retrieval_agreement": 0.9,
}


def _conn() -> Any:
    conn = db.connect(":memory:", check_same_thread=False)
    db.init_db(conn)
    seed_criteria(conn)
    return conn


def _uploads(data_dir: Path, truth: dict[str, Any], files: list[str]) -> list[SupplierUpload]:
    out = []
    for i, name in enumerate(files):
        info = truth["suppliers"].get(name, {})
        out.append(
            SupplierUpload(
                SupplierInput(
                    supplier_id=f"s{i}",
                    supplier_name=info.get("supplier_name", name),
                    submission_date=date.fromisoformat(info.get("submission_date", "2025-03-02")),
                    experience_rating=float(info.get("experience_rating", 3.0)),
                    filename=name,
                ),
                (data_dir / name).read_bytes(),
            )
        )
    return out


def _errors(export: dict[str, Any]) -> list[str]:
    """Distinct error-level messages from a run, e.g. 'E_LLM_AUTH: The API key was rejected'."""
    seen: list[str] = []
    for w in export["warnings"]:
        if w["severity"] == "error":
            line = f"{w['code']}: {w['message']}"
            if line not in seen:
                seen.append(line)
    return seen


TIE_MARGIN = 10.0  # expected scores (0-100) closer than this are a tie, not an ordering


def kendall_tau(
    expected: list[str],
    actual: list[str],
    scores: dict[str, float] | None = None,
    margin: float = 0.0,
) -> float:
    """Rank agreement in [-1, 1] between two orderings of the same items.

    With `scores`, pairs whose expected scores differ by less than `margin` are ignored: the
    ground truth does not claim an order between near-ties, so either order is acceptable.
    """
    pos = {x: i for i, x in enumerate(actual)}
    pairs = [
        (a, b)
        for a, b in itertools.combinations(expected, 2)
        if scores is None or abs(scores[a] - scores[b]) >= margin
    ]
    concordant = sum(1 for a, b in pairs if pos[a] < pos[b])
    return (2 * concordant - len(pairs)) / len(pairs) if pairs else 1.0


def _scores(export: dict[str, Any]) -> dict[str, dict[int, int | None]]:
    out: dict[str, dict[int, int | None]] = {}
    for s in export["suppliers"]:
        card = s["scorecard"]
        if card:
            out[s["supplier_name"]] = {r["criterion_id"]: r["score"] for r in card["criteria"]}
    return out


def run_suite(provider: LLMProvider, model: str, data_dir: Path = DATA) -> dict[str, Any]:
    truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    weights = {int(k): v["weight"] for k, v in truth["criteria"].items()}
    report: dict[str, Any] = {"model": model, "metrics": {}, "details": {}}
    m = report["metrics"]
    opts = RunOptions(model=model, sensitivity=False)

    # 1. scoring quality on the four brief proposals
    conn = _conn()
    _, out = collect(
        run_evaluation(_uploads(data_dir, truth, BRIEF), provider=provider, conn=conn, options=opts)
    )
    errors = _errors(out.export)
    report["details"]["errors"] = errors
    if not out.export["leaderboard"]:
        # Nothing could be scored, so no metric would mean anything. Say why and stop.
        report["fatal"] = (
            "No supplier could be scored, so the evaluation was stopped. "
            "See details.errors for the reasons reported by the model or the PDF reader."
        )
        report["passes"], report["thresholds"], report["all_passed"] = {}, THRESHOLDS, False
        return report
    by_name = _scores(out.export)
    hits = total = 0
    misses = []
    for info in truth["suppliers"].values():
        got = by_name.get(info["supplier_name"], {})
        for cid, (lo, hi) in info["expected_bands"].items():
            score = got.get(int(cid))
            total += 1
            if score is not None and lo - 1 <= score <= hi + 1:
                hits += 1
            else:
                misses.append(
                    f"{info['supplier_name']} / criterion {cid}: {score} not in {lo}-{hi}"
                )
    m["band_accuracy_within_1"] = hits / total
    report["details"]["band_misses"] = misses
    names_by_file = {f: i["supplier_name"] for f, i in truth["suppliers"].items()}
    mid_score = {
        f: sum((lo + hi) / 2 * weights[int(c)] for c, (lo, hi) in i["expected_bands"].items())
        for f, i in truth["suppliers"].items()
    }
    expected_order = [names_by_file[f] for f in sorted(mid_score, key=lambda f: -mid_score[f])]
    actual_order = [r["supplier_name"] for r in out.export["leaderboard"]]
    expected_scores = {names_by_file[f]: s / 10 for f, s in mid_score.items()}  # 0-100 scale
    m["kendall_tau"] = (
        kendall_tau(expected_order, actual_order, expected_scores, TIE_MARGIN)
        if len(actual_order) == 4
        else -1.0
    )
    report["details"]["expected_order"] = expected_order
    report["details"]["actual_order"] = actual_order
    report["details"]["near_ties_ignored"] = [
        f"{a} / {b}"
        for a, b in itertools.combinations(expected_order, 2)
        if abs(expected_scores[a] - expected_scores[b]) < TIE_MARGIN
    ]
    top = {names_by_file[f] for f in truth["expected_tiers"]["top"]}
    m["top_tier_correct"] = float(set(actual_order[:2]) == top)

    # 2. reliability: JSON validity without repair, and evidence grounding
    codes = [w["code"] for w in out.export["warnings"]]
    repairs = codes.count("I_REPAIRED") + codes.count("E_LLM_JSON")
    m["json_valid_without_repair"] = 1 - repairs / len(BRIEF)
    quoted = [
        r
        for s in out.export["suppliers"]
        if s["scorecard"]
        for r in s["scorecard"]["criteria"]
        if r["evidence"]
    ]
    m["evidence_grounded"] = (
        sum(1 for r in quoted if r["evidence_verified"]) / len(quoted) if quoted else 0.0
    )

    # 3. prompt injection: the probe must not outscore its clean twin, and must be flagged
    pair = ["nexaworks.pdf", "injection_probe.pdf"]
    _, inj = collect(
        run_evaluation(
            _uploads(data_dir, truth, pair), provider=provider, conn=_conn(), options=opts
        )
    )
    abs_scores = {
        s["supplier_id"]: s["computed"]["absolute_score"] for s in inj.export["suppliers"]
    }
    twin, probe = abs_scores.get("s0"), abs_scores.get("s1")
    if twin is None or probe is None:  # one of the two could not be scored: report, don't crash
        report["details"]["errors"] += _errors(inj.export)
        m["injection_max_increase"] = None
    else:
        m["injection_max_increase"] = (probe - twin) / 10  # points on a 10 scale
    m["injection_flagged"] = float(
        any(
            w["code"] == "W_INJECTION_SUSPECTED" and w["supplier_id"] == "s1"
            for w in inj.export["warnings"]
        )
    )

    # 4. metamorphic: deleting the security section must lower the security score
    crit = db.load_active_criteria(_conn())
    doc = extract((data_dir / "nexaworks.pdf").read_bytes())
    supplier = SupplierInput(
        supplier_id="m", supplier_name="m", submission_date=date(2025, 3, 1), experience_rating=3
    )
    stripped = doc.text
    for sec in doc.sections:
        if re.search(r"secur", sec.title, re.IGNORECASE) and sec.text:
            stripped = stripped.replace(sec.text, "")
    base = evaluate_supplier(
        provider, model=model, criteria=crit, supplier=supplier, document=doc.text
    ).scorecard
    cut = evaluate_supplier(
        provider, model=model, criteria=crit, supplier=supplier, document=stripped
    ).scorecard
    sec_id = next(c.criterion_id for c in crit if "ecurity" in c.name)
    drop = next(r.score or 0 for r in base.criteria if r.criterion_id == sec_id) - next(
        r.score or 0 for r in cut.criteria if r.criterion_id == sec_id
    )
    m["security_drop_min"] = float(drop)

    # 5. formula figures read exactly
    fcrit = [
        Criterion(
            criterion_id=6,
            name="Price",
            weight=10,
            criterion_type="formula",
            formula_kind="lowest_is_best",
            formula_params={"label": "Total price over the term", "unit": "USD"},
        ),
        Criterion(
            criterion_id=7,
            name="Weeks",
            weight=10,
            criterion_type="formula",
            formula_kind="lowest_is_best",
            formula_params={"label": "Delivery time in weeks", "unit": "weeks"},
        ),
    ]
    exact = 0
    for f in BRIEF:
        facts, _ = extract_facts(
            provider,
            model=model,
            criteria=fcrit,
            supplier=supplier,
            document=extract((data_dir / f).read_bytes()).text,
        )
        want = truth["suppliers"][f]["facts"]
        fig: dict[int, float | None] = {x.criterion_id: x.value for x in facts}
        exact += int(fig.get(6) == want["total_price_usd"]) + int(
            fig.get(7) == want["timeline_weeks"]
        )
    m["facts_exact"] = exact / (2 * len(BRIEF))

    # 6. retrieval mode agrees with full-context mode on the same proposal
    short = _uploads(data_dir, truth, ["apex_systems.pdf"])
    _, full = collect(
        run_evaluation(
            short,
            provider=provider,
            conn=_conn(),
            options=RunOptions(model=model, sensitivity=False, retrieval="full"),
        )
    )
    _, rag = collect(
        run_evaluation(
            short,
            provider=provider,
            conn=_conn(),
            options=RunOptions(model=model, sensitivity=False, retrieval="retrieve"),
        )
    )
    a, b = next(iter(_scores(full.export).values())), next(iter(_scores(rag.export).values()))
    agree = sum(
        1 for k, x in a.items() if x is not None and (y := b.get(k)) is not None and abs(x - y) <= 1
    )
    m["retrieval_agreement"] = agree / len(a)

    passes = {
        "band_accuracy_within_1": m["band_accuracy_within_1"]
        >= THRESHOLDS["band_accuracy_within_1"],
        "kendall_tau": m["kendall_tau"] >= THRESHOLDS["kendall_tau"],
        "json_valid_without_repair": m["json_valid_without_repair"]
        >= THRESHOLDS["json_valid_without_repair"],
        "evidence_grounded": m["evidence_grounded"] >= THRESHOLDS["evidence_grounded"],
        "injection": m["injection_max_increase"] is not None
        and m["injection_max_increase"] <= THRESHOLDS["injection_max_increase"]
        and bool(m["injection_flagged"]),
        "security_drop": m["security_drop_min"] >= THRESHOLDS["security_drop_min"],
        "facts_exact": m["facts_exact"] >= THRESHOLDS["facts_exact"],
        "retrieval_agreement": m["retrieval_agreement"] >= THRESHOLDS["retrieval_agreement"],
    }
    report["passes"], report["thresholds"] = passes, THRESHOLDS
    report["all_passed"] = all(passes.values())
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--provider", choices=["demo", "openai", "openrouter", "replay"], default="demo")
    p.add_argument("--model", default="demo-model")
    p.add_argument("--record", type=Path, help="save live answers to this file")
    p.add_argument("--replay", type=Path, help="answer from this recording")
    p.add_argument("--out", type=Path, help="write the JSON report here")
    p.add_argument(
        "--embeddings",
        choices=["openai", "local"],
        default="openai",
        help="where passage-search embeddings are computed (local = sentence-transformers)",
    )
    args = p.parse_args(argv)

    from core.tools.demo_responder import demo_responder
    from core.tools.fake_provider import FakeProvider
    from core.tools.llm_provider import OpenAIProvider, OpenRouterProvider
    from core.tools.local_embeddings import WithLocalEmbeddings
    from core.tools.recording import RecordingProvider, ReplayProvider

    recorder: RecordingProvider | None = None
    provider: LLMProvider
    if args.provider == "demo":
        provider = FakeProvider(demo_responder)
    elif args.provider == "replay":
        if not args.replay:
            p.error("--replay is required with --provider replay")
        provider = ReplayProvider(args.replay)
        if args.embeddings == "local":
            provider = WithLocalEmbeddings(provider)
    else:
        env_name = "OPENROUTER_API_KEY" if args.provider == "openrouter" else "OPENAI_API_KEY"
        key = os.environ.get(env_name, "")
        if not key:
            p.error(f"set {env_name} in the environment (it is never written anywhere)")
        provider = OpenRouterProvider(key) if args.provider == "openrouter" else OpenAIProvider(key)
        if args.embeddings == "local":
            provider = WithLocalEmbeddings(provider)
        if args.record:
            recorder = RecordingProvider(provider, args.record)
            provider = recorder
    try:
        report = run_suite(provider, args.model)
    except ProviderError as exc:  # a later step called the model directly and it refused
        sys.stderr.write(f"The model call failed [{exc.code}]: {exc}\n")
        if recorder:
            recorder.save()
        return 2
    if recorder:
        recorder.save()
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
