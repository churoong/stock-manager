"""분기 순이익 계열 — SUE 의 입력 (종목선정 기법 발굴 루프 1회차 A, docs/factors.md 12.2, docs/infra.md 25.456).

DART 분기·반기 보고서의 손익은 **그 분기 3개월 값**이다(`dart._pick`). 4분기는 보고서가 없어
사업보고서 연간에서 1·2·3분기를 뺀다. 기준일(t-1)까지 접수된 공시만 쓴다.

계산만 한다. DB 도 시각도 모른다. 날짜는 ISO 문자열로 받는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from batch.sources.dart import ANNUAL_REPORT_CODE

#: 분기 번호(1~4)를 내는 보고서. 4분기는 사업보고서에서 뺀다
QUARTER_OF: dict[str, int] = {"11013": 1, "11012": 2, "11014": 3}

#: SUE 가 쓰는 분기 수 — 전년동기 변화 8개(`scoring.SUE_MIN_CHANGES`) + 계절 4
SERIES_QUARTERS = 12

#: 가장 최근 분기의 공시가 이보다 묵었으면 NULL (docs/factors.md 12.2 "신선도")
SUE_MAX_AGE_DAYS = 150


_Key = tuple[int, str]


def _latest(rows: list[dict[str, Any]], cutoff: str) -> tuple[dict[_Key, dict[str, Any]], dict[_Key, str]]:
    """((회계연도, 보고서) → 기준일까지 접수된 가장 늦은 행(정정 포함), 같은 열쇠 → **처음** 접수일).

    처음 접수일은 신선도에 쓴다 — 정정 공시일로 재면 오래된 분기가 새것처럼 보인다 (25.461, 교차검증)."""
    out: dict[tuple[int, str], dict[str, Any]] = {}
    처음: dict[tuple[int, str], str] = {}
    for r in rows:
        as_of = str(r["as_of_date"])
        if as_of > cutoff:
            continue
        key = (int(r["fiscal_year"]), str(r["report_code"]))
        kept = out.get(key)
        # 같은 날 두 행(원공시와 같은 날 정정)이면 접수번호가 큰 것 — 읽는 순서에 따라 값이 갈리지 않게 (25.465)
        순서 = (as_of, str(r.get("receipt_no", "")))
        if kept is None or 순서 > (str(kept["as_of_date"]), str(kept.get("receipt_no", ""))):
            out[key] = r
        if key not in 처음 or as_of < 처음[key]:
            처음[key] = as_of
    return out, 처음


def _ni(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    v = (row.get("values") or {}).get("net_income")
    return None if v is None else float(v)


def _ccy(row: dict[str, Any] | None) -> str | None:
    v = (row or {}).get("values") or {}
    return str(v["currency"]) if v.get("currency") else None


def _one_basis(rows: list[dict[str, Any]], cutoff: str) -> tuple[int, list[float | None]] | None:
    """한 기준(연결 또는 별도)의 행만으로 (최근 분기 번호, 계열). 알려진 분기가 없거나 묵었으면 None.

    **최근 분기와 통화가 다른 분기는 비운다** (docs/infra.md 25.920, 11회차 1). 두산밥캣은 2023 부터 연결을 USD 로 내
    그 전후의 전년동기 변화가 달러 − 원이었다. 4분기(연간 − 세 분기)도 넷의 통화가 같을 때만 낸다.
    통화를 모르는 행은 막지 않는다.
    """
    known, 처음 = _latest(rows, cutoff)
    값: dict[int, float | None] = {}  # 분기 번호(연도*4 + 분기-1) → 순이익
    공시일: dict[int, str] = {}  # 분기 번호 → 그 분기가 처음 알려진 날
    통화: dict[int, str | None] = {}  # 분기 번호 → 그 값의 통화(모르면 None)
    years = {y for y, _ in known}
    for y in years:
        for code, q in QUARTER_OF.items():
            r = known.get((y, code))
            if r is not None:
                값[y * 4 + q - 1] = _ni(r)
                공시일[y * 4 + q - 1] = 처음[(y, code)]
                통화[y * 4 + q - 1] = _ccy(r)
        annual = known.get((y, ANNUAL_REPORT_CODE))
        if annual is None:
            continue
        # 사업보고서가 나왔으면 4분기가 "가장 최근 분기" 다 — 1·2·3분기 중 하나를 몰라 값을 못 내도 자리는 4분기다.
        # 그러지 않으면 3분기가 최근 분기로 남아 이미 지난 서프라이즈를 새것처럼 쓴다
        parts = [known.get((y, c)) for c in QUARTER_OF]
        나머지 = [_ni(p) for p in parts]
        연간 = _ni(annual)
        idx = y * 4 + 3
        넷통화 = {c for c in [_ccy(annual)] + [_ccy(p) for p in parts] if c}
        섞임 = len(넷통화) > 1
        값[idx] = None if 연간 is None or 섞임 or any(x is None for x in 나머지) else 연간 - sum(나머지)  # type: ignore[arg-type]
        통화[idx] = next(iter(넷통화)) if len(넷통화) == 1 else None
        공시일[idx] = max([처음[(y, ANNUAL_REPORT_CODE)]] + [처음[(y, c)] for c in QUARTER_OF if (y, c) in 처음])
    if not 공시일:
        return None
    last = max(공시일)
    age = (date.fromisoformat(cutoff[:10]) - date.fromisoformat(공시일[last][:10])).days
    if age > SUE_MAX_AGE_DAYS:
        return None
    기준통화 = 통화.get(last)
    return last, [
        None if (기준통화 and 통화.get(i) and 통화.get(i) != 기준통화) else 값.get(i)
        for i in range(last - SERIES_QUARTERS + 1, last + 1)
    ]


def quarter_series(rows: list[dict[str, Any]], cutoff: str) -> list[float | None] | None:
    """기준일에 알 수 있던 분기 순이익 `SERIES_QUARTERS` 개, 오래된 것부터. 알려진 분기가 없거나 묵었으면 None.

    `rows` 는 한 종목의 스냅샷: as_of_date, fiscal_year, report_code, values, consolidated(없으면 연결로 본다).
    **기준은 기준일마다 고른다** (25.461, 교차검증) — 연결·별도를 따로 계열로 만들어 (1) 최근 분기가 더 새것,
    (2) 같으면 값이 있는 칸이 더 많은 것, (3) 그래도 같으면 연결. 두 기준을 한 계열 안에서 섞지는 않는다
    (4분기 = 연간 − 세 분기의 뺄셈이 틀린다).
    """
    후보 = []
    for 연결 in (True, False):
        got = _one_basis([r for r in rows if bool(r.get("consolidated", True)) is 연결], cutoff)
        if got is not None:
            last, 계열 = got
            후보.append((last, sum(v is not None for v in 계열), 연결, 계열))
    if not 후보:
        return None
    return max(후보, key=lambda c: (c[0], c[1], c[2]))[3]
