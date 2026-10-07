"""팩터 IC 계산 (batch/services/factor_ic.py, docs/infra.md 25.435).

손으로 셀 수 있는 고정 데이터로 검증한다(CLAUDE.md 계산 로직 규칙).
"""

from __future__ import annotations

import math

import pytest

from batch.services import factor_ic as fic


class Test스피어만:
    def test_순위가_같으면_1_반대면_마이너스_1(self) -> None:
        assert fic.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
        assert fic.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_손으로_센_값(self) -> None:
        # 순위 x=(1,2,3,4,5), y=(2,1,4,3,5). d²=1+1+1+1+0=4 → 1 − 6·4/(5·24) = 0.8
        assert fic.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)

    def test_동점은_평균_순위(self) -> None:
        # x 순위 (1.5, 1.5, 3), y 순위 (1, 2, 3)
        got = fic.spearman([1, 1, 2], [1, 2, 3])
        rx, ry = [1.5, 1.5, 3], [1, 2, 3]
        mx, my = 2, 2
        want = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / math.sqrt(
            sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)
        )
        assert got == pytest.approx(want)

    def test_한쪽이_모두_같으면_없다(self) -> None:
        assert fic.spearman([1, 1, 1], [1, 2, 3]) is None
        assert fic.spearman([1], [1]) is None


class Test한_달:
    def test_표본이_모자라면_없다(self) -> None:
        scores = {i: float(i) for i in range(fic.MIN_STOCKS - 1)}
        assert fic.period_ic(scores, {i: float(i) for i in scores}) is None

    def test_둘_다_있는_종목만_쓴다(self) -> None:
        scores: dict[int, float | None] = {i: float(i) for i in range(fic.MIN_STOCKS)}
        scores[999] = None
        rets: dict[int, float | None] = {i: float(i) for i in range(fic.MIN_STOCKS)}
        rets[998] = 0.5
        assert fic.period_ic(scores, rets) == pytest.approx(1.0)


class Test요약:
    def test_평균_표준편차_t(self) -> None:
        s = fic.summarize([0.1, 0.3, None, 0.2])
        assert s.months == 3
        assert s.mean == pytest.approx(0.2)
        assert s.std == pytest.approx(0.1)
        assert s.t_stat == pytest.approx(0.2 / fic.newey_west_se([0.1, 0.3, 0.2]))
        assert s.first_half_mean == pytest.approx(0.1) and s.second_half_mean == pytest.approx(0.25)

    def test_켜는_기준은_36개월_비율60_t2_두_반_모두_양(self) -> None:
        비율 = [0.9] * 36
        좋음 = fic.summarize([0.05, 0.03] * 18, 비율)  # 36개월, 평균 0.04, 표준편차 작음
        assert 좋음.passes() and 좋음.verdict() == "통과"
        assert not fic.summarize([0.05, 0.03] * 17, 비율[:34]).passes()  # 34개월
        뒤가_나쁨 = fic.summarize([0.2] * 18 + [-0.01, -0.02] * 9, 비율)
        assert not 뒤가_나쁨.passes() and 뒤가_나쁨.verdict() == "탈락"

    def test_창이_짧거나_표본이_성기면_판정_불가(self) -> None:
        """탈락과 다르다 — "재 봤는데 없다" 가 아니라 "말할 수 없다" (docs/infra.md 25.442, 교차검증)."""
        assert fic.summarize([0.05, 0.03] * 15, [0.9] * 30).verdict().startswith("판정 불가(IC 30개월")
        성김 = fic.summarize([0.05, 0.03] * 18, [0.09] * 36)  # 업종 모멘텀처럼 9% 만 값이 있다
        assert not 성김.passes() and 성김.verdict().startswith("판정 불가(표본 비율")
        assert fic.summarize([0.05, 0.03] * 18).verdict().startswith("판정 불가(표본 비율 모름")

    def test_NW_표준오차는_시차_0_이면_보통의_식(self) -> None:
        xs = [0.1, 0.3, 0.2, 0.4]
        m = sum(xs) / 4
        보통 = math.sqrt(sum((x - m) ** 2 for x in xs) / 4 / 4)
        assert fic.newey_west_se(xs, lags=0) == pytest.approx(보통)

    def test_이어지는_IC_는_t_가_줄어든다(self) -> None:
        """느리게 변하는 IC(양의 자기상관)에서 보통 t 는 부푼다 — NW 가 줄인다."""
        이어짐 = [0.05, 0.06, 0.07, 0.06, 0.05, 0.0, -0.01, 0.0, 0.01, 0.02] * 4
        s = fic.summarize(이어짐, [0.9] * 40)
        m = sum(이어짐) / len(이어짐)
        보통_t = m / (s.std / math.sqrt(len(이어짐)))
        assert s.t_stat < 보통_t and s.autocorr is not None and s.autocorr > 0

    def test_비어도_터지지_않는다(self) -> None:
        s = fic.summarize([None, None])
        assert s.months == 0 and s.mean is None and not s.passes()


