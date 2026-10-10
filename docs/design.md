# 설계 문서 (초안)

- 상태: 확정. Step 0 검증 완료(2026-09-16), Step 1 진행 예정
- 기준 문서: 루트 `CLAUDE.md`
- 작성일: 2026-09-16
- 원칙: 운영 비용 0원, 인증된 본인만 접근, 자동 매매 없음, 규칙 기반 신호

---

## 0. 한눈에 보기

> **2026-09-16 구조 변경**: 내 PC는 이 시스템에 포함되지 않는다. PC가 꺼져 있어도 폰으로 전부 확인할 수 있어야 한다는 요구에 따라, 배치와 화면을 모두 무료 클라우드로 옮겼다. 자세한 내용은 2장.

```
[GitHub Actions cron] --Python 배치--> [수집 → 유니버스 → 팩터 → 성과지표 → 감성 → 신호 → 매도플래그 → 리포트]
                                                       |                                    |
                                                  [클라우드 DB]                        [텔레그램]
                                                       |
                                      [Next.js 웹앱 (Vercel)] <--- 폰 브라우저 (로그인 필요)
                                                       |
[장중 모니터] --Actions 5분 cron(정규장만)--> [트리거 판정] --> [alerts] --> [텔레그램]
```

- 배치가 **유일한 분석 데이터 쓰기 주체**. 웹앱은 읽기 + 사용자 입력(설정·매매기록·관심종목)만 쓴다.
  - 2026-09-26 덧(docs/infra.md 25.195): 이 줄은 초안 때 것이다. 지금 웹앱의 크론 경로도 쓴다 — 뉴스 기사 제목·주소(`news`, 채점은 배치), 장중 알림(`alerts`), 무응답 알림(`health_alerts`), 운영 기록(`cron_heartbeats`·`api_usage`·`news_fetch_log`·`settings` 의 Turso 복귀 표시). **점수·신호·매매·보유는 여전히 배치와 사용자만 쓴다** — 경로별 목록은 `web/__tests__/cronWrites.test.ts` 가 정의처다
- 장중 모니터링은 알림만 만들고 스코어·신호를 바꾸지 않는다.
- 무거운 계산(팩터, 성과지표, 백테스트)은 전부 Actions의 Python에서 돈다. 웹앱은 계산하지 않는다.
- **웹앱은 반드시 인증 뒤에 둔다.** 2.4절 참조.

---

## 1. 확정된 결정과 남은 질문

### 1.1 확정 (2026-09-16 사용자 답변)

| # | 항목 | 결정 |
|---|---|---|
| A1 | API 키 | **발급 완료**: 텔레그램 봇, DART, KRX, FRED. `.env`에 입력됨. **FRED 는 발급만 됐고 읽는 코드가 없다**(2026-09-23, infra 25.160 — 무위험수익률은 설정 화면 수동 입력). KIS는 선택 항목으로 내려 발급하지 않는다. 6.0절 참조 |
| A2 | 실행 위치 | **전부 클라우드.** 내 PC는 시스템에서 빠진다. 배치는 GitHub Actions, 화면은 웹앱. 1.3절 참조 |
| A3 | 유니버스 | 국내는 KOSPI+KOSDAQ 중 제외 규칙 통과 종목 전체. 미국은 S&P500 + 나스닥100 합집합으로 **시작했으나 지금은 나스닥 심볼 디렉터리 전 보통주**를 같은 제외 규칙(시총 10억달러·거래대금)으로 거른다(Step 3 아래 "미국 유니버스" 절, 2026-09-27 바로잡음 infra 25.366). 1.2절 참조 |
| A4 | 투자금 통화 | **원화 단일 풀**. 미국 종목은 당일 환율로 환산해 비중과 권장 금액을 계산한다 |
| A5 | 야간 알림 | 설정 화면에서 **켜고 끌 수 있게** 한다. 끄면 조용시간에 쌓아뒀다 해제 시각에 묶어 발송 |

원화 단일 풀의 계산 규칙:

- 비중 상한(기본 10%)과 섹터 상한(기본 30%)은 **원화 환산 평가액 기준**으로 판정한다. 분모는 **총 투자가능금액**이다(보유가 그보다 크면 보유 합). 리포트 2부와 포트폴리오 화면이 같은 잣대를 쓴다 (docs/infra.md 25.238).
- 미국 종목 권장 금액은 원화로 산출한 뒤 당일 USDKRW로 나눠 달러 금액을 함께 보여준다.
- 환율이 바뀌면 비중이 변하므로, 포트폴리오 화면은 평가 시점 환율과 그 기준 시각을 항상 표시한다.
- **남은 여력은 두 리포트가 나눠 쓴다** (2026-09-28, docs/infra.md 25.508). 국내·미국 리포트는 각자 "총액 − 보유" 로 여력을 냈다 — 두 리포트대로 사면 여력보다 더 쓴다. 이제 다른 나라 리포트 가운데 **최근 20시간 안 또는 이 나라 앞 리포트 뒤**에 나온 가장 새 것의 2부 배분(보유 종목의 추가 배분 포함, 25.515)을 여력에서 빼고, 2부 머리에 "○○ 리포트 2부 배분 N만큼 여력을 줄였습니다" 라고 적는다. 아직 안 샀어도 뺀다 — 돈을 덜 쓰는 쪽이다. 섹터 사용분은 아직 나누지 않는다(업종 이름표가 나라마다 다르다, 25.508 남은 것)
- **나라를 넘는 섹터 합산은 이름이 같은 업종뿐이다** (2026-09-28, docs/infra.md 25.509). 국내 KSIC·미국 SIC 중분류는 같은 산업이어도 이름표가 다르고(반도체 "전자부품·컴퓨터·통신장비" vs "전자·전기장비"), 중분류로는 한 줄씩 대응시킬 수 없다(SIC 28 은 화학과 의약품이 섞였다). 규칙은 그대로 두고, 다른 나라 보유가 있으면 2부 섹터 줄 아래에 그 한계를 적는다. 대응표(세분류 기준)는 `[확인필요]` 로 남긴다

야간 알림 설정(`settings.quiet_hours`)의 구조:

```
{ "enabled": true, "start": "00:00", "end": "07:00",
  "deliver_on_release": true }
```
> 2026-09-30 정정 (docs/infra.md 25.768, 설정 감사): 예전 예시의 `"timezone"` 칸은 스키마에 없다 — 저장할 때 지워지고 장중 경로는 **KST 고정**이다(`web/lib/intraday.ts`).

- `enabled=false`면 밤에도 즉시 발송한다.
- `deliver_on_release=true`면 조용시간에 생긴 알림을 해제 시각에 한 통으로 묶어 보낸다. `false`면 저장만 하고 알림 센터에서만 본다.
- 조용시간에도 `alerts` 저장은 그대로 한다. 발송 여부만 달라진다.

### 1.2 유니버스 추천안

**국내: KOSPI + KOSDAQ 전체에서 `CLAUDE.md` 제외 규칙을 적용한 잔여 종목.**

상장 종목이 약 2,600개이고 여기서 관리종목·거래정지·스팩·상장 1년 미만·시총 1,000억 미만·거래대금 하한 미달을 걷어내면 대략 700~1,000개가 남습니다. 정확한 수는 Step 3에서 실측합니다. DART와 KRX는 종목당 호출이 아니라 목록 단위로 받을 수 있어 이 규모가 부담되지 않습니다.

**미국: S&P500 + 나스닥100 합집합, 약 510~520 종목으로 시작.** (처음 계획이다. 지금은 나스닥 심볼 디렉터리 전 보통주를 시총·거래대금으로 거른다 — Step 3 아래 "미국 유니버스" 절, infra 25.366)

이유는 세 가지입니다.

1. yfinance는 비공식 경로여서 호출량을 늘릴수록 차단 위험이 커집니다. 500종목은 일괄 다운로드로 한 번에 받을 수 있는 현실적인 규모입니다.
2. 시총 10억 달러 조건만 걸면 대상이 2,000종목을 넘습니다. 개인용 일일 배치로는 수집 시간과 실패율이 모두 부담입니다.
3. 두 지수 구성 종목은 시총·거래대금 하한을 이미 만족하므로 제외 규칙 대부분이 자동 충족됩니다.

**확장 경로**: Step 3에서 실제 수집 시간과 실패율을 측정한 뒤, 여유가 있으면 S&P MidCap 400을 더해 러셀1000급으로 넓힙니다. 유니버스 문턱은 `services/universe.UniverseFilters` 의 코드 상수다 — 설정 화면에서는 바꿀 수 없다(아래, 2026-09-29 정정).

**제외 문턱 — 정의처는 `batch/services/universe.UniverseFilters`** (2026-09-27 적음, docs/infra.md 25.304).
이 표 말고는 문턱 숫자가 적힌 문서가 없었다. `tests/test_doc_constants.py` 가 코드 상수와 대 본다.

| 문턱 | 국내 | 미국 | 근거 |
|---|---|---|---|
| 시가총액 하한 | 1,000억원 | 10억달러 | CLAUDE.md 기본값 |
| 20일 평균 거래대금 하한 | 5억원 | 500만달러 | CLAUDE.md 에 숫자가 없어 둔 출발값 `[확인필요: 실측 뒤 조정]` |
| 상장 경과일 하한 | 365일 | 365일 | "상장 1년 미만" 을 **달력 365일**로 센다 — 윤년을 끼면 달력상 1년보다 하루 이르게 들어온다(알고 둔 차이) |

**설정 화면에서는 바꿀 수 없다.** 코드 주석이 "설정에서 조정한다" 고 했지만 연결된 적이 없다. 바꾸려면 위 클래스를 고친다.

**뉴스 감성은 유니버스 전체에 돌리지 않습니다.** 종목당 뉴스 API 호출이 필요해 1,500종목을 매일 긁으면 어떤 무료 한도도 버티지 못합니다. 대상은 `보유 종목 ∪ 관심 종목 ∪ 시장별 점수 상위 50종목`으로 제한하고, 나머지 종목의 센티먼트는 `NULL`로 두어 종합 점수에서 센티먼트 가중치를 제외한 뒤 재정규화합니다.

### 1.3 실행 위치: 전부 클라우드로

cron-job.org를 쓰자는 제안이 먼저 있었지만, 그것은 외부 서버가 내 PC로 요청을 보내는 방식이라 PC를 인터넷에 노출해야 합니다. 이어서 **PC가 서버가 되면 안 되고, PC가 꺼져 있어도 폰으로 전부 확인할 수 있어야 한다**는 요구가 나왔습니다. 이 요구는 로컬 실행 구조 자체를 무효화합니다. 화면이 localhost에 있으면 폰에서 볼 방법이 없습니다.

따라서 내 PC를 시스템에서 완전히 뺍니다.

| 기존 | 변경 |
|---|---|
| Windows 작업 스케줄러가 배치 실행 | GitHub Actions cron이 배치 실행 |
| 로컬 SQLite 파일 | 무료 클라우드 DB |
| localhost React 화면 | 웹앱. 폰 브라우저에서 접근 |
| FastAPI 로컬 서버 | 없앤다. 웹앱이 DB를 직접 읽는다 |
| PC가 꺼지면 리포트 없음 | PC와 무관하게 동작 |

이 변경은 `CLAUDE.md`의 여러 규칙과 충돌하므로 해당 규칙을 함께 고쳤습니다. 무엇을 왜 고쳤는지는 1.6절에 정리했습니다.

**파이썬은 그대로 씁니다.** 팩터 계산, 성과 지표, 백테스트는 pandas와 numpy가 필요합니다. 이것들은 Actions 위에서 돌고, 웹앱은 결과를 읽어 보여주기만 합니다. 계산 로직과 그 검증 테스트를 다시 짤 필요가 없습니다.

### 1.4 남은 질문

