"""문턱과 계산식이 문서와 같은가 (`docs/signals.md` `docs/metrics.md` `docs/factors.md`).

**왜 있나.** CLAUDE.md 기록 규칙에 이렇게 적혀 있다 — "계산식은 **문서가 단일 정의처다**.
코드와 문서가 어긋나면 문서를 고치거나 코드를 고치되, **어긋난 채로 두지 않는다**."
그런데 2026-09-20 까지 그것을 **검사하는 장치가 없었다.**

이 규칙이 왜 강한 규칙인가: 문턱을 바꾸는 일은 잦다. `MIN_QUALITY_SCORE_LONG` 은
2026-09-17 에 60 → 55 로 내렸다(백테스트 결과, 사용자 결정). 그때 문서도 같이 고쳤다.
**다음에도 그럴 거라는 보장이 없다.** 코드만 고치면 문서는 조용히 거짓말을 시작하고,
사람은 문서를 믿고 판단한다. 같은 일이 `CLAUDE.md` 에서 실제로 일어났다 —
Alembic 을 안 쓰기로 한 지 넉 달이 되도록 "Alembic 으로 관리" 라고 적혀 있었다(25.36).

**방법**: 코드 상수로 문장을 만들어 문서에서 **그대로** 찾는다. 상수를 바꾸면 찾는
문장이 달라지고, 문서를 안 고쳤으면 여기서 깨진다. 문서의 숫자를 읽어 와 비교하는
방식은 쓰지 않는다 — 파싱이 느슨해서, 문서가 두 곳에서 다른 숫자를 말해도 통과한다.

**한계**: 문서에 적힌 것과 코드가 **하는 일**이 같은지는 증명하지 못한다. 그건 각
계산의 단위 테스트 몫이다. 여기서 막는 것은 "숫자만 갈아 끼우고 문서를 잊는" 일이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.services import adjust as adj
from batch.services import buyback as bb
from batch.services import factor_ic as fic
from batch.services import metrics as m
from batch.services import outcomes as oc
from batch.services import quarterly_earnings as qe
from batch.services import scoring as sc
from batch.services import sentiment as se
from batch.services import share_issuance as si
from batch.services import signals as sg
from batch.services import stress as st
from batch.services import trend
from batch.services import valuation_band as vb

문서 = Path(__file__).resolve().parent.parent / "docs"


def 읽기(이름: str) -> str:
    본문 = (문서 / 이름).read_text(encoding="utf-8")
    assert len(본문) > 2000, f"{이름} 을 제대로 읽지 못했다"
    return 본문


SIGNALS = 읽기("signals.md")
METRICS = 읽기("metrics.md")
FACTORS = 읽기("factors.md")
REPORTS = 읽기("reports.md")
HEALTH = 읽기("health.md")
# 2026-09-23 에 더한 이웃들 (docs/infra.md 25.148)
ADJUST = 읽기("adjust.md")
SENTIMENT = 읽기("sentiment.md")
STRESS = 읽기("stress.md")
DETAIL = 읽기("stock_detail.md")
SELL_FLAGS = 읽기("sell_flags.md")
BACKTEST = 읽기("backtest.md")
ETF = 읽기("etf.md")
ANALYSIS = 읽기("analysis.md")
INTRADAY = 읽기("intraday.md")
BROKERS = 읽기("brokers.md")
REACTION = 읽기("disclosure_reaction.md")


def 검사(문서본문: str, 찾을것: str, 무엇: str) -> None:
    assert 찾을것 in 문서본문, (
        f"{무엇}: 문서에서 `{찾을것}` 를 찾지 못했다."
        " 코드를 고쳤으면 문서도 같은 커밋에서 고친다 (CLAUDE.md 기록 규칙)"
    )


class Test단기_신호:
    def test_이동평균_기간(self) -> None:
        검사(SIGNALS, f"MA{sg.MA_SHORT_DAYS} > MA{sg.MA_LONG_DAYS}", "정배열")
        검사(SIGNALS, f"close > MA{sg.MA_SHORT_DAYS}", "추세 위")

    def test_과열_문턱(self) -> None:
        검사(
            SIGNALS,
            f"close / MA{sg.MA_SHORT_DAYS} - 1 <= {sg.MAX_EXTENSION:.2f}",
            "과열 아님",
        )

    def test_수급_배수(self) -> None:
        검사(
            SIGNALS,
            f"{sg.MA_SHORT_DAYS}일 평균 거래대금 > {sg.MA_LONG_DAYS}일 평균 × {sg.TURNOVER_MULTIPLE}",
            "수급 유입",
        )

    def test_모멘텀_문턱(self) -> None:
        검사(SIGNALS, f"momentum >= {sg.MIN_MOMENTUM_SCORE:.0f}", "모멘텀 점수")


class Test중기_신호:
    def test_성장_문턱(self) -> None:
        검사(SIGNALS, f"growth >= {sg.MIN_GROWTH_SCORE:.0f}", "성장 점수")

    def test_최소_품질(self) -> None:
        검사(SIGNALS, f"quality >= {sg.MIN_QUALITY_SCORE_MID:.0f}", "중기 최소 품질")


class Test장기_신호:
    def test_밴드_분위(self) -> None:
        검사(SIGNALS, f"PBR 분포의 {sg.BAND_ENTRY_PERCENTILE}% 분위", "밴드 하단")

    def test_밴드_창과_손절_분위(self) -> None:
        """2026-09-22 에 더했다 (docs/infra.md 25.108).

        진입 분위만 문서와 대 보고 있었다. **창(3년)과 손절 분위(20%)는 안 봤다** —
        권장 매수 구간의 하단이 그 손절 분위다. 그물이 한 방향만 본 것이다.
        """
        검사(SIGNALS, f"과거 {sg.BAND_YEARS}년 PBR 분포", "밴드 창")
        검사(SIGNALS, f"밴드 {sg.BAND_STOP_PERCENTILE}% 분위 가격", "장기 구간 하단")
        검사(SIGNALS, f"밴드 {sg.BAND_ENTRY_PERCENTILE}% 분위 가격", "장기 구간 상단")

    def test_퀄리티와_밸류_문턱(self) -> None:
        # 2026-09-17 에 60 → 55 로 내린 자리다. 다음에 또 움직이면 문서가 따라와야 한다
        검사(SIGNALS, f"quality >= {sg.MIN_QUALITY_SCORE_LONG:.0f}", "장기 퀄리티")
        검사(SIGNALS, f"value >= {sg.MIN_VALUE_SCORE_LONG:.0f}", "장기 밸류")


class Test권장_매수_구간:
    """구간의 폭에 **이름이 생겼다** (2026-09-22, docs/infra.md 25.109).

    전에는 `ma20 * 1.02` 처럼 글자로만 있어서, 코드를 고쳐도 이 검사가 만들 문장이
    없었다 — 문서가 조용히 거짓말을 시작해도 아무 일이 없었다.
    """

    def test_단기_구간(self) -> None:
        검사(SIGNALS, f"min(현재가, MA{sg.MA_SHORT_DAYS} × {sg.SHORT_ZONE_HIGH})", "단기 상단")
        검사(SIGNALS, f"MA{sg.MA_SHORT_DAYS} × {sg.SHORT_ZONE_LOW}", "단기 하단")

    def test_중기_구간(self) -> None:
        검사(SIGNALS, f"현재가 × {sg.MID_ZONE_LOW}", "중기 하단")

    def test_중기_재무_신선도(self) -> None:
        검사(SIGNALS, f"{sg.STALE_ANNUAL_DAYS}일", "사업보고서 신선도 (25.660)")

    def test_근거가_없다는_사실을_적어_두었다(self) -> None:
        """**근거 없는 숫자를 근거 있는 것처럼 두지 않는다** (CLAUDE.md 기록 규칙).

        백테스트로 정한 값이 아니다. 그 사실이 코드에 남아 있어야 다음 사람이 재 본다.
        """
        본문 = (Path(__file__).resolve().parent.parent / "batch" / "services" / "signals.py").read_text("utf-8")
        자리 = 본문.split("SHORT_ZONE_HIGH", 1)[0][-900:]
        assert "[확인필요" in 자리, "구간 폭의 근거가 없다는 표시가 사라졌다"


class Test사이징:
    def test_기준_변동성과_MDD(self) -> None:
        검사(SIGNALS, f"연 {sg.TARGET_VOLATILITY * 100:.0f}%", "기준 변동성")
        검사(SIGNALS, f"| 기준 MDD | {sg.TARGET_MDD * 100:.0f}% |", "기준 MDD")

    def test_축소_하한(self) -> None:
        검사(SIGNALS, f"변동성, {sg.REDUCTION_FLOOR}, 1.0)", "변동성 배수 하한")
        검사(SIGNALS, f"MDD|, {sg.REDUCTION_FLOOR}, 1.0)", "낙폭 배수 하한")

    @pytest.mark.parametrize("차례", [0, 1, 2])
    def test_분할_비중(self, 차례: int) -> None:
        검사(
            SIGNALS,
            f"| {차례 + 1}차 | {sg.TRANCHE_RATIOS[차례] * 100:.0f}% |",
            f"{차례 + 1}차 분할",
        )

    def test_분할_비중의_합은_1이다(self) -> None:
        # 문서와 별개로 이것이 깨지면 권장 금액이 모자라거나 넘친다
        assert sum(sg.TRANCHE_RATIOS) == pytest.approx(1.0)


class Test목표와_손절:
    @pytest.mark.parametrize("horizon", sg.HORIZONS)
    def test_기간별_기본값(self, horizon: str) -> None:
        값 = sg.DEFAULT_TARGETS[horizon]
        줄 = (
            f"| {sg.HORIZON_LABEL[horizon]} | +{값['target_pct']:.0f}%"
            f" | {값['stop_pct']:.0f}% |"
        )
        검사(SIGNALS, 줄, f"{sg.HORIZON_LABEL[horizon]} 목표·손절")


class Test시장_추세_필터:
    def test_창과_배수(self) -> None:
        검사(SIGNALS, f"**{trend.SMA_DAYS} 거래일**", "추세 필터 창")
        검사(SIGNALS, f"기본 **{trend.DEFAULT_BEAR_FACTOR}**", "약세 배수")

    def test_모르는_경우(self) -> None:
        검사(
            SIGNALS,
            f"지수가 {trend.SMA_DAYS}일치 미만이거나 마지막 값이 {trend.MAX_STALE_DAYS}일 넘게",
            "국면 미판정 조건",
        )


class Test성과_지표:
    def test_연환산_계수(self) -> None:
        검사(METRICS, f"연환산 계수 {m.TRADING_DAYS_PER_YEAR}", "연환산 계수")
        검사(METRICS, f"σ_annual = σ_daily × √{m.TRADING_DAYS_PER_YEAR}", "연환산 변동성 식")
        검사(
            METRICS,
            f"rf_daily = (1 + rf_annual)^(1/{m.TRADING_DAYS_PER_YEAR}) - 1",
            "무위험수익률 일간 환산",
        )

    def test_스코어링도_같은_계수를_쓴다(self) -> None:
        # 두 모듈이 각자 상수를 들고 있다. 어긋나면 같은 종목이 화면마다 다른 값을 갖는다
        assert sc.TRADING_DAYS_PER_YEAR == m.TRADING_DAYS_PER_YEAR


class Test팩터_정규화:
    def test_비교_집단_최소_크기(self) -> None:
        검사(FACTORS, f"종목 수가 {sc.MIN_PEER_SIZE} 미만이면", "비교 집단 최소 크기")

    def test_윈저라이즈(self) -> None:
        검사(FACTORS, f"상·하위 {sc.WINSOR_PCT * 100:.0f}%", "윈저라이즈")
        검사(FACTORS, f"값이 **{sc.WINSOR_MIN_VALUES}개 미만**이면 안 자른다", "윈저라이즈 최소 값 수")

    def test_z_절단(self) -> None:
        검사(FACTORS, f"±{sc.Z_CLIP:.0f}σ", "z 절단")
        검사(FACTORS, f"z = +{sc.Z_CLIP:.0f} 을 100점", "z 절단의 뜻")


class Test모멘텀과_유동성:
    def test_12_1_창(self) -> None:
        검사(
            FACTORS,
            f"{sc.TRADING_DAYS_PER_YEAR} 거래일 전 대비 수익률, **최근 {sc.MOMENTUM_SKIP_DAYS} 거래일 제외**",
            "12-1 모멘텀",
        )

    def test_꾸준함_최소_표본(self) -> None:
        검사(FACTORS, f"{sc.CONSISTENCY_MIN_RETURNS}개 미만이면 NULL", "꾸준함 최소 표본")

    def test_amihud(self) -> None:
        검사(FACTORS, f"최근 {sc.AMIHUD_DAYS} 거래일", "Amihud 창")
        검사(FACTORS, f"{sc.AMIHUD_MIN_DAYS}일 미만이면 NULL", "Amihud 최소 표본")
        검사(
            FACTORS,
            f"거래대금 / {sc.AMIHUD_VALUE_UNIT // 100_000_000}억)",
            "Amihud 단위",
        )

    def test_피오트로스키_축소판(self) -> None:
        검사(
            FACTORS,
            f"{sc.PIOTROSKI_LITE_CONDITIONS}개 조건 중 참인 개수, 0~{sc.PIOTROSKI_LITE_CONDITIONS}",
            "피오트로스키 축소판",
        )


class Test모멘텀_창과_배당_연속성:
    """**손으로 고른 것만 보는 그물은 다음 상수를 놓친다** (docs/infra.md 25.147).

    아래 검사들은 2026-09-23 에 한꺼번에 더했다. 전부 전에도 문서에 있었고 코드와도
    맞았지만 **아무도 대조하지 않았다** — 다음에 숫자를 갈아 끼우면 조용히 갈라진다.
    """

    def test_모멘텀_창(self) -> None:
        검사(FACTORS, f"{sc.MOMENTUM_12M_DAYS} 거래일 전 대비 수익률", "12개월 창")
        검사(FACTORS, f"{sc.MOMENTUM_6M_DAYS} 거래일 전 대비", "6개월 창")
        검사(FACTORS, f"{sc.MOMENTUM_3M_DAYS} 거래일 전 대비", "3개월 창")

    def test_52주_고점(self) -> None:
        검사(FACTORS, f"max(최근 {sc.HIGH_52W_DAYS} 거래일 종가)", "52주 고점 창")

    def test_MAX_효과(self) -> None:
        검사(FACTORS, f"최근 {sc.MAX_RETURN_DAYS} 거래일 일간 수익률의 **최댓값**", "MAX 창")
        검사(FACTORS, f"일간 수익률이 **{sc.MAX_RETURN_MIN_DAYS}개 미만이면 NULL**", "MAX 최소 표본")

    def test_잔차_변동성(self) -> None:
        검사(FACTORS, f"창은 {sc.IDIO_WINDOW_DAYS} 거래일", "잔차 변동성 창")
        검사(FACTORS, f"수익률이 **{sc.IDIO_MIN_POINTS}개 미만이면 NULL**", "잔차 최소 표본")

    def test_배당_연속성(self) -> None:
        검사(FACTORS, f"최근 {sc.DIVIDEND_YEARS_WINDOW}개 사업연도 중", "배당 관측 창")
        검사(FACTORS, f"**{sc.DIVIDEND_MIN_YEARS}년 미만이면 NULL**", "배당 최소 연수")

    def test_SUE(self) -> None:
        검사(FACTORS, f"당분기 EPS − {sc.SUE_SEASON}분기 전 EPS", "SUE 계절 비교")
        검사(FACTORS, f"최근 {sc.SUE_MIN_CHANGES}개 분기의", "SUE 표본")


class Test밴드_창:
    """**"3년" 은 몇 거래일인가** (docs/infra.md 25.147).

    `docs/signals.md` 는 "최근 3년 PBR 계열" 이라고만 적고 있었다. 창을 500으로 줄여도
    문서는 그대로 "3년" 이다 — 코드의 `BAND_YEARS` 는 2로 바뀌는데 문서만 안 바뀐다.
    25.108 이 숫자를 한 곳으로 모았고, 여기서 그 숫자를 문서에 묶는다.
    """

    def test_창과_최소_표본이_문서에_있다(self) -> None:
        검사(SIGNALS, f"{sg.BAND_DAYS} 거래일이다", "밴드 창")
        검사(SIGNALS, f"**{sg.BAND_MIN_SAMPLE} 거래일 미만이면 밴드를 만들지 않는다**", "밴드 최소 표본")

    def test_사람에게_보여_줄_햇수가_창을_따라간다(self) -> None:
        """글과 창이 따로 놀면 화면만 거짓말한다 (25.108 이 고친 그 모양)."""
        assert sg.BAND_YEARS == sg.BAND_DAYS // 250


class Test거래일_간격:
    def test_구멍을_버리는_문턱(self) -> None:
        검사(METRICS, f"두 행이 {m.MAX_SESSION_GAP_DAYS}일", "거래일 간격 상한")
        검사(METRICS, f"창 시작일 뒤 **{m.COVER_START_SLACK_DAYS}일** 안에", "창 시작 커버 여유 (25.703)")


class Test가중치_흔들기:
    """리포트 1부의 흔들기 (docs/reports.md 3.2, docs/infra.md 25.947). 폭과 기본 세계 허용 오차."""

    def test_폭과_허용_오차(self) -> None:
        from batch.services import robustness as rb

        검사(REPORTS, f"팩터 하나를 ±{rb.STEP_PCT:.0f}%p", "흔들기 폭")
        검사(REPORTS, f"저장된 종합 점수와 {rb.BASE_TOLERANCE} 안에서", "기본 세계 허용 오차")


class Test데이터_열_점검:
    """조용히 썩는 열 (docs/health.md 7.1, docs/infra.md 25.948). 창·문턱·표본 하한."""

    def test_창과_문턱(self) -> None:
        from batch.services import column_rot as cr

        검사(HEALTH, f"최근 {cr.RECENT_DAYS}일을 그 전 {cr.BASELINE_DAYS}일과 견준다", "창")
        검사(HEALTH, f"최근 − 기준 > {cr.NULL_JUMP:.2f} ({cr.NULL_JUMP:.0%}p)", "빈 값 문턱")
        검사(HEALTH, f"최근 < 기준 × {cr.DISTINCT_DROP}", "고유값 문턱")
        검사(HEALTH, f"비지 않은 행이 {cr.MIN_ROWS}개 아래면 판단하지 않는다", "표본 하한")


class Test점수_보정표:
    """점수 보정표 (docs/signals.md 10.1, docs/infra.md 25.949). 구간 폭·표본 하한·무관 문턱."""

    def test_구간과_문턱(self) -> None:
        from batch.services import calibration as cb

        검사(SIGNALS, f"점수 {cb.BUCKET}점 폭", "구간 폭")
        검사(SIGNALS, f"구간 표본 {cb.MIN_BUCKET_N}건 미만이면 평균을 내지 않는다", "구간 표본 하한")
        검사(SIGNALS, f"표본 {cb.MIN_CORR_N}건 미만이면 \"아직 셀 수 없다\"", "상관 표본 하한")
        검사(SIGNALS, f"ρ > {cb.FLAT_RHO}", "무관 문턱")
        검사(SIGNALS, f"표본 {cb.CAUTION_N}건 미만이면 판정 뒤에 \"단정하지 않음\"", "단정 하한")


class Test자기_채점:
    """리포트 자기 채점 (docs/reports.md 3.4, docs/infra.md 25.951). 표본 하한과 창."""

    def test_하한과_창(self) -> None:
        from batch.services import self_grade as sg

        검사(REPORTS, f"표본 {sg.MIN_N}건 미만이면 내지 않는다", "자기 채점 표본 하한")
        검사(REPORTS, f"창       = {'·'.join(str(w) for w in sg.WINDOWS)} 거래일", "자기 채점 창")


class Test계좌별_ETF:
    """장기 적립 ETF 의 계좌별 순위 (docs/etf.md 11.1, docs/infra.md 25.966). IRP 한도와 미국 상장 순위."""

    def test_한도와_순위(self) -> None:
        from batch.services import etf_accounts as ea

        검사(ETF, f"**위험자산 {ea.IRP_RISK_LIMIT_PCT}% 한도**", "IRP 위험자산 한도")
        검사(ETF, f"미국 상장 핵심 ETF 는 일반계좌 {ea.US_TAXABLE_PRIORITY}순위", "미국 상장 일반계좌 순위")


class Test증권사_성적:
    """증권사 적중률 성적표 (docs/brokers.md, docs/infra.md 25.995)."""

    def test_기간과_표본(self) -> None:
        from batch.services import broker_stats as bs

        검사(BROKERS, f"기준가에서 {bs.FWD_DAYS}거래일 뒤 종가", "초과수익 기간")
        검사(BROKERS, f"기준일부터 {bs.TOUCH_DAYS}거래일 안에", "터치 기간")
        검사(BROKERS, f"**{bs.MIN_N}건 미만**", "표본 하한")
        검사(BROKERS, f"{bs.ACTION_TOLERANCE * 100:.0f}% 넘게 바뀜", "기업행위 문턱")
        웹 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "brokers.ts").read_text("utf-8")
        assert f"BROKER_MIN_N = {bs.MIN_N};" in 웹, "web/lib/brokers.ts 표본 하한이 배치와 다르다"


class Test공시_반응:
    """공시 반응 통계 (docs/disclosure_reaction.md, docs/infra.md 25.996)."""

    def test_기간과_표본(self) -> None:
        from batch.services import disclosure_reaction as dr

        검사(REACTION, f"**공시일부터 {dr.WINDOW_DAYS}거래일째 종가**", "반응 기간")
        검사(REACTION, f"**{dr.MIN_N}건 미만**", "표본 하한")
        검사(REACTION, f"{dr.ACTION_TOLERANCE * 100:.0f}% 넘게 바뀜", "기업행위 문턱")
        for _, label, _ in dr.TYPES:
            assert label in REACTION, f"docs/disclosure_reaction.md 2장에 유형 {label} 이 없다"
        웹 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "disclosureReaction.ts").read_text("utf-8")
        assert f"REACTION_MIN_N = {dr.MIN_N};" in 웹, "web/lib/disclosureReaction.ts 표본 하한이 배치와 다르다"

    def test_모으는_기간(self) -> None:
        from batch.jobs import disclosure_reaction as job

        검사(REACTION, f"지난 **{job.BACKFILL_DAYS}일**", "처음 모으는 기간")
        검사(REACTION, f"매일 지난 {job.DAILY_DAYS}일을 겹쳐", "평소 겹쳐 받는 기간")


class Test우연일_확률:
    """운인가 실력인가 (docs/backtest.md 9장, docs/infra.md 25.997)."""

    def test_상수(self) -> None:
        from batch.services import luck

        검사(BACKTEST, f"**{luck.MIN_MONTHS}개월 미만이면 판정하지 않는다**", "판정 최소 기간")
        검사(BACKTEST, f"γ = {luck.EULER_GAMMA:.4f}(오일러 상수)", "오일러 상수")


class Test사후_분석:
    """틀린 추천 사후 분석 (docs/reports.md 3.8, docs/infra.md 25.998)."""

    def test_상수(self) -> None:
        from batch.jobs import weekly_summary as ws
        from batch.services import postmortem as pm

        검사(REPORTS, f"**{pm.MIN_SECTOR_PEERS}개 미만**(`MIN_SECTOR_PEERS`)", "업종 표본 하한")
        검사(REPORTS, f"예로 {pm.MAX_EXAMPLES}건을 적는다", "예 수")
        검사(REPORTS, f"나쁜 순 {ws.POSTMORTEM_MAX}건까지만", "나누는 상한")
        검사(REPORTS, f"진입일이 {ws.POSTMORTEM_ENTRY_DAYS[0]}~{ws.POSTMORTEM_ENTRY_DAYS[1]}일 전", "창")


class Test종목_분석:
    """종목 분석 의견 (docs/analysis.md, docs/infra.md 25.1016)."""

    def test_상수(self) -> None:
        from batch.services import verdict as vd

        검사(ANALYSIS, f"지난 `RECENT_DISCLOSURE_DAYS`({vd.RECENT_DISCLOSURE_DAYS})일", "최근 공시 날 수")
        for 이름 in vd.VERDICTS.values():
            검사(ANALYSIS, f"**{이름}**", f"결론 {이름}")
        검사(ANALYSIS, f"지난 `CONSENSUS_DAYS`({vd.CONSENSUS_DAYS})일", "증권사 목표가를 모으는 날 수")
        검사(ANALYSIS, f"지난 최대 `MARKET_YEARS`({vd.MARKET_YEARS})년 연환산 수익률", "시장 기대수익률 창")
        검사(ANALYSIS, f"`FORECAST_MONTHS`({'·'.join(str(m) for m in vd.FORECAST_MONTHS)}개월)", "예상 주가 기간")
        검사(ANALYSIS, f"1 + `DROP_LEVEL`({vd.DROP_LEVEL:.2f})", "확률의 하락 폭")
        검사(ANALYSIS, f"`SKILLED_MIN_N`({vd.SKILLED_MIN_N})건 이상", "잘 맞힌 증권사 표본 하한")
        검사(ANALYSIS, f"`BAND_ENTRY_PERCENTILE`, {vd.BAND_ENTRY_PERCENTILE}% 지점", "밴드 문턱(신호의 값)")

    def test_비슷한_국면과_성적표(self) -> None:
        from batch.services import forecast_track as ft
        from batch.services import patterns as pt

        검사(ANALYSIS, f"(종가 ÷ {pt.R3_DAYS}거래일 전 − 1)", "3개월 수익률 창")
        검사(ANALYSIS, f"최근 {pt.HIGH_DAYS}거래일 최고 종가", "52주 고점 창")
        검사(ANALYSIS, f"`MIN_DAYS`({pt.MIN_DAYS})일 미만", "칸 최소 일수")
        검사(ANALYSIS, f"`WORST_MONTHS`({pt.WORST_MONTHS})개 달", "하락장 성적 달 수")
        검사(ANALYSIS, f"`MIN_MONTHS`({pt.MIN_MONTHS}) 미만", "하락장 성적 최소 달 수")
        검사(ANALYSIS, f"`REGIME_SMA_DAYS`({pt.REGIME_SMA_DAYS})거래일 단순이동평균", "시장 국면 이동평균 창")
        # 예상 주가 범위 (10.5, 25.1055)
        from batch.services import verdict as vd

        검사(ANALYSIS, f"`EWMA_DAYS`({pt.EWMA_DAYS})거래일", "EWMA 창")
        검사(ANALYSIS, f"감쇠 `EWMA_LAMBDA` {pt.EWMA_LAMBDA}", "EWMA 감쇠")
        검사(ANALYSIS, f"`EWMA_MIN`({pt.EWMA_MIN})개 미만", "EWMA 최소 수익률 수")
        검사(ANALYSIS, f"절반 켈리(`KELLY_SHOWN` {vd.KELLY_SHOWN})", "켈리 표시 몫")
        검사(ANALYSIS, f"반감기 `VOL_HALF_LIFE_MONTHS`({vd.VOL_HALF_LIFE_MONTHS:g})개월", "변동성 반감기")
        검사(ANALYSIS, f"z = {vd.FORECAST_Z['50']}(50%)·{vd.FORECAST_Z['68']:g}(68%)·{vd.FORECAST_Z['90']}(90%)", "범위 z")
        검사(ANALYSIS, f"({'·'.join(str(d) for d in pt.HORIZON_DAYS.values())}거래일)", "기간 → 거래일")
        검사(ANALYSIS, f"`MIN_SAMPLE`({ft.MIN_SAMPLE})건 미만", "성적표 최소 표본")

    def test_자기_시세_이력(self) -> None:
        from batch.services import history as hs

        검사(ANALYSIS, f"`DD_LEVELS`({'%·'.join(f'{x * 100:.0f}' for x in hs.DD_LEVELS)}%", "낙폭 단계")
        검사(ANALYSIS, f"`MIN_DAYS`({hs.MIN_DAYS})거래일 미만이면", "이력 최소 거래일")
        검사(ANALYSIS, f"`TAIL_P`({hs.TAIL_P * 100:.0f}%)", "꼬리 확률")
        검사(ANALYSIS, f"`MONTH_DAYS`({hs.MONTH_DAYS})거래일", "한 달 거래일")
        검사(ANALYSIS, f"`SEASON_MIN`({hs.SEASON_MIN})번 미만", "계절성 최소 표본")
        검사(ANALYSIS, f"`BREAKOUT_COOLDOWN`({hs.BREAKOUT_COOLDOWN})거래일", "신고가 식힘")
        검사(ANALYSIS, f"{'·'.join(str(m) for m in hs.BREAKOUT_MONTHS)}개월(`BREAKOUT_MONTHS`)", "신고가 뒤 기간")

    def test_재무_수급_사건(self) -> None:
        from batch.jobs import metrics as mj
        from batch.services import event_history as ev

        검사(ANALYSIS, f"`EVENT_YEARS`({mj.EVENT_YEARS})년치", "사건 이력 햇수")
        검사(ANALYSIS, f"`REACTION_DAYS`({'·'.join(str(x) for x in ev.REACTION_DAYS)})거래일", "발표 반응 거래일")
        검사(ANALYSIS, f"`REACTION_SHOW`({ev.REACTION_SHOW})번", "발표 반응 표시 수")
        검사(ANALYSIS, f"`SOURCE_YEARS`({ev.SOURCE_YEARS})년 전", "상승 출처 햇수")
        검사(ANALYSIS, f"`SURGE_BASE`({ev.SURGE_BASE})거래일 중앙값의 `SURGE_X`({ev.SURGE_X})배", "공매도 급증 정의")
        검사(ANALYSIS, f"`SURGE_COOLDOWN`({ev.SURGE_COOLDOWN})거래일", "공매도 급증 식힘")
        검사(ANALYSIS, f"`SURGE_DAYS`({'·'.join(str(x) for x in ev.SURGE_DAYS)})거래일", "공매도 급증 뒤 거래일")

    def test_해석_묶음(self) -> None:
        from batch.jobs import verdicts as vj
        from batch.services import insights as ins

        검사(ANALYSIS, f"`WEIGHT_MONTHS`({ins.WEIGHT_MONTHS})개월 성적", "성적 가중 합의 기간")
        검사(ANALYSIS, f"Z″ = {ins.Z2_COEF[0]}·(유동자산 − 유동부채)/자산 + {ins.Z2_COEF[1]}·이익잉여금/자산 + "
                       f"{ins.Z2_COEF[2]}·영업이익/자산 + {ins.Z2_COEF[3]}·자본/부채", "Altman Z″ 계수")
        검사(ANALYSIS, f"`Z2_SAFE`({ins.Z2_SAFE:.2f})", "Z″ 안전 경계")
        검사(ANALYSIS, f"`Z2_DISTRESS`({ins.Z2_DISTRESS:.2f})", "Z″ 위험 경계")
        검사(ANALYSIS, f"`SIGNAL_HISTORY_DAYS`({vj.SIGNAL_HISTORY_DAYS})일", "신호 성적 창")
        검사(ANALYSIS, f"`SIGNAL_GAP_DAYS`({ins.SIGNAL_GAP_DAYS})달력일", "신호 끊김 문턱")
        검사(ANALYSIS, f"`SIGNAL_SHOW`({ins.SIGNAL_SHOW})번", "신호 표시 수")
        검사(ANALYSIS, f"`TREND_POINTS`({'·'.join(str(x) for x in ins.TREND_POINTS)})일 전", "목표가 흐름 찍는 날")

        검사(ANALYSIS, f"석 달치(`FLOWS_CALENDAR_DAYS` {vj.FLOWS_CALENDAR_DAYS}일)", "수급 흐름 읽기 창")
        검사(ANALYSIS, f"`FLOW_WINDOWS`({'·'.join(str(w) for w in ins.FLOW_WINDOWS)})거래일", "수급 흐름 창")
        검사(ANALYSIS, f"`SCORE_CHANGE_DAYS`({ins.SCORE_CHANGE_DAYS})일 앞", "점수 변화 거리")
        검사(ANALYSIS, f"`TWINS`({ins.TWINS})종목", "닮은 종목 수")
        검사(ANALYSIS, f"`RADAR_N`({ins.RADAR_N})종목", "레이더 목록 길이")
        검사(ANALYSIS, f"`PEERS_TOP`({ins.PEERS_TOP})종목", "업종 상위 이름 수")
        검사(ANALYSIS, f"최근 `TREND_QUARTERS`({ins.TREND_QUARTERS}) 분기", "분기 추세 길이")
        검사(ANALYSIS, f"한 번에 최대 `MAX_EVAL_DATES`({vj.MAX_EVAL_DATES})일", "한 번에 평가하는 기록일")
        검사(ANALYSIS, f"최근 `LOGGED_KEEP`({vj.LOGGED_KEEP})일", "기록일 목록 길이")

    def test_참고_분석(self) -> None:
        from batch.jobs import analyze_extra as ax

        검사(ANALYSIS, f"`MAX_STOCKS`({ax.MAX_STOCKS})종목", "한 번에 분석하는 상한")
        검사(ANALYSIS, f"`HARD_REASONS`({'·'.join(ax.HARD_REASONS)})", "점수를 내지 않는 사유")


class Test세후_시뮬레이션:
    """계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, docs/infra.md 25.1003)."""

    def test_상수(self) -> None:
        from batch.services import tax_sim as ts

        검사(ETF, f"매달 **{ts.MONTHLY_KRW // 10_000}만원**(`MONTHLY_KRW`", "월 적립액")
        검사(ETF, f"**{ts.YEARS[0]}년·{ts.YEARS[1]}년**(`YEARS`)", "기간")
        검사(ETF, f"연 가격 수익 **{ts.PRICE_RETURN_PCT:.0f}%**(`PRICE_RETURN_PCT`)", "가정 가격 수익")
        검사(ETF, f"연 분배 **{ts.DIST_YIELD_PCT:.0f}%**(`DIST_YIELD_PCT`)", "가정 분배율")
        검사(ETF, f"기본공제 **{ts.US_GAIN_DEDUCTION_KRW // 10_000}만원**(`US_GAIN_DEDUCTION_KRW`", "양도 기본공제")
        검사(ETF, f"연 **{ts.PENSION_CREDIT_LIMIT_KRW // 10_000}만원**(`PENSION_CREDIT_LIMIT_KRW`)", "세액공제 한도")


class Test엇갈림:
    """점수 × 수급 × 증권사 의견 엇갈림 (docs/reports.md 3.9, docs/infra.md 25.1001)."""

    def test_상수(self) -> None:
        from batch.services import divergence as dv

        검사(REPORTS, f"최근 **{dv.FLOW_DAYS}거래일**(`FLOW_DAYS`)", "수급 기간")
        검사(REPORTS, f"**{dv.MIN_FLOW_DAYS}일 미만**(`MIN_FLOW_DAYS`)", "수급 최소 일수")
        검사(REPORTS, f"최근 **{dv.OPINION_DAYS}일**(`OPINION_DAYS`)", "의견 기간")
        검사(REPORTS, f"**{dv.TARGET_TOLERANCE * 100:.1f}%** 이하(`TARGET_TOLERANCE`)", "목표가 허용 오차")


class Test시간외:
    """보유 종목 시간외 단일가 알림 (docs/intraday.md 1.2, docs/infra.md 25.992)."""

    def test_기본_문턱(self) -> None:
        from batch.services import after_hours as ah

        검사(INTRADAY, f"`alert_thresholds.spike_pct`, 기본 {ah.DEFAULT_SPIKE_PCT:.0f}%", "시간외 기본 문턱")


class Test점수_담음:
    """우리 점수를 많이 담은 ETF (docs/etf.md 11.2·11.3, docs/infra.md 25.967). 덮은 비중 하한·기준 ETF·대리 표."""

    def test_하한과_기준(self) -> None:
        from batch.services import etf_tilt as et

        검사(ETF, f"**C 가 {et.COVERAGE_MIN_PCT:.0f}% 미만이면 A 를 내지 않는다**", "덮은 비중 하한")
        검사(ETF, f"미국: {et.MARKET_BENCHMARK}", "시장 대비 기준 ETF")
        검사(ETF, f"기여한 {et.TOP_CONTRIBUTORS}종목", "근거표 기여 종목 수")
        검사(ETF, f"상위 {et.TOP_SHARE_PCT:.0f}% (동점은 함께)", "상위 종목 범위")
        검사(ETF, f"주식 비중 ≥ {et.POOL_MIN_STOCK_POSITION}", "후보 풀 주식 비중")
        for cat in et.POOL_EXCLUDED_CATEGORIES:
            assert f"`{cat}`" in ETF, f"docs/etf.md 11.5 에 빼는 분류 {cat} 가 없다"
        assert et.POOL_MIN_ASSETS_USD == 100_000_000 and "순자산 ≥ 1억달러" in ETF, "후보 풀 순자산 하한이 문서와 다르다"
        assert et.POOL_MIN_ASSETS_KRW == 10_000_000_000 and "순자산 100억원 이상" in ETF, "국내 풀 순자산 하한이 문서와 다르다"
        for index, proxy in et.PROXY_BY_INDEX.items():
            assert index in ETF and f"| {proxy} |" in ETF, f"docs/etf.md 11.3 대리 표에 {index} → {proxy} 가 없다"


class Test추천_이력:
    """추천 이력 대비 띠 (docs/reports.md 3.3, docs/infra.md 25.963). 분할·병합을 대비로 적지 않는 띠."""

    def test_띠(self) -> None:
        from batch.services import pick_history as ph

        lo, hi = ph.RATIO_BAND
        검사(REPORTS, f"{lo} ≤ 오늘/첫 종가 ≤ {hi} 밖이면", "추천 이력 대비 띠")


class Test보유_점수:
    """보유 종목 점수 영수증 (docs/reports.md 3.6, docs/infra.md 25.953). 줄 상한."""

    def test_줄_상한(self) -> None:
        from batch.services import holding_scores as hsc

        검사(REPORTS, f"보유 {hsc.MAX_LINES}종목까지", "보유 점수 줄 상한")


class Test밸류_분모:
    """밸류 분모 시점 규칙 (docs/factors.md 3.1, docs/infra.md 25.954·25.960). 띠와 묵은 시총 문턱."""

    def test_띠와_묵음(self) -> None:
        검사(FACTORS, f"둘의 차이 ≤ {sc.MARKET_CAP_STALE_DAYS}일", "묵은 시총 문턱")
        lo, hi = sc.MARKET_CAP_BAND
        검사(FACTORS, f"{lo} ≤ 배수 ≤ {hi}", "배수 띠")


#: 상수를 문서와 대조하는 모듈. **넷에서 아홉으로 늘렸다** (docs/infra.md 25.148).
#:
#: 여기 없는 `services/*` 는 아래 `아직_안_보는_모듈` 에 사유와 함께 적는다 —
#: 빠뜨림과 판단을 가른다(25.76).
훑는_모듈 = (
    "signals", "scoring", "metrics", "trend", "adjust", "sentiment",
    "valuation_band", "outcomes", "stress", "sell_flags", "factor_ic", "sector_momentum", "earnings_quality", "share_issuance",
    "quarterly_earnings", "short_reversal", "div_omission", "buyback", "dilution", "flow_surge",
    "gross_profitability", "robustness", "column_rot", "calibration", "self_grade", "holding_scores",
    "pick_history", "etf_accounts", "etf_tilt", "after_hours", "broker_stats", "disclosure_reaction", "luck", "postmortem",
    "divergence", "tax_sim", "verdict", "forecast_track", "patterns", "insights", "history",
    "event_history",
)  # fmt: skip

#: 아직 그물 밖인 모듈과 **왜 아직인지**. 하나씩 줄여 간다
아직_안_보는_모듈 = {
    "backtest": "거래비용·리밸런스 수는 docs/backtest.md 가 표로 적고 있는데 문장 모양이"
    " 제각각이라 그대로 찾기 어렵다. 문서를 먼저 다듬고 묶는다 `[확인필요]`",
    "etf": "규모·유동성 문턱이 문서에서 '10억달러'·'1,000억원' 처럼 사람 단위로 적혀 있다."
    " 코드는 원 단위 정수라 문장을 그대로 만들 수 없다 — 표기를 맞춘 뒤 묶는다 `[확인필요]`",
    "etf_satellite": "위와 같은 이유(%와 배수 표기). 같이 다룬다",
    "accumulation": "게이트 문턱이 docs/accumulation.md 표에 있고 값도 맞지만,"
    " 표 칸이 길어 문장으로 잘라 내기 어렵다 `[확인필요]`",
    "portfolio": "실적 D-day 의 법정 기한(45·90일)은 계산식이 아니라 외부 규정이다."
    " docs/portfolio.md 가 글로 적고 있다",
    "review": "`MIN_SAMPLE` 은 이미 이 파일이 다른 검사에서 묶고 있다",
    "pit": "시점 조회라 판정 문턱이 없다. 미래 참조 금지는 docs/backtest.md 1.1 이 적는다",
    "factor_ab": "지표 교체 A/B 기록(25.903). 기준은 docs/factors.md 12.12 가 글로 적고 `rule_met` 테스트가 묶는다."
    " `MIN_PAIR_STOCKS` 는 `factor_ic.MIN_STOCKS` 를 그대로 쓴다",
    "pit_universe": "창·하한을 `services/universe` 와 같은 값으로 두고 docs/backtest.md 1.3 이 적는다",
    "universe": "제외 기준은 `UniverseFilters` 코드 상수이고 이 파일의 다른 검사가 docs/design.md 표와 대조한다",
    "reports": "리포트 서식뿐이다. 판정하는 숫자가 없다",
    "report_picks": "1부·2부를 고르는 규칙은 개수가 아니라 부분집합 규칙이고 테스트가 따로 지킨다",
    "adjust_drift": "재수집 대기열 크기는 운영값이고 docs/adjust.md 8장이 적는다",
    "insider": "근거표의 참고 행이라 점수·신호를 바꾸지 않는다",
    "fx": "묵음 일수는 웹과 묶여 있다 (docs/infra.md 25.136·25.140)",
    "sectors": "업종 코드 매핑이라 판정 문턱이 없다",
    "criteria": "근거표 한 행의 **모양**을 보는 곳이다. 문턱이 아니라 웹의 거르개와 맺은"
    " 약속이고, 그 약속은 `tests/test_criteria_contract.py` 가 웹 소스를 읽어 대조한다",
}

#: **문서에 적을 것이 아닌 상수.** 사유가 곧 판단의 기록이다 (docs/infra.md 25.147)
문서_불필요 = {
    "CALC_VERSION": "계산 판 번호다. 식이 아니라 이력이고, 판이 움직일 때 무엇을 고칠지는"
    " `tests/test_calc_version_pick.py` 가 따로 본다",
    "VOL_ANN_EPS": "0 으로 나누기를 막는 엡실론이다. 판정 문턱이 아니라 수치 안정용이라"
    " 바뀌어도 사람이 읽는 규칙이 달라지지 않는다",
}


def _상수들(모듈이름: str) -> list[str]:
    """그 모듈의 **대문자 숫자 상수** 이름들. 문자열·튜플·딕셔너리는 식의 문턱이 아니다."""
    import ast

    뿌리 = Path(__file__).resolve().parent.parent
    나무 = ast.parse((뿌리 / "batch" / "services" / f"{모듈이름}.py").read_text(encoding="utf-8"))
    이름들 = []
    for 마디 in 나무.body:
        if not isinstance(마디, ast.Assign):
            continue
        값 = 마디.value
        숫자 = (isinstance(값, ast.Constant) and isinstance(값.value, int | float)
                and not isinstance(값.value, bool)) or (
            isinstance(값, ast.UnaryOp) and isinstance(값.operand, ast.Constant)
        )  # fmt: skip
        if not 숫자:
            continue
        이름들 += [t.id for t in 마디.targets if isinstance(t, ast.Name) and t.id.isupper()]
    return 이름들


이_파일 = Path(__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("모듈", 훑는_모듈)
def test_문서와_대조하지_않는_상수가_없다(모듈: str) -> None:
    """**그물이 제 손으로 고른 것만 본다** (docs/infra.md 25.147).

    이 파일의 검사들은 전부 사람이 하나씩 적은 것이다. 그래서 **새 상수를 더하면
    아무도 물어 주지 않는다.** 2026-09-23 에 세어 보니 계산 모듈의 숫자 상수 50개 중
    19개가 이 파일에 이름조차 없었다 — 그중 `BAND_DAYS` 는 계산 문서 어디에도 없었다.
    (있는 것은 `docs/infra.md` 뿐인데 그것은 운영 기록이지 계산의 정의처가 아니다.)

    여기서 막는 것은 **다음번**이다. 상수를 더하면 둘 중 하나를 해야 한다 —
    문서와 묶는 검사를 쓰거나, `문서_불필요` 에 **왜 안 적는지**를 남기거나.
    """
    없는것 = [이름 for 이름 in _상수들(모듈) if 이름 not in 이_파일 and 이름 not in 문서_불필요]

    assert not 없는것, (
        f"`services/{모듈}` 의 숫자 상수가 문서와 대조되지 않는다: {없는것}\n"
        "계산식의 단일 정의처는 문서다 (CLAUDE.md 기록 규칙).\n"
        "문서에 적고 여기에 `검사(...)` 를 쓰거나, 적을 것이 아니면"
        " `문서_불필요` 에 사유와 함께 적어라"
    )


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 위 검사가 공짜로 통과한다."""
    전체 = sum(len(_상수들(m)) for m in 훑는_모듈)
    assert 전체 >= 30, f"계산 상수를 {전체}개밖에 못 찾았다 — 훑기가 고장 났나"


