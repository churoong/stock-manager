"""매도 플래그 테스트 (docs/sell_flags.md, Step 13).

완료 기준 (design.md): 자동 매도 경로가 코드에 존재하지 않음을 확인, 각 레벨의 판정 조건 테스트.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.services import report_picks as rp
from batch.services import sell_flags as sf
from tests.test_portfolio_job import MemClient

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 9, 17)


def holding(**kw) -> sf.HoldingInput:
    base = dict(stock_id=1, name="종목", horizon="long", first_buy_date="2026-01-02", currency="KRW",
                cost=1_000_000.0, market_value=1_000_000.0, price_date="2026-09-16")  # fmt: skip
    base.update(kw)
    return sf.HoldingInput(**base)


def codes(flags: list[sf.Flag]) -> list[tuple[str, str]]:
    return [(f.level, f.reason_code) for f in flags]


class Test자동매도_경로가_없다:
    def test_배치_코드는_매매_기록을_쓰지_않는다(self) -> None:
        pattern = re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+trades\b", re.IGNORECASE)
        offenders = [
            str(path.relative_to(ROOT))
            for path in (ROOT / "batch").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_웹에서_매매를_쓰는_곳은_사용자_입력_경로뿐(self) -> None:
        pattern = re.compile(r"(INSERT\s+INTO|DELETE\s+FROM)\s+trades\b", re.IGNORECASE)
        writers = sorted(
            str(path.relative_to(ROOT)).replace("\\", "/")
            for path in (ROOT / "web").rglob("*.ts")
            if "node_modules" not in path.parts and ".next" not in path.parts and "__tests__" not in path.parts
            and pattern.search(path.read_text(encoding="utf-8"))
        )  # fmt: skip
        assert writers == ["web/app/api/trades/[id]/route.ts", "web/lib/portfolio.ts"]


class Test레벨:
    def test_적_손절선(self) -> None:
        assert codes(sf.evaluate(holding(market_value=750_000.0), TODAY)) == [("red", "손절")]
        assert codes(sf.evaluate(holding(market_value=751_000.0), TODAY)) == []

    def test_녹_목표도달(self) -> None:
        assert codes(sf.evaluate(holding(market_value=1_500_000.0), TODAY)) == [("green", "목표도달")]

    def test_투자_기간마다_문턱이_다르다(self) -> None:
        # 단기 손절 -7%
        assert codes(
            sf.evaluate(holding(horizon="short", market_value=930_000.0, first_buy_date="2026-09-01"), TODAY)
        ) == [("red", "손절")]

    def test_설정_문턱을_쓴다(self) -> None:
        targets = {"long": {"target_pct": 20.0, "stop_pct": -10.0}}
        assert codes(sf.evaluate(holding(market_value=1_200_000.0), TODAY, targets)) == [("green", "목표도달")]

    def test_황_기간초과(self) -> None:
        flags = sf.evaluate(holding(horizon="mid", first_buy_date="2025-09-16"), TODAY)
        assert codes(flags) == [("yellow", "기간초과")]
        assert sf.evaluate(holding(horizon="long", first_buy_date="2010-01-01"), TODAY) == []

    def test_황_재무악화_영업적자_전환(self) -> None:
        h = holding(op_income_latest=-5.0, op_income_prev=10.0, fiscal_year_latest=2025)
        assert codes(sf.evaluate(h, TODAY)) == [("yellow", "재무악화")]
        assert sf.evaluate(holding(op_income_latest=-5.0, op_income_prev=-1.0), TODAY) == []

    def test_적자_전환_근거는_단위와_통화를_붙인다(self) -> None:
        """원 단위 날값이라 원인지 달러인지 알 수 없었다 (docs/infra.md 25.937, 감사)."""
        h = holding(op_income_latest=-12_345_000_000.0, op_income_prev=3_456_000_000.0, fiscal_year_latest=2025,
                    op_income_currency="KRW", op_income_prev_currency="KRW")  # fmt: skip
        (flag,) = sf.evaluate(h, TODAY)
        (근거,) = [c for c in flag.criteria if c["label"] == "영업이익 적자 전환"]
        assert 근거["display"] == "FY2025 -123.5억원 (전년 34.6억원)"
        # 국내 상장사가 달러로 보고하면(25.915) 달러로 보인다 — 원으로 읽히지 않게
        h = holding(op_income_latest=-5_000_000.0, op_income_prev=20_000_000.0, fiscal_year_latest=2025,
                    op_income_currency="USD", op_income_prev_currency="USD")  # fmt: skip
        assert sf.evaluate(h, TODAY)[0].criteria[0]["display"] == "FY2025 $-5.0M (전년 $20.0M)"

    def test_황_재무악화_점수_20점_하락(self) -> None:
        assert codes(sf.evaluate(holding(score_at_buy=75.0, score_now=55.0), TODAY)) == [("yellow", "재무악화")]
        assert sf.evaluate(holding(score_at_buy=75.0, score_now=55.1), TODAY) == []

    def test_황_점검_유니버스_제외(self) -> None:
        assert codes(sf.evaluate(holding(universe_excluded_reason="관리종목"), TODAY)) == [("yellow", "점검")]

    def test_황_감성급락(self) -> None:
        h = holding(sentiment_delta_7d=-45.0, sentiment_negative_7d=5, sentiment_date="2026-09-16")
        assert codes(sf.evaluate(h, TODAY)) == [("yellow", "감성급락")]
        assert sf.evaluate(holding(sentiment_delta_7d=-45.0, sentiment_negative_7d=4), TODAY) == []

    def test_평가가_없으면_수익률_플래그를_내지_않는다(self) -> None:
        assert sf.evaluate(holding(market_value=None), TODAY) == []

    def test_급한_순서(self) -> None:
        flags = sf.sort_flags(
            sf.evaluate(holding(stock_id=2, market_value=1_600_000.0), TODAY)
            + sf.evaluate(holding(stock_id=1, market_value=700_000.0, universe_excluded_reason="거래정지"), TODAY)
        )
        assert [f.level for f in flags] == ["red", "yellow", "green"]


class Test묵은_값으로_판정하지_않는다:
    """**적어 놓고 읽을 때 안 걸었다** (docs/infra.md 25.161).

    `price_date` 와 `sentiment_date` 는 근거표의 "언제 기준" 으로 찍히고 있었는데,
    **그 날짜를 보고 판단하는 곳이 없었다.** 25.136 이 포트폴리오 평가에는 같은
    문턱을 걸었고, `scores` 는 감성에 3일 문턱을 걸고 있었다 — 매도 플래그만 안 걸었다.

    매도 플래그는 텔레그램으로 나간다. 묵은 값이면 **"닿았습니다" 가 현재형 거짓**이다.
    """

    def test_종가가_묵으면_플래그는_내되_말한다(self) -> None:
        # 11일(거래일 사이 최장 간격)을 넘긴다
        h = holding(market_value=700_000.0, price_date="2026-09-01")
        flags = sf.evaluate(h, TODAY)

        assert codes(flags) == [("red", "손절")], "값을 지우지 않는다 — 진짜 손절일 수 있다"
        assert "2026-09-01" in flags[0].rationale_text
        assert "16일 전" in flags[0].rationale_text
        assert "시세 수집을 확인" in flags[0].rationale_text

    def test_묵은_종가는_근거표에도_한_줄_는다(self) -> None:
        flags = sf.evaluate(holding(market_value=700_000.0, price_date="2026-09-01"), TODAY)
        라벨 = [c["label"] for c in flags[0].criteria]

        assert 라벨 == ["손절선", "종가 나이"], "근거표에 어느 행에서 왔는지 남아야 한다 (CLAUDE.md)"

    def test_목표도달에도_같은_말이_붙는다(self) -> None:
        """**같은 판단을 하는 다른 자리** — 녹색도 같은 종가를 쓴다."""
        flags = sf.evaluate(holding(market_value=1_600_000.0, price_date="2026-09-01"), TODAY)

        assert codes(flags) == [("green", "목표도달")]
        assert "16일 전" in flags[0].rationale_text

    def test_안_묵었으면_아무_말도_안_붙는다(self) -> None:
        flags = sf.evaluate(holding(market_value=700_000.0, price_date="2026-09-16"), TODAY)

        assert "일 전" not in flags[0].rationale_text
        assert [c["label"] for c in flags[0].criteria] == ["손절선"]

    def test_정상_휴장으로는_걸리지_않는다(self) -> None:
        """문턱은 `metrics.MAX_SESSION_GAP_DAYS`(11일 실측)다. 연휴는 안 걸린다."""
        from batch.services.metrics import MAX_SESSION_GAP_DAYS

        최장휴장 = (TODAY - timedelta(days=MAX_SESSION_GAP_DAYS)).isoformat()
        flags = sf.evaluate(holding(market_value=700_000.0, price_date=최장휴장), TODAY)

        assert "일 전" not in flags[0].rationale_text

    def test_묵은_감성으로는_급락을_판정하지_않는다(self) -> None:
        """종가와 달리 **버린다.** 문장이 "7일 사이" 라는 창을 말하는데 그 창이 끝난 지 오래다."""
        낡음 = holding(sentiment_delta_7d=-45.0, sentiment_negative_7d=5, sentiment_date="2026-09-01")

        assert sf.evaluate(낡음, TODAY) == []

    def test_감성_문턱이_점수_배치와_같은_값이다(self) -> None:
        """**같은 규칙이 두 곳에 있었고 한 곳만 지켰다.** 이제 정의처가 하나다."""
        from batch.jobs import scores
        from batch.services import sentiment

        # **값을 대 보면 안 된다.** 다시 손으로 `3` 을 적어도 `3 is 3` 은 참이다
        # (25.153 에서 배운 것: 그물이 새는 자리는 늘 이렇게 생겼다). 소스를 본다
        글 = (ROOT / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")
        assert "SENTIMENT_MAX_AGE_DAYS = sentiment_svc.MAX_AGE_DAYS" in 글, (
            "점수 배치가 문턱을 다시 손으로 적고 있다 — 정의처는 services.sentiment 다"
        )
        assert scores.SENTIMENT_MAX_AGE_DAYS == sentiment.MAX_AGE_DAYS

        경계 = (TODAY - timedelta(days=sentiment.MAX_AGE_DAYS)).isoformat()
        h = holding(sentiment_delta_7d=-45.0, sentiment_negative_7d=5, sentiment_date=경계)
        assert codes(sf.evaluate(h, TODAY)) == [("yellow", "감성급락")], "경계는 포함이다"

        하루더 = (TODAY - timedelta(days=sentiment.MAX_AGE_DAYS + 1)).isoformat()
        h2 = holding(sentiment_delta_7d=-45.0, sentiment_negative_7d=5, sentiment_date=하루더)
        assert sf.evaluate(h2, TODAY) == []

    def test_감성_날짜를_모르면_막지_않는다(self) -> None:
        """`as_of_date` 는 NOT NULL 이다. 비었다는 것은 감성 행이 없다는 뜻이고,
        그러면 `delta_7d` 도 없어 `is_sharp_drop` 이 이미 False 다."""
        assert sf.evaluate(holding(sentiment_date=None), TODAY) == []

    def test_묵음_판정이_포트폴리오와_같은_잣대다(self) -> None:
        """두 곳이 다른 수를 쓰면 화면과 알림이 서로 다른 말을 한다."""
        from batch.services import portfolio as pf

        assert sf._종가_묵음_일수() == pf._종가_묵음_일수()


class Test리포트:
    def test_추천이_없는_날에도_플래그를_싣는다(self) -> None:
        text = rp.render([], 0, None, sell_flags=[{"level": "red", "name": "A", "rationale": "A: 손절선", "new": True}])
        assert "매도 플래그" in text and "[적] NEW A: 손절선" in text


class Test배치:
    def test_이어지는_플래그는_처음_날짜와_확인을_이어받고_풀리면_비활성(self, monkeypatch) -> None:
        from batch.core import db
        from batch.jobs import sell_flags as job

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        c.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " horizon, price_date, close, market_value, updated_at)"
            " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-01-02', 'long', '2026-09-15', 70, 700, 't')"
        )
        assert job.run(date(2026, 9, 16)) == 0
        # 리포트에 싣는 플래그는 종목 번호와 근거표를 함께 가진다 (docs/infra.md 25.262)
        실린 = job.active_flags_for_report(mem, "KR", "2026-09-16")  # type: ignore[arg-type]
        assert 실린 and 실린[0]["stock_id"] == 1 and 실린[0]["criteria"], "리포트 플래그에 근거표가 없다"
        # 판정일도 싣는다 — 화면이 리포트 거래일을 판정일로 적지 않게 (docs/infra.md 25.342)
        assert 실린[0]["as_of_date"] == "2026-09-16"
        c.execute("UPDATE sell_flags SET dismissed_at = 'x', created_at = '2026-09-16T00:00:00+00:00'")
        assert job.run(date(2026, 9, 17)) == 0
        # created_at 은 처음 걸린 시각 — 이어지는 동안 이어받는다 (docs/infra.md 25.501)
        assert c.execute("SELECT DISTINCT created_at FROM sell_flags").fetchall() == [("2026-09-16T00:00:00+00:00",)]
        rows = c.execute(
            "SELECT as_of_date, is_active, first_seen_date, dismissed_at FROM sell_flags ORDER BY as_of_date"
        ).fetchall()
        assert rows == [("2026-09-16", 0, "2026-09-16", "x"), ("2026-09-17", 1, "2026-09-16", "x")]
        # 이어진 플래그의 어제 행에는 풀린 시각을 찍지 않는다 (docs/infra.md 25.564)
        assert c.execute("SELECT resolved_at FROM sell_flags WHERE as_of_date = '2026-09-16'").fetchone() == (None,)
        # 확인한 플래그는 리포트에서 빠진다
        assert job.active_flags_for_report(mem, "KR", "2026-09-17") == []  # type: ignore[arg-type]

        c.execute("UPDATE positions SET market_value = 1000")  # 회복 → 조건 풀림
        job.run(date(2026, 9, 18))
        assert c.execute("SELECT COUNT(*) FROM sell_flags WHERE is_active = 1").fetchone()[0] == 0
        assert c.execute("SELECT resolved_at FROM sell_flags WHERE as_of_date = '2026-09-17'").fetchone() == ("2026-09-18",)

    def _하루_두_번(self, monkeypatch, 첫날_있었나: bool):
        """국내 아침 배치와 미국 저녁 배치가 **같은 사용자의 오늘**로 둘 다 부른다 (docs/infra.md 25.198)."""
        from batch.core import db
        from batch.jobs import sell_flags as job

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        c.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " horizon, price_date, close, market_value, updated_at)"
            " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-01-02', 'long', '2026-09-16', 70, 700, 't')"
        )
        if 첫날_있었나:
            assert job.run(date(2026, 9, 16)) == 0
        assert job.run(date(2026, 9, 17)) == 0  # 국내 아침
        # 사용자가 낮에 화면에서 "확인" 을 눌렀다 (web/app/api/sell-flags/[id])
        c.execute("UPDATE sell_flags SET dismissed_at = '2026-09-17T03:00:00Z' WHERE as_of_date = '2026-09-17'")
        assert job.run(date(2026, 9, 17)) == 0  # 미국 저녁
        return mem, c, job

    def test_같은_날_다시_돌아도_오늘_누른_확인을_잃지_않는다(self, monkeypatch) -> None:
        """**오늘 새로 걸린 플래그.** 이어받을 어제 행이 없다."""
        mem, c, job = self._하루_두_번(monkeypatch, 첫날_있었나=False)

        rows = c.execute("SELECT as_of_date, is_active, first_seen_date, dismissed_at FROM sell_flags").fetchall()
        assert rows == [("2026-09-17", 1, "2026-09-17", "2026-09-17T03:00:00Z")]
        assert job.active_flags_for_report(mem, "KR", "2026-09-17") == []  # type: ignore[arg-type]

    def test_어제부터_이어진_플래그도_오늘_누른_확인을_잃지_않는다(self, monkeypatch) -> None:
        """**어제부터 이어진 플래그.** 어제 행은 아침 실행이 이미 비활성으로 돌렸다."""
        _mem, c, _job = self._하루_두_번(monkeypatch, 첫날_있었나=True)

        오늘 = c.execute(
            "SELECT first_seen_date, dismissed_at FROM sell_flags WHERE as_of_date = '2026-09-17'"
        ).fetchall()
        assert 오늘 == [("2026-09-16", "2026-09-17T03:00:00Z")]


class Test확인은_본_속_사유까지만:
    """시총미달을 확인했는데 관리종목으로 넘어가도 계속 숨겨졌다 (docs/infra.md 25.564, 감사 재현)."""

    def test_점검_사유가_바뀌면_다시_알린다(self) -> None:
        어제 = [{"label": "유니버스 제외", "display": "시총미달"}]
        오늘 = [{"label": "유니버스 제외", "display": "관리종목"}]
        assert not sf.keeps_dismissal("점검", 어제, 오늘)
        assert sf.keeps_dismissal("점검", 어제, 어제)

    def test_재무악화에_새_속_사유가_붙으면_다시_알린다(self) -> None:
        어제 = [{"label": "종합 점수 하락", "display": "70→48"}]
        오늘 = 어제 + [{"label": "영업이익 적자 전환", "display": "…"}]
        assert not sf.keeps_dismissal("재무악화", 어제, 오늘)
        # 값만 바뀐 것(점수 48→45)은 같은 속 사유다
        assert sf.keeps_dismissal("재무악화", 어제, [{"label": "종합 점수 하락", "display": "70→45"}])
        # 속 사유가 줄면 그대로 확인
        assert sf.keeps_dismissal("재무악화", 오늘, 어제)

    def test_값_문턱_플래그는_이어받는다(self) -> None:
        assert sf.keeps_dismissal("손절", [{"label": "손절", "display": "-7%"}], [{"label": "손절", "display": "-40%"}])

    def test_배치가_확인을_이어받거나_다시_알린다(self, monkeypatch) -> None:
        """소스 글자만 봐서, 이름이 겹친 읽기 함수가 늘 빈 목록을 줘 확인이 매번 풀린 것을 못 잡았다 (25.566)."""
        from batch.core import db
        from batch.jobs import sell_flags as job

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        사유 = {"값": "시총미달"}
        monkeypatch.setattr(job, "load_holdings", lambda client, today: ([object()], []))
        monkeypatch.setattr(job.sf, "evaluate", lambda h, today, rules: [sf.Flag(
            1, "yellow", "점검", "빠졌습니다", [{"label": "유니버스 제외", "display": 사유["값"]}])])
        assert job.run(date(2026, 9, 16)) == 0
        mem.conn.execute("UPDATE sell_flags SET dismissed_at = 'x', created_at = '2026-09-16T00:00:00+00:00'")
        assert job.run(date(2026, 9, 17)) == 0  # 같은 사유 — 확인을 이어받는다
        assert mem.conn.execute("SELECT dismissed_at FROM sell_flags WHERE as_of_date = '2026-09-17'").fetchone() == ("x",)
        사유["값"] = "관리종목"
        assert job.run(date(2026, 9, 18)) == 0  # 사유가 나빠졌다 — 다시 알리고 NEW
        확인, 처음 = mem.conn.execute(
            "SELECT dismissed_at, created_at FROM sell_flags WHERE as_of_date = '2026-09-18'"
        ).fetchone()
        assert 확인 is None and 처음 != "2026-09-16T00:00:00+00:00"


def test_기업행위_뒤_손절에는_수량_확인을_붙인다() -> None:
    """4:1 분할 뒤 −75% "손절선에 닿았습니다" 가 사유 없이 매일 실렸다 (docs/infra.md 25.1093, 감사 재현).

    지우지는 않는다 — 진짜 손절일 수 있다(묵은 종가 25.161 과 같은 판단)."""
    분할 = holding(market_value=250_000.0, corporate_action_recent=True)
    [f] = sf.evaluate(분할, TODAY)
    assert f.reason_code == "손절" and "기업행위" in f.rationale_text and "수량을 확인" in f.rationale_text
    assert any(r["label"] == "기업행위" for r in f.criteria)
    병합 = holding(market_value=10_000_000.0, corporate_action_recent=True)
    assert "기업행위" in sf.evaluate(병합, TODAY)[0].rationale_text
    [보통] = sf.evaluate(holding(market_value=250_000.0), TODAY)
    assert "기업행위" not in 보통.rationale_text and not any(r["label"] == "기업행위" for r in 보통.criteria)


def test_처음부터_유니버스_밖이던_보유는_빠졌다고_하지_않는다() -> None:
    """우선주·시총미달 종목을 사면 다음 배치부터 "빠졌습니다" 황색이 NEW 로 떴다 (docs/infra.md 25.1095, 감사 재현)."""
    for 사유 in ("보통주아님", "시총미달", "상장1년미만"):
        assert sf.evaluate(holding(universe_excluded_reason=사유, universe_included_since_buy=False), TODAY) == []
    # 결격으로 넘어가면 알리되 "빠졌습니다" 라 하지 않는다
    [f] = sf.evaluate(holding(universe_excluded_reason="관리종목", universe_included_since_buy=False), TODAY)
    assert f.reason_code == "점검" and "빠졌" not in f.rationale_text and "관리종목" in f.rationale_text
    # 들었다가 빠졌거나 모르면 예전 그대로
    for 들었나 in (True, None):
        [g] = sf.evaluate(holding(universe_excluded_reason="시총미달", universe_included_since_buy=들었나), TODAY)
        assert "빠졌습니다(시총미달)" in g.rationale_text


def test_묵은_지금_점수로는_점수_하락을_판정하지_않는다() -> None:
    """유니버스에서 빠져 점수가 멈춘 종목의 3월 점수가 9월에도 "지금" 이었다 (docs/infra.md 25.564, 감사 재현)."""
    묵음 = holding(score_at_buy=70.0, score_now=48.0, score_now_date="2026-03-02")
    assert not any(f.reason_code == "재무악화" for f in sf.evaluate(묵음, TODAY))
    새것 = holding(score_at_buy=70.0, score_now=48.0, score_now_date=(TODAY - timedelta(days=1)).isoformat())
    assert any(f.reason_code == "재무악화" for f in sf.evaluate(새것, TODAY))


class Test점수_하락은_같은_잣대끼리만:
    """**가중치를 바꾸면 종목은 그대로인데 점수만 움직인다** (docs/infra.md 25.209)."""

    def _돌리기(self, monkeypatch, 지금_가중치: str, 지금_판: int = 1, 중간=None, 중간_판=None, 더함=(), 유니버스=()):
        from batch.core import db
        from batch.jobs import sell_flags as job

        mem = MemClient()
        self._mem = mem
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        c.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " horizon, price_date, close, market_value, updated_at)"
            " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-09-16', 100, 1000, 't')"
        )
        c.execute(
            "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
            " snapshot_as_of, score_at_trade, created_at, updated_at)"
            " VALUES (1, 'buy', '2026-09-01', 100, 10, 'KRW', 1, 'none', '2026-08-31', 80, 't', 't')"
        )
        행들 = [("2026-08-31", 80, '{"value": 30, "quality": 70}', 1), ("2026-09-15", 55, 지금_가중치, 지금_판)]
        if 중간:
            행들.append((중간[0], 중간[1], 지금_가중치, 중간_판 or 지금_판))
        행들 += list(더함)
        for day, total, weights, 판 in 행들:
            c.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, ?, ?, 't')",
                [day, total, weights, 판],
            )
        for day, 편입, 사유 in 유니버스:
            c.execute(
                "INSERT INTO universe_members (snapshot_date, stock_id, included, exclude_reason, currency, created_at)"
                " VALUES (?, 1, ?, ?, 'KRW', 't')",
                [day, 편입, 사유],
            )
        holdings, 경고 = job.load_holdings(mem)  # type: ignore[arg-type]
        self._holdings = holdings
        flags = [f for h in holdings for f in sf.evaluate(h, date(2026, 9, 16))]
        return [f.reason_code for f in flags], 경고

    def test_미끼__같은_잣대면_25점_하락이_재무악화다(self, monkeypatch) -> None:
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 30, "quality": 70}')
        assert "재무악화" in codes and 경고 == []

    def test_가중치가_바뀌었으면_비교하지_않고_말한다(self, monkeypatch) -> None:
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 10, "quality": 90}')
        assert "재무악화" not in codes
        assert any("가중치" in 줄 and "에이" in 줄 for 줄 in 경고)

    def test_계산_판이_바뀌어도_같다(self, monkeypatch) -> None:
        codes, _ = self._돌리기(monkeypatch, '{"value": 30, "quality": 70}', 지금_판=2)
        assert "재무악화" not in codes

    def test_같은_잣대의_첫_점수가_있으면_기준점을_다시_잡는다(self, monkeypatch) -> None:
        """25.961 — 판을 올리면 그 전 보유 전부가 검사에서 영구히 빠졌다. 지금 잣대의 첫 점수(09-10, 78점)로 다시 잡는다."""
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 30, "quality": 70}', 지금_판=2, 중간=("2026-09-10", 78))
        assert "재무악화" in codes and 경고 == []
        # 근거표는 "매수 당시" 가 아니라 "기준 재설정" 이라 적는다
        from batch.jobs import sell_flags as job

        holdings, _ = job.load_holdings(self._mem)  # type: ignore[arg-type]
        h = holdings[0]
        assert h.score_rebased and h.score_at_buy == 78 and h.score_at_buy_date == "2026-09-10"
        [flag] = sf.evaluate(h, date(2026, 9, 16))
        assert any("기준 재설정(2026-09-10" in str(r) for r in flag.criteria)

    def test_옛_가중치_점수가_20행을_넘어도_기준점을_찾는다(self, monkeypatch) -> None:
        """매수 뒤 28거래일을 옛 가중치로 지낸 뒤 바꿨더니 앞 20행만 봐서 영영 못 찾았다 (docs/infra.md 25.1092, 감사 재현)."""
        옛 = '{"value": 30, "quality": 70}'
        # 09-01~09-08 여덟 행이 옛 가중치, 09-10 이 지금 가중치의 첫 점수(78). 쪽을 3행으로 줄여 "앞 20행이 모두 옛
        # 가중치" 를 재현한다(실제는 20행 × 28거래일)
        더함 = [((date(2026, 9, 1) + timedelta(days=i)).isoformat(), 80, 옛, 1) for i in range(8)]
        from batch.jobs import sell_flags as job

        monkeypatch.setattr(job, "BASELINE_PAGE", 3)
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 10, "quality": 90}', 중간=("2026-09-10", 78), 더함=더함)
        assert "재무악화" in codes and 경고 == []
        monkeypatch.setattr(job, "BASELINE_SCAN_PAGES", 1)  # 한 쪽(3행)만 보면 예전처럼 못 찾는다
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 10, "quality": 90}', 중간=("2026-09-10", 78), 더함=더함)
        assert "재무악화" not in codes and any("아직 없음" in 줄 for 줄 in 경고)

    def test_매수일을_새_판으로_다시_내도_매매_점수의_판으로_견준다(self, monkeypatch) -> None:
        """매매엔 판 1의 80점, 같은 날 판 2로 다시 낸 58점, 지금 판 2의 55점 — 80→55 거짓 재무악화였다 (25.1094, 감사)."""
        w = '{"value": 30, "quality": 70}'
        codes, 경고 = self._돌리기(monkeypatch, w, 지금_판=2, 더함=[("2026-08-31", 58, w, 2)])
        assert "재무악화" not in codes and any("아직 없음" in 줄 for 줄 in 경고)
        # 매수일 판이 하나뿐이고 지금과 같으면 예전처럼 견준다 — `test_미끼__같은_잣대면_25점_하락이_재무악화다`

    def test_매수_때부터_유니버스_밖이었는지_읽는다(self, monkeypatch) -> None:
        """매수(09-01) 때 쓰던 스냅샷(08-25)부터 한 번도 들지 않았으면 "빠졌습니다" 를 내지 않는다 (25.1095)."""
        w = '{"value": 30, "quality": 70}'
        밖 = [("2026-08-18", 1, None), ("2026-08-25", 0, "보통주아님"), ("2026-09-14", 0, "보통주아님")]
        codes, _ = self._돌리기(monkeypatch, w, 유니버스=밖)
        assert "점검" not in codes and self._holdings[0].universe_included_since_buy is False
        들었다 = [("2026-08-25", 1, None), ("2026-09-14", 0, "시총미달")]
        codes, _ = self._돌리기(monkeypatch, w, 유니버스=들었다)
        assert "점검" in codes and self._holdings[0].universe_included_since_buy is True

    def test_중간_점수가_옛_잣대면_기준점으로_쓰지_않는다(self, monkeypatch) -> None:
        codes, 경고 = self._돌리기(monkeypatch, '{"value": 30, "quality": 70}', 지금_판=2, 중간=("2026-09-10", 78), 중간_판=1)
        assert "재무악화" not in codes and any("아직 없음" in 줄 for 줄 in 경고)


def test_팔았다_다시_산_종목은_지금_보유분의_매수_점수와_비교한다(monkeypatch) -> None:
    """옛 매수(80점)는 이미 다 팔았다. 다시 산 때 58점, 지금 55점 → 3점 하락이라 재무악화가 아니다 (docs/infra.md 25.286)."""
    from batch.core import db
    from batch.jobs import sell_flags as job

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-09-16', 100, 1000, 't')"
    )
    for side, day, snap, score in (
        ("buy", "2025-01-02", "2024-12-31", 80),
        ("sell", "2025-06-02", None, None),
        ("buy", "2026-09-01", "2026-08-31", 58),
    ):
        c.execute(
            "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
            " snapshot_as_of, score_at_trade, created_at, updated_at)"
            " VALUES (1, ?, ?, 100, 10, 'KRW', 1, 'none', ?, ?, 't', 't')",
            [side, day, snap, score],
        )
    for day, total in (("2024-12-31", 80), ("2026-08-31", 58), ("2026-09-15", 55)):
        c.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
            " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, '{}', 1, 't')",
            [day, total],
        )
    holdings, _ = job.load_holdings(mem)  # type: ignore[arg-type]
    assert holdings[0].score_at_buy == 58
    assert [f.reason_code for h in holdings for f in sf.evaluate(h, date(2026, 9, 16))] == []


def test_소수_목표·손절선은_반올림하지_않고_적는다() -> None:
    """설정 7.5% 를 "8%" 로 적으면 근거표·문장과 판정이 어긋난다 (docs/infra.md 25.368)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "services" / "sell_flags.py").read_text(encoding="utf-8")
    assert "rules['stop_pct']:.0f" not in src and "rules['target_pct']:.0f" not in src
    assert f"{7.5:g}" == "7.5" and f"{-15.0:g}" == "-15"


