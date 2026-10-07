# stock-manager

국내(KOSPI·KOSDAQ)·미국(NYSE·NASDAQ) 주식을 재무·밸류에이션·기술적 지표·뉴스 감성으로 점수화하고,
매 거래일 장 시작 전에 추천 리포트를 텔레그램으로 보내며, 장중에는 보유·관심·추천 종목의 변화를 알린다.

**개인 투자자 1명을 위한 비상업 앱이다.** 상용 서비스가 아니고, 자동 매매가 없으며, 운영 비용은 0원이다.
매수 신호와 매도 플래그는 전부 규칙 기반이고, 모든 추천은 **왜 추천했는지를 화면에서 펼쳐 확인할 수 있어야 한다**(근거표).

> **규칙의 최상위 문서는 [`CLAUDE.md`](CLAUDE.md) 다.** 비용·데이터 이용 조건·보안·작업 방식이 거기 있고,
> 이 README 와 어긋나면 `CLAUDE.md` 가 이긴다.

---

## 1. 무엇으로 돌아가나

| 층 | 무엇 | 어디서 |
|---|---|---|
| 배치 | Python 3.12 (수집·점수·신호·리포트·백테스트) | GitHub Actions |
| DB | Turso 무료 플랜 (SQLite 호환) | 클라우드 |
| 웹앱 | Next.js + TypeScript + Tailwind | Vercel |
| 정시 트리거 | cron-job.org → `repository_dispatch` / 웹 경로 | 외부 |
| 알림 | 텔레그램 봇 | |

무거운 계산은 전부 Python 에서 한다. **웹앱은 계산하지 않고** 배치가 저장한 값을 읽어 그린다.
스키마의 단일 정의처는 `migrations/` 의 SQL 이다.

## 2. 디렉터리

```
batch/      수집·계산·발송 (core 공통, sources 외부 API, services 순수 계산, jobs 실행 단위)
web/        Next.js 앱 (app 화면·API, lib 질의와 순수 함수, components 화면 조각)
docs/       규칙과 계산식의 단일 정의처. 코드보다 이쪽이 먼저다
migrations/ 번호순 SQL. batch.core.db.apply_migrations 가 적용한다
scripts/    운영 도구 (DB 상태 확인, 백업)
tests/      pytest. API 키 없이 오프라인으로 돈다
```

## 3. 처음 받아서 돌리기

저장소를 새로 받은 사람이 **시크릿만 채우면** 동작하는 절차다.

```bash
git clone <저장소>
cd stock-manager

# 1) 파이썬 (3.12)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 키 채우기 — 무엇을 어디서 받는지는 docs/api-keys.md
cp .env.example .env     # 값만 채운다. .env 는 커밋되지 않는다

# 3) 스키마 만들기 (비어 있는 Turso DB 에 표를 만든다)
python -m batch.jobs.migrate

# 4) 테스트 — 키 없이 통과해야 한다
OFFLINE_MODE=1 pytest tests/ -q

# 5) 웹
cd web && npm ci && npm test && npm run dev    # http://localhost:4310
```

웹앱 배포와 환경변수는 [`docs/deploy.md`](docs/deploy.md), 외부 크론 등록은 같은 문서와
[`docs/intraday.md`](docs/intraday.md) 4장·[`docs/health.md`](docs/health.md) 1장에 있다.

### 자주 쓰는 명령

```bash
pytest tests/ -v                              # 배치 테스트
python -m batch.jobs.daily --market KR        # 일일 배치 수동 실행
python -m batch.jobs.migrate                  # 마이그레이션 적용
python scripts/db_status.py                   # 운영 DB 상태 (읽기 전용)
python scripts/backup_db.py --scope essential # 백업 (docs/backup.md)
cd web && npm run dev                         # 웹 개발 서버
cd web && npm test && npm run typecheck       # 웹 테스트·타입 검사
```

## 3.1 폰에 앱으로 설치하기

아이폰은 사파리에서 공유 → **홈 화면에 추가**, 안드로이드는 크롬 메뉴 → **앱 설치**.
아이콘이 생기고 주소창 없이 전체화면으로 열린다. 설치 전에 로그인해 두어야 한다.

네이티브 앱(앱스토어)은 개발자 계정이 연 99달러라 비용 규칙에 어긋난다. 자세한 것은
[`docs/pwa.md`](docs/pwa.md). **오프라인에서는 옛 숫자를 보여 주지 않는다** — 데이터를
폰에 남기지 않기 때문이고, 그게 더 안전하다.

## 4. 비밀값

**코드에 키를 적지 않는다.** 어디에 무엇을 넣는지는 [`.env.example`](.env.example) 이 단일 정의처다
(항목마다 `[ACTIONS]` `[VERCEL]` `[LOCAL]` 로 표시돼 있다). 요약하면:

