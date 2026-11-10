"""조건별 비교 규칙 (계획서 5.3 표). DB·HTTP에 의존하지 않는 순수 함수입니다.

각 함수는 `MET`(충족) / `UNMET`(불충족) / `UNKNOWN`(알 수 없음)을 돌려줍니다.
공지에 그 조건이 없으면(`null`, `제한없음`) `None`을 돌려줘 비교하지 않습니다.
프로필 값이 비어 있거나 `모름`이면 `UNKNOWN`입니다.
`ambiguous_fields` 처리는 engine.py가 합니다.
"""

from datetime import date

from app.extraction.schemas import ScholarshipCriteria
from app.parsers.dates import to_kst
from app.schemas.api.profile import Profile, Region, grade_of, is_extra_semester
from app.schemas.api.scholarships import ConditionOutcome

Outcome = ConditionOutcome | None

MET: ConditionOutcome = "MET"
UNMET: ConditionOutcome = "UNMET"
UNKNOWN: ConditionOutcome = "UNKNOWN"

# 프로필에서 고를 수 있는 특수자격 (4.1). 공지 표현에 이 낱말이 들어 있으면 같은 자격으로 봅니다.
PROFILE_SPECIAL_KINDS = ("다자녀", "보훈", "장애")

# 공지에 줄여 쓴 시/도 이름 → 프로필(행정구역 정식 이름)
SIDO_ALIASES = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "강원도": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전라북도": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
    "제주도": "제주특별자치도",
}


def _bool(value: bool) -> ConditionOutcome:
    return MET if value else UNMET


# --- 학년·재학 ----------------------------------------------------------------------