class Test감성_나이는_거래일_기준:
    """월·화 아침과 연휴 뒤에 감성급락이 꺼졌다 켜지며 [확인] 이 사라졌다 (docs/infra.md 25.401)."""

    def test_월요일_아침_목요일_감성도_판정한다(self) -> None:
        # 2026-09-14(월) 국내 아침: 미국 최신 감성은 목요일(09-10). 달력일로 4일
        h = holding(sentiment_delta_7d=-50.0, sentiment_negative_7d=6, sentiment_date="2026-09-10",
                    sentiment_ref_date="2026-09-11")  # fmt: skip
        assert codes(sf.evaluate(h, date(2026, 9, 15))) == [("yellow", "감성급락")]

    def test_연휴_뒤_국내(self) -> None:
        # 2026-09-28(월) 추석 연휴 뒤: 직전 거래일 09-23, 감성도 09-23. 달력일로는 5일
        h = holding(sentiment_delta_7d=-50.0, sentiment_negative_7d=6, sentiment_date="2026-09-23",
                    sentiment_ref_date="2026-09-23")  # fmt: skip
        assert codes(sf.evaluate(h, date(2026, 9, 28))) == [("yellow", "감성급락")]

    def test_기준일로도_묵으면_판정하지_않는다(self) -> None:
        h = holding(sentiment_delta_7d=-50.0, sentiment_negative_7d=6, sentiment_date="2026-09-01",
                    sentiment_ref_date="2026-09-11")  # fmt: skip
        assert sf.evaluate(h, date(2026, 9, 14)) == []

    def test_배치가_시장별_직전_거래일을_넣는다(self) -> None:
        from batch.jobs import sell_flags as job

        assert job._감성_기준일("KR", date(2026, 9, 28)) == "2026-09-23"
        assert job._감성_기준일("US", date(2026, 9, 14)) == "2026-09-11"
        assert job._감성_기준일("KR", None) is None
        assert job._감성_기준일("ZZ", date(2026, 9, 14)) is None


    def test_오늘이면_미국은_미국_현지_날짜로_잰다(self, monkeypatch) -> None:
        """노동절 뒤 수요일 한국 아침 — 미국은 아직 화요일이라 기준일은 금요일(09-04)이다 (docs/infra.md 25.564)."""
        from batch.jobs import sell_flags as job

        monkeypatch.setattr(job.cal, "user_today", lambda: date(2026, 9, 9))
        monkeypatch.setattr(job.cal, "local_today", lambda m: date(2026, 9, 8) if m == "US" else date(2026, 9, 9))
        assert job._감성_기준일("US", date(2026, 9, 9)) == "2026-09-04"
        assert job._감성_기준일("KR", date(2026, 9, 9)) == "2026-09-08"