def test_사유가_비어_있지_않다() -> None:
    짧은것 = [이름 for 이름, 사유 in 문서_불필요.items() if len(사유.strip()) < 20]
    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


def test_예외_목록이_없는_상수를_가리키지_않는다() -> None:
    """**낡은 예외는 빠뜨린 것과 구별이 안 된다.**

    처음 쓸 때 `FACTOR_COUNT` 를 넣어 두었는데 그런 상수는 없었다. 그대로 뒀으면
    목록이 조용히 거짓말을 시작한다 — 25.131 의 보존 정책 목록과 같은 이유로 막는다.
    """
    있는것 = {이름 for 모듈 in 훑는_모듈 for 이름 in _상수들(모듈)}
    유령 = sorted(set(문서_불필요) - 있는것)

    assert not 유령, f"없는 상수를 예외에 적어 두었다: {유령}"


class Test이웃_모듈들:
    """**그물의 범위를 넷에서 아홉으로** (docs/infra.md 25.148).

    25.147 이 계산 모듈 넷의 상수를 전부 묶었다. 그런데 그 넷 밖에도 문턱을 든 모듈이
    여럿이고, 그중 `valuation_band.MIN_PEERS` 는 **계산 문서 어디에도 없었다** —
    `BAND_DAYS` 와 같은 모양을 한 번 더 만난 것이다.
    """

    def test_기업행위_판정_문턱(self) -> None:
        검사(ADJUST, f"ACTION_TOLERANCE = {adj.ACTION_TOLERANCE}", "기업행위 문턱")
        검사(ADJUST, f"MIN_FACTOR {adj.MIN_FACTOR}", "계수 하한")
        검사(ADJUST, f"MAX_FACTOR {adj.MAX_FACTOR:.0f}", "계수 상한")

    def test_감성_시간_감쇠와_최소_표본(self) -> None:
        검사(SENTIMENT, f"0.5^(경과일 / **{se.HALFLIFE_DAYS:.0f}**)", "반감기")
        검사(SENTIMENT, f"**{se.MIN_ARTICLES}건 미만**", "최소 기사 수")
        검사(SENTIMENT, f"**가장 새 기사가 {se.NEWEST_MAX_DAYS}일보다 오래됐으면**", "가장 새 기사 나이 (25.832)")
        검사(SENTIMENT, f"**채점되지 않은 몫이 {se.UNSCORED_MAX_SHARE * 100:.0f}% 를 넘으면**", "미채점 몫 (25.834)")
        검사(SENTIMENT, f"≥ +{se.POSITIVE_AT} 긍정, ≤ −{abs(se.NEGATIVE_AT)} 부정", "긍정·부정 문턱")

    def test_감성_급락_문턱(self) -> None:
        """매도 플래그(황)의 문턱이다. `services/sell_flags` 가 이 값을 가져다 쓴다."""
        검사(
            SENTIMENT,
            f"7일 변화 ≤ −{se.DROP_POINTS_7D:.0f}점 그리고 최근 7일 부정 기사"
            f" ≥ {se.DROP_MIN_NEGATIVE_7D}건",
            "감성 급락",
        )

    def test_종가_나이_문턱(self) -> None:
        """손절·목표를 **묵은 종가로** 판정하지 않는다 (25.161). 잣대는 포트폴리오와 같다."""
        검사(SELL_FLAGS, f"**{m.MAX_SESSION_GAP_DAYS}일**. 거래일 사이 최장 간격", "종가 나이 문턱")

    def test_감성_나이_문턱(self) -> None:
        """**같은 규칙이 두 곳에 있었다** (docs/infra.md 25.161). 점수 배치는 걸고
        매도 플래그는 안 걸었다. 이제 `services/sentiment` 가 정의처다."""
        검사(SELL_FLAGS, f"**{se.MAX_AGE_DAYS}일**(`sentiment.MAX_AGE_DAYS`)", "감성 나이 문턱")

    def test_업종_백분위_최소_집단(self) -> None:
        """**문서에 없던 것** — 2026-09-23 에 `docs/stock_detail.md` 에 적었다."""
        검사(DETAIL, f"같은 업종 종목이 {vb.MIN_PEERS}개 미만이면", "업종 백분위 최소 집단")

    def test_팩터_IC_문턱(self) -> None:
        """발굴 루프의 켜는 기준 (docs/backtest.md 2.5, docs/infra.md 25.435)."""
        검사(BACKTEST, f"둘 다 있는 종목이 {fic.MIN_STOCKS} 미만이면", "IC 한 달 최소 표본")
        검사(BACKTEST, f"{fic.MIN_MONTHS}개월 이상, t ≥ {fic.T_THRESHOLD:g}", "IC 켜는 기준")
        검사(BACKTEST, f"평균이 **{fic.MIN_COVERAGE:g} 미만**이면 판정하지 않는다", "IC 표본 비율")
        검사(BACKTEST, f"Newey-West 표준오차**(시차 {fic.NW_LAGS})", "IC t 의 NW 시차")
        검사(BACKTEST, f"`bh_pass`({fic.FDR_Q * 100:g}%)", "다중검정 기록의 거짓 발견률 (25.810)")

    def test_순주식발행_분할_문턱(self) -> None:
        """분할·병합을 대량 발행으로 넣지 않는다 (docs/factors.md 12.2, docs/infra.md 25.446)."""
        검사(FACTORS, f"{si.SPLIT_RATIO:g}배 문턱으로 분할을 걸렀다", "순주식발행 (쓰지 않는) 분할 문턱")

    def test_자사주_취득_창(self) -> None:
        검사(FACTORS, f"(c − {bb.WINDOW_DAYS}일, c]", "자사주 취득 창")

    def test_SUE_분기_계열(self) -> None:
        """분기 실적 서프라이즈의 입력 (docs/factors.md 12.2, docs/infra.md 25.456)."""
        검사(FACTORS, f"거슬러 **{qe.SERIES_QUARTERS}개**(오래된 것부터)", "SUE 계열 길이")
        검사(FACTORS, f"기준일보다 **{qe.SUE_MAX_AGE_DAYS}일** 넘게 묵었으면 NULL", "SUE 신선도")

    def test_성적표_창(self) -> None:
        검사(SIGNALS, f"{oc.HORIZON_WINDOW} 거래일 안 **종가**", "목표·손절 터치 창")
        검사(SIGNALS, f"마지막 날보다 {oc.CUTOFF_GAP_DAYS}일 넘게 먼저 끝났고", "시세 끊김 판정 (25.696)")
        검사(SIGNALS, f"**{oc.EXCESS_SAMPLE_SHARE * 100:.0f}%** 이상이 지수값을 가질 때만", "지수 대비 표본 비율 (25.706)")

    def test_스트레스_표본_하한(self) -> None:
        검사(STRESS, f"창 길이의 {st.MIN_SAMPLE_MULTIPLE}배에 못 미치면", "스트레스 표본 하한")


