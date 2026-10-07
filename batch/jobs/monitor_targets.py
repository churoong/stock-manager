"""장중 감시 준비 (docs/intraday.md, Step 14). 일일 배치 끝에 시장마다 돈다.

1. 거래 세션: 앞으로 14일의 정규장 시작·종료(UTC)를 market_sessions 에. 웹에는 휴장일 계산기가 없다
2. 감시 대상: 보유 종목 + 그 나라 최신 신호 종목을 monitor_targets 에 (문턱을 미리 계산해 둔다)
   관심 종목(watchlist)은 장중에 추가될 수 있어 웹 경로가 직접 읽는다

실행
  python -m batch.jobs.monitor_targets --market KR
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import sell_flags as sf

log = logging.getLogger("monitor_targets")

JOB_NAME = "monitor_targets"
SESSION_DAYS = 14


#: **수능일 국내 장은 한 시간 늦게 열고 늦게 닫는다**(10:00~16:30 KST) (docs/infra.md 25.350).
#: exchange_calendars 4.13.2 는 이날을 평일처럼 09:00~15:30 으로 준다 — 2026-09-27 이 세션에서 직접 돌려 확인했다
#: (새해 첫 거래일 10:00 개장은 맞게 준다). 그대로 두면 장중 경로가 15:55 뒤를 장 밖으로 보고
#: **마지막 한 시간의 손절·목표 터치를 영영 놓친다.** 해마다 거래소 공지로 한 줄씩 더한다(CLAUDE.md "연 1회 대조").
#: 2025-11-13 은 2026학년도 수능(지난 일, 대조용).
#: 2026-11-19 는 2027학년도 수능 `[확인필요: 교육부 확정 공고·거래소 공지]`.
KR_CSAT_DAYS = frozenset({"2025-11-13", "2026-11-19"})
CSAT_SHIFT = timedelta(hours=1)


def session_rows(market: str, start, days: int = SESSION_DAYS) -> list[tuple[str, str, str, str]]:
    """(현지 날짜, 개장 UTC, 폐장 UTC, 출처). 휴장일은 빠진다."""
    import pandas as pd

    xc = cal.exchange_calendar(market)  # 손으로 더한 휴장일 포함 (25.449)
    end = start + timedelta(days=days)
    sessions = xc.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    out = []
    for s in sessions:
        day = s.date().isoformat()
        opened = xc.session_open(s).tz_convert("UTC")
        closed = xc.session_close(s).tz_convert("UTC")
        source = f"exchange_calendars {cal.library_version()}"
        # 라이브러리가 이미 늦춰 주면(09:00 KST = 00:00 UTC 가 아니면) 두 번 늦추지 않는다
        if market.upper() == "KR" and day in KR_CSAT_DAYS and opened.hour == 0:
            opened, closed = opened + CSAT_SHIFT, closed + CSAT_SHIFT
            source += " + 수능일 1시간 늦춤(KR_CSAT_DAYS)"
        out.append((day, opened.isoformat(), closed.isoformat(), source))
    return out


def _단위_비율(client: TursoClient, stock_id: int, as_of: str, rationale_data: Any) -> float:
    """신호의 가격(매수 구간·목표·손절)을 **지금 단위**로 옮기는 비율 (docs/infra.md 25.218).

    신호가 묵은 채(신호 계산이 멈춘 날) 분할이 나면 옛 단위의 손절선을 실시간 시세와 대어 거짓 손절 알림이 난다.
    신호 날 적어 둔 기준 종가(`ref_close`, 25.215)와 **지금 계열의 그날 가격**(`db.SPLIT_ONLY_PRICE_SQL`)의 비로 옮긴다.
    가격 계열이 아직 다시 조정되지 않았으면(국내 수정주가는 사람이 돌린다, 25.138) 비율이 1 이라 예전과 같다.
    모르면 1 — 옮기지 않는다.
    """
    try:
        ref = (json.loads(rationale_data or "{}") or {}).get("ref_close")
    except (TypeError, ValueError):
        return 1.0
    if not ref or float(ref) <= 0:
        return 1.0
    rs = client.execute(
        f"SELECT {db.SPLIT_ONLY_PRICE_SQL} FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE p.stock_id = ? AND p.date = ?",
        [stock_id, as_of],
    ).rows
    지금 = rs[0][0] if rs else None
    return float(지금) / float(ref) if 지금 and float(지금) > 0 else 1.0


#: 기업행위 뒤 보유 목표·손절·거래량 감시를 멈추는 기간(거래일 행 수) —
#: 거래량 20일 평균이 분할 뒤 값으로 다 바뀌는 길이 (25.533)
ACTION_GUARD_ROWS = 20


def recent_action(client: TursoClient, stock_id: int, market: str) -> bool:
    """최근 `ACTION_GUARD_ROWS` 거래일 안에 분할·병합 같은 기업행위가 있었나 (docs/infra.md 25.533).

    국내: 그 종목 행의 (앞 종가, 종가, 거래소 등락률)로 조정 계수를 되살린다(`adjust.factor_of`, 25.138 과 같은 식).
    미국: 수정주가 재수집 대기열의 분할 표시(두 비율이 같음)가 그 기간 안에 감지됐나. 모르면 False.
    """
    from batch.services import adjust as adj

    if market == "US":
        # 분할 감지 기록 (`adjust_drift.SPLIT_LOG_KEY`, 25.535) — 대기열 표시는 배당 감지와 뒤엉켜 새 분할을 놓쳤다
        감지 = (db.get_setting(client, "us_split_detections", {}) or {}).get(str(stock_id))
        if not 감지:
            return False
        from datetime import UTC, datetime, timedelta

        return str(감지) >= (datetime.now(UTC) - timedelta(days=ACTION_GUARD_ROWS * 7 // 5)).isoformat()
    rows = client.execute(
        "SELECT close, change_pct FROM prices WHERE stock_id = ? AND close IS NOT NULL AND close > 0"
        " ORDER BY date DESC LIMIT ?",
        [stock_id, ACTION_GUARD_ROWS + 1],
    ).rows
    for (close, pct), (prev, _p) in zip(rows, rows[1:], strict=False):
        if adj.is_action(adj.factor_of(float(prev), float(close), None if pct is None else float(pct))):
            return True
    return False


def expected_signal_date(market: str, now: datetime | None = None) -> str:
    """이 목록이 쓰일 **다음 장**의 직전 거래일 (25.547, 교차검증).

    25.542 는 만드는 시각의 현지 날짜로 "직전 거래일" 을 냈다. 국내 저녁(21~24시 KST)이나 미국 낮(뉴욕 자정 전)에
    만들면 목록은 **다음 장**을 위한 것인데, 한 장 묵은 신호가 직전 거래일로 통과했다. 그래서 아직 닫히지 않은 첫
    세션을 찾고 그 앞 거래일을 낸다.
    """
    지금 = now or datetime.now(UTC)
    # 훑기 시작일도 `지금` 에서 낸다 — 실제 시계를 쓰면 `now` 를 준 테스트가 날짜에 묶였다 (25.548, 교차검증)
    오늘 = 지금.astimezone(cal.market_tz(market)).date() if now else cal.local_today(market)
    for day, _open, close, _src in session_rows(market, 오늘):
        if datetime.fromisoformat(close) > 지금:
            return cal.previous_session(market, date.fromisoformat(day)).isoformat()
    return cal.default_as_of(market)


def signal_date_current(signal_date: str, market: str) -> bool:
    """신호 기준일이 이 목록이 쓰일 장의 직전 거래일인가 (25.542·25.547)."""
    return signal_date == expected_signal_date(market)


def holding_levels(avg_price: float, horizon: str | None, targets: dict | None) -> tuple[float, float]:
    """보유 종목의 목표가·손절가. 매도 플래그와 같은 문턱(docs/sell_flags.md 1장)."""
    key = horizon if horizon in sf.DEFAULT_TARGETS else "long"
    rules = {**sf.DEFAULT_TARGETS[key], **((targets or {}).get(key) or {})}
    return avg_price * (1 + rules["target_pct"] / 100), avg_price * (1 + rules["stop_pct"] / 100)


def merge_zones(
    zones: list[tuple[float, float]], prev_close: float | None
) -> tuple[float | None, float | None]:
    """기간별 매수 구간을 감시 칸 하나(low, high)로 (docs/intraday.md 2장, docs/infra.md 25.287).

    **겹치는 구간만 합친다.** 예전에는 전부 min·max 로 합쳐, 단기 100~105 와 장기 90~95 가 떨어져 있으면
    90~105 가 되어 97원 — 어느 신호의 구간에도 없는 값 — 에서 "권장 매수 구간 진입" 이 나갔다.
    떨어진 무리가 여럿이면 칸이 하나뿐이라 **전일 종가에 가장 가까운 무리**를 고른다(오늘 들어올 수 있는 쪽).
    전일 종가를 모르면 가장 높은 무리 — 내려오는 가격이 먼저 만난다.
    """
    if not zones:
        return None, None
    merged: list[list[float]] = []
    for low, high in sorted(zones):
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    if len(merged) == 1 or prev_close is None:
        low, high = merged[-1]
        return low, high

    def distance(z: list[float]) -> float:
        return 0.0 if z[0] <= prev_close <= z[1] else min(abs(prev_close - z[0]), abs(prev_close - z[1]))

    low, high = min(merged, key=distance)
    return low, high


#: 분할 매수 계획을 이어 보는 기간(달력일) — 신호가 이보다 오래됐으면 계획은 끝난 것으로 본다 (25.999)
TRANCHE_PLAN_DAYS = 30


def next_tranche(client: TursoClient, stock_id: int, today: date) -> tuple[float | None, int | None, str | None]:
    """(다음 차수 가격, 차수, 계획 기준일). 최근 신호의 분할 계획을 신호 뒤 매수 기록 수로 이어 본다
    (docs/intraday.md 2절 tranche).

    매수 0번이면(계획 전에 산 보유) 이어 보지 않는다 — 1차를 산 사람만 2·3차를 기다린다. 3번 이상이면 끝났다."""
    rows = client.execute(
        "SELECT as_of_date, tranche_plan FROM signals WHERE stock_id = ? AND as_of_date >= ?"
        " ORDER BY as_of_date DESC, CASE horizon WHEN 'short' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END LIMIT 1",
        [stock_id, (today - timedelta(days=TRANCHE_PLAN_DAYS)).isoformat()],
    ).rows
    if not rows:
        return None, None, None
    기준일, 계획 = str(rows[0][0]), json.loads(rows[0][1] or "[]")
    산 = int(client.execute(
        "SELECT COUNT(*) FROM trades WHERE stock_id = ? AND side = 'buy' AND trade_date >= ?", [stock_id, 기준일]
    ).scalar() or 0)  # fmt: skip
    if 산 < 1 or 산 >= len(계획):
        return None, None, None
    다음 = 계획[산]
    가격 = 다음.get("price") if isinstance(다음, dict) else None
    return (float(가격), 산 + 1, 기준일) if 가격 else (None, None, None)


def build_targets(client: TursoClient, market: str, now: str) -> list[tuple]:
    # 범위 밖·반쪽 짝은 버리고 기본값을 쓴다 (docs/infra.md 25.180)
    targets_setting, 설정경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
    for 줄 in 설정경고:
        log.warning("%s", 줄)
    by_stock: dict[int, dict[str, Any]] = {}

    def base(r: dict) -> dict[str, Any]:
        sid = int(r["stock_id"])
        if sid not in by_stock:
            by_stock[sid] = {
                "stock_id": sid, "symbol": r["yahoo_symbol"], "name": r["name"], "currency": r["currency"],
                "reasons": [], "zones": [], "target_price": None, "stop_price": None,
                "dart": r.get("dart_corp_code"), "tranche": (None, None, None),
            }  # fmt: skip
        return by_stock[sid]

    for r in client.execute(
        "SELECT p.stock_id, s.yahoo_symbol, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency,"
        " s.dart_corp_code, p.avg_price, p.horizon, s.asset_type"
        " FROM positions p JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND s.yahoo_symbol IS NOT NULL",
        [market],
    ).dicts():
        t = base(r)
        t["reasons"].append("holding")
        # 기업행위 표시는 ETF 보다 **먼저** 본다 (25.925, 감사). 웹은 이 표시로 관심 목표가를 거르는데, ETF 를 먼저
        # 건너뛰어 보유+관심 ETF 가 분할 뒤 20거래일 동안 분할 전 관심 목표가로 "도달" 알림을 받을 수 있었다
        경계 = recent_action(client, int(r["stock_id"]), market)
        if 경계:
            t["reasons"].append("action_guard")
        if r.get("asset_type") == "etf":
            # **ETF 보유에는 목표·손절선을 대지 않는다** (docs/infra.md 25.908, 감사). ETF 는 10~20년 정액 적립·타이밍
            # 무관(CLAUDE.md)인데,
            # 기간을 비운 매매가 "장기" 로 읽혀 −25% 에 "손절선 터치" 가 나갔다 — 폭락장 바닥에서 적립을 팔라는 말이
            # 된다.
            # 급등락·거래량 알림은 보유 종목과 같이 받는다
            continue
        if 경계:
            # **기업행위 뒤에는 평균단가로 목표·손절을 대지 않는다** (docs/infra.md 25.533, 감사 재현). 평균단가는
            # 사용자가 매매 기록을 고치기 전까지 분할 전 값이라, 1:5 분할 뒤 19,800원 시세에 "손절선 93,000원 터치"
            # 가 매일 나갔다(25.218 은 보유는 해당 없다고 적었지만 틀렸다). 매도 플래그·리포트가 수량 확인을 따로 말한다
            continue
        t["target_price"], t["stop_price"] = holding_levels(float(r["avg_price"]), r["horizon"], targets_setting)
        # 분할 매수 계획의 다음 차수 (docs/intraday.md 2절 tranche, 25.999) — 기업행위·ETF 는 위에서 이미 건너뛰었다
        t["tranche"] = next_tranche(client, int(r["stock_id"]), cal.local_today(market))

    # **신호를 마지막으로 계산한 날** — 아침 리포트와 같은 잣대 (docs/infra.md 25.407, 25.337).
    # 예전에는 `signals` 의 MAX 였다. 오늘 계산했는데 한 건도 안 걸리면 MAX 가 어제로 남아 **어제 신호를
    # 오늘 추천으로 지켜보고** 매수 구간·목표·손절 알림을 냈다. 오늘을 넘는 날짜(25.234)도 잡지 않는다
    기준일 = db.last_signal_calc_date(client, market, cal.local_today(market).isoformat())
    # **직전 거래일 신호가 아니면 신호 종목을 싣지 않는다** (docs/infra.md 25.542, 교차검증). 신호 계산이 실패하거나
    # 건너뛰어도(25.526) 이 작업은 돈다 — 그러면 `built_at` 은 오늘인데 내용은 어제 추천인 목록이 생겨, 웹의 신선도
    # 판정(25.534)을 통과해 어제 매수 구간 알림이 나갔다. 보유·관심 감시는 그대로다
    if 기준일 and not signal_date_current(기준일, market):
        log.warning("신호 기준일 %s 가 직전 거래일이 아니라 신호 종목을 감시에 넣지 않습니다", 기준일)
        기준일 = None
    for r in client.execute(
        "SELECT sg.stock_id, s.yahoo_symbol, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency,"
        " NULL AS dart_corp_code, sg.horizon, sg.buy_zone_low, sg.buy_zone_high, sg.target_price, sg.stop_price,"
        " sg.as_of_date, sg.rationale_data"
        " FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        # 상장 상태 조건 — 추천 카드·리포트와 같다(25.802). 제외·폐지된 종목의 신호로 매수 구간·목표 알림을 보내지
        # 않는다 (25.805)
        " WHERE s.country = ? AND s.yahoo_symbol IS NOT NULL AND sg.as_of_date = ? AND s.status = 'active'"
        # 그 나라·그 기준일의 가장 새 판 하나만 — 옛 판의 목표·손절이 섞이지 않게 (docs/infra.md 25.410·25.423)
        "   AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
        # 바깥 행을 가리키지 않는다 — 가리키면 신호 행마다 다시 돌아 읽는 행이 수백 배가 됐다 (25.428)
        "     WHERE s3.country = ? AND c.as_of_date = ?)"
        # 순서를 고정한다. 정렬이 없으면 reasons 순서가 실행마다 달라질 수 있다
        " ORDER BY sg.stock_id, CASE sg.horizon WHEN 'short' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END",
        [market, 기준일 or "", market, 기준일 or ""],
    ).dicts():
        t = base(r)
        t["reasons"].append(f"signal:{r['horizon']}")
        k = _단위_비율(client, int(r["stock_id"]), str(r["as_of_date"]), r["rationale_data"])
        # 구간은 모아 두었다가 전일 종가를 안 뒤에 고른다(merge_zones)
        t["zones"].append((float(r["buy_zone_low"]) * k, float(r["buy_zone_high"]) * k))
        if "holding" not in t["reasons"]:
            # **기간이 여럿이면 먼저 닿는 선** — 목표는 가장 낮게, 손절은 가장 높게 (docs/infra.md 25.287).
            # 예전에는 첫 행(단기)의 값만 남아 "어느 신호든" 이라는 구간 규칙과 따로 놀았다
            목표 = None if r["target_price"] is None else float(r["target_price"]) * k
            손절 = None if r["stop_price"] is None else float(r["stop_price"]) * k
            if 목표 is not None:
                t["target_price"] = 목표 if t["target_price"] is None else min(t["target_price"], 목표)
            if 손절 is not None:
                t["stop_price"] = 손절 if t["stop_price"] is None else max(t["stop_price"], 손절)

    rows = []
    for t in by_stock.values():
        stats = client.execute(
            "SELECT (SELECT close FROM prices WHERE stock_id = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1),"
            " (SELECT CASE WHEN COUNT(*) = 20 AND MIN(date) >= date(MAX(date), '-45 days') THEN AVG(volume) END"
            "   FROM (SELECT volume, date FROM prices WHERE stock_id = ? AND volume > 0"
            "   ORDER BY date DESC LIMIT 20))",
            [t["stock_id"], t["stock_id"]],
        ).rows[0]
        zone_low, zone_high = merge_zones(t["zones"], stats[0])
        # 20일 평균은 **거래가 있은 최근 20일, 45일 안에서만** 낸다 (docs/infra.md 25.635, 감사). 예전에는
        # 거래량 0 인 날(정지일)과 몇 달 전 행이 섞여 재개 뒤 평소 1.5배 거래에도 "20일 평균 3배" 가 났다.
        # 모자라면 None — 거래량 판정을 끈다(웹 route 와 같은 식)
        # 20일 평균 거래량은 조정하지 않은 거래소 원자료다 — 분할 뒤 며칠은 "거래량 N배" 가 거짓이다 (25.533)
        평균거래량 = None if recent_action(client, t["stock_id"], market) else stats[1]
        rows.append((
            market, t["stock_id"], t["symbol"], t["name"], t["currency"], json.dumps(t["reasons"]),
            zone_low, zone_high, t["target_price"], t["stop_price"], stats[0], 평균거래량,
            t["dart"] if "holding" in t["reasons"] else None, now, *t["tranche"],
        ))  # fmt: skip
    return rows


# 뉴스 수집 후보에 넣을 점수 상위 종목 수. 웹이 1분마다 한 종목씩 가져가므로 하루 1,440종목까지 돈다.
# 설정 sentiment_target_top_n 으로 바꾼다(0 이면 웹이 수집을 건너뛴다)
NEWS_TOP_N = 200


def news_target_rows(client: TursoClient, market: str, monitor: list[tuple], now: str) -> list[tuple]:
    """뉴스 수집 후보 (docs/infra.md 24절). 점수 상위 + 감시 대상(보유·관심·추천).

    **웹이 1분마다 고르지 않고 여기서 하루 한 번 고른다.** 웹 안에서 고르면 scores 를 매번 두 번
    훑어 읽기 한도를 태운다. 점수는 하루 한 번 바뀌므로 후보도 하루 한 번이면 된다.
    """
    country = market
    # **범위를 보고, 0 은 0 이다** (docs/infra.md 25.356). 예전에는 범위 검사 없이 읽어 5000 이 그대로 쓰였고
    # (웹 상한 500), 0 은 200 으로 바꿔 읽었다 — 웹 뉴스 크론은 0 을 "수집 끔" 으로 읽는다. 두 곳이 한 설정을
    # 다르게 알면 안 된다. 범위 밖이면 기본값으로 돌아가고 그 사실을 남긴다(settings_range, 25.170)
    top_n_raw, 경고 = db.get_setting_in_range(client, "sentiment_target_top_n", NEWS_TOP_N)
    if 경고:
        log.warning(경고)
    # **정수만** — 웹 스키마는 `.int()` 다. 0.5 를 `int()` 로 0(수집 끔)으로 읽어 웹과 갈렸다 (25.629)
    if isinstance(top_n_raw, float) and not top_n_raw.is_integer():
        log.warning("설정 sentiment_target_top_n 이 정수가 아니라(%s) 기본값 %d 을 씁니다", top_n_raw, NEWS_TOP_N)
        top_n_raw = NEWS_TOP_N
    top_n = int(top_n_raw) if isinstance(top_n_raw, (int, float)) else NEWS_TOP_N
    rows: list[tuple] = []
    seen: set[int] = set()
    rs = client.execute(
        "SELECT sc.stock_id FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
        " WHERE s.country = ? AND s.status = 'active' AND sc.total_score IS NOT NULL"
        "   AND sc.as_of_date = (SELECT MAX(x.as_of_date) FROM scores x JOIN stocks sx ON sx.id = x.stock_id"
        "                        WHERE sx.country = ?)"
        # 판을 안 걸면 같은 종목이 두 번 걸려 **상위 N 자리를 중복이 먹는다** —
        # 지켜보는 종목이 그만큼 줄어든다 (2026-09-21, docs/infra.md 25.91)
        "   AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c"
        "                          WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)"
        " ORDER BY sc.total_score DESC LIMIT ?",
        [country, country, top_n],
    )
    for rank, row in enumerate(rs.rows, start=1):
        stock_id = int(row[0])
        seen.add(stock_id)
        rows.append((market, stock_id, rank, "score", now))
    for row in monitor:
        stock_id = int(row[1])
        if stock_id not in seen:
            seen.add(stock_id)
            rows.append((market, stock_id, None, "monitor", now))
    return rows


def run(market: str) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.local_today(market)
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=market, trade_date=today.isoformat(),
                                    )  # fmt: skip
        now = db.now_iso()
        sessions = session_rows(market, today)
        # **비었으면 지우지 않는다** (2026-09-24, docs/infra.md 25.189).
        #
        # 아래 첫 문장이 오늘 이후 세션을 **지우고** 다시 넣는다. `sessions` 가 비면
        # 지우기만 하고 끝나고, `market_sessions` 가 비면 **장중 감시가 조용히 멈춘다**
        # (25.104 — 세션을 못 찾아 시세를 아예 안 받는다).
        #
        # 14일 창이 통째로 휴장일 수는 없다. 비었다면 달력 쪽이 고장 난 것이고,
        # 그때 할 일은 **덮어쓰지 않고 멈추는 것**이다. 지금 있는 달력이 낡았더라도
        # 빈 달력보다는 낫다 — 낡은 것은 25.104 의 무응답 감시가 알린다.
        # 25.166(미국 유니버스를 하루치 실패로 통째로 비운 일)과 같은 모양이다.
        if not sessions:
            까닭 = (
                f"{market} 거래일 달력을 한 줄도 만들지 못했습니다"
                f" (exchange_calendars {cal.library_version()}, {today} 부터 {SESSION_DAYS}일)."
                " 지금 달력을 덮어쓰지 않고 멈춥니다 — 비우면 장중 감시가 멈춥니다"
            )
            # **실행 기록을 닫고 나간다.** 안 닫으면 `batch_runs` 에 `running` 이 남아
            # /status 가 "실패" 가 아니라 "멈춘 듯" 으로 보여 준다 (25.135 가 잡는 모양)
            db.finish_batch_run(client, run_id, status="failed", error_text=까닭)
            raise RuntimeError(까닭)
        세션_문장: list[tuple[str, list[Any]]] = [
            ("DELETE FROM market_sessions WHERE market = ? AND date >= ?", [market, today.isoformat()]),
        ] + [
            ("INSERT INTO market_sessions (market, date, open_utc, close_utc, source) VALUES (?, ?, ?, ?, ?)",
             [market, *row])
            for row in sessions
        ]  # fmt: skip
        # **반쪽 보유로 목록을 만들지 않는다** (docs/infra.md 25.648, 교차검증). 빠진 보유가 장중 손절 감시에서 조용히
        # 빠진다. 어제 목록을 그대로 두고(보유 전부가 있다) 실패로 남긴다 — 웹은 묵은 목록으로 보고
        # 신호 값만 뺀다(25.534). **세션은 그래도 쓴다** (25.650, 교차검증) — 보유와 무관한데 함께 건너뛰어,
        # 재계산이 날마다 같은 까닭으로 실패하면 14일 뒤 장중 경로 전체가 "세션 정보 없음" 으로 멈췄다
        from batch.jobs import portfolio as _pf

        if _pf.쓰는_중(client):
            client.batch(세션_문장)
            이유 = (
                "포트폴리오 재계산이 끝나지 않아(보유 표가 반쪽일 수 있음) 감시 목록을 새로 만들지 않았습니다"
                " — 어제 목록 유지 (거래 세션은 새로 씀)"
            )
            db.finish_batch_run(client, run_id, status="failed", error_text=이유)
            print(이유)
            return 1
        targets = build_targets(client, market, now)

        statements: list[tuple[str, list[Any]]] = [
            *세션_문장,
            ("DELETE FROM monitor_targets WHERE market = ?", [market]),
            ("DELETE FROM news_targets WHERE market = ?", [market]),
        ]
        statements += [
            ("INSERT INTO monitor_targets (market, stock_id, yahoo_symbol, name, currency, reasons, buy_zone_low,"
             " buy_zone_high, target_price, stop_price, prev_close, avg_volume_20d, dart_corp_code, built_at,"
             " next_tranche_price, next_tranche_step, tranche_plan_date)"
             " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row))
            for row in targets
        ]  # fmt: skip
        news_targets = news_target_rows(client, market, targets, now)
        statements += [
            ("INSERT INTO news_targets (market, stock_id, rank, reason, built_at) VALUES (?, ?, ?, ?, ?)", list(row))
            for row in news_targets
        ]
        client.batch(statements)
        db.finish_batch_run(client, run_id, status="success",
                            step_log={"sessions": len(sessions), "targets": len(targets),
                                      "news_targets": len(news_targets)})  # fmt: skip
        print(f"{market} 세션 {len(sessions)}일, 감시 대상 {len(targets)}종목, 뉴스 후보 {len(news_targets)}종목")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="장중 감시 준비")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.market)


if __name__ == "__main__":
    sys.exit(guard(main))
