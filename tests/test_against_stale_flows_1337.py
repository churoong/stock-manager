"""종목 의견의 반대 목소리가 묵은 수급을 "최근 5거래일" 로 쓰지 않는다 (docs/infra.md 25.1337, 분석 화면 감사 #3).

리포트 엇갈림(25.1276)과 같은 거래일 창 — 달력 15일 창이라 수급 수집이 멈추면 1~2주 묵은 행이 순매도로 실렸다."""

from __future__ import annotations

from datetime import date

from batch.jobs import verdicts as job

순매도 = [{"date": d, "frgn_net_amt": -100, "orgn_net_amt": -50}
         for d in ("2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02")]  # fmt: skip


class _Client:
    def execute(self, sql, args=None):  # noqa: ANN001, ANN201
        class R:
            rows: list = []

        return R()


def _반대(flows: list[dict], 오늘: date) -> list[str]:
    out = job.against_kr(_Client(), [1], 오늘, [], flows={1: flows})  # type: ignore[arg-type]
    return [a["text"] for a in out.get(1, []) if a["against"]]


def test_수집이_멈춘_뒤의_묵은_순매도는_반대_목소리가_아니다() -> None:
    # 10-02(금) 뒤로 수집이 멈췄다. 10-12(월)의 최근 5거래일은 10-02·06·07·08·12 — 남는 날 하루라 판단하지 않는다.
    # 예전: 달력 15일(9-27~) 안의 네 날을 "외국인·기관 5거래일 순매도" 로 셌다
    assert _반대(순매도, date(2026, 10, 12)) == []


def test_최근_5거래일이면_그대로() -> None:
    assert any(t.startswith("외국인·기관 4거래일 순매도") for t in _반대(순매도, date(2026, 10, 2)))


def test_아침_실행은_전_거래일까지_5거래일을_센다() -> None:
    """아침 배치 안에서 돌 때 그날 수급은 아직 없다 (docs/infra.md 25.1388, KIS 수급 감사).

    10-15(목) 아침: 리포트(3.9)는 10-14 까지 5거래일(10-07·08·12·13·14, 10-09 한글날 휴장). 예전 이 잡은 10-15 를
    끝으로 잡아 10-07 의 큰 순매수를 빼고 4거래일 순매도를 반대 목소리로 붙였다 — 같은 아침 리포트와 엇갈렸다"""
    흐름 = [{"date": "2026-10-07", "frgn_net_amt": 1_000, "orgn_net_amt": 0}] + [
        {"date": d, "frgn_net_amt": -100, "orgn_net_amt": -50}
        for d in ("2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14")
    ]
    assert job.flow_anchor([r["date"] for r in 흐름], date(2026, 10, 15)) == date(2026, 10, 14)
    assert _반대(흐름, date(2026, 10, 15)) == []


def test_그날_행이_있으면_그날이_끝이다() -> None:
    """저녁 수동 실행 — 그날 수급이 들어와 있으면 그날까지 센다"""
    assert job.flow_anchor(["2026-10-14", "2026-10-15"], date(2026, 10, 15)) == date(2026, 10, 15)
