"""판정 아닌 까닭(그날 시세 못 받음·환율 없음)으로 미룬 가상 매도는 KIS 비교 표본 밖 (docs/infra.md 25.1811, 결함 루프 10-27).

같은 까닭의 매수 만료는 25.1561 이 뺐는데 매도의 미룸 길이 남아, 그날 KIS 가 정상으로 판 줄이 "KIS만 참" 으로 세져 괴리
알림이 "가상 판정이 비관" 이라고 틀린 방향을 말했다. 정지·하한가로 미룬 것(판정 까닭, 25.1553)은 표본에 남는다.
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm


class _Rs:
    def __init__(self, rows: list) -> None:
        self.rows = rows


class _C:
    """시세가 있는 날·환율이 있는 날만 아는 가짜 DB"""

    def __init__(self, 시세날: set[str], 환율날: set[str] | None = None) -> None:
        self.시세날, self.환율날, self.불림 = 시세날, 환율날 or set(), 0

    def execute(self, sql: str, args: list) -> _Rs:
        self.불림 += 1
        if sql == job.DEFER_MARKET_DAY_SQL:
            return _Rs([[1]] if args[0] in self.시세날 else [])
        if sql == job.DEFER_FX_SQL:
            return _Rs([[1]] if any(args[1] <= d <= args[0] for d in self.환율날) else [])
        raise AssertionError(sql)


def _줄(d: str, i_status: str = "filled", i_fill: str | None = None, side: str = "sell") -> dict:
    return {"trade_date": d, "side": side, "status": "filled", "filled_qty": 1, "quantity": 1, "avg_price": 100.0,
            "i_status": i_status, "i_fill_date": i_fill or d, "i_price": 100.0, "i_order_type": "market",
            "i_fill_basis": "open", "reason": "x", "message": None}  # fmt: skip


def test_시세_구멍_날_미룬_매도는_표본_밖_정지_하한가는_남는다() -> None:
    맞음 = [_줄("2026-10-15") for _ in range(20)]
    구멍 = [_줄("2026-10-16", i_fill="2026-10-19") for _ in range(6)]  # 10-16 국내 시세가 통째로 없음
    줄 = 맞음 + 구멍
    c = _C({"2026-10-15", "2026-10-19"})
    assert job.mark_nonjudgment_defers(c, 줄, "KR") == 6
    assert c.불림 == 1  # 그 거래일 한 번만 묻는다
    r = bm.compare(줄)
    assert r["kis_only"] == 0 and r["excluded"] >= 6 and r["match_pct"] == 100.0
    assert "match" not in bm.diverge_active(r, set())  # 예전엔 match 77% 로 괴리 알림이 켜졌다
    # 시세가 있는 날(그 종목만 정지·하한가 — 판정 까닭)에 미룬 매도는 그대로 KIS만 참
    정지 = [_줄("2026-10-15", i_fill="2026-10-16")]
    assert job.mark_nonjudgment_defers(_C({"2026-10-15"}), 정지, "KR") == 0
    assert bm.compare(맞음 + 정지)["kis_only"] == 1


def test_미국은_환율_없는_날도() -> None:
    줄 = [_줄("2026-10-16", i_fill="2026-10-19")]
    assert job.mark_nonjudgment_defers(_C({"2026-10-16"}, set()), 줄, "US") == 1
    줄 = [_줄("2026-10-16", i_fill="2026-10-19")]
    assert job.mark_nonjudgment_defers(_C({"2026-10-16"}, {"2026-10-12"}), 줄, "US") == 0  # 7일 안 묵은 환율은 쓴다
    # 국내는 환율을 묻지 않는다
    assert job.mark_nonjudgment_defers(_C({"2026-10-16"}), [_줄("2026-10-16", i_fill="2026-10-19")], "KR") == 0


def test_체결_칸도_같다() -> None:
    줄 = [_줄("2026-10-16", i_fill="2026-10-19") for _ in range(3)]
    job.mark_nonjudgment_defers(_C(set()), 줄, "KR")
    칸 = bm.cells(줄)
    assert all((v or {}).get("kis_only", 0) == 0 for v in (칸.get("cells") or {}).values())


def test_가상_단계와_같은_잣대() -> None:
    """25.1812(25.1811 교차검증 [참고]) — 시세 질의는 가상 단계와 글자까지 같고, 환율은 7일째까지 쓴다(`paper._fx`)"""
    from batch.jobs import paper

    assert job.DEFER_MARKET_DAY_SQL == paper.MARKET_DAY_SQL
    줄 = [_줄("2026-10-16", i_fill="2026-10-19")]
    assert job.mark_nonjudgment_defers(_C({"2026-10-16"}, {"2026-10-09"}), 줄, "US") == 0  # 정확히 7일 — 씀
    줄 = [_줄("2026-10-16", i_fill="2026-10-19")]
    assert job.mark_nonjudgment_defers(_C({"2026-10-16"}, {"2026-10-08"}), 줄, "US") == 1  # 8일 — 없음


def test_비교와_체결_칸_둘_다에_표시를_단다() -> None:
    """배선 — 두 호출을 빼도 위 시험은 통과했다(25.1811 교차검증 [권고]). 실행이 비교 줄·칸 줄 모두에 단다"""
    import inspect

    글 = inspect.getsource(job.run)
    assert 글.count("mark_nonjudgment_defers(client, 비교줄, market)") == 1
    assert 글.count("mark_nonjudgment_defers(client, 칸줄, market)") == 1
    assert 글.index("비교 = bm.compare(비교줄)") > 글.index("mark_nonjudgment_defers(client, 비교줄, market)")


def test_표본_밖_끝_줄은_배치와_웹이_같은_글() -> None:
    """25.1813(25.1812 교차검증 [참고]) — 배치 주간 요약과 웹 /kis 끝 줄이 같은 글자여야 한다(웹은 paperCompany1469 가 본다)"""
    from pathlib import Path

    줄 = [_줄("2026-10-16", i_fill="2026-10-19")]
    job.mark_nonjudgment_defers(_C(set()), 줄, "KR")
    끝 = " · ".join(bm.compare_lines(bm.compare(줄), None, None))
    글 = "표본 밖 1(가상의 판정 아닌 만료·시세·환율 없어 미룬 매도·장 열린 뒤 시장가)"
    assert 글 in 끝
    웹 = (Path(__file__).resolve().parents[1] / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    assert "(가상의 판정 아닌 만료·시세·환율 없어 미룬 매도·장 열린 뒤 시장가)" in 웹