| # | 질문 | 관련 |
|---|---|---|
| A6 | 텔레그램 리포트의 추천 종목 개수 상한은? (제안: 시장별 상위 5종목 + 매도 플래그는 전부) | Step 9 |
| A7 | 과거 데이터 백필 기간은? (제안: 가격 10년, 재무 5년, 백테스트 5년) | Step 3 |
| A8 | 세율·수수료 기본값을 직접 지정하시겠습니까, 아니면 `[확인필요]`로 비워두고 첫 실행 때 입력받을까요? | Step 2 |
| A10 | 보유 종목은 수동 입력으로 확정. 증권사 잔고 자동 동기화는 KIS를 빼면서 함께 제외됨 | 확정 |

### 1.5 해당 Step에서 확정할 것

| # | 질문 | 관련 Step |
|---|---|---|
| B1 | CAGR·성과지표를 배당 재투자 포함 총수익 기준으로 볼지, 가격 수익만 볼지 | Step 5 |
| B2 | 리스크 팩터 점수 방향: MDD·변동성이 작을수록 100점(안정성 점수)으로 두는 것이 맞는지 | Step 6 |
| B3 | "감성 급락" 기준값 (제안: 7일 내 센티먼트 30점 이상 하락 + 부정 기사 3건 이상) | Step 7 |
| B4 | 급등락 알림 기준 N%와 거래량 배수 N배 기본값 (제안: ±5%, 3배) | Step 14 |
| B5 | 백테스트 슬리피지 기본값(제안: 편도 0.1%)과 리밸런싱 보유 종목 수 | Step 16 |
| B6 | 로컬 LLM(Ollama)을 실제로 설치할 계획이 있는지 | Step 7 |
| ~~B7~~ | ~~인증 방식을 이메일 매직링크로 할지 구글 로그인으로 할지~~ — 2026-10-01 사용자 결정: 비밀번호 하나(지금 구현)로 확정, CLAUDE.md 문구를 맞춤 | Step 2 |

### 1.6 `CLAUDE.md`에서 고쳐야 하는 규칙

클라우드 전환은 기준 문서의 전제 몇 가지를 무효화합니다. 임의로 어기지 않고 문서를 함께 고칩니다.

| 기존 규칙 | 문제 | 수정 |
|---|---|---|
| 앱은 localhost 전용, 외부 노출 설정 금지 | 폰에서 보려면 성립 불가 | **인증된 본인 계정만 접근 가능.** 로그인 없이는 어떤 데이터도 반환하지 않고, 허용 계정을 내 계정 하나로 고정. 회원가입 없음. 저장소는 프라이빗 |
| SQLite만 사용, 서버 DB 없음 | 배치와 웹앱이 데이터를 공유해야 함 | **무료 클라우드 DB 사용.** 후보와 한도는 조사 결과로 확정 |
| 실행: 내 PC에서 로컬 실행 | PC를 시스템에서 뺌 | **배치는 GitHub Actions, 화면은 웹앱** |
| 백엔드 FastAPI + APScheduler | 상주 서버와 스케줄러가 없어짐 | **배치는 Python 단발 실행**, 스케줄은 Actions cron. FastAPI와 APScheduler 제거 |
| 프론트 React + Vite | 웹 배포가 필요 | **Next.js + TypeScript + Tailwind** |
| 파일 구조 `/backend /frontend` | 역할이 바뀜 | `/batch /web /docs /tests /scripts /.github/workflows` |
| API 키는 `.env`만 사용 | 클라우드에서는 `.env` 파일이 없음 | **Actions 시크릿과 웹 호스팅 환경변수**에 저장. 로컬 개발용으로만 `.env` 유지. 하드코딩 금지는 그대로 |

**바뀌지 않는 것**을 분명히 해 둡니다. 운영 비용 0원, 자동 매매 없음, 규칙 기반 신호, 실제 수치만 인용, 테스트 없이 커밋 금지, 책임 고지 표시, 한국어 문서, 무료 한도 확인 전 사용 보류는 그대로입니다.

---

## 2. 아키텍처

### 2.0 왜 바뀌었나

원래 설계는 내 PC에서 파이썬 배치와 로컬 화면을 돌리는 구조였다. 두 가지 요구가 이를 무효화했다.

1. PC가 서버가 되면 안 된다. 인터넷이 자주 끊긴다
2. **PC가 꺼져 있어도 폰으로 전부 확인할 수 있어야 한다**

두 번째가 결정적이다. 화면이 localhost에 있으면 폰에서 볼 방법이 없다. 따라서 배치와 화면을 모두 클라우드로 옮긴다. 대신 무료 범위를 벗어나지 않아야 하고, 아래 2.4절의 이용약관 제약을 반드시 지켜야 한다.

### 2.1 구성 요소

| 구성 | 플랫폼 | 역할 |
|---|---|---|
| 일일 배치 | GitHub Actions (Python) | 수집, 유니버스, 팩터, 성과지표, 감성, 신호, 매도 플래그, 리포트, 텔레그램 발송 |
| 배치 트리거 | cron-job.org → `repository_dispatch` | Actions cron은 지연·누락이 잦다. 외부에서 정시에 깨운다 |
| 장중 모니터 | cron-job.org → Vercel API 경로 | 정규장에만 5분 간격 호출. 가벼운 시세 확인과 알림 판정만 |
| 백테스트 | GitHub Actions `workflow_dispatch` | 웹에서 요청하면 워크플로를 깨워 Python으로 실행, 결과를 DB에 저장 |
| 데이터베이스 | Turso 무료 플랜 | 배치와 웹앱이 공유하는 단일 저장소 |
| 웹앱 | Next.js on Vercel | 13개 화면. 폰과 PC 브라우저에서 접근. 무거운 계산은 하지 않는다 |
| 알림 | 텔레그램 봇 | 리포트와 장중 알림 |

**장중 모니터링을 Actions에 두지 않는 이유**는 무료 한도 때문이다. 5분 간격이면 두 시장 합쳐 하루 156회이고, Actions는 짧은 작업도 1분으로 계산한다. 한 달 4,680분이 되어 프라이빗 저장소 무료 한도 2,000분을 두 배 넘게 초과한다. 반면 장중 확인은 보유·관심·추천 종목 수십 개의 현재가를 보고 임계값과 비교하는 가벼운 일이라 pandas가 필요 없다. 웹앱의 API 경로에 두고 외부 크론이 호출하는 편이 맞다.

**cron-job.org를 이렇게 쓰는 것은 안전하다.** 처음 검토했을 때 문제가 된 것은 내 PC를 인터넷에 열어야 한다는 점이었다. 지금은 호출 대상이 이미 인터넷에 있는 서비스라 그 문제가 없다. 호출 경로는 비밀 토큰으로 보호한다.

**계산은 전부 Python에 남긴다.** 팩터 z-score, CAGR·MDD·샤프, 백테스트는 pandas와 numpy가 필요하다. 이를 웹 런타임으로 옮기면 검증 테스트를 다시 짜야 하고 실행 시간 제한에 걸린다. Actions는 실행 시간이 넉넉하고 파이썬 생태계를 그대로 쓴다.

**웹앱은 화면만 담당한다.** DB를 읽어 렌더링하고, 사용자 입력을 DB에 쓴다. 무거운 계산을 요청받으면 Actions 워크플로를 깨우고 결과를 기다린다.

### 2.2 저장소 구조

```
/batch          Python. GitHub Actions에서 실행
  jobs/         daily_kr, daily_us, intraday, weekly_universe, backtest
  sources/      외부 데이터 어댑터
  services/     universe, factors, metrics, sentiment, signals, sizing,
                sell_flags, report, portfolio, backtest, review
  notify/       telegram, formatter
  core/         calendar, ratelimit, cache, db, errors
  models/       SQLAlchemy 모델 (스키마의 단일 정의처)
/web            Next.js + TypeScript + Tailwind
  app/          라우트별 화면
  lib/db.ts     DB 클라이언트
  lib/auth.ts   인증
  components/   차트, 표, 카드
/docs
/tests          pytest. batch를 대상으로 한다
/scripts
/.github/workflows
  daily-kr.yml  daily-us.yml  intraday.yml  backtest.yml  weekly-universe.yml
```

**스키마의 단일 정의처는 `migrations/` 의 SQL 파일이다.** 웹앱도 배치도 스키마를 따로 선언하지 않는다. 두 언어가 각자 스키마를 선언하면 반드시 어긋난다.

> **2026-09-23 정정.** 여기 "Python 쪽 SQLAlchemy 모델이 단일 정의처이고 마이그레이션은 Alembic 으로 돌린다" 고 적혀 있었다. **둘 다 끝내 만들지 않았다** — SQLAlchemy 는 어디서도 import 하지 않고, Alembic 은 Turso 용 드라이버가 낡아 동작하지 않는다(docs/infra.md 2번). CLAUDE.md 는 2026-09-20 에 고쳤는데 이 문서는 넉 달 동안 **다른 정의처를 가리키고 있었다**. 실제 적용기는 `batch/jobs/migrate.py` 이고, 번호 순서로 적용하며 `schema_migrations` 에 이력을 남긴다 (규칙과 그물은 docs/infra.md 25.168).

### 2.3 배치 레이어

```
batch/
  config.py          환경변수 로딩, 오프라인 모드 플래그
  core/db.py         DB 연결·세션
  models/            SQLAlchemy 모델
  sources/           외부 데이터 어댑터
  services/
    universe.py      유니버스 구성·제외 사유 기록
    factors.py       5팩터 계산·정규화
    metrics.py       CAGR·MDD·샤프·소르티노·베타 (순수 함수)
    sentiment.py     감성 사전 채점·시간 감쇠 합산
    signals.py       기간별 매수 신호, 매수 구간, 분할 계획
    sizing.py        권장 금액·비중 상한·변동성 축소
    sell_flags.py    녹·적·황 플래그 판정
    report.py        일일 리포트 생성 + 근거 문장 템플릿
    portfolio.py     평가액·실현손익·환차손익·집중도
    backtest.py      리밸런싱 시뮬레이터
    review.py        매매 복기 통계
  notify/
    telegram.py      발송 (재시도·길이 분할)
    formatter.py     리포트·알림 메시지 포맷
  jobs/
    daily_kr.py  daily_us.py  intraday.py  weekly_universe.py  backtest.py
  core/
    calendar.py      거래소 캘린더·휴장일
    ratelimit.py     api_usage 카운터, 80% 경고 / 100% 중단
    cache.py         응답 캐시 + 폴백
    errors.py
```

### 2.4 접근 제어: 선택이 아니라 의무

**웹앱을 인터넷에 열면 이용약관 위반이 된다.** 조사에서 확인된 조항이다.

| 소스 | 조항 |
|---|---|
| KRX Open API | 비상업 목적으로만 이용 가능. 제공받은 정보를 **제3자에게 제공할 수 없음** |
| DART | 정확성·완전성 미보장. 재배포 금지 조항은 없으나 공개 배포는 하지 않는다 |
| yfinance | 데이터는 개인 사용 한정 |

누구나 열 수 있는 주소에 시세와 점수를 띄우면 제3자 제공에 해당한다. 따라서 다음을 강제한다.

- **로그인 없이는 어떤 데이터도 반환하지 않는다.** 화면뿐 아니라 API 경로 전부에 적용한다
- 허용 계정은 **내 계정 하나**로 고정한다. ~~허용 이메일 목록을 환경변수에 두고, 목록에 없으면 로그인 자체를 거부한다~~ → **비밀번호 하나(`APP_PASSWORD`)·시도 제한·서명 쿠키(`AUTH_SECRET`)** 로 확정 (2026-10-01 사용자 결정, `web/lib/auth.ts`)
- 회원가입 기능을 만들지 않는다
- 검색엔진 색인을 막는다
- 저장소는 **프라이빗**으로 둔다. 시세 데이터가 들어간 저장소를 공개하면 같은 문제가 된다

~~인증 방식은 Step 2에서 확정한다. 후보는 이메일 매직링크와 구글 로그인이고, 둘 다 단일 허용 이메일로 제한한다.~~ 비밀번호 방식으로 확정했다(위).

### 2.5 무료 한도 안에서 돌리기

조사로 확인한 수치와 그에 맞춘 계획이다. 상세 근거는 `docs/infra.md`에 있다.