class Test기간초과는_판정_기간의_첫_매수부터:
    """장기 10주(1월) 뒤 단기 100주(9/1) — 다음 날 "단기 92일 초과, 243일째" 가 떴다 (docs/infra.md 25.417)."""

    def test_섞인_보유(self) -> None:
        h = holding(horizon="short", first_buy_date="2026-01-02", horizon_since="2026-09-01")
        assert "기간초과" not in [f.reason_code for f in sf.evaluate(h, date(2026, 9, 2))]
        늦게 = sf.evaluate(h, date(2026, 12, 15))
        assert "기간초과" in [f.reason_code for f in 늦게]

    def test_배치가_그_기간의_첫_매수를_읽는다(self) -> None:
        from batch.core import db
        from batch.jobs import sell_flags as job

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
        )
        for tid, day, hz in ((1, "2026-01-02", "long"), (2, "2026-09-01", "short")):
            c.execute(
                "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                " fee, tax, horizon, created_at, updated_at) VALUES (?, 1, 'buy', ?, 100, 10, 'KRW', 1, 'none',"
                " 0, 0, ?, 't', 't')",
                [tid, day, hz],
            )
        c.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " horizon, updated_at) VALUES (1, 20, 'KRW', 100, 1, 2000, 2000, '2026-01-02', 'short', 't')"
        )
        holdings, _ = job.load_holdings(mem)  # type: ignore[arg-type]
        assert holdings[0].horizon_since == "2026-09-01"


