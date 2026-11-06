# WOOWANGTHON_TEAM_36_BE

장학금 매칭 서비스 backend입니다. 사양은 상위 작업 공간의 `docs/장학금_매칭_서비스_계획서.md`를 따릅니다.

## 배포 준비 (T08)

계정 생성, 결제, 비밀값 입력은 **개발 담당자**가 직접 합니다. 아래 순서대로 체크하고, 확인한 값(리전, 모델 이름, 한도)은 계획서에 기록합니다.

공통 변수(명령 예시에서 씀):

```bash
export PROJECT_ID=<프로젝트 ID>
export REGION=asia-southeast1   # 싱가포르 (9.1). 사이트가 막히면 asia-northeast3(서울)
```

### 1. Google Cloud 결제·프로젝트 (9.1)

- [ ] 담당자 Google 계정으로 결제 계정 생성(무료 체험, 2027-02-02까지)
- [ ] 프로젝트 생성 후 `gcloud config set project $PROJECT_ID`
- [ ] 예산 알림 월 $5: Billing → Budgets & alerts. **"Include credits in cost" 체크 해제**(크레딧 제외 기준), 알림 수신자는 담당자만(결제 관리자·사용자 기본 알림 해제 후 담당자 이메일만)
- [ ] IAM에서 다른 팀원에게 프로젝트 편집 이상 권한을 주지 않음
- [ ] API 사용 설정

  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com secretmanager.googleapis.com \
    cloudscheduler.googleapis.com
  ```

### 2. 사이트 접근 확인: Cloud Run Job (완료 기준)

`scripts/check_sites.py`는 표준 라이브러리만 씁니다. 그래서 앱 이미지를 빌드하지 않고 공개 `python:3.12-slim` 이미지에 스크립트를 base64로 넘겨 실행합니다. gcloud `--args`는 쉼표로 인자를 나누는데, base64에는 쉼표가 없어서 안전합니다.

```bash
SCRIPT_B64=$(base64 < scripts/check_sites.py | tr -d '\n')
gcloud run jobs deploy check-sites \
  --region $REGION \
  --image mirror.gcr.io/library/python:3.12-slim \
  --command sh \
  --args="-c","echo $SCRIPT_B64 | base64 -d | python -" \
  --max-retries 0 --task-timeout 120s
gcloud run jobs execute check-sites --region $REGION --wait
```

결과는 Cloud Logging에서 확인합니다.

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="check-sites"' \
  --limit 20 --format 'value(textPayload)' --freshness 10m
```

- [ ] 세 사이트(SW_DEPT, AIBC, KOSAF) 모두 `200`이면 싱가포르 확정
- [ ] 하나라도 막히면 `REGION=asia-northeast3`으로 다시 실행하고, 결과를 계획서 9.1 "리전"에 기록
- [ ] 확인이 끝나면 Job 삭제: `gcloud run jobs delete check-sites --region $REGION`

### 3. Neon (9.4)

- [ ] 프로젝트 생성: PostgreSQL **16**, 리전 **AWS Asia Pacific (Singapore)**
- [ ] Compute 설정: 오토스케일 최대 **0.25 CU**(최소도 0.25)
- [ ] 연결 문자열 두 개를 복사해 드라이버를 `postgresql+psycopg://`로 바꿈
  - pooled(호스트에 `-pooler`) → `DATABASE_URL`
  - direct → `DATABASE_URL_DIRECT`
- [ ] 매월 15일 전후 사용량 확인 일정 등록(100 CU-시간 초과 시 DB 정지)

### 4. Gemini (3장 AI 행, 9.1, 9.6, 9.9)

GCP 무료 체험 크레딧은 Gemini에 쓸 수 없습니다(2026-03부터). AI Studio 선불 크레딧으로 결제합니다.

- [ ] AI Studio에서 이 GCP 프로젝트를 가져옴(Import project)
- [ ] AI Studio 결제 화면에서 **선불 크레딧 $5** 구매(자동 충전 끔). 잔액이 $0이면 호출이 402로 실패함
- [ ] API 키를 gcloud로 만들고 Gemini API만 쓰도록 제한한 뒤, 값을 화면에 띄우지 않고 Secret Manager에 바로 등록

  ```bash
  gcloud services enable generativelanguage.googleapis.com apikeys.googleapis.com
  gcloud services api-keys create --display-name=scholarship-gemini \
    --api-target=service=generativelanguage.googleapis.com --format='value(name)'
  gcloud services api-keys get-key-string <위 출력의 키 이름> --format='value(keyString)' \
    | tr -d '\n' | gcloud secrets create GEMINI_API_KEY --replication-policy=automatic --data-file=-
  ```

- [ ] 새 프로젝트에서 쓸 수 있는 **최신 안정 Flash 모델 이름**을 확인해 계획서 9.6 `GEMINI_MODEL` 칸에 기록(2026-11-05: `gemini-3.8-flash`)
- [ ] AI Studio 한도 화면의 **분당·하루 요청 한도**를 계획서 9.9에 기록(2026-11-05 Tier 1: RPM 1,000 / TPM 2M / RPD 10,000)

