"""분할·병합을 건넌 증권사 목표가 빼기 (docs/analysis.md 9.3, docs/infra.md 25.1343, factors.md 12.23 21회차 P1).

`kr_opinions.target_price` 는 **의견을 낸 날의 주식 단위**다. 그 뒤 5:1 분할이 있으면 오늘 종가와 견줄 때
+400% 괴리가 되고, 같은 증권사의 분할 뒤 목표가는 −80% "내림" 으로 셌다 — 종목 분석 컨센서스(9.3)·사다리·
예측 성적표의 consensus 모델, 목표가 추세, 반대 목소리(5장), 아침 리포트 엇갈림(3.9) 모두.

규칙(검증 둘 — 갈린 곳은 보수 쪽)
- 의견 날 d 의 계수 k(d) = 그날 이전 마지막 시세 행의 (수정종가 ÷ 원 종가). 국내 수정종가는 기업행위가 없으면
  비어 있어 `COALESCE(adj_close, close)` 로 읽는다 — 비면 1(검증 B: 그렇지 않으면 거의 전 종목 의견이 사라진다)
- |k(d) ÷ k(오늘) − 1| > `ACTION_TOLERANCE` 면 그 의견을 **뺀다**(배수로 맞추지 않는다 — 검증 A). KIS 가 지난
  목표가를 분할 조정해 다시 주는지 `[확인필요]` 라 맞추면 두 번 맞출 수 있다. 빼면 덜 보일 뿐 틀리지 않는다
  (`broker_stats` 와 같은 쪽)
- k 를 모르면(그날 이전 시세가 없음) 그 의견도 뺀다. 오늘 k 를 모르면 그 종목은 손대지 않는다(예전처럼)
- 같은 증권사의 분할 앞뒤 목표가는 앞 것이 빠지므로 서로 견주지 않는다(거짓 "내림" 없음)
- 알려진 틈: 일일 자동 수정이 건너뛴 날(20종목 넘음)의 분할은 아직 계수 변화가 없어 못 잡는다.
  가운데가 빈 수정종가 행(백필)은 1 로 읽힌다
읽기: (종목, 날) 쌍마다 시세 색인 한 행 — 하루 수천 행
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from batch.services.adjust import ACTION_TOLERANCE

T = TypeVar("T")

#: (종목, 날) 쌍마다 그날 이전 마지막 시세 행의 계수. 쌍은 JSON 배열 [[종목, "YYYY-MM-DD"], ...]
FACTOR_SQL = (
    "SELECT json_extract(j.value, '$[0]') AS stock_id, json_extract(j.value, '$[1]') AS d,"
    " (SELECT COALESCE(p.adj_close, p.close) * 1.0 / p.close FROM prices p"
    "   WHERE p.stock_id = json_extract(j.value, '$[0]') AND p.date <= json_extract(j.value, '$[1]') AND p.close > 0"
    "   ORDER BY p.date DESC LIMIT 1) AS k"
    " FROM json_each(?) j"
)
#: 한 질의에 넣는 쌍 수
CHUNK = 500


def factors(client: Any, pairs: Iterable[tuple[int, str]]) -> dict[tuple[int, str], float | None]:
    쌍 = sorted({(int(s), str(d)[:10]) for s, d in pairs})
    out: dict[tuple[int, str], float | None] = {}
    for i in range(0, len(쌍), CHUNK):
        for r in client.execute(FACTOR_SQL, [json.dumps([list(x) for x in 쌍[i : i + CHUNK]])]).dicts():
            k = r.get("k")
            out[(int(r["stock_id"]), str(r["d"]))] = float(k) if isinstance(k, (int, float)) and k > 0 else None
    return out


def same_units(k_then: float | None, k_now: float | None) -> bool:
    """의견 날과 오늘이 같은 주식 단위인가. 오늘을 모르면 참(손대지 않음), 그날을 모르면 거짓(뺀다)."""
    if k_now is None:
        return True
    if k_then is None:
        return False
    return abs(k_then / k_now - 1.0) <= ACTION_TOLERANCE


def drop_across_actions(  # noqa: UP047 — 로컬·CI 파이썬 3.11 (criteria.drop_unverifiable 와 같다)
    client: Any, rows: list[T], today: str, stock_of: Callable[[T], int], date_of: Callable[[T], str],
) -> tuple[list[T], dict[int, int]]:
    """(남긴 의견, 종목별 뺀 수). 읽기에 실패하면 그대로 돌려준다 — 부르는 쪽이 경고한다(예외를 올린다)."""
    if not rows:
        return rows, {}
    오늘 = str(today)[:10]
    쌍 = {(stock_of(r), str(date_of(r))[:10]) for r in rows} | {(stock_of(r), 오늘) for r in rows}
    k = factors(client, 쌍)
    남김: list[T] = []
    뺌: dict[int, int] = {}
    for r in rows:
        sid = stock_of(r)
        if same_units(k.get((sid, str(date_of(r))[:10])), k.get((sid, 오늘))):
            남김.append(r)
        else:
            뺌[sid] = 뺌.get(sid, 0) + 1
    return 남김, 뺌