def test_그물_밖의_모듈이_전부_적혀_있다() -> None:
    """**범위를 판단으로 남긴다** (docs/infra.md 25.148, 25.76 과 같은 이유).

    `services/` 에 새 모듈이 생기면 둘 중 하나를 해야 한다 — 훑는 목록에 넣거나,
    `아직_안_보는_모듈` 에 **왜 아직인지**를 적거나. 아무것도 안 하면 여기서 깨진다.
    전에는 넷만 보고 나머지는 **아무 데도 적혀 있지 않았다.**
    """
    뿌리 = Path(__file__).resolve().parent.parent / "batch" / "services"
    있는모듈 = {
        길.stem
        for 길 in 뿌리.glob("*.py")
        if 길.stem != "__init__"
    }
    assert len(있는모듈) >= 20, f"서비스 모듈을 {len(있는모듈)}개밖에 못 찾았다"

    빠진것 = sorted(있는모듈 - set(훑는_모듈) - set(아직_안_보는_모듈))
    assert not 빠진것, (
        f"`services/` 에 그물 안팎 어디에도 안 적힌 모듈이 있다: {빠진것}\n"
        "`훑는_모듈` 에 넣거나 `아직_안_보는_모듈` 에 사유와 함께 적어라"
    )

    유령 = sorted(set(훑는_모듈) | set(아직_안_보는_모듈) - 있는모듈)
    유령 = [이름 for 이름 in 유령 if 이름 not in 있는모듈]
    assert not 유령, f"없는 모듈이 목록에 남아 있다: {유령}"


