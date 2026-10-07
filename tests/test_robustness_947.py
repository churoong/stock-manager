"""가중치 흔들기 (docs/reports.md 3.2, docs/infra.md 25.947) — 추천이 데이터가 고른 것인지 설정값이 고른 것인지."""

from __future__ import annotations

from typing import Any

from batch.notify import report_sections as rs
from batch.services import report_picks as rp
from batch.services import robustness as rb
from batch.services import scoring

EVEN = {f: 20.0 for f in scoring.FACTORS}


def row(stock_id: int, factors: dict[str, float], sentiment: float | None = None, **kw: Any) -> rp.SignalRow:
    total = scoring.total_score(factors, EVEN, sentiment, 10.0 if sentiment is not None else 0.0).total
    base: dict[str, Any] = {
        "stock_id": stock_id,
        "ticker": f"{stock_id:06d}",
        "name": f"종목{stock_id}",
        "market": "KOSPI",
        "horizon": "mid",
        "signal_type": "실적 모멘텀",
        "currency": "KRW",
        "buy_zone_low": 9000.0,
        "buy_zone_high": 10000.0,
        "suggested_weight_pct": 10.0,
        "suggested_amount": 1_000_000.0,
        "size_reduction": 1.0,
        "rationale_text": "",
        "as_of_date": "2026-10-02",
        "total_score": total,
        "factor_scores": factors,
        "sentiment": sentiment,
        "criteria": [{"label": "매출 증가", "display": "18.2%", "threshold": "> 0", "source": "dart", "passed": True}],
    }
    base.update(kw)
    return rp.SignalRow(**base)


def flat(v: float) -> dict[str, float]:
    return {f: v for f in scoring.FACTORS}


class Test세계:
    def test_균등_가중치면_열여섯_세계이고_합은_늘_100(self) -> None:
        ws = rb.worlds(EVEN, 10.0)
        assert len(ws) == 16
        for w in ws:
            assert abs(sum(w.weights.values()) - 100) < 1e-9
        밸류_올림 = next(w for w in ws if w.name == "밸류 +10%p")
        assert abs(밸류_올림.weights["value"] - 30) < 1e-9
        # 나머지 넷은 비율대로 70 을 나눈다
        assert abs(밸류_올림.weights["quality"] - 17.5) < 1e-9
        assert next(w for w in ws if w.name == "센티먼트 끔").sentiment_weight == 0.0

    def test_설정값과_같은_세계는_세지_않는다(self) -> None:
        # 모멘텀이 이미 0 이면 "모멘텀 −10%p" 와 "모멘텀 빼기" 는 설정값 그대로다. 센티먼트 0 이면 "끔" 도 없다
        w = {**EVEN, "momentum": 0.0}
        names = [x.name for x in rb.worlds(w, 0.0)]
        assert "모멘텀 −10%p" not in names and "모멘텀 빼기" not in names and "센티먼트 끔" not in names
        assert len(names) == 13

    def test_앞의_세계와_같은_세계도_세지_않는다(self) -> None:
        # 운영 설정(2026-10-04) 25·25·20·20·10 — 안정성 −10%p 는 안정성 0, 곧 "안정성 빼기" 와 같은 세계다.
        # 열다섯이어야 한다
        ws = rb.worlds({"value": 25, "quality": 25, "growth": 20, "momentum": 20, "risk": 10}, 10.0)
        names = [w.name for w in ws]
        assert len(ws) == 15 and "안정성 −10%p" in names and "안정성 빼기" not in names
        벡터 = [(tuple(round(w.weights[f], 9) for f in scoring.FACTORS), w.sentiment_weight) for w in ws]
        assert len(set(벡터)) == len(벡터)

    def test_정규화하지_않은_가중치도_받는다(self) -> None:
        ws = rb.worlds({f: 2.0 for f in scoring.FACTORS}, 10.0)  # 합 10 — 설정 화면은 합 100 을 강제하지만 복구 경로는 아니다
        assert len(ws) == 16 and abs(ws[0].weights["value"] - 30) < 1e-9


