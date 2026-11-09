# 테스트 fixture (T07)

크롤러·파서·추출 테스트가 쓰는 저장 파일입니다. **테스트에서는 실제 사이트에 요청하지 않고 이 파일만 씁니다.**

- 수집일: 2026-11-05 (KST)
- 요청 간격 1.2초, User-Agent `CBNU-ScholarshipMatcher/0.1 (WOOWANGTHON team36; contact: pgmo0329@naver.com)`
- HTML은 서버 응답을 그대로 저장했습니다. 예외로 `aibc/kiF1EPTNkL5vj4HVH5idK5.html`의 학번 180개는 뒤 6자리를 `******`로 가렸습니다(개인정보).

## sw_dept/ — 소프트웨어학부 장학 게시판 (XE)

URL: `https://software.cbnu.ac.kr/index.php?mid=sub0401&document_srl=<id>`
선택자: 본문 `.xe_content`, 제목 `.top_area h1`, 게시일 `.date`(`2026.10.16 20:47` 형식).

| 파일 | 경우 |
| --- | --- |
| `list_p1.html` | 목록 1페이지. 상단 고정 공지(★)와 일반 글이 섞여 있음 |
| `1154870.html` | **다른 분류(학부) 상단 고정 글**. 장학 공지가 아니므로 골든셋에서 제외 |
| `1154016.html` | 상단 고정 기준 안내(성적우수 장학생 선발 기준), PDF 첨부 |
| `1154730.html` | **장학금이 여러 개인 공지**(푸른등대 2차: 일상회복·지역 1차 산업) |
| `1154905.html`, `1154665.html`, `1154615.html` | 외부 재단 장학금, PDF 공고문 첨부 |
| `1154830.html` | 국가근로 동계 집중근로(시급, 신청 시각 포함) |
| `1154721.html` | 지역 조건(충청북도 본인 주소), 초과학기자 제외 |
| `1154674.html` | 학부 자체 장학금(Level-Up), 성적 B0·12학점 |
| `1154590.html` | 교내 천사장학금, 첨부는 HWP뿐 |
| `1154414.html` | 소득 5구간 이내, 휴학생 불가 |
| `1154346.html` | 신청 시작 연도가 오타(2025)인 공지 |
| `1154342.html` | 지역(논산시, 본인·부모 무관) + 출생일 조건 |
| `1153811.html` | 등록금+지원금(혼합), 백분위 성적 조건 |
| `1153906.html` | 마감 시각 없는 마감일(9.10) |
| `1154172.html` | **장학금이 아닌 글**(대학원 오픈랩 행사) |
| `1153873.html` | 장학금이 아닌 절차 안내(국가장학금 가구원 동의) |
| `pdf/1154016_0.pdf` | 표가 많은 학부 기준 PDF |
| `pdf/1154721_0.pdf` | 1쪽짜리 짧은 PDF |
| `pdf/1154905_0.pdf` | 재단 공문형 PDF |
| `pdf/1154665_1.pdf` | 재단 선발 공고문 PDF(여러 쪽) |

- **첨부 다운로드 주의**: XE 첨부는 상세 페이지를 연 **같은 세션 쿠키 + Referer(상세 URL)**가 있어야 받아집니다. 없으면 200 응답으로 42,284바이트짜리 "OOPS! 잘못된 요청" HTML이 옵니다. 크롤러는 Content-Type 또는 `%PDF` 시그니처로 확인해야 합니다.
- HWP/HWPX 첨부는 텍스트를 추출하지 않습니다(계획서 2장 PDF만).

## aibc/ — AI부트캠프사업단 공지

URL: `https://aibc.cbnu.ac.kr/menu/1108/<id>`
선택자: 본문 `#content`, 게시일은 `작성자 … 등록일 YYYY-MM-DD 조회수` 줄에서 읽습니다(시각 없음 → 00:00 KST).

| 파일 | 경우 |
| --- | --- |
| `list.html` | 목록 1페이지 |
| `aq74eFJIirVMESsaQS7qtM.html` | 장학금 지급 기준 안내(마감 없는 기준 안내) |
| `pVAfFxOycuAihHhbGS0LAc.html` | 자격증 장학금 가점 안내(긴 표) |
| `kiF1EPTNkL5vj4HVH5idK5.html` | **결과 공지**(동계 몰입형교육 선발 결과, 학번 마스킹) |
| `ypFXkiw6xSDhBxkPBz3aht.html` | 장학금이 아닌 경비 지원 안내, HWPX·PDF 첨부 링크 |

## kosaf/ — 한국장학재단 장학금 공지

URL: `https://kosaf.go.kr/ko/notice.do?mode=view&seqNo=<id>` (목록 `ctgrId1=0000000002`)
선택자: `table.t-viewz`, 본문 `td#VIEW_MCONTENT`, 게시일 `작성일 : YYYY.MM.DD`.

| 파일 | 경우 |
| --- | --- |
| `list_p1.html` | 목록 1페이지 |
| `21427.html` | 모집 공지(세부 기준은 첨부 참조) |
| `21470.html` | **결과 공지**(SOS장학금 최종 선발결과) |
| `21475.html` | 장학금이 아닌 글(운영위원회 회의록) |
| `files/21475_1.pdf` | 21475의 첨부 PDF(회의록, 텍스트 PDF) |
| `files/21427_1.zip` | 21427의 붙임1 ZIP. 안에 PDF 1개(업무처리기준)와 HWP 1개, 파일 이름은 CP949 |

- 페이지 1건이 약 1.2MB라서 상세는 3건만 저장했습니다. 골든셋의 나머지 KOSAF 7건은 `golden/`에 원문 텍스트만 있습니다.
- 첨부 링크 `fileDown(pPath, seqNo, fileNo)`는 `/ko/download.do?pPath=…&pSeq_No=…&pFile_No=…`를 GET으로 여는 것뿐입니다. 세션 쿠키·Referer 없이도 받아집니다(2026-11-09 확인, `files/`는 이날 받은 파일).
- **저작권**: 한국장학재단 게시물은 공공누리 제4유형(출처표시·상업적 이용금지·변경금지)입니다. 이 파일은 테스트 용도로만 원본 그대로 두며, 서비스 화면에는 출처(한국장학재단)와 원문 링크를 표시합니다.

## golden/

골든셋 30건(`<source>_<id>.input.json` + `.expected.json`). 구성과 라벨링 기준은 [golden/README.md](golden/README.md)를 보세요.