**GitHub Actions** — 프라이빗 저장소 월 2,000분, 리눅스 러너는 1배 소모, 카드 불필요. 저장소는 프라이빗이어야 한다. 시세 데이터가 든 저장소를 공개하면 제3자 제공 금지 조항에 걸린다.

| 용도 | 빈도 | 월 소모 추정 |
|---|---|---|
| 국내 일일 배치 | 거래일 1회, 약 10분 | 약 220분 |
| 미국 일일 배치 | 거래일 1회, 약 10분 | 약 210분 |
| 주간 유니버스 갱신 | 주 1회, 약 10분 | 약 40분 |
| 백테스트 | 요청할 때만 | 가변 |
| **합계** | | **약 470분 + 백테스트** |

여유가 1,500분쯤 남는다. 백테스트를 자주 돌려도 한도 안에 들어온다.

**Turso** — 무료 플랜에 카드가 필요 없다. 스토리지 5GB, 월 읽기 5억 행, 월 쓰기 1천만 행, DB 100개. 이 앱의 규모로는 넉넉하다. Step 0에서 연결과 읽기·쓰기를 확인했다.

**접속은 공식 파이썬 라이브러리 대신 HTTP 경로를 직접 쓴다.** `libsql-client`가 401을 돌려주는데 같은 자격 증명으로 `/v2/pipeline`을 부르면 200이 온다. 해당 패키지는 2024-05 이후 배포가 없다. `batch/core/turso.py`가 `requests`만으로 호출하며 추가 의존성이 없다. 값에 타입이 붙어 오가고 정수가 문자열로 실려 오므로 변환을 테스트로 고정했다.

- **주의: 무료 플랜은 10일간 활동이 없으면 DB가 보관 상태로 넘어간다.** 매일 배치가 돌면 문제없지만, 배치가 멈추면 DB까지 잠기는 이중 장애가 된다. 2.6절의 무응답 감시가 이것도 막아 준다.

**Actions cron의 정시성 문제** — 공식 문서가 부하 시간대에 지연될 수 있다고 인정하고 있다. 실제로 수십 분에서 몇 시간 지연되거나 **알림 없이 건너뛰는 사례**가 보고된다. 장 시작 전 리포트에는 치명적이다. 그래서 정시성이 필요한 배치는 **cron-job.org가 `repository_dispatch`로 깨우고**, Actions cron은 그것이 실패했을 때를 위한 예비로만 둔다.

**60일 자동 비활성화** — 저장소에 활동이 없으면 예약 워크플로가 꺼진다. 공식 문서는 퍼블릭 저장소를 명시하고 프라이빗 적용 여부는 `[확인필요]`다. 배치가 매일 도는 한 문제되지 않지만, 예비 경로로만 남긴 cron이 조용히 꺼질 수 있으므로 무응답 감시를 신뢰한다.

**Vercel** — 장중 모니터링 API 경로와 웹앱을 올린다. 무료 플랜은 비상업 용도이고 이 앱이 그에 해당한다. 함수 실행 시간 제한이 있으므로 장중 경로는 수십 종목 시세 확인과 임계값 비교만 한다 `[확인필요: 무료 플랜 함수 실행 시간 상한]`.

**cron-job.org** — 장중 5분 호출과 일일 배치 트리거. 무료 한도는 `[확인필요]`지만 다른 프로젝트에서 쓰고 있어 동작은 확인됐다.

### 2.6 남아 있는 위험

**위험 1: 야후 파이낸스가 클라우드 IP에서 막힐 수 있다 — 2026-09-16 검증 통과.**

Step 0에서 GitHub Actions로 실제 호출해 확인했다. 2종목 일괄 다운로드가 1.2초에 끝났고 429가 없었다. 15분 안에 두 번 실행했는데 둘 다 통과했다. 러너 바깥 주소는 Azure 대역이었다.

**통과했다고 완화책을 빼지 않는다.** 차단은 시점과 러너 주소에 따라 달라질 수 있다. 일괄 다운로드, 호출 간격, 429 재시도, 전일 데이터 폴백을 모두 유지하고, 배치가 자리를 잡으면 실패율을 누적해 관찰한다.

**위험 2: 국내 장중 시세를 얻을 경로가 야후 파이낸스뿐이다.**

한국투자증권을 뺐으므로 국내 장중 현재가는 야후 파이낸스에 전적으로 의존한다. 위험 1이 현실화되면 **국내와 미국 장중 알림이 동시에 무너진다.** 일별 배치는 한국거래소 경로가 살아 있어 국내 쪽이 버티지만, 장중 기능은 통째로 멈춘다.

지연 폭도 확인되지 않았다 `[확인필요]`. 목표가 터치 알림이 얼마나 늦는지는 Step 0에서 실측한다.

대응은 이렇다. 장중 알림은 **없어도 나머지가 도는 독립 기능**으로 설계한다. 소스가 막히면 그 기능만 비활성화하고 화면에 사유를 표시한다. 일일 리포트는 영향을 받지 않는다.

**나머지 위험**

| 위험 | 대응 |
|---|---|
| 시크릿이 Actions와 Vercel 양쪽에 필요 | 각 플랫폼의 시크릿 저장소에만 둔다. 저장소에 커밋하지 않는다 |
| 백테스트 실행 시간 | `workflow_dispatch`로 비동기 실행. 웹은 진행 상태만 폴링 |
| 배치가 조용히 멈춤 | 정해진 시각까지 성공 기록이 없으면 알리는 무응답 감시. Turso 10일 보관 전환도 함께 막는다 |
| Turso 무료 플랜 정책 변경 | 스키마를 표준 SQL로 유지해 다른 무료 DB로 옮길 수 있게 한다. 대안은 Cloudflare D1 |

**어댑터 규약** (`sources/`): 모든 어댑터가 같은 인터페이스를 따른다.

```
fetch(...) -> (data, meta)
meta = {source, fetched_at, from_cache: bool, limit_state: ok|warn|blocked|unknown}
```

- 어댑터는 `limit_state`(ok/warn/blocked/unknown)를 돌려주고, 잡이 호출 수를 `db.record_api_call`·`record_and_guard`(`api_usage` 표)로 센다 — 80% 경고, 100% 에서 멈춤. 별도의 `ratelimit` 모듈은 없다 (2026-09-29 정정, docs/infra.md 25.606).
- `OFFLINE_MODE=1`이면 네트워크를 타지 않고 `tests/fixtures`를 읽는다. 테스트는 이 모드로 돈다.

어댑터 목록 (`batch/sources/`, 2026-09-29 실제 파일과 맞춤): `dart.py`(재무·고유번호·업종) `dart_disclosures.py`(공시 목록) `dart_dividends.py`(배당) `dart_insider.py`(내부자) `krx.py`(종목마스터·일별 시세·ETF 시세) `yfinance_src.py`(해외 시세·국내 폴백·지수·환율·실적일) `yahoo_fund.py` `sec_edgar.py`·`sec_facts.py`(미국 재무·주식수·업종) `nasdaq_symbols.py`(미국 종목 목록). **뉴스는 배치 어댑터가 아니라 웹 경로**(`web/lib/news.ts`·`newsKr.ts`)가 언론사 RSS 로 받는다. `kis.py` `naver_news.py` `google_news_rss.py`(금지) `yahoo_news.py` `ecos.py` `fred.py` 는 만들지 않았다

### 2.7 일일 배치 파이프라인

각 단계는 독립 함수이고 `batch_runs`에 단계별 성공·실패를 기록한다. 한 단계가 실패해도 다음 단계는 **직전 성공 데이터**로 진행하되, 리포트에 "해당 데이터는 N일 기준" 경고를 붙인다.

```
1  휴장일 확인        휴장이면 즉시 종료 (기록만 남김)
2  가격 갱신          전일 종가·거래량 → prices
3  재무·공시 갱신     신규 공시분만 → financials(+PIT 스냅샷), disclosures
4  환율·무위험수익률  fx_rates, risk_free_rates
5  성과 지표          performance_metrics (1/3/5년)
6  뉴스 감성          news → sentiment_scores (하루 1회)
7  팩터 계산          factors (시장·업종 z-score → 0~100)
8  종합 점수          5팩터 가중 + 센티먼트 가중(기본 10%) → scores
9  매수 신호          signals (단기·중기·장기) + 매수 구간 + 분할 계획
10 매도 플래그        보유 종목 대상 sell_flags
11 리포트 생성        daily_reports + report_items (근거 문장)
12 텔레그램 발송      결과를 daily_reports.sent_at에 기록
```

### 2.8 장중 모니터링

- 대상: 보유 종목, 관심 종목, 당일 추천 종목의 합집합. 목록은 배치 종료 시 확정해 캐시한다.
- 5분 폴링. 정규장 시간 밖에는 job 자체가 뜨지 않는다.
- 트리거 판정 후 `alerts` 삽입 시 `(stock_id, trigger_type, trade_date)` 유니크 제약으로 **하루 1회**를 DB가 보장한다.
- 조용시간(A9)이 설정돼 있으면 저장은 하되 발송은 보류하고, 해제 시각에 묶어 보낸다.

### 2.9 시간대 처리

- DB 저장은 전부 **UTC**. 화면·리포트 표시만 KST·ET로 변환한다.
- 미국 배치는 `zoneinfo("America/New_York")`로 "정규장 09:30 ET의 1시간 전"을 계산해 서머타임을 자동 반영한다. 고정 KST 시각을 쓰지 않는다.
- 거래일 기준 컬럼(`trade_date`)은 해당 시장의 현지 날짜를 쓴다.

---

## 3. DB 스키마

SQLite. 공통 규칙:

- 외부 수집 테이블에는 `source TEXT NOT NULL`, `fetched_at TIMESTAMP NOT NULL`
- 금액 컬럼은 `currency` 동반. 재무는 `unit`(원·천원·백만·USD)과 `accounting_standard`(K-IFRS·US-GAAP) 동반
- 시계열 테이블은 `(stock_id, date)` 유니크 + 인덱스
- 스키마 변경은 번호 붙은 `migrations/NNNN_*.sql` 로만 (Alembic 은 쓰지 않는다 — 위 2.2 정정)

### 3.1 기준 정보

**stocks** — 종목 마스터
`id PK / ticker / market(KOSPI|KOSDAQ|NYSE|NASDAQ) / country(KR|US) / name_ko / name_en / sector / sector_code / sector_source / sector_updated_at / currency / yahoo_symbol / listed_date / listed_shares / market_cap / market_cap_date / security_group / section_type(소속부 — 관리종목 판정) / share_kind / isin / dart_corp_code / status(active|delisted|excluded) / source / fetched_at`

> 2026-09-29 정정 (docs/infra.md 25.612): 예전 표의 `industry`·`shares_outstanding`·`is_spac`·`is_etf`·`delisted_date`·`status=halted|supervised` 는 만들지 않았다. 단일 정의처는 `migrations/` 다. 스팩은 이름으로, 관리종목·거래정지는 `section_type` 글자로 판정한다
유니크 `(ticker, market)`

**universe_members** — 주 1회 스냅샷
`id / snapshot_date / stock_id FK / included BOOL / exclude_reason(관리종목|거래정지|투자주의환기|정리매매|스팩|상장1년미만|시총미달|거래대금미달|보통주아님|주권아님|데이터없음|상장폐지|마스터제외) / market_cap / avg_turnover_20d / listed_days / currency / created_at` (상장폐지·마스터제외는 지난 스냅샷에 **편입**이었거나 **보유 중**인데 종목 마스터에서 빠진 종목에 남기는 줄 — 보유 중이면 매주 이어지고, 아니면 한 번이다. infra 25.412. 사유 목록의 정의처는 `batch/services/universe.ALL_REASONS` — `tests/test_doc_constants.py` 가 대조한다, infra 25.366)
유니크 `(snapshot_date, stock_id)`. 제외 종목도 사유와 함께 남긴다.

