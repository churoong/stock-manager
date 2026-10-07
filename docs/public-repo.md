# 공개 저장소로 옮기기 — 1번 방안 안내서 (2026-10-07)

> 사용자 결정 (2026-10-07): "1번으로 진행하고싶은데 지금은 안되니깐 할 수 있을 때 다시 말해줄게."
> **1번 = 코드만 깨끗한 이력으로 새 공개 저장소에 올리고, 지금 저장소는 비공개 운영 저장소로 남긴다.**
> 까닭: 10월 1~6일에 Actions 무료 2,000분을 다 써 일일 배치가 멈췄다(docs/infra.md 25.975). 공개 저장소는 Actions 가 무료·무제한이다.
> 2번(지금 저장소를 그대로 공개)과의 비교는 대화에서 표로 정리했다 — 이력·이슈·로그를 지우지 않아도 되고, 운영 출력(리포트·보유·금액·DB 조회)을
> 계속 비공개로 볼 수 있어 1번을 골랐다.

## 0. 무엇이 공개되고 무엇이 비공개로 남나

| | 새 공개 저장소 (예: `stock-manager`) | 지금 저장소 → 비공개 운영 저장소 (예: `stock-manager-ops`) |
|---|---|---|
| 코드·문서·테스트 | ○ (이력은 새로 시작) | 옛 이력 그대로 보관 |
| Actions 실행 | ○ 여기서 돈다 (무료·무제한) | 돌리지 않는다 |
| Actions 로그 | 누구나 본다 → **운영 출력을 찍지 않는다**(`scripts/ops_tee.py`) | 옛 로그 보관 |
| 운영 출력(리포트·DB 조회 결과) | 올리지 않는다 | **이슈 #1 에 쌓인다**(`scripts/publish_output.py` 가 `OPS_REPO` 로 보낸다) |
| 시세·재무·보유 데이터 | 없다 (DB 에만 — KRX 조건과 맞다) | 없다 (DB 에만) |

준비는 **코드에 다 되어 있다**(infra 25.978). 시크릿만 넣으면 바로 이 모양으로 돈다.

## 1. 사용자가 하는 일 (PC 권장, 약 40분) — 순서대로

> 순서가 중요하다. **A(이름 바꾸기)를 B(새 저장소 만들기)보다 먼저** 해야 새 저장소가 원래 이름(`stock-manager`)을 쓸 수 있고,
> 그래야 cron-job.org 주소·Vercel 의 `GH_REPO` 를 바꾸지 않아도 된다.

### A. 지금 저장소를 운영 저장소로 (5분)
- [ ] A1. github.com/churoong/stock-manager → **Settings** → General → Repository name 을 `stock-manager-ops` 로 → Rename. **비공개 그대로 둔다**
- [ ] A2. 같은 저장소 → Settings → **Actions → General** → Actions permissions: **Disable actions** → Save (여기서는 더 돌지 않는다)
- [ ] A3. 이슈 #1 "운영 출력 (클라우드 세션용)" 이 **열려 있는지**만 본다 (닫혀 있으면 Reopen). 앞으로 운영 출력이 여기에 쌓인다

### B. 새 공개 저장소 (2분)
- [ ] B1. github.com/new → Repository name `stock-manager` · **Public** · "Add a README" 등 **아무것도 체크하지 않고** Create (빈 저장소)
- [ ] B2. 코드는 넣지 않는다 — Claude 세션이 넣는다(2장)

### C. 토큰 2개 만들기 (10분) — github.com → 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
- [ ] C1. **운영 출력용** — 이름 `ops-issues` · Expiration 1년 · Repository access: Only select → **`stock-manager-ops`** · Permissions → Repository → **Issues: Read and write** (나머지 없음) → Generate → 값을 복사해 둔다 (다시 볼 수 없다)
- [ ] C2. **배치 깨우기용** — 이름 `dispatch` · Expiration 1년 · Repository access: Only select → **새 `stock-manager`** · Permissions → **Actions: Read and write**, **Contents: Read and write** → Generate → 값 복사
  - 웹(Vercel `GH_DISPATCH_TOKEN`)과 cron-job.org 가 이 토큰으로 새 저장소의 배치를 깨운다. 옛 토큰은 옛 저장소에만 권한이 있어 새 저장소에서 403 이 난다

### D. 새 저장소 시크릿·변수 (15분) — 새 `stock-manager` → Settings → Secrets and variables → Actions
GitHub 은 시크릿 값을 다시 보여 주지 않는다. 옛 저장소에서 복사할 수 없으니 각 서비스에서 값을 다시 찾는다.

**Secrets 탭 → New repository secret** (이름은 글자 그대로)
- [ ] D1. `TURSO_DATABASE_URL` — Turso 대시보드(app.turso.tech) → DB → URL (`libsql://…`)
- [ ] D2. `TURSO_AUTH_TOKEN` — Turso 대시보드 → DB → **Create token** (새로 만들어도 된다 — 옛 토큰도 계속 쓰인다면 Vercel 값과 같게)
- [ ] D3. `KRX_API_KEY` — openapi.krx.co.kr → 마이페이지 → 인증키
- [ ] D4. `DART_API_KEY` — opendart.fss.or.kr → 인증키 관리
- [ ] D5. `TELEGRAM_BOT_TOKEN` — 텔레그램 @BotFather → `/mybots` → 봇 → API Token
- [ ] D6. `TELEGRAM_CHAT_ID` — 내 텔레그램 대화방 번호(숫자). Vercel 에서는 값이 보이지 않는다(2026-10-07 사용자 확인). 찾는 법 둘 중 하나:
  - **휴대폰으로 가장 쉽게**: 텔레그램에서 `@userinfobot` 을 검색해 대화를 시작(Start)하면 `Id: 숫자` 를 답한다. 봇과 1:1 대화방의 번호는 내 Id 와 같다(이 앱은 1:1 대화방으로 보낸다)
  - **브라우저로 찾기**: ① 텔레그램 앱에서 이 봇에게 아무 메시지("hi")를 보낸다 ② 브라우저 주소창에 `https://api.telegram.org/bot<D5 토큰>/getUpdates` 를 넣는다(`<D5 토큰>` 자리에 토큰 그대로, 꺾쇠 없이) ③ 결과 글자에서 `"chat":{"id":` 다음 숫자가 값이다(앞에 `-` 가 있으면 그것까지). 결과가 `"result":[]` 로 비면 ①을 다시 하고 새로고침