class Test시장별_IC:
    """6회차 1 (docs/backtest.md 2.5, factors.md 12.8, docs/infra.md 25.810) — 시장을 섞으면 두 시장 사이 타이밍을 잰다."""

    def test_시장_수준_차이만_있는_지표는_시장_안에서_재면_0_에_가깝다(self) -> None:
        # KOSDAQ 이 이번 달 모두 더 올랐고, 지표도 KOSDAQ 이 모두 높다 — 시장 안에서는 순위가 수익과 무관(엇갈림)
        scores: dict[int, float | None] = {}
        rets: dict[int, float | None] = {}
        시장: dict[int, str | None] = {}
        for sid in range(60):
            kosdaq = sid >= 30
            scores[sid] = (10.0 if kosdaq else 0.0) + (sid % 30) * 0.01
            rets[sid] = (0.10 if kosdaq else 0.0) + (0.001 if sid % 2 else -0.001)
            시장[sid] = "KOSDAQ" if kosdaq else "KOSPI"
        섞음 = fic.period_ic_n(scores, rets)[0]
        합침, 쓴수, 갈래 = fic.period_ic_by_market(scores, rets, 시장)
        assert 섞음 is not None and 섞음 > 0.5  # 섞으면 시장 타이밍이 IC 로 보인다
        assert 합침 is not None and abs(합침) < 0.2 and 쓴수 == 60 and set(갈래) == {"KOSDAQ", "KOSPI"}

    def test_30_미만인_시장은_빼고_남은_시장의_종목_수로_가중(self) -> None:
        scores = {sid: float(sid) for sid in range(45)}
        rets: dict[int, float | None] = {sid: float(sid) for sid in range(45)}
        시장 = {sid: ("KOSPI" if sid < 35 else "KOSDAQ") for sid in range(45)}
        합침, 쓴수, 갈래 = fic.period_ic_by_market(scores, rets, 시장)
        assert 합침 == pytest.approx(1.0) and 쓴수 == 35  # 표본 비율 분자는 남은 시장만 — 분모는 부르는 쪽이 후보 전체로
        assert 갈래["KOSDAQ"] == (None, 10)

    def test_다중검정_기록은_묶음_크기를_고정하고_판정_불가는_p_1(self) -> None:
        좋음 = fic.summarize([0.05, 0.03] * 18, [0.9] * 36)
        모름 = fic.summarize([0.05] * 10, [0.9] * 10)
        이름 = ("a", "b", "c", "d")
        rec = fic.bh_record({"a": 좋음, "b": 모름}, 이름)
        assert rec["b"]["p"] == 1.0 and rec["c"]["p"] == 1.0
        assert rec["a"]["p"] == pytest.approx(fic.one_sided_p(좋음.t_stat))
        assert rec["a"]["q"] == pytest.approx(min(1.0, rec["a"]["p"] * 4))  # 가장 작은 p 는 m/1 배
        # 판정에는 쓰지 않는다
        assert 좋음.verdict() == "통과"


