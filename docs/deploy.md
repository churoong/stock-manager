# 배포 안내

- 웹앱을 Vercel에 올려 폰에서 접속할 수 있게 하는 절차다
- 전부 무료이고 카드가 필요 없다
- 저장소는 프라이빗이어야 한다. 시세 데이터를 공개하면 한국거래소 약관 위반이다

---

## 1. Vercel 프로젝트 만들기

1. https://vercel.com 에 GitHub 계정으로 로그인한다
2. **Add New** → **Project**
3. `churoong/stock-manager` 저장소를 고른다. 프라이빗 저장소도 보인다
4. **Root Directory** 를 `web` 으로 바꾼다. 이게 핵심이다. 저장소 루트에는 파이썬 배치가 있어 그대로 두면 빌드가 실패한다
5. Framework Preset 은 Next.js 로 자동 인식된다. 건드리지 않는다
6. 아직 **Deploy 를 누르지 말고** 환경변수부터 넣는다

## 2. 환경변수 넣기

**Environment Variables** 에 아래 다섯 개를 넣는다. 값은 로컬 `web/.env.local` 에 있는 것과 같다.

| 이름 | 값을 어디서 가져오나 |
|---|---|
| `AUTH_SECRET` | `web/.env.local` |
| `APP_PASSWORD` | `web/.env.local` |
| `TURSO_DATABASE_URL` | `web/.env.local` |
| `TURSO_AUTH_TOKEN` | `web/.env.local` |
| `TELEGRAM_BOT_TOKEN` | `web/.env.local` |
| `TELEGRAM_CHAT_ID` | `web/.env.local` |

**붙여넣을 때 값이 잘리지 않았는지 확인한다.** Turso 토큰은 348자로 길다. Step 0에서 잘린 토큰 때문에 클라우드만 401이 나는 일을 실제로 겪었다.

환경은 Production, Preview, Development 전부에 적용한다.

## 3. 배포하고 확인하기

**Deploy** 를 누른다. 2~3분이면 끝난다.

### 배포 주소

| 환경 | 주소 | 무엇이 배포되나 |
|---|---|---|
| 프로덕션 | https://<웹앱 주소> | `main` 브랜치 |
| 프리뷰 | 브랜치마다 Vercel 이 따로 만든다 | 그 브랜치 |

주요 화면은 이렇게 연다. 로그인하지 않으면 전부 로그인 화면으로 보내진다.

| 화면 | 주소 |
|---|---|
| 오늘의 추천 (첫 화면) | https://<웹앱 주소>/recommend |
| 종목 찾기 | https://<웹앱 주소>/screener |
| 설정 | https://<웹앱 주소>/settings |

> 2026-09-17 사용자 최종 확정. 앞서 다른 주소를 적었으나
> 사용자가 Vercel 도메인 설정에서 이 주소로 바꾸기로 했다. 옛 주소가 보이면 이 주소로 고친다.
> 저장소가 프라이빗이므로 주소를 여기 적어도 된다. 다만
> **주소를 남에게 알려주지 않는다** (4절).

주소가 나오면 폰 브라우저로 접속해 확인한다.

1. 로그인 화면이 뜨는가
2. 비밀번호를 넣으면 설정 화면으로 가는가
3. 값을 바꾸고 저장하면 저장되는가
4. 텔레그램 테스트 버튼이 동작하는가
5. 로그아웃 후 주소에 `/settings` 를 직접 쳐도 로그인 화면으로 돌아오는가
6. **홈으로 들어가면 `/recommend` 추천 화면이 뜨는가** (2026-09-16 추가)
7. 추천 화면에서 기간별로 나뉘어 보이고, 근거 문장에 실제 수치가 있는가

5번이 중요하다. 인증이 실제로 막고 있는지 확인하는 것이다.

**비밀번호는 짧다.** 사용자가 기억하기 쉬운 값을 원해 길이 대신 잠금 장치로 막는다. 같은 IP에서 15분에 5번, 전체로 1시간에 20번 틀리면 로그인이 잠긴다 (`web/lib/loginGuard.ts`). 로그인은 30일 유지된다.

## 4. 알아 둘 것

**주소를 남에게 알려주지 않는다.** 로그인이 막고 있지만, 한국거래소 약관은 데이터를 제3자에게 제공하는 것 자체를 금지한다.