> **미국은 관리종목·거래정지·스팩을 판정하지 않는다** (2026-09-22, docs/infra.md 25.129).
> 나스닥 심볼 디렉터리에 소속부·증권구분 열이 없어서다. 미국 스냅샷에 그 사유가 0건인 것은
> **"없다" 가 아니라 "안 본다"** 이고, 유니버스 실행이 그 사실을 경고 한 줄로 남긴다
> (`services/universe.NOT_CHECKED`). `[확인필요: nasdaqlisted.txt 의 Financial Status
> 열로 관리종목을 대신할 수 있는지]`

**market_calendar** — 거래소 캘린더
`id / exchange(XKRX|XNYS) / date / is_open BOOL / open_time / close_time / note / source / fetched_at`
유니크 `(exchange, date)`

### 3.2 시세·재무

**prices** — 일봉
`id / stock_id FK / date / open / high / low / close / adj_close / volume / value(거래대금) / currency / source / fetched_at`
유니크 `(stock_id, date)`

**intraday_quotes** — 장중 최신가 (모니터링 대상만, 롤링 보관)
`id / stock_id / ts / price / prev_close / change_pct / volume / avg_volume_20d / source / fetched_at`

**fx_rates**
`id / pair(USDKRW) / date / rate / source / fetched_at` — 유니크 `(pair, date)`

**risk_free_rates**
`id / country(KR|US) / name(CD91|국고채3년|T-Bill 3M) / date / rate / is_manual BOOL / source / fetched_at`

**financials** — 최신 재무 (연결 기준 우선)
`id / stock_id / fiscal_year / fiscal_quarter / period_type(Q|A) / report_date(발표일) / consolidated BOOL / accounting_standard / currency / unit / revenue / operating_income / net_income / total_assets / total_equity / total_debt / cash / cfo / capex / eps / bps / dps / shares / source / fetched_at`
유니크 `(stock_id, fiscal_year, fiscal_quarter, consolidated)`

**financial_snapshots** — point-in-time
`id / stock_id / as_of_date(이 값을 알 수 있게 된 날 = 발표일) / fiscal_year / fiscal_quarter / payload JSON / source / fetched_at`
백테스트는 **오직 이 테이블만** 읽는다. `as_of_date <= 시뮬레이션 날짜` 조건으로 look-ahead를 차단한다.

**disclosures** — 공시
`id / stock_id / disclosure_id(원본 접수번호) / title / disclosed_at / url / type / is_material BOOL / source / fetched_at` — 유니크 `(source, disclosure_id)`

**earnings_calendar**
`id / stock_id / event_type(실적발표|배당락|주총) / scheduled_date / is_confirmed BOOL / source / fetched_at`

### 3.3 분석 결과

**performance_metrics** — 종목별 과거 성과
`id / stock_id / as_of_date / window(1Y|3Y|5Y) / cagr / mdd / mdd_start_date / mdd_end_date / mdd_recovery_days(NULL 가능) / volatility_ann / sharpe / sortino / beta / benchmark(KOSPI|S&P500) / risk_free_rate_used / data_points / calc_version / created_at`
유니크 `(stock_id, as_of_date, window)`. `data_points`가 기준 미달이면 값을 NULL로 두고 팩터에서 제외한다.

**factors**
`id / stock_id / as_of_date / factor(value|quality|growth|momentum|risk) / raw_json(원시 지표) / zscore / score_0_100 / peer_group(예: market:KOSPI, sector:반도체) / missing_fields JSON / calc_version / created_at`
유니크 `(stock_id, as_of_date, factor)`

**scores** — 종합 점수
`id / stock_id / as_of_date / total_score / factor_scores JSON / sentiment_score / sentiment_weight_used / weights_json(당시 가중치 스냅샷) / rank_in_market / rank_in_sector / created_at`
가중치를 바꿔도 과거 점수를 재현할 수 있도록 당시 가중치를 함께 저장한다.

**news**
`id / stock_id / title / url / published_at / publisher / lang(ko|en) / source / fetched_at` — 유니크 `(stock_id, url)`
**원문 본문은 저장하지 않는다.**

**article_sentiments**
`id / news_id FK / score(-1~+1) / method(vader|knu|ollama) / matched_terms JSON / created_at`

**sentiment_scores** — 종목 단위 집계
`id / stock_id / as_of_date / sentiment(-100~+100) / article_count / positive_count / negative_count / decay_halflife_days / delta_7d / delta_30d / method / created_at`
유니크 `(stock_id, as_of_date)`. `delta_7d`가 감성 급락 판정의 입력이다.

**signals**
`id / stock_id / as_of_date / horizon(short|mid|long) / signal_type(기술적돌파|실적모멘텀|밸류밴드하단 등) / strength / buy_zone_low / buy_zone_high / currency / rationale_text / rationale_data JSON / target_price / stop_price / suggested_weight_pct / suggested_amount / tranche_plan JSON / created_at`
`tranche_plan`은 3회 분할 계획: `[{step:1, price_at_or_below, amount, ratio}, ...]`
`rationale_data`에는 인용한 실제 수치와 그 출처 행을 담아 근거 문장을 검증 가능하게 한다.

### 3.4 리포트·알림

**daily_reports**
`id / market(KR|US) / trade_date / status(success|partial|failed) / generated_at / sent_at / telegram_message_id / summary_text / warnings JSON / batch_run_id FK`
유니크 `(market, trade_date)`

**report_items**
`id / report_id FK / section(recommend|buy_signal|sell_flag|notice) / stock_id / rank / payload JSON / rationale_text`
화면과 텔레그램이 **같은 report_items를 읽어** 동일 내용을 보장한다.
> 2026-09-17 구현 (마이그레이션 0030, `docs/reports.md`). 화면은 보낸 본문(`summary_text`)을 그대로 보여 주고 report_items 는 곁들인다.

**batch_runs**
`id / job_name / market / started_at / finished_at / status / step_log JSON(단계별 성공·소요·건수) / error_text`

**alerts**
`id / stock_id / trade_date / trigger_type(buy_zone|target|stop|spike|volume|disclosure) / triggered_at / price_at_trigger / threshold_json / message / sent_at / telegram_message_id / is_read BOOL`
유니크 `(stock_id, trade_date, trigger_type)` — 하루 1회 규칙을 DB가 강제한다.

**api_usage**
`id / api_name / window_type(day|minute) / window_start / call_count / limit_value(NULL 가능) / warn_at_pct / state(ok|warn|blocked|unknown) / last_call_at / updated_at`
유니크 `(api_name, window_type, window_start)`. 한도가 `[확인필요]`라 NULL이면 상태를 `unknown`으로 두고 보수적인 호출 간격을 적용한다.

### 3.5 사용자 데이터

**settings** — key-value
`key PK / value JSON / updated_at`
**키의 단일 정의처는 `web/lib/settings.ts` 의 `settingsSchema` 다.** 설정은 웹앱만 쓰고 배치는
읽기만 하므로 검증이 거기 한 곳에 있다. 아래 목록이 그것과 어긋나지 않는지 `tests/test_settings_keys.py`
가 확인한다 (2026-09-21 에 세 개가 어긋나 있었다 — docs/infra.md 25.62).

키 목록: `total_investable_amount`(원화), `base_currency`(KRW 고정), `factor_weights`, `sentiment_weight`,
`horizon_targets`(기간별 목표·손절), `max_weight_per_stock`, `max_weight_per_sector`,
`min_order_amount`(원. 이보다 작은 권장 금액은 2부에서 빼고 사유를 적는다 — infra 25.96),
`trend_filter`(지수 추세 필터 on/off 와 약세 배수, 3.5절·signals.md 3.5), `fees`, `taxes`, `risk_free_manual`,
`alert_thresholds`(급등락 %, 거래량 배수), `quiet_hours`(야간 알림 on/off와 시간대),
`sentiment_target_top_n`, `backfill_years`(시세·재무 각각 몇 년)

**설정이 아닌 것** — 예전 판에 키로 적혀 있었으나 그렇게 만들지 않았다.

| 적혀 있던 키 | 실제로 어디에 |
|---|---|
| `telegram_chat_id` | `.env` / 시크릿의 `TELEGRAM_CHAT_ID`. 봇 토큰과 같은 자리다 (아래 줄 참고) |
| `universe_filters` | 코드 상수와 CLAUDE.md "종목 유니버스 규칙". 화면에서 바꾸지 않는다 |
| `sentiment_method` | 고정이다 (영어 VADER, 한국어는 보류). 고를 것이 없어 설정으로 두지 않았다 |
텔레그램 봇 토큰은 DB가 아니라 **`.env`에만** 둔다.

**trades** — 사용자 입력 원본. 시스템이 수정하지 않는다
`id / stock_id / side(buy|sell) / trade_date / price / quantity / currency / fx_rate_at_trade / fee / tax / horizon(short|mid|long) / memo / created_at`
매수 시에만 자동 저장되는 스냅샷: `score_at_trade / signal_type_at_trade / sentiment_at_trade / factor_scores_at_trade JSON` — 매수 당시 근거를 얼려둔다. 복기의 기준이다.

**trade_lots** — 파생(FIFO 매칭). 재계산 가능
`id / buy_trade_id / sell_trade_id / quantity / buy_price / sell_price / realized_pnl / realized_pnl_krw / fx_pnl / fee_total / tax_total / holding_days / created_at`
환차손익을 주가 손익과 분리해 저장한다.

**positions** — 파생(현재 보유)
`stock_id / quantity / avg_price / currency / first_buy_date / horizon / updated_at`

**dividends**
`id / stock_id / pay_date / amount_per_share / quantity / gross_amount / tax / net_amount / currency / fx_rate / is_manual BOOL / source / fetched_at`

**watchlist**
`id / stock_id / added_at / memo / target_buy_price(NULL 가능) / alert_enabled BOOL`

**sell_flags**
`id / stock_id / as_of_date / level(green|red|yellow) / reason_code(목표도달|손절|재무악화|기간초과|감성급락|점검) / rationale_text / rationale_data JSON / is_active BOOL / resolved_at / created_at`

**screener_presets**
`id / name / filters_json / created_at / last_used_at`

**backtest_runs**
`id / name / params_json(신호 종류·가중치·기간·리밸런싱 주기·비용) / period_start / period_end / universe_desc / created_at / status / error_text`

**backtest_results**
`id / run_id FK / cagr / mdd / sharpe / sortino / win_rate / profit_factor / longest_drawdown_days / turnover / total_cost / benchmark(KOSPI|S&P500) / benchmark_cagr / excess_return / equity_curve JSON / drawdown_curve JSON`

**backtest_trades**
`id / run_id FK / stock_id / entry_date / exit_date / entry_price / exit_price / weight / pnl_pct / cost_pct / reason`

---

## 3.9 리포트 구조: 두 부분으로 나눈다

> 2026-09-16 추가. 지금까지의 설계가 포트폴리오 구성 쪽에 기울어 있어, 개별 종목을 보는 눈을 따로 세운다.

리포트와 화면을 **1부 개별 종목**과 **2부 포트폴리오**로 나눈다. 텔레그램 메시지도 같은 구조다.

### 왜 나누는가

두 질문이 다르기 때문이다.

| 부 | 묻는 것 | 보는 단위 |
|---|---|---|
| 1부 개별 종목 | **이 종목 자체가 어떤가** | 종목 하나 |
| 2부 포트폴리오 | **내 돈을 어떻게 나눌까** | 전체 |

같은 화면에 섞으면 "왜 이 종목이 추천인데 살 금액이 0원인가" 같은 혼란이 생긴다. 실제로 그런 경우가 생긴다. 좋은 종목인데 이미 그 섹터를 30% 채웠으면 더 못 산다. **그건 종목이 나쁜 게 아니라 내 포트폴리오가 찬 것이다.** 나눠 놓으면 이 차이가 드러난다.

### 1부 개별 종목

포트폴리오 제약을 **전혀 보지 않는다.** 비중 상한도 섹터 상한도 보유 현황도 무관하다.

담는 것은 이렇다.

