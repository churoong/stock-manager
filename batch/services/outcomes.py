"""신호 성적표 (docs/signals.md 10장). 지난 신호가 그 뒤 어떻게 됐는지를 저장된 종가로 잰다.

원칙
- 진입가 = 기준일 **다음** 거래일 종가. 기준일은 **데이터의 날짜**(직전 거래일)이고 리포트는
  그 다음 날 아침 장 시작 전에 온다 — 사람이 처음 살 수 있는 값은 **그날 종가**다
  (2026-09-23, docs/infra.md 25.154). 기준일 그날 종가는 **신호를 만든 값 자체**라 살 수 없다
- 창이 아직 안 찼으면 NULL. 앞날을 지어내지 않는다
- 목표·손절 터치는 **종가** 기준이다. 고가·저가는 국내는 있지만 미국 야후 일봉도 있고, 그러나 조정 전 값이라
  수정주가 계열과 섞이면 어긋난다. 보수적으로 종가만 본다
- 지수는 같은 날짜끼리 견준다. 그날 지수가 없으면 초과수익은 NULL
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from typing import Any

WINDOWS = (5, 20, 60)  # 거래일. 단기 1~3개월·중기 3~12개월과 나란히 읽을 수 있는 가장 짧은 눈금들
HORIZON_WINDOW = 60  # 목표·손절 터치와 최고·최저를 보는 창
#: 종목 시세가 지수보다 이만큼(달력일) 먼저 끝나면 "끊겼다" 로 본다 (25.696).
#: 연휴(설·추석 최장 약 5일)와 하루 이틀 늦은 적재를 넘는 값. 거래정지·상장폐지가 여기 걸린다 [확인필요: 운영에서
#: 실측한 적재 지연 최댓값]
CUTOFF_GAP_DAYS = 10
#: 지수 대비를 낼 최소 표본 비율 — 수익률 표본 n 의 95% 이상이 지수값을 가져야 한다 (25.706). 굳은 성적처럼 체계적으로
#: 빠지는 경우는 막고(5일 창 절반), 지수 하루 누락 같은 우연한 한두 건은 받아들인다 [확인필요: 되돌릴 수 있는 선택]
EXCESS_SAMPLE_SHARE = 0.95


@dataclass
class Outcome:
    stock_id: int
    as_of_date: str
    horizon: str
    entry_date: str | None = None
    entry_close: float | None = None
    rets: dict[int, float | None] = field(default_factory=lambda: dict.fromkeys(WINDOWS))
    max_up: float | None = None
    max_down: float | None = None
    hit_target: bool | None = None
    hit_stop: bool | None = None
    bench: dict[int, float | None] = field(default_factory=lambda: dict.fromkeys(WINDOWS))
    days_available: int = 0

    def excess(self, window: int) -> float | None:
        r, b = self.rets.get(window), self.bench.get(window)
        return None if r is None or b is None else r - b


def _ret(later: float | None, base: float) -> float | None:
    return None if later is None or base <= 0 else later / base - 1


def evaluate(
    signal: dict[str, Any],
    closes: list[tuple[str, float]],
    index_closes: list[tuple[str, float]] | None = None,
    raw_closes: dict[str, float] | None = None,
    market_last: str | None = None,
) -> Outcome:
    """신호 하나의 성적. closes 는 날짜 오름차순 (날짜, 종가), index_closes 도 같다.

    closes 는 **지금의** 수정주가 계열이다. raw_closes 는 같은 날짜의 원래 종가({날짜: 종가}).
    목표·손절가는 신호를 낸 날의 값인데 그날 마지막 행의 조정 계수는 1 이라 원래 가격과 같은 단위다.
    **신호 뒤에** 분할·배당이 생기면 수정 계열만 다시 조정되어 둘이 어긋난다 — 5:1 분할이면 수정 종가가
    전부 손절가 아래로 내려가 "손절 도달" 이 거짓으로 찍혔다 (docs/infra.md 25.208).
    그래서 진입일의 (수정 ÷ 원래) 비율로 목표·손절을 지금 단위로 옮긴다. 원래 종가를 모르면 옮기지 않는다.
    """
    out = Outcome(int(signal["stock_id"]), str(signal["as_of_date"]), str(signal["horizon"]))
    dates = [d for d, _c in closes]
    # **기준일 그날은 진입일이 아니다** (docs/infra.md 25.154). `as_of_date` 는 배치가 다룬
    # 데이터의 날짜(직전 거래일)이고, 그 신호가 실린 리포트는 **다음 날 아침**에 온다.
    # `bisect_left` 는 그날 종가가 있으면 그것을 집었다 — 신호를 만든 바로 그 값이라
    # 살 수 없는 가격이고, 이어 오르는 신호일수록 성적이 부풀려진다.
    # 백테스트는 같은 규칙을 이미 지키고 있다("판단에는 t-1 까지", docs/backtest.md 1.1)
    start = bisect_right(dates, out.as_of_date)
    if start >= len(closes):
        return out
    entry_date, entry = closes[start]
    if entry <= 0:
        return out
    out.entry_date, out.entry_close = entry_date, entry
    after = closes[start + 1 :]
    out.days_available = len(after)

    # **시세가 끊긴 종목을 빼지 않는다** (docs/infra.md 25.696, 감사 재현 — 생존편향). 창이 안 찬 신호는 NULL 이라
    # 집계에서 빠지는데, 상장폐지·거래정지로 시세가 **영영** 끊긴 종목도 같은 NULL 이었다 — −50% 로 떨어진 뒤 끊긴
    # 5건이 빠져 20·60일 승률 100%, 손절률 0% 가 나왔다. 지수는 창을 지났는데 종목 계열이 먼저 끝났으면 **마지막
    # 종가를 청산가로** 남은 창을 채운다.
    # **기준은 지수가 아니라 그 나라 시세 전체의 마지막 날**(`market_last`)이다 (25.697, 교차검증 재현). 25.696 은
    # 지수와
    # 견줘, D1 운영에서 미국 일일 배치가 쉬는 동안(국내 배치가 SP500 지수만 계속 받는다) 정상 미국 종목이 **전부**
    # 끊김이
    # 되어 승률 100% 가 됐다. 또 **그 창의 끝날이 지났는지**를 본다 — 시장 거래일(지수 날짜 중 market_last 이하)로
    # 진입 뒤
    # w 일이 지나야 그 창을 채운다. 25.696 은 60일 창을 3일치로 채웠다. market_last 를 모르면 채우지 않는다(보수적).
    # 진입일만 있고 끊긴 신호는 진입가가 마지막 종가다 — 0% 로 채운다
    시장일: list[str] = []
    끊김 = False
    if market_last and index_closes and closes:
        끊김 = (date.fromisoformat(market_last) - date.fromisoformat(closes[-1][0])).days > CUTOFF_GAP_DAYS
        시장일 = [d for d, _c in index_closes if entry_date < d <= market_last]
    거래일_뒤 = len(시장일)
    for w in WINDOWS:
        if len(after) >= w:
            out.rets[w] = _ret(after[w - 1][1], entry)
        elif 끊김 and 거래일_뒤 >= w:
            out.rets[w] = _ret(after[-1][1] if after else entry, entry)

    window = after[:HORIZON_WINDOW]
    if window:
        highs = max(c for _d, c in window)
        lows = min(c for _d, c in window)
        out.max_up, out.max_down = highs / entry - 1, lows / entry - 1
        target, stop = signal.get("target_price"), signal.get("stop_price")
        # 단위 맞추기. **신호 날 기준 종가가 있으면 그것이 가장 곧다** (docs/infra.md 25.215):
        # 지금 계열의 그날 종가 ÷ 신호 날 적어 둔 종가. 분할·배당, 국내·미국을 가리지 않는다.
        # 미국 `close` 는 야후 Close 라 다시 받을 때 **분할만큼 과거가 조정**되어, 25.208 의
        # (수정 ÷ 원래) 비율은 배당만 잡고 분할을 못 잡았다. 옛 신호(기준 종가 없음)는 그 비율로 둔다
        ref = signal.get("ref_close")
        지금_그날 = dict(closes).get(out.as_of_date)
        if ref and float(ref) > 0 and 지금_그날 and 지금_그날 > 0:
            k = 지금_그날 / float(ref)
        else:
            raw_entry = (raw_closes or {}).get(entry_date)
            k = entry / raw_entry if raw_entry and raw_entry > 0 else 1.0
        target = None if target is None else float(target) * k
        stop = None if stop is None else float(stop) * k
        # **먼저 닿은 쪽만** 닿은 것이다 (docs/infra.md 25.680, 감사). 예전에는 둘을 따로 `any` 로 봐, 3일째 손절가
        # 아래로
        # 갔다가 40일째 목표가를 넘은 신호가 "목표 도달" 에도 들어갔다 — 실제로는 손절로 끝났을 매매다.
        # 종가 하나로 보므로 같은 날 둘 다 닿을 수는 없다
        첫목표 = next((i for i, (_d, c) in enumerate(window) if target is not None and c >= float(target)), None)
        첫손절 = next((i for i, (_d, c) in enumerate(window) if stop is not None and c <= float(stop)), None)
        out.hit_target = None if target is None else 첫목표 is not None and (첫손절 is None or 첫목표 < 첫손절)
        out.hit_stop = None if stop is None else 첫손절 is not None and (첫목표 is None or 첫손절 < 첫목표)

    if index_closes:
        idx = dict(index_closes)
        base = idx.get(entry_date)
        for w in WINDOWS:
            if base and len(after) >= w:
                later = idx.get(after[w - 1][0])
                out.bench[w] = _ret(later, base)
            elif base and out.rets[w] is not None and 끊김 and len(시장일) >= w:
                # 채운 창도 **같은 창 끝날(시장 거래일로 w 일째)** 의 지수로 견준다 (25.698, 교차검증) — 비워 두면
                # "지수 대비" 가
                # 살아남은 종목만의 평균인데 화면에는 같은 n 옆에 적혔다
                out.bench[w] = _ret(idx.get(시장일[w - 1]), base)
    return out


@dataclass
class Stat:
    horizon: str
    window: int
    n: int
    avg_ret: float | None
    win_rate: float | None
    avg_excess: float | None
    hit_target_rate: float | None
    hit_stop_rate: float | None
    since: str | None


def summarize(outcomes: list[Outcome], min_n: int = 5) -> list[Stat]:
    """기간·창별 평균. 표본이 min_n 미만이면 평균을 내지 않는다(n 만 남긴다) — 세 건 평균은 뜻이 없다."""
    stats: list[Stat] = []
    for horizon in sorted({o.horizon for o in outcomes}):
        group = [o for o in outcomes if o.horizon == horizon]
        for w in WINDOWS:
            rets = [o.rets[w] for o in group if o.rets.get(w) is not None]
            excess = [o.excess(w) for o in group if o.excess(w) is not None]
            n = len(rets)
            if n < min_n:
                stats.append(Stat(horizon, w, n, None, None, None, None, None, None))
                continue
            with_window = [o for o in group if o.rets.get(w) is not None]
            targets = [o.hit_target for o in with_window if o.hit_target is not None] if w == HORIZON_WINDOW else []
            stops = [o.hit_stop for o in with_window if o.hit_stop is not None] if w == HORIZON_WINDOW else []
            stats.append(Stat(
                horizon, w, n,
                sum(rets) / n,
                sum(1 for r in rets if r > 0) / n,
                # **지수 대비는 같은 표본일 때만** (25.702, 교차검증) — 굳은 성적은 5일 지수값을 저장하지 않아(열 없음)
                # 5일 "지수 대비" 가 살아 있는 행만의 평균인데 같은 n 옆에 적혔다. 표본이 다르면 내지 않는다
                # 지수 하루 빠짐까지 통째로 버리지 않게 5% 까지는 허용한다 (25.706, 교차검증 — 30건 중 1건 빠짐에 None
                # 이었다)
                (sum(excess) / len(excess)) if excess and len(excess) >= n * EXCESS_SAMPLE_SHARE else None,  # type: ignore[arg-type]
                (sum(1 for t in targets if t) / len(targets)) if targets else None,
                (sum(1 for t in stops if t) / len(stops)) if stops else None,
                min(o.as_of_date for o in with_window),
            ))
    return stats