**검색엔진에는 잡히지 않는다.** `robots.txt` 와 응답 헤더 양쪽으로 막아 뒀다. `robots.txt` 는 크롤러가 쿠키 없이 읽어야 하므로 로그인 문지기(`web/proxy.ts`)의 예외다 — 2026-09-26 까지는 로그인 뒤에 있어서 크롤러가 받는 것이 로그인 화면으로 가는 307 이었다(docs/infra.md 25.199).

**무료 플랜은 비상업 용도다.** 이 앱이 그에 해당한다.

**비밀번호를 바꾸려면** Vercel 환경변수의 `APP_PASSWORD` 를 고치고 재배포한다. `AUTH_SECRET` 을 바꾸면 기존 로그인이 전부 풀린다.

---

## 나중에 할 것

**장중 모니터링 경로**(Step 14)를 붙이면 cron-job.org 가 이 웹앱의 주소를 부르게 된다. 그때 `CRON_SECRET` 을 환경변수에 추가한다. (2026-09-17 붙임. 등록 방법은 docs/intraday.md 4장)
**`DART_API_KEY` 도 함께 넣는다** (2026-09-30, docs/infra.md 25.720) — 장중 경로가 보유 종목 신규 공시(트리거 e)를 DART 로 본다. 없으면 그 알림이 꺼지고
장중 호출 기록에 "DART_API_KEY 없음" 이 오류로 남는다. 값은 Actions 시크릿과 같다.

**무응답 감시**(Step 17, 2026-09-17 붙임)도 같은 `CRON_SECRET` 을 쓴다. cron-job.org 에 작업 하나를 더 등록한다.

| 작업 | 주소 | 시각 | 요일 |
|---|---|---|---|
| 무응답 감시 | `https://<앱 주소>/api/cron/health` | **매시 정각에서 몇 분 비낀 시각**(예: 매시 12분) | 매일 |

- 헤더 `x-cron-secret: <CRON_SECRET>` (주소 뒤에 붙이지 않는다)
- 텔레그램 발송을 하므로 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 가 Vercel 환경변수에 있어야 한다
- 등록 전에는 아무 알림도 오지 않는다. `/status` 화면이 "마지막 감시 호출: 아직 없음" 으로 알려 준다

**백테스트 실행**(Step 16, 2026-09-17 붙임)은 웹이 GitHub 워크플로를 깨운다. `GH_DISPATCH_TOKEN` 과 `GH_REPO` 가 Vercel 환경변수에 있어야 한다(포트폴리오 재계산과 같은 것을 쓴다).

권한은 이 저장소로만 제한한 fine-grained 토큰으로 두고, 둘 중 **하나만** 있으면 된다.

| 권한 | 웹이 쓰는 길 |
|---|---|
| Actions: read and write | `workflow_dispatch` — 시장·기간·종목 수를 그대로 넘긴다 (먼저 시도) |
| Contents: read and write | `repository_dispatch` — 파라미터를 `client_payload` 로 넘긴다. 포트폴리오 재계산이 이미 쓰는 길 |

웹은 앞엣것을 먼저 부르고 403·404(권한 없음)일 때만 뒤엣것을 부른다. 둘 다 실패하면 화면에 이유가 뜨고, GitHub Actions 에서 직접 돌리면 된다. 지금 토큰에 어느 권한이 들어 있는지는 저장소에서 알 수 없다 `[확인필요]`.

---

## cron-job.org 등록 (정시 실행)

GitHub Actions 예약 실행은 지연되거나 건너뛰는 일이 있다(`docs/infra.md` 1절). 그래서
**시각이 중요한 일일 배치 두 개만** cron-job.org 가 GitHub 에 "지금 돌려라" 신호를 보낸다.
주간 배치(유니버스·재무·지표·점수·신호)는 몇 시간 밀려도 괜찮으므로 Actions 예약에 맡긴다.

Actions 예약은 예비로 그대로 둔다. 둘 다 도착해도 배치가 같은 날 성공 기록을 보고
두 번째는 건너뛴다.

### 1. GitHub 토큰 만들기

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. 이름 `cron-job-dispatch`, 만료 1년 (만료일을 달력에 적어 둔다. 만료되면 배치가 조용히 안 돈다)
3. Repository access → **Only select repositories** → `churoong/stock-manager`
4. Permissions → Repository permissions → **Contents: Read and write** 만 켠다
   (`repository_dispatch` 에 필요한 권한이 이것이다. 다른 권한은 주지 않는다)