- 종합 점수와 시장 내 순위
- 5개 팩터 점수 (밸류·퀄리티·성장·모멘텀·리스크)
- 센티먼트 점수 (항상 분리 표시)
- 매수 신호 종류와 투자 기간 (단기·중기·장기)
- 권장 매수 구간 (가격 범위)
- **근거 문장.** DB에 저장된 실제 수치만 인용한다
- 과거 성과 요약 (CAGR, MDD, 샤프)

기간별로 나눠 보여 준다. 단기·중기·장기는 판단 근거가 다르므로 섞지 않는다.

### 2부 포트폴리오

1부의 종목에 **돈을 어떻게 나눌지**를 본다.

담는 것은 이렇다.

- 종목별 권장 금액과 비중, 3회 분할 계획
- 비중 상한과 섹터 상한을 적용한 결과
- **상한에 걸려 줄어들거나 빠진 종목과 그 사유.** 1부에 있는데 2부에 없으면 반드시 이유를 밝힌다
- 현재 보유와 합친 섹터 집중도
- 매도 플래그 (녹·적·황)
- 남은 투자 여력

### 두 부를 잇는 규칙

**2부는 1부의 부분집합이다.** 1부에 없는 종목이 2부에 나오지 않는다.

**신호가 묵었으면 2부는 통째로 배분하지 않는다** (2026-10-01, infra 25.820). 신호 기준일이 리포트 거래일보다 **2거래일 이상** 앞서면(신호 계산이 멈춘 날)
1부는 기준일과 함께 그대로 싣되 2부는 금액을 내지 않고, 사유 한 줄을 추천 머리에 적는다("신호가 N거래일 전(날짜) 것이라 2부에서 금액을 배분하지 않았습니다"). 종목마다의 사유가 아니라 2부 전체의 사유라 아래 표에 넣지 않았다.
어제 신호(1거래일)는 배분한다 — 한 번 건너뛴 것은 흔하다.

**1부에 있는데 2부에 없으면 사유를 적는다.** 사유는 이 여덟 중 하나다 (2026-09-27 "환율 없음" 이 더해져 일곱, 그 뒤 "신호에 금액 없음" 으로 여덟 — 문장만 늦게 고쳤다, infra 25.649).
정의처는 `batch/notify/report_sections.EXCLUDED_REASONS` 이고,
`tests/test_excluded_reasons.py` 가 이 표와 양방향으로 대 본다(docs/infra.md 25.190).

| 사유 | 뜻 | 사용자가 할 일 |
|---|---|---|
| 섹터 상한 도달 | 이 종목 금액을 **다 넣으면** 그 섹터가 상한을 넘는다 — 남은 여유만큼 나눠 넣지 않고 통째로 뺀다 (2026-09-27 바로잡음, infra 25.293) | 설정의 섹터 상한(기본 30%)을 보거나 그대로 둔다 |
| 종목 비중 상한 도달 | 이미 그만큼 갖고 있다 — **보유 종목은 1부가 낸 권장 비중(변동성·MDD·약세장으로 줄인 값)까지만 채운다** (2026-09-28, infra 25.560). 보유가 종목 상한까지 넘었으면 상세 칸에 "종목 상한 10% 도 넘음" 을 덧붙인다 (2026-10-06, 25.964) | 설정의 종목 상한(기본 10%)을 보거나 그대로 둔다 |
| 투자 여력 부족 | 이 종목 금액이 **남은 돈보다 크다** — 남은 만큼 나눠 넣지 않고 통째로 뺀다(점수 순으로 앞 종목이 먼저 가져간다) (infra 25.293) | 총 투자가능금액을 늘리거나 다음 달을 기다린다 |
| 약세장 비중 0으로 0원 | 시장 국면이 약세이고 설정의 약세장 배수(`bear_factor`)가 0이다. 변동성·MDD 축소는 바닥이 0.3이라 0을 만들지 못한다 (2026-09-27 바로잡음, infra 25.340. 전에는 "변동성 축소로 0원") | 약세장에 새로 사지 않겠다는 뜻이면 그대로 둔다. 아니면 설정의 약세장 배수를 올린다 |
| 총 투자가능금액 미설정 | 설정에 금액이 없어 2부를 낼 수 없다 | **설정 화면에서 총 투자가능금액을 넣는다** |
| 최소 주문 단위 미만 | 줄이고 나니 한 주도 못 사는 금액이 됐다 (2026-09-21, infra 25.96). **배분 전체가 분할 회차 가운데 가장 낮은 가격의 1주보다 작거나 반올림해 0 이어도** (2026-09-28, infra 25.507). **회차마다 따로 1주를 못 사도** 뺀다(25.514 — 계획대로 나눠 사면 한 주도 못 산다. 배분 전체로는 몇 주를 살 수 있어도. 2026-10-10 감사가 표와 코드가 어긋난 것을 찾아 표를 코드에 맞췄다, infra 25.1088) | 최소 주문 금액을 낮추거나 그대로 둔다 |
| 환율 없음 | 미국 리포트에서 원화 총액을 달러로 바꿀 환율(7일 이내)이 없다. 설정은 있다 (2026-09-27, infra 25.292) | 설정은 그대로 둔다. 환율 수집(`python -m batch.jobs.fx`)이 도는지 본다 |
| 신호에 금액 없음 | 신호를 계산할 때 총액이 없었거나 환율이 없어 금액을 내지 못했다. 오늘은 총액이 있다 — 신호가 묵은 날(신호 단계 실패) 생긴다 (2026-09-29, infra 25.600. 전에는 "최소 주문 단위 미만" 으로 잘못 적었다) | 다음 신호 계산을 기다린다. 이어지면 신호 배치·환율 수집이 도는지 본다 |

표 밖에 있던 사유 — `총 투자가능금액 미설정`·`최소 주문 단위 미만` 은 2026-09 에 생겼는데 이 표에는 2026-09-25 까지 없었다 — 그동안
"사유는 이 중 하나다" 가 거짓이었다(infra 25.190).

**1부만 보고 판단해도 되게 만든다.** 포트폴리오를 안 짜고 개별 종목만 참고하는 것도 쓸모 있는 사용법이다. 2부를 안 봐도 1부가 그 자체로 완결되어야 한다.

### 구현 (Step 9, 2026-09-17)

| 무엇 | 어디 |
|---|---|
| 흐름 | `batch/jobs/daily.py` — 시세 수집 → 점수 → 신호 → 리포트 → 발송 |
| 무엇을 실을지 | `batch/services/report_picks.py` |
| 그리기 | `batch/notify/report_sections.py` |

- **시장별 상위 5종목** (2026-09-17 사용자 확정). 국내 리포트 5개, 미국 리포트 5개. 종합 점수 순, 서로 다른 종목
- 한 종목에 여러 기간 신호가 났으면 1부에 모두 싣는다. 기간마다 근거가 달라서다
- 머리말은 **실제로 고른 수**를 적는다("상위 2종목"). 추천이 0건인 날은 수를 적지 않는다. 그날도 보유·총액·매도 플래그 가운데 하나라도 있으면 **2부를 싣는다**(현재 보유 평가·남은 여력) — 2026-09-27, docs/infra.md 25.420
- 2부는 종목당 배분 하나. 기간별 권장 금액은 서로 독립으로 계산돼 더하면 비중 상한을 넘을 수 있어 가장 큰 한 건만 쓴다
- 2부 제외 사유에 둘을 더했다: **총 투자가능금액 미설정**, **최소 주문 단위 미만**. 금액이 비는 이유가 설정 때문인지 종목 때문인지 갈라야 무엇을 고칠지 안다
- 점수·신호를 일일 배치 안에서 다시 낸다. 주 1회였으면 추천이 최대 일주일 묵는다. 계산이 실패해도 리포트는 보내고, 마지막 신호의 기준일을 경고로 적는다

~~**아직 없는 것**: 보유 종목(trades)이 없어 섹터 집중도·보유와 합친 비중·매도 플래그를 낼 수 없다.~~ → 2026-09-17 밤(Step 30): 2부가 `positions` 의 보유 평가를 포함한다 — 남은 여력 = 총액 − 보유, 종목 상한과 섹터 상한·집중도는 보유 + 배분. 매도 플래그는 Step 13 에서 붙었다. 업종은 15절 경로로 채워진다. 값이 없는 것은 채우지 않고 비워 둔다.

## 4. API 엔드포인트

모두 `/api` 프리픽스. **모든 경로가 인증을 요구한다.** 로그인하지 않은 요청은 데이터 대신 401을 받는다. 웹앱의 서버 측 코드가 이 경로들을 구현하고 Turso를 직접 읽는다.

아래 경로만 로그인 대신 비밀 토큰(헤더 `x-cron-secret`)으로 보호한다. 외부 크론이 호출하기 때문이다.
로그인(`POST /api/auth/login`)만 인증 전에 열린다(`web/proxy.ts` PUBLIC_PATHS). 로그아웃(`POST /api/auth/logout`)은 인증 뒤다.

```
GET    /api/cron/intraday?market=KR|US  장중 모니터링. cron-job.org가 5분마다 호출
GET    /api/cron/health                 배치 무응답 감시. 1시간마다
GET    /api/cron/news                   미국 뉴스 수집(한 번에 한 종목). 1분마다
GET    /api/cron/news-kr                국내 뉴스 수집. 1시간마다
```
(2026-09-28 4장 전체를 코드와 맞춤, docs/infra.md 25.587 — 예전의 `POST /cron/intraday`·`/cron/watchdog` 은 없는 모양이었다)

### 설정
```
GET    /api/settings                    전체 설정 (포트폴리오 화면이 배당 원천징수 칸을 채우는 데 쓴다)
PUT    /api/settings                    **전체** 저장 — 모든 키가 있어야 한다(일부만 오면 400). 검증 포함
POST   /api/telegram/test               테스트 메시지 발송 (TELEGRAM_CHAT_ID 가 비면 보내지 않는다, 25.584)
(2026-09-28 바로잡음, infra 25.573: "부분 업데이트"·`/api/settings/telegram/test`·`/api/settings/defaults` 는 코드와 달랐다)
```

### 리포트
```
GET    /api/reports?market=&date=       그 시장의 리포트 하나(date 없으면 가장 최근) + report_items + 날짜 목록(history)
POST   /api/recommend                   오늘 신호 카드(1부와 같은 규칙, 신호 전부) {country, market, horizon, limit}
```
(2026-09-28 코드와 맞춤, docs/infra.md 25.586. 예전 목록의 `/reports/today`·`/reports/{id}`·`resend`·`/jobs/daily/run` 은 없는 경로였다.
재발송은 만들지 않았다(reports.md 4장). 수동 배치 실행은 `/api/status/run`)

### 종목
```
GET    /api/stocks/search?q=            검색
GET    /api/stocks/{id}                 마스터 + 최신 점수
GET    /api/stocks/{id}/{section}       section = prices(?range=)·financials·valuation·metrics·news·events·signals·dividends·quarterly
```

### 스크리너
```
POST   /api/screener                    필터 조건 → 결과
GET    /api/screener/presets?country=
POST   /api/screener/presets
PATCH  /api/screener/presets/{id}       마지막 사용 시각
DELETE /api/screener/presets/{id}
```

### ETF·장기 적립
```
POST   /api/etf                         핵심 ETF 판정 결과 + 큰데 빠진 것 + 시장별 마지막 실행
POST   /api/etf/satellite               위성 ETF {country}
POST   /api/etf/stocks                  장기 적립 종목 {country}
```

### 매매·포트폴리오
```
GET/POST             /api/trades        목록·넣기 (고치기는 지우고 다시 넣는다 — 스냅샷이 넣는 순간의 것이어야 해서)
GET/DELETE           /api/trades/{id}   매수 당시 근거 스냅샷 보기·지우기
GET/POST             /api/dividends
DELETE               /api/dividends/{id}
GET    /api/portfolio                   평가액·손익·환차손익·집중도·CAGR·MDD·샤프·실적 D-day (배치가 만든 요약 하나)
GET    /api/review                      청산 매수 결정별 복기 + 집단 통계 (한 경로로 합침, Step 15)
```

