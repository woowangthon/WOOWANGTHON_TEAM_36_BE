"""T06: 계획서 7.2의 모든 엔드포인트가 /openapi.json에 있고, 아직 구현 전에는 501을 돌려줍니다."""

import pytest

# 계획서 7.2 표 (메서드, 경로)
SPEC_ENDPOINTS = {
    ("post", "/auth/signup"),
    ("post", "/auth/login"),
    ("post", "/auth/logout"),
    ("get", "/auth/google/login"),
    ("get", "/auth/google/callback"),
    ("post", "/auth/consent"),
    ("get", "/auth/me"),
    ("delete", "/auth/me"),
    ("get", "/profile"),
    ("put", "/profile"),
    ("delete", "/auth/consent/sensitive"),
    ("get", "/scholarships"),
    ("get", "/scholarships/{scholarship_id}"),
    ("put", "/scholarships/{scholarship_id}/state"),
    ("delete", "/scholarships/{scholarship_id}/state"),
    ("get", "/admin/crawl-runs"),
    ("post", "/admin/notices/{notice_id}/reextract"),
    ("post", "/internal/crawl"),
    ("get", "/health"),
}

# 501을 확인할 요청 (본문 검증을 통과하는 값)
STUB_REQUESTS = [
    ("get", "/auth/google/login", None),
    ("get", "/auth/google/callback?code=c&state=s", None),
    ("get", "/scholarships?result=eligible&sort=deadline&source=internal", None),
    ("get", "/scholarships/1", None),
    ("put", "/scholarships/1/state", {"state": "BOOKMARKED"}),
    ("delete", "/scholarships/1/state", None),
    ("get", "/admin/crawl-runs", None),
    ("post", "/admin/notices/1/reextract", None),
]

# 기능 카드에서 구현해 더 이상 501이 아닌 엔드포인트
IMPLEMENTED = {
    ("post", "/internal/crawl"),  # T17 (tests/scheduler/test_internal_api.py)
    # T19 (tests/api/test_auth.py)
    ("post", "/auth/signup"),
    ("post", "/auth/login"),
    ("post", "/auth/logout"),
    ("post", "/auth/consent"),
    ("get", "/auth/me"),
    ("delete", "/auth/me"),
    ("delete", "/auth/consent/sensitive"),
    # T21 (tests/api/test_profile.py)
    ("get", "/profile"),
    ("put", "/profile"),
}


def test_openapi_has_all_spec_endpoints(client):
    spec = client.get("/openapi.json").json()
    actual = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert actual == SPEC_ENDPOINTS


def test_every_spec_endpoint_is_stubbed():
    covered = {(m, p.split("?")[0]) for m, p, _ in STUB_REQUESTS}
    normalized = {
        (m, p.replace("{scholarship_id}", "1").replace("{notice_id}", "1"))
        for m, p in SPEC_ENDPOINTS
        if p != "/health" and (m, p) not in IMPLEMENTED
    }
    assert covered == normalized


@pytest.mark.parametrize(("method", "path", "body"), STUB_REQUESTS)
def test_stub_returns_501(client, method, path, body):
    response = client.request(method, path, json=body, follow_redirects=False)
    assert response.status_code == 501


def test_match_result_dto_shape(client):
    """5.3 판정 결과 DTO: result 3종, 조건별 {condition, outcome, evidence}, 상태 3종."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    summary = schemas["ScholarshipSummary"]["properties"]
    assert summary["result"]["enum"] == ["ELIGIBLE", "CONDITIONAL", "INELIGIBLE"]
    assert summary["status"]["enum"] == ["OPEN", "ALWAYS", "CLOSED"]
    check = schemas["ConditionCheck"]["properties"]
    assert set(check) == {"condition", "outcome", "evidence"}
    assert check["outcome"]["enum"] == ["MET", "UNMET", "UNKNOWN"]
    assert "conditions" in schemas["ScholarshipDetail"]["properties"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/auth/signup", {"email": "not-an-email", "password": "password123"}),
        ("/auth/signup", {"email": "a@example.com", "password": "short"}),
        # /auth/consent 본문 검증은 로그인이 필요해 tests/api/test_auth.py에서 확인
    ],
)
def test_invalid_body_is_422(client, path, body):
    assert client.post(path, json=body).status_code == 422


# 프로필 본문 검증(422)은 로그인·동의가 필요해 tests/api/test_profile.py에서 확인합니다.