- [ ] D7. `SEC_USER_AGENT` — `이름 이메일` 한 줄 (예전에 넣은 것과 같은 모양, 이메일이 들어 있어야 한다)
- [ ] D8. ~~`D1_ACCOUNT_ID` · `D1_DATABASE_ID` · `D1_API_TOKEN`~~ — **넣지 않는다.** 9월 Turso 가 막혔던 동안 쓰던 임시 DB(Cloudflare D1)다. 10월 Turso 로 돌아왔고(복귀 워크플로 9회, 10월 읽기 대부분이 Turso), 새 저장소는 D11 을 `turso` 로 두므로 배치가 D1 을 찾지 않는다
- [ ] D9. `OPS_REPO` — `churoong/stock-manager-ops`
- [ ] D10. `OPS_TOKEN` — C1 의 토큰

**Variables 탭 → New repository variable**
- [ ] D11. `DB_BACKEND` — **`turso`** (옛 저장소는 `auto` 였다. `auto` 는 D1 을 함께 보는 모드라 D1 값이 필요하다 — D8 을 건너뛰므로 `turso` 로 고정한다. Vercel 의 값은 그대로 둔다)
- [ ] D12. `APP_URL` — 웹앱 주소 (Vercel 의 `APP_URL` 과 같은 값)

### E. Vercel (5분) — vercel.com → 프로젝트
- [ ] E1. Settings → **Git** → Disconnect → Connect Git Repository → 새 `churoong/stock-manager` 선택 (Vercel GitHub 앱이 새 저장소에 접근하도록 허용하라는 창이 뜨면 허용)
- [ ] E2. Settings → **Environment Variables** → `GH_DISPATCH_TOKEN` 을 C2 의 토큰으로 바꾼다. `GH_REPO` 는 `churoong/stock-manager` 그대로
- [ ] E3. Deployments → 가장 위 → **Redeploy** (환경변수는 다시 배포해야 들어간다)

### F. cron-job.org (5분) — console.cron-job.org → Cronjobs
- [ ] F1. **GitHub 를 부르는 작업**(URL 이 `https://api.github.com/repos/churoong/stock-manager/dispatches` 인 것 — 국내·미국 일일 배치, 따라잡기 등)마다 Edit → Advanced → Headers 의 `Authorization: Bearer …` 를 **C2 의 토큰**으로 바꾼다. URL 은 그대로
- [ ] F2. **웹앱을 부르는 작업**(장중 알림·뉴스 수집 — URL 이 웹앱 주소)은 바꿀 것 없다
- [ ] F3. 아무 GitHub 작업 하나를 **Test run** → 응답 204 면 성공 (401·403 이면 토큰 권한 확인)

### G. 마지막
- [ ] G1. Claude 세션에 "옮겼다" 고 말한다. 새 세션이면 저장소로 새 `stock-manager` 를 고른다 (세션의 GitHub 연결은 저장소마다다)

## 2. Claude 세션이 하는 일

> 2026-10-07 사용자가 A~F 를 마쳤다고 알렸다("전부 다 옮겼어"). 1·2 를 같은 날 했다 — 올리기 전에 문서·테스트의 보유 수량·금액·보유 종목 표시·웹앱 주소를 지웠다.

1. 새 저장소에 **지금 코드의 한 커밋**을 올린다 (옛 이력 없음). 올리기 전에 문서에서 개인 메모(보유 종목·금액)를 지운다.
   A 를 한 뒤에는 이 세션의 저장소 원격 주소가 옛 저장소(이름 바뀜)로 따라가므로, 새 저장소를 따로 붙여 올린다
2. CLAUDE.md "저장소는 프라이빗" 을 "코드는 공개, 데이터·운영 출력은 비공개(OPS_REPO)" 로 고친다
3. 첫 실행 확인: DB 상태 확인 한 번 → 결과가 **운영 저장소 이슈**에 오고 새 저장소 로그에는 줄 수만 찍히는지
4. 옛 저장소의 미처리 일(handoff "사용자 결정 필요")을 새 저장소 handoff 로 옮긴다

## 3. 남은 위험 — 옮긴 뒤 다시 볼 것

- `| python scripts/ops_tee.py` 를 거치지 않고 **로그에 바로 찍는 작업**을 훑었다(2026-10-07, infra 25.979). 개인 정보를 찍던 둘 —
  포트폴리오 재계산(평가액·손익·주의)과 매도 플래그(종목별 근거) — 은 공개 저장소에서 건수만 찍는다. 나머지(점수·신호·감시 대상·ETF·적립 종목)는
  건수와 **시장 데이터**(종목명·점수)만 찍는다 — 보유·금액은 없다. 시세 숫자를 대량으로 찍는 곳은 없다 `[확인필요: 옮긴 뒤 첫 실행 로그를 한 번 본다]`
- 공개 저장소의 워크플로는 남이 포크해 PR 을 열 수 있다. `pull_request_target` 은 쓰지 않는다(지금 없다). 시크릿은 포크 PR 에 넘어가지 않는다
