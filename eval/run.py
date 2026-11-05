"""골든셋 평가 실행 (계획서 10장, T16).

    uv run python -m eval.run              # 30건 전체
    uv run python -m eval.run --only sw_dept_1154730 --workers 1

`tests/fixtures/golden/*.input.json`을 실제 `extract()`로 추출하고, `*.expected.json`과 채점해
필드별 표를 출력합니다. 결과는 `eval/results/{model_version}.json`에 저장합니다(커밋 대상).

Gemini를 실제로 호출하므로 `GEMINI_MODEL`, `GEMINI_API_KEY`가 필요하고 `AI_MOCK`은 꺼야 합니다.
입력은 공지 텍스트뿐이라 사용자 정보는 보내지 않습니다(8.1).
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.extraction import ExtractionError, ExtractRequest, extract
from app.extraction.schemas import ExtractedCriteria
from app.parsers.dates import now_kst
from eval.scoring import FIELDS, NoticeScore, score_notice, summarize

BACKEND_DIR = Path(__file__).resolve().parents[1]
GOLDEN_DIR = BACKEND_DIR / "tests" / "fixtures" / "golden"
RESULTS_DIR = BACKEND_DIR / "eval" / "results"
DEFAULT_WORKERS = 4  # Tier 1 분당 1,000회 한도(9.9)보다 훨씬 적게


def golden_ids(only: list[str] | None = None) -> list[str]:
    ids = sorted(p.name.removesuffix(".input.json") for p in GOLDEN_DIR.glob("*.input.json"))
    if only:
        missing = set(only) - set(ids)
        if missing:
            raise SystemExit(f"골든셋에 없는 공지: {sorted(missing)}")
        ids = [i for i in ids if i in only]
    return ids


def run_one(notice_id: str) -> dict[str, Any]:
    request = ExtractRequest.model_validate_json(
        (GOLDEN_DIR / f"{notice_id}.input.json").read_text(encoding="utf-8")
    )
    expected = ExtractedCriteria.model_validate_json(
        (GOLDEN_DIR / f"{notice_id}.expected.json").read_text(encoding="utf-8")
    )
    started = time.monotonic()
    predicted: ExtractedCriteria | None = None
    model_version = None
    error = None
    try:
        result = extract(request)
        predicted, model_version = result.criteria, result.model_version
    except ExtractionError as exc:
        error = str(exc)
    seconds = round(time.monotonic() - started, 1)
    score = score_notice(notice_id, expected, predicted)
    return {
        "score": score,
        "seconds": seconds,
        "error": error,
        "model_version": model_version,
        "predicted": None if predicted is None else predicted.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="골든셋 평가")
    parser.add_argument("--only", nargs="*", help="이 공지 ID만 평가")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.ai_mock:
        raise SystemExit("AI_MOCK=1이면 실제 모델을 평가할 수 없습니다. AI_MOCK=0으로 실행하세요.")
    if not settings.gemini_model:
        raise SystemExit("GEMINI_MODEL이 필요합니다")

    ids = golden_ids(args.only)
    print(f"골든셋 {len(ids)}건 평가 시작 (model={settings.gemini_model}, workers={args.workers})")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        runs = dict(zip(ids, pool.map(run_one, ids), strict=True))
    elapsed = time.monotonic() - started

    scores: list[NoticeScore] = [r["score"] for r in runs.values()]
    summary = summarize(scores)
    versions = {r["model_version"] for r in runs.values() if r["model_version"]}
    model_version = versions.pop() if len(versions) == 1 else f"{settings.gemini_model}+unknown"
    seconds = sorted(r["seconds"] for r in runs.values())

    report = {
        "model_version": model_version,
        "run_at": now_kst().isoformat(timespec="seconds"),
        "summary": summary,
        "latency_seconds": {
            "total": round(elapsed, 1),
            "median": seconds[len(seconds) // 2] if seconds else None,
            "max": seconds[-1] if seconds else None,
        },
        "notices": {
            notice_id: {
                "seconds": r["seconds"],
                "error": r["error"],
                "count_expected": r["score"].count_expected,
                "count_predicted": r["score"].count_predicted,
                "fields": {k: asdict(v) for k, v in r["score"].fields.items()},
                "mismatches": r["score"].mismatches,
                "predicted": r["predicted"],
            }
            for notice_id, r in runs.items()
        },
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{model_version}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print_table(report)
    print(f"결과 저장: {path}")
    return 0


def print_table(report: dict[str, Any]) -> None:
    summary = report["summary"]
    latency = report["latency_seconds"]
    print()
    print(f"모델: {report['model_version']}")
    print(
        f"공지 {summary['notices']}건, 추출 실패 {summary['failed']}건, "
        f"장학금 개수 일치 {summary['count_match']}/{summary['notices']}"
    )
    print(
        f"소요 {latency['total']}초 "
        f"(공지 1건 중앙값 {latency['median']}초, 최대 {latency['max']}초)"
    )
    print()
    print(f"| {'필드':<20} | {'맞음':>5} | {'전체':>5} | {'정답률':>7} |")
    print(f"| {'-' * 20} | {'-' * 5}:| {'-' * 5}:| {'-' * 7}:|")
    for name in FIELDS:
        value = summary["fields"][name]
        print(
            f"| {name:<20} | {value['correct']:>5} | {value['total']:>5} | "
            f"{value['accuracy']:>7.1%} |"
        )
    print(
        f"| {'전체':<20} | {summary['correct']:>5} | {summary['total']:>5} | "
        f"{summary['accuracy']:>7.1%} |"
    )
    print()
    print(f"목표 {summary['target']:.0%} 대비 {summary['gap']:+.1%}p")


if __name__ == "__main__":
    sys.exit(main())
