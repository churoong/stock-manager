"""모의 투자사의 조사 자료 적재 — 부서들이 볼 원자료를 읽어 `Dossier` 를 채운다.

docs/company.md 2·5장, docs/infra.md 25.1463. 판단은 `services/company` 가 한다. 여기서는 **읽기만** —
그리고 미래 정보가 끼지 않게 모든 읽기를 신호 기준일(`as_of`) 이하로 묶는다. 같은 규칙을 두 곳에 적지 않는다:
성과 지표는 `jobs/signals.load_metrics`(창 3Y→1Y, 판), 감성은 `jobs/scores.load_sentiments`(신선도), 국면은
`services/trend.regimes_for_country` 를 그대로 부른다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import median
from typing import Any

from batch.core import calendar as cal
from batch.services import company as co
from batch.services import report_picks, trend

#: 증권사 의견 — 목표가가 있는 의견과 **그 의견 날짜 이하의 마지막 종가** (발표 뒤 값을 쓰지 않는다)
BROKER_SQL = (
    "SELECT o.stock_id, o.date, o.broker, o.target_price,"
    " (SELECT p.close FROM prices p WHERE p.stock_id = o.stock_id AND p.date <= o.date AND p.close > 0"
    "  ORDER BY p.date DESC LIMIT 1) AS close"
    " FROM kr_opinions o WHERE o.stock_id IN (SELECT value FROM json_each(?)) AND o.date > ? AND o.date <= ?"
    " AND o.target_price > 0"
)
#: 수급 — 외국인·기관 순매수 대금(백만원). 투자자별 금액이 있는 행만 센다(공매도만 있는 행 제외, 25.1042)
FLOWS_SQL = (
    "SELECT stock_id, date, frgn_net_amt, orgn_net_amt FROM kr_flows"
    " WHERE stock_id IN (SELECT value FROM json_each(?)) AND date > ? AND date <= ?"
    " AND frgn_net_amt IS NOT NULL AND orgn_net_amt IS NOT NULL"
    " ORDER BY stock_id, date DESC"
)
#: 20일 평균 거래대금(원) — 그 나라 기준일 이하 최신 유니버스 스냅샷의 판정값
TURNOVER_SQL = (
    "SELECT u.stock_id, u.avg_turnover_20d FROM universe_members u"
    " WHERE u.stock_id IN (SELECT value FROM json_each(?)) AND u.snapshot_date = (SELECT um.snapshot_date"
    " FROM universe_members um CROSS JOIN stocks su ON su.id = um.stock_id"
    " WHERE su.country = ? AND um.snapshot_date <= ?"
    " ORDER BY um.snapshot_date DESC LIMIT 1)"
)
GOV_SQL = (
    "SELECT stock_id, day, type FROM governance_events WHERE stock_id IN (SELECT value FROM json_each(?))"
    " AND day > ? AND day <= ? ORDER BY day DESC"
)
#: 공시 사건 표가 덮은 구간 — 매주 통째로 다시 쓰여 행마다 같은 덮개를 적는다(0063). 비면 덮개 모름
GOV_COVER_SQL = "SELECT MIN(covered_from), MAX(covered_to) FROM governance_events"
#: 주간 사건 표 덮개 끝(금요일) 뒤 ~ 기준일까지 **날마다** 시장 전체 거래소 공시를 받은 날
#: (`disclosure_coverage` kind I — 평일 18:17 KST 수집, 주말도 받는다). 이 구간이 다 차 있으면 그 사이 사건을
#: 공시 표에서 보충해 기준일까지 덮은 것으로 본다(25.1576, 위원회 감사 [필수] — 예전엔 금요일에만 덮여
#: 화~금 배치는 공시부가 "자료 없음" 이었다)
GOV_DAILY_COVER_SQL = "SELECT day FROM disclosure_coverage WHERE kind = 'I' AND day > ? AND day <= ?"
#: 그 구간의 안건 종목 공시 — 종목 칸이 비고 고유번호로만 이어진 줄도(`REACTION_EVENTS_SQL` 과 같은 이음). 색인
#: (stock_id, disclosed_at) 으로 날짜 범위만 읽는다
GOV_RECENT_SQL = (
    "SELECT d.stock_id, d.disclosed_at, d.title, d.receipt_no FROM disclosures d"
    " WHERE d.stock_id IN (SELECT value FROM json_each(?)) AND d.disclosed_at >= ? AND d.disclosed_at <= ?"
    " UNION ALL SELECT s.id, d.disclosed_at, d.title, d.receipt_no FROM stocks s JOIN disclosures d"
    " ON d.stock_id IS NULL AND d.corp_code = s.dart_corp_code"
    " WHERE s.id IN (SELECT value FROM json_each(?)) AND d.disclosed_at >= ? AND d.disclosed_at <= ?"
)
#: 보충 구간의 전환사채 발행결정 공시가 상세(`cb_issuances`)를 받았나 — 상세는 주 1회(금요일)만 받는다
#: `no_row`(그날 DART 상세에 행이 없었음)는 받은 것으로 치지 않는다 — 수집이 2주 안에 다시 부른다(25.1580)
CB_RECEIVED_SQL = (
    "SELECT receipt_no FROM cb_issuances WHERE receipt_no IN (SELECT value FROM json_each(?)) AND status = 'ok'"
)
CB_SQL = (
    "SELECT stock_id, SUM(shares_pct) FROM cb_issuances WHERE stock_id IN (SELECT value FROM json_each(?))"
    " AND status = 'ok' AND decided_at > ? AND decided_at <= ? GROUP BY stock_id"
)
#: 수급 행을 이만큼(달력일) 앞부터 읽는다 — 5거래일 + 연휴 여유
FLOW_LOOKBACK_DAYS = 14
#: `kr_flows` 의 대금 단위(백만원) → 원
FLOW_AMOUNT_UNIT = 1_000_000


def _ids(ids: list[int]) -> str:
    return json.dumps(sorted(set(ids)))


def agenda_rows(rows: list[report_picks.SignalRow], size: int) -> list[report_picks.SignalRow]:
    """안건 — 근거표가 있는 신호 종목 가운데 종합 점수 상위 `size` 개(같으면 티커 순). 종목마다 첫 행 하나."""
    한종목: dict[int, report_picks.SignalRow] = {}
    for r in report_picks.with_criteria(rows):
        한종목.setdefault(r.stock_id, r)
    return sorted(한종목.values(), key=lambda r: (r.total_score is None, -(r.total_score or 0), r.ticker))[:size]


def _broker_upsides(client: Any, ids: list[int], as_of: str) -> dict[int, list[tuple[str, str, float]]]:
    """{종목: [(의견 날짜, 증권사, 상승여력)]} — 기준일 앞 90일, 목표가 있고 그 의견 날짜의 종가를 잡은 것만."""
    out: dict[int, list[tuple[str, str, float]]] = {}
    시작 = (date.fromisoformat(as_of[:10]) - timedelta(days=co.BROKER_DAYS)).isoformat()
    for sid, day, 증권사명, 목표, 종가 in client.execute(BROKER_SQL, [_ids(ids), 시작, as_of]).rows:
        if 종가:
            out.setdefault(int(sid), []).append((str(day), str(증권사명), float(목표) / float(종가) - 1))
    return {k: sorted(v) for k, v in out.items()}


def population_dossiers(
    client: Any, rows: list[report_picks.SignalRow], country: str, as_of: str,
) -> list[co.Dossier]:
    """점수 부서의 백분위 모집단 — 그날 그 시장 **신호 종목 전체**(근거표가 있는 것). 팩터와 성과 지표만 (25.1465).

    안건(점수 상위 20·30) 안에서 순위를 내면 회사마다 안건 크기에 따라 같은 종목의 입장이 바뀌었다(검증 [필수] 1)
    """
    from batch.jobs import signals as signals_job

    한종목: dict[int, report_picks.SignalRow] = {}
    for r in report_picks.with_criteria(rows):
        한종목.setdefault(r.stock_id, r)
    if not 한종목:
        return []
    지표 = signals_job.load_metrics(client, country, as_of, sorted(한종목))
    # 증권사 의견도 — 증권사리서치부는 중앙 상승여력의 신호 종목 안 순위로 판단한다 (25.1467)
    증권사 = _broker_upsides(client, sorted(한종목), as_of) if country == "KR" else {}
    return [co.Dossier(
        stock_id=sid, ticker=r.ticker, as_of=as_of, total_score=r.total_score, domestic=country == "KR",
        broker_upsides=증권사.get(sid) or [],
        factors={k: r.factor_scores.get(k) for k in ("value", "quality", "growth", "momentum")},
        mdd=(지표.get(sid) or {}).get("mdd"), volatility=(지표.get(sid) or {}).get("volatility_ann"),
        perf_window=(지표.get(sid) or {}).get("window"), perf_as_of=(지표.get(sid) or {}).get("as_of_date"),
    ) for sid, r in sorted(한종목.items())]  # fmt: skip


def recent_sessions(market: str, as_of: str, n: int) -> list[str]:
    """기준일을 포함한 최근 n 거래일 (기준일이 휴장일이면 그 앞 거래일부터)."""
    날 = date.fromisoformat(as_of[:10])
    out: list[str] = []
    if cal.is_session(market, 날):
        out.append(날.isoformat())
    while len(out) < n:
        날 = cal.previous_session(market, 날)
        out.append(날.isoformat())
    return out


def load_dossiers(client: Any, rows: list[report_picks.SignalRow], country: str, as_of: str) -> list[co.Dossier]:
    """안건 종목들의 조사 자료. `rows` 는 `agenda_rows` 결과(종목마다 한 행), `as_of` 는 신호 기준일.

    국내만 증권사·수급·공시 자료를 읽는다 — 미국은 `domestic=False` 로 그 부서들이 "자료 없음" 을 낸다
    """
    from batch.jobs import scores as scores_job
    from batch.jobs import signals as signals_job

    ids = [r.stock_id for r in rows]
    if not ids:
        return []
    기준 = date.fromisoformat(as_of[:10])
    국내 = country == "KR"
    지표 = signals_job.load_metrics(client, country, as_of, ids)
    감성날: dict[int, str] = {}
    감성, 감성오류 = scores_job.load_sentiments(client, country, as_of, ids, dates=감성날)
    국면 = trend.regimes_for_country(client, country, as_of)

    증권사: dict[int, list[tuple[str, str, float]]] = {}
    수급: dict[int, list[tuple[str, float]]] = {}
    거래대금: dict[int, float] = {}
    사건: dict[int, list[tuple[str, str]]] = {}
    전환사채: dict[int, float] = {}
    덮음 = False
    보충종목: set[int] = set()  # 사건을 공시 표에서 보충한 종목 — 근거표 출처가 다르다(25.1577)
    전환모름: set[int] = set()  # 상세를 못 받은 전환사채 결정이 있는 종목
    if 국내:
        덮개 = client.execute(GOV_COVER_SQL).rows
        보충끝: str | None = None  # 주간 표 덮개 끝 — 그 뒤 사건은 공시 표에서 보충한다
        if 덮개 and 덮개[0][0] and 덮개[0][1]:
            시작덮음 = str(덮개[0][0]) <= (기준 - timedelta(days=co.GOV_DAYS)).isoformat()
            끝 = str(덮개[0][1])[:10]
            if 시작덮음 and 끝 >= as_of[:10]:
                덮음 = True
                # 덮개 끝이 기준일 그날(월요일 배치 — 주간 표가 금요일 18:17 에 지어졌다)이면 그날 뒤 공시도 보충한다
                # (25.1580, 25.1577 반박 [권고] 1 — 금요일 저녁 횡령 공시가 월요일 배치에서 빠졌다)
                보충끝 = 끝 if 끝 == as_of[:10] else None
            elif 시작덮음:
                받은날 = {str(r[0])[:10] for r in client.execute(GOV_DAILY_COVER_SQL, [끝, as_of[:10]]).rows}
                d, 빈날 = date.fromisoformat(끝) + timedelta(days=1), False
                while d <= 기준:
                    빈날 = 빈날 or d.isoformat() not in 받은날
                    d += timedelta(days=1)
                if not 빈날:
                    덮음, 보충끝 = True, 끝
        # 수급은 **최근 5거래일이 다 있어야** — 그 종목에 기준일 행이 빠졌으면 더 묵은 5일을 쓰게 된다
        # (25.1465 검증 권장)
        최근날 = recent_sessions("KR", as_of, co.FLOW_DAYS)
        증권사 = _broker_upsides(client, ids, as_of)
        for sid, day, 외, 기 in client.execute(
            FLOWS_SQL, [_ids(ids), (기준 - timedelta(days=FLOW_LOOKBACK_DAYS)).isoformat(), as_of]
        ).rows:
            if str(day) in 최근날:
                수급.setdefault(int(sid), []).append((str(day), (float(외) + float(기)) * FLOW_AMOUNT_UNIT))
        for sid, v in client.execute(TURNOVER_SQL, [_ids(ids), country, as_of]).rows:
            if v:
                거래대금[int(sid)] = float(v)
        for sid, day, kind in client.execute(
            GOV_SQL, [_ids(ids), (기준 - timedelta(days=co.GOV_DAYS)).isoformat(), as_of]
        ).rows:
            사건.setdefault(int(sid), []).append((str(day), str(kind)))
        if 보충끝:
            from batch.services import cb_overhang as cbo
            from batch.services import disclosure_reaction as dr

            # 주간 표가 아직 담지 않은 사건 — 주간 계산과 같은 분류(`dr.classify`·`GOVERNANCE_TYPES`). 덮개 끝 **날**
            # 부터 읽는다: 주간 표는 금요일 18:17 에 지어져 그날 저녁 공시가 다음 주까지 빠졌다(25.1577, 반박 [권고] B).
            # 겹치는 날의 같은 사건은 `본` 이 거른다
            본 = {(s, d, k) for s, xs in 사건.items() for d, k in xs}
            전환대기: dict[str, int] = {}
            for sid, day, title, no in client.execute(GOV_RECENT_SQL, [_ids(ids), 보충끝, as_of[:10]] * 2).rows:
                if sid is None:
                    continue
                kind = dr.classify(str(title))
                if kind in dr.GOVERNANCE_TYPES and (int(sid), str(day)[:10], kind) not in 본:
                    본.add((int(sid), str(day)[:10], kind))
                    사건.setdefault(int(sid), []).append((str(day)[:10], str(kind)))
                    보충종목.add(int(sid))
                if cbo.is_cb_title(str(title)):
                    전환대기[str(no)] = int(sid)
            if 전환대기:
                # 상세를 아직 못 받은 전환사채 결정 — 그 종목은 전환사채 반대를 낼 수 없어 공시부를 "자료 없음" 으로
                # (25.1577, 반박 [권고] A — 상세는 금요일에만 받아 주중 결정이 다음 월요일에야 반대가 됐다)
                받음 = {str(r[0]) for r in client.execute(CB_RECEIVED_SQL, [json.dumps(sorted(전환대기))]).rows}
                전환모름 |= {s for no, s in 전환대기.items() if no not in 받음}
            for xs in 사건.values():
                xs.sort(reverse=True)  # 최근 것부터(주간 표 읽기와 같은 순서)
        for sid, pct in client.execute(
            CB_SQL, [_ids(ids), (기준 - timedelta(days=co.GOV_DAYS)).isoformat(), as_of]
        ).rows:
            if pct is not None:
                전환사채[int(sid)] = float(pct)

    out: list[co.Dossier] = []
    for r in rows:
        m = 지표.get(r.stock_id) or {}
        idx = trend.index_for_market(r.market)
        g = 국면.get(idx or "")
        흐름 = 수급.get(r.stock_id) or []
        out.append(co.Dossier(
            stock_id=r.stock_id, ticker=r.ticker, as_of=as_of, total_score=r.total_score,
            factors={k: r.factor_scores.get(k) for k in ("value", "quality", "growth", "momentum")},
            broker_upsides=증권사.get(r.stock_id) or [],
            flow_sum=sum(v for _d, v in 흐름) if 흐름 else None, turnover_20d=거래대금.get(r.stock_id),
            flow_days=len(흐름), flow_date=흐름[0][0] if 흐름 else None,
            gov_events=사건.get(r.stock_id) or [], cb_pct=전환사채.get(r.stock_id), domestic=국내,
            sentiment=감성.get(r.stock_id), sentiment_error=감성오류, sentiment_date=감성날.get(r.stock_id),
            # 이미 아는 전환사채가 문턱 이상이면 반대는 그대로 선다(하한) — 모르는 것은 문턱 아래일 때만
            gov_covered=(덮음 and not (r.stock_id in 전환모름 and (전환사채.get(r.stock_id) or 0) < co.CB_PCT))
            if 국내 else True,
            gov_source="governance_events·disclosures" if r.stock_id in 보충종목 else "governance_events",
            mdd=m.get("mdd"), volatility=m.get("volatility_ann"), perf_window=m.get("window"),
            perf_as_of=m.get("as_of_date"),
            regime=g.state if g else None, regime_index=idx, regime_date=g.date if g else None,
        ))  # fmt: skip
    return out


# ── 거장 부서 자료 (docs/company.md 12.1, 25.2261) ───────────────────────────────────────────────

#: 사업보고서(`dart.ANNUAL_REPORT_CODE` — 날글자를 SQL 에 박지 않는다, 25.143) 스냅샷 — 기준일까지 접수된 것만.
#: 통화는 같은 사업연도 원장에서 잇는다
#: (스냅샷 payload 에 통화가 없다, 25.919)
MASTER_SNAP_SQL = (
    "SELECT f.stock_id, f.as_of_date, f.receipt_no, f.fiscal_year, f.consolidated, f.payload, fin.currency AS cur"
    " FROM financial_snapshots f LEFT JOIN financials fin ON fin.stock_id = f.stock_id"
    "  AND fin.fiscal_year = f.fiscal_year AND fin.report_code = f.report_code AND fin.consolidated = f.consolidated"
    " WHERE f.report_code = ? AND f.as_of_date <= ? AND f.stock_id IN (SELECT value FROM json_each(?))"
)
#: 그날 점수가 쓴 팩터 원값 — (종목, 팩터)마다 그날 가장 높은 계산 판
MASTER_FACTOR_SQL = (
    "SELECT stock_id, factor, raw_json, calc_version FROM factors WHERE as_of_date = ?"
    " AND factor IN ('value', 'quality', 'momentum') AND stock_id IN (SELECT value FROM json_each(?))"
)
#: 배당 — 접수일이 기준일 이하이고 NULL 이 아닌 것
MASTER_DIV_SQL = (
    "SELECT stock_id, fiscal_year, as_of_date, receipt_no, cash_dividend_total FROM stock_dividends"
    " WHERE as_of_date IS NOT NULL AND as_of_date <= ? AND cash_dividend_total IS NOT NULL"
    " AND stock_id IN (SELECT value FROM json_each(?))"
)
#: 베타 — 기준일 이하 가장 늦은 행, 3Y 먼저
MASTER_BETA_SQL = (
    "SELECT stock_id, window, as_of_date, beta FROM performance_metrics WHERE as_of_date <= ? AND beta IS NOT NULL"
    " AND window IN ('3Y', '1Y') AND stock_id IN (SELECT value FROM json_each(?))"
)
MASTER_STOCK_SQL = "SELECT id, currency, sector_code FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
#: 국내 영업현금흐름 채우기를 마친 날(`jobs/kr_cashflow.DONE_KEY`) — 없으면 국내 영업현금흐름을 쓰지 않는다
#: (반쯤 찬 표본)
KR_CFO_DONE_KEY = "kr_cfo_fill_done"
#: 유틸리티(전기·가스·수도) 중분류 — 그린블랫부가 금융과 함께 뺀다 (12.1)
UTILITY_KSIC = frozenset({"35"})
UTILITY_SIC = frozenset({"49"})


def _pit_annual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 종목 스냅샷 → 기준(연결·별도) 하나로 고른 연간 줄.

    `backtest.pit_basis_rows` 와 같은 규칙: 가장 늦은 사업연도에 연결이 있으면 연결.
    같은 사업연도는 가장 늦은 접수 하나 — 접수일, 같으면 접수번호(`backtest._공시_순`, 25.1291: 같은 날 원공시와
    정정이 함께 접수되면 저장 순서가 이겼다, 25.2263). 사업연도 오름차순"""
    from batch.jobs.backtest import _공시_순

    if not rows:
        return []
    끝해 = max(int(r["fiscal_year"]) for r in rows)
    연결 = 1 if any(int(r["fiscal_year"]) == 끝해 and int(r["consolidated"] or 0) == 1 for r in rows) else 0
    고름: dict[int, dict[str, Any]] = {}
    for r in sorted(rows, key=_공시_순):
        if int(r["consolidated"] or 0) == 연결:
            고름[int(r["fiscal_year"])] = r
    return [고름[k] for k in sorted(고름)]