5. 만든 토큰은 이 화면에서 한 번만 보인다. 바로 cron-job.org 에 붙여 넣는다.
   채팅·메모·저장소에 적지 않는다

### 2. 작업 두 개 만들기

cron-job.org → Dashboard → **Create cronjob**. 두 작업의 공통 설정이다.

| 항목 | 값 |
|---|---|
| URL | `https://api.github.com/repos/churoong/stock-manager/dispatches` |
| 요청 방식 (Advanced) | `POST` |
| 헤더 `Authorization` | `Bearer <1번에서 만든 토큰>` |
| 헤더 `Accept` | `application/vnd.github+json` |
| 헤더 `X-GitHub-Api-Version` | `2022-11-28` |
| 헤더 `Content-Type` | `application/json` |
| 실패 알림 | 켠다 |

작업마다 다른 것은 시각과 본문이다.

| 작업 | 시간대 | 시각 | 요일 | 요청 본문 |
|---|---|---|---|---|
| 국내 일일 | `Asia/Seoul` | **08:27** | 월~금 | `{"event_type":"daily-kr"}` |
| 미국 일일 | `America/New_York` | **08:27** | 월~금 | `{"event_type":"daily-us"}` |

- **국내를 08:00 으로 걸지 않는다.** 한국거래소가 전일 시세를 08:00 전후에야 내놓는다(`docs/infra.md` 16.1절)
- 미국은 시간대를 뉴욕으로 두면 서머타임이 저절로 반영된다. 개장(09:30 ET) 63분 전이다
- **국내 09:35 예비는 cron-job.org 에 걸지 않아도 된다.** Actions cron(`35 0 * * 1-5`)이 이미 건다 — D1 하루 읽기 한도가 풀리는 09:00 KST 뒤에 한 번 더 돌아, 08:27 실행이 전날 한도 때문에 건너뛴 날만 "늦은 리포트" 를 낸다(infra 25.870). 성공한 날은 곧바로 끝난다
- 휴장일에도 신호는 가지만 배치가 휴장일을 판단해 아무것도 하지 않는다. 요일만 거르면 된다

### 3. 확인

1. 작업 화면의 **Test run** 으로 한 번 보낸다. 응답이 **204** 면 성공이다
   - 401: 토큰이 틀렸거나 만료 / 404: 저장소 이름 오타이거나 토큰에 저장소 권한이 없다
2. GitHub → Actions 에 `repository_dispatch` 로 시작한 실행이 뜨는지 본다
3. 배치는 누르는 시각에 따라 다르게 반응한다. 국내 기준 개장 10~100분 전(07:20~08:50 KST)에 누르면 **실제로 돌아 텔레그램 리포트가 간다.** 그보다 이르면 "아직 이릅니다" 로 건너뛴다.
   그보다 늦어도 **장 마감 전이면 "늦은 실행" 으로 돌아 머리에 "⚠️ 개장 N분 뒤에 만든 늦은 리포트입니다" 를 단 리포트가 간다**(2026-10-01 사용자 결정, infra 25.845 — 예전에는 "너무 늦었습니다" 로 그날을 건너뛰어 손절 플래그까지 빠졌다).
   장이 끝난 뒤면 건너뛴다. 같은 거래일에 이미 성공했으면 중복으로 건너뛴다. 모두 정상이다

**등록 완료 (2026-09-17)**: 국내 작업 Test run 204. 08:43 KST 에 눌러 실행 `35163554690` 이 실제로 돌았고(2,763종목, 텔레그램 발송), 3분 뒤 두 번째 `35163790253` 은 중복으로 건너뛰었다. 중복 방지도 함께 확인됐다

**미국 작업 등록 완료 (2026-09-17)**: 첫 Test run 은 204 인데 실행이 생기지 않았다. **GitHub 은 `event_type` 이 어느 워크플로와도 맞지 않아도 204 를 준다.** 본문을 고치자 실행 `35164936961` 이 생겼고, 미국 장 마감 뒤라 "이미 개장했습니다" 로 건너뛰었다(정상). **204 만 보고 끝내지 말고 Actions 에 실행이 생겼는지 확인한다**
