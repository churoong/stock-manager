"""분기 순이익 계열 — SUE 의 입력 (docs/factors.md 12.2, docs/infra.md 25.456)."""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import quarterly_earnings as qe
from batch.services import scoring as sc


def row(y: int, code: str, ni: float | None, as_of: str) -> dict[str, Any]:
    return {"fiscal_year": y, "report_code": code, "as_of_date": as_of, "values": {"net_income": ni}}


def 한_해(y: int, q: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """1·2·3분기 3개월 값과 연간(= 넷의 합). 공시일은 법정 기한 무렵."""
    return [
        row(y, "11013", q[0], f"{y}-05-14"),
        row(y, "11012", q[1], f"{y}-08-13"),
        row(y, "11014", q[2], f"{y}-11-13"),
        row(y, "11011", sum(q), f"{y + 1}-03-20"),
    ]


def test_4분기는_연간에서_세_분기를_뺀다() -> None:
    rows = [r for y in (2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0 + y - 2023))]
    got = qe.quarter_series(rows, "2026-03-25")
    assert got == [1.0, 2.0, 3.0, 4.0, 1.0, 2.0, 3.0, 5.0, 1.0, 2.0, 3.0, 6.0]


def test_기준일_뒤_공시는_없는_것이다() -> None:
    rows = [r for y in (2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0))]
    got = qe.quarter_series(rows, "2026-03-19")  # 2025 사업보고서는 3/20 접수 — 아직 모른다
    assert got is not None and got[-1] == 3.0 and len(got) == qe.SERIES_QUARTERS
    assert got[0] is None  # 2023 1분기 앞(2022 4분기)은 없다 — 자리를 지킨다


def test_정정_공시는_기준일까지_나온_것_중_늦은_것() -> None:
    rows = 한_해(2025, (1.0, 2.0, 3.0, 4.0)) + [row(2025, "11014", 30.0, "2025-12-01")]
    assert qe.quarter_series(rows, "2025-11-30")[-1] == 3.0  # type: ignore[index]
    assert qe.quarter_series(rows, "2025-12-02")[-1] == 30.0  # type: ignore[index]


def test_사업보고서가_나왔는데_분기_하나를_모르면_4분기는_None_이고_자리는_4분기() -> None:
    """3분기를 최근 분기로 남기면 이미 지난 서프라이즈를 새것처럼 쓴다."""
    rows = [r for r in 한_해(2025, (1.0, 2.0, 3.0, 4.0)) if r["report_code"] != "11012"]
    got = qe.quarter_series(rows, "2026-03-25")
    assert got is not None and got[-1] is None and got[-2] == 3.0 and got[-3] is None


def test_묵은_분기는_쓰지_않는다() -> None:
    rows = 한_해(2025, (1.0, 2.0, 3.0, 4.0))
    assert qe.quarter_series(rows, "2026-08-16") is not None  # 3/20 + 149일
    assert qe.quarter_series(rows, "2026-08-18") is None  # 151일
    assert qe.quarter_series([], "2026-01-01") is None


def test_SUE_까지_이어진다() -> None:
    """12분기가 모두 있으면 `scoring.sue` 가 값을 낸다 — 입력 모양이 맞는다."""
    rows = [r for y in (2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0 + y - 2023, 3.0, 4.0 + (y - 2023) ** 2))]
    rows += 한_해(2022, (1.0, 1.5, 3.0, 4.0))
    got = qe.quarter_series(rows, "2026-03-25")
    assert got is not None and None not in got
    assert sc.sue(got, 1.0) == pytest.approx(sc.sue(got, 1e12))  # 시가총액은 약분된다(11.4)
    assert sc.sue(got, 1.0) is not None


def test_백테스트_IC_가_sue_를_잰다(monkeypatch: pytest.MonkeyPatch) -> None:
    """분기 스냅샷을 넘기면 IC 이름 `sue` 가 생기고, 서프라이즈가 큰 종목이 더 오르면 IC 가 1 이다."""
    from types import SimpleNamespace

    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-03-30", "2026-03-31", "2026-04-29", "2026-04-30"]
    prices = {sid: {"2026-03-30": 100.0, "2026-03-31": 100.0, "2026-04-30": 100.0 + sid} for sid in ids}
    분기: dict[int, list[dict[str, Any]]] = {}
    for sid in ids:
        rows = [r for y in (2022, 2023, 2024) for r in 한_해(y, (1.0, 2.0 + (y % 2), 3.0, 4.0 + (y % 3)))]
        rows += 한_해(2025, (1.0, 2.0, 3.0, 4.0 + sid))  # 종목 번호가 클수록 4분기 서프라이즈가 크다
        분기[sid] = rows
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {},
                         quarters_by_stock=분기)  # fmt: skip
    assert got["sue"].months == 1 and got["sue"].mean == pytest.approx(1.0) and got["sue"].coverage == 1.0
    assert job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {}
                          )["sue"].months == 0  # 분기 스냅샷이 없으면(미국) 재지 못한다  # fmt: skip


