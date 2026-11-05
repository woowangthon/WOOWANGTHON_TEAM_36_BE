# Git 규칙

backend·frontend 공통. 커밋·PR·머지 전에 확인합니다. 계획서 8.4(브랜치)·9.7(배포)을 구체화한 문서입니다.

> 원본은 작업 공간의 `docs/git_규칙.md`입니다. 두 저장소의 `CONTRIBUTING.md`는 그 사본이므로 함께 고칩니다.

```
main → 브랜치 → 커밋 → PR → CI + 리뷰 1명 → Squash 머지 → 자동 배포
```

---

## 브랜치

`<type>/T<카드번호>-<kebab-case-설명>`

```
feat/T18-security-core
fix/T17-deadline-parse
```

| type | 용도 |
| --- | --- |
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 동작 변화 없는 구조 개선 |
| `test` | 테스트 |
| `docs` | 문서 |
| `chore` | 설정·의존성·빌드 |

- `main`에 직접 push하지 않습니다.
- PR 하나 = 카드 하나. 카드 번호는 브랜치 이름과 PR 본문에만 씁니다.
- 카드에 속하지 않는 작업은 카드 번호를 뺍니다: `docs/contributing`, `chore/ci-setup`

---

## 커밋

[Conventional Commits](https://www.conventionalcommits.org/ko/)

```
<type>(<scope>): <요약>

<본문: 필요할 때만, 왜 바꿨는지 1~3줄>
```

| 항목 | 규칙 |
| --- | --- |
| type | 브랜치 표와 같음 |
| scope | backend `crawler` `parser` `extraction` `scheduler` `api` `auth` `profile` `matching` `db` `eval` `deploy` `deps`<br>frontend `ui` `api` `auth` `profile` `dashboard` `admin` `deploy` `deps` |
| 요약 | 한국어, 50자 이내, 마침표 없음, "~ 추가 / 수정 / 제거"로 끝냄 |
| 본문 | **무엇**이 아니라 **왜**. 파일 목록·계획서 절 번호는 쓰지 않음 |
| 의존성 | 추가 이유를 본문에 한 줄 |

```
feat(crawler): 소프트웨어학부 장학 게시판 크롤러 추가
fix(extraction): 조건 없는 참/거짓 필드를 null로 처리
chore(deps): pymupdf 추가

PDF 첨부의 장학 조건을 읽기 위해 필요합니다.
```

| ❌ | 이유 |
| --- | --- |
| `T17: 수집 파이프라인 구현` | 카드 번호는 GitHub에서 의미가 없음 |
| `수정` | 무엇을 고쳤는지 알 수 없음 |
| `feat: 크롤러·파서·API 추가` | 한 커밋에 여러 일 |
| 본문에 변경 사항 10줄 나열 | diff로 충분 |

작업 브랜치 안의 커밋은 자유롭게 합니다. Squash 머지하면 **PR 제목이 `main`의 커밋 제목**이 됩니다.

---

## PR

**제목**: 커밋 제목과 같은 형식 → 머지 시 `feat(scheduler): 정기 수집 파이프라인 추가 (#13)`

**본문**

```markdown
## 변경 내용
- 무엇을 왜 바꿨는지 2~5줄

## 확인
- `uv run pytest` 269 passed

## 관련
- 카드: T17
- 연결 PR: woowangthon/WOOWANGTHON_TEAM_36_FE#3
```

**올리기 전**
- 테스트·린트 통과 — backend `uv run pytest`, `uv run ruff check . && uv run ruff format .` / frontend `npm run test`, `npm run lint`
- `git diff --staged`로 비밀값·무관한 파일이 없는지 확인
- backend API가 바뀌면 frontend에서 `npm run gen:api` PR을 만들고 서로 링크

---

## 머지

| 항목 | 규칙 |
| --- | --- |
| 조건 | 리뷰 1명 승인 + CI(테스트·린트) 통과 |
| 방식 | Squash merge만 사용 |
| 제목 | `PR 제목 (#번호)` |
| 정리 | 머지한 브랜치는 바로 삭제 |

---

## 릴리스

`main` 머지 = 배포. 릴리스 브랜치·버전 번호는 두지 않습니다.

| 저장소 | 머지 후 | 확인 |
| --- | --- | --- |
| frontend | Vercel 자동 배포 (PR마다 미리보기 URL) | 배포 URL |
| backend | GitHub Actions: `alembic upgrade head` → Cloud Run 배포. Environment `production` 담당자 승인 필요 | `GET /health` 200 |

**API 변경**: backend 머지·배포 완료 → frontend 머지. 순서가 바뀌면 화면이 없는 API를 호출합니다.

**마이그레이션**: 컬럼 삭제·이름 변경은 PR 본문에 명시하고, 가능하면 추가 → 코드 전환 → 삭제 순으로 나눠 배포합니다.

**롤백**: 머지된 PR에서 **Revert** → 새 PR로 머지. `main`을 reset하거나 force push하지 않습니다. DB 되돌리기(`alembic downgrade`)는 담당자가 판단합니다.

**시점 표시**: 시연·제출본만 태그를 답니다.

```bash
git tag -a demo-2026-11-20 -m "시연 제출본" && git push origin demo-2026-11-20
```

---

## 금지

- `main` 직접 push, `main` force push (개인 브랜치는 `--force-with-lease`만)
- 비밀값 커밋 — 올렸다면 삭제로 끝나지 않으므로 즉시 알리고 키를 재발급
- 관계없는 변경을 한 PR에 섞기
- 테스트 실패 상태로 머지
