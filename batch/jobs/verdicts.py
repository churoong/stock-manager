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
from batch.core.rounding import fixed
from batch.jobs import daily
from batch.services import adjust as adj
from batch.services import buy_thesis as bt
from batch.services import cb_overhang as cb
from batch.services import disclosure_reaction as dr
from batch.services import divergence, history, insights
from batch.services import forecast_track as ft
from batch.services import verdict as vd
from batch.sources import dart

JOB_NAME = "verdicts"

SCORES_SQL = (
    "SELECT sc.stock_id, sc.as_of_date, sc.total_score, sc.rank_in_market, sc.factor_scores, sc.skip_reason,"
    " sc.weights_json, sc.sentiment_score, sc.sentiment_weight_used,"
    " sc.calc_version, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency, s.market, s.sector,"
    " s.listed_shares, s.sector_code"
    " FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = (SELECT sc2.as_of_date FROM scores sc2"
    "   CROSS JOIN stocks s2 ON s2.id = sc2.stock_id WHERE s2.country = ? ORDER BY sc2.as_of_date DESC LIMIT 1)"
    " ORDER BY sc.stock_id, sc.calc_version DESC"
)
#: **신호는 마지막 계산일(신호·판정표 둘의 최신) 것만** (docs/infra.md 25.1336, 분석 화면 감사 #2). 신호 작업은 그날
#: 행만
#: 지우고 다시 넣어 신호 0 인 날에는 `signals` 의 최신 날이 지난 날로 남는다 — 그 날 신호로 "매수 검토 — 단기 매수 신호
#: (지난 날)" 을 냈고 화면 신호 카드(`web/lib/recommend.LAST_CALC_DATE`)는 "오늘 신호 없음" 이었다. 판정표는 신호 0 인
#: 날에도 전 종목에 남는다 — 웹과 같은 잣대. 인자는 (나라, 나라, 나라)
SIGNALS_SQL = (
    "SELECT g.stock_id, g.as_of_date AS as_of, g.horizon, g.buy_zone_low, g.buy_zone_high, g.target_price, g.stop_price"
    " FROM signals g JOIN stocks s ON s.id = g.stock_id"
    " WHERE s.country = ? AND g.as_of_date = (SELECT MAX(d) FROM ("
    "   SELECT d FROM (SELECT g2.as_of_date AS d FROM signals g2 CROSS JOIN stocks s2 ON s2.id = g2.stock_id"
    "     WHERE s2.country = ? ORDER BY g2.as_of_date DESC LIMIT 1)"
    "   UNION ALL"
    "   SELECT d FROM (SELECT c2.as_of_date AS d FROM signal_checks c2 CROSS JOIN stocks s3 ON s3.id = c2.stock_id"
    "     WHERE s3.country = ? ORDER BY c2.as_of_date DESC LIMIT 1)))"
)
CHECKS_SQL = (
    "SELECT c.stock_id, c.as_of_date, c.horizon, c.passed, c.failed_count, c.checks_json, c.levels_json"
    " FROM signal_checks c JOIN stocks s ON s.id = c.stock_id"
    " WHERE s.country = ? AND c.as_of_date = (SELECT c2.as_of_date FROM signal_checks c2"
    "   CROSS JOIN stocks s2 ON s2.id = c2.stock_id WHERE s2.country = ? ORDER BY c2.as_of_date DESC LIMIT 1)"
)
# 가격·가치 진단 재료 (docs/analysis.md 9장, 25.1023). 종가는 밴드와 같은 잣대(분할만 반영, `db.SPLIT_ONLY_PRICE_SQL`)
CLOSE_SQL = (
    "SELECT p.stock_id, p.date,"
    " CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS close,"
    " p.adj_close, p.close AS raw_close"
    " FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND p.date = ?"
)
#: 국내 기준 종가가 **수정주가 빈 행**이면 앞뒤를 읽어 직전 계수로 메운다
#: (`adjust.fill_adjusted`, docs/infra.md 25.1190). 하루만 읽으면 그 행이 가운데 구멍인지 끝인지 모른다.
#: 앞뒤 이만큼(달력일)이면 거래일 몇 개는 늘 걸린다
HOLE_WINDOW_DAYS = 20
HOLE_SQL = (
    "SELECT p.stock_id, p.date, p.adj_close, p.close FROM json_each(?) j CROSS JOIN prices p ON p.stock_id = j.value"
    " WHERE p.date >= ? AND p.date <= ? AND p.close > 0 ORDER BY p.stock_id, p.date"
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
    " WHERE s.country = ? AND v.metric = 'PBR' AND v.as_of_date = (SELECT v2.as_of_date FROM valuation_bands v2"
    "   CROSS JOIN stocks s2 ON s2.id = v2.stock_id WHERE s2.country = ?"
    " ORDER BY v2.as_of_date DESC LIMIT 1) ORDER BY v.stock_id, v.calc_version DESC"
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
    "SELECT p.stock_id, p.quantity, p.unrealized_pnl_krw, p.cost_krw, p.price_date,"
    " p.unrealized_pnl, p.cost, p.unrealized_fx_pnl_krw"  # 해외 주가·환차 분리 (25.1294)
    " FROM positions p"
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
#: 공시 위험 이력 1년 (docs/analysis.md 54장, 25.1157) — 금요일 공시 반응 계산이 채운 작은 표만 읽는다
GOVERNANCE_SQL = "SELECT stock_id, day, type, title, receipt_no, covered_from, covered_to FROM governance_events"
# 매수 근거가 아직 참인가 (56장, 25.1312) — 보유 종목의 가장 최근 중기·장기 매수와 그날 점수 기준일.
# 보유 몇 종목만 읽는다
BUY_THESIS_TRADES_SQL = (
    "SELECT t.stock_id, t.horizon, t.snapshot_as_of FROM trades t JOIN json_each(?) j ON j.value = t.stock_id"
    " WHERE t.side = 'buy' AND t.horizon IN ('mid', 'long') AND t.snapshot_as_of IS NOT NULL"
    " ORDER BY t.trade_date DESC, t.id DESC"
)
#: 산 날의 같은 기간 신호(가장 높은 판)와 오늘(가장 최근) 판정표
BUY_THESIS_SIGNAL_SQL = (
    "SELECT id, as_of_date, rationale_data, calc_version FROM signals WHERE stock_id = ? AND as_of_date = ?"
    " AND horizon = ? ORDER BY calc_version DESC LIMIT 1"
)
BUY_THESIS_CHECKS_SQL = (
    "SELECT as_of_date, checks_json, calc_version FROM signal_checks WHERE stock_id = ? AND horizon = ?"
    " ORDER BY as_of_date DESC LIMIT 1"
)


def load_buy_thesis(client: TursoClient, held: list[int], warnings: list[str]) -> dict[int, dict]:
    """보유 종목 → {line, evidence}. 같은 기간 신호가 산 날에 있을 때만 (검증 A 조건 ①). 못 읽으면 그 줄만 빠진다."""
    out: dict[int, dict] = {}
    if not held:
        return out
    최근: dict[int, dict] = {}
    for r in _safe(client, BUY_THESIS_TRADES_SQL, [json.dumps(sorted(held))], warnings, "매수 근거(매매)"):
        최근.setdefault(int(r["stock_id"]), r)
    for sid, t in 최근.items():
        try:
            sig = client.execute(BUY_THESIS_SIGNAL_SQL, [sid, t["snapshot_as_of"], t["horizon"]]).dicts()
            if not sig:
                continue
            chk = client.execute(BUY_THESIS_CHECKS_SQL, [sid, t["horizon"]]).dicts()
        except Exception as exc:  # noqa: BLE001 — 사실 줄 하나다
            warnings.append(f"매수 근거를 읽지 못했습니다: {type(exc).__name__}")
            return out
        try:
            그때 = (json.loads(sig[0]["rationale_data"] or "{}") or {}).get("criteria") or []
            오늘 = json.loads(chk[0]["checks_json"]) if chk else None
        except (TypeError, ValueError):
            continue
        오늘판 = chk[0]["calc_version"] if chk else None
        결과 = bt.compare(str(t["horizon"]), 그때, sig[0]["calc_version"], 오늘, 오늘판)
        if 결과 is None:
            continue
        오늘날 = str(chk[0]["as_of_date"]) if chk else "없음"
        out[sid] = {
            "line": bt.line(str(t["horizon"]), str(sig[0]["as_of_date"]), 오늘날, 결과),
            "evidence": [
                vd._row("산 날의 신호", f"{bt.HORIZON_KO.get(str(t['horizon']))} 신호 #{sig[0]['id']}", "—",
                        f"signals 판 {sig[0]['calc_version']}", str(sig[0]["as_of_date"])),
                *([vd._row("오늘 판정표", f"{bt.HORIZON_KO.get(str(t['horizon']))} 판정표", "—",
                           f"signal_checks 판 {chk[0]['calc_version']}", 오늘날)] if chk else []),
            ],
        }  # fmt: skip
    return out
# 전환사채 잠재 물량 사실 줄 (55장, 25.1192) — 청구 기간이 남은 것만(표 자체가 작다)
CB_SQL = (
    "SELECT stock_id, receipt_no, decided_at, status, face_amount, conv_price, shares_pct, claim_start, claim_end,"
    " refix_floor FROM cb_issuances WHERE status = 'ok' AND decided_at >= ? AND claim_end >= ?"
)
# `halted` — 5거래일째 거래정지로 못 잰 건수 (25.1154)
REACTION_SQL = "SELECT type, label, n, mean_pct, median_pct, pos_pct, halted FROM disclosure_reaction"
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
    "SELECT f.stock_id, f.fiscal_year, f.report_code, f.consolidated, f.report_date, f.revenue, f.operating_income,"
    # 재무 건전성 (38장, 25.1061) — 같은 행에서 사업보고서(11011)의 재무상태표 열도 함께 읽는다(행 둘 더)
    " f.current_assets, f.current_liabilities, f.total_assets, f.total_liabilities, f.retained_earnings,"
    " f.total_equity, f.period_days"  # 짧은 사업연도 (25.1293)
    " FROM financials f JOIN json_each(?) j ON j.value = f.stock_id"
    " WHERE f.report_code IN ('11013', '11012', '11014', ?) AND f.fiscal_year >= ?"  # ? = dart.ANNUAL_REPORT_CODE
)
# 다음 실적 발표 (24장, 25.1048) — 종목마다 오늘 이후 가장 이른 실적발표 일정 하나 (SQLite 의 MIN 과 같은 행의 열)
EARNINGS_SQL = (
    "SELECT e.stock_id, MIN(e.scheduled_date) AS d, e.is_confirmed, e.source FROM earnings_calendar e"
    # 법정 기한 추정 행('실적발표(분기)'·'실적발표(연간)')도 (25.1827) — 야후 날짜가 없는 종목(국내 다수)은 빠졌다
    " JOIN stocks s ON s.id = e.stock_id WHERE s.country = ? AND e.event_type LIKE '실적발표%'"
    " AND e.scheduled_date >= ?"
    " GROUP BY e.stock_id"
)
# 같은 점수대의 지난 신호 성적 (22장, 25.1046) — 신호 성적표 작업이 실행 기록에 남긴 점수 보정표 (한 행)
CALIBRATION_SQL = (
    "SELECT step_log FROM batch_runs WHERE job_name = 'signal_outcomes' AND market = ? AND status = 'success'"
    " ORDER BY started_at DESC LIMIT 1"
)
# 이 종목 신호 성적 (37장, 25.1060) — 지난 1년 신호의 사후 결과(신호 성적표 작업이 쓴다)
SIGNAL_HISTORY_SQL = (
    "SELECT o.stock_id, o.as_of_date, o.horizon, o.ret_5d, o.ret_20d, o.ret_60d, o.hit_target, o.hit_stop,"
    " o.bench_ret_20d FROM signal_outcomes o JOIN stocks s ON s.id = o.stock_id"
    " WHERE s.country = ? AND o.as_of_date >= ? ORDER BY o.stock_id, o.as_of_date"
)
SIGNAL_HISTORY_DAYS = 365
# 커버리지 개시 (49장, 15회차 S6) — (종목, 증권사)마다 지난 1년 첫 의견일. 국내만. 날짜 색인으로 1년치 의견 행을 읽는다
INITIATION_SQL = (
    "SELECT stock_id, broker, MIN(date) AS first_date FROM kr_opinions WHERE date >= ? AND target_price > 0"
    " GROUP BY stock_id, broker"
)
# 감성과 가격의 엇갈림 (50장, 25.1070) — 지난 한 주 안 감성 행(종목마다 최신)과 그 30일 앞 한 주의 종가
# 날짜 색인(0061)을 바깥으로 고정한다(25.1081). 종목순 정렬을 빼야 플래너가 (stock_id, as_of_date) 색인
# 통째 훑기를 고르지 않는다 — 종목마다 최신 한 행은 파이썬이 날짜 내림차순의 첫 행으로 고른다(setdefault)
SENTIMENT_GAP_SQL = (
    "SELECT ss.stock_id, ss.as_of_date, ss.sentiment, ss.delta_30d FROM sentiment_scores ss"
    " CROSS JOIN stocks s ON s.id = ss.stock_id WHERE ss.as_of_date >= ? AND s.country = ? AND ss.delta_30d IS NOT NULL"
    " ORDER BY ss.as_of_date DESC"
)
# **감성 있는 종목의 종가만** (docs/infra.md 25.1081). 예전에는 나라 전체 시세 약 28거래일을 날짜 색인으로
# 읽어(다른 나라 행까지 지나간다) 파이썬에서 감성 종목만 골랐다. 종목 목록을 바깥에 고정하면 (stock_id, date) 색인
# 범위만 읽는다
# 수정주가 가운데 빈 행은 직전 계수로 메운다 (`adjust.split_only_prices`, docs/infra.md 25.1193).
# 30일 앞 종가가 빈 행이면 원 종가(1:10 이면 10배)가 들어가 주가 변화가 −90% 로 읽혀
# "감성은 좋아졌는데 주가는 빠졌다" 가 거짓으로 섰다
PRICE_BACK_SQL = (
    "SELECT p.stock_id, p.date, p.adj_close, p.close, s.country"
    " FROM json_each(?) j CROSS JOIN prices p ON p.stock_id = j.value CROSS JOIN stocks s ON s.id = p.stock_id"
    " WHERE p.date >= ? AND p.date <= ? AND p.close > 0 ORDER BY p.stock_id, p.date"
)
# 점수 변화 (17장) — 4주 앞 가장 가까운 점수일
SCORES_BEFORE_SQL = (
    "SELECT sc.stock_id, sc.as_of_date, sc.total_score, sc.factor_scores, sc.weights_json, sc.sentiment_score,"
    " sc.sentiment_weight_used FROM scores sc"
    " JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = (SELECT sc2.as_of_date FROM scores sc2"
    "   CROSS JOIN stocks s2 ON s2.id = sc2.stock_id WHERE s2.country = ? AND sc2.as_of_date <= ?"
    " ORDER BY sc2.as_of_date DESC LIMIT 1)"
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
#: 기간이 찼는데 오늘 종가가 없던(거래정지) 예측을 기다리는 달력 날 수 (docs/infra.md 25.1085). 거래가 다시 열리면 그날
#: 종가로 채점한다. 이보다 오래 멈춘 종목(상장폐지 등)은 버리고 `dropped` 에 센다 — 조용히 빼지 않는다. 90일은 국내
#: 거래정지 대부분(감사·실질심사 결과 대기)을 덮는 어림 [확인필요: 정지 기간 분포를 재지 않았다]
PENDING_MAX_DAYS = 90


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
            못함 = evaluate_date(client, country, d, 달, now_close, prior, market, 바뀜, warnings)
            if 못함 is None:
                # **읽기에 실패한 날은 `through` 를 넘기지 않는다** (25.1152, 감사 재현) — 예전엔 빈 목록으로 보고
                # 넘어가 그 기록일의 예측이 영영 채점되지 않았다. 뒤 날짜도 멈춘다(순서대로 넘겨야 막을 수 있다)
                break
            through[str(달)] = d
            본날.append(f"{달}개월←{d}")
            # **거래정지라 오늘 종가가 없던 종목은 기다린다** (25.1085). 예전엔 `through` 만 넘어가 영영 채점되지
            # 않았다 — 정지·상폐는 대개 나쁜 소식이라 성적표가 살아남은 종목 쪽으로 낙관적으로 기울었다
            state.setdefault("pending", []).extend([d, 달, sid] for sid in 못함)
    _pending(client, country, as_of, now_close, prior, market, 바뀜, state, warnings)
    return 본날


def _pending(client: TursoClient, country: str, as_of: str, now_close: dict[int, dict], prior: dict[int, dict],
             market: dict, 바뀜: set[int], state: dict, warnings: list[str]) -> None:  # fmt: skip
    """기다리던 예측 가운데 오늘 종가가 생긴 것을 채점한다. 너무 오래 기다린 것은 버리고 센다."""
    남김: list[list] = []
    묶음: dict[tuple[str, int], set[int]] = {}
    오늘 = date.fromisoformat(as_of[:10])
    for d, 달, sid in state.get("pending") or []:
        if (now_close.get(int(sid)) or {}).get("close"):
            묶음.setdefault((str(d), int(달)), set()).add(int(sid))
        elif (오늘 - date.fromisoformat(str(d)[:10])).days > 31 * int(달) + PENDING_MAX_DAYS:
            state["dropped"] = int(state.get("dropped") or 0) + 1
        else:
            남김.append([d, 달, sid])
    for (d, 달), sids in sorted(묶음.items()):
        못함 = evaluate_date(client, country, d, 달, now_close, prior, market, 바뀜, warnings, only=sids)
        남김 += [[d, 달, s] for s in (sorted(sids) if 못함 is None else 못함)]  # 읽기 실패면 그대로 기다린다
    state["pending"] = 남김


def evaluate_date(client: TursoClient, country: str, d: str, 달: int, now_close: dict[int, dict],
                  prior: dict[int, dict], market: dict, 바뀜: set[int], warnings: list[str],
                  only: set[int] | None = None) -> list[int] | None:  # fmt: skip
    """기록일 하나의 `달` 개월 예측을 오늘 종가와 견준다. **기준 종가는 있는데 오늘 종가가 없는**(거래정지) 종목을
    돌려준다 — 부르는 쪽이 기다린다(25.1085). `only` 를 주면 그 종목만.

    **읽기에 실패하면 None** (25.1152) — 아무것도 더하지 않았으니 부르는 쪽이 그 날을 다음에 다시 본다."""
    종가줄 = _strict(client, CLOSE_SQL, [country, str(d)], warnings, "기준 종가")
    기록줄 = None if 종가줄 is None else _strict(client, LOG_ROWS_SQL, [str(d), country], warnings, "예측 기록")
    if 종가줄 is None or 기록줄 is None:
        return None
    기준 = {int(r["stock_id"]): r for r in 종가줄}
    if country == "KR":
        메움 = _fill_base_holes(client, 기준, str(d), warnings)
        if 메움 is None:
            return None
    # **미국 분할이 재수집을 기다리는 종목은 기다린다** (docs/infra.md 25.1255, 미국 시세 감사 재현).
    # 일일 배치는 최근 10일만 고쳐 쓰고 옛 행은 토요일 재수집까지 분할 전 값이라, 몇 달 전 기준 종가와
    # 오늘(분할 뒤) 종가를 견주면 4:1 분할이 −75% 로 채점됐다 — 성적표는 더하기만 하고 `through` 가 넘어가
    # 재수집 뒤에도 지워지지 않았다(25.1190 과 같은 모양). 거래정지와 같은 길(`못함` → `_pending`)로 미룬다
    분할대기: set[int] = set()
    if country == "US":
        try:
            분할대기 = db.split_pending_us(client)
        except Exception as exc:  # noqa: BLE001 — 읽기 실패는 그 날을 다음에 다시 본다(25.1152 와 같은 처리)
            warnings.append(f"분할 대기열을 읽지 못해 이 기록일 채점을 미룹니다: {type(exc).__name__}")
            return None
    못함: list[int] = []
    for r in 기록줄:
        sid = int(r["stock_id"])
        if only is not None and sid not in only:
            continue
        b, n = (기준.get(sid) or {}).get("close"), (now_close.get(sid) or {}).get("close")
        if b and (not n or sid in 분할대기):
            못함.append(sid)
            continue
        if not b:
            continue
        try:
            models = json.loads(r["models_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for model, cell in ft.evaluate(models, 달, float(b), float(n)).items():
            ft.add(prior.setdefault(sid, {}), model, 달, cell)
            ft.add(market, model, 달, cell)
            바뀜.add(sid)
    return 못함


#: 그 시장의 **유니버스 의견만** 지운다 — 참고 분석(`jobs/analyze_extra`, `detail.excluded_reason` 이 있는 행)은
#: 남긴다 (25.1022).
#: 예전엔 시장 전체를 지워, 일일 배치의 참고 분석 단계가 실패하거나 30종목 상한을 넘으면 "지금 분석" 결과가 사라졌다
CLEAR = "DELETE FROM stock_verdicts WHERE market = ? AND json_extract(detail_json, '$.excluded_reason') IS NULL"


def _fill_base_holes(client: TursoClient, 기준: dict[int, dict], d: str, warnings: list[str]) -> int | None:
    """기준일 국내 종가 가운데 `adj_close` 가 빈 행을 앞뒤 행으로 메운다 (docs/infra.md 25.1190).
    메운 수를 돌려주고, 읽기에 실패하면 None.

    예전에는 `COALESCE(adj_close, close)` 한 날만 읽어, 분할 전 구간 한가운데 빈 행이면 원 종가(1:10 이면 10배)가
    기준이 되어 그 기록일 예측이 −90% 로 채점됐다 — 모델 성적표(예측 적중)가 한 종목에 끌렸다.
    빈 행이 없으면 더 읽지 않는다(거의 늘 그렇다)."""
    빈 = sorted(sid for sid, r in 기준.items() if r.get("adj_close") is None and r.get("raw_close"))
    if not 빈:
        return 0
    day = date.fromisoformat(d)
    창 = [(day - timedelta(days=HOLE_WINDOW_DAYS)).isoformat(), (day + timedelta(days=HOLE_WINDOW_DAYS)).isoformat()]
    줄 = _strict(client, HOLE_SQL, [json.dumps(빈), *창], warnings, "기준 종가 앞뒤")
    if 줄 is None:
        return None
    묶음: dict[int, list[dict]] = defaultdict(list)
    for r in 줄:
        묶음[int(r["stock_id"])].append(r)
    n = 0
    for sid, rows in 묶음.items():
        for r, (px, _) in zip(rows, adj.fill_adjusted(rows), strict=True):
            if str(r["date"]) == d and px:
                기준[sid] = {**기준[sid], "close": float(px)}
                n += 1
    return n


def _strict(client: TursoClient, sql: str, args: list, warnings: list[str], what: str) -> list[dict] | None:
    """`_safe` 와 같되 **일시 오류를 빈 목록과 가른다** — 표가 없으면 [](정말 없음), 그 밖은 경고하고 None (25.1152)."""
    try:
        return client.execute(sql, args).dicts()
    except Exception as exc:  # noqa: BLE001
        if db.표가_없나(exc):
            return []
        warnings.append(f"{what}을(를) 읽지 못했습니다: {exc}")
        return None


def _safe(client: TursoClient, sql: str, args: list, warnings: list[str], what: str) -> list[dict]:
    """곁 재료는 못 읽어도 의견은 낸다 — 표가 없으면(마이그레이션 전) 조용히, 그 밖은 경고."""
    try:
        return client.execute(sql, args).dicts()
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"{what}을(를) 읽지 못했습니다: {exc}")
        return []


def _units_safe(client: Any, rows: list, today: str, stock_of: Any, date_of: Any,
                warnings: list[str]) -> tuple[list, dict[int, int]]:  # fmt: skip
    """분할·병합을 건넌 증권사 목표가를 뺀다 (`services/target_units`, 25.1343). 계수를 못 읽으면 그대로 두고 말한다."""
    from batch.services import target_units

    try:
        return target_units.drop_across_actions(client, rows, today, stock_of, date_of)
    except Exception as exc:  # noqa: BLE001 — 곁다리. 예전처럼 원 목표가로 두되 말한다
        if not db.표가_없나(exc):
            warnings.append(f"증권사 목표가의 기업행위 계수를 읽지 못해 그대로 썼습니다: {exc}")
        return rows, {}


def flow_anchor(dates: list[str], today: date) -> date:
    """수급 창의 마지막 거래일 (docs/infra.md 25.1388, KIS 수급 감사).

    이 잡은 아침 일일 배치 안에서 돈다(`daily` "종목 분석 의견"). 그날 수급은 저녁 수집에서야 들어오므로
    `today` 를 끝으로 잡으면 "최근 5거래일" 중 하루가 늘 비어 **4거래일**로 셌고, 같은 아침 리포트(3.9, 전 거래일 끝)와
    다른 종목에 반대 목소리가 붙었다. 그날 행이 하나라도 있으면(저녁 수동 실행) 그날, 없으면 직전 거래일 — 한 거래일
    넘게 물러나지 않으므로 수집이 멈춘 묵은 행은 여전히 창 밖이다(25.1337). 달력을 못 읽으면 `today`"""
    if today.isoformat() in set(dates):
        return today
    try:
        return cal.previous_session("KR", before=today)
    except Exception:  # noqa: BLE001 — 달력을 못 읽으면 예전 창
        return today


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
        의견, _ = _units_safe(client, list(의견), today.isoformat(), lambda r: int(r[0]), lambda r: str(r[1]),
                            warnings)  # 분할·병합을 건넌 목표가는 견주지 않는다 (25.1343)
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"수급·의견을 읽지 못했습니다: {exc}")
        흐름, 의견 = [], []
    # **최근 FLOW_DAYS 거래일 안의 행만** (docs/infra.md 25.1337, 분석 화면 감사 #3 — 리포트 25.1276 과 같은 창). 달력
    # 15일 창이라 수급 수집이 멈추면 1~2주 묵은 행이 "외국인·기관 5거래일 순매도" 로 반대 목소리에 실렸다.
    # 더하기는 리포트와 한 함수(`divergence.sum_flows`, 25.1384) — 날짜를 함께 돌려줘 거래대금 몫을 같은 날로 낸다
    기준 = flow_anchor([str(r[1]) for r in 흐름], today)
    합 = divergence.sum_flows(list(흐름), daily.flow_window_start(기준))
    최근날 = {sid: v[3][0] for sid, v in 합.items() if v[3]}
    몫 = daily.flow_turnover_shares(client, 합, 기준.isoformat(), warnings)
    목표: dict[int, list[tuple[str, str, float | None]]] = defaultdict(list)
    for sid, d, broker, target in 의견:
        목표[int(sid)].append((str(d), str(broker), float(target) if target else None))
    since = (today - timedelta(days=divergence.OPINION_DAYS)).isoformat()
    for sid in ids:
        n, frgn, orgn, _날 = 합.get(sid, (0, 0, 0, []))
        날 = 최근날.get(sid)
        up, down = divergence.target_changes(목표.get(sid, []), since)
        v = divergence.View("", "", n, frgn, orgn, up, down, 몫.get(sid))
        if v.flow_against:
            합계 = f"{(frgn + orgn) / 100:+,.1f}억 ({n}거래일)" + (
                f", 거래대금의 {fixed(v.turnover_share * 100, 2, sign=True)}%" if v.turnover_share is not None else "")
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
    의견뺌: dict[int, int] = {}
    성적: dict[str, dict] = {}
    if country == "KR":
        성적 = {str(r["broker"]): r for r in _safe(client, BROKER_STATS_SQL, [], warnings, "증권사 성적표")}
        since = (today - timedelta(days=vd.CONSENSUS_DAYS)).isoformat()
        의견행 = _safe(client, OPINIONS_SQL, [since], warnings, "증권사 목표가")
        의견행, 의견뺌 = _units_safe(client, 의견행, today.isoformat(), lambda r: int(r["stock_id"]),
                               lambda r: str(r["date"]), warnings)  # fmt: skip
        for r in 의견행:
            의견[int(r["stock_id"])].append(r)
    국면: dict[int, dict] = {}
    # 오늘 시장 국면 (29장, 25.1054) — 주간 표가 칸을 나눈 기준 지수와 같은 지수, 추세 필터와 같은 정의
    시장국면 = None
    try:
        from batch.jobs import metrics
        from batch.services import trend

        code = metrics.BENCHMARK.get(country)
        if code:
            closes, source = trend.load_index_closes(client, code, as_of)
            g = trend.regime_from_closes(code, closes, as_of, source)
            시장국면 = g.state if g.state in ("bull", "bear") else None
    except Exception as exc:  # noqa: BLE001 — 시장 국면 줄만 빠진다
        warnings.append(f"시장 국면을 읽지 못했습니다: {exc}")
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
    # 시장 전체 신고가 뒤 (34장) — 주간 작업이 나라마다 한 행
    시장신고가 = None
    try:
        시장신고가 = (db.get_setting(client, history.market_key(country)) or {}).get("h")
    except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
        warnings.append(f"시장 전체 신고가 뒤 분포를 읽지 못했습니다: {exc}")
    try:
        무위험 = risk_free(client, country)
    except Exception as exc:  # noqa: BLE001 — 0 으로 두고 그렇게 적는다
        warnings.append(f"무위험수익률을 읽지 못했습니다: {exc}")
        무위험 = None
    return {"close": 종가, "momentum": 모멘텀, "band": 밴드, "risk": 위험, "opinions": 의견, "opinions_dropped": 의견뺌,
            "market": market_returns(client, country, as_of, warnings), "rf": 무위험,
            "patterns": 국면, "fundamentals": 재무원값, "broker_stats": 성적, "regime": 시장국면,
            "market_breakout": 시장신고가}  # fmt: skip


def outlook_for(재료: dict, sid: int, currency: str, today: date, market: str | None = None) -> dict:
    from batch.services import patterns, trend

    c = 재료["close"].get(sid) or {}
    b = 재료["band"].get(sid)
    m = 재료["momentum"].get(sid) or {}
    return vd.outlook(
        analog=patterns.pick((재료.get("patterns") or {}).get(sid), m.get("momentum_3m"), m.get("high_52w_proximity"),
                              재료.get("regime")),
        fundamentals=(재료.get("fundamentals") or {}).get(sid), broker_stats=재료.get("broker_stats"),
        stress=((재료.get("patterns") or {}).get(sid) or {}).get("stress"),
        vol=((재료.get("patterns") or {}).get(sid) or {}).get("vol"),
        history=(재료.get("patterns") or {}).get(sid), market_breakout=재료.get("market_breakout"),
        close=c.get("close"), close_date=c.get("date"), currency=currency, momentum=재료["momentum"].get(sid),
        risk=재료["risk"].get(sid), band=b, opinions=재료["opinions"].get(sid, []), today=today,
        opinions_dropped=(재료.get("opinions_dropped") or {}).get(sid, 0),
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
    for r in _safe(client, SIGNALS_SQL, [country, country, country], warnings, "신호"):
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
    위험이력: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        for r in _safe(client, GOVERNANCE_SQL, [], warnings, "공시 위험 이력"):
            위험이력[int(r["stock_id"])].append(r)
    전환사채: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        for r in _safe(client, CB_SQL, [(today - timedelta(days=cb.WINDOW_DAYS)).isoformat(), today.isoformat()],
                       warnings, "전환사채 발행결정"):  # fmt: skip
            전환사채[int(r["stock_id"])].append(r)
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
        for r in _safe(client, QUARTERS_SQL, [json.dumps(sorted(점수)), dart.ANNUAL_REPORT_CODE, today.year - 2],
                       warnings, "분기 재무"):
            분기[int(r["stock_id"])].append(r)
    실적일 = {int(r["stock_id"]): r for r in _safe(client, EARNINGS_SQL, [country, today.isoformat()], warnings,
                                                   "실적 일정")}  # fmt: skip
    실적반응 = None
    if country == "KR":
        실적반응 = next((r for r in _safe(client, REACTION_SQL, [], warnings, "공시 반응 통계")
                     if r.get("type") == "earnings"), None)  # fmt: skip
    # 감성과 가격의 엇갈림 (50장) — 감성 행의 날짜에서 30일 앞 종가(그날 이하 마지막, 한 주 안)와 그날 종가
    감성: dict[int, dict] = {}
    한주 = (today - timedelta(days=7)).isoformat()
    for r in _safe(client, SENTIMENT_GAP_SQL, [한주, country], warnings, "뉴스 감성"):
        감성.setdefault(int(r["stock_id"]), r)
    감성가격: dict[int, list[tuple[str, float]]] = defaultdict(list)
    if 감성:
        가장앞 = min(date.fromisoformat(str(r["as_of_date"])[:10]) for r in 감성.values())
        시작 = (가장앞 - timedelta(days=insights.SENT_GAP_DAYS + 7)).isoformat()
        종목들 = json.dumps(sorted(감성))
        묶음: dict[int, list[dict]] = defaultdict(list)
        for r in _safe(client, PRICE_BACK_SQL, [종목들, 시작, today.isoformat()], warnings, "엇갈림 종가"):
            if int(r["stock_id"]) in 감성:
                묶음[int(r["stock_id"])].append(r)
        for sid, 줄 in 묶음.items():
            for r, px in zip(줄, adj.split_only_prices(줄, str(줄[0]["country"])), strict=True):
                if px and px > 0:
                    감성가격[sid].append((str(r["date"]), float(px)))
    개시: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        for r in _safe(client, INITIATION_SQL, [(today - timedelta(days=insights.INIT_LOOKBACK_DAYS)).isoformat()],
                       warnings, "커버리지 개시"):  # fmt: skip
            개시[int(r["stock_id"])].append(r)
    신호이력: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, SIGNAL_HISTORY_SQL, [country, (today - timedelta(days=SIGNAL_HISTORY_DAYS)).isoformat()],
                   warnings, "이 종목 신호 성적"):  # fmt: skip
        신호이력[int(r["stock_id"])].append(r)
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
            # 같은 업종 대체 후보 (47장) — 성과 지표의 연환산 변동성·MDD
            "vol": (재료["risk"].get(sid) or {}).get("volatility_ann"), "mdd": (재료["risk"].get(sid) or {}).get("mdd"),
        })  # fmt: skip
    # 첫 성적이 나오는 날 — 처음 쌓은 날의 한 달 뒤(가장 짧은 기간)
    첫평가 = ft.months_back(date.fromisoformat(str(상태["since"])[:10]), -vd.FORECAST_MONTHS[0]).isoformat()
    stamp = db.now_iso()
    rows: list[tuple[str, list[Any]]] = [(CLEAR, [country])]
    기록: list[tuple[str, list[Any]]] = []
    레이더재료: list[dict] = []
    매수근거 = load_buy_thesis(client, sorted(sid for sid in 보유 if sid in 점수), warnings)
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
            position=vd.position_of(p),
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
            조각 = [f"{vd.FACTOR[변화[k]]} {fixed(변화['factors'][변화[k]], 0, sign=True)}"
                  for k in ("up", "down") if 변화.get(k)]  # .5 는 0 에서 멀어지게 (25.1338)
            뒤 = f" (가장 오른 팩터 {조각[0]} · 가장 내린 팩터 {조각[1]})" if len(조각) == 2 else ""
            out["reasons"].append(f"점수 변화: {변화['since']}보다 {fixed(변화['delta'], 1, sign=True)}{뒤}")
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
        # 성적 가중 합의 (30장, 25.1056) — 네 눈(19장)을 시장 전체 1년 성적으로 가중
        합의 = (detail.get("outlook") or {}).get("agreement")
        if 합의:
            합의["weighted"] = insights.weighted_agreement(합의, 시장성적, 상태.get("since"))
            가중 = 합의["weighted"] or {}
            if 가중.get("value") is not None:
                out["reasons"].append("성적 가중 1년 예상: " + f"{가중['value'] * 100:+.1f}% ("
                                      + " · ".join(f"{ft.MODELS.get(m, m)} {w * 100:.0f}%"
                                                   for m, w in 가중["weights"].items()) + ")")  # fmt: skip
        detail["score_change"] = 변화
        detail["twins"] = 닮음
        # 사실 줄 (54장) — 점수·결론·반대 목소리 셈에 넣지 않는다. 근거표에는 접수번호
        사실 = dr.governance_line(위험이력.get(sid, []))
        if 사실:
            detail.setdefault("facts", []).append(사실)
            out["evidence"] += [vd._row("공시 위험 이력", str(e["title"]), "—", f"DART 접수번호 {e['receipt_no']}",
                                        str(e["day"])) for e in 위험이력[sid]]  # fmt: skip
        # 전환사채 잠재 물량 (55장, 25.1192) — 같은 사실 줄. 근거표에는 접수번호와 결정일
        물량 = cb.line(전환사채.get(sid, []), today)
        if 물량:
            detail.setdefault("facts", []).append(물량)
            out["evidence"] += [vd._row("전환사채 발행결정", f"발행주식의 {e['shares_pct']}%" if e.get("shares_pct")
                                        is not None else "—", "—", f"DART 접수번호 {e['receipt_no']} (cvbdIsDecsn)",
                                        str(e["decided_at"])) for e in cb.live(전환사채[sid], today)]  # fmt: skip
        # 매수 근거가 아직 참인가 (56장, 25.1312) — 같은 사실 줄. 결론·플래그·알림에 넣지 않는다
        if sid in 매수근거:
            detail.setdefault("facts", []).append(매수근거[sid]["line"])
            out["evidence"] += 매수근거[sid]["evidence"]
        if sid in 내부자:
            행 = insider.criteria_rows(내부자[sid], r.get("listed_shares"))
            if 행:
                out["reasons"].append(f"내부자 매매 최근 {내부자[sid].window_days}일: {행[0]['display']}")
                out["evidence"].append(행[0])
            detail["insider"] = 내부자[sid].as_dict()
        if 분기.get(sid):
            건전 = insights.health(분기[sid], r.get("sector_code"))
            detail["health"] = 건전
            if 건전 and 건전.get("z") is not None:
                out["reasons"].append(insights.health_line(건전))
            추세 = insights.quarter_trend(분기[sid])
            줄 = insights.quarter_line(추세)
            if 줄:
                out["reasons"].append(줄)
            detail["quarters"] = 추세
        if sid in 실적일:
            e = 실적일[sid]
            남은 = (date.fromisoformat(str(e["d"])[:10]) - today).days
            # 법정 기한 추정은 "예상 날짜" 가 아니라 늦어도 그날까지라는 뜻이다 (25.1827)
            확정 = ("법정 기한(늦어도)" if str(e.get("source") or "").startswith("estimate:")
                  else "확정" if e.get("is_confirmed") else "예상")  # fmt: skip
            # 반응 통계는 장중 알림과 **같은 문장 함수**로 (docs/infra.md 25.1394, 공시 감사). 예전에는 따로 짜서
            # 표본 하한(`MIN_N` 30)·거래정지로 못 잰 건수(25.1154)·"공시일 반응 포함" 문구(25.1013)가 모두 빠졌다 —
            # 표가 다시 만들어지는 첫 주처럼 n 이 적어도 중앙값이 근거 줄에 나갔다
            반응줄 = dr.line(실적반응)
            반응 = f" — 시장 전체 {반응줄}" if 반응줄 else ""
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
        # 함께 움직이는 종목 (52장) — 주간 표의 짝을 이름으로(유니버스 종목만)
        짝원본 = ((재료.get("patterns") or {}).get(sid) or {}).get("comovers") or []
        짝 = [{**x, "ticker": 점수[x["stock_id"]]["ticker"], "name": 점수[x["stock_id"]]["name"]}
             for x in 짝원본 if x["stock_id"] in 점수]  # fmt: skip
        detail["comovers"] = 짝 or None
        폭 = ((재료.get("patterns") or {}).get(sid) or {}).get("pairs_gap")
        detail["pairs_gap"] = 폭
        if 폭:
            # 53장 — 사실과 문헌만. 우리 백테스트 판정 전이라 "따라잡는다" 고 말하지 않는다
            out["reasons"].append(
                f"짝 {폭['n']}종목(시장 몫을 뺀 1년 상관 상위, {폭['formation_until']}까지로 고름)의 지난 한 달 평균 "
                f"{폭['peers'] * 100:+.1f}%, 이 종목 {폭['own'] * 100:+.1f}%, β {폭['beta']:.2f} — 벌어진 폭 "
                f"{폭['gap'] * 100:+.1f}%p (짝 거래 문헌은 다음 달 좁혀지는 쪽, 우리 판정 전)")  # fmt: skip
        if 짝:
            밖 = [x["name"] for x in 짝 if not x["same_sector"]]
            out["reasons"].append("함께 움직이는 종목(지난 1년, 시장 몫을 뺀 상관): "
                                  + ", ".join(f"{x['name']} {x['corr']:.2f}" for x in 짝)
                                  + (f" — 업종 분류 밖: {', '.join(밖)}" if 밖 else ""))  # fmt: skip
        엇갈림 = None
        if sid in 감성:
            끝날 = str(감성[sid]["as_of_date"])[:10]
            앞날 = (date.fromisoformat(끝날) - timedelta(days=insights.SENT_GAP_DAYS)).isoformat()
            계열 = sorted(감성가격.get(sid, []))
            앞 = [c for d, c in 계열 if d <= 앞날 and d >= (date.fromisoformat(앞날) - timedelta(days=7)).isoformat()]
            뒤 = [c for d, c in 계열 if d <= 끝날]
            엇갈림 = insights.sentiment_gap(감성[sid], 앞[-1] if 앞 else None, 뒤[-1] if 뒤 else None)
        detail["sentiment_gap"] = 엇갈림
        줄 = insights.sentiment_gap_line(엇갈림)
        if 줄:
            out["reasons"].append(줄)
        새증권사 = insights.initiations(개시.get(sid, []), today)
        if 새증권사:
            detail["initiations"] = 새증권사
            out["reasons"].append(f"최근 {insights.INIT_RECENT_DAYS}일 처음 의견을 낸 증권사(그 앞 1년 의견 없음): "
                                  + ", ".join(f"{x['broker']}({x['date']})" for x in 새증권사))  # fmt: skip
        이력 = insights.signal_history(신호이력.get(sid, []))
        detail["signal_history"] = 이력
        if 이력 and 이력["n20"]:
            초과 = f"(시장 대비 {이력['excess20'] * 100:+.1f}%p)" if 이력["excess20"] is not None else ""
            out["reasons"].append(
                f"이 종목에 난 신호(지난 1년 {이력['signals']}번): 20거래일 뒤 평균 {이력['avg20'] * 100:+.1f}%{초과}"
                f"·오른 {이력['up20']}/{이력['n20']}번, 목표 도달 {이력['targets']}·손절 도달 {이력['stops']}")
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
            대체 = insights.alternatives(sid, 업종.get((str(r.get("market")), str(r["sector"])), []))
            detail["alternatives"] = 대체
            if 대체:
                out["reasons"].append("같은 업종에서 점수가 같거나 높고 변동성이 낮은 종목: " + ", ".join(
                    f"{a['name']}(점수 {a['total']:.0f}·변동성 {a['vol'] * 100:.0f}%)" for a in 대체)
                    + f" — 이 종목 변동성 {대체[0]['my_vol'] * 100:.0f}%")  # fmt: skip
        레이더재료.append({"stock_id": sid, "ticker": r["ticker"], "name": r["name"], "verdict": out["verdict"],
                       "ladder": (out.get("outlook") or {}).get("ladder"), "score_change": 변화,
                       "agreement": (out.get("outlook") or {}).get("agreement"),
                       "sentiment_gap": 엇갈림})  # fmt: skip
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