class Test흔들기:
    def test_어떻게_흔들어도_남는_종목과_한_팩터에_기대는_종목을_가른다(self) -> None:
        # 1: 모든 팩터 90 — 데이터가 고른 종목. 2: 밸류만 100 나머지 40 — 평균 52 로 간신히 5위권.
        # 3~6: 고르게 60. 7: 고르게 50. 상위 5 = {1, 3, 4, 5, 6} … 2 는 52 로 7(50) 바로 위, 3~6(60) 아래라 6위.
        # 2 가 5위권에 들려면 밸류를 올려야 한다 — 그래서 2 대신 7 과 겨루는 자리를 만든다
        rows = [
            row(1, flat(90)),
            row(2, {"value": 100, "quality": 40, "growth": 40, "momentum": 40, "risk": 40}),
            row(3, flat(60)),
            row(4, flat(60)),
            row(5, flat(60)),
            row(6, flat(51)),
            row(7, flat(50)),
        ]
        chosen = {r.stock_id for r in rp.select_top(rows)}
        assert chosen == {1, 2, 3, 4, 5}  # 2 는 52 로 6(51) 을 간신히 이긴다
        shaken = rb.shake(rows, EVEN, 0.0, n=5, chosen=chosen)
        assert shaken is not None and shaken.total == 15  # 센티먼트 가중치 0 — "끔" 세계 없음
        assert shaken.by_stock[1].kept == 15 and shaken.by_stock[1].dropped_by == []
        둘 = shaken.by_stock[2]
        assert 둘.kept < 15
        # 밸류를 줄이거나 빼면 빠진다. 밸류를 올리면 남는다
        assert "밸류 −10%p" in 둘.dropped_by and "밸류 빼기" in 둘.dropped_by
        assert "밸류 +10%p" not in 둘.dropped_by
        assert 둘.score_low is not None and 둘.score_high is not None and 둘.score_low < 52 < 둘.score_high
        # 6 은 그 세계들에서 들어온다 — 그림자 후보
        assert shaken.entrants and shaken.entrants[0] == ("종목6", 15 - 둘.kept)
        assert all(st.base_matches for st in shaken.by_stock.values())

    def test_후보가_다섯_이하면_뜻이_없어_None(self) -> None:
        rows = [row(i, flat(60 + i)) for i in range(1, 6)]
        assert rb.shake(rows, EVEN, 10.0, n=5, chosen={1, 2, 3, 4, 5}) is None

    def test_저장된_점수가_지금_가중치와_안_맞으면_표시한다(self) -> None:
        rows = [row(i, flat(60 + i)) for i in range(1, 8)]
        rows[6].total_score = 95.0  # 점수 계산 뒤 가중치가 바뀌어 저장값이 다른 경우를 흉내
        shaken = rb.shake(rows, EVEN, 0.0, n=5, chosen={7, 6, 5, 4, 3})
        assert shaken is not None and shaken.by_stock[7].base_matches is False
        assert "가중치가 점수 계산 뒤 바뀜" in (rb.stability_line(shaken.by_stock[7]) or "")

    def test_팩터_점수가_없는_옛_행은_점수_없음으로_뒤로_간다(self) -> None:
        rows = [row(i, flat(60 + i)) for i in range(1, 7)] + [row(9, {}, total_score=99.0)]
        shaken = rb.shake(rows, EVEN, 0.0, n=5, chosen={9, 6, 5, 4, 3})
        assert shaken is not None and shaken.by_stock[9].kept == 0 and shaken.by_stock[9].base_matches is False


class Test글:
    def test_한_줄은_남은_수와_빠지는_세계를_적고_셋까지만(self) -> None:
        st = rb.Stability(kept=11, total=16, dropped_by=["밸류 −10%p", "밸류 빼기", "성장 +10%p", "센티먼트 끔", "퀄리티 빼기"])
        assert rb.stability_line(st) == "흔들기 11/16 유지 — 밸류 −10%p·밸류 빼기·성장 +10%p 외 2이면 빠짐"
        assert rb.stability_line(rb.Stability(kept=16, total=16)) == "흔들기 16/16 유지"
        assert rb.stability_line(None) is None
        assert rb.stability_line({"kept": "x"}) is None  # 깨진 payload 는 줄을 만들지 않는다

    def test_그림자_후보_줄(self) -> None:
        assert rb.entrants_line([("A", 4), ("B", 2), ("C", 1), ("D", 1)], 16) == (
            "가중치를 흔들면 들어오는 종목: A 4/16 · B 2/16 · C 1/16 외 1종목"
        )
        assert rb.entrants_line([], 16) is None

    def test_compose_가_가중치를_받으면_종목_줄과_1부_끝에_붙고_payload_에_남는다(self) -> None:
        rows = [row(1, flat(90)), row(2, {"value": 100, "quality": 40, "growth": 40, "momentum": 40, "risk": 40})] + [
            row(i, flat(60)) for i in (3, 4, 5)
        ] + [row(6, flat(51)), row(7, flat(50))]
        composed = rp.compose(rows, 0.0, "2026-10-02", weights=EVEN, sentiment_weight=0.0)
        assert "흔들기 15/15 유지" in composed.text
        assert "이면 빠짐" in composed.text
        assert "가중치를 흔들면 들어오는 종목: 종목6" in composed.text
        둘 = next(p for p in composed.picks if p.ticker == "000002")
        assert isinstance(둘.stability, dict) and 둘.stability["kept"] < 15 and "밸류 빼기" in 둘.stability["dropped_by"]
        # 가중치를 안 주면 전과 같다 — 옛 호출부·테스트
        전 = rp.compose(rows, 0.0, "2026-10-02")
        assert "흔들기" not in 전.text and all(p.stability is None for p in 전.picks)

    def test_옛_payload_에_stability_가_없어도_StockPick_은_만들어진다(self) -> None:
        pick = rs.StockPick(ticker="A", name="a", market="KOSPI", horizon="mid")
        assert pick.stability is None and "흔들기" not in rs.render_picks([pick])