def row_b(y: int, code: str, ni: float | None, as_of: str, 연결: bool) -> dict[str, Any]:
    return {**row(y, code, ni, as_of), "consolidated": 연결}


class Test기준은_기준일마다:
    """연결·별도를 종목 전체의 MAX 로 고르면 미래 정보다 (docs/infra.md 25.461, 교차검증)."""

    def test_나중에_처음_연결을_낸_회사도_그때는_별도로(self) -> None:
        별도 = [{**r, "consolidated": False} for y in (2021, 2022, 2023) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0))]
        별도 += [row_b(2024, c, v, d, False) for c, v, d in (("11013", 1.0, "2024-05-14"), ("11012", 2.0, "2024-08-13"),
                                                            ("11014", 3.0, "2024-11-13"))]  # fmt: skip
        연결 = [{**r, "consolidated": True} for r in 한_해(2025, (1.0, 2.0, 3.0, 4.0))]
        got = qe.quarter_series(별도 + 연결, "2024-12-01")
        assert got is not None and got[-1] == 3.0 and None not in got

    def test_연결이_끊기면_별도로_이어진다(self) -> None:
        연결 = [{**r, "consolidated": True} for y in (2021, 2022, 2023, 2024) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0))]
        별도 = [{**r, "consolidated": False} for y in (2021, 2022, 2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0, 3.0, 5.0))]
        got = qe.quarter_series(연결 + 별도, "2026-04-01")
        assert got == [1.0, 2.0, 3.0, 5.0] * 3  # 한 기준(별도)으로 끝까지 — 연결 4.0 이 섞이지 않는다

    def test_연간만_연결이고_분기는_별도면_별도(self) -> None:
        연결 = [row_b(y, "11011", 10.0, f"{y + 1}-03-20", True) for y in (2022, 2023, 2024, 2025)]
        별도 = [{**r, "consolidated": False} for y in (2022, 2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0))]
        got = qe.quarter_series(연결 + 별도, "2026-03-25")
        assert got == [1.0, 2.0, 3.0, 4.0] * 3  # 연결은 4분기 자리만 있고 값이 없다 → 칸이 많은 별도

    def test_같으면_연결(self) -> None:
        연결 = [{**r, "consolidated": True} for y in (2023, 2024, 2025) for r in 한_해(y, (1.0, 2.0, 3.0, 4.0))]
        별도 = [{**r, "consolidated": False} for y in (2023, 2024, 2025) for r in 한_해(y, (9.0, 9.0, 9.0, 9.0))]
        assert qe.quarter_series(별도 + 연결, "2026-03-25") == [1.0, 2.0, 3.0, 4.0] * 3


def test_신선도는_처음_공시일로_잰다() -> None:
    """정정 공시일로 재면 오래된 분기가 새것처럼 보인다 (25.461, 교차검증)."""
    rows = 한_해(2025, (1.0, 2.0, 3.0, 4.0)) + [row(2025, "11011", 11.0, "2026-09-01")]  # 연간 정정
    assert qe.quarter_series(rows, "2026-09-02") is None  # 처음 3/20 → 166일
    assert qe.quarter_series(rows, "2026-08-16") is not None


def test_같은_날_두_행이면_접수번호가_큰_것() -> None:
    """읽는 순서에 따라 값이 갈렸다 (docs/infra.md 25.465, 교차검증)."""
    원 = {**row(2025, "11014", 3.0, "2025-11-13"), "receipt_no": "20251113000100"}
    정정 = {**row(2025, "11014", 30.0, "2025-11-13"), "receipt_no": "20251113000200"}
    기본 = [r for r in 한_해(2025, (1.0, 2.0, 3.0, 4.0)) if r["report_code"] != "11014"]
    for 순서 in ([원, 정정], [정정, 원]):
        assert qe.quarter_series(기본 + 순서, "2025-11-30")[-1] == 30.0  # type: ignore[index]