def target_grades(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """학년은 이수학기에서 계산합니다(4.1). 초과학기 학생(8학기 이상)은 4학년입니다."""
    if c.target_grades is None:
        return None
    return _bool(grade_of(p.completed_semesters) in c.target_grades)


def excludes_extra_semester(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """`target_grades`가 null이어도 따로 비교합니다. false·null은 조건 없음."""
    if not c.excludes_extra_semester:
        return None
    return _bool(not is_extra_semester(p.completed_semesters))


def enrollment_status(p: Profile, c: ScholarshipCriteria) -> Outcome:
    if c.enrollment_status is None:
        return None
    return _bool(p.enrollment_status in c.enrollment_status)


# --- 성적 ---------------------------------------------------------------------------


def _at_least(value: float | None, minimum: float) -> Outcome:
    return UNKNOWN if value is None else _bool(value >= minimum)


def min_gpa(p: Profile, c: ScholarshipCriteria, *, basis_ambiguous: bool = False) -> Outcome:
    """평점(4.5 만점). `grade_basis = 전체`면 전체 평점평균, 그 밖은 직전학기 평점과 비교합니다."""
    if c.min_gpa is None:
        return None
    if basis_ambiguous:
        return UNKNOWN
    value = p.gpa_total if c.grade_basis == "전체" else p.gpa
    return _at_least(value, c.min_gpa)


def min_score_100(p: Profile, c: ScholarshipCriteria, *, basis_ambiguous: bool = False) -> Outcome:
    """100점 만점 성적. 평점에서 환산하지 않고 성적표의 100점 환산 점수와만 비교합니다."""
    if c.min_score_100 is None:
        return None
    if basis_ambiguous:
        return UNKNOWN
    value = p.score_100_total if c.grade_basis == "전체" else p.score_100
    return _at_least(value, c.min_score_100)


def min_credits(p: Profile, c: ScholarshipCriteria) -> Outcome:
    if c.min_credits is None:
        return None
    return _at_least(p.credits, c.min_credits)


# --- 인적 사항 ----------------------------------------------------------------------


def gender(p: Profile, c: ScholarshipCriteria) -> Outcome:
    if c.gender is None:
        return None
    return _bool(p.gender == c.gender)


def max_age(p: Profile, c: ScholarshipCriteria, today: date) -> Outcome:
    """기준일(`apply_end`, 없으면 오늘)의 연도 − 출생연도 = `n`.

    생일을 모르므로 실제 만 나이는 n 또는 n−1입니다.

    n ≤ max_age 충족 / n = max_age + 1 알 수 없음 / n ≥ max_age + 2 불충족
    """
    if c.max_age is None:
        return None
    basis_year = to_kst(c.apply_end).year if c.apply_end else today.year
    n = basis_year - p.birth_year
    if n <= c.max_age:
        return MET
    if n == c.max_age + 1:
        return UNKNOWN
    return UNMET


def max_income_bracket(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """기초 0, 차상위 0.5, 1~10구간. 프로필이 비어 있으면(모름) 알 수 없음."""
    if c.max_income_bracket is None:
        return None
    if p.income_bracket is None:
        return UNKNOWN
    return _bool(p.income_bracket <= c.max_income_bracket)


# --- 지역 ---------------------------------------------------------------------------


def _sido(name: str) -> str:
    return SIDO_ALIASES.get(name, name)


def _sigungu(name: str) -> str:
    """'논산시'와 '논산'을 같게 봅니다."""
    return name[:-1] if len(name) > 2 and name[-1] in "시군구" else name


def _match_one(notice_region: str, address: Region) -> ConditionOutcome:
    """공지 지역이 시/도만이면 시/도까지, 시/군/구까지 있으면 시/군/구까지 비교합니다."""
    parts = notice_region.split()
    if not parts:
        return UNKNOWN
    if _sido(parts[0]) != _sido(address.sido.strip()):
        return UNMET
    if len(parts) == 1:
        return MET
    if not address.sigungu:
        return UNKNOWN  # 공지는 시/군/구까지인데 프로필에 시/도만 있음
    return _bool(_sigungu(parts[1]) == _sigungu(address.sigungu.strip()))


def _match_address(regions: list[str], address: Region | None) -> ConditionOutcome:
    """공지 지역 중 하나라도 맞으면 충족."""
    if address is None:
        return UNKNOWN
    outcomes = {_match_one(r, address) for r in regions}
    if MET in outcomes:
        return MET
    return UNKNOWN if UNKNOWN in outcomes else UNMET


def regions(p: Profile, c: ScholarshipCriteria, *, basis_ambiguous: bool = False) -> Outcome:
    """`region_basis`가 본인/부모면 그 주소만, 무관(또는 null)이면 둘 중 하나라도 맞으면 충족.

    무관: 입력한 주소가 모두 안 맞고 하나가 비어 있으면 알 수 없음,
    둘 다 입력했고 둘 다 안 맞으면 불충족.
    기준이 모호하면(`ambiguous_fields`에 `region_basis`) 두 주소가 같은 결과일 때만 확정합니다.
    """
    if c.regions is None:
        return None
    own = _match_address(c.regions, p.self_region)
    parent = _match_address(c.regions, p.parent_region)
    if basis_ambiguous:
        return own if own == parent else UNKNOWN
    if c.region_basis == "본인":
        return own
    if c.region_basis == "부모":
        return parent
    if MET in (own, parent):
        return MET
    return UNKNOWN if UNKNOWN in (own, parent) else UNMET


# --- 특수자격·감면 -------------------------------------------------------------------


def _profile_kind(requirement: str) -> str | None:
    return next((k for k in PROFILE_SPECIAL_KINDS if k in requirement), None)


def required_special(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """요구 자격을 하나라도 가지면 충족. 프로필에 없는 종류(한부모 등)가 섞여 있으면 알 수 없음.

    프로필 값이 null이면(입력 안 함·민감정보 미동의) 알 수 없음, 빈 목록이면 '해당 없음'입니다.
    """
    if c.required_special is None:
        return None
    if p.special_flags is None:
        return UNKNOWN
    kinds = [_profile_kind(r) for r in c.required_special]
    if any(k in p.special_flags for k in kinds if k is not None):
        return MET
    return UNKNOWN if None in kinds else UNMET


def excludes_full_tuition(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """전액 감면자 중복수혜 불가. 프로필이 `예`면 불충족, `모름`·비어 있으면 알 수 없음."""
    if not c.excludes_full_tuition:
        return None
    if p.full_tuition_waiver == "예":
        return UNMET
    if p.full_tuition_waiver == "아니오":
        return MET
    return UNKNOWN


# --- 소속 ---------------------------------------------------------------------------


def target_department(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """프로필에 학과가 없고 모든 사용자를 소프트웨어학부로 봅니다(4.1, 2026-11-10).

    제한없음은 비교하지 않음 / 포함은 충족 / 불포함은 불충족 / 불명은 알 수 없음.
    """
    match c.target_department:
        case "제한없음":
            return None
        case "소프트웨어학부 포함":
            return MET
        case "소프트웨어학부 불포함":
            return UNMET
        case _:  # 불명
            return UNKNOWN


def target_program(p: Profile, c: ScholarshipCriteria) -> Outcome:
    """AIBC 양성인재 대상이면 예 충족, 아니오 불충족, 모름(null) 알 수 없음."""
    if c.target_program == "제한없음":
        return None
    return UNKNOWN if p.aibc_member is None else _bool(p.aibc_member)