def master_facts(
    client: Any, ids: list[int], country: str, as_of: str, *, light: bool = False,
) -> dict[int, dict[str, Any]]:
    """거장 부서 자료 — {종목: Dossier.masters} (docs/company.md 12.1). 기준일 `as_of` 까지 알 수 있던 값만.

    `light` — 배당·베타를 읽지 않는다(`div` 빈 dict · `beta` None). 그린블랫·오닐 부서만 쓰는 단독 계좌 Z3·Z4 가
    유니버스 전체를 읽을 때(`jobs/paper_solo`, 25.2319) — 베타는 기준일까지의 모든 행을 읽어 유니버스 단위로는 무겁다.
    버핏(베타)·그레이엄(배당) 부서에 이 자료를 넣으면 '판정 불가' 가 된다 — 위원회 길은 늘 전부 읽는다"""
    from batch.core import db
    from batch.jobs.backtest import _공시_순
    from batch.services import sectors
    from batch.services.scoring import MARKET_CAP_KEY
    from batch.sources import dart

    if not ids:
        return {}
    묶음 = _ids(ids)
    국내ocf = country != "KR" or bool(db.get_setting(client, KR_CFO_DONE_KEY))
    스냅: dict[int, list[dict[str, Any]]] = {}
    for r in client.execute(MASTER_SNAP_SQL, [dart.ANNUAL_REPORT_CODE, as_of, 묶음]).dicts():
        스냅.setdefault(int(r["stock_id"]), []).append(r)
    원값: dict[int, dict[str, Any]] = {}
    판: dict[tuple[int, str], int] = {}
    for r in client.execute(MASTER_FACTOR_SQL, [as_of, 묶음]).dicts():
        k = (int(r["stock_id"]), str(r["factor"]))
        if int(r["calc_version"] or 0) < 판.get(k, -1):
            continue
        판[k] = int(r["calc_version"] or 0)
        try:
            v = json.loads(r["raw_json"] or "{}")
        except (TypeError, ValueError):
            v = {}
        원값.setdefault(k[0], {})[k[1]] = v if isinstance(v, dict) else {}
    배당: dict[int, dict[int, tuple[tuple[str, str], bool]]] = {}
    for r in [] if light else client.execute(MASTER_DIV_SQL, [as_of, 묶음]).dicts():
        표 = 배당.setdefault(int(r["stock_id"]), {})
        fy, 순 = int(r["fiscal_year"]), _공시_순(r)  # 같은 사업연도는 가장 늦은 접수(같은 날이면 접수번호)
        if fy not in 표 or 순 >= 표[fy][0]:
            표[fy] = (순, float(r["cash_dividend_total"]) > 0)
    베타: dict[int, tuple[int, str, float]] = {}
    for r in [] if light else client.execute(MASTER_BETA_SQL, [as_of, 묶음]).dicts():
        sid, 순 = int(r["stock_id"]), (1 if r["window"] == "3Y" else 0)
        후보 = (순, str(r["as_of_date"]), float(r["beta"]))
        if sid not in 베타 or 후보[:2] > 베타[sid][:2]:
            베타[sid] = 후보
    종목 = {int(r["id"]): r for r in client.execute(MASTER_STOCK_SQL, [묶음]).dicts()}
    out: dict[int, dict[str, Any]] = {}
    for sid in sorted(set(ids)):
        연간 = []
        for r in _pit_annual(스냅.get(sid, [])):
            try:
                p = json.loads(r["payload"] or "{}")
            except (TypeError, ValueError):
                p = {}
            연간.append({"fy": int(r["fiscal_year"]), "ni": p.get("net_income"), "eq": p.get("total_equity"),
                       "ta": p.get("total_assets"), "ca": p.get("current_assets"), "cl": p.get("current_liabilities"),
                       "ncl": p.get("noncurrent_liabilities"), "oi": p.get("operating_income"),
                       "ocf": p.get("operating_cash_flow") if 국내ocf else None,
                       "cur": p.get("currency") or r.get("cur"), "as_of": str(r["as_of_date"])})  # fmt: skip
        밸류, 퀄, 모멘 = (원값.get(sid, {}).get(k, {}) for k in ("value", "quality", "momentum"))
        시총 = 밸류.get(MARKET_CAP_KEY) or {}
        cap = 시총.get("cap") if isinstance(시총, dict) else None
        if isinstance(cap, int | float) and 시총.get("scaled") and isinstance(시총.get("factor"), int | float):
            cap = float(cap) * float(시총["factor"])
        st = 종목.get(sid) or {}
        코드 = str(st.get("sector_code") or "")
        scheme, _, raw = 코드.partition(" ")
        중 = sectors.middle_code(raw)
        out[sid] = {
            "annual": 연간,
            "raw": {"ep": 밸류.get("ep"), "bp": 밸류.get("bp"), "dy": 밸류.get("dividend_yield"), "cap": cap,
                    "roe": 퀄.get("roe"), "debt_ratio": 퀄.get("debt_ratio"),
                    "profit_stability": 퀄.get("profit_stability"), "current_ratio": 퀄.get("current_ratio"),
                    "high52": 모멘.get("high_52w_proximity"), "mom12_1": 모멘.get("momentum_12_1")},
            "div": {fy: v for fy, (_날, v) in 배당.get(sid, {}).items()},
            "beta": 베타[sid][2] if sid in 베타 else None, "beta_as_of": 베타[sid][1] if sid in 베타 else None,
            "financial": sectors.is_financial(코드),
            "utility": None if 중 is None else (중 in UTILITY_SIC if scheme == "SIC" else 중 in UTILITY_KSIC),
            "price_currency": st.get("currency"),
        }  # fmt: skip
    return out