### 플래그·관심·알림
```
POST   /api/sell-flags/{id}                 플래그 끄기(dismiss). 이미 끈 것은 409 (목록은 리포트·종목 화면이 읽는다)
GET/POST             /api/watchlist       POST 는 종목별 upsert. 목표가 키가 없으면 있던 목표가를 그대로 둔다
PATCH/DELETE         /api/watchlist/{id}  목표가·알림 켜기 수정, 삭제. 응답 warnings: 종가 이상 목표가
GET    /api/alerts                          최근 알림 + 감시 목록·다음 정규장·호출 기록 (쿼리 필터 없음 — 화면이 거른다)
PATCH  /api/alerts                          읽음 처리 {ids, read}. ids 가 비면 안 읽은 전부 (화면은 보이는 것만 보낸다)
```
(2026-09-28 코드와 맞춤, docs/infra.md 25.584 — 예전 목록의 `/sell-flags?active`·`/alerts/{id}/read`·`?from=&to=` 는 없는 경로였다)

### 백테스트
```
GET    /api/backtest?market=&group=&curves=   묶음 결과 + 자본곡선(고른 전략 3개까지) + 최신 스트레스 + 실행 상태
POST   /api/backtest/run                      GitHub Actions 백테스트 워크플로 깨우기 (결과는 배치가 저장)
```
(2026-09-28 코드와 맞춤, 25.584. 결과 가중치를 설정에 반영하는 경로는 만들지 않았다)

### 시스템
```
GET    /api/status                      마지막 배치·다음 예정·API 한도 사용량·데이터 기준 시각 (한 경로로 합침)
POST   /api/status/run                  배치 수동 실행 요청 {job} (목록에 있는 작업만)
GET    /api/db-health                   DB 가 살아 있는가(`SELECT 1`). 화면 맨 위 띠가 부른다
```
(2026-09-28 코드와 맞춤, 25.584 — `/api/system/*`·`/api/health` 는 없는 경로였다)

---

## 5. 화면 목록

> 2026-09-16 추가. **추천 화면(`/recommend`)이 첫 화면이다.** 스크리너는 조건을
> 정할 줄 알아야 쓸 수 있는 화면이라, 아무것도 정하지 않아도 볼 것이 있는
> 화면을 앞에 둔다. 구조는 3.9절의 1부 개별 종목이다.

공통: 모든 화면 하단에 "투자 판단의 책임은 본인에게 있습니다" 고지, 상단에 데이터 기준 시각과 stale 경고 배지.

| # | 화면 | 핵심 요소 |
|---|---|---|
| 1 | 설정 | 총 투자가능금액, 팩터 5개와 센티먼트 가중치 슬라이더(합계 검증), 기간별 목표·손절, 비중·섹터 상한, 수수료·세율, 무위험수익률(자동·수동), 급등락·거래량 임계값, 조용시간, 텔레그램 chat_id와 테스트 발송 |
| 2 | 일일 리포트 (`/reports`. 홈은 `/recommend` 추천 카드 — 25.586) | 시장 탭(국내·미국), 오늘 추천 카드(점수·근거 문장·매수 구간·분할 계획·권장 금액), 매수 신호 목록, 매도 플래그 목록, 과거 리포트 이력, "텔레그램과 동일" 표시 |
| 3 | 종목 상세 | 가격 차트(매수 구간·목표·손절 오버레이), 5년 재무 추이, 밸류에이션 밴드와 업종 대비 백분위, 과거 성과 카드(1/3/5년 CAGR·MDD·샤프·변동성·베타), 뉴스 목록과 감성 추이, 실적·공시 일정, 팩터 레이더와 센티먼트 분리 표시 |
| 4 | 스크리너 | 시장·업종·시총·밸류·성장·모멘텀·리스크(MDD·샤프·CAGR 범위)·감성 필터, 결과 테이블 정렬과 CSV 내보내기, 프리셋 저장·불러오기 |
| 5 | 매매 기록 | 매수·매도 입력 폼(종목·일자·단가·수량·기간·수수료·메모), 목록, 매수 당시 근거 스냅샷 보기, 배당 입력 |
| 6 | 포트폴리오 | 환율 반영 평가액, 실현·미실현 손익(환차손익 분리), 섹터·종목 집중도 차트, 포트폴리오 CAGR·MDD·샤프, 실적 발표 D-day 경고 |
| 7 | 관심종목 | 목록, 목표 매수가 설정, 알림 on/off |
| 8 | 알림 센터 | 알림 이력, 트리거별·종목별 필터, 읽음 처리, 클릭 시 종목 상세로 이동 |
| 9 | 매매 복기 | 청산 종목별 매수 근거와 결과 비교표, 신호 종류별 적중률·평균 수익률·평균 보유일. **구현은 6번 포트폴리오 안의 [복기] 탭**(상단 탭이 여섯이라 더 넓히지 않음, 2026-09-17) |
| 10 | 백테스트 | 조건 입력(신호·가중치 조합·기간·리밸런싱·비용), 실행 진행률, 결과 지표표, 자본곡선·낙폭 차트, 벤치마크 대비 초과수익, "이 가중치를 설정에 반영" 버튼 |
| 11 | 시스템 상태 | 마지막 배치 시각·성공 여부·단계별 로그, API별 한도 사용량 게이지(80% 경고색), 다음 실행 예정 시각, 소스별 데이터 신선도, 수동 배치 실행 버튼 |

---

## 6. 데이터 소스

인증 방식·무료 한도·이용 조건은 `docs/data-sources.md`에, 키 발급 절차는 `docs/api-keys.md`에 기록했다. 한도가 확인되지 않은 소스는 `[확인필요]`로 두고, 확인 전까지는 보수적인 호출 간격으로만 사용한다.

폴백 순서는 전 소스 공통이다.

```
정상 호출 → (실패·한도초과) → 캐시 → 대체 무료 소스 → 기능 일시 비활성화 + 화면 표시
```

예: 국내 일별 시세는 `KRX Open API → yfinance(005930.KS, 그 거래일 봉만) → 실패`. pykrx 는 로그인 요구로 막혀 쓰지 않는다 (2026-09-29 정정, docs/data-sources.md 2절·5절).

### 6.0 국내 데이터 경로 (2026-09-16 확정)

한국투자증권 API를 **선택 항목으로 내렸다.** 실계좌 개설이 필요하고, 무엇보다 클라우드에서 호출되는지가 확인되지 않아 준비가 헛수고가 될 위험이 있다. 그 API가 유일하게 주는 것은 장중 실시간 시세와 공식 휴장일 조회 둘뿐이고, 나머지는 전부 대체된다.

| 필요한 것 | 1순위 | 폴백 |
|---|---|---|
| 종목 마스터, 시총, 거래대금 | KRX Open API | 없음(기존 행으로 판정) |
| 업종 | DART 기업개황 (data-sources.md 15절) | 없음 |
| 일별 시세 | KRX Open API | yfinance (25.505) |
| 재무, 공시, 발표일 | DART OpenAPI | 캐시 |
| 휴장일 | exchange_calendars | 불일치 시 보수적으로 휴장 처리 |
| 국내 장중 현재가 | yfinance 지연 시세 | 장중 알림 일시 비활성화 |
| 미국 전부 | yfinance | 캐시 |

**타협한 것은 장중 알림의 시의성 하나다.** 한국거래소 계열 소스는 일별 데이터라 장중 현재가를 주지 않는다. 야후 파이낸스가 국내 종목 장중 시세를 주지만 지연이 있다.

**Step 0에서 실측한 지연은 약 20분이다.** 정규장 중 두 번 측정해 20분과 21분이 나왔다. 설계에 반영할 것은 셋이다.

1. 목표가·손절선 터치 알림은 20분 뒤에 온다. **알림 문구에 "20분 지연 시세 기준"과 데이터 시각을 반드시 표시한다**
2. 원본이 20분 지연이면 5분 간격 폴링의 정보량이 10~15분 간격과 같다. **폴링 주기를 늘려 호출을 아낀다.** Step 14에서 확정
3. 급등락·거래량 트리거는 지연이어도 쓸모가 있다. 되돌림이 있어도 그날 이례적인 움직임이었다는 사실은 남는다

**나중에 붙일 수 있게 둔다.** 어댑터 규약이 동일하므로 국내 시세 소스 교체는 파일 하나를 바꾸는 일이다. 장중 정확도가 아쉬워지면 그때 계좌를 만들어 추가한다.

### 6.1 조사에서 드러난 설계 변경 (2026-09-16)

**뉴스 소스 1순위 두 개가 모두 막혔다.**

| 소스 | 문제 | 대응 |
|---|---|---|
| 네이버 검색 API | 레거시 종료 절차 진행 중으로 보임. 신규 등록 가능 여부 불확실. 캐싱·저장 제한 특약 보도 | 사용자가 브라우저로 신규 등록 가능 여부 확인. 안 되면 언론사 공식 RSS로 전환 |
| 구글 뉴스 RSS | 피드 본문에 개인 피드 리더 외 사용 금지가 명시. robots.txt도 `/rss` 미허용 | **사용하지 않는다.** 언론사 공식 RSS 또는 yfinance의 뉴스 필드로 대체 |
| KNU 한국어 감성사전 | 저장소에 라이선스 문구가 없음 | 보류. 사전 파일을 저장소에 포함하지 않는다. 대안 사전 탐색 또는 규칙 기반 대체 |

**센티먼트 축은 처음부터 끄고 시작할 수 있게 설계한다.** 종합 점수에서 센티먼트 가중치가 0이면 5팩터를 재정규화해 쓴다. 이렇게 하면 Step 7이 지연되거나 무산돼도 나머지 12개 기능이 그대로 돈다. 화면에는 센티먼트 칸에 "비활성" 사유를 표시한다.

### 6.2 이용 조건이 코드에 영향을 주는 항목

| 조건 | 출처 | 코드에 반영할 것 |
|---|---|---|
| KRX는 비상업 전용, 제3자 제공 금지 | KRX 약관 | 인증 뒤에만 데이터 노출. 저장소 프라이빗. 공개 공유 기능을 만들지 않는다 |
| DART 데이터 정확성 미보장 | DART 약관 | 화면에 출처와 기준 시각을 함께 표시 |
| yfinance 데이터는 개인 사용 한정 | 공식 README | 상업화·재배포 경로를 만들지 않는다 |
| FRED 고지 문구 표기 의무 | FRED 약관 | 화면 하단에 책임 고지와 함께 표기 |
| ECOS 출처 표시 의무 | 한국은행 방침 | 금리 데이터 옆에 출처 표기 |
| 텔레그램 한도 미공개 | — | 숫자를 가정하지 않고 429 응답의 `retry_after`를 지키는 재시도로 구현 |

### 6.3 휴장일 판정

캘린더가 틀리면 배치 전체가 어긋난다. 한국투자증권의 공식 휴장일 조회를 쓰지 않기로 했으므로 대조 상대가 없어졌다. 대신 이렇게 막는다.

1. `exchange_calendars`를 판정 근거로 쓴다. XKRX는 1956~2050년이 수록돼 있다
2. **판정 근거와 캘린더 라이브러리 버전을 `batch_runs`에 기록한다.** 나중에 틀린 날을 추적할 수 있어야 한다
3. **휴장일인데 데이터가 들어오거나, 개장일인데 시세가 전부 비면** 캘린더를 의심하고 텔레그램으로 알린다. 이것이 사후 감지 장치다
4. 캘린더 패키지를 연 1회 이상 갱신하고 거래소 공지와 대조한다

`exchange_calendars`는 사용자 기여로 유지보수되므로 임시공휴일 반영이 늦을 수 있다. 정부가 갑자기 지정하는 임시공휴일이 가장 위험하다. **라이브러리에 빠진 휴장일은 `batch/core/calendar.EXTRA_HOLIDAYS` 에 손으로 더한다**(2026-09-28, docs/infra.md 25.449) — 배치는 달력을 `calendar.exchange_calendar(market)` 하나로만 받으므로 한 곳에 적으면 판정·세션 목록·개장 시각이 함께 바뀐다. 처음 더한 날: 2026-06-03 지방선거일. 공공데이터포털 특일 정보 API는 법정공휴일이지 거래소 휴장일이 아니라서(근로자의 날은 공휴일이 아닌데 휴장하고, 연말 폐장일도 다르다) 단독 근거로 쓰지 않는다.

