"""KIS 수급 — 출처별 실패 판정, 분석 카드의 기준일·신용 줄 (docs/infra.md 25.1397, KIS 수급 감사)."""

from __future__ import annotations

from batch.jobs import kis_flows as job
from batch.services import insights as ins


def test_출처_하나만_통째로_실패해도_실패다() -> None:
    # 1,000 종목에서 투자자별만 전부 실패 — 네 호출 합의 1/4 이라 예전에는 partial
    failed = {f"{i:06d}:투자자": "오류" for i in range(1000)}
    by = job.failed_by_source(failed)
    assert by == {"투자자": 1000}
    assert job.run_status(by, 1000, len(failed)) == "failed"
    # 조금 빠진 것은 부분 성공 — 다음 날 겹쳐 받는다
    조금 = {f"{i:06d}:{s}": "오류" for i in range(100) for s in ("투자자", "공매도")}
    assert job.run_status(job.failed_by_source(조금), 1000, len(조금)) == "partial"
    assert job.run_status({}, 1000, 0) == "success"
    # 종목에 딸리지 않은 실패(일정 등)는 기타 — 출처 문턱에 들지 않는다
    assert job.failed_by_source({"일정:배당": "x", "000001:시간외": "y"}) == {"기타": 2}


def _행(날: str, 매매: bool, 신용: float | None = None) -> dict:
    return {"date": 날, "frgn_net_amt": -100 if 매매 else None, "orgn_net_amt": -50 if 매매 else None,
            "prsn_net_amt": 150 if 매매 else None, "short_vol_pct": 1.0, "credit_rmnd_pct": 신용}  # fmt: skip


def test_기준일은_합에_든_행의_날짜() -> None:
    # 투자자별 수집이 10-01 뒤로 멈췄고 공매도만 10-15 까지 들어왔다
    rows = [_행(f"2026-10-{d:02d}", False) for d in range(15, 1, -1)] + [_행(f"2026-09-{d:02d}", True)
                                                                         for d in range(30, 0, -1)]  # fmt: skip
    assert ins.flow_card(rows)["latest"] == "2026-09-30"


def test_신용은_신용_칸이_있는_행만_본다() -> None:
    # 신용잔고는 결제일 뒤에야 나와 최근 사흘은 빈칸 — 예전에는 첫 행이 빈칸이라 신용 줄이 아예 없었다
    rows = [_행(f"2026-10-{d:02d}", True) for d in (15, 14, 13)]
    rows += [_행(f"2026-09-{d:02d}", True, 3.0 - i * 0.01) for i, d in enumerate(range(30, 5, -1))]
    c = ins.flow_card(rows)
    assert c["credit"] == {"now": 3.0, "before": 2.8, "date": "2026-09-30"}
    assert any("신용 잔고율 3.00% (20거래일 전 2.80%, 2026-09-30 기준)" in 줄 for 줄 in ins.flow_lines(c))
