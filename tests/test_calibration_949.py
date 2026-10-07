"""점수 보정표 (docs/signals.md 10.1, docs/infra.md 25.949) — 종합 점수가 실제 수익과 같은 방향인가."""

from __future__ import annotations

from batch.services import calibration as cb
from batch.services import outcomes as oc


class Test순위상관:
    def test_손으로_센_값(self) -> None:
        # 순위 (1,2,3,4,5) vs (1,2,3,5,4): d² 합 2 → ρ = 1 − 6·2/(5·24) = 0.9
        assert abs(cb.spearman([10, 20, 30, 40, 50], [1, 2, 3, 5, 4]) - 0.9) < 1e-9
        assert cb.spearman([1, 2, 3], [3, 2, 1]) == -1.0

    def test_동률은_평균_순위(self) -> None:
        # x 에 동률 둘 — 평균 순위로 센다. 완전 단조면 1 에 못 미치되 양수
        r = cb.spearman([1, 2, 2, 3], [1, 2, 3, 4])
        assert r is not None and 0.9 < r < 1.0
        assert cb.spearman([5, 5, 5], [1, 2, 3]) is None  # 한쪽이 전부 같으면 순서가 없다


class Test보정표:
    def test_구간별_평균과_표본_하한(self) -> None:
        pairs = [(72.0, 0.03), (75.0, 0.01), (78.0, -0.02), (71.0, 0.04), (79.9, 0.02), (61.0, 0.01), (100.0, 0.1)]
        cal = cb.calibrate(pairs, 20)
        assert cal.n == 7 and [b.lo for b in cal.buckets] == [60, 70, 90]
        칠십 = cal.buckets[1]
        assert 칠십.n == 5 and abs(칠십.avg_ret - 0.016) < 1e-9 and abs(칠십.win_rate - 0.8) < 1e-9
        assert cal.buckets[0].avg_ret is None  # 표본 1건 — 평균 안 냄
        assert cal.buckets[2].lo == 90 and cal.buckets[2].hi == 100  # 100점은 마지막 칸에
        assert cal.rho is None and "아직 셀 수 없다 (표본 7건" in cal.verdict

    def test_표본이_차면_상관과_판정(self) -> None:
        올라감 = [(float(s), s / 1000) for s in range(40, 100, 2)]  # 30건, 점수 높을수록 수익 높음
        cal = cb.calibrate(올라감, 60)
        assert cal.n == 30 and cal.rho is not None and cal.rho > 0.99
        assert cal.verdict.startswith("점수가 높을수록 수익이 높았다") and "단정하지 않음" in cal.verdict  # 30 ≤ n < 100
        많음 = [(float(s), s / 1000) for s in range(0, 100)] + [(50.0, 0.05)]  # 101건
        assert "단정하지 않음" not in cb.calibrate(많음, 60).verdict
        거꾸로 = [(float(s), -s / 1000) for s in range(40, 100, 2)]
        assert "낮았다" in cb.calibrate(거꾸로, 60).verdict
        # 무관 — 점수와 상관없는 톱니
        무관 = [(float(s), (0.01 if i % 2 else -0.01)) for i, s in enumerate(range(40, 100, 2))]
        판정 = cb.calibrate(무관, 60).verdict
        assert 판정.startswith("점수와 수익이 무관했다") or "낮았다" in 판정 or "높았다" in 판정  # 톱니는 ρ≈0

    def test_빈_짝(self) -> None:
        cal = cb.calibrate([], 20)
        assert cal.n == 0 and cal.buckets == [] and "아직 셀 수 없다" in cal.verdict


class Test짝짓기:
    def test_같은_종목_같은_날의_기간별_신호는_한_짝(self) -> None:
        outs = [
            oc.Outcome(1, "2026-09-16", "short", rets={5: 0.01, 20: 0.02, 60: None}),
            oc.Outcome(1, "2026-09-16", "mid", rets={5: 0.01, 20: 0.02, 60: None}),
            oc.Outcome(2, "2026-09-16", "mid", rets={5: 0.0, 20: None, 60: None}),  # 20일 창이 안 참
            oc.Outcome(3, "2026-09-17", "mid", rets={5: 0.0, 20: 0.05, 60: None}),  # 점수 없음
        ]
        scores = {(1, "2026-09-16"): 72.0, (2, "2026-09-16"): 60.0}
        assert cb.pairs_for(outs, scores, 20) == [(72.0, 0.02)]
        assert cb.pairs_for(outs, scores, 5) == [(72.0, 0.01), (60.0, 0.0)]


class Test글:
    def test_줄과_payload_가_같은_말(self) -> None:
        cal = cb.calibrate([(72.0, 0.03), (75.0, 0.01), (78.0, -0.02), (71.0, 0.04), (79.0, 0.02)], 20)
        줄 = cb.render_lines(cal)
        assert 줄[0].startswith("20일 뒤: 아직 셀 수 없다")
        assert 줄[1] == "  70~80점: 평균 +1.6% · 이긴 80% (n=5)"
        assert cb.render_lines(cal.as_payload()) == 줄
        assert cb.render_lines({"window": 5, "verdict": "x", "buckets": [{"lo": 0, "hi": 10, "n": 1, "avg_ret": None}]})[1] == (
            "  0~10점: 표본 1건 — 평균 안 냄"
        )


class Test상관을_못_낼_때:
    """25.977 — 표본이 찼는데 점수가 전부 같으면 "30건부터" 가 아니라 그 까닭을 적는다."""

    def test_점수가_전부_같으면(self) -> None:
        from batch.services import calibration as cal

        c = cal.calibrate([(50.0, 0.01 * i) for i in range(120)], 5)
        assert c.rho is None and c.verdict == "점수나 수익이 모두 같아 상관을 낼 수 없다 (n=120)"
        assert cal.calibrate([(50.0, 0.01)] * 5, 5).verdict.startswith("아직 셀 수 없다 (표본 5건")
