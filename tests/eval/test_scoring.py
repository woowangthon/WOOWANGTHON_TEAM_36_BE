"""골든셋 채점 테스트 (T16). Gemini를 호출하지 않습니다."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.extraction.schemas import ExtractedCriteria, ExtractionResult
from eval import run
from eval.scoring import FIELDS, pair_scholarships, score_notice, summarize

SEOUL = ZoneInfo("Asia/Seoul")


def criteria(kind: str = "모집", scholarships: list[dict] | None = None) -> ExtractedCriteria:
    return ExtractedCriteria.model_validate(
        {"notice_kind": kind, "scholarships": scholarships or []}
    )


def scholarship(name: str, **fields) -> dict:
    return {"scholarship_name": name, **fields}


BASE = scholarship(
    "푸른등대 일상회복",
    apply_end=datetime(2026, 11, 12, 18, 0, tzinfo=SEOUL),
    min_gpa=3.0,
    target_grades=[2, 3],
    max_income_bracket=8,
    target_department="제한없음",
)


def test_perfect_match():
    expected = criteria(scholarships=[BASE])
    score = score_notice("n1", expected, criteria(scholarships=[BASE]))
    assert score.count_match
    assert all(v.correct == v.total == 1 for v in score.fields.values())
    assert score.mismatches == []


def test_target_grades_order_does_not_matter():
    predicted = criteria(scholarships=[{**BASE, "target_grades": [3, 2]}])
    score = score_notice("n1", criteria(scholarships=[BASE]), predicted)
    assert score.fields["target_grades"].correct == 1


def test_apply_end_must_match_exactly():
    predicted = criteria(
        scholarships=[{**BASE, "apply_end": datetime(2026, 11, 12, 23, 59, tzinfo=SEOUL)}]
    )
    score = score_notice("n1", criteria(scholarships=[BASE]), predicted)
    assert score.fields["apply_end"].correct == 0
    assert score.mismatches == [
        {
            "scholarship": "푸른등대 일상회복",
            "field": "apply_end",
            "expected": "2026-11-12T18:00:00+09:00",
            "got": "2026-11-12T23:59:00+09:00",
        }
    ]


def test_null_is_a_value():
    # null = 조건 없음이므로 정답 null에 값을 넣으면 틀림
    predicted = criteria(scholarships=[{**BASE, "min_gpa": None}])
    score = score_notice("n1", criteria(scholarships=[BASE]), predicted)
    assert score.fields["min_gpa"].correct == 0


def test_pairs_by_name_not_order():
    other = scholarship("푸른등대 지역 1차 산업", target_department="소프트웨어학부 불포함")
    expected = criteria(scholarships=[BASE, other])
    predicted = criteria(scholarships=[other, BASE])  # 순서가 바뀜
    score = score_notice("n1", expected, predicted)
    assert all(v.correct == v.total for v in score.fields.values())


def test_missing_scholarship_counts_all_fields_wrong():
    other = scholarship("푸른등대 지역 1차 산업")
    score = score_notice("n1", criteria(scholarships=[BASE, other]), criteria(scholarships=[BASE]))
    assert not score.count_match
    assert score.fields["min_gpa"].total == 2
    assert score.fields["min_gpa"].correct == 1
    assert sum(1 for m in score.mismatches if m["got"] == "(장학금 없음)") == 5


def test_extra_predicted_scholarship_does_not_grow_denominator():
    extra = scholarship("없는 장학금")
    score = score_notice("n1", criteria(scholarships=[BASE]), criteria(scholarships=[BASE, extra]))
    assert not score.count_match
    assert sum(v.total for v in score.fields.values()) == len(FIELDS)
    assert all(v.correct == v.total for v in score.fields.values())


def test_result_notice_scores_only_notice_kind():
    score = score_notice("n1", criteria("결과"), criteria("결과"))
    assert score.fields["notice_kind"].correct == 1
    assert sum(v.total for v in score.fields.values()) == 1


def test_wrong_notice_kind():
    score = score_notice("n1", criteria("기타"), criteria("모집", [BASE]))
    assert score.fields["notice_kind"].correct == 0
    assert not score.count_match


def test_extraction_failure_counts_everything_wrong():
    score = score_notice("n1", criteria(scholarships=[BASE]), None)
    assert score.count_predicted is None
    assert sum(v.correct for v in score.fields.values()) == 0
    assert sum(v.total for v in score.fields.values()) == len(FIELDS)


def test_pair_scholarships_uses_each_prediction_once():
    a, b = (
        criteria(scholarships=[BASE]).scholarships[0],
        criteria(scholarships=[scholarship("푸른등대 일상회복 2")]).scholarships[0],
    )
    pairs = pair_scholarships([a, a], [b])
    assert pairs[0][1] is b
    assert pairs[1][1] is None


def test_summarize_accuracy_and_gap():
    perfect = score_notice("n1", criteria(scholarships=[BASE]), criteria(scholarships=[BASE]))
    failed = score_notice("n2", criteria(scholarships=[BASE]), None)
    summary = summarize([perfect, failed])
    assert summary["correct"] == 6 and summary["total"] == 12
    assert summary["accuracy"] == 0.5
    assert summary["gap"] == pytest.approx(-0.4)
    assert summary["failed"] == 1
    assert summary["count_match"] == 1


def test_golden_set_expected_answers_score_perfectly():
    # 정답끼리 채점하면 100%여야 함(채점·정답 파일 형식 확인)
    scores = []
    for notice_id in run.golden_ids():
        expected = ExtractedCriteria.model_validate_json(
            (run.GOLDEN_DIR / f"{notice_id}.expected.json").read_text(encoding="utf-8")
        )
        scores.append(score_notice(notice_id, expected, expected))
    summary = summarize(scores)
    assert summary["notices"] == 30
    assert summary["accuracy"] == 1.0


def test_run_writes_results_with_mocked_extract(monkeypatch, tmp_path, capsys):
    """run.main 전체 흐름: 정답을 그대로 돌려주는 가짜 extract로 실행합니다."""
    monkeypatch.setenv("AI_MOCK", "0")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-flash")
    monkeypatch.setattr(run, "RESULTS_DIR", tmp_path)

    def fake_extract(request):
        notice_id = next(
            i
            for i in run.golden_ids()
            if (run.GOLDEN_DIR / f"{i}.input.json").read_text(encoding="utf-8").find(request.title)
            >= 0
        )
        expected = ExtractedCriteria.model_validate_json(
            (run.GOLDEN_DIR / f"{notice_id}.expected.json").read_text(encoding="utf-8")
        )
        return ExtractionResult(criteria=expected, model_version="gemini-test-flash+extract_v1")

    monkeypatch.setattr(run, "extract", fake_extract)
    assert run.main(["--only", "sw_dept_1154730", "aibc_aq74eFJIirVMESsaQS7qtM"]) == 0

    saved = json.loads((tmp_path / "gemini-test-flash+extract_v1.json").read_text(encoding="utf-8"))
    assert saved["summary"]["accuracy"] == 1.0
    assert set(saved["notices"]) == {"sw_dept_1154730", "aibc_aq74eFJIirVMESsaQS7qtM"}
    output = capsys.readouterr().out
    assert "| 전체" in output and "100.0%" in output


def test_run_refuses_mock(monkeypatch):
    monkeypatch.setenv("AI_MOCK", "1")
    with pytest.raises(SystemExit):
        run.main([])