| 곳 | 무엇 |
|---|---|
| GitHub Actions 시크릿 | `TURSO_*`, `TELEGRAM_*`, `DART_API_KEY`, `KRX_API_KEY`, `SEC_USER_AGENT` |
| Vercel 환경변수 | `TURSO_*`, `TELEGRAM_*`, `AUTH_SECRET`, `APP_PASSWORD`, `CRON_SECRET`, `GH_DISPATCH_TOKEN`, `GH_REPO` |
| 로컬 `.env` | 위의 것 중 손으로 돌릴 때 필요한 것 |

- 값을 붙여넣을 때 **잘리지 않았는지 길이로 확인한다.** 잘린 Turso 토큰 때문에 클라우드만 401 이 난 적이 있다(docs/infra.md 4절)
- 저장소는 **반드시 프라이빗**이다. 시세 데이터가 든 저장소를 공개하면 한국거래소 약관 위반이다
- `FRED_API_KEY`·`ECOS_API_KEY` 는 **아직 코드가 읽지 않는다.** 무위험수익률은 설정 화면에 직접 넣는다
- `GH_DISPATCH_TOKEN` 은 이 저장소에만 쓰는 fine-grained 토큰이고, Actions 쓰기 **또는** Contents 쓰기 중 하나면 된다(docs/deploy.md)

## 5. 무엇이 언제 도나

| 무엇 | 언제 | 어디서 |
|---|---|---|
| 일일 배치 (국내) | 매 거래일 08:27 KST | Actions (cron-job.org 가 깨움) |
| 일일 배치 (미국) | 미국 정규장 1시간 전 | Actions |
| 국내 뉴스 감성 | 매 거래일 07:40 KST | Actions |
| 장중 모니터링 | 정규장 중 5분마다 | 웹 경로 (cron-job.org) |
| 뉴스 수집 | 1분·1시간마다 | 웹 경로 (cron-job.org) |
| 무응답 감시 | 1시간마다 | 웹 경로 (cron-job.org) |
| 주간·월간 (유니버스·재무·지표·점수·신호·ETF) | 예약 | Actions |
| 백테스트·스트레스 | 수동 | Actions (웹 화면에서 요청 가능) |
| DB 백업 | 주 1회(필수)·월 1회(전체) | Actions |

배치가 **안 도는 것**은 배치가 알릴 수 없다. 그래서 웹 경로가 1시간마다 확인하고 텔레그램으로 알린다
([`docs/health.md`](docs/health.md)). 상태는 화면 아래 "시스템 상태" 링크(`/status`)에서 본다.

## 6. 문서 지도

읽는 순서로 적었다. **계산식은 코드가 아니라 문서가 단일 정의처다.**

| 문서 | 무엇 |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 최상위 규칙 (비용·데이터·보안·기록) |
| [`docs/handoff.md`](docs/handoff.md) | **지금 어디까지 왔나, 다음은 무엇인가.** 이어서 작업할 때 첫 문 |
| [`docs/design.md`](docs/design.md) | 전체 설계와 Step 목록 |
| [`docs/factors.md`](docs/factors.md) · [`metrics.md`](docs/metrics.md) · [`signals.md`](docs/signals.md) | 팩터·성과지표·매수 신호 계산식 |
| [`docs/portfolio.md`](docs/portfolio.md) · [`review.md`](docs/review.md) · [`sell_flags.md`](docs/sell_flags.md) | 매매 기록·복기·매도 플래그 |
| [`docs/backtest.md`](docs/backtest.md) · [`stress.md`](docs/stress.md) | 백테스트·스트레스 테스트 |
| [`docs/etf.md`](docs/etf.md) · [`accumulation.md`](docs/accumulation.md) | 장기 적립 |
| [`docs/sentiment.md`](docs/sentiment.md) · [`intraday.md`](docs/intraday.md) · [`stock_detail.md`](docs/stock_detail.md) | 뉴스 감성·장중 알림·종목 상세 |
| [`docs/health.md`](docs/health.md) · [`backup.md`](docs/backup.md) · [`pwa.md`](docs/pwa.md) | 무응답 감시·백업·홈 화면 앱 |
| [`docs/data-sources.md`](docs/data-sources.md) · [`api-keys.md`](docs/api-keys.md) · [`infra.md`](docs/infra.md) · [`deploy.md`](docs/deploy.md) | 데이터 출처·키 발급·운영에서 깨진 것·배포 |

## 7. 이것만은 지킨다

- **추천 근거는 DB 에 저장된 실제 수치만.** 없는 숫자를 만들지 않는다. 근거표를 못 만드는 추천은 표시하지 않는다
- **자동 매매 없음.** 매도 플래그는 표시와 알림뿐이다
- **계산 로직은 테스트 없이 커밋하지 않는다.** 테스트는 API 키 없이 돈다
- **문서를 코드와 같은 커밋에 넣는다.** 왜 그렇게 했는지, 왜 그 숫자인지를 적는다
- **모르는 것은 `[확인필요]` 로 남긴다.** 외부 API 스펙을 추측하지 않는다
- 데이터 이용 조건을 지킨다. KRX 비상업·제3자 제공 금지, yfinance 개인 사용 한정