class Test백테스트에서_팩터_IC:
    """리밸런스마다 전략과 같은 함수로 점수를 내고 다음 리밸런스까지 수익률과 견준다 (docs/infra.md 25.435)."""

    def test_점수가_수익을_그대로_맞히면_IC_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from batch.jobs import backtest as job

        ids = list(range(1, 41))
        dates = ["2026-01-02", "2026-01-05", "2026-02-02", "2026-02-03", "2026-03-02"]
        # 종목 번호가 클수록 다음 달 더 오른다
        prices = {sid: {"2026-01-02": 100.0, "2026-01-05": 100.0, "2026-02-02": 100.0 + sid,
                        "2026-02-03": 100.0 + sid, "2026-03-02": (100.0 + sid) * (1 + sid / 100)} for sid in ids}
        universe = [{"stock_id": sid} for sid in ids]
        monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
        monkeypatch.setattr(
            job.sc, "score_factors",
            lambda inputs: [SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs]
            + [SimpleNamespace(stock_id=i.stock_id, factor="value", score=float(-i.stock_id)) for i in inputs],
        )  # fmt: skip
        got = job.factor_ics(universe, {}, prices, dates, ["2026-02-02", "2026-03-02"], {"momentum": 50, "value": 50})
        assert got["momentum"].months == 1 and got["momentum"].mean == pytest.approx(1.0)
        assert got["value"].mean == pytest.approx(-1.0)
        log = job.ic_log(got)
        assert log["momentum"]["mean"] == 1.0 and log["momentum"]["passes"] is False  # 36개월 미만
        assert log["momentum"]["coverage"] == 1.0 and log["momentum"]["verdict"].startswith("판정 불가(IC")
        # 시장별 요약과 섞은 IC(참고)가 함께 남는다. 발굴 루프 이름에만 다중검정 기록 (25.810)
        assert log["momentum"]["by_market"]["KOSPI"]["months"] == 1 and log["momentum"]["mixed_mean"] == 1.0
        assert "fdr" not in log["momentum"] and log["reversal_1m"]["fdr"]["note"].startswith("기록용")
        assert len(job.LOOP_IC_NAMES) == 11

    def test_판정_계열은_시장_안_IC_이고_섞은_IC_는_참고다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """두 시장(각 35종목). 점수·수익이 시장 수준만 같이 움직이고 시장 안에서는 무관하다 (25.810, 교차검증 — 한 시장 데이터로는
        시장별 IC 와 섞은 IC 가 같아 되돌려도 아무 검사도 실패하지 않았다)."""
        from types import SimpleNamespace

        from batch.jobs import backtest as job

        ids = list(range(1, 71))
        시장 = {sid: ("KOSDAQ" if sid > 35 else "KOSPI") for sid in ids}
        dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
        # KOSDAQ 이 10% 더 올랐고, 시장 안에서는 홀짝으로 엇갈린다(점수 순서와 무관)
        prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-02-27": 100.0,
                        "2026-03-02": 100.0 * (1.1 if 시장[sid] == "KOSDAQ" else 1.0) * (1.001 if sid % 2 else 0.999)}
                  for sid in ids}  # fmt: skip
        monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
            SimpleNamespace(stock_id=r["stock_id"], market=시장[r["stock_id"]], sector=None, metrics={}) for r in rows])
        monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
            SimpleNamespace(stock_id=i.stock_id, factor="momentum",
                            score=(10.0 if 시장[i.stock_id] == "KOSDAQ" else 0.0) + (i.stock_id % 35) * 0.01)
            for i in inputs])  # fmt: skip
        got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-02-02", "2026-03-02"], {})
        m = got["momentum"]
        assert m.mixed is not None and m.mixed.mean is not None and m.mixed.mean > 0.5  # 섞으면 시장 타이밍
        assert m.mean is not None and abs(m.mean) < 0.2  # 판정 계열은 시장 안
        assert {이름 for 이름, _, _ in m.by_market} == {"KOSDAQ", "KOSPI"}

    def test_30_미만_시장은_빠지고_표본_비율_분모에는_남는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from batch.jobs import backtest as job

        ids = list(range(1, 46))
        시장 = {sid: ("KOSDAQ" if sid > 35 else "KOSPI") for sid in ids}  # KOSDAQ 10종목
        dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
        prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-02-27": 100.0, "2026-03-02": 100.0 + sid}
                  for sid in ids}  # fmt: skip
        monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
            SimpleNamespace(stock_id=r["stock_id"], market=시장[r["stock_id"]], sector=None, metrics={}) for r in rows])
        monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
            SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
        got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-02-02", "2026-03-02"], {})
        m = got["momentum"]
        assert m.coverage == pytest.approx(35 / 45)  # 뺀 시장도 분모에
        assert dict((이름, 뺌) for 이름, _, 뺌 in m.by_market) == {"KOSDAQ": 1, "KOSPI": 0}

    def test_다음_날_가격이_없는_종목은_마지막_종가로(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """거래정지로 다음 리밸런스 날 가격이 없으면 예전에는 빠졌다 — simulate 는 묶어 둔다 (25.442)."""
        from types import SimpleNamespace

        from batch.jobs import backtest as job

        ids = list(range(1, 41))
        dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
        prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-02-27": 100.0 + sid, "2026-03-02": 100.0 + sid}
                  for sid in ids}  # fmt: skip
        # 점수 1등 종목은 월중 −60% 뒤 거래정지 — 다음 리밸런스 날 가격이 없다
        prices[40] = {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-02-27": 40.0}
        monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
            SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
        monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
            SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
        got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-02-02", "2026-03-02"], {})
        assert got["momentum"].months == 1 and got["momentum"].coverage == pytest.approx(1.0)  # 40종목 모두 썼다
        assert got["momentum"].mean < 1.0  # 정지된 1등이 −60% 로 들어가 완벽한 상관이 아니다


# 실행 기록에 IC 가 남는지·진행 칸이 IC 단계를 세는지는 소스 글자로 보던 것을 `tests/test_backtest_run_harness.py` 가
# `run` 을 실제로 돌려 본다 (docs/infra.md 25.460·25.462)