**휴장일을 잘못 판정해도 피해는 제한적이다.** 휴장일에 배치가 돌면 전일과 같은 데이터를 다시 받아 리포트가 중복될 뿐이고, 개장일에 안 돌면 그날 리포트가 빠진다. 2.6절의 무응답 감시가 후자를 잡는다.

---

## 7. 구현 Step

각 Step은 커밋 1개 이상, pytest 통과, 그리고 ① 실행 방법 ② 확인 포인트 ③ 알려진 한계 보고로 끝난다. 계산 로직은 테스트 없이 커밋하지 않는다.

### Step 0 — 클라우드 기반 검증 (선행, 짧게)
목표: 설계를 무너뜨릴 수 있는 전제를 **코드를 쌓기 전에** 확인한다. 여기서 막히면 구조를 다시 짠다.
- 프라이빗 저장소 생성, GitHub Actions에서 Python 실행
- **야후 파이낸스가 Actions IP에서 동작하는지 확인.** 여러 번, 여러 시간대에 돌려 429가 나는지 본다
- **KRX Open API가 Actions에서 호출되는지 확인.** 국내 일별 시세 경로의 전제다
- **국내 장중 현재가의 지연 폭 실측.** 야후가 주는 국내 종목 현재가를 장중에 받아 실제 시세와 얼마나 차이 나는지 본다
- Turso 무료 가입, 카드 요구 여부 확인, 연결과 읽기·쓰기 테스트
- Actions 시크릿에 텔레그램 토큰 저장, 한 줄 메시지 발송
- cron-job.org에서 `repository_dispatch`로 워크플로를 깨우는 경로 확인
- **완료 기준**: ① Actions 수동 실행으로 삼성전자와 AAPL 종가를 받아 텔레그램 수신 ② 같은 워크플로를 하루에 여러 번 돌려도 야후가 막지 않음을 확인 ③ KRX Open API 호출 성공 ④ Turso에 행을 쓰고 읽기 성공 ⑤ 어느 서비스도 카드를 요구하지 않음 ⑥ 외부 크론이 Actions를 깨우는 데 성공 ⑦ 국내 장중 지연 폭을 수치로 보고 ⑧ 야후가 막히면 그 사실과 대안을 보고하고 진행을 멈춘다

### Step 1 — 배치 뼈대 + 가격 수집 + cron + 텔레그램
목표: 삼성전자(005930)와 AAPL 가격을 수집해 매일 08:00 KST에 텔레그램으로 보낸다. cron 스케줄과 텔레그램 발송을 여기서 검증한다.
- 저장소 구조, `.env.example`, ruff 설정, 초기 마이그레이션(`migrations/0001_initial.sql`)
- 테이블: `stocks`, `prices`, `batch_runs`, `api_usage`, `settings`
- 어댑터: `yfinance_src`(AAPL, 005930.KS). 국내 일별 시세는 Step 3에서 KRX 경로로 교체
- 배치 진입점 `python -m batch.jobs.daily --market KR`, 워크플로 `daily-kr.yml`, `daily-us.yml`
- 텔레그램 발송, 실패 시 실패 알림, cron 지연 대비 실제 실행 시각 기록
- **완료 기준**: ① cron으로 08:00 KST에 두 종목 종가·등락률이 담긴 텔레그램 수신 ② `workflow_dispatch` 수동 실행으로도 같은 메시지 ③ 오프라인 모드 테스트 통과 ④ `batch_runs`에 성공 기록과 실제 실행 시각 ⑤ ~~메시지 끝에 책임 고지 포함~~ (2026-10-02 사용자 지시로 텔레그램 고지를 뺐다, infra 25.878) ⑥ 미국 배치가 서머타임을 반영해 장 1시간 전에 도는지 확인

### Step 2 — 웹앱 골격 + 인증 + 설정 화면
- Next.js 배포, **인증 적용**, 허용 이메일 단일 계정 제한, 검색엔진 색인 차단
- `settings` key-value와 검증(가중치 합계, 상한 범위), 설정 화면
- 텔레그램 테스트 발송 버튼
- **완료 기준**: ① 로그인하지 않은 상태로 모든 경로에 접근해 데이터가 전혀 노출되지 않음을 확인 ② 허용 목록에 없는 계정으로 로그인 시도가 거부됨 ③ **폰 브라우저에서 로그인해 설정을 저장** ④ 잘못된 값 거부 ⑤ 화면 하단에 책임 고지와 FRED 고지 렌더

### Step 3 — 유니버스 + 캘린더 + 대량 수집
- `krx` 어댑터에 종목기본정보 추가, `nasdaq_symbols` 어댑터 신규, `market_calendar`, `universe_members`, 주 1회 갱신 job
- 국내 일별 시세를 야후 파이낸스에서 KRX 전종목 수집으로 교체
- 백필 스크립트, 레이트리밋 카운터 실동작
- **완료 기준**: 유니버스 종목 수와 제외 사유 분포가 보이고, 휴장일에 배치가 건너뛰며, 한도 80%에서 경고가 남는다

**미국 유니버스 방식 변경 (2026-09-16)**

S&P500 + 나스닥100 합집합으로 시작하려 했으나, 지수 구성종목 목록을 무료로 받을 안전한 경로가 마땅치 않았다. 위키백과를 긁거나 제3자 저장소에 의존해야 하는데 출처마다 라이선스가 걸리고 갱신이 늦을 수 있다.

대신 **나스닥이 공개하는 전체 종목 목록**을 쓴다. `CLAUDE.md`의 유니버스 규칙이 지수 편입 여부가 아니라 시총과 거래대금이므로, 거래소가 직접 매일 갱신하는 전체 목록에 규칙을 적용하는 편이 명세에 더 맞는다.

**미국 일일 시세 수집을 전종목으로 교체 (2026-09-16)**

국내만 한국거래소 전종목으로 바꾸고 미국은 Step 1 의 AAPL 한 종목이 그대로 남아 있었다. 시세가 쌓이지 않으니 20일 평균 거래대금이 나오지 않고, 그래서 미국 유니버스 판정이 통째로 비어 있었다. 스코어링에 들어가기 전에 막았다.

한국거래소 경로와 성격이 다른 점 셋을 기억해 둔다.

- **거래대금을 주지 않는다.** 종가 × 거래량으로 갈음한다. 추정치이고 그 사실을 `docs/data-sources.md` 4번에 기록했다
- **호출 수가 종목 수에 비례한다.** 심볼을 200개 조각으로 나눠 부른다. 한국거래소는 한 번이면 끝난다
- **창 안의 모든 거래일을 저장한다.** 20일 평균은 시장의 최근 20 거래일 창에서 **행이 빠진 날을 거래대금 0 으로 센다**(infra 25.211 — 예전에는 하루만 비어도 판정에서 뺐다). **시장의 거래일은 그날 행이 있는 종목이 가장 많은 날의 절반 이상인 날**이다 — 토요일 수정주가 재수집이 대기열 종목에만 금요일 행을 넣으면 창이 하루 밀려 나머지 전 종목의 금요일이 0 이 됐다(infra 25.1109)

수천 종목을 조각으로 나눠 부를 때 야후가 막는지는 **아직 운영에서 확인되지 않았다** `[확인필요]`. 첫 실행에서 소요 시간과 429 여부를 본다.

**미국 시총 판정은 붙어 있다** (2026-09-27 바로잡음, infra 25.366). 처음에는 "아직 붙이지 않았다" 고 적었는데, 지금은 `jobs/us_shares`(주 1회)가 주식수를 받아 `stocks.market_cap` 을 채우고 `UniverseFilters.usa()` 의 시총 하한 10억달러로 판정한다. 시총 기준일이 묵으면 실행 경고가 남는다(25.362).

### Step 4 — 재무 + point-in-time 스냅샷
- `financials`, `financial_snapshots`, `disclosures`, `earnings_calendar`
- 회계기준·통화·단위·연결 여부 기록, 발표일 기준 스냅샷 적재
- **완료 기준**: 특정 과거 날짜를 주면 그 시점에 알 수 있었던 재무만 반환하는 함수와 그 테스트 통과

### Step 5 — 성과 지표 (독립 Step)
- `services/metrics.py` 순수 함수: CAGR, MDD, MDD 회복 기간, 연환산 변동성, 샤프, 소르티노, 베타
- `docs/metrics.md` 작성, `performance_metrics` 적재
- **무위험수익률은 설정 화면 수동 입력으로 갔다.** `docs/api-keys.md` 6번이 "Step 5에서 자동으로 넣겠다" 고 적어 둔 FRED 연동은 **끝내 만들지 않았고**, 그것을 되돌려 적지도 않아 문서만 넉 달 동안 "붙였다" 고 말했다 (2026-09-23 발견, infra 25.160). 안 넣으면 샤프·소르티노가 NULL 이고 설정 화면이 "빠진 값" 으로 알린다
- **완료 기준**: 손으로 계산 가능한 고정 데이터로 각 지표의 기대값을 검증하는 pytest 통과. 결측·데이터 부족 시 NULL 처리 테스트 포함

### Step 6 — 스코어링 엔진
- **완료 (2026-09-16).** `docs/factors.md` 를 먼저 쓰고 그 문서대로 구현했다
- 리스크 팩터는 **안정성 점수**로 확정했다(B2). MDD·변동성이 작을수록 100점이고, 다섯 팩터가 모두 "높을수록 좋다"로 통일된다
- 업종 z-score 는 **시장별로 먼저 낸다.** `stocks.sector` 를 채우는 코드가 아직 없다. `peer_group` 이 `market:KOSPI` 와 `sector:KOSPI:반도체` 두 형태를 모두 담으므로, 업종이 채워지면 코드 변경 없이 올라간다
- `batch/services/scoring.py`(순수 계산), `batch/jobs/scores.py`(적재), `migrations/0007_scores.sql`, `.github/workflows/scores.yml`
- **지금 실제로 점수가 나오는 것은 국내뿐이다.** 미국은 재무 수집 경로가 없어 밸류·퀄리티·성장이 비고, 팩터가 둘 이상 비면 종합 점수를 내지 않는다는 규칙에 걸린다. 버그가 아니라 데이터 상태이고 `scores.skip_reason` 에 남는다
- **완료 기준 확인**: 국내와 미국을 직접 비교하지 않음을 테스트로 고정(나스닥 종목을 더해도 코스피 종목 점수가 변하지 않음), 결측 많은 종목 제외 규칙 동작, 시장별 순위 산출
- 5팩터 계산 → 시장·업종 z-score → 0~100, `scores`에 가중치 스냅샷과 함께 저장
- **완료 기준**: 국내와 미국을 직접 비교하지 않음을 테스트로 확인, 결측 많은 종목 제외 규칙 동작, 종목 랭킹 출력

### Step 7 — 뉴스 감성 (독립 Step, 착수 전 소스 확정 필요)
- **선행 조건**: 6.1절의 뉴스 소스 문제를 먼저 해결한다. 국내는 네이버 신규 등록 가능 여부 확인 후 언론사 공식 RSS로 전환, 해외는 yfinance 뉴스 필드 또는 언론사 RSS, 한국어 감성은 라이선스가 명시된 자원을 찾는다
- 뉴스 수집, VADER 채점(영어), 시간 감쇠 합산, `delta_7d` 계산
- 수집 대상은 유니버스 전체가 아니라 `보유 ∪ 관심 ∪ 시장별 상위 50종목`
- 원문 미저장 확인, Ollama는 설정으로만 켜지는 선택 경로
- **완료 기준**: 고정 기사 세트로 감성 점수 회귀 테스트, 센티먼트가 종합 점수에 가중치만큼만 반영되고 화면에서 분리 표시, 센티먼트를 끄면 5팩터 재정규화로 정상 동작
- **이 Step은 건너뛸 수 있다.** 소스 문제가 풀리지 않으면 센티먼트 가중치 0으로 두고 Step 8로 넘어간다

