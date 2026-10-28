"""핵심 H 다년 백테스트의 장부형 전략 다섯 — 조각 1c (docs/backtest.md 10장, docs/infra.md 25.1532). 순수 함수.

`make_core_strategy(variant, market, sessions=…)` 가 엔진(`bt.simulate`)의 판단 함수를 돌려준다. 날마다
`bt.LedgerOrders(sells, buys)` — 운영 가상모의투자 한 계좌의 하루(`jobs/paper.run_step`)를 그 시점으로 다시 돈다:
① 보유 매도 판정(`sell_flags.evaluate` + 추세 계좌는 `jobs/paper._추적손절`) → ② 계좌 규칙(`pp.sells_for`) →
③ 판 뒤 쉼(`pp.REBUY_COOLDOWN_SESSIONS`) → ④ 매수(`jobs/paper.buy_orders` — 2부 `build_portfolio`·`size_buys`,
무작위 대조는 `random_substitute`) → ⑤ 사전 위험 점검(`risk_gate.gate`).

**판단식을 다시 적지 않는다.** 운영 함수는 DB 클라이언트를 받으므로 `_ViewClient` 가 그 자리에 들어가
**cutoff 로 자른 가격 창(`PriceView`)만으로** 세 가지 읽기(마지막 종가·첫 매수 뒤 고점·기업행위 창)에 답한다 —
모르는 읽기는 오류로 멈춘다(운영이 읽기를 더하면 여기서 조용히 빈 값이 되지 않게). 사기 전 점검 계좌(A 가치·C 모멘텀,
25.1934)는 `_PitClient` 가 운영 `sell_flags.enrich`·`prebuy_sell_flags` 의 읽기(재무·점수·유니버스)에도 cutoff 까지의
자료로 답한다 — 감성은 늘 빈 답(영구 차이).

**제 장부를 엔진과 맞춘다.** 첫 매수일·기간·주당 원가(수수료 포함)·고점 시작일을 전략이 들고, 날마다 `view.weights`·
`view.holding` 과 맞춘다 — 엔진이 못 산 것(정지·상장폐지·체결가 없음)은 버리고, 엔진이 판 것(상장폐지)은 지운다. 산 날의
원가는 **다음 결정에서야** 그날 종가가 창에 들어와 정한다 — cutoff 뒤 가격은 읽을 길이 없다.

운영과 다른 것(알고 둔다, docs/backtest.md 10장 조각 1c): 체결은 t 종가 전량(운영 3회 분할 지정가·시가 시장가 — 회차
변형은 `limit_fills=True` 로 켜면 회차마다 t 종가 ≤ 지정가 일 때만 지정가에 찬다, 25.1914 전략 깔때기 ④ 분할 지정가
체결) · 상관 축소 없음 · 정수 주·최소 주문 금액은 명목 자본(`NOTIONAL_EQUITY`)이 커서 묶이지 않는다 ·
기업행위 멈춤 없음(수정주가) · 정지 매도의 20거래일 청산 없음 · 하루 손실 관문의 수익률은 직전 cutoff 비중 × 종가 변화.
"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass, field, replace
from datetime import date
from functools import partial
from typing import Any

from batch.services import backtest as bt
from batch.services import company as co
from batch.services import core_backtest as cb
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import risk_gate as rg
from batch.services import sell_flags as sf


@dataclass(frozen=True)
class Variant:
    """운영 계좌 칸(`paper_accounts`) 그대로 — 마이그레이션 0070(B)·0075(R1·R2)·0082(G)·0083(H)."""

    account: str  # 운영 계좌 id — 무작위 씨앗 `계좌|시장|신호 종목` 에 들어간다
    sell_rule: str  # `pp.SELL_RULES` 열쇠
    entry: str  # tranche(3회 분할 지정가) · open(시가 일괄)
    top_n: int
    selection: str  # signal · random · weighted(지주사 H) · committee(위원회 회사 — 탐색 변형, 25.1904)


#: 핵심 변형 다섯 — 짝 차이가 운영과 같은 뜻(H−G 선정, G−B 매도, B−R 선정 대 무작위)
VARIANTS: dict[str, Variant] = {
    "core_h": Variant("H", "trend", "tranche", 5, "weighted"),
    "core_g": Variant("G", "trend", "tranche", 5, "signal"),
    "core_b": Variant("B", "core", "tranche", 5, "signal"),
    "core_r1": Variant("R1", "core", "open", 5, "random"),
    "core_r2": Variant("R2", "core", "open", 5, "random"),
}

#: 탐색 변형 — 전략 깔때기 0단계(docs/funnel.md 4장, 25.1904). 핵심 H 판정(`VARIANTS` — 11-03 기본 실행·시도 수)에는
#: 넣지 않고 탐색 실행기가 따로 돌린다. 위원회 회사(D 장기보유·E 안정·A 가치·C 모멘텀)는 하루 재료(`DayView`)의
#: 같은 안건·모집단으로 운영 `jobs/paper.committee_step` 과 같은 `co.meet` 를 다시 열어 가결 행을 미리 고름으로
#: 넘긴다. 상위 몇 종목은 운영처럼 헌장 `top_n` 이 정한다(여기 `top_n` 은 헌장을 읽을 때 덮인다).
#: 가치(A)·모멘텀(C)은 매도 규칙이 사기 전 점검(`PREBUY_CHECK_RULES`)이라 운영 `prebuy_sell_flags`·
#: `sell_flags.enrich` 를 시점 창(`_PitClient`)으로 부른다(25.1934, funnel.md 4장 ⑤) — 감성급락은 리플레이에서
#: 늘 "플래그 없음"(무료 과거 뉴스 없음, 영구 차이). 칸은 운영 계좌 그대로: A = 0070 + 0077(`value`, 진입·top_n 은
#: 0075 열 기본값 tranche·5), C = 0075(open·5) + 0077(`momentum`)
EXPLORE_VARIANTS: dict[str, Variant] = {
    "core_d": Variant("D", "time", "tranche", 5, "committee"),
    "core_e": Variant("E", "core", "tranche", 10, "committee"),
    "core_a": Variant("A", "value", "tranche", 5, "committee"),
    "core_c": Variant("C", "momentum", "open", 5, "committee"),
}



def variant_spec(name: str) -> Variant:
    """변형 이름 → 칸(핵심 판정 `VARIANTS` 또는 탐색 `EXPLORE_VARIANTS`).

    위원회 변형의 `top_n` 은 헌장이 덮기 전 값이다
    """
    v = VARIANTS.get(name) or EXPLORE_VARIANTS.get(name)
    if v is None:
        raise ValueError(f"모르는 변형 {name} — {sorted({*VARIANTS, *EXPLORE_VARIANTS})}")
    return v


#: 비중(자본 1)을 운영 함수가 먹는 원화 금액으로 바꾸는 명목 자본. 크게 잡아 정수 주 내림이 비중에 1e-6 아래로만
#: 남는다 — 운영의 정수 주·최소 주문 금액(작은 계좌에서 비싼 종목을 못 사는 일)은 재현하지 않는다(알고 둔 차이)
NOTIONAL_EQUITY = 1e12


@dataclass
class Lot:
    """전략 장부의 한 보유 — 운영 `pp.Position` 에서 매도 판정이 쓰는 칸만."""

    stock_id: int
    first_buy: str  # 첫 매수 체결일 = 그 결정일 t(엔진은 t 종가에 산다). 기간초과·고점의 시작
    horizon: str | None  # 첫 매수 신호의 기간 — 더 사도 바뀌지 않는다(운영 `positions`)
    unit_cost: float  # 주당 원가, 수수료(시장가는 슬리피지도) 포함 — 운영 `Position.cost / quantity`
    substitute_of: int | None = None  # 무작위 대조: 어느 신호 자리로 산 종목인가(나중 주문이 이긴다)
    last_buy: str = ""
    #: 첫 매수 주문의 신호 점수와 그 기준일 — 운영 `Position.score_at_buy`(첫 체결의 `score_at_trade`·`snapshot_as_of`).
    #: 사기 전 점검 계좌(A·C)의 점수 20점 하락(재무악화)이 견준다(25.1934)
    score_at_buy: float | None = None
    score_at_buy_date: str | None = None


@dataclass(frozen=True)
class _Bought:
    stock_id: int
    day: str
    weight: float  # 그날 자본 대비 더 산 비중
    horizon: str | None
    substitute_of: int | None
    #: 회차 지정가 모드(25.1914 전략 깔때기 ④ 분할 지정가 체결)에서 낸 회차 ((비중, 지정가), …). 비면 t 종가 전량
    tranches: tuple[tuple[float, float], ...] = ()
    #: 첫 주문의 신호 점수·기준일(운영 주문 `score`·`score_as_of`) — 새 로트의 `score_at_buy`(25.1934)
    score: float | None = None
    score_as_of: str | None = None


@dataclass
class DayLog:
    """하루 판단 기록 — 시험·1d 저장이 읽는다."""

    day: str
    cutoff: str
    sells: dict[int, list[str]] = field(default_factory=dict)  # 오늘 낸 매도와 사유(정지로 미룬 것은 ["미룬 매도"])
    buys: dict[int, float] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)  # 운영 결정 원장 모양(buy·skip·blocked)
    cooldown: frozenset[int] = frozenset()
    day_return_pct: float | None = None
    random: dict[str, Any] = field(default_factory=dict)  # 무작위 대체 기록(운영 `기록["random"]`)
    #: 판단 직전 든 종목(cutoff 비중 > 0) — 결정 단위 판정이 추가 매수를 뺀다(25.1544)
    held: frozenset[int] = frozenset()
    #: 오늘 매도 판정(`evaluate`)에 "목표도달" 이 선 보유 → 그 보유의 기간. 계좌 규칙과 무관하게 적는다 —
    #: 같은 로트 청산 대조(`exit_pair`, 25.1552)가 "목표에서 팔았다면" 의 날로 쓴다
    target_hit: dict[int, str | None] = field(default_factory=dict)
    #: 하루 수익을 잰 비중의 합(직전 cutoff 투자 비중) — 하루 손실 관문 빈도(`loss_gate`, 25.1552)의 시장 조정
    invested: float | None = None
    #: 회차 지정가 모드에서 낸 회차 {종목: ((비중, 지정가), …)} — 25.1914 (전략 깔때기 ④ 분할 지정가 체결). `buys` 는
    #: 이때도 **낸** 비중(회차 합)이다 — 찬 몫은 엔진이 정하고 다음 날 장부(`lots`)·엔진 비중에 든다
    limits: dict[int, tuple[tuple[float, float], ...]] = field(default_factory=dict)


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def dicts(self) -> list[dict[str, Any]]:
        # 줄이 있는데 dict 로 읽는 운영 읽기는 아직 없다 — 생기면 조용히 빈 값을 주지 않고 깨진다(25.1533)
        if self.rows:
            raise LookupError("핵심 H 백테스트 창은 dict 읽기를 모른다 — 운영 함수가 바뀌었다")
        return []


#: `jobs/monitor_targets.action_factors` 의 국내 읽기 머리 — 수정주가 창에는 기업행위 점프가 없다고 보고 빈 답을 준다
_ACTION_SQL_HEAD = "SELECT close, change_pct, date FROM prices WHERE stock_id = ?"


class _ViewClient:
    """운영 `jobs/paper` 함수가 DB 대신 읽는 창 — cutoff 로 자른 `PriceView` 만 답한다(미래를 볼 것이 없다)."""

    def __init__(self, view: bt.PriceView) -> None:
        from batch.jobs import paper as jp

        self.view, self._last, self._peak = view, jp.LAST_CLOSE_SQL, jp.PEAK_SQL
        #: 모르는 읽기 — 운영 `frozen_ids` 처럼 예외를 삼키는 함수 안에서 나면 "멈춤" 으로 조용히 바뀐다. 적어 두고
        #: 그날 판단이 끝나면 다시 던진다(25.1533, 25.1532 반박 검증)
        self.misses: list[str] = []

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> _Rows:
        if sql == self._last:
            sid, upto = int(params[0]), str(params[1])
            앞 = [(c, d) for d, c in self.view.closes(sid) if d <= upto and c > 0]
            return _Rows([앞[-1]] if 앞 else [])
        if sql == self._peak:
            sid, 부터, 까지 = int(params[0]), str(params[1]), str(params[2])
            값 = [c for d, c in self.view.closes(sid) if 부터 <= d <= 까지 and c > 0]
            return _Rows([(max(값) if 값 else None,)])
        if sql.startswith(_ACTION_SQL_HEAD):
            return _Rows([])
        self.misses.append(sql[:80])
        raise LookupError(f"핵심 H 백테스트 창이 모르는 읽기 — 운영 함수가 바뀌었다: {sql[:80]}")


class _DictRows(_Rows):
    """열 이름이 있는 답 — 운영 `enrich` 가 `.dicts()`·`.scalar()` 로 읽는 것."""

    def __init__(self, dicts: list[dict[str, Any]]) -> None:
        super().__init__([tuple(d.values()) for d in dicts])
        self._dicts = dicts

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self._dicts]

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows and self.rows[0] else None


#: 운영 `jobs/sell_flags.enrich` 의 읽기 머리 — 사기 전 점검 계좌(A·C)의 시점 창(`_PitClient`)이 답한다(25.1934).
#: 글이 바뀌면 여기서 모르는 읽기가 되어 멈춘다(`_ViewClient` 의 원칙)
_FIN_SQL_HEAD = ("SELECT fiscal_year, operating_income, report_date, currency, period_days FROM financials"
                 " WHERE stock_id = ?")  # fmt: skip
_UNI_SQL_HEAD = "SELECT u.included, u.exclude_reason, u.snapshot_date FROM universe_members u WHERE u.stock_id = ?"
#: 운영 글은 "… AND snapshot_date >= COALESCE(" 로 이어진다 — 괄호가 열린 조각이면 SQL 그물(`test_sql_schema`)이 못 보는
#: 질의로 세어(25.1935) 완결된 앞머리까지만 맞춘다(같은 표에서 MAX(included) 를 읽는 운영 질의는 이것 하나)
_UNI_SINCE_SQL_HEAD = "SELECT MAX(included) FROM universe_members WHERE stock_id = ?"
_SENTI_SQL_HEAD = "SELECT sentiment, delta_7d, negative_count_7d, as_of_date FROM sentiment_scores WHERE stock_id = ?"
_SCORE_ROW_SQL_HEAD = ("SELECT weights_json, calc_version, sentiment_weight_used, total_score, factor_scores,"
                       " sentiment_score FROM scores WHERE stock_id = ? AND as_of_date = ?")  # fmt: skip
_RIGHTS_SQL_HEAD = "SELECT e.record_date FROM kr_corp_events e JOIN stocks s ON s.ticker = e.code"
#: 시점 유니버스(`CoreMarket.members`)에서 빠진 종목의 제외 사유 — 운영 `universe_members.exclude_reason` 자리.
#: 시점 판정(`pit_universe.members_at`)은 종목별 사유를 넘기지 않아 이 이름 하나로 적는다(점검 판정에는 "빠졌다" 만
#: 쓰인다 — 사유가 결격(관리종목·거래정지 …)인지 가르는 것은 처음부터 유니버스 밖이던 보유뿐인데, 리플레이는
#: 유니버스 안에서만 산다)
PIT_EXCLUDED_REASON = "시점 유니버스 밖"


class _PitClient(_ViewClient):
    """사기 전 점검 계좌(A 가치·C 모멘텀)의 시점 창 — 25.1934 (전략 깔때기 ⑤).

    운영 `prebuy_sell_flags`·`sell_flags.enrich` 를 **그대로** 부르고, 그 읽기에 cutoff 까지의 자료로만 답한다:
    - 재무(영업이익 적자 전환): 재무 스냅샷을 접수일 ≤ cutoff 로 자른 연간 두 해(`cb.pit_fin_rows`)
    - 지금 점수(`SCORE_NOW_SQL`)·점수 행: 그 주 종합 점수(`week_totals`, 날짜 = cutoff) · 매수일 점수 = 로트가 든
      첫 주문 점수. 잣대(가중·판·감성 가중)는 리플레이 안에서 늘 같다 — 잣대 바뀜 갈래(`CHAIN_HISTORY_SQL` 등)는
      읽지 않는다(읽으면 멈춤)
    - 유니버스(점검): 그 주 첫 cutoff 의 시점 유니버스(`members(score_cutoff)` — 운영 주간 스냅샷 자리). 유니버스를
      자르지 않는 실행(`eligible_at` 없음)은 스냅샷이 없는 것처럼 빈 답(점검 없음)
    - **감성(감성급락)은 늘 빈 답** — 무료 과거 뉴스·감성 이력이 없다(영구 차이, docs/backtest.md 10장).
      지어내지 않는다
    - 유상증자 권리락 일정: 빈 답(수정주가 창 — `_ACTION_SQL_HEAD` 와 같은 판단)
    """

    def __init__(
        self, view: bt.PriceView, *, snapshots: dict[int, list[dict[str, Any]]], totals: dict[int, float | None],
        score_book: dict[tuple[int, str], float | None], members: Any, score_cutoff: str, snap_days: list[str],
        weights_json: str,
    ) -> None:  # fmt: skip
        super().__init__(view)
        self.snapshots, self.totals, self.score_book = snapshots, totals, score_book
        self.members, self.score_cutoff, self.snap_days = members, score_cutoff, snap_days
        self.weights_json = weights_json

    def _score(self, sid: int, day: str | None) -> float | None:
        if day is None:
            return None
        if day == self.view.cutoff:
            return self.totals.get(sid)
        return self.score_book.get((sid, day))

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> _Rows:  # noqa: C901 — 읽기마다 한 갈래
        from batch.jobs import paper as jp

        if sql == jp.SCORE_NOW_SQL:
            값 = self.totals.get(int(params[0]))
            return _Rows([] if 값 is None else [(값, self.view.cutoff)])
        if sql.startswith(_FIN_SQL_HEAD):
            return _DictRows(cb.pit_fin_rows(self.snapshots.get(int(params[0]), []), self.view.cutoff))
        if sql.startswith(_UNI_SQL_HEAD):
            든 = self.members(self.score_cutoff)
            if 든 is None:
                return _DictRows([])
            sid = int(params[0])
            return _DictRows([{"included": int(sid in 든), "exclude_reason": None if sid in 든 else PIT_EXCLUDED_REASON,
                               "snapshot_date": self.score_cutoff}])  # fmt: skip
        if sql.startswith(_UNI_SINCE_SQL_HEAD):
            # 매수일 무렵 스냅샷(그 날 이하 가장 늦은 것)부터 지금까지 한 번이라도 들었나(25.1095)
            sid, 매수일 = int(params[0]), str(params[2])
            앞 = [d for d in self.snap_days if d <= 매수일]
            부터 = 앞[-1] if 앞 else ""
            들었나 = [sid in (self.members(d) or set()) for d in self.snap_days if d >= 부터]
            return _DictRows([{"m": (int(any(들었나)) if 들었나 else None)}])
        if sql.startswith(_SENTI_SQL_HEAD) or sql.startswith(_RIGHTS_SQL_HEAD):
            return _DictRows([])
        if sql.startswith(_SCORE_ROW_SQL_HEAD):
            값 = self._score(int(params[0]), None if params[1] is None else str(params[1]))
            if 값 is None:
                return _DictRows([])
            return _DictRows([{"weights_json": self.weights_json, "calc_version": 0, "sentiment_weight_used": 0.0,
                               "total_score": 값, "factor_scores": None, "sentiment_score": None}])  # fmt: skip
        return super().execute(sql, params)


def _close_on(view: bt.PriceView, sid: int, day: str) -> float | None:
    """그날 종가(없으면 None — 그날 거래되지 않았다)."""
    rows = view.closes(sid)
    k = bisect.bisect_left(rows, (day, -math.inf))
    return rows[k][1] if k < len(rows) and rows[k][0] == day else None


def _close_upto(view: bt.PriceView, sid: int, day: str) -> float | None:
    rows = view.closes(sid)
    k = bisect.bisect_right(rows, (day, math.inf))
    return rows[k - 1][1] if k else None


def _substitute_of(order: dict[str, Any]) -> int | None:
    """운영 `held_substitutes` 가 주문 근거에서 읽는 `substitute_of`."""
    for c in (order.get("rationale") or {}).get("signal", {}).get("criteria") or []:
        if isinstance(c, dict) and c.get("substitute_of") is not None:
            return int(c["substitute_of"])
    return None


def holding_skips(dv: cb.DayView, top_n: int, held: set[int]) -> list[dict[str, Any]]:
    """지주사가 추천받았지만 고르지 않은 종목의 skip 결정 — 운영 `jobs.paper.holding_step` 과 같은 사유·같은 꼴.

    예전엔 `buy_orders` 의 결정(고른 종목의 buy·skip)만 넣어, 관문 잣대(`selection_evidence`)의 "안 산 새 후보" 풀이
    운영 H 와 달리 거의 비었다 — 운영은 이 줄을 원장에 적어 그 풀로 센다(25.1634, 핵심 H 감사 [필수]). 신호 행이 있는
    종목만(운영 `sid not in 행들` 건너뜀과 같다)
    """
    행 = {}
    for r in dv.rows:
        행.setdefault(r.stock_id, r)
    고른 = set(dv.picks)
    out: list[dict[str, Any]] = []
    for x in dv.ranked:
        sid = x["stock_id"]
        if sid in 고른 or sid not in 행:
            continue
        r0 = 행[sid]
        out.append({"stock_id": sid, "kind": "skip",
                    "reason": "지주사: 추천 회사 1곳" if not x["eligible"] else f"지주사: 점수 순위 밖(상위 {top_n})",
                    "ref_close": r0.close, "ref_date": r0.close_date,
                    "detail": {"horizon": r0.horizon, "score": r0.total_score, "held": sid in held}})  # fmt: skip
    return out


def _meet(dv: cb.DayView, charter: dict[str, Any]) -> tuple[list[co.Item], list[co.Item]]:
    """그 회사의 회의 — 운영 `committee_step` 처럼 하루 안건의 앞 `agenda` 개와 그날 모집단으로.

    `DayView` 가 표를 낼 때와 같은 호출이다
    """
    return co.meet(dv.agenda[: int(charter["agenda"])], charter, dv.population)


def committee_rows(dv: cb.DayView, account: str) -> list[report_picks.SignalRow]:
    """위원회 회사가 가결한 종목의 신호 행 — 표 순, 한 종목 안에서는 기간 순(운영 `committee_step` 의 `가결행`).

    `DayView.approved` 는 시장 판단기가 H 의 표를 낼 때 같은 `co.meet` 로 이미 냈다 — 다시 열지 않는다
    """
    행들: dict[int, list[report_picks.SignalRow]] = {}
    for r in report_picks.with_criteria(dv.rows):
        행들.setdefault(r.stock_id, []).append(r)
    순서 = report_picks._기간_순
    return [r for i in dv.approved.get(account, []) for r in sorted(행들.get(i.stock_id, []),
                                                                    key=lambda r: 순서.get(r.horizon, 9))]  # fmt: skip


def committee_skips(dv: cb.DayView, charter: dict[str, Any], held: set[int]) -> list[dict[str, Any]]:
    """위원회가 부결했거나 가결이나 표 순 밖인 안건의 skip 결정 — 운영 `committee_step` 과 같은 사유.

    결정 단위 잣대(`selection_evidence`)의 "안 산 새 후보" 풀이 운영 회사와 같게(`holding_skips` 와 같은 까닭, 25.1634).
    신호 행이 있는 종목만
    """
    items, approved = _meet(dv, charter)
    가결 = {i.stock_id for i in approved}
    행: dict[int, report_picks.SignalRow] = {}
    for r in report_picks.with_criteria(dv.rows):
        행.setdefault(r.stock_id, r)
    out: list[dict[str, Any]] = []
    for i in items:
        if i.stock_id in 가결 or i.stock_id not in 행:
            continue
        r0 = 행[i.stock_id]
        까닭 = i.reason if not i.passed else f"위원회: 가결이나 표 순 상위 {int(charter['top_n'])} 밖"
        out.append({"stock_id": i.stock_id, "kind": "skip", "reason": 까닭, "ref_close": r0.close,
                    "ref_date": r0.close_date,
                    "detail": {"horizon": r0.horizon, "score": r0.total_score,
                               "held": i.stock_id in held}})  # fmt: skip
    return out


class CoreStrategy:
    """변형 하나의 장부형 판단 함수(`bt.WeightsAt`). 시장 판단기(`CoreMarket`)는 변형마다 따로 —
    cutoff 가 순차여야 한다.

    `sessions` = 거래일(엔진 `dates`, 워밍업 포함 가능) — 판 뒤 쉼을 거래일로 센다. 결정은 **날마다**(리밸런스 날 =
    모든 거래일)를 전제한다: 산 날 원가·더 산 몫의 평균은 "직전 결정일 = 지금 cutoff" 로 맞춘다
    """

    def __init__(
        self, variant: str, market: cb.CoreMarket, *, sessions: list[str], costs: bt.Costs | None = None,
        buy_fee_pct: float | None = None,
        max_sector_pct: float = report_picks.DEFAULT_MAX_SECTOR_PCT,
        limit_fills: bool = False,
    ) -> None:  # fmt: skip
        from batch.jobs import paper as jp

        v = variant_spec(variant)
        self.charter: dict[str, Any] | None = None
        if v.selection == "committee":
            if v.account not in market.charters:
                raise ValueError(f"변형 {variant} 의 회사 {v.account} 헌장이 시장 판단기에 없다 — voters 에 넣는다")
            self.charter = market.charters[v.account]
            # 운영 run_step: 위원회 회사의 상위 몇 종목은 헌장 `top_n`(`종목수 = int(헌장들[aid]["top_n"])`)
            v = replace(v, top_n=int(self.charter["top_n"]))
        self.variant, self.v = variant, v
        #: 회차 지정가 체결 — 25.1914 (전략 깔때기 ④ 분할 지정가 체결). 회차 진입(tranche) 변형만 — 시가 일괄(R1·R2)은
        #: 회차가 없어 켜도 예전과 같다. 끄면(기본) t 종가 전량
        self.limit_fills = bool(limit_fills) and v.entry == "tranche"
        #: 사기 전 점검 계좌(A 가치·C 모멘텀 — 운영 `PREBUY_CHECK_RULES`). 매도 판정은 운영 `sell_decisions` 처럼
        #: `sell_flags.enrich` 보강을 거치고(재무악화·점검), 매수 전에 운영 `prebuy_sell_flags` 를 부른다 — 읽기는
        #: 시점 창 `_PitClient`(25.1934). 그 밖의 계좌(core·trend·time)는 보유와 무관한 플래그를 따르지 않아 예전 길
        #: 그대로다
        self.prebuy = self.v.sell_rule in jp.PREBUY_CHECK_RULES
        self._snap_days: list[str] = []  # 지나온 주간 점수 cutoff = 시점 유니버스 "스냅샷" 날(점검의 매수 뒤 편입 이력)
        self.market = market
        self.sessions = sorted(sessions)
        c = costs if costs is not None else bt.Costs.for_country(market.country)
        # 운영 `make_fill`: 지정가 매수는 슬리피지 없이 수수료만, 시장가(시가 일괄)는 슬리피지도 원가에.
        # 수수료는 **매수 쪽** — 운영은 25.1620 부터 매수·매도 수수료를 따로 붙인다(`jobs/paper._수수료`). 엔진의
        # `commission_pct` 는 편도 평균이라 설정이 쪽마다 다르면 원가가 운영과 갈렸다(25.1625, 25.1621 [권고]).
        # 안 주면 예전처럼 편도 평균(설정이 비면 둘이 같다)
        매수 = c.commission_pct if buy_fee_pct is None else float(buy_fee_pct)
        self.unit_factor = 1 + 매수 / 100 + (c.slippage_pct / 100 if self.v.entry == "open" else 0.0)
        self.settings = {"max_weight_per_stock": float(market.max_weight_per_stock),
                         "max_weight_per_sector": float(max_sector_pct), "min_order_amount": 0.0}  # fmt: skip
        self._cache = {
            int(r["stock_id"]): {
                "id": int(r["stock_id"]), "ticker": str(r["ticker"]), "name": str(r.get("name") or r["ticker"]),
                "sector": r.get("sector") or None, "country": market.country, "currency": r.get("currency") or "KRW",
                "asset_type": r.get("asset_type") or "stock",
            }
            for r in market.universe
        }  # fmt: skip
        self.lots: dict[int, Lot] = {}
        self.sold: dict[int, str] = {}  # 종목 → 마지막 매도 체결일(엔진이 실제로 판 결정일)
        self._selling: dict[int, str] = {}  # 낸 매도 가운데 아직 안 팔린 것(정지) → 처음 낸 날
        self._bought: list[_Bought] = []
        self._prev: tuple[str, str, dict[int, float]] | None = None  # (결정일, cutoff, cutoff 비중)
        self.log: list[DayLog] = []

    def _client(self, view: bt.PriceView, dv: cb.DayView) -> _ViewClient:
        """그날 운영 함수가 읽을 창 — 사기 전 점검 계좌면 시점 자료까지 답하는 `_PitClient`(25.1934)."""
        if not self.prebuy:
            return _ViewClient(view)
        if not self._snap_days or self._snap_days[-1] != dv.score_cutoff:
            self._snap_days.append(dv.score_cutoff)
        무게 = getattr(self.market, "factor_weights", cb.EQUAL_WEIGHTS)
        return _PitClient(
            view, snapshots=self.market.snapshots, totals=self.market.week_totals(dv.score_cutoff),
            score_book={(lot.stock_id, lot.score_at_buy_date): lot.score_at_buy for lot in self.lots.values()
                        if lot.score_at_buy_date},
            members=self.market.members, score_cutoff=dv.score_cutoff, snap_days=list(self._snap_days),
            weights_json=json.dumps(무게, sort_keys=True),
        )  # fmt: skip

    # ── 장부 맞추기 ──────────────────────────────────────────────────────────

    def _reconcile(self, view: bt.PriceView, w: dict[int, float]) -> None:
        held = set(view.holding) if view.holding is not None else set(w)
        직전 = self._prev[0] if self._prev else None
        for sid in list(self._selling):
            if sid not in held:
                # 어제 다시 낸 매도가 찼다 — 판 날은 그 결정일(운영 체결일)
                self.sold[sid] = 직전 or self._selling[sid]
                self.lots.pop(sid, None)
                del self._selling[sid]
        for sid in list(self.lots):
            if sid not in held:
                del self.lots[sid]  # 엔진이 판 것(상장폐지 — 마지막 종가)
        찬회차 = self._filled_tranches(view, held)
        배율, 남음 = 1.0, 1.0
        if 찬회차:
            # 회차 지정가(25.1914 전략 깔때기 ④ 분할 지정가 체결) — 엔진은 회차 비중을 cutoff 자본 대비 정해진 수량으로
            # 사고(`bt.fill_limits` 의 scale = cutoff 자본 ÷ 산 날 자본 = 1 ÷ (1 + 그 사이 수익)), 지정가 − 종가 만큼
            # 자본이 준다(`남음`). 엔진의 회전 비용은 든 것·현금을 비례로 줄여 비중 비율에서 지워진다 — 새 몫이 정확하다
            r = self._day_return(view)
            배율 = 1.0 / (1.0 + r / 100) if r is not None else 1.0
            남음 = 1.0 - sum(x * 배율 * (1 - px / 가격) for px, 찬 in 찬회차.values() for x, 가격 in 찬)
        for i, b in enumerate(self._bought):
            px = _close_on(view, b.stock_id, b.day)
            if px is None or px <= 0 or b.stock_id not in held:
                continue  # 엔진이 못 산 것(그날 체결가 없음·정지·상장폐지)
            단가, 새 = px * self.unit_factor, b.weight
            if b.tranches:
                # 회차 지정가(25.1914 전략 깔때기 ④ 분할 지정가 체결) — 엔진과 같은 잣대(그날 종가 ≤ 지정가)로 찬 회차만
                # 원가는 찬 회차의 지정가를 주식 수로 평균(운영 `make_fill` — 지정가 매수는 수수료만), 새 몫은 종가 가치
                if i not in 찬회차:
                    continue  # 하나도 안 찼다 — 회차는 하루짜리라 사라진다(든 로트는 그대로, 마지막 매수일도 그대로)
                _px, 찬 = 찬회차[i]
                단가 = sum(x for x, _ in 찬) / sum(x / 가격 for x, 가격 in 찬) * self.unit_factor
                새 = sum(x * 배율 * px / 가격 for x, 가격 in 찬) / 남음
            lot = self.lots.get(b.stock_id)
            if lot is None:
                self.lots[b.stock_id] = Lot(b.stock_id, b.day, b.horizon, 단가, b.substitute_of, b.day,
                                            score_at_buy=b.score, score_at_buy_date=b.score_as_of)  # fmt: skip
                continue
            # 더 산 몫 — 주식 수 비율은 그날 종가로 잰 비중(cutoff = 산 날)에서: 옛 몫 (h − w)/h, 새 몫 w/h
            h = w.get(b.stock_id, 0.0)
            옛 = max(h - 새, 0.0) / h if h > 0 else 0.0
            lot.unit_cost = 옛 * lot.unit_cost + (1 - 옛) * 단가
            lot.last_buy = b.day
            if b.substitute_of is not None:
                lot.substitute_of = b.substitute_of
        self._bought = []

    def _filled_tranches(
        self, view: bt.PriceView, held: set[int]
    ) -> dict[int, tuple[float, list[tuple[float, float]]]]:
        """어제 낸 회차 가운데 엔진이 채운 것 — 25.1914 (전략 깔때기 ④ 분할 지정가 체결). {`_bought` 번호: (산 날 종가,
        찬 회차)}. 잣대는 엔진(`bt.fill_limits`)·운영(`pp.judge`)과 같다 — 산 날 종가 ≤ 지정가. 엔진이 들지 않으면(정지·
        상장폐지·하나도 안 참) 넣지 않는다"""
        out: dict[int, tuple[float, list[tuple[float, float]]]] = {}
        for i, b in enumerate(self._bought):
            px = _close_on(view, b.stock_id, b.day) if b.tranches else None
            if px is None or px <= 0 or b.stock_id not in held:
                continue
            찬 = [(x, 가격) for x, 가격 in b.tranches if px <= 가격 and x > 0]
            if 찬:
                out[i] = (px, 찬)
        return out

    def _cooldown_start(self, cutoff: str) -> str:
        """운영 `쉼_시작` — 리포트 거래일(= cutoff)에서 `REBUY_COOLDOWN_SESSIONS − 1` 거래일 앞."""
        k = bisect.bisect_right(self.sessions, cutoff) - 1
        if k < 0:
            return cutoff
        return self.sessions[max(k - (pp.REBUY_COOLDOWN_SESSIONS - 1), 0)]

    def _day_return(self, view: bt.PriceView) -> float | None:
        """사전 위험 점검의 하루 수익(%) — 직전 cutoff 비중 × 그 뒤 종가 변화. 첫날은 None(운영 첫 기록과 같다)."""
        if self._prev is None:
            return None
        _d, 앞cut, 앞w = self._prev
        r = 0.0
        for sid, x in 앞w.items():
            a, b = _close_upto(view, sid, 앞cut), view.last_close(sid)
            if a and b:
                r += x * (b / a - 1)
        return r * 100

    # ── 매도 ─────────────────────────────────────────────────────────────────

    def _sell_codes(
        self, client: _ViewClient, view: bt.PriceView, lot: Lot, h: float, today: date,
    ) -> set[str]:  # fmt: skip
        """운영 `sell_decisions` 한 종목 — 같은 `evaluate`, 추세 계좌는 같은 `_추적손절`(고점은 창에서)."""
        from batch.jobs import paper as jp

        px = view.last_close(lot.stock_id)
        if px is None or px <= 0 or h <= 0:
            return set()
        수량 = h * NOTIONAL_EQUITY / px
        행 = {"quantity": 수량, "market_value": px * 수량, "cost": lot.unit_cost * 수량,
             "first_buy_date": lot.first_buy}  # fmt: skip
        hi = sf.HoldingInput(
            stock_id=lot.stock_id, name=self._cache.get(lot.stock_id, {}).get("name", str(lot.stock_id)),
            horizon=lot.horizon, first_buy_date=lot.first_buy, currency="KRW", cost=행["cost"],
            market_value=행["market_value"], price_date=view.last_date(lot.stock_id), horizon_since=lot.first_buy,
        )  # fmt: skip
        if self.prebuy:
            hi = self._enrich(client, lot, 행, view, today)
            if hi is None:
                return set()
        코드들 = {f.reason_code for f in sf.evaluate(hi, today, self.market.targets)}
        if self.v.sell_rule == "trend" and jp._추적손절(client, hi, 행, self.market.targets, today):
            코드들.add(pp.TRAIL_CODE)
        return 코드들

    def _enrich(
        self, client: _ViewClient, lot: Lot, 행: dict[str, Any], view: bt.PriceView, today: date,
    ) -> sf.HoldingInput | None:  # fmt: skip
        """사기 전 점검 계좌의 보유 하나 — 운영 `sell_decisions` 와 같은 행으로 같은 `sell_flags.enrich` 보강(25.1934).

        재무(적자 전환)·지금 점수·유니버스 제외는 `_PitClient` 가 cutoff 까지의 자료로 답한다. 감성은 늘 빈 답"""
        from batch.jobs import paper as jp
        from batch.jobs import sell_flags as sf_job

        점수 = client.execute(jp.SCORE_NOW_SQL, [lot.stock_id]).rows
        s = self._cache.get(lot.stock_id, {})
        보유행 = {
            "stock_id": lot.stock_id, "name": s.get("name", str(lot.stock_id)), "country": self.market.country,
            "horizon": lot.horizon, "first_buy_date": lot.first_buy, "currency": s.get("currency") or "KRW",
            "cost": 행["cost"], "market_value": 행["market_value"], "price_date": view.last_date(lot.stock_id),
            "score_now": 점수[0][0] if 점수 else None, "score_now_date": 점수[0][1] if 점수 else None,
            "score_at_buy": lot.score_at_buy, "score_at_buy_date": lot.score_at_buy_date,
            "horizon_since": lot.first_buy, "lots": [(lot.first_buy, 행["quantity"], lot.unit_cost)],
            "quantity": 행["quantity"],
        }  # fmt: skip
        입력, _경고 = sf_job.enrich(client, [보유행], today, dividends=False)
        return 입력[0] if 입력 else None

    # ── 하루 ─────────────────────────────────────────────────────────────────

    def __call__(self, day: str, view: bt.PriceView) -> bt.LedgerOrders:
        if not view.stocks():
            # 엔진 첫날(워밍업 없음)의 빈 창 — 판단기를 부르지 않는다(다음 날 같은 cutoff 로 다시 부르게 된다)
            return bt.LedgerOrders()
        w = {int(s): float(x) for s, x in (view.weights or {}).items() if x > 0}
        self._reconcile(view, w)
        dv = self.market.at(view, day)
        client = self._client(view, dv)
        today = date.fromisoformat(day[:10])
        log = DayLog(day=day, cutoff=view.cutoff, day_return_pct=self._day_return(view), held=frozenset(w),
                     invested=sum(self._prev[2].values()) if self._prev else None)  # fmt: skip

        # ① 매도 — 계좌 규칙 전의 판정에서 손절을 따로 모은다(운영 검증 [필수] 4)
        매도: dict[int, list[str]] = {}
        손절: set[int] = set()
        for sid, lot in sorted(self.lots.items()):
            코드들 = self._sell_codes(client, view, lot, w.get(sid, 0.0), today)
            if "손절" in 코드들:
                손절.add(sid)
            if "목표도달" in 코드들:
                log.target_hit[sid] = lot.horizon
            팔것 = pp.sells_for(self.v.sell_rule, {sid: 코드들})
            if 팔것:
                매도[sid] = 팔것[sid]
        for sid in self._selling:
            매도.setdefault(sid, ["미룬 매도"])  # 정지로 못 판 매도 — 운영은 열린 주문이 다음 장으로 밀린다
        log.sells = 매도

        # ② 판 뒤 쉼 — 운영 `최근판`: 쉼 시작 뒤 체결된 매도 + 오늘 매도
        쉼 = self._cooldown_start(view.cutoff)
        최근판 = {sid for sid, d in self.sold.items() if d >= 쉼} | set(매도)
        log.cooldown = frozenset(최근판)

        # ③ 매수 — 운영 `buy_orders` 그대로(현금은 오늘 판 돈을 넣지 않은 cutoff 현금)
        현금 = max(1.0 - sum(w.values()), 0.0)
        buys, bought = self._buys(client, view, dv, w, 현금, 손절, 최근판, today, log)
        # 회차 지정가 모드(25.1914 전략 깔때기 ④ 분할 지정가 체결) — 회차가 있는 종목은 엔진에 회차로 넘긴다
        회차 = {b.stock_id: b.tranches for b in bought if b.tranches}

        if client.misses:
            # 운영 함수가 삼킨 모르는 읽기 — 백테스트가 운영과 다른 판단을 조용히 쓰지 않게 여기서 깨진다
            raise LookupError(f"핵심 H 백테스트 창이 모르는 읽기({len(client.misses)}건, 삼켜짐): {client.misses[0]}")
        for sid in 매도:
            self._selling.setdefault(sid, day)
        self._bought = bought
        self._prev = (day, view.cutoff, w)
        log.buys = buys
        log.limits = 회차
        self.log.append(log)
        if 회차:
            return bt.LedgerOrders(sells=frozenset(매도), buys={sid: x for sid, x in buys.items() if sid not in 회차},
                                   limits=회차)  # fmt: skip
        return bt.LedgerOrders(sells=frozenset(매도), buys=buys)

    def _rows(self, dv: cb.DayView) -> tuple[list[report_picks.SignalRow], list[report_picks.SignalRow] | None]:
        """(신호 행, 미리 고른 행) — H 는 지주사 합의(`pick_rows`), 나머지는 `buy_orders` 가 점수 상위를 고른다."""
        if self.v.selection == "weighted":
            return dv.rows, list(dv.pick_rows)
        if self.v.selection == "committee":
            return dv.rows, committee_rows(dv, self.v.account)
        return dv.rows, None

    def _prebuy(
        self, client: _ViewClient, dv: cb.DayView, rows: list[report_picks.SignalRow],
        미리: list[report_picks.SignalRow] | None, w: dict[int, float], today: date, log: DayLog,
    ) -> tuple[list[report_picks.SignalRow], list[report_picks.SignalRow] | None]:  # fmt: skip
        """사면 바로 팔 후보는 사지 않는다 — 운영 `run_step` 의 `prebuy_sell_flags` 자리 그대로(25.1480·25.1934).

        후보 = 미리 고른 행(위원회 가결)이 있으면 그것, 없으면 신호 행. 걸린 종목은 두 목록에서 빼고(위원회 계좌는
        상위 N 을 자른 뒤라 자리가 현금으로 남는다, 25.1588) 결정 원장에 운영과 같은 skip 줄. 든 종목은 운영처럼 보지
        않는다(오늘 매도 전 보유), 기업행위 멈춤은 리플레이에 없다(빈 집합)"""
        from batch.jobs import paper as jp

        든 = {sid for sid, x in w.items() if x > 0}
        즉시 = jp.prebuy_sell_flags(client, self.market.country, self.v.sell_rule, 미리 if 미리 is not None else rows,
                                  든, set(), self._cache, self.market.targets, today, {})  # fmt: skip
        if not 즉시:
            return rows, 미리
        for sid, 사유 in sorted(즉시.items()):
            r0 = next(r for r in dv.rows if r.stock_id == sid)
            log.decisions.append({"stock_id": sid, "kind": "skip",
                                  "reason": "사면 바로 매도 규칙에 걸림: " + ",".join(사유),
                                  "ref_close": r0.close, "ref_date": r0.close_date,
                                  "detail": {"flags": 사유, "sell_rule": self.v.sell_rule, "horizon": r0.horizon,
                                             "score": r0.total_score}})  # fmt: skip
        return ([r for r in rows if r.stock_id not in 즉시],
                None if 미리 is None else [r for r in 미리 if r.stock_id not in 즉시])

    def _buys(
        self, client: _ViewClient, view: bt.PriceView, dv: cb.DayView, w: dict[int, float], 현금: float,
        손절: set[int], 최근판: set[int], today: date, log: DayLog,
    ) -> tuple[dict[int, float], list[_Bought]]:  # fmt: skip
        from batch.jobs import paper as jp

        rows, 미리 = self._rows(dv)
        if not rows:
            return {}, []
        if self.prebuy:
            rows, 미리 = self._prebuy(client, dv, rows, 미리, w, today, log)
        N = NOTIONAL_EQUITY
        v = pp.Valuation(
            invested_krw=N, cash_krw=현금 * N, holdings_krw=sum(w.values()) * N, equity_krw=N, pnl_krw=0.0, pnl_pct=0.0,
            rows=[{"stock_id": sid, "value_krw": x * N} for sid, x in sorted(w.items())],
        )  # fmt: skip
        바꾸기 = None
        if self.v.selection == "random":
            pool = [int(r["stock_id"]) for r in self.market.pool(view.cutoff)
                    if (r.get("asset_type") or "stock") == "stock"]  # fmt: skip
            # 운영 `held_substitutes` — 지금 든 대체 종목, 나중에 산 것이 이긴다
            든대체 = {lot.substitute_of: sid for sid, lot in sorted(self.lots.items(), key=lambda kv: kv[1].last_buy)
                      if lot.substitute_of is not None and w.get(sid, 0.0) > 0}  # fmt: skip
            바꾸기 = partial(jp.random_substitute, client, account=self.v.account, market=self.market.country,
                            pool=pool, cooldown=최근판, stock_cache=self._cache, today=today, 기록=log.random,
                            held=든대체)  # fmt: skip
        주문, b기록 = jp.buy_orders(
            client, market=self.market.country, account=self.v.account, account_fills=[], valuation=v, open_buys=[],
            signal_rows=rows, settings=self.settings, price_series={}, series_unknown={}, fx_rate=None,
            stop_ids=손절, sold_recently=최근판, stock_cache=self._cache, top_n=self.v.top_n, entry=self.v.entry,
            substitute=바꾸기, preselected=미리,
            preselected_reason="지주사 합의" if self.v.selection == "weighted" else "위원회 가결",
        )  # fmt: skip
        # ④ 사전 위험 점검 — 운영 `run_step` 과 같은 후보·같은 문맥(보유는 오늘 매도 전, 업종은 후보까지)
        후보 = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i, o["cash_krw"])
                for i, o in enumerate(주문)]  # fmt: skip
        sector_of = {sid: self._cache.get(sid, {}).get("sector") for sid in {*w, *(c.stock_id for c in 후보)}}
        문맥 = rg.Context(
            equity_krw=N, day_return_pct=log.day_return_pct, cash_available_krw=현금 * N,
            holding_krw={sid: x * N for sid, x in w.items()}, sector_of=sector_of,
            max_stock_pct=self.settings["max_weight_per_stock"], max_sector_pct=self.settings["max_weight_per_sector"],
            today=today.isoformat(), ref_floor=rg.ref_floor(self.market.country, today),  # 거래일 잣대(25.1629)
        )  # fmt: skip
        통과, 막힘 = rg.gate(후보, 문맥)
        주문 = [주문[c.key] for c in 통과]
        낸 = {o["stock_id"] for o in 주문}
        막힌까닭 = {c.stock_id: "위험 점검: " + "; ".join(t for _c, t in 까닭) for c, 까닭 in 막힘}
        for d in b기록.pop("_decisions", []):
            if d["kind"] == "buy" and d["stock_id"] not in 낸:
                d = {**d, "kind": "blocked", "reason": 막힌까닭.get(d["stock_id"], "위험 점검")}
            log.decisions.append(d)
        if self.v.selection == "weighted":
            log.decisions += holding_skips(dv, self.v.top_n, {sid for sid, x in w.items() if x > 0})
        if self.v.selection == "committee" and self.charter is not None:
            log.decisions += committee_skips(dv, self.charter, {sid for sid, x in w.items() if x > 0})
        buys: dict[int, float] = {}
        첫주문: dict[int, dict[str, Any]] = {}
        회차: dict[int, list[tuple[float, float]]] = {}
        for o in 주문:
            단가 = o["limit_price"] if o.get("limit_price") is not None else o["ref_price"]
            buys[o["stock_id"]] = buys.get(o["stock_id"], 0.0) + 단가 * o["quantity"] / N
            첫주문.setdefault(o["stock_id"], o)
            if self.limit_fills and o.get("limit_price") is not None:
                # 회차 하나 = 운영 주문 한 줄(`jobs/paper.run_step` 이 `paper_orders` 에 적는 지정가·수량) — 25.1914
                회차.setdefault(o["stock_id"], []).append((단가 * o["quantity"] / N, float(단가)))
        bought = [_Bought(sid, log.day, x, 첫주문[sid].get("horizon"), _substitute_of(첫주문[sid]),
                          tuple(회차.get(sid, ())), 첫주문[sid].get("score"), 첫주문[sid].get("score_as_of"))
                  for sid, x in sorted(buys.items())]  # fmt: skip
        return buys, bought


def make_core_strategy(
    variant: str, market: cb.CoreMarket, *, sessions: list[str], costs: bt.Costs | None = None,
    buy_fee_pct: float | None = None,
    max_sector_pct: float = report_picks.DEFAULT_MAX_SECTOR_PCT,
    limit_fills: bool = False,
) -> CoreStrategy:  # fmt: skip
    """변형 하나의 판단 함수 — `bt.simulate(prices, dates, dates, make_core_strategy(…), costs)` 로 돌린다.

    `costs` 는 엔진에 넘기는 것과 같은 값을 준다(원가의 수수료·슬리피지). 변형마다 새 `CoreMarket` 을 준다.
    `limit_fills` — 25.1914 (전략 깔때기 ④ 분할 지정가 체결): 회차 변형(B·G·H·D·E)의 매수를 t 종가 전량 대신 운영처럼
    회차 지정가로 낸다(기본 꺼짐 — 11-03 판정·`run_core` 기본은 그대로)"""
    return CoreStrategy(variant, market, sessions=sessions, costs=costs, max_sector_pct=max_sector_pct,
                        buy_fee_pct=buy_fee_pct, limit_fills=limit_fills)