### 5. Google OAuth (7.3 표)

- [ ] OAuth 동의 화면: 사용자 유형 **External**, scope는 `openid email profile`만
- [ ] 앱 홈페이지·개인정보처리방침 링크: `https://cbnu-scholarship.vercel.app/`, `https://cbnu-scholarship.vercel.app/privacy`
- [ ] OAuth 클라이언트: 유형 **웹 애플리케이션**, 승인된 리디렉션 URI 2개
  - `https://cbnu-scholarship.vercel.app/api/auth/google/callback`
  - `http://localhost:5173/api/auth/google/callback`
- [ ] `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` 보관(secret은 7번 Secret Manager에만)
- [ ] `/privacy` 화면 배포(T24) 후 게시 상태를 **Testing → In production**으로 전환

### 6. Vercel (9.2, 9.7)

- [ ] Hobby 계정으로 공개 저장소 `WOOWANGTHON_TEAM_36_FE`를 Import(Framework: Vite, 프로젝트 이름 `scholarship-matcher`)
- [ ] 프로덕션 주소 확정 → 5번 OAuth 설정에 입력. 2026-11-06: `https://cbnu-scholarship.vercel.app`(프로젝트 → Settings → Domains에서 추가. 기본 주소 `scholarship-matcher-seven.vercel.app`도 그대로 동작)
- [ ] `vercel.json`의 backend 주소는 첫 backend 배포(T28) 후 실제 `run.app` 주소로 바꿈

### 7. Secret Manager (9.5)

비밀값 7개를 등록합니다. `GEMINI_API_KEY`는 4번에서 이미 등록했습니다. 값이 화면·셸 기록에 남지 않도록 명령을 먼저 실행하고, 입력 프롬프트에 붙여 넣은 뒤 Enter를 누릅니다(입력은 보이지 않음). 명령을 복사하면 클립보드의 비밀값이 덮어써지므로 비밀값은 명령 실행 **뒤에** 복사합니다.

```bash
# zsh 기준. 이름마다 한 번씩 실행
name=GOOGLE_CLIENT_SECRET   # DATABASE_URL, DATABASE_URL_DIRECT도 같은 방법
read -rs "v?$name: "; echo
printf %s "$v" | gcloud secrets create $name --replication-policy=automatic --data-file=-
unset v
```

값을 붙여 넣는 칸이 프롬프트가 아니라 명령줄이면 셸이 그 값을 명령으로 실행해 기록에 남습니다. 그랬다면 그 값은 새로 발급합니다.

무작위 값 3개는 생성해서 바로 등록합니다(화면에 출력하지 않음).

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create JWT_SECRET --replication-policy=automatic --data-file=-
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" \
  | gcloud secrets create CRON_TOKEN --replication-policy=automatic --data-file=-
# AES-256 키: 32바이트 무작위 값의 base64 (T18 core/crypto.py가 이 형식을 읽음)
python3 -c "import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode(), end='')" \
  | gcloud secrets create FIELD_ENCRYPTION_KEY --replication-policy=automatic --data-file=-
```

- [ ] 7개 등록 확인: `gcloud secrets list`
- [ ] `FIELD_ENCRYPTION_KEY`는 잃어버리면 암호화 필드를 복구할 수 없으므로 담당자만 아는 곳에 따로 백업
- [ ] 값을 바꾸면 `gcloud secrets versions add`로 새 버전을 넣고, 이전 버전은 `versions disable` 후 확인되면 `versions destroy`(무료 활성 버전 6개)

비밀이 아닌 값(`GEMINI_MODEL`, `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `ADMIN_EMAILS`)은 Cloud Run 환경변수로 직접 넣습니다(T28).

## 배포 (T28)

`main`에 머지하면 GitHub Actions(`.github/workflows/deploy.yml`)가 backend를 Cloud Run에 배포합니다(계획서 9.5, 9.7).

- 순서: 담당자 승인(GitHub Environment `production`) → 마이그레이션(`DATABASE_URL_DIRECT`) → `gcloud run deploy --source .` → `GET /health` 확인
- 인증: Workload Identity Federation. 서비스 계정 키는 만들지 않습니다.
- 서비스: `scholarship-backend` (`asia-southeast1`), 런타임 서비스 계정 `scholarship-backend@`(Secret Manager 읽기만)
- 비밀값은 Secret Manager에서 연결하고, 비밀이 아닌 값(`GEMINI_MODEL`, `GOOGLE_REDIRECT_URI`)은 워크플로에 적고, `GOOGLE_CLIENT_ID`·`ADMIN_EMAILS`는 Environment 변수로 둡니다.
- 소스 업로드에서 빼는 파일은 `.gcloudignore`에 있습니다(`.env` 포함).

### 최초 1회: 권한 설정 (담당자)

필요한 역할은 공식 문서(Cloud Run 소스 배포의 Required roles, 2026-11-06 확인)를 따릅니다. 계획서 9.7 목록도 이에 맞춰 고쳤습니다.