def test_7일_전_기사가_모자라면_중립에서_잰다() -> None:
    """조용하던 종목에 악재가 몰려도 과거 점수가 없어 급락이 안 떴다 (docs/infra.md 25.638, 감사)."""
    h = holding(sentiment_delta_7d=None, sentiment_now=-50.0, sentiment_negative_7d=7, sentiment_date="2026-09-16",
                sentiment_tracked_7d=True)
    flags = sf.evaluate(h, TODAY)
    # 7일 전에는 수집하지 않던 종목이면 원래 부정적이었을 수 있다 — 급락이라 하지 않는다 (25.641, 교차검증)
    늦게 = holding(sentiment_delta_7d=None, sentiment_now=-50.0, sentiment_negative_7d=7, sentiment_date="2026-09-16")
    assert sf.evaluate(늦게, TODAY) == []
    assert [f.reason_code for f in flags] == ["감성급락"]
    assert "중립 0 기준" in flags[0].criteria[0]["display"]
    # 부정 기사 조건은 그대로, 오늘 감성이 없으면 판정 안 함
    assert sf.evaluate(holding(sentiment_delta_7d=None, sentiment_now=-50.0, sentiment_negative_7d=4,
                               sentiment_tracked_7d=True), TODAY) == []
    assert sf.evaluate(holding(sentiment_delta_7d=None, sentiment_now=None, sentiment_negative_7d=9), TODAY) == []
    # 과거가 있으면 예전대로 변화를 본다 — 오늘 −50 이어도 7일 변화 −10 이면 안 뜬다
    assert sf.evaluate(holding(sentiment_delta_7d=-10.0, sentiment_now=-50.0, sentiment_negative_7d=9), TODAY) == []


