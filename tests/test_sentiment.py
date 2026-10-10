"""종목 감성 집계 테스트 (docs/sentiment.md). 손으로 계산 가능한 고정 값."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.services import sentiment as st

NOW = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)


def art(days_ago: float, score: float) -> st.ScoredArticle:
    return st.ScoredArticle(NOW - timedelta(days=days_ago), score)


def test_같은_날_기사는_단순_평균() -> None:
    agg = st.aggregate([art(0, 0.5), art(0, 0.1), art(0, -0.3), art(0, 0.2), art(0, 0.0)], NOW)
    assert agg.sentiment == pytest.approx(10.0)
    assert (agg.article_count, agg.positive_count, agg.negative_count) == (5, 3, 1)


def test_반감기_7일_가중() -> None:
    # 오늘 +1 ×2 (가중 1씩), 7일 전 −1 ×4 (0.5씩) → (2 − 2) / 4 = 0
    agg = st.aggregate([art(0, 1.0), art(0, 1.0), art(7, -1.0), art(7, -1.0), art(7, -1.0), art(7, -1.0)], NOW)
    assert agg.sentiment == pytest.approx(0.0)


def test_기사가_5건_미만이면_점수를_내지_않는다() -> None:
    agg = st.aggregate([art(0, 0.9), art(1, 0.9), art(2, 0.9), art(3, 0.9)], NOW)
    assert agg.sentiment is None and agg.article_count == 4


def test_30일_넘은_기사와_미래_기사는_뺀다() -> None:
    kept = [art(d, 0.2) for d in (0, 1, 2, 3, 4)]
    agg = st.aggregate([art(31, -1), *kept, art(-1, -1)], NOW)
    assert agg.article_count == 5 and agg.sentiment == pytest.approx(20.0)


def test_7일_변화는_그때_알던_기사로만() -> None:
    old = [art(d, 0.6) for d in (12, 11, 10, 9, 8)]  # 7일 전 시점 점수 +60
    new = [art(d, -0.6) for d in (1, 0.5, 0.2, 0.1, 0)]
    d7, d30 = st.deltas(old + new, NOW)
    now_score = st.aggregate(old + new, NOW).sentiment
    assert d7 == pytest.approx(now_score - 60.0)
    assert d30 is None  # 30일 전에는 기사가 없었다


def test_감성_급락_두_조건_모두() -> None:
    assert st.is_sharp_drop(-40.0, 5)
    assert not st.is_sharp_drop(-39.9, 9)
    assert not st.is_sharp_drop(-60.0, 4)
    assert not st.is_sharp_drop(None, 9)


def test_판만_다른_기사는_하나로_센다() -> None:
    """연합 1보·2보·종합이 부정 기사 네 건이 됐다 (docs/infra.md 25.647, 감사)."""
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 9, 20, tzinfo=UTC)
    rows = [
        ("삼성전자 공장 화재(2보)", t0 + timedelta(hours=1), -0.8),
        ("[속보] 삼성전자 공장 화재", t0, -0.9),
        ("삼성전자 공장 화재(종합)", t0 + timedelta(hours=3), -0.7),
        ("삼성전자 공장 화재(종합2보)", t0 + timedelta(hours=5), -0.6),
        ("삼성전자, 새 공장 착공", t0 + timedelta(hours=2), 0.5),
    ]
    남은 = st.판_하나만(rows)
    assert len(남은) == 2
    assert min(남은, key=lambda a: a.score).published_at == t0  # 가장 먼저 나온 판을 남긴다


def test_날이_다르면_같은_제목도_따로_센다() -> None:
    t0 = datetime(2026, 9, 20, 1, tzinfo=UTC)
    rows = [("삼성전자 실적 호조", t0, 0.9), ("삼성전자 실적 호조", t0 + timedelta(days=1), 0.9)]
    assert len(st.판_하나만(rows)) == 2


def test_판_표시_여러_꼴과_자정을_넘긴_판도_하나로_센다() -> None:
    """`(속보)`·`[종합2보]`·`<속보>`·`[고침]` 을 떼지 못했고, 23:50 1보와 00:40 종합이 두 건이었다 (docs/infra.md 25.1099, 감사)."""
    t0 = datetime(2026, 9, 20, 14, 50, tzinfo=UTC)  # 23:50 KST
    for 표시 in ("(속보)", "[종합2보]", "<속보>", "&lt;속보&gt;", "[고침]", "(고침)"):
        assert st.판_뗀_제목(f"{표시}삼성전자 공장 화재") == "삼성전자공장화재", 표시
    rows = [("[속보] 삼성전자 공장 화재", t0, -0.9), ("삼성전자 공장 화재(종합)", t0 + timedelta(minutes=50), -0.7)]
    assert len(st.판_하나만(rows)) == 1
    # 12시간이 넘으면 다음 날 같은 제목은 다른 기사로 본다(예전과 같음)
    assert len(st.판_하나만([rows[0], ("삼성전자 공장 화재", t0 + timedelta(hours=13), -0.5)])) == 2


class Test현지달력_25_749:
    """판 중복 날짜와 30·7일 창을 그 시장 현지 달력으로 (docs/infra.md 25.749, 뉴스 감성 감사)."""

    def test_미국_같은_날_두_판은_하나(self) -> None:
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        from batch.services import sentiment as st

        et = ZoneInfo("America/New_York")
        rows = [
            ("Apple beats estimates", datetime(2026, 10, 1, 13, 0, tzinfo=UTC), 0.5),  # 09:00 ET
            ("Apple beats estimates", datetime(2026, 10, 1, 16, 0, tzinfo=UTC), 0.4),  # 12:00 ET, 같은 제목
        ]
        # KST 로는 10-01 22:00 과 10-02 01:00 — 날이 갈려 둘이 됐다. 25.1099 부터는 12시간 안이라 tz 없이도 하나
        assert len(st.판_하나만(rows)) == 1
        assert len(st.판_하나만(rows, et)) == 1

    def test_서머타임_경계에서도_30일은_현지_자정부터(self) -> None:
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        from batch.services import sentiment as st

        et = ZoneInfo("America/New_York")
        as_of = datetime(2026, 11, 10, 23, 59, 59, tzinfo=et).astimezone(UTC)  # EST
        기사 = st.ScoredArticle(datetime(2026, 10, 12, 0, 30, tzinfo=et).astimezone(UTC), 0.0)  # EDT 00:30
        assert st.aggregate([기사], as_of).article_count == 0  # UTC 로 빼면 창 시작이 00:59:59 라 빠졌다
        assert st.aggregate([기사], as_of, et).article_count == 1


def test_추적_판정은_현지_날짜로() -> None:
    """KST 08:00 첫 수집(UTC 전날 23:00)을 전날로 읽었다 (docs/infra.md 25.749)."""
    from batch.jobs import sell_flags

    assert sell_flags.현지날짜("2026-08-23T23:00:00Z", "KR") == "2026-08-24"
    assert sell_flags.현지날짜("2026-08-24T03:00:00+00:00", "US") == "2026-08-23"
    from pathlib import Path

    # 25.1100 에서 나라를 변수로 뺐다 — 첫 기사 시각과 시장 수집 시작 모두 현지 날짜로 바꾼다
    원문 = Path("batch/jobs/sell_flags.py").read_text(encoding="utf-8")
    assert "현지날짜(str(처음), 나라)" in 원문 and "현지날짜(시장, 나라)" in 원문


def test_센티먼트_가중치가_바뀌면_20점_하락을_보지_않는다() -> None:
    """설정 10→50 이 감성 나쁜 종목의 종합 점수를 설정 탓만으로 빼 거짓 재무악화가 떴다 (docs/infra.md 25.762)."""
    from batch.jobs import sell_flags

    assert sell_flags._가중치_다름(10, 50)
    assert not sell_flags._가중치_다름(10, 10.0)
    assert not sell_flags._가중치_다름(None, 50)
    # 감성이 없는 날은 0 이 적힌다 — 설정 변경이 아니다 (25.763)
    assert not sell_flags._가중치_다름(10, 0.0)
    assert not sell_flags._가중치_다름(0.0, 10)
    assert "_가중치_다름(잣대[0][0].get(\"sentiment_weight_used\")" in __import__("inspect").getsource(sell_flags)


def test_팩터_구성이_다르면_비교하지_않고_같은_구성은_비율로_본다() -> None:
    """25.766 에서 공통 비율로 보다가 설정 변경을 놓쳐(교차검증) 25.769 에 구성 비교를 더했다."""
    import json

    from batch.jobs import sell_flags

    다섯 = json.dumps({"value": 20, "quality": 20, "growth": 20, "momentum": 20, "risk": 20})
    넷 = json.dumps({"value": 25, "quality": 25, "momentum": 25, "risk": 25})
    # 25.769: 구성이 다르면 설정이 같은지 알 수 없어 비교하지 않는다(거짓 재무악화 방지) — 25.766 의 "같음" 을 되돌렸다
    assert sell_flags._팩터가중치_다름(다섯, 넷)
    # 같은 구성이면 부동소수 차는 같다
    assert not sell_flags._팩터가중치_다름(다섯, json.dumps({"value": 20.0000001, "quality": 20, "growth": 20, "momentum": 20, "risk": 20}))
    바꿈 = json.dumps({"value": 40, "quality": 15, "growth": 15, "momentum": 15, "risk": 15})
    assert sell_flags._팩터가중치_다름(다섯, 바꿈)
    assert sell_flags._팩터가중치_다름("깨짐", 다섯)


def test_한쪽에만_있는_팩터의_가중치가_0이면_같은_구성으로_본다() -> None:
    """{50,50,0,0,0} 에서 성장 결측 유무만 다르면 같은 잣대다 (docs/infra.md 25.773, 교차검증)."""
    import json

    from batch.jobs import sell_flags

    넷 = json.dumps({"value": 50, "quality": 50, "momentum": 0, "risk": 0})
    다섯 = json.dumps({"value": 50, "quality": 50, "growth": 0, "momentum": 0, "risk": 0})
    assert not sell_flags._팩터가중치_다름(넷, 다섯)
    assert "and r[\"score_now\"] is not None:" in __import__("inspect").getsource(sell_flags)


def test_새_기사가_끊기면_옛_평균을_오늘_감성으로_쓰지_않는다() -> None:
    """수집 대상에서 빠진 종목의 −60 이 30일 동안 오늘 감성으로 쓰였다 (docs/infra.md 25.832, 감사)."""
    옛 = [art(d, -0.6) for d in (8, 9, 10, 11, 12)]
    assert st.aggregate(옛, NOW).sentiment is None and st.aggregate(옛, NOW).article_count == 5
    # 가장 새 기사가 7일 안이면 그대로
    assert st.aggregate([art(6.5, -0.6)] + 옛[1:], NOW).sentiment is not None
    assert st.NEWEST_MAX_DAYS == 7


def test_가장_새_기사_7일_경계() -> None:
    """정확히 7일이면 묵음, 그보다 조금 새면 값 (25.838, 교차검증 — 경계 변이가 살아남았다)."""
    다섯 = [art(d, -0.6) for d in (8, 9, 10, 11)]
    assert st.aggregate([art(7, -0.6)] + 다섯, NOW).sentiment is None
    assert st.aggregate([art(6.99, -0.6)] + 다섯, NOW).sentiment is not None


def test_조용한_틈이_있어도_7일_변화가_거짓_급락을_만들지_않는다() -> None:
    """15~25일 전 −0.6 여섯 건, 8~14일 전 공백, 최근 7일 −0.6 다섯 건 — 예전 규칙이면 7일 변화 0 (25.838, 교차검증)."""
    old = [art(d, -0.6) for d in (15, 17, 19, 21, 23, 25)]
    new = [art(d, -0.6) for d in (0.5, 1, 2, 3, 4)]
    d7, _ = st.deltas(old + new, NOW)
    assert d7 == pytest.approx(0.0)  # 과거 점수가 NULL 이 되어 중립 0 출발로 −60 이 되지 않는다