```bash
export PROJECT_ID=scholarship-matcher-513804 REGION=asia-southeast1
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
export REPO=woowangthon/WOOWANGTHON_TEAM_36_BE
export RUNTIME_SA=scholarship-backend@$PROJECT_ID.iam.gserviceaccount.com
export DEPLOY_SA=github-deploy@$PROJECT_ID.iam.gserviceaccount.com

# 1) 런타임 서비스 계정: 앱이 쓰는 비밀값 6개만 읽기
gcloud iam service-accounts create scholarship-backend --display-name="backend runtime"
for s in DATABASE_URL JWT_SECRET FIELD_ENCRYPTION_KEY GEMINI_API_KEY CRON_TOKEN GOOGLE_CLIENT_SECRET; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$RUNTIME_SA \
    --role=roles/secretmanager.secretAccessor --condition=None
done

# 2) 빌드 서비스 계정(Compute Engine 기본): 소스 배포 빌드
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --role=roles/run.builder --condition=None

# 3) 배포용 서비스 계정 (GitHub Actions가 빌려 씀)
gcloud iam service-accounts create github-deploy --display-name="GitHub Actions deploy"
for r in roles/run.sourceDeveloper roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$DEPLOY_SA \
    --role=$r --condition=None
done
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member=serviceAccount:$DEPLOY_SA --role=roles/iam.serviceAccountUser
gcloud secrets add-iam-policy-binding DATABASE_URL_DIRECT --member=serviceAccount:$DEPLOY_SA \
  --role=roles/secretmanager.secretAccessor --condition=None   # 마이그레이션용

# 4) Workload Identity Federation: 이 저장소의 Actions만 배포 계정을 빌릴 수 있음
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc backend-repo \
  --location=global --workload-identity-pool=github --display-name="backend repo" \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository == '$REPO'"
gcloud iam service-accounts add-iam-policy-binding $DEPLOY_SA --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```

### 최초 1회: GitHub 설정 (담당자)

```bash
REPO=woowangthon/WOOWANGTHON_TEAM_36_BE
# Environment production: 필수 승인자 = 담당자(GitHub 사용자 id), main 브랜치만 배포
gh api -X PUT repos/$REPO/environments/production \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)" \
  -F "deployment_branch_policy[protected_branches]=false" \
  -F "deployment_branch_policy[custom_branch_policies]=true"
gh api -X POST repos/$REPO/environments/production/deployment-branch-policies -f name=main -f type=branch

# Environment 변수 (비밀값 아님)
gh variable set GCP_WIF_PROVIDER --env production --repo $REPO \
  --body "projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/backend-repo"
gh variable set GCP_DEPLOY_SA --env production --repo $REPO --body "$DEPLOY_SA"
gh variable set GOOGLE_CLIENT_ID --env production --repo $REPO --body "<OAuth 클라이언트 ID>"
gh variable set ADMIN_EMAILS --env production --repo $REPO --body "<관리자 Gmail, 쉼표 구분>"
```

### 최초 1회: 첫 배포 후 공개 접근·정리 정책·Scheduler

배포용 계정에는 IAM 정책 변경 권한이 없으므로 공개 접근(`allUsers` 호출 허용)은 첫 배포 직후 프로젝트 소유자가 한 번 설정합니다.

```bash
gcloud run services add-iam-policy-binding scholarship-backend --region=$REGION \
  --member=allUsers --role=roles/run.invoker
URL=$(gcloud run services describe scholarship-backend --region=$REGION --format='value(status.url)')

# Artifact Registry: 최근 이미지 2개만 남김(1개는 롤백용, 9.5)
cat > /tmp/ar-cleanup.json <<'JSON'
[
  {"name": "keep-2-most-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
  {"name": "delete-others", "action": {"type": "Delete"}, "condition": {"tagState": "ANY"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location=$REGION --policy=/tmp/ar-cleanup.json --no-dry-run

# 소스 버킷: 30일 지난 파일 삭제(9.5)
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lifecycle.json
gcloud storage buckets update gs://run-sources-$PROJECT_ID-$REGION --lifecycle-file=/tmp/lifecycle.json

# Cloud Scheduler 2개 (9.3, 9.8). 토큰은 화면에 출력하지 않고 바로 넘깁니다.
gcloud scheduler jobs create http crawl --location=$REGION \
  --schedule="0 0,3,6-23 * * *" --time-zone=Asia/Seoul \
  --uri="$URL/internal/crawl" --http-method=POST \
  --headers="X-Cron-Token=$(gcloud secrets versions access latest --secret=CRON_TOKEN)" \
  --attempt-deadline=30m --max-retry-attempts=0
gcloud scheduler jobs create http wake --location=$REGION \
  --schedule="*/10 8-23 * * *" --time-zone=Asia/Seoul \
  --uri="$URL/health" --http-method=GET --attempt-deadline=30s --max-retry-attempts=0
```

- 확인: `gcloud scheduler jobs run crawl --location=$REGION` 후 Neon `crawl_runs`에 `DONE` 기록
- `CRON_TOKEN`을 바꾸면 `gcloud scheduler jobs update http crawl --update-headers=...`로 같이 바꿉니다.
- 백필(T29) 동안은 `gcloud scheduler jobs pause crawl`, 끝나면 `resume`