def test_아직_안_보는_사유가_비어_있지_않다() -> None:
    짧은것 = [이름 for 이름, 사유 in 아직_안_보는_모듈.items() if len(사유.strip()) < 10]
    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


class Test매도_플래그_문턱:
    """표시와 알림만 하지만 **사람이 그걸 보고 판다** (docs/sell_flags.md 1장)."""

    def test_기간_초과(self) -> None:
        from batch.services import sell_flags as sf

        검사(
            SELL_FLAGS,
            f"단기 {sf.MAX_HOLDING_DAYS['short']}일 · 중기 {sf.MAX_HOLDING_DAYS['mid']}일",
            "기간 초과",
        )

    def test_점수_하락(self) -> None:
        from batch.services import sell_flags as sf

        검사(SELL_FLAGS, f"매수 당시보다 {sf.SCORE_DROP_POINTS:.0f}점 이상", "재무악화 ②")

    def test_기간별_목표와_손절(self) -> None:
        """CLAUDE.md 매매 규칙의 기본값이다. 설정이 없으면 이 값으로 판정한다."""
        from batch.services import sell_flags as sf

        손절들 = " · ".join(
            f"{이름} {sf.DEFAULT_TARGETS[키]['stop_pct']:.0f}%".replace("-", "−")
            for 키, 이름 in (("short", "단기"), ("mid", "중기"), ("long", "장기"))
        )
        목표들 = " · ".join(
            f"{이름} +{sf.DEFAULT_TARGETS[키]['target_pct']:.0f}%"
            for 키, 이름 in (("short", "단기"), ("mid", "중기"), ("long", "장기"))
        )
        검사(SELL_FLAGS, 손절들, "손절선 기본값")
        검사(SELL_FLAGS, 목표들, "목표 수익률 기본값")



