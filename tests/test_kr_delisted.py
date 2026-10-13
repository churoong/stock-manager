"""거래소 목록에서 사라진 국내 종목을 상장폐지로 적는다 (docs/infra.md 25.361)."""

from __future__ import annotations

from batch.jobs import universe as job

# 시장마다 10종목. 목록이 잘렸는지 보는 문턱(80%)을 넘게 한두 개만 빠뜨린다
KOSPI = [f"1{i:05d}" for i in range(10)]
KOSDAQ = [f"2{i:05d}" for i in range(10)]
ACTIVE = [(i, t, "KOSPI") for i, t in enumerate(KOSPI)] + [(100 + i, t, "KOSDAQ") for i, t in enumerate(KOSDAQ)]


def _상태(statements):
    return {args[2]: args[0] for _sql, args in statements}


def test_두_시장_어디에도_없으면_상장폐지() -> None:
    받은 = {"KOSPI": set(KOSPI), "KOSDAQ": set(KOSDAQ[1:])}
    statements, memos = job.kr_missing_statements(ACTIVE, 받은, "t")
    assert _상태(statements) == {100: "delisted"}
    assert memos


def test_다른_시장으로_옮기면_상장폐지가_아니다() -> None:
    받은 = {"KOSPI": set(KOSPI) | {KOSDAQ[0]}, "KOSDAQ": set(KOSDAQ[1:])}
    statements, _ = job.kr_missing_statements(ACTIVE, 받은, "t")
    assert _상태(statements) == {100: "excluded"}


def test_목록이_잘렸으면_아무것도_적지_않는다() -> None:
    받은 = {"KOSPI": set(KOSPI), "KOSDAQ": set(KOSDAQ[:3])}
    statements, memos = job.kr_missing_statements(ACTIVE, 받은, "t")
    assert statements == [] and "건너뛰었습니다" in memos[0]


def test_마스터_저장이_다시_나타난_폐지_종목을_되돌린다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "universe.py").read_text(encoding="utf-8")
    assert "status = CASE WHEN stocks.status = 'delisted' THEN 'active' ELSE stocks.status END" in src
    assert "kr_missing_statements(" in src


def test_묵은_시총으로_판정하면_말한다() -> None:
    """market_cap_date 를 아무도 안 읽어 지난주 시총으로 편입·제외가 정해졌다 (docs/infra.md 25.362)."""
    from datetime import date

    rows = [
        {"ticker": "A", "market_cap": 1e11, "market_cap_date": "2026-09-01"},
        {"ticker": "B", "market_cap": 1e11, "market_cap_date": "2026-09-25"},
        {"ticker": "C", "market_cap": None, "market_cap_date": "2026-01-01"},
    ]
    말 = job.stale_market_cap_warning(rows, date(2026, 9, 28))
    assert 말 is not None and "1종목" in 말 and "A(2026-09-01)" in 말
    assert job.stale_market_cap_warning(rows[1:], date(2026, 9, 28)) is None


def test_스냅샷이_묵은_시총을_본다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "universe.py").read_text(encoding="utf-8")
    assert "market_cap, market_cap_date, currency" in src
    assert "stale_market_cap_warning(rows, as_of, verdicts, turnover, filters.min_avg_turnover_20d)" in src


def test_묵은_시총은_판정을_가른_종목만_센다() -> None:
    """시총과 상관없이 빠진 종목(거래대금미달·관리종목)까지 세어 경고가 늘 켜져 있었다 (docs/infra.md 25.1122)."""
    from datetime import date

    from batch.services import universe as uni

    묵은 = {"market_cap": 1e11, "market_cap_date": "2026-09-01"}
    rows = [{"ticker": t, **묵은} for t in ("IN", "SMALL", "TURN", "SUP")]
    판정 = [
        uni.Verdict(included=True, reason=None, listed_days=999),
        uni.Verdict(included=False, reason=uni.REASON_SMALL_CAP, listed_days=999),
        uni.Verdict(included=False, reason=uni.REASON_LOW_TURNOVER, listed_days=999),
        uni.Verdict(included=False, reason=uni.REASON_SUPERVISED, listed_days=999),
    ]
    말 = job.stale_market_cap_warning(rows, date(2026, 9, 28), 판정)
    assert 말 is not None and "2종목" in 말 and "TURN" not in 말 and "SUP" not in 말
    assert job.stale_market_cap_warning(rows[2:], date(2026, 9, 28), 판정[2:]) is None


def test_거래대금으로도_빠질_시총미달은_세지_않는다() -> None:
    """미국 소형주의 묵은 시총이 거래대금 미달 종목에서 매주 경고를 켰다 (docs/infra.md 25.1266, 유니버스 감사 재현)."""
    from datetime import date

    from batch.services import universe as uni

    묵은 = {"market_cap": 4e8, "market_cap_date": "2026-06-05"}
    rows = [{"id": i, "ticker": t, **묵은} for i, t in ((1, "THIN"), (2, "LIQ"), (3, "UNK"))]
    판정 = [uni.Verdict(included=False, reason=uni.REASON_SMALL_CAP, listed_days=999)] * 3
    거래대금 = {1: 900_000, 2: 9_000_000}  # 3 은 모른다
    말 = job.stale_market_cap_warning(rows, date(2026, 10, 12), 판정, 거래대금, 5_000_000)
    assert 말 is not None and "2종목" in 말 and "THIN" not in 말
