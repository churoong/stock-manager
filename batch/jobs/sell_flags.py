"""매도 플래그 판정 배치 (docs/sell_flags.md, Step 13). 일일 배치가 포트폴리오 재계산 뒤에 부른다.

**읽고 플래그만 쓴다.** trades 를 만들거나 고치지 않는다(자동 매도 없음).

실행
  python -m batch.jobs.sell_flags
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db, visibility
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import sell_flags as sf
from batch.services import sentiment
from batch.services.accumulation import display_name
from batch.sources import dart

log = logging.getLogger("sell_flags")

JOB_NAME = "sell_flags"
#: 잣대가 바뀐 보유의 새 기준점을 찾을 때 한 번에 읽는 점수 행 수와 최대 쪽 수 (25.961·25.1092). 20×19 = 380행은
#: 거래일 1년 반 남짓 — 그보다 오래전에 바꾼 잣대 뒤 첫 점수까지 못 닿으면 "같은 잣대의 점수가 없음" 으로 말한다
BASELINE_PAGE = 20
BASELINE_SCAN_PAGES = 19


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _감성_기준일(country: object, today: date | None) -> str | None:
    """감성 나이를 잴 기준 = 그 시장의 직전 거래일 (docs/infra.md 25.401). 모르면 None(달력일로 잰다)."""
    if today is None or not country:
        return None
    try:
        # **그 시장의 현지 날짜에서** 직전 거래일을 잰다 (docs/infra.md 25.564, 감사 재현). `today` 는 사용자(한국)
        # 날짜라, 국내 아침 실행에서 미국은 현지로 아직 전날인데 한국 날짜로 재 기준일이 한 거래일 앞섰다 — 미국 감성
        # 배치(`trade_date` = 미국 현지 직전 거래일)보다 앞서 노동절 뒤 수요일 아침 감성이 "4일 묵음" 으로 꺼졌다가
        # 저녁에 다시 켜지며 [확인] 이 사라지고 NEW 가 다시 나갔다. 지나간 날을 손으로 줄 때는 그 날짜를 그대로 쓴다
        day = cal.local_today(str(country)) if today == cal.user_today() else today
        return cal.default_as_of(str(country), day)
    except cal.UnknownMarket:
        return None


def load_holdings(client: TursoClient, today: date | None = None) -> tuple[list[sf.HoldingInput], list[str]]:
    """보유 종목과 **못 읽은 것이 있으면 그 이유**  (docs/infra.md 25.164).

    "매수 당시 점수" 는 **지금 보유분**의 첫 매수다 — `trade_date >= positions.first_buy_date`(열린 로트의 최솟값).
    예전에는 이미 다 팔아 닫힌 옛 매수까지 보아, 팔았다 다시 산 종목이 옛 점수와 비교돼 거짓 "재무악화" 가 떴다
    (docs/infra.md 25.286).
    """
    rows = client.execute(
        "SELECT p.stock_id, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.country, p.horizon, p.first_buy_date,"
        " p.currency, p.cost, p.market_value, p.price_date,"
        " (SELECT sc.total_score FROM scores sc WHERE sc.stock_id = p.stock_id"
        "   ORDER BY sc.as_of_date DESC, sc.calc_version DESC LIMIT 1) AS score_now,"
        " (SELECT sc.as_of_date FROM scores sc WHERE sc.stock_id = p.stock_id"
        "   ORDER BY sc.as_of_date DESC, sc.calc_version DESC LIMIT 1) AS score_now_date,"
        " (SELECT t.score_at_trade FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy'"
        "   AND t.score_at_trade IS NOT NULL AND t.trade_date >= p.first_buy_date"
        "   ORDER BY t.trade_date, t.id LIMIT 1) AS score_at_buy,"
        " (SELECT t.snapshot_as_of FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy'"
        "   AND t.score_at_trade IS NOT NULL AND t.trade_date >= p.first_buy_date"
        "   ORDER BY t.trade_date, t.id LIMIT 1) AS score_at_buy_date,"
        # **판정에 쓴 투자 기간의 첫 매수일** (docs/infra.md 25.417). FIFO 라 첫 열린 로트 뒤의 매수는 모두 열려 있다.
        # 기간이 섞이면 25.406 이 원가가 큰 기간을 고르는데, 보유 기간을 전체 첫 매수부터 재면 장기 10주(1월) 뒤
        # 단기 100주(9/1)를 산 다음 날 "단기 92일 초과, 243일째" 가 떴다
        " (SELECT MIN(t.trade_date) FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy'"
        "   AND t.horizon = p.horizon AND t.trade_date >= p.first_buy_date) AS horizon_since"
        " FROM positions p JOIN stocks s ON s.id = p.stock_id"
        # **ETF 보유에는 매도 플래그를 세우지 않는다** (docs/infra.md 25.908, 감사). 플래그는 종목 규칙(목표·손절·점수
        # 악화·
        # 감성·유니버스)이다 — ETF 는 장기 적립·타이밍 무관(CLAUDE.md)이라 −25% 에 적색 "손절" 이 붙으면 적립을 팔라는
        # 말이 된다
        " WHERE s.asset_type = 'stock'"
    ).dicts()
    out: list[sf.HoldingInput] = []
    못읽음: set[str] = set()
    시장시작: dict[str, str | None] = {}
    잣대바뀐종목: list[str] = []
    for r in rows:
        sid = int(r["stock_id"])
        fin = client.execute(
            "SELECT fiscal_year, operating_income, report_date, currency FROM financials WHERE stock_id = ?"
            " AND report_code = ? AND financials.consolidated ="
            " (SELECT fb.consolidated FROM financials fb"
            " WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code"
            " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
            " ORDER BY fiscal_year DESC LIMIT 2",
            [sid, dart.ANNUAL_REPORT_CODE],
        ).dicts()
        uni = client.execute(
            "SELECT u.included, u.exclude_reason, u.snapshot_date FROM universe_members u"
            f" WHERE u.stock_id = ? AND u.snapshot_date = {db.latest_snapshot_sql()}",
            [sid, r["country"]],
        ).dicts()
        try:
            senti = client.execute(
                "SELECT sentiment, delta_7d, negative_count_7d, as_of_date FROM sentiment_scores WHERE stock_id = ?"
                " ORDER BY as_of_date DESC LIMIT 1",
                [sid],
            ).dicts()
        except Exception as e:  # noqa: BLE001 — 표가 없는 DB 는 정상, 나머지는 말한다
            senti = []
            # **못 읽은 것을 "감성 없음" 으로 만들지 않는다** (docs/infra.md 25.164).
            # 조용히 넘어가면 감성급락 플래그가 영영 안 뜨는 것을 아무도 모른다
            if not db.표가_없나(e):
                못읽음.add(str(e))
        s0 = senti[0] if senti else {}
        # 7일 전에도 이 종목 뉴스를 받고 있었나 (25.641, 교차검증). 과거 점수가 없는 까닭이 "기사가 적었다" 가
        # 아니라 "수집을 늦게 시작했다" 면 중립 기준(25.638)은 원래 부정적이던 종목을 "급락" 으로 만든다
        추적 = False
        if s0.get("as_of_date"):
            처음 = client.execute("SELECT MIN(fetched_at) FROM news WHERE stock_id = ?", [sid]).scalar()
            나라 = str(r["country"])
            # **기사가 없던 종목은 첫 기사 시각이 곧 악재가 몰린 날이다** (docs/infra.md 25.1100, 감사).
            # 종목별 첫 기사로만 재면 조용하던 종목에 악재가 몰린 주(25.638 이 잡으려던 바로 그 경우)에
            # "추적 전" 이 되어 급락을 보지 않았다.
            # 보유 종목은 매수일부터 수집 대상이다(국내 `news_targets ∪ positions ∪ watchlist`, 미국 보유 우선 25.909) —
            # **max(그 시장 수집 시작, 첫 매수일)** 부터 받고 있었다고 보고, 종목별 첫 기사와 둘 중 이른 쪽을 쓴다
            후보일 = [현지날짜(str(처음), 나라)] if 처음 else []
            시장 = _수집_시작(client, 나라, 시장시작)
            if 시장 and r["first_buy_date"]:
                후보일.append(max(현지날짜(시장, 나라), str(r["first_buy_date"])))
            # 과거 점수는 `as_of−7` 에서 끝나는 **30일 창**으로 낸다 — 그 창이 열릴 때(37일 전)부터 받고 있었어야
            # "과거가 비었다 = 기사가 적었다" 가 참이다. 7일이면 8일 전 시작한 종목이 여전히 거짓 급락이었다 (25.645,
            # 교차검증)
            창시작 = date.fromisoformat(str(s0["as_of_date"])) - timedelta(days=7 + sentiment.WINDOW_DAYS)
            # fetched_at 은 UTC 다 — **그 시장 현지 날짜**로 바꿔 견준다 (25.749, 감사). 앞 10글자(UTC 날짜)와 현지
            # 날짜를 견줘
            # KST 08:00 첫 수집이 전날로 읽혀 하루 일찍 "추적 중" 이 됐다
            추적 = bool(후보일) and min(후보일) <= 창시작.isoformat()
        # **두 점수가 같은 잣대인가** (docs/infra.md 25.209). 설정에서 팩터 가중치를 바꾸거나
        # 점수 계산 판이 바뀌면 종목은 그대로인데 점수만 20점 넘게 움직여 거짓 "재무악화" 가 뜬다.
        # 두 행의 잣대를 **둘 다 알 때만** 다르다고 한다 — 모르면 예전처럼 비교한다
        # **매수일 잣대는 매매에 적힌 점수를 낸 판으로** (docs/infra.md 25.1094, 감사 재현). 두 날 모두 그날 가장 높은
        # 판을
        # 읽어, 뒤에 `scores --as-of`·재계산으로 매수일을 새 판으로 다시 내면 매매의 옛 판 점수(80)와 지금 새 판
        # 점수(57)를
        # 같은 잣대로 보고 거짓 재무악화를 냈다. 그날 판이 여럿이면 매매 점수와 같은 값(소수 첫째 자리)의 판을 쓰고,
        # 맞는 것이 없으면 예전처럼 가장 높은 판
        잣대 = [
            client.execute(
                "SELECT weights_json, calc_version, sentiment_weight_used, total_score FROM scores"
                " WHERE stock_id = ? AND as_of_date = ?"
                " ORDER BY calc_version DESC LIMIT ?",
                [sid, day, 한도],
            ).dicts()
            for day, 한도 in ((r["score_at_buy_date"], 5), (r["score_now_date"], 1))
        ]
        if r["score_at_buy"] is not None:
            맞음 = [x for x in 잣대[0] if x["total_score"] is not None
                    and abs(float(x["total_score"]) - float(r["score_at_buy"])) < 0.051]  # fmt: skip
            잣대[0] = 맞음[:1] or 잣대[0][:1]
        else:
            잣대[0] = 잣대[0][:1]
        잣대가_바뀜 = bool(잣대[0] and 잣대[1]) and (
            _팩터가중치_다름(잣대[0][0]["weights_json"], 잣대[1][0]["weights_json"])
            or int(잣대[0][0]["calc_version"]) != int(잣대[1][0]["calc_version"])
            # **센티먼트 가중치도 잣대다** (docs/infra.md 25.762, 설정 감사). `weights_json` 에 없고 따로 적혀, 설정에서
            # 10 → 50 으로 바꾸면 감성이 나쁜 종목의 종합 점수가 설정 탓만으로 16점 빠져 거짓 "재무악화" 가 떴다.
            # 둘 다 알 때만 견준다(모르면 예전처럼)
            or _가중치_다름(잣대[0][0].get("sentiment_weight_used"), 잣대[1][0].get("sentiment_weight_used"))
        )
        # **잣대가 바뀌었으면 기준점을 다시 잡는다** (docs/infra.md 25.961). 예전에는 비교를 영영 끄기만 했다 — 계산
        # 판을 한 번 올리면(25.954·25.960) 그 전에 산 보유 **전부**가 "점수 20점 하락" 검사에서 영구히 빠졌다(10-06
        # 리포트 9종목 전부). 매수일 이후·지금 점수 전의 점수 가운데 **지금과 같은 잣대**(판·팩터 가중치·센티먼트
        # 가중치)인 가장 이른 행을 기준점으로 쓴다
        기준점: dict[str, Any] | None = None
        if 잣대가_바뀜 and r["score_at_buy_date"] and r["score_now_date"]:
            지금잣대 = 잣대[1][0]
            # **20행씩 넘겨 본다** (docs/infra.md 25.1092, 감사 재현). 앞 20행만 봐서, 매수 뒤 20거래일 넘게 지나
            # 가중치를
            # 바꾸면 그 20행이 모두 옛 가중치라 기준점을 영영 못 찾았다 — 매일 같은 20행이라 시간이 가도 그대로였다.
            # 읽기는 잣대가 바뀐 보유에서만, 찾으면 바로 멈춘다. 상한 `BASELINE_SCAN_PAGES` 쪽(약 1년 반)
            # 넘으면 예전처럼 "같은 잣대의 점수가 없음" 으로 말한다
            쪽 = 0
            while 기준점 is None and 쪽 < BASELINE_SCAN_PAGES:
                후보들 = client.execute(
                    "SELECT as_of_date, total_score, weights_json, sentiment_weight_used FROM scores"
                    " WHERE stock_id = ? AND as_of_date > ? AND as_of_date < ? AND calc_version = ?"
                    "   AND total_score IS NOT NULL ORDER BY as_of_date LIMIT ? OFFSET ?",
                    [sid, r["score_at_buy_date"], r["score_now_date"], int(지금잣대["calc_version"]),
                     BASELINE_PAGE, 쪽 * BASELINE_PAGE],
                ).dicts()  # fmt: skip
                for 후보 in 후보들:
                    if not (_팩터가중치_다름(후보["weights_json"], 지금잣대["weights_json"])
                            or _가중치_다름(후보.get("sentiment_weight_used"), 지금잣대.get("sentiment_weight_used"))):
                        기준점 = 후보
                        break
                if len(후보들) < BASELINE_PAGE:
                    break
                쪽 += 1
        # 오늘 점수가 없으면 검사가 원래 돌지 않는다 — "잣대가 달라 안 봤다" 는 거짓 까닭이 된다 (25.773, 교차검증)
        if 잣대가_바뀜 and 기준점 is None and r["score_at_buy"] is not None and r["score_now"] is not None:
            잣대바뀐종목.append(display_name(str(r["name"])))  # 미국 목록 이름 꼬리는 뗀다 (25.957)
        latest = fin[0] if fin else {}
        prev = fin[1] if len(fin) > 1 and int(fin[1]["fiscal_year"]) == int(fin[0]["fiscal_year"]) - 1 else {}
        excluded = uni[0] if uni and not uni[0]["included"] else None
        # 제외된 보유만 — 매수 때 쓰던 스냅샷부터 한 번이라도 들었나 (25.1095). 주 1회 스냅샷이라 1년에 52행 남짓
        들었나: bool | None = None
        if excluded is not None:
            try:
                최대 = client.execute(
                    "SELECT MAX(included) FROM universe_members WHERE stock_id = ? AND snapshot_date >= COALESCE("
                    " (SELECT MAX(snapshot_date) FROM universe_members WHERE stock_id = ? AND snapshot_date <= ?), '')",
                    [sid, sid, r["first_buy_date"]],
                ).scalar()
                들었나 = bool(최대)
            except Exception:  # noqa: BLE001 — 모르면 예전처럼 "빠졌습니다"
                들었나 = None
        out.append(
            sf.HoldingInput(
                stock_id=sid, name=str(r["name"]), horizon=r["horizon"], first_buy_date=str(r["first_buy_date"]),
                currency=str(r["currency"]), cost=float(r["cost"]), market_value=_f(r["market_value"]),
                price_date=r["price_date"], score_now=_f(r["score_now"]), score_now_date=r["score_now_date"],
                score_at_buy=(_f(기준점["total_score"]) if 기준점 else None) if 잣대가_바뀜 else _f(r["score_at_buy"]),
                score_at_buy_date=str(기준점["as_of_date"]) if 기준점 else r["score_at_buy_date"],
                score_rebased=기준점 is not None,
                op_income_latest=_f(latest.get("operating_income")), op_income_prev=_f(prev.get("operating_income")),
                fiscal_year_latest=latest.get("fiscal_year"), fin_report_date=latest.get("report_date"),
                op_income_currency=latest.get("currency"), op_income_prev_currency=prev.get("currency"),
                universe_excluded_reason=(excluded or {}).get("exclude_reason"),
                universe_date=(excluded or {}).get("snapshot_date"),
                sentiment_delta_7d=_f(s0.get("delta_7d")), sentiment_negative_7d=int(s0.get("negative_count_7d") or 0),
                sentiment_now=_f(s0.get("sentiment")),
                sentiment_tracked_7d=추적,
                sentiment_date=s0.get("as_of_date"),
                sentiment_ref_date=_감성_기준일(r["country"], today),
                horizon_since=r["horizon_since"],
                corporate_action_recent=_기업행위(client, sid, str(r["country"])),
                universe_included_since_buy=들었나,
            )
        )  # fmt: skip
    경고 = [f"감성을 읽지 못해 감성급락은 판정하지 않았습니다 — {이유}" for 이유 in sorted(못읽음)]
    if 잣대바뀐종목:
        경고.append(
            f"점수 가중치·팩터 구성·계산 판이 매수 때와 달라 '점수 20점 하락' 은 보지 않았습니다"
            "(같은 잣대의 점수가 아직 없음)"
            f" ({len(잣대바뀐종목)}종목:"
            f" {', '.join(잣대바뀐종목[:5])}) — 다른 잣대의 두 점수를 빼면 거짓 경보가 난다"
        )
    return out, 경고


#: 시장별 뉴스 수집 시작 시각(UTC ISO)을 한 번 재서 두는 설정 키 (25.1100). 수집 시작은 바뀌지 않으므로 매일 `news`
#: 전체를 훑지 않는다
NEWS_START_KEY = "news_collection_started"


def _수집_시작(client: TursoClient, country: str, 캐시: dict[str, str | None]) -> str | None:
    """그 시장의 뉴스 수집 시작 시각. 설정에 없으면 `news` 의 가장 이른 수집 시각을 한 번 재서 적는다 (25.1100).

    읽지 못하면 None — 예전처럼 종목별 첫 기사만 본다."""
    if country in 캐시:
        return 캐시[country]
    값: str | None = None
    try:
        저장 = db.get_setting(client, NEWS_START_KEY, {}) or {}
        값 = 저장.get(country) if isinstance(저장, dict) else None
        if not 값:
            값 = client.execute(
                "SELECT MIN(n.fetched_at) FROM news n JOIN stocks s ON s.id = n.stock_id WHERE s.country = ?",
                [country],
            ).scalar()
            if 값:
                db.set_setting(client, NEWS_START_KEY, {**(저장 if isinstance(저장, dict) else {}), country: str(값)})
    except Exception:  # noqa: BLE001 — 덧붙이는 추정이 판정을 막으면 안 된다
        값 = None
    캐시[country] = str(값) if 값 else None
    return 캐시[country]


def _기업행위(client: TursoClient, stock_id: int, country: str) -> bool:
    """최근 20거래일 안의 분할·병합 — 장중 감시(`monitor_targets.recent_action`, 25.533)와 같은 판정 (25.1093).

    읽지 못하면 False(예전과 같다) — 이 표시는 문장에 말을 덧붙일 뿐 플래그를 바꾸지 않는다."""
    from batch.jobs import monitor_targets

    try:
        return monitor_targets.recent_action(client, stock_id, "US" if country == "US" else "KR")
    except Exception:  # noqa: BLE001 — 덧붙이는 말이 판정을 막으면 안 된다
        return False


def _팩터가중치_다름(a: Any, b: Any) -> bool:
    """두 `weights_json` 이 **다른 설정**인가 (docs/infra.md 25.766, 설정 감사).

    저장되는 것은 설정값이 아니라 **그날 살아 있는 팩터끼리 다시 나눈 비율**이다(`scoring.total_score`). 팩터 하나가
    빠진 날(20×5 → 25×4) 문자열로 견주면 설정이 그대로여도 "다르다" 가 되어 20점 하락 검사가 꺼졌다.
    둘 다 가진 팩터끼리 다시 나눠 견준다. 읽지 못하면 예전처럼 문자열로 본다."""
    try:
        x, y = json.loads(a) if isinstance(a, str) else a, json.loads(b) if isinstance(b, str) else b
        # **팩터 구성이 다르면 같은 설정인지 알 수 없다 — 비교하지 않는다** (25.769, 교차검증). 25.766 은 공통 팩터
        # 비율만 봐
        # 매수일에 없던 팩터의 비중만 올린 설정 변경(성장 20 → 40)을 "같음" 으로 읽어, 설정 탓 하락이 섞인 거짓
        # 재무악화를 냈다.
        # 거짓 경보(매도로 이어질 수 있다)보다 검사를 건너뛰고 말하는 쪽이 보수적이다. 같은 구성이면 비율로
        # 본다(부동소수 차는 무시)
        # 한쪽에만 있는 팩터의 가중치가 0 이면 점수에 들어가지 않아 같은 잣대다 (25.773, 교차검증 — {50,50,0,0,0})
        한쪽만 = [k for k in set(x) ^ set(y) if float((x if k in x else y)[k]) != 0.0]
        if 한쪽만:
            return True
        공통 = [k for k in x if k in y]
        합x, 합y = sum(float(x[k]) for k in 공통), sum(float(y[k]) for k in 공통)
        if not 공통 or 합x <= 0 or 합y <= 0:
            return str(a) != str(b)
        return any(abs(float(x[k]) / 합x - float(y[k]) / 합y) > 1e-6 for k in 공통)
    except (TypeError, ValueError, AttributeError):
        return str(a) != str(b)


def _가중치_다름(a: Any, b: Any) -> bool:
    """두 센티먼트 가중치를 **둘 다 감성을 실제로 썼을 때만** 견준다 (25.762·25.763).

    감성이 없는 날(기사 5건 미만 등)은 `scoring.total_score` 가 0.0 을 적는다 — 설정값이 아니다.
    0 을 설정 변경으로 읽으면 매수일엔 감성이 있고 오늘은 없는(흔한) 종목마다 20점 하락 검사가 꺼졌다
    (자체 확인·교차검증). 설정을 바꾼 경우만 잡는다."""
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if x <= 0 or y <= 0:
        return False
    return abs(x - y) > 1e-9


def 현지날짜(utc_iso: str, market: str) -> str:
    """UTC ISO 시각의 그 시장 현지 날짜 (25.749). 읽지 못하면 앞 10글자(예전 규칙)."""
    try:
        시각 = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
        return 시각.astimezone(cal.market_tz(market)).date().isoformat()
    except (ValueError, KeyError):
        return utc_iso[:10]


def run(today: date | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # **사용자의 오늘이다** (docs/infra.md 25.125). 보유 기간과 근거표의 "언제 기준" 이
        # 이 값으로 찍힌다 — UTC 로 잡으면 국내 아침 배치에서 언제나 하루 전이 된다
        today = today or cal.user_today()
        day = today.isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=day)
        now = db.now_iso()
        # **반쪽 보유로 판정하지 않는다** (docs/infra.md 25.644). 재계산이 쓰다 말았으면 positions 가 일부만 있어
        # 빠진 종목의 손절 판정이 조용히 사라진다. 어제 플래그를 그대로 두고 실패로 남긴다(리포트가 "실패" 를 싣는다)
        from batch.jobs import portfolio as _pf

        if _pf.쓰는_중(client):
            이유 = (
                "포트폴리오 재계산이 끝나지 않아(보유 표가 반쪽일 수 있음) 매도 플래그를 판정하지 않았습니다"
                " — 어제 플래그 유지"
            )
            db.finish_batch_run(client, run_id, status="failed", error_text=이유)
            print(이유)
            return 1
        # **범위를 여기서도 본다** (2026-09-23, docs/infra.md 25.180). 예전에는
        # `jobs/signals` 만 검사하고 나머지 셋은 날것을 썼다 — 같은 설정으로 낸
        # 신호의 손절선과 매도 플래그의 손절선이 **서로 다를 수 있었다.**
        경고: list[str] = []
        rules, 설정경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None, 못읽음=경고))
        경고 += 설정경고
        holdings, 보유경고 = load_holdings(client, today)
        경고 += 보유경고
        flags = sf.sort_flags([f for h in holdings for f in sf.evaluate(h, today, rules)])

        # 이어지던 같은 (종목, 사유) 의 처음 걸린 날·확인 여부를 이어받는다.
        # **오늘 행도 본다** (docs/infra.md 25.198). 국내 아침·미국 저녁 배치가 같은 "사용자의 오늘"로
        # 둘 다 부른다. 예전에는 어제까지(`<`)만 봐서, 저녁 실행이 오늘 행을 지우고 다시 넣으며
        # **낮에 누른 "확인" 을 지웠다.** 어제 행은 아침 실행이 이미 비활성으로 돌렸으므로
        # 처음 걸린 날도 오늘로 바뀌어 NEW 가 다시 떴다. 날짜 순으로 읽어 **가장 최근 행이 이긴다**
        previous = {
            (int(r["stock_id"]), str(r["reason_code"])): r
            for r in client.execute(
                "SELECT stock_id, reason_code, first_seen_date, dismissed_at, created_at, rationale_data"
                " FROM sell_flags"
                " WHERE is_active = 1 AND as_of_date <= ? ORDER BY as_of_date",
                [day],
            ).dicts()
        }
        statements: list[tuple[str, list[Any]]] = [("DELETE FROM sell_flags WHERE as_of_date = ?", [day])]
        for f in flags:
            prev = previous.get((f.stock_id, f.reason_code))
            확인 = prev["dismissed_at"] if prev else None
            처음 = prev["created_at"] if prev else now
            # 어제 근거는 아래 `_criteria_of`(리포트와 같은 읽기)로 읽는다 — 같은 이름을 하나 더 만들어 뒤의 것에
            # 덮여 늘 빈 목록이 됐고, 확인이 매 배치 풀렸다 (25.566, 교차검증)
            if 확인 and not sf.keeps_dismissal(f.reason_code, _criteria_of(prev.get("rationale_data")), f.criteria):
                # 속 사유가 새로 생겼다 — 다시 알린다(25.564). **NEW 도 붙인다**: NEW 는 `created_at` 을 마지막 발송과
                # 견주므로 처음 걸린 시각을 이어받으면 다시 실려도 NEW 가 없었다 (25.566)
                확인 = None
                처음 = now
            statements.append((
                "INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data,"
                " is_active, first_seen_date, dismissed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                [f.stock_id, day, f.level, f.reason_code, f.rationale_text,
                 json.dumps({"criteria": f.criteria}, ensure_ascii=False),
                 prev["first_seen_date"] if prev else day, 확인,
                 # created_at 은 **처음 걸린 시각**이다 — 이어지는 동안 이어받는다 (docs/infra.md 25.501).
                 # 단, 속 사유가 새로 생겨 확인을 풀었으면 지금이다(다시 알림 = NEW, 25.566)
                 # NEW 를 리포트 발송 시각과 견주려면 날짜로는 모자랐다(같은 날 발송 뒤에 걸린 것을 못 가른다)
                 처음],
            ))  # fmt: skip
        # 옛 행은 비활성으로 돌린다. **풀린 것에만 `resolved_at` 을 찍는다** (docs/infra.md 25.564, 감사) — 오늘로
        # 이어진 플래그의 어제 행까지 찍어 문서("조건이 풀리면 찍힌다")와 달랐다. 오늘 행은 위에서 먼저 넣었다
        statements.append((
            "UPDATE sell_flags SET is_active = 0, resolved_at = CASE WHEN EXISTS ("
            "   SELECT 1 FROM sell_flags t WHERE t.as_of_date = ? AND t.stock_id = sell_flags.stock_id"
            "     AND t.reason_code = sell_flags.reason_code) THEN resolved_at ELSE COALESCE(resolved_at, ?) END"
            " WHERE is_active = 1 AND as_of_date < ?",
            [day, day, day],
        ))  # fmt: skip
        client.batch(statements)

        counts = {level: sum(1 for f in flags if f.level == level) for level in ("red", "yellow", "green")}
        db.finish_batch_run(client, run_id, status="success",
                            step_log={"holdings": len(holdings), "flags": len(flags), **counts,
                                      **({"warnings": 경고, "warning_count": len(경고)} if 경고 else {})})  # fmt: skip
        print(
            f"보유 {len(holdings)}종목 → 플래그 {len(flags)}개"
            f" (적 {counts['red']} · 황 {counts['yellow']} · 녹 {counts['green']})"
        )
        # **공개 저장소면 종목·근거 줄을 로그에 찍지 않는다** (25.979) — 보유 종목과 가격이 남이 보는 로그에 남는다.
        # 같은 내용은 DB(sell_flags)와 텔레그램·웹에 있다
        if visibility.is_public():
            print(f"  (공개 저장소 — 주의 {len(경고)}줄·플래그 {len(flags)}줄은 로그에 찍지 않음)")
            return 0
        for 줄 in 경고:
            print(f"  주의: {줄}")
        for f in flags:
            print(f"  [{f.level}] {f.rationale_text}")
        return 0
    finally:
        client.close()


def _criteria_of(raw: object) -> list:
    """`rationale_data` JSON 의 근거표 목록. 깨졌거나 없으면 빈 목록 — 화면이 "근거 0개" 경고를 그린다(25.71)."""
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    criteria = data.get("criteria") if isinstance(data, dict) else None
    return criteria if isinstance(criteria, list) else []


def _시각(text: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _보여준_적_없나(row: dict, as_of: str, last_shown: str | None) -> bool:
    """처음 걸린 시각이 이 나라 리포트의 마지막 발송 시각보다 뒤면 NEW (docs/infra.md 25.501)."""
    발송, 처음 = _시각(last_shown), _시각(row.get("created_at"))
    if 발송 is None or 처음 is None:
        return row["first_seen_date"] == as_of
    return 처음 > 발송


def active_flags_for_report(
    client: TursoClient, country: str, as_of: str, last_shown: str | None = None
) -> list[dict]:
    """리포트에 실을 플래그. 그 나라 보유 종목, 오늘 걸려 있고 사용자가 확인하지 않은 것.

    **종목 번호와 근거표도 싣는다** (docs/infra.md 25.262). 예전에는 문장만 실어, 웹 리포트 화면의 플래그 항목이
    종목으로 이어지지도(링크 없음) 근거를 펼치지도 못했다. CLAUDE.md: 근거에 쓴 수치는 펼쳐 볼 수 있어야 한다.
    """
    rows = client.execute(
        "SELECT f.stock_id, f.level, f.reason_code, f.rationale_text, f.rationale_data, f.first_seen_date,"
        " f.created_at,"
        " f.as_of_date, COALESCE(s.name_ko, s.name_en, s.ticker) AS name"
        " FROM sell_flags f JOIN stocks s ON s.id = f.stock_id"
        " WHERE s.country = ? AND f.is_active = 1 AND f.dismissed_at IS NULL"
        "   AND f.as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)",
        [country],
    ).dicts()
    order = sf.LEVEL_ORDER
    return [
        {
            "stock_id": r["stock_id"], "level": r["level"], "reason_code": r["reason_code"], "name": r["name"],
            # **이 나라 리포트가 아직 보여 주지 않은 것이 NEW** (docs/infra.md 25.495·25.501, 텔레그램 감사).
            # 예전에는 처음 걸린 날 == 오늘 이었다 — 국내 종목 플래그가 저녁 미국 배치에서 처음 걸리면 다음 날
            # 국내 리포트에서 한 번도 보여 준 적 없는데 NEW 가 빠졌다. 25.495 는 날짜로 견줘 **아침 발송 뒤 같은 날**
            # 걸린 것을 못 갈랐다 — 이제 처음 걸린 시각과 마지막 발송 시각을 견준다. 모르면 예전 규칙
            "rationale": r["rationale_text"],
            "new": _보여준_적_없나(r, as_of, last_shown),
            "criteria": _criteria_of(r.get("rationale_data")),
            # **판정일을 싣는다** (docs/infra.md 25.342). 화면이 리포트 거래일을 판정일로 적었는데, 판정은 사용자의
            # 오늘(월요일 국내 리포트면 거래일 금요일이 아니라 월요일)이고, 플래그 배치가 실패한 날엔 어제 것이다
            "as_of_date": r["as_of_date"],
        }
        for r in sorted(rows, key=lambda r: (order.get(str(r["level"]), 9), str(r["name"])))
    ]


def main() -> int:
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run()


if __name__ == "__main__":
    sys.exit(guard(main))