def test_유니버스_문턱이_설계서_표와_같다() -> None:
    """문턱 숫자가 코드에만 있었다 (docs/infra.md 25.304)."""
    from batch.services import universe as uni

    글 = 읽기("design.md")
    kr, us = uni.UniverseFilters.korea(), uni.UniverseFilters.usa()
    assert kr.min_market_cap == 100_000_000_000 and "| 시가총액 하한 | 1,000억원 | 10억달러 |" in 글
    assert us.min_market_cap == 1_000_000_000
    assert kr.min_avg_turnover_20d == 500_000_000 and us.min_avg_turnover_20d == 5_000_000
    assert "| 20일 평균 거래대금 하한 | 5억원 | 500만달러 |" in 글
    assert f"| 상장 경과일 하한 | {uni.DEFAULT_MIN_LISTED_DAYS}일 | {uni.DEFAULT_MIN_LISTED_DAYS}일 |" in 글


def test_유니버스_제외_사유가_설계서_목록과_같다() -> None:
    """설계서 `exclude_reason` 목록에 코드가 쓰는 사유 셋이 빠져 있었다 (docs/infra.md 25.366)."""
    import re

    from batch.services import universe as uni

    글 = 읽기("design.md")
    목록 = re.search(r"exclude_reason\(([^)]*)\)", 글)
    assert 목록 is not None
    assert set(목록.group(1).split("|")) == set(uni.ALL_REASONS)
    assert "미국 시총 판정은 아직 붙이지 않았다" not in 글
    assert "하루만 비어도 그 종목을 판정에서 통째로 뺀다" not in 글