def with_masters(dossiers: list[co.Dossier], facts: dict[int, dict[str, Any]]) -> list[co.Dossier]:
    """조사 자료에 거장 자료를 붙인 새 목록 (자료가 없는 종목은 빈 dict — '자료 없음' 이 된다)"""
    from dataclasses import replace

    return [replace(d, masters=facts.get(d.stock_id) or {}) for d in dossiers]


def dossier_json(d: co.Dossier) -> dict[str, Any]:
    """`paper_dossiers` 한 행 — 회사가 달라도 같은 원자료 (docs/company.md 5장).

    증권사 의견은 요약만(건수·중앙값·최근)
    """
    u = [x for _d, _b, x in d.broker_upsides]
    return {
        "total_score": d.total_score, "factors": d.factors,
        "broker": {"n": len(u), "median_upside": median(u) if u else None,
                   "latest": max((x[0] for x in d.broker_upsides), default=None)},
        "flows": {"sum_krw": d.flow_sum, "turnover_20d": d.turnover_20d, "days": d.flow_days, "date": d.flow_date},
        "governance": {"events": d.gov_events[:5], "cb_pct": d.cb_pct, "covered": d.gov_covered,
                       "source": d.gov_source}, "domestic": d.domestic,
        "sentiment": d.sentiment, "sentiment_date": d.sentiment_date,
        "risk": {"mdd": d.mdd, "volatility": d.volatility, "window": d.perf_window, "as_of": d.perf_as_of},
        "regime": {"state": d.regime, "index": d.regime_index, "date": d.regime_date},
        # 거장 자료는 거장 헌장이 있는 날만 — 비면 싣지 않아 A~E 의 조사 자료 행이 그대로다 (docs/company.md 12.8)
        **({"masters": _masters_json(d.masters)} if d.masters else {}),
    }  # fmt: skip