def test_재계산이_쓰다_말았으면_판정하지_않는다(monkeypatch) -> None:
    """반쪽 보유로 판정해 빠진 종목의 손절 플래그가 조용히 사라졌다 (docs/infra.md 25.644, 감사)."""
    from batch.core import db
    from batch.jobs import portfolio as pf_job
    from batch.jobs import sell_flags as job

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-01-02', 'long', '2026-09-15', 70, 700, 't')"
    )
    c.execute(
        "INSERT INTO portfolio_summary (id, as_of_date, totals_json, allocation_json, metrics_json, upcoming_json,"
        " warnings_json, trades_version, calc_version, created_at) VALUES (1, 'd', '{}', '{}', '{}', '[]', '[]', ?, 1, 't')",
        [pf_job.REBUILDING],
    )
    assert job.run(date(2026, 9, 16)) == 1
    assert c.execute("SELECT COUNT(*) FROM sell_flags").fetchone()[0] == 0
    상태, 까닭 = c.execute("SELECT status, error_text FROM batch_runs WHERE job_name = ?", [job.JOB_NAME]).fetchone()
    assert 상태 == "failed" and "재계산이 끝나지 않아" in 까닭
    c.execute("UPDATE portfolio_summary SET trades_version = 'v1'")
    assert job.run(date(2026, 9, 16)) == 0  # 끝난 지문이면 예전대로


