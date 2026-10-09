"""종목 분석 의견 — 그 시장의 점수가 있는 종목마다 결론·근거·반대 목소리를 `stock_verdicts` 에 둔다
(docs/analysis.md, docs/infra.md 25.1016).

일일 배치의 매도 플래그 뒤에 돈다(`daily.refresh_portfolio`). 읽기는 그 시장 한 번씩 — 점수·신호·판정표·보유·플래그,
국내면 수급·증권사 의견·최근 공시와 공시 반응 통계. 계산은 `services/verdict.build`.

실행
  python -m batch.jobs.verdicts --market KR
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs import daily
from batch.services import disclosure_reaction as dr
from batch.services import divergence, insights
from batch.services import forecast_track as ft
from batch.services import verdict as vd

JOB_NAME = "verdicts"

SCORES_SQL = (
    "SELECT sc.stock_id, sc.as_of_date, sc.total_score, sc.rank_in_market, sc.factor_scores, sc.skip_reason,"
    " sc.weights_json, sc.sentiment_score, sc.sentiment_weight_used,"
    " sc.calc_version, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency, s.market, s.sector,"
    " s.listed_shares"
    " FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = (SELECT MAX(sc2.as_of_date) FROM scores sc2"
    "   JOIN stocks s2 ON s2.id = sc2.stock_id WHERE s2.country = ?)"
    " ORDER BY sc.stock_id, sc.calc_version DESC"
)
SIGNALS_SQL = (
    "SELECT g.stock_id, g.as_of_date AS as_of, g.horizon, g.buy_zone_low, g.buy_zone_high, g.target_price, g.stop_price"
    " FROM signals g JOIN stocks s ON s.id = g.stock_id"
    " WHERE s.country = ? AND g.as_of_date = (SELECT MAX(g2.as_of_date) FROM signals g2"
    "   JOIN stocks s2 ON s2.id = g2.stock_id WHERE s2.country = ?)"
)
CHECKS_SQL = (
    "SELECT c.stock_id, c.as_of_date, c.horizon, c.passed, c.failed_count, c.checks_json, c.levels_json"
    " FROM signal_checks c JOIN stocks s ON s.id = c.stock_id"
    " WHERE s.country = ? AND c.as_of_date = (SELECT MAX(c2.as_of_date) FROM signal_checks c2"
    "   JOIN stocks s2 ON s2.id = c2.stock_id WHERE s2.country = ?)"
)
# 가격·가치 진단 재료 (docs/analysis.md 9장, 25.1023). 종가는 밴드와 같은 잣대(분할만 반영, `db.SPLIT_ONLY_PRICE_SQL`)
CLOSE_SQL = (
    "SELECT p.stock_id, p.date,"
    " CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS close"
    " FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND p.date = ?"
)
MOMENTUM_SQL = (
    "SELECT f.stock_id, f.raw_json, f.as_of_date FROM factors f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.factor = 'momentum' AND f.as_of_date = ? ORDER BY f.stock_id, f.calc_version DESC"
)
BAND_SQL = (
    "SELECT v.stock_id, v.p20, v.p50, v.p80, v.current_value, v.band_rank, v.price_date, v.skip_reason,"
    " CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS band_close"
    " FROM valuation_bands v JOIN stocks s ON s.id = v.stock_id"
    " LEFT JOIN prices p ON p.stock_id = v.stock_id AND p.date = v.price_date"
    " WHERE s.country = ? AND v.metric = 'PBR' AND v.as_of_date = (SELECT MAX(v2.as_of_date) FROM valuation_bands v2"
    "   JOIN stocks s2 ON s2.id = v2.stock_id WHERE s2.country = ?) ORDER BY v.stock_id, v.calc_version DESC"
)
OPINIONS_SQL = "SELECT stock_id, date, broker, target_price FROM kr_opinions WHERE date >= ?"
# 잘 맞힌 증권사 가중 (27장, 25.1052) — 증권사 성적표 (증권사 수십 행)
BROKER_STATS_SQL = "SELECT broker, n_touch, touch_pct FROM broker_stats"
# 비슷한 국면 (docs/analysis.md 13장, 25.1039) — 주간 지표 작업이 종목마다 한 행
PATTERNS_SQL = (
    "SELECT p.stock_id, p.stats_json FROM price_patterns p JOIN stocks s ON s.id = p.stock_id WHERE s.country = ?"
)
# 시나리오·역DCF 재료 (docs/analysis.md 14·15장) — 점수 작업이 그날 저장한 밸류·퀄리티·성장 원값
FUNDAMENTALS_SQL = (
    "SELECT f.stock_id, f.factor, f.raw_json, f.as_of_date FROM factors f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.factor IN ('value', 'quality', 'growth') AND f.as_of_date = ?"
    " ORDER BY f.stock_id, f.calc_version DESC"
)
# 예상 주가의 시장 기대수익률 — 지수의 첫·마지막 종가 (docs/analysis.md 10.1, 25.1024). 기본 키 범위라 한 행씩 읽는다
INDEX_FIRST_SQL = (
    "SELECT date, close FROM index_prices WHERE index_code = ? AND date >= ? AND date <= ? ORDER BY date LIMIT 1"
)
INDEX_LAST_SQL = "SELECT date, close FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT 1"


def market_returns(client: TursoClient, country: str, as_of: str, warnings: list[str]) -> dict[str, dict]:
    """그 나라 지수마다 지난 최대 `MARKET_YEARS` 년 연환산 수익률. {지수: market_return(...) + index}"""
    from batch.services import trend

    since = (date.fromisoformat(as_of[:10]) - timedelta(days=round(vd.MARKET_YEARS * 365.25))).isoformat()
    out: dict[str, dict] = {}
    for code in trend.COUNTRY_INDEXES.get(country, ()):
        첫 = _safe(client, INDEX_FIRST_SQL, [code, since, as_of], warnings, f"{code} 지수")
        끝 = _safe(client, INDEX_LAST_SQL, [code, as_of], warnings, f"{code} 지수")
        m = vd.market_return((str(첫[0]["date"]), float(첫[0]["close"])) if 첫 else None,
                             (str(끝[0]["date"]), float(끝[0]["close"])) if 끝 else None)  # fmt: skip
        if m:
            out[code] = {**m, "index": code}
    return out


def risk_free(client: TursoClient, country: str) -> float | None:
    """설정의 무위험수익률 (연, 소수). 없으면 None — 예상 주가는 0 으로 두고 그렇게 적는다."""
    from batch.jobs import metrics

    return metrics.risk_free_for(client, country)

POSITIONS_SQL = (
    "SELECT p.stock_id, p.quantity, p.unrealized_pnl_krw, p.cost_krw, p.price_date FROM positions p"
    " JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND p.quantity > 0"
)
FLAGS_SQL = (
    "SELECT f.stock_id, f.level, f.rationale_text, f.as_of_date FROM sell_flags f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.is_active = 1 AND f.as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)"
    " ORDER BY CASE f.level WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END"
)
DISCLOSURES_SQL = (
    "SELECT d.stock_id, d.title, d.disclosed_at, d.receipt_no FROM disclosures d JOIN stocks s ON s.id = d.stock_id"
    " WHERE s.country = 'KR' AND d.disclosed_at >= ? ORDER BY d.disclosed_at DESC"
)
REACTION_SQL = "SELECT type, label, n, mean_pct, median_pct, pos_pct FROM disclosure_reaction"
INSERT = (
    "INSERT INTO stock_verdicts (stock_id, market, verdict, headline, detail_json, evidence_json, score_as_of,"
    " signal_as_of, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    # 유니버스에 새로 든 종목의 참고 분석 행을 덮는다 (25.1022)
    " ON CONFLICT (stock_id) DO UPDATE SET market = excluded.market, verdict = excluded.verdict,"
    " headline = excluded.headline, detail_json = excluded.detail_json, evidence_json = excluded.evidence_json,"
    " score_as_of = excluded.score_as_of, signal_as_of = excluded.signal_as_of, computed_at = excluded.computed_at"
)
# 수급 흐름 (docs/analysis.md 16장, 25.1041) — 국내 석 달치.
# 반대 목소리의 5거래일 엇갈림도 이것으로 낸다(한 번만 읽는다)
FLOWS_SQL = (
    "SELECT f.stock_id, f.date, f.frgn_net_amt, f.orgn_net_amt, f.prsn_net_amt, f.short_vol_pct, f.credit_rmnd_pct"
    " FROM kr_flows f JOIN json_each(?) j ON j.value = f.stock_id WHERE f.date <= ? AND f.date > ?"
    " ORDER BY f.stock_id, f.date DESC"
)
#: 석 달 ≈ 60거래일 + 연휴 여유
FLOWS_CALENDAR_DAYS = 100
# 분기 실적 추세 (25장, 25.1049) — 국내만(미국은 연간만 수집). 지난 3년치 분기 보고서
QUARTERS_SQL = (
    "SELECT f.stock_id, f.fiscal_year, f.report_code, f.consolidated, f.report_date, f.revenue, f.operating_income"
    " FROM financials f JOIN json_each(?) j ON j.value = f.stock_id"
    " WHERE f.report_code IN ('11013', '11012', '11014') AND f.fiscal_year >= ?"
)
# 다음 실적 발표 (24장, 25.1048) — 종목마다 오늘 이후 가장 이른 실적발표 일정 하나 (SQLite 의 MIN 과 같은 행의 열)
EARNINGS_SQL = (
    "SELECT e.stock_id, MIN(e.scheduled_date) AS d, e.is_confirmed, e.source FROM earnings_calendar e"
    " JOIN stocks s ON s.id = e.stock_id WHERE s.country = ? AND e.event_type = '실적발표' AND e.scheduled_date >= ?"
    " GROUP BY e.stock_id"
)
# 같은 점수대의 지난 신호 성적 (22장, 25.1046) — 신호 성적표 작업이 실행 기록에 남긴 점수 보정표 (한 행)
CALIBRATION_SQL = (
    "SELECT step_log FROM batch_runs WHERE job_name = 'signal_outcomes' AND market = ? AND status = 'success'"
    " ORDER BY started_at DESC LIMIT 1"
)
# 점수 변화 (17장) — 4주 앞 가장 가까운 점수일
SCORES_BEFORE_SQL = (
    "SELECT sc.stock_id, sc.as_of_date, sc.total_score, sc.factor_scores, sc.weights_json, sc.sentiment_score,"
    " sc.sentiment_weight_used FROM scores sc"
    " JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = (SELECT MAX(sc2.as_of_date) FROM scores sc2"
    "   JOIN stocks s2 ON s2.id = sc2.stock_id WHERE s2.country = ? AND sc2.as_of_date <= ?)"
    " ORDER BY sc.stock_id, sc.calc_version DESC"
)

# 예측 성적표 (docs/analysis.md 11장, 25.1037) — 오늘 낸 예측을 쌓고, 기간이 찬 예측을 실제와 견준다
LOG_INSERT = (
    "INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
    " VALUES (?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (as_of_date, stock_id) DO UPDATE SET close = excluded.close, models_json = excluded.models_json,"
    " computed_at = excluded.computed_at"
)
#: 기간이 찬 예측의 날 — 그 기간 앞(달력)보다 늦지 않은 가장 최근 기록일
LOG_DUE_SQL = "SELECT MAX(as_of_date) FROM forecast_log WHERE country = ? AND as_of_date <= ?"
LOG_ROWS_SQL = "SELECT stock_id, models_json FROM forecast_log WHERE as_of_date = ? AND country = ?"
LOG_FIRST_SQL = "SELECT MIN(as_of_date) FROM forecast_log WHERE country = ?"
#: 종목 누계는 따로 둔다 — 의견 행을 지우고 다시 쓰는 길에서 사라지지 않게 (25.1042, 교차검증 감사)
TRACK_ROWS_SQL = "SELECT stock_id, track_json FROM forecast_track WHERE country = ?"
TRACK_UPSERT = (
    "INSERT INTO forecast_track (stock_id, country, track_json, updated_at) VALUES (?, ?, ?, ?)"
    " ON CONFLICT (stock_id) DO UPDATE SET country = excluded.country, track_json = excluded.track_json,"
    " updated_at = excluded.updated_at"
)
#: 한 번에 평가하는 기록일의 상한(기간마다). 날마다 돌면 1~3일이다 — 오래 멈췄다 돌아온 날 한꺼번에 읽지 않게
MAX_EVAL_DATES = 10
#: 쌓은 기록일 목록의 길이 — 가장 긴 기간(12개월 ≈ 250거래일)보다 넉넉히
LOGGED_KEEP = 400


def track_key(country: str) -> str:
    """시장 누계와 "어디까지 평가했나" 를 두는 설정 열쇠 (배치만 쓰는 기록 키)."""
    return f"forecast_track_{country}"


def evaluate_due(client: TursoClient, country: str, as_of: str, now_close: dict[int, dict],
                 prior: dict[int, dict], state: dict, warnings: list[str]) -> list[str]:  # fmt: skip
    """기간이 찬 예측을 견줘 `prior`(종목 누계)와 `state["market"]`(시장 누계)에 더한다. 평가한 기록일 목록. 바뀐 종목은
    `state["_changed"]` 에 둔다(저장하지 않는 열쇠 — 부르는 쪽이 빼고 쓴다).

    기간마다 **어디까지 평가했나(`through`) 뒤 ~ 그 기간 앞(달력)** 의 기록일을 **모두** 오래된 것부터(한 번에 최대
    `MAX_EVAL_DATES`) 본다 — 25.1037 은 가장 최근 하나만 봐 달력 차이(주말)로 기록일의 약 30% 가 영영 평가되지 않았다
    (교차검증 감사, 반년 모의 106 일 중 31 일). 같은 날 다시 돌아도 `through` 가 막아 두 번 세지 않는다.
    기록일은 `state["logged"]` 로 안다(따로 읽지 않는다). 실제 끝 가격은 오늘 종가 —
    기록일이 밀려 평가된 날은 기간이 며칠 길다.
    기준 종가는 **지금 계열에서 그날 것을 다시 읽는다**(수정주가가 다시 매겨져도 두 끝이 같은 잣대)."""
    through = state.setdefault("through", {})
    market = state.setdefault("market", {})
    바뀜: set[int] = state.setdefault("_changed", set())
    본날: list[str] = []
    기록일 = sorted(set(state.get("logged") or []))
    for 달 in vd.FORECAST_MONTHS:
        cutoff = ft.months_back(date.fromisoformat(as_of[:10]), 달).isoformat()
        앞 = str(through.get(str(달)) or "")
        if 기록일:
            날들 = [d for d in 기록일 if 앞 < d <= cutoff][:MAX_EVAL_DATES]
        else:  # 기록일 목록 전(25.1042 전)의 상태 — 가장 최근 하나만
            rows = _safe(client, LOG_DUE_SQL, [country, cutoff], warnings, "예측 기록")
            d0 = rows[0][next(iter(rows[0]))] if rows else None
            날들 = [str(d0)] if d0 and str(d0) > 앞 else []
        for d in 날들:
            evaluate_date(client, country, d, 달, now_close, prior, market, 바뀜, warnings)
            through[str(달)] = d
            본날.append(f"{달}개월←{d}")
    return 본날


def evaluate_date(client: TursoClient, country: str, d: str, 달: int, now_close: dict[int, dict],
                  prior: dict[int, dict], market: dict, 바뀜: set[int], warnings: list[str]) -> None:  # fmt: skip
    """기록일 하나의 `달` 개월 예측을 오늘 종가와 견준다."""
    기준 = {int(r["stock_id"]): r for r in _safe(client, CLOSE_SQL, [country, str(d)], warnings, "기준 종가")}
    for r in _safe(client, LOG_ROWS_SQL, [str(d), country], warnings, "예측 기록"):
        sid = int(r["stock_id"])
        b, n = (기준.get(sid) or {}).get("close"), (now_close.get(sid) or {}).get("close")
        if not b or not n:
            continue
        try:
            models = json.loads(r["models_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for model, cell in ft.evaluate(models, 달, float(b), float(n)).items():
            ft.add(prior.setdefault(sid, {}), model, 달, cell)
            ft.add(market, model, 달, cell)
            바뀜.add(sid)


#: 그 시장의 **유니버스 의견만** 지운다 — 참고 분석(`jobs/analyze_extra`, `detail.excluded_reason` 이 있는 행)은
#: 남긴다 (25.1022).
#: 예전엔 시장 전체를 지워, 일일 배치의 참고 분석 단계가 실패하거나 30종목 상한을 넘으면 "지금 분석" 결과가 사라졌다
CLEAR = "DELETE FROM stock_verdicts WHERE market = ? AND json_extract(detail_json, '$.excluded_reason') IS NULL"


def _safe(client: TursoClient, sql: str, args: list, warnings: list[str], what: str) -> list[dict]:
    """곁 재료는 못 읽어도 의견은 낸다 — 표가 없으면(마이그레이션 전) 조용히, 그 밖은 경고."""
    try:
        return client.execute(sql, args).dicts()
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"{what}을(를) 읽지 못했습니다: {exc}")
        return []


def against_kr(client: TursoClient, ids: list[int], today: date, warnings: list[str],
               flows: dict[int, list[dict]] | None = None) -> dict[int, list[dict]]:  # fmt: skip
    """국내 반대 목소리와 공시 근거 (docs/analysis.md 5장). 종목 → [{text, evidence, against}].

    `flows` 를 주면(16장 수급 흐름이 읽은 석 달치) 그것으로 엇갈림을 내고 따로 읽지 않는다."""
    out: dict[int, list[dict]] = defaultdict(list)
    # 엇갈림 — 리포트 3.9 와 같은 질의·식
    try:
        oid = json.dumps(sorted(ids))
        흐름시작 = (today - timedelta(days=divergence.FLOW_DAYS * 3)).isoformat()
        if flows is not None:
            흐름 = [(sid, r["date"], r["frgn_net_amt"], r["orgn_net_amt"]) for sid, rs in sorted(flows.items())
                  for r in rs if str(r["date"]) > 흐름시작 and str(r["date"]) <= today.isoformat()
                  and r["frgn_net_amt"] is not None and r["orgn_net_amt"] is not None]  # fmt: skip
        else:
            흐름 = client.execute(daily.DIVERGENCE_FLOWS_SQL, [oid, today.isoformat(), 흐름시작]).rows
        의견 = client.execute(daily.DIVERGENCE_OPINIONS_SQL,
                            [oid, today.isoformat(), (today - timedelta(days=400)).isoformat()]).rows  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"수급·의견을 읽지 못했습니다: {exc}")
        흐름, 의견 = [], []
    합: dict[int, list[int]] = {}
    최근날: dict[int, str] = {}
    for sid, d, frgn, orgn in 흐름:  # 종목마다 최근 날부터 (질의가 날짜 내림차순)
        s = 합.setdefault(int(sid), [0, 0, 0])
        최근날.setdefault(int(sid), str(d))
        if s[0] < divergence.FLOW_DAYS:
            s[0] += 1
            s[1] += int(frgn)
            s[2] += int(orgn)
    목표: dict[int, list[tuple[str, str, float | None]]] = defaultdict(list)
    for sid, d, broker, target in 의견:
        목표[int(sid)].append((str(d), str(broker), float(target) if target else None))
    since = (today - timedelta(days=divergence.OPINION_DAYS)).isoformat()
    for sid in ids:
        n, frgn, orgn = 합.get(sid, [0, 0, 0])
        날 = 최근날.get(sid)
        up, down = divergence.target_changes(목표.get(sid, []), since)
        v = divergence.View("", "", n, frgn, orgn, up, down)
        if v.flow_against:
            합계 = f"{(frgn + orgn) / 100:+,.1f}억 ({n}거래일)"
            out[sid].append({"against": True, "text": divergence.against_lines(v)[0], "evidence": vd._row(
                "외국인+기관 순매수 합", 합계, "< 0 이면 반대", "kr_flows (KIS)", 날)})
        if v.opinion_against:
            out[sid].append({"against": True, "text": divergence.against_lines(v)[-1], "evidence": vd._row(
                "증권사 목표가 변경", f"내림 {down}·올림 {up}", "내림 > 올림이면 반대", "kr_opinions (KIS)", since)})
    # 최근 공시 + 그 유형의 과거 반응 — 중앙값이 음수면 반대 목소리, 아니면 근거
    반응 = {str(r["type"]): r for r in _safe(client, REACTION_SQL, [], warnings, "공시 반응 통계")}
    본것: set[tuple[int, str]] = set()
    아는종목 = set(ids)
    시작 = (today - timedelta(days=vd.RECENT_DISCLOSURE_DAYS)).isoformat()
    for r in _safe(client, DISCLOSURES_SQL, [시작], warnings, "최근 공시"):
        sid = int(r["stock_id"])
        kind = dr.classify(str(r["title"]))
        row = 반응.get(kind or "")
        줄 = dr.line(row) if row else None
        if sid not in 아는종목 or not 줄 or (sid, kind) in 본것:
            continue
        본것.add((sid, str(kind)))
        글 = f"최근 공시 \"{r['title']}\" ({r['disclosed_at']}) — {줄}"
        근거 = vd._row("공시", str(r["title"]), "—", f"DART 접수번호 {r['receipt_no']}", str(r["disclosed_at"]))
        out[sid].append({"against": float(row["median_pct"]) < 0, "text": 글, "evidence": 근거})
    return out


def outlook_inputs(client: TursoClient, country: str, as_of: str, today: date, warnings: list[str]) -> dict:
    """가격·가치 진단의 재료를 시장 한 번에 읽는다 (docs/analysis.md 9장). 못 읽은 것은 경고로 — 그 줄만 빠진다."""
    from batch.jobs import signals as sig

    종가 = {int(r["stock_id"]): r for r in _safe(client, CLOSE_SQL, [country, as_of], warnings, "종가")}
    모멘텀: dict[int, dict] = {}
    for r in _safe(client, MOMENTUM_SQL, [country, as_of], warnings, "모멘텀"):
        if int(r["stock_id"]) not in 모멘텀:
            with contextlib.suppress(TypeError, ValueError):
                모멘텀[int(r["stock_id"])] = {**json.loads(r["raw_json"] or "{}"), "as_of": r["as_of_date"]}
    밴드: dict[int, dict] = {}
    for r in _safe(client, BAND_SQL, [country, country], warnings, "밸류에이션 밴드"):
        밴드.setdefault(int(r["stock_id"]), r)
    try:
        위험 = sig.load_metrics(client, country, as_of)
    except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
        warnings.append(f"성과 지표를 읽지 못했습니다: {exc}")
        위험 = {}
    의견: dict[int, list[dict]] = defaultdict(list)
    성적: dict[str, dict] = {}
    if country == "KR":
        성적 = {str(r["broker"]): r for r in _safe(client, BROKER_STATS_SQL, [], warnings, "증권사 성적표")}
        since = (today - timedelta(days=vd.CONSENSUS_DAYS)).isoformat()
        for r in _safe(client, OPINIONS_SQL, [since], warnings, "증권사 목표가"):
            의견[int(r["stock_id"])].append(r)
    국면: dict[int, dict] = {}
    for r in _safe(client, PATTERNS_SQL, [country], warnings, "비슷한 국면"):
        with contextlib.suppress(TypeError, ValueError):
            국면[int(r["stock_id"])] = json.loads(r["stats_json"])
    재무원값: dict[int, dict] = {}
    본칸: set[tuple[int, str]] = set()
    for r in _safe(client, FUNDAMENTALS_SQL, [country, as_of], warnings, "밸류·퀄리티·성장 원값"):
        k = (int(r["stock_id"]), str(r["factor"]))
        if k in 본칸:  # 같은 날 여러 판이면 큰 calc_version (정렬)
            continue
        본칸.add(k)
        with contextlib.suppress(TypeError, ValueError):
            재무원값.setdefault(k[0], {"as_of": r["as_of_date"]}).update(json.loads(r["raw_json"] or "{}"))
    try:
        무위험 = risk_free(client, country)
    except Exception as exc:  # noqa: BLE001 — 0 으로 두고 그렇게 적는다
        warnings.append(f"무위험수익률을 읽지 못했습니다: {exc}")
        무위험 = None
    return {"close": 종가, "momentum": 모멘텀, "band": 밴드, "risk": 위험, "opinions": 의견,
            "market": market_returns(client, country, as_of, warnings), "rf": 무위험,
            "patterns": 국면, "fundamentals": 재무원값, "broker_stats": 성적}  # fmt: skip


def outlook_for(재료: dict, sid: int, currency: str, today: date, market: str | None = None) -> dict:
    from batch.services import patterns, trend

    c = 재료["close"].get(sid) or {}
    b = 재료["band"].get(sid)
    m = 재료["momentum"].get(sid) or {}
    return vd.outlook(
        analog=patterns.pick((재료.get("patterns") or {}).get(sid), m.get("momentum_3m"), m.get("high_52w_proximity")),
        fundamentals=(재료.get("fundamentals") or {}).get(sid), broker_stats=재료.get("broker_stats"),
        stress=((재료.get("patterns") or {}).get(sid) or {}).get("stress"),
        close=c.get("close"), close_date=c.get("date"), currency=currency, momentum=재료["momentum"].get(sid),
        risk=재료["risk"].get(sid), band=b, opinions=재료["opinions"].get(sid, []), today=today,
        band_note=(b or {}).get("skip_reason"),
        market=재료.get("market", {}).get(trend.index_for_market(market) or ""), rf=재료.get("rf"),
    )  # fmt: skip


def build_market(client: TursoClient, market: str, today: date, warnings: list[str]) -> list[tuple[str, list[Any]]]:
    country = "KR" if market == "KR" else "US"
    점수: dict[int, dict] = {}
    for r in client.execute(SCORES_SQL, [country, country]).dicts():
        점수.setdefault(int(r["stock_id"]), r)  # 같은 날 여러 판이면 큰 calc_version (정렬)
    if not 점수:
        return []
    순위있음 = sum(1 for r in 점수.values() if r["total_score"] is not None)
    신호: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, SIGNALS_SQL, [country, country], warnings, "신호"):
        신호[int(r["stock_id"])].append(r)
    판정: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, CHECKS_SQL, [country, country], warnings, "판정표"):
        try:
            rows = json.loads(r["checks_json"] or "[]")
        except (TypeError, ValueError):
            rows = []
        try:
            레벨 = json.loads(r.get("levels_json") or "[]")
        except (TypeError, ValueError):
            레벨 = []
        판정[int(r["stock_id"])].append({"horizon": r["horizon"], "as_of": r["as_of_date"], "passed": bool(r["passed"]),
                                       "failed_count": r["failed_count"], "rows": rows, "levels": 레벨})  # fmt: skip
    보유 = {int(r["stock_id"]): r for r in _safe(client, POSITIONS_SQL, [country], warnings, "보유")}
    플래그: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, FLAGS_SQL, [country], warnings, "매도 플래그"):
        플래그[int(r["stock_id"])].append({"level": r["level"], "rationale_text": r["rationale_text"],
                                         "as_of": r["as_of_date"]})  # fmt: skip
    흐름: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        for r in _safe(client, FLOWS_SQL, [json.dumps(sorted(점수)), today.isoformat(),
                                           (today - timedelta(days=FLOWS_CALENDAR_DAYS)).isoformat()],
                       warnings, "수급 흐름"):  # fmt: skip
            흐름[int(r["stock_id"])].append(r)
    곁 = against_kr(client, sorted(점수), today, warnings, flows=흐름) if country == "KR" else {}
    as_of = max(str(r["as_of_date"]) for r in 점수.values())
    재료 = outlook_inputs(client, country, as_of, today, warnings)
    # 예측 성적표 (25.1037) — 지난 누계를 읽고 기간이 찬 예측을 더한다
    누계: dict[int, dict] = {}
    for r in _safe(client, TRACK_ROWS_SQL, [country], warnings, "지난 예측 성적"):
        with contextlib.suppress(TypeError, ValueError):
            누계[int(r["stock_id"])] = json.loads(r["track_json"])
    상태 = db.get_setting(client, track_key(country), {}) or {}
    평가 = evaluate_due(client, country, as_of, 재료["close"], 누계, 상태, warnings)
    if not 상태.get("since"):
        첫 = _safe(client, LOG_FIRST_SQL, [country], warnings, "예측 기록")
        상태["since"] = (첫[0][next(iter(첫[0]))] if 첫 else None) or as_of
    # 점수 변화 (17장) · 닮은 종목 (18장)
    앞점수: dict[int, dict] = {}
    앞날 = (date.fromisoformat(as_of[:10]) - timedelta(days=insights.SCORE_CHANGE_DAYS)).isoformat()
    for r in _safe(client, SCORES_BEFORE_SQL, [country, country, 앞날], warnings, "4주 전 점수"):
        if int(r["stock_id"]) not in 앞점수:
            with contextlib.suppress(TypeError, ValueError):
                앞점수[int(r["stock_id"])] = {"as_of": r["as_of_date"], "total": r["total_score"],
                                           "factors": json.loads(r["factor_scores"] or "{}"),
                                           "weights": json.loads(r["weights_json"] or "{}"),
                                           "sentiment": r["sentiment_score"],
                                           "sentiment_weight": r["sentiment_weight_used"]}  # fmt: skip
    모양: dict[int, dict[str, float]] = {}
    for sid, r in 점수.items():
        with contextlib.suppress(TypeError, ValueError):
            p = insights.profile(json.loads(r["factor_scores"] or "{}"))
            if p:
                모양[sid] = p
    시장성적 = 상태.get("market") or {}
    # 내부자 매매 (23장, 25.1047) — 신호 작업과 같은 집계(최근 90일 접수). 판정에 쓰지 않는다
    try:
        from batch.services import insider

        내부자 = insider.load_summaries(client, country, as_of[:10])
    except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
        if not db.표가_없나(exc):
            warnings.append(f"내부자 매매를 읽지 못했습니다: {exc}")
        내부자 = {}
    분기: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        for r in _safe(client, QUARTERS_SQL, [json.dumps(sorted(점수)), today.year - 2], warnings, "분기 재무"):
            분기[int(r["stock_id"])].append(r)
    실적일 = {int(r["stock_id"]): r for r in _safe(client, EARNINGS_SQL, [country, today.isoformat()], warnings,
                                                   "실적 일정")}  # fmt: skip
    실적반응 = None
    if country == "KR":
        실적반응 = next((r for r in _safe(client, REACTION_SQL, [], warnings, "공시 반응 통계")
                     if r.get("type") == "earnings"), None)  # fmt: skip
    보정표: list[dict] = []
    for r in _safe(client, CALIBRATION_SQL, [country], warnings, "점수 보정표"):
        with contextlib.suppress(TypeError, ValueError, AttributeError):
            보정표 = list(json.loads(r["step_log"] or "{}").get("calibration") or [])
    # 같은 업종 비교 (21장) — (시장, 업종)마다. 점수는 시장별·업종별 z-score 라 코스피·코스닥을 섞지 않는다
    업종: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sid, r in 점수.items():
        if not r.get("sector"):
            continue
        fu = (재료.get("fundamentals") or {}).get(sid) or {}
        bp = fu.get("bp")
        업종[(str(r.get("market")), str(r["sector"]))].append({
            "stock_id": sid, "ticker": r["ticker"], "name": r["name"], "total": r["total_score"],
            "pbr": 1 / bp if isinstance(bp, (int, float)) and bp > 0 else None, "roe": fu.get("roe"),
            "r3": (재료["momentum"].get(sid) or {}).get("momentum_3m"),
        })  # fmt: skip
    # 첫 성적이 나오는 날 — 처음 쌓은 날의 한 달 뒤(가장 짧은 기간)
    첫평가 = ft.months_back(date.fromisoformat(str(상태["since"])[:10]), -vd.FORECAST_MONTHS[0]).isoformat()
    stamp = db.now_iso()
    rows: list[tuple[str, list[Any]]] = [(CLEAR, [country])]
    기록: list[tuple[str, list[Any]]] = []
    레이더재료: list[dict] = []
    for sid, r in 점수.items():
        try:
            factors = json.loads(r["factor_scores"] or "{}")
        except (TypeError, ValueError):
            factors = {}
        p = 보유.get(sid)
        inp = vd.Inputs(
            name=str(r["name"]), ticker=str(r["ticker"]), currency=str(r["currency"] or "KRW"),
            score={"as_of": r["as_of_date"], "total": r["total_score"], "rank": r["rank_in_market"],
                   "ranked": 순위있음, "factors": factors, "skip_reason": r["skip_reason"]},
            signals=신호.get(sid, []), checks=판정.get(sid, []),
            position=None if not p else {
                "quantity": p["quantity"], "price_date": p["price_date"],
                "pnl_pct": (float(p["unrealized_pnl_krw"]) / float(p["cost_krw"]) * 100
                            if p["unrealized_pnl_krw"] is not None and p["cost_krw"] else None)},
            flags=플래그.get(sid, []),
            against=[a for a in 곁.get(sid, []) if a["against"]],
            outlook=outlook_for(재료, sid, str(r["currency"] or "KRW"), today, r.get("market")),
        )  # fmt: skip
        if 흐름.get(sid):
            카드 = insights.flow_card(흐름[sid])
            if 카드 and inp.outlook is not None:
                inp.outlook["flows"] = {**카드, "lines": insights.flow_lines(카드)}
        out = vd.build(inp)
        # 점수 변화·닮은 종목 (17·18장)
        변화 = insights.score_change({"as_of": r["as_of_date"], "total": r["total_score"], "factors": factors},
                                    앞점수.get(sid))  # fmt: skip
        if 변화 and 변화.get("since") and str(변화["since"]) < str(r["as_of_date"]):
            조각 = [f"{vd.FACTOR[변화[k]]} {변화['factors'][변화[k]]:+.0f}" for k in ("up", "down") if 변화.get(k)]
            뒤 = f" (가장 오른 팩터 {조각[0]} · 가장 내린 팩터 {조각[1]})" if len(조각) == 2 else ""
            out["reasons"].append(f"점수 변화: {변화['since']}보다 {변화['delta']:+.1f}{뒤}")
        닮음 = [{"stock_id": t, "ticker": 점수[t]["ticker"], "name": 점수[t]["name"],
                 "total": 점수[t]["total_score"], "dist": d, "signal": bool(신호.get(t))}
                for t, d in insights.twins(sid, 모양)]  # fmt: skip
        out["reasons"] += [a["text"] for a in 곁.get(sid, []) if not a["against"]]
        out["evidence"] += [a["evidence"] for a in 곁.get(sid, []) if not a["against"]]
        종목성적 = 누계.get(sid) or {}
        for 줄 in (ft.line(종목성적, scope="이 종목"), ft.line(시장성적, scope="시장 전체")):
            if 줄:
                out["reasons"].append(f"예측 성적표: {줄}")
        detail = {k: out[k] for k in ("label", "reasons", "against", "nearest", "outlook")}
        detail["track"] = {"stock": 종목성적, "market": 시장성적, "since": 상태.get("since"), "first_due": 첫평가}
        detail["score_change"] = 변화
        detail["twins"] = 닮음
        if sid in 내부자:
            행 = insider.criteria_rows(내부자[sid], r.get("listed_shares"))
            if 행:
                out["reasons"].append(f"내부자 매매 최근 {내부자[sid].window_days}일: {행[0]['display']}")
                out["evidence"].append(행[0])
            detail["insider"] = 내부자[sid].as_dict()
        if 분기.get(sid):
            추세 = insights.quarter_trend(분기[sid])
            줄 = insights.quarter_line(추세)
            if 줄:
                out["reasons"].append(줄)
            detail["quarters"] = 추세
        if sid in 실적일:
            e = 실적일[sid]
            남은 = (date.fromisoformat(str(e["d"])[:10]) - today).days
            확정 = "확정" if e.get("is_confirmed") else "예상"
            반응 = ""
            if 실적반응 and isinstance(실적반응.get("median_pct"), (int, float)):
                반응 = (f" — 국내 실적(잠정) 공시 뒤 5거래일 반응(시장 전체): 중앙값 {실적반응['median_pct']:+.1f}%·"
                        f"오른 비율 {실적반응['pos_pct']:.0f}% ({실적반응['n']}건)")  # fmt: skip
            out["reasons"].append(f"다음 실적 발표 {e['d']} (D-{남은}, {확정}){반응}")
            out["evidence"].append(vd._row("다음 실적 발표", str(e["d"]), 확정,
                                           f"earnings_calendar ({e.get('source')})", str(e["d"])))  # fmt: skip
            detail["earnings"] = {"date": e["d"], "days": 남은, "confirmed": bool(e.get("is_confirmed")),
                                  "source": e.get("source")}  # fmt: skip
        # 점수 분해 (26장) — 오늘 몫과 4주 동안 몫의 변화
        with contextlib.suppress(TypeError, ValueError):
            지금점수 = {"as_of": r["as_of_date"], "total": r["total_score"], "factors": factors,
                     "weights": json.loads(r["weights_json"] or "{}"), "sentiment": r["sentiment_score"],
                     "sentiment_weight": r["sentiment_weight_used"]}  # fmt: skip
            분해 = insights.decompose(지금점수)
            if 분해:
                분해["change"] = insights.decompose_change(지금점수, 앞점수.get(sid))
                분해["since"] = (앞점수.get(sid) or {}).get("as_of") if 분해["change"] else None
            detail["decomposition"] = 분해
        보정 = insights.calibration_for(r["total_score"], 보정표)
        detail["calibration"] = 보정
        if 보정:
            상관 = f" · 점수-수익 순위 상관 ρ {보정['rho']:+.2f}" if isinstance(보정.get("rho"), (int, float)) else ""
            오름 = f"·오른 비율 {보정['win_rate'] * 100:.0f}%" if isinstance(보정.get("win_rate"), (int, float)) else ""
            out["reasons"].append(
                f"같은 점수대의 지난 신호: {보정['lo']}~{보정['hi']}점 {보정['window']}거래일 뒤"
                f" 평균 {보정['avg_ret'] * 100:+.1f}%{오름} ({보정['n']}건){상관} — {보정.get('verdict') or ''}")
        if r.get("sector"):
            동종 = insights.peers(sid, 업종.get((str(r.get("market")), str(r["sector"])), []))
            detail["peers"] = None if not 동종 else {"sector": r["sector"], "market": r.get("market"), **동종}
        레이더재료.append({"stock_id": sid, "ticker": r["ticker"], "name": r["name"], "verdict": out["verdict"],
                       "ladder": (out.get("outlook") or {}).get("ladder"), "score_change": 변화,
                       "agreement": (out.get("outlook") or {}).get("agreement")})  # fmt: skip
        o = out.get("outlook") or {}
        모델 = ft.log_models(o)
        if 모델 and o.get("close_date") == as_of:
            기록.append((LOG_INSERT, [as_of, sid, country, float(o["close"]),
                                     json.dumps(모델, separators=(",", ":")), stamp]))  # fmt: skip
        신호일 = max((g["as_of"] for g in 신호.get(sid, [])), default=None) or max(
            (c["as_of"] for c in 판정.get(sid, [])), default=None)
        rows.append((INSERT, [
            sid, country, out["verdict"], out["headline"], json.dumps(detail, ensure_ascii=False),
            json.dumps(out["evidence"], ensure_ascii=False), r["as_of_date"], 신호일, stamp,
        ]))  # fmt: skip
    if 평가:
        print(f"예측 성적 평가: {', '.join(평가)}")
    # "어디까지 평가했나" 는 지우기와 같은 첫 묶음에 — 뒤 묶음이 깨지면 그날 평가를 잃을 뿐 **두 번 세지 않는다**
    # (덜 센 성적이 더 센 성적보다 덜 틀리게 읽힌다). 종목 누계는 그 바로 뒤 — 의견 행보다 먼저 (25.1042)
    바뀜 = 상태.pop("_changed", set())
    if 기록:
        상태["logged"] = sorted(set(상태.get("logged") or []) | {as_of})[-LOGGED_KEEP:]
    누계행 = [(TRACK_UPSERT, [sid, country, json.dumps(누계[sid], separators=(",", ":")), stamp])
            for sid in sorted(바뀜) if sid in 누계]  # fmt: skip
    rows[1:1] = [db.setting_statement(track_key(country), 상태), *누계행]
    # 레이더 (20장) — 첫 화면이 설정 한 행만 읽게 시장마다 한 번 만들어 둔다
    레이더 = {"as_of": as_of, "computed_at": stamp, **insights.radar(레이더재료)}
    return rows + 기록 + [db.setting_statement(radar_key(country), 레이더)]


def radar_key(country: str) -> str:
    """종목 분석 탭 레이더를 두는 설정 열쇠 (배치만 쓰는 기록 키). 웹 `lib/analysis.ts` `RADAR_KEYS` 와 같다."""
    return f"analysis_radar_{country}"


def run(market: str) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.user_today()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=market, trade_date=today.isoformat())
        warnings: list[str] = []
        rows = build_market(client, market, today, warnings)
        for i in range(0, len(rows), 400):
            client.batch(rows[i : i + 400])
        세기: dict[str, int] = defaultdict(int)
        의견수 = 0
        for sql, args in rows:
            if sql == INSERT:
                의견수 += 1
                세기[str(args[2])] += 1
        기록수 = sum(1 for sql, _ in rows if sql == LOG_INSERT)
        db.finish_batch_run(client, run_id, status="partial" if warnings else "success",
                            step_log={"stocks": 의견수, "by_verdict": dict(세기), "forecast_logged": 기록수,
                                      "warnings": warnings[:10]})  # fmt: skip
        # 결론별 수는 찍지 않는다 — "보유 점검·보유 유지" 수가 보유 종목 수다(공개 로그, 25.979). step_log 에만
        print(f"종목 분석 의견({market}): {의견수}종목 · 예측 기록 {기록수}건")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    return run(parser.parse_args().market)


if __name__ == "__main__":
    sys.exit(guard(main))