### Step 8 — 매수 신호 + 사이징
- **완료 (2026-09-16).** 규칙을 `docs/signals.md` 에 먼저 적고 그것만 구현했다
- `batch/services/signals.py`(순수 계산), `batch/jobs/signals.py`(적재), `migrations/0008_signals.sql`, `.github/workflows/signals.yml`
- 웹에 **`/recommend` 추천 화면**을 붙였다. 스크리너는 사용자가 조건을 정하고, 이 화면은 규칙이 고른 결과를 보여 준다. 홈이 여기로 온다
- **완료 기준 확인**: 어떤 조합에서도 종목 비중이 상한을 넘지 않음을 테스트로 고정, 근거 문장이 받은 수치만 인용하고 없는 값은 문장에서 빠짐을 테스트로 고정
- **섹터 상한은 미적용 상태다.** `stocks.sector` 가 비어 있다. 조용히 넘어가지 않고 `signals.sector_cap_note` 에 사유를 남겨 화면이 표시한다. 업종이 채워지면 코드 변경 없이 켜진다
- 밸류에이션 밴드는 **발표일 기준**으로 그 시점에 알 수 있던 재무만 쓴다. 미래 참조 금지를 테스트로 고정했다

### Step 9 — 일일 리포트 + 텔레그램 본발송
- `daily_reports`·`report_items`, 템플릿 근거 문장, 국내·미국 배치 분리, 부분 실패 시 경고 포함 발송
- 리포트 화면(홈)과 이력
- **완료 기준**: 화면과 텔레그램이 같은 `report_items`에서 생성됨을 테스트로 확인, 배치 실패 시 실패 알림과 마지막 성공 리포트 유지

### Step 10 — 종목 상세 화면
- 차트, 5년 재무, 밸류에이션 밴드, 성과 카드, 뉴스·감성 추이, 일정
- **완료 기준**: 데이터 없는 항목이 무너지지 않고 "데이터 없음"과 기준 시각을 표시
- 2026-09-17 완료. 구현과 결정(밸류에이션 밴드 주간 전 종목 배치, 차트 라이브러리 표기)은 `docs/stock_detail.md`

### Step 11 — 스크리너
- 필터·정렬·프리셋 저장
- **완료 기준**: 리스크 지표 범위 필터가 `performance_metrics`와 일치, 프리셋 재현

### Step 12 — 매매 기록 + 배당 + 포트폴리오
- `trades`(입력 원본)와 매수 시 근거 스냅샷 자동 저장, FIFO `trade_lots`, 환차손익 분리, 수수료·세금 반영
- 포트폴리오 화면, 실적 D-day
- **완료 기준**: 환율이 다른 시점의 미국 매매에서 주가 손익과 환차손익이 분리되고 합계가 총손익과 일치하는 테스트 통과

### Step 13 — 매도 플래그
- 녹·적·황 판정, 근거 문장, 일일 배치 편입, 텔레그램 알림
- **완료 기준**: 자동 매도 경로가 코드에 존재하지 않음을 확인, 각 레벨의 판정 조건 테스트

### Step 14 — 관심종목 + 장중 모니터링 + 알림 센터 (독립 Step)
- 웹앱에 장중 모니터링 API 경로를 만들고 비밀 토큰으로 보호한다. cron-job.org가 정규장 시간에만 5분 간격으로 호출한다
- 트리거 5종, 하루 1회 유니크 제약, 야간 알림 on/off와 묶음 발송, 알림 센터 화면
- 경로 안에서 휴장일과 정규장 시간을 다시 확인한다. 외부 크론이 잘못 호출해도 장 밖에서는 아무것도 하지 않는다
- **완료 기준**: 같은 트리거 중복 발송이 DB 제약으로 차단됨을 테스트, 장 밖 호출이 즉시 종료됨, 토큰 없는 호출이 거부됨, 장중 실행이 `scores`를 수정하지 않음, 야간 알림을 끈 상태에서 알림이 저장은 되고 발송은 해제 시각에 한 통으로 묶여 나감

### Step 15 — 매매 복기
- **완료 (2026-09-17).** `docs/review.md` 가 규칙의 단일 정의처. `batch/services/review.py`(순수 계산), 포트폴리오 배치(`batch/jobs/portfolio.py`)가 `trade_lots` 와 같은 실행에서 `trade_reviews`·`review_stats`(0024)를 통째로 다시 만든다. 웹은 `/api/review` 와 [내 포트폴리오] → [복기] 탭
- 단위는 **매수 결정 하나**(FIFO lot 을 `buy_trade_id` 로 합침). lot 을 그대로 세면 관찰이 부푼다
- 청산 종목의 매수 근거(스냅샷 열)와 결과 비교, 집단(전체·기간·신호)별 승률·원가가중 평균·중앙값·평균 보유일·목표 도달률·손절 이탈률
- 판정은 복기 시점 설정 `horizon_targets` 로. 매수 시점 값을 얼리지 않았다 [확인필요: 얼릴지]
- **완료 기준 충족**: `MIN_SAMPLE=10`(출발값 [확인필요]) 미만이면 `sample_ok=0` → 화면 회색 + "표본 N건 — 단정하지 않는다"

### Step 16 — 백테스트 (독립 Step)
- **엔진·적재 완료 (2026-09-16).** `docs/backtest.md` 를 먼저 쓰고 그대로 만들었다. 웹 화면(결과 표시·실행 요청)은 아직 없다
- `batch/services/backtest.py`(순수 엔진), `batch/jobs/backtest.py`(적재), `migrations/0009_backtest.sql`, `.github/workflows/backtest.yml`(수동 실행만)
- **성과요인분석**은 별도 엔진 없이 같은 엔진으로 팩터 하나씩 전략을 돌려 나란히 놓는다. 회귀로 기여를 나누는 방식은 팩터 간 상관 가정이 필요하고 지금 데이터로 검증할 수 없다
- **스트레스 테스트**를 함께 넣었다. `docs/stress.md`, `batch/services/stress.py`, `batch/jobs/stress.py`. 지금 추천된 바스켓을 과거 가격에 얹어 20·60·120·250 거래일 최악의 창을 기계적으로 찾는다. 날짜를 손으로 고르지 않는다
- **⚠ 생존편향을 지금 데이터로는 막지 못한다.** DB 에 상장폐지 종목이 없다(`stocks.status` 전부 `active`). 엔진은 규칙대로 만들었고 결과에 경고가 항상 붙는다. 경고가 붙은 결과로 규칙을 확정하지 않는다
- 원래 계획대로 `financial_snapshots` 만 쓴다. 정정본은 접수된 뒤에만 반영된다. `pit_financials` 를 테스트로 고정했다
- **화면 완료 (2026-09-17).** `/backtest` — 경고 먼저, 실행 요청(시장·무엇을·기간·종목 수), 전략별 지표표(성과요인 포함), 자본곡선(최대 3개 겹침), 스트레스 창별 최악 구간. `docs/backtest.md` 8장이 화면 규칙의 단일 정의처
- 웹에서 실행을 요청하면 Actions 를 깨우고 화면은 `batch_runs` 상태만 폴링한다(20초, 도는 동안만). 깨우는 길이 둘인 이유는 토큰 권한 때문이다 (backtest.md 8.1)
- **가중치 반영 버튼은 두지 않았다.** 백테스트가 팩터 가중치를 20%씩 고정으로 돌려 반영할 값이 없고, 가중치를 찾아 주는 것은 backtest.md 5장이 금지한 파라미터 최적화다 (8.2)
- 월간 리밸런싱, 지표·자본곡선, 벤치마크 대비 초과수익
- **완료 기준**: 미래 재무를 참조하면 실패하는 look-ahead 방지 테스트, 비용 0과 비용 반영 결과가 다름을 확인, 동일 파라미터 재실행 시 동일 결과, 웹에서 요청한 실행이 완료되어 결과 화면에 뜬다

### Step 17 — 시스템 상태 화면 + 무응답 감시
- **완료 (2026-09-17).** `docs/health.md` 가 규칙의 단일 정의처. 화면 `/status`(들어가는 문은 모든 화면 아래 고지의 "시스템 상태" 링크), 감시 `GET /api/cron/health`(cron-job.org 1시간마다, `x-cron-secret`), 표 `health_alerts`(0025)
- 감시를 Actions 가 아니라 **웹 경로**에 뒀다(사용자 결정). Actions 예약으로 감시하면 Actions 가 멈출 때 감시도 함께 멈춘다. Actions 분도 쓰지 않는다
- **마감은 절대 시각이 아니라 개장 기준 오프셋**이다(`market_sessions.open_utc` ± 분). 서머타임·임시공휴일이 저절로 맞고, 그날 세션이 없으면 휴장일이라 아무것도 기대하지 않는다. 국내 일일 +20분, 미국 일일 +0분, 국내 감성 −30분 (health.md 2장)
- 달력(`market_sessions`)이 바닥나면 따로 알린다. 세션이 없으면 "휴장일" 과 구별되지 않아 감시가 조용해지기 때문이다 (2.1)
- 같은 대상·날짜·종류는 하루 한 번만. DB UNIQUE 가 막는다(1시간마다 도는 감시라 코드로 막으면 겹쳐 돌 때 새어 나간다)
- 화면: 무응답 감시 현황 → API 한도 게이지(80% 경고·100% 차단) → 데이터 신선도 → 최근 배치 25건(단계별 로그 펼침) → 크론 호출 기록
- **Actions 사용량은 넣지 않았다.** 사용량 API 가 계정 단위 admin 권한을 요구해 지금 토큰으로 읽을 수 없다 `[확인필요]`. 화면에 GitHub 사용량 링크를 둔다
- **완료 기준**: 한도 80%를 넘긴 API가 경고색으로 보이고, 배치를 일부러 실패시키면 화면과 텔레그램에 모두 드러나며, 워크플로를 꺼 두면 무응답 알림이 온다

### Step 18 — 마무리
- **완료 (2026-09-17).** `README.md`(처음 받아 돌리는 절차·문서 지도·시크릿 표), `scripts/backup_db.py` + `.github/workflows/backup.yml`, `docs/backup.md`
- 백업은 두 층이다: `essential`(다시 만들 수 없는 표만, 주 1회·90일 보관) / `full`(전부, 월 1회·30일). 아티팩트 용량이 곧 비용이라 나눴다
- 산출물은 gzip 한 SQL 파일이라 `sqlite3` 로 그대로 되살아난다. **덤프→복원→값 비교**를 테스트로 고정했다(`tests/test_backup.py`)
- 시크릿 목록의 단일 정의처는 `.env.example` 이다(항목마다 ACTIONS/VERCEL/LOCAL 표시). README 는 요약만 싣고 그쪽을 가리킨다
- 전체 회귀는 `tests.yml` 이 이미 배치(ruff + pytest)와 웹(tsc + vitest)을 둘 다 돌린다
- **완료 기준**: 저장소를 새로 받아 시크릿만 채우면 동작하는 절차가 문서대로 재현되고, DB 백업과 복구가 실제로 된다

---

## 8. 설계상 주의점

- **시장 간 비교 경계**: 한국과 미국 종목을 한 화면에 나란히 정렬할 때는 종합 점수 정렬을 시장별로 분리하거나 "시장 내 순위"를 함께 표시한다.
- **가중치 변경 후 과거 점수**: 가중치를 바꿔도 과거 `scores` 행은 그대로 둔다. 재계산이 필요하면 `calc_version`을 올려 새 행을 쌓는다.
- **SQLite 동시성**: 배치가 도는 동안 화면 쓰기가 잠길 수 있다. 쓰기 트랜잭션을 짧게 끊고 `busy_timeout`을 넉넉히 준다.
- **텔레그램 메시지 길이**: 길이 제한이 있으므로 리포트가 길면 분할 발송한다.
- **무료 한도 미확인 소스**: 한도를 모르는 상태에서 전 종목 루프를 돌리지 않는다. Step 3 전까지 수집 대상은 소수 종목으로 제한한다.