def _masters_json(m: dict[str, Any]) -> dict[str, Any]:
    """조사 자료 행에 싣는 거장 자료 — 근거표가 가리킬 원값(연간은 최근 5 사업연도만)"""
    return {**m, "annual": list(m.get("annual") or [])[-5:]}


#: 소급 성적표의 뒤 성과 — 기준일 뒤 시세(종목·지수). 창은 기준일 뒤 `FORWARD_LOOKAHEAD_DAYS` 달력일
FORWARD_PRICES_SQL = (
    "SELECT stock_id, date, close FROM prices WHERE stock_id IN (SELECT value FROM json_each(?))"
    " AND date > ? AND date <= ? AND close > 0 ORDER BY stock_id, date"
)
FORWARD_INDEX_SQL = (
    "SELECT date, close FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT 400"
)
#: 20 거래일 뒤를 잡으려고 읽는 달력일 — 연휴 여유
FORWARD_LOOKAHEAD_DAYS = 45
#: 성적표의 지수 — 결정 원장 성과(`jobs/paper.BENCH_INDEX`)와 같다
BENCH = {"KR": "KOSPI", "US": "SP500"}


def forward_excess(
    client: Any, market: str, rows: list[report_picks.SignalRow], horizon: int = 20,
) -> dict[int, float]:
    """{종목: 신호 종가 날짜 뒤 `horizon` 거래일의 지수 대비 초과(소수)} — 소급 성적표용 (docs/company.md 6장, 25.1470).

    기준가는 신호 행의 종가(그 날짜), 뒤 값은 그 날짜 뒤 `horizon` 번째 시세 행. 지수는 각 날짜 이하의 마지막 값.
    뒤 시세가 모자라면(최근 신호) 빼고 돌려준다
    """
    기준 = {r.stock_id: (float(r.close), str(r.close_date)[:10]) for r in rows if r.close and r.close_date}
    if not 기준:
        return {}
    시작 = min(d for _c, d in 기준.values())
    끝 = (date.fromisoformat(max(d for _c, d in 기준.values())) + timedelta(days=FORWARD_LOOKAHEAD_DAYS)).isoformat()
    뒤: dict[int, list[tuple[str, float]]] = {}
    for sid, day, close in client.execute(FORWARD_PRICES_SQL, [_ids(list(기준)), 시작, 끝]).rows:
        뒤.setdefault(int(sid), []).append((str(day), float(close)))
    지수행 = client.execute(FORWARD_INDEX_SQL, [BENCH.get(market, "KOSPI"), 끝]).rows
    지수 = sorted((str(d), float(c)) for d, c in 지수행)

    def 지수값(day: str) -> float | None:
        앞 = [c for d, c in 지수 if d <= day]
        return 앞[-1] if 앞 else None

    out: dict[int, float] = {}
    for sid, (종가, 날) in 기준.items():
        행 = [x for x in 뒤.get(sid, []) if x[0] > 날]
        if len(행) < horizon:
            continue
        끝날, 끝값 = 행[horizon - 1]
        i0, i1 = 지수값(날), 지수값(끝날)
        if not i0 or not i1:
            continue
        out[sid] = (끝값 / 종가 - 1) - (i1 / i0 - 1)
    return out
