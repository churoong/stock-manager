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
