"""설정 범위 사본이 **스키마와 같은가** (docs/infra.md 25.170).

설정의 단일 정의처는 `web/lib/settings.ts` 다. 배치가 파이썬이라 그 스키마를 쓸 수
없어 `batch/core/settings_range.py` 에 사본을 둔다 — **사본은 낡는다.** 그래서 여기서
그 파일을 읽어 한 칸씩 대 본다.

왜 사본이 필요한가: `settings.ts` 머리말의 "설정은 웹앱만 쓴다" 는 전제가
`restore_backup`·`move_user_data` 에서 깨진다. 25.169 에서 가중치 하나가 종합 점수를
−40 으로 만드는 것을 봤고, 여기 있는 값들은 **돈에 닿는다**.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.core import settings_range as sr
from batch.core.settings_range import 범위_안, 설정_범위, 잎마다

뿌리 = Path(__file__).resolve().parent.parent
스키마파일 = 뿌리 / "web" / "lib" / "settings.ts"

#: `percent` 별칭을 쓰는 칸. zod 에 숫자가 직접 안 적혀 있어 따로 잇는다
_별칭 = "percent"


def _percent_범위(글: str) -> tuple[float, float]:
    m = re.search(r"const percent = z\.number\(\)\.min\(([\d_.]+)\)\.max\(([\d_.]+)\)", 글)
    assert m, "`percent` 정의를 못 읽었다 — 스키마 모양이 바뀌었다"
    return float(m.group(1).replace("_", "")), float(m.group(2).replace("_", ""))


def 스키마_범위() -> dict[str, tuple[float, float]]:
    """`settings.ts` 의 숫자 잎마다 (최소, 최대). 순서가 달라도 읽는다."""
    글 = 스키마파일.read_text(encoding="utf-8")
    나온것: dict[str, tuple[float, float]] = {}

    for m in re.finditer(r"^\s*([a-z_0-9]+):\s*(z\.number\(\)[^,\n]*|percent),", 글, re.M):
        이름, 정의 = m.group(1), m.group(2)
        if 정의 == _별칭:
            나온것[이름] = _percent_범위(글)
            continue
        최소 = re.search(r"\.min\((-?[\d_.]+)\)", 정의)
        최대 = re.search(r"\.max\((-?[\d_.]+)\)", 정의)
        나온것[이름] = (
            float(최소.group(1).replace("_", "")) if 최소 else float("-inf"),
            float(최대.group(1).replace("_", "")) if 최대 else float("inf"),
        )
    return 나온것


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    assert len(스키마_범위()) > 20, "스키마에서 숫자 칸을 너무 적게 찾았다"


def test_잎_이름이_겹치지_않는다() -> None:
    """사본이 잎 이름으로 잇는다. 겹치면 엉뚱한 범위를 걸게 된다."""
    글 = 스키마파일.read_text(encoding="utf-8")
    이름들 = re.findall(r"^\s*([a-z_0-9]+):\s*(?:z\.number\(\)[^,\n]*|percent),", 글, re.M)

    겹침 = sorted({n for n in 이름들 if 이름들.count(n) > 1})
    assert not 겹침, f"잎 이름이 겹친다: {겹침} — 사본의 열쇠를 바꿔야 한다"


def test_스키마의_모든_숫자_칸이_사본에_있다() -> None:
    """**새 숫자 칸이 생기면 여기서 깨진다.** 사본이 조용히 낡는 것을 막는다."""
    빠진것 = sorted(set(스키마_범위()) - set(설정_범위))

    assert not 빠진것, (
        f"`settings.ts` 에 있는데 `settings_range` 에 없는 숫자 칸: {빠진것}\n"
        "범위를 옮겨 적어라 (docs/infra.md 25.170)"
    )


def test_사본에만_있는_칸이_없다() -> None:
    """스키마에서 지운 칸이 사본에 남으면, 없는 설정을 지키는 셈이다."""
    남은것 = sorted(set(설정_범위) - set(스키마_범위()))
    assert not 남은것, f"스키마에 없는 칸이 사본에 남아 있다: {남은것}"


@pytest.mark.parametrize("이름", sorted(설정_범위))
def test_범위가_스키마와_같다(이름: str) -> None:
    """**두 수가 갈라지면** 화면이 받는 값을 배치가 거부하거나 그 반대가 된다."""
    assert 설정_범위[이름] == 스키마_범위()[이름]


class Test범위_안:
    def test_범위_안이면_그대로(self) -> None:
        assert 범위_안("max_weight_per_stock", 10, 10) == (10.0, None)
        assert 범위_안("stop_pct", -15, -15) == (-15.0, None)

    def test_경계는_통과한다(self) -> None:
        for 이름 in ("max_weight_per_stock", "stop_pct", "kr_buy_pct"):
            최소, 최대 = 설정_범위[이름]
            assert 범위_안(이름, 최소, 0)[1] is None, f"{이름} 최소는 범위 안이다"
            assert 범위_안(이름, 최대, 0)[1] is None, f"{이름} 최대는 범위 안이다"

    def test_범위_밖이면_되돌리고_말한다(self) -> None:
        값, 말 = 범위_안("max_weight_per_stock", 500, 10)

        assert 값 == 10
        assert 말 is not None
        assert "max_weight_per_stock" in 말
        assert "설정 화면에서 고치세요" in 말, "무엇을 해야 하는지 말해야 한다"

    def test_부호가_뒤집힌_손절선도_잡는다(self) -> None:
        """손절선은 음수여야 한다. 양수면 **손절이 목표 위에 선다.**"""
        값, 말 = 범위_안("stop_pct", 15, -15)

        assert 값 == -15
        assert 말 is not None

    def test_숫자가_아니면_되돌린다(self) -> None:
        값, 말 = 범위_안("min_order_amount", "십만원", 100_000)
        assert 값 == 100_000
        assert 말 is not None and "숫자가 아닙니다" in 말

    def test_참거짓은_숫자가_아니다(self) -> None:
        """파이썬에서 `True` 는 1 이다. 그대로 두면 가중치 1% 가 된다."""
        값, 말 = 범위_안("sentiment_weight", True, 0)
        assert 값 == 0
        assert 말 is not None

    def test_안_넣은_값은_정상이다(self) -> None:
        """세율처럼 **모르면 안 쓰는** 값이 있다. 그것까지 경고하면 매일 시끄럽다."""
        assert 범위_안("kr_transaction_pct", None, None) == (None, None)

    def test_기본이_없으면_쓰지_않는다고_말한다(self) -> None:
        _값, 말 = 범위_안("kr_dividend_pct", 99, None)
        assert 말 is not None and "쓰지 않았습니다" in 말

    def test_모르는_이름은_바로_터진다(self) -> None:
        """**조용히 통과시키지 않는다.** 범위를 안 적고 쓰는 것이 이 절의 결함이다."""
        with pytest.raises(KeyError):
            범위_안("없는_설정", 1, 1)

    def test_무제한_최대도_말이_된다(self) -> None:
        값, 말 = 범위_안("total_investable_amount", -1, 0)
        assert 값 == 0
        assert 말 is not None and "무제한" in 말


class Test잎마다:
    """중첩 설정(`fees`·`taxes`·`risk_free_manual`)의 잎을 한 번에 본다."""

    def test_멀쩡하면_그대로(self) -> None:
        값, 경고 = 잎마다({"kr_buy_pct": 0.015, "kr_sell_pct": 0.015}, ("kr_buy_pct", "kr_sell_pct"))

        assert 값 == {"kr_buy_pct": 0.015, "kr_sell_pct": 0.015}
        assert 경고 == []

    def test_범위_밖이면_모른다로_둔다(self) -> None:
        """**0 으로 되돌리면 "수수료 없음" 이 되어 실현손익이 좋은 쪽으로 틀린다.**"""
        값, 경고 = 잎마다({"kr_buy_pct": 500}, ("kr_buy_pct",))

        assert 값 == {"kr_buy_pct": None}, "0 이 아니라 None 이어야 한다"
        assert len(경고) == 1 and "kr_buy_pct" in 경고[0]

    def test_안_넣은_잎은_조용하다(self) -> None:
        """세율은 확인 전에는 비워 두는 값이다. 매일 경고하면 아무도 안 읽는다."""
        값, 경고 = 잎마다({}, ("kr_transaction_pct",))

        assert 값 == {"kr_transaction_pct": None}
        assert 경고 == []

    def test_설정이_dict_가_아니어도_버틴다(self) -> None:
        값, 경고 = 잎마다(None, ("us_pct",))
        assert 값 == {"us_pct": None}
        assert 경고 == []

    def test_무위험수익률도_같은_잣대다(self) -> None:
        값, 경고 = 잎마다({"kr_pct": 99}, ("kr_pct",))

        assert 값["kr_pct"] is None, "무위험수익률 99% 로 낸 샤프는 값이 아니다"
        assert 경고


# ----------------------------------------------------------------------
# 잎마다 **어디서 범위를 보나** (docs/infra.md 25.171)
# ----------------------------------------------------------------------
#
# 범위 표만 있고 아무도 안 보면 뜻이 없다. 잎마다 "어느 파일이 본다" 를 적고,
# 그 파일에 그 이름이 실제로 있는지 확인한다. 안 보는 잎은 **왜 안 보는지**를 적는다.
#
# 새 숫자 칸이 생기면 위의 `test_스키마의_모든_숫자_칸이_사본에_있다` 가 먼저 깨지고,
# 범위를 적고 나면 여기서 한 번 더 깨진다 — "그래서 어디서 볼 건가".

보는_곳: dict[str, str] = {
    # 종합 점수 (25.169)
    "value": "batch/jobs/scores.py",
    "quality": "batch/jobs/scores.py",
    "growth": "batch/jobs/scores.py",
    "momentum": "batch/jobs/scores.py",
    "risk": "batch/jobs/scores.py",
    "sentiment_weight": "batch/jobs/scores.py",
    # 권장 금액·비중·목표가 (25.170)
    "total_investable_amount": "batch/jobs/signals.py",
    "max_weight_per_stock": "batch/jobs/signals.py",
    "max_weight_per_sector": "batch/jobs/signals.py",
    "min_order_amount": "batch/jobs/signals.py",
    "target_pct": "batch/core/settings_range.py",
    "stop_pct": "batch/core/settings_range.py",
    # 실현손익 (25.171)
    "kr_buy_pct": "batch/jobs/portfolio.py",
    "kr_sell_pct": "batch/jobs/portfolio.py",
    "us_buy_pct": "batch/jobs/portfolio.py",
    "us_sell_pct": "batch/jobs/portfolio.py",
    "kr_transaction_pct": "batch/jobs/portfolio.py",
    "kr_dividend_pct": "batch/jobs/portfolio.py",
    "us_dividend_pct": "batch/jobs/portfolio.py",
    "us_capital_gains_pct": "batch/jobs/portfolio.py",
    # 샤프·소르티노 (25.171)
    "kr_pct": "batch/jobs/metrics.py",
    "us_pct": "batch/jobs/metrics.py",
}

#: 아직 안 보는 잎과 **왜 안 보는지**. 사유 없이 비워 두지 않는다
안_보는_것: dict[str, str] = {
    "bear_factor": "`services/trend.load_settings` 가 처음부터 0~1 로 자른다 —"
    " 이 저장소에서 설정을 안 믿은 유일한 자리였다(25.169)",
    "spike_pct": "장중 알림 문턱. **웹이 읽는다**(`/api/cron/intraday`). 파이썬 표의 일이 아니다"
    " `[확인필요: 웹 쪽에도 같은 잣대를 둘지]`",
    "volume_multiple": "거래량 배수 문턱. 위와 같이 웹이 읽는다 — 파이썬은 이 값을 안 쓴다",
    "sentiment_target_top_n": "뉴스 대상 수. 범위를 벗어나도 **돈에 닿지 않고**"
    " 대상이 많아지거나 적어질 뿐이다",
    "prices": "시세 백필 연수. 사람이 손으로 돌리는 작업의 인자라 그 자리에서 바로 드러난다",
    "financials": "재무 백필 연수. 위와 같이 사람이 손으로 돌리는 작업의 인자다",
}


def test_잎마다_보는_곳이_있다() -> None:
    """**표만 있고 아무도 안 보면 뜻이 없다.**"""
    빠진것 = sorted(set(설정_범위) - set(보는_곳) - set(안_보는_것))

    assert not 빠진것, (
        f"범위는 적었는데 **어디서 보는지** 안 적은 잎: {빠진것}\n"
        "보는 파일을 `보는_곳` 에, 안 볼 것이면 사유를 `안_보는_것` 에 적어라"
    )


def test_보는_곳_목록이_낡지_않았다() -> None:
    모르는것 = sorted((set(보는_곳) | set(안_보는_것)) - set(설정_범위))
    assert not 모르는것, f"범위 표에 없는 이름이 목록에 남아 있다: {모르는것}"

    겹침 = sorted(set(보는_곳) & set(안_보는_것))
    assert not 겹침, f"보는 곳과 안 보는 것에 동시에 있다: {겹침}"


@pytest.mark.parametrize("잎", sorted(보는_곳))
def test_그_파일이_그_이름을_실제로_본다(잎: str) -> None:
    """**적어 두고 안 보는 것**을 막는다 (25.0 「적어 놓고 읽을 때 안 건다」)."""
    글 = (뿌리 / 보는_곳[잎]).read_text(encoding="utf-8")

    assert 잎 in 글, f"{보는_곳[잎]} 에 `{잎}` 이 없다 — 옮겼으면 목록도 옮겨라"
    assert any(문 in 글 for 문 in ("get_setting_in_range", "범위_안", "잎마다")), (
        f"{보는_곳[잎]} 이 범위를 보는 문을 안 쓴다"
    )


def test_안_보는_것에는_사유가_있다() -> None:
    짧은것 = [k for k, v in 안_보는_것.items() if len(v.strip()) < 20]
    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


# ----------------------------------------------------------------------
# 기간별 목표·손절 — **네 곳이 같은 잣대를 쓴다** (docs/infra.md 25.180)
# ----------------------------------------------------------------------


class Test기간별_목표:
    """`horizon_targets` 는 짝으로 뜻을 갖는다. 반쪽이면 배치가 죽는다."""

    def test_성한_짝은_그대로(self) -> None:
        좋은것, 경고 = sr.기간별_목표({"short": {"target_pct": 12.0, "stop_pct": -8.0}})

        assert 좋은것 == {"short": {"target_pct": 12.0, "stop_pct": -8.0}}
        assert 경고 == []

    def test_잎이_하나_없으면_그_기간을_버린다(self) -> None:
        """**여기가 진짜 고장이었다** (docs/infra.md 25.180).

        `범위_안` 은 "안 넣은 값은 정상" 이라며 `(None, None)` 을 돌려준다.
        예전 코드는 그것을 통과로 읽고 **원래 dict** 을 넘겨, 반쪽 짝이 그대로 살았다.
        그러면 `services/signals.target_and_stop()` 이 `KeyError: 'stop_pct'` 로
        **신호 배치를 통째로 죽인다.**
        """
        좋은것, 경고 = sr.기간별_목표({"short": {"target_pct": 12.0}})

        assert 좋은것 is None
        assert any("stop_pct" in 줄 for 줄 in 경고), f"왜 버렸는지 안 말한다: {경고}"

    def test_버린_기간으로_신호를_내도_안_죽는다(self) -> None:
        """그 반쪽 짝이 실제로 무엇을 죽이는지 **끝까지** 확인한다."""
        from batch.services import signals as sg

        살아남은것, _ = sr.기간별_목표({"short": {"target_pct": 12.0}})

        목표, 손절 = sg.target_and_stop("short", 100.0, 110.0, 살아남은것)

        assert 목표 > 105.0 > 손절, "기본값으로 되돌아가야 한다"

    def test_범위_밖이면_짝_전체를_버린다(self) -> None:
        """목표만 되돌리고 손절을 두면 **손절이 목표 위에 서는** 짝이 생긴다."""
        좋은것, 경고 = sr.기간별_목표({"short": {"target_pct": 9999.0, "stop_pct": -8.0}})

        assert 좋은것 is None
        assert 경고 and "target_pct" in 경고[0]

    def test_성한_기간만_남긴다(self) -> None:
        좋은것, 경고 = sr.기간별_목표(
            {
                "short": {"target_pct": 12.0, "stop_pct": -8.0},
                "mid": {"target_pct": 9999.0, "stop_pct": -8.0},
            }
        )

        assert 좋은것 is not None and set(좋은것) == {"short"}
        assert len(경고) == 1

    def test_표가_아니면_말한다(self) -> None:
        좋은것, 경고 = sr.기간별_목표({"short": "엉뚱"})

        assert 좋은것 is None
        assert 경고, "조용히 버리면 왜 기본값이 쓰였는지 모른다"

    def test_검사한_값으로_다시_짓는다(self) -> None:
        """원래 dict 을 넘기면 검사와 쓰는 값이 갈라진다. 남는 것은 잎 둘뿐이다."""
        좋은것, _ = sr.기간별_목표(
            {"short": {"target_pct": 12, "stop_pct": -8, "엉뚱한칸": "남으면안된다"}}
        )

        assert 좋은것 == {"short": {"target_pct": 12.0, "stop_pct": -8.0}}


#: `horizon_targets` 를 읽는 배치 경로와 **그 설정이 무엇을 정하는가**.
#: 하나라도 날것을 쓰면 같은 설정으로 낸 값이 화면마다 달라진다
목표를_읽는_곳 = {
    "batch/jobs/signals.py": "매수 신호의 목표가·손절가",
    "batch/jobs/sell_flags.py": "매도 플래그의 목표 도달·손절 이탈 판정",
    "batch/jobs/monitor_targets.py": "장중 감시가 볼 목표가·손절가",
    "batch/jobs/portfolio.py": "매매 복기의 '목표에 닿았나' 판정",
}


def test_목표를_읽는_곳을_실제로_찾아_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    찾은것 = {
        str(길.relative_to(뿌리)).replace("\\", "/")
        for 길 in sorted((뿌리 / "batch").rglob("*.py"))
        if "__pycache__" not in str(길) and '"horizon_targets"' in 길.read_text(encoding="utf-8")
    }

    assert 찾은것 == set(목표를_읽는_곳), (
        f"`horizon_targets` 를 읽는 곳이 바뀌었다: {sorted(찾은것)}\n"
        "새로 읽는 곳이 생겼으면 `목표를_읽는_곳` 에 무엇을 정하는지와 함께 적어라"
    )


@pytest.mark.parametrize("경로", sorted(목표를_읽는_곳))
def test_네_곳이_모두_같은_잣대를_쓴다(경로: str) -> None:
    """**함수가 옳은 것과 부르는 쪽이 옳은 것은 다른 일이다** (25.151·25.174·25.176).

    2026-09-23 까지 범위 검사는 `jobs/signals` **한 곳**에만 있었다. 나머지 셋은
    날것을 읽어, 같은 설정으로 낸 신호의 손절선과 매도 플래그의 손절선이
    **서로 다를 수 있었다.**
    """
    글 = (뿌리 / 경로).read_text(encoding="utf-8")

    assert "기간별_목표" in 글, (
        f"{경로} 가 `horizon_targets` 를 날것으로 읽는다 ({목표를_읽는_곳[경로]}).\n"
        "`core/settings_range.기간별_목표()` 를 지나게 하라"
    )
    assert "isinstance(targets" not in 글 and "isinstance(horizon_targets" not in 글, (
        f"{경로} 에 옛 검사가 남아 있다 — 잣대가 둘이 된다"
    )


def test_목표_0_손절_0_짝은_웹처럼_거절한다() -> None:
    """docs/infra.md 25.276 — 범위는 끝을 포함해 {0, 0} 이 통과했고, 목표가·손절가가 매수 기준가와 같아졌다."""
    from batch.core import settings_range as sr_

    좋은, 경고 = sr_.기간별_목표({"short": {"target_pct": 0, "stop_pct": -7}, "mid": {"target_pct": 25, "stop_pct": 0},
                                "long": {"target_pct": 50, "stop_pct": -25}})  # fmt: skip
    assert 좋은 == {"long": {"target_pct": 50.0, "stop_pct": -25.0}}
    assert len(경고) == 2


class Test웹이_막는_조합:
    """웹은 저장 때 막지만 옛 백업을 되살리면 배치에 그대로 들어온다 (docs/infra.md 25.300)."""

    def test_센티먼트_50_초과는_기본으로(self) -> None:
        from batch.core import settings_range as sr

        값, 말 = sr.센티먼트_가중치(80.0, 10.0)
        assert 값 == 10.0 and 말 is not None and "80" in 말
        assert sr.센티먼트_가중치(50.0, 10.0) == (50.0, None)

    def test_종목_상한이_섹터_상한보다_크면_섹터까지(self) -> None:
        from batch.core import settings_range as sr

        값, 말 = sr.비중_상한_맞추기(40.0, 30.0)
        assert 값 == 30.0 and 말 is not None
        assert sr.비중_상한_맞추기(10.0, 30.0) == (10.0, None)

    def test_잡이_실제로_건다(self) -> None:
        """서비스 함수만 있고 잡이 안 부르면 소용없다."""
        import inspect

        from batch.jobs import scores, signals

        assert "센티먼트_가중치(" in inspect.getsource(scores)
        assert "비중_상한_맞추기(" in inspect.getsource(signals.load_settings)
