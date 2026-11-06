"""수집 대상 세 사이트의 목록 1페이지에 요청해 상태 코드와 응답 크기를 출력합니다 (T08).

Cloud Run(싱가포르)에서 해외 IP 차단이 없는지 확인하는 용도입니다(계획서 9.1).
표준 라이브러리만 쓰므로 `python:3.12-slim` 이미지에서 그대로 실행됩니다.

실행: python scripts/check_sites.py
종료 코드: 세 사이트 모두 200이면 0, 하나라도 아니면 1
"""

import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "CBNU-ScholarshipMatcher/0.1 (WOOWANGTHON team36; contact: pgmo0329@naver.com)"
REQUEST_INTERVAL_SECONDS = 1.2  # 공통 규칙: 요청 간격 1초 이상
TIMEOUT_SECONDS = 10

SITES = [
    ("SW_DEPT", "https://software.cbnu.ac.kr/index.php?mid=sub0401&category=8409"),
    ("AIBC", "https://aibc.cbnu.ac.kr/menu/1108"),
    ("KOSAF", "https://www.kosaf.go.kr/ko/notice.do?ctgrId1=0000000002"),
]


def check(url: str) -> tuple[int | str, int, float]:
    """(상태 코드 또는 오류 이름, 응답 바이트 수, 걸린 초)를 돌려줍니다."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
            return response.status, len(body), time.monotonic() - started
    except urllib.error.HTTPError as error:
        return error.code, len(error.read()), time.monotonic() - started
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return f"ERROR {type(reason).__name__}: {reason}", 0, time.monotonic() - started


def main() -> int:
    all_ok = True
    print(f"{'source':<8} {'status':<8} {'bytes':>10} {'seconds':>8}  url")
    for index, (source, url) in enumerate(SITES):
        if index:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        status, size, seconds = check(url)
        all_ok = all_ok and status == 200
        print(f"{source:<8} {status!s:<8} {size:>10} {seconds:>8.2f}  {url}")
    print("결과:", "세 사이트 모두 200" if all_ok else "200이 아닌 사이트가 있음")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