@pytest.mark.parametrize(("처음_수집", "뜨나"), [("2026-08-01T00:00:00+00:00", True), ("2026-09-08T00:00:00+00:00", False)])
def test_중립_기준은_과거_창이_열릴_때부터_받던_종목만(monkeypatch, 처음_수집: str, 뜨나: bool) -> None:
    """8일 전 수집을 시작한 종목도 과거 30일 창이 비어 거짓 급락이었다 (docs/infra.md 25.645, 교차검증)."""
    from batch.core import db
    from batch.jobs import sell_flags as job

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-01-02', 'long', '2026-09-15', 100, 1000, 't')"
    )
    c.execute(
        "INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count, negative_count,"
        " negative_count_7d, decay_halflife_days, delta_7d, method, calc_version, created_at)"
        " VALUES (1, '2026-09-16', -55, 8, 0, 8, 8, 7, NULL, 'm', 1, 't')"
    )
    c.execute(
        "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (1, 't', 'u', '2026-09-15', 'ko', 'rss', ?)",
        [처음_수집],
    )
    assert job.run(date(2026, 9, 16)) == 0
    뜬것 = [r[0] for r in c.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1")]
    assert ("감성급락" in 뜬것) is 뜨나


@pytest.mark.parametrize(("첫_매수", "뜨나"), [("2026-01-02", True), ("2026-09-10", False)])
def test_조용하던_보유에_악재가_몰리면_시장_수집_시작부터_본다(monkeypatch, 첫_매수: str, 뜨나: bool) -> None:
    """기사가 없던 종목은 첫 기사가 곧 악재라 "추적 전" 으로 읽혀 급락을 놓쳤다 (docs/infra.md 25.1100, 감사).

    시장 수집은 08-01 부터(다른 종목 기사), 이 종목 첫 기사는 09-15. 1월부터 보유했으면 그동안 수집 대상이었으니
    과거가 빈 것은 기사가 적었던 것이다 — 뜬다. 09-10 에 샀으면 그 전엔 대상이 아니었을 수 있다 — 뜨지 않는다."""
    from batch.core import db
    from batch.jobs import sell_flags as job

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't'),"
        " (2, 'B', 'KOSPI', 'KR', '비', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, ?, 'long', '2026-09-15', 100, 1000, 't')",
        [첫_매수],
    )
    c.execute(
        "INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count, negative_count,"
        " negative_count_7d, decay_halflife_days, delta_7d, method, calc_version, created_at)"
        " VALUES (1, '2026-09-16', -55, 8, 0, 8, 8, 7, NULL, 'm', 1, 't')"
    )
    c.execute(
        "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at) VALUES"
        " (2, 't', 'u0', '2026-08-01', 'ko', 'rss', '2026-08-01T00:00:00+00:00'),"
        " (1, 't', 'u1', '2026-09-15', 'ko', 'rss', '2026-09-15T00:00:00+00:00')"
    )
    assert job.run(date(2026, 9, 16)) == 0
    뜬것 = [r[0] for r in c.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1")]
    assert ("감성급락" in 뜬것) is 뜨나
    # 시장 수집 시작은 한 번 재서 설정에 둔다 — 다음 실행은 news 전체를 훑지 않는다
    assert "2026-08-01" in str(db.get_setting(mem, job.NEWS_START_KEY, {}))  # type: ignore[arg-type]


def test_ETF_보유에는_매도_플래그를_세우지_않는다() -> None:
    """적립 ETF 가 −25% 에 적색 "손절" 을 받았다 — ETF 는 타이밍 무관 장기 적립이다 (docs/infra.md 25.908, 감사)."""
    from batch.core import db
    from batch.jobs import sell_flags as job

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, asset_type, source, fetched_at)"
        " VALUES (1, '069500', 'ETF', 'KR', 'KODEX 200', 'KRW', 'active', 'etf', 't', 't')"
    )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 40000, 1, 400000, 400000, '2026-09-01', 'long', '2026-09-16', 30000, 300000, 't')"
    )
    holdings, _ = job.load_holdings(mem)  # type: ignore[arg-type]
    assert holdings == []
