"""웹과 배치에 따로 적힌 규칙 상수를 대조한다 (docs/infra.md 25.1753, 중복 규칙 감사 ⑦ 3차).

웹(TypeScript)은 파이썬을 import 할 수 없어 같은 값을 다시 적는다. 그 사본이 갈라지면 웹과 배치가 서로 다른 잣대로
돈을 센다 — 예: 예약금 여유가 다르면 웹이 입금 취소를 허락했는데 배치는 현금이 모자라 장부 현금이 음수가 된다.
감사(25.1746)에서 "웹↔파이썬 대조 없음" 으로 나온 것만 모았다. 이미 대조하는 쌍(FX_MAX_AGE_DAYS paper.ts↔jobs/paper —
`paper1446.test.ts`, health↔fx — `test_freshness_verdict`)은 여기서도 함께 본다(정의처가 `fx.MAX_STALE_DAYS` 로 모였다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.services import fx, signals
from batch.services import paper as pp
from batch.sources import kis_vts as kv
from batch.sources import nasdaq_symbols as ns

웹 = Path(__file__).resolve().parent.parent / "web" / "lib"


def _ts(파일: str, 이름: str) -> float:
    m = re.search(rf"export const {이름}\s*=\s*([\d_.]+)\s*;", (웹 / 파일).read_text(encoding="utf-8"))
    assert m, f"web/lib/{파일} 에서 `{이름}` 을 못 읽었다"
    return float(m.group(1).replace("_", ""))


@pytest.mark.parametrize(("파일", "이름", "배치"), [
    ("paper.ts", "FEE_BUFFER", pp.FEE_BUFFER),
    ("paper.ts", "FX_BUFFER", pp.FX_BUFFER),
    ("paper.ts", "OPEN_BUY_BUFFER", pp.OPEN_BUY_BUFFER),
    ("paper.ts", "DEPOSIT_MIN_KRW", pp.DEPOSIT_MIN_KRW),
    ("paper.ts", "DEPOSIT_MAX_KRW", pp.DEPOSIT_MAX_KRW),
    ("paper.ts", "FX_MAX_AGE_DAYS", fx.MAX_STALE_DAYS),
    ("portfolio.ts", "FX_MAX_STALE_DAYS", fx.MAX_STALE_DAYS),
    ("health.ts", "FX_STALE_DAYS", fx.MAX_STALE_DAYS),
])  # fmt: skip
def test_웹_사본이_배치와_같다(파일: str, 이름: str, 배치: float) -> None:
    assert _ts(파일, 이름) == float(배치), f"web/lib/{파일} {이름} 이 배치({배치})와 다르다"


#: 정의처를 따라야 하는 사본 이름 — 숫자로 다시 적으면 정의처만 바꾼 날 조용히 갈라진다(25.1755, 25.1753 교차검증 [권고]:
#: 값만 견줘 `KR_PRICE_LIMIT = 0.30` 으로 되돌려도 통과했다). 정의처 파일(`core/markets.py`·`services/fx.py`)은 뺀다
_사본 = ("KR_PRICE_LIMIT", "KR_DAILY_LIMIT", "KR_UNKNOWN_PCT_JUMP", "LT_KR_LIMIT", "STOP_VOL_KR_JUMP", "FX_MAX_AGE_DAYS",
         "REALIZED_FX_MAX_AGE_DAYS")  # fmt: skip
#: 빼는 정의처 파일 — 이름이 아니라 경로로(25.1756 — 이름으로 빼서 `batch/jobs/fx.py` 까지 빠졌다)
_정의처 = ("batch/core/markets.py", "batch/services/fx.py")


def test_가격제한과_환율_묵음은_정의처_하나() -> None:
    from batch.core import markets
    from batch.jobs import broker_mirror as bmj
    from batch.jobs import paper as pj
    from batch.services import adjust, candidate_factors, stop_vol

    assert pp.KR_PRICE_LIMIT == signals.KR_DAILY_LIMIT == adjust.KR_UNKNOWN_PCT_JUMP == candidate_factors.LT_KR_LIMIT == (
        stop_vol.STOP_VOL_KR_JUMP) == markets.KR_DAILY_LIMIT
    assert pj.FX_MAX_AGE_DAYS == bmj.REALIZED_FX_MAX_AGE_DAYS == fx.MAX_STALE_DAYS
    뿌리 = Path(__file__).resolve().parent.parent
    따로 = [f"{f.relative_to(뿌리)}:{k}" for f in (뿌리 / "batch").rglob("*.py") if str(f.relative_to(뿌리)) not in _정의처
            for k, 줄 in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.match(rf"^({'|'.join(_사본)})\s*(:\s*\w+\s*)?=\s*[\d.]", 줄)]  # fmt: skip
    assert not 따로, f"정의처 사본을 숫자로 다시 적었다 — core/markets·services/fx 를 쓴다: {따로}"


def test_장중_기업행위_의심_폭은_가격제한_바로_밖() -> None:
    """웹 장중 감시의 국내 기업행위 의심 비율 [0.69, 1.31] 은 가격제한폭 ±30% 에 1%p 여유 — 제한폭이 바뀌면 같이 바꾼다"""
    글 = (웹 / "intraday.ts").read_text(encoding="utf-8")
    m = re.search(r'market === "KR" \? \[([\d.]+), ([\d.]+)\]', 글)
    assert m, "web/lib/intraday.ts 의 국내 의심 폭을 못 읽었다"
    assert (float(m.group(1)), float(m.group(2))) == pytest.approx((1 - signals.KR_DAILY_LIMIT - 0.01,
                                                                    1 + signals.KR_DAILY_LIMIT + 0.01))  # fmt: skip


def test_KIS_미국_거래소_매핑은_우리_시장_이름_안에() -> None:
    """KIS 모의 미국 주문의 거래소 코드 표 — 키가 `stocks.market` 이름과 어긋나면 그 시장 주문이 말없이 안 나간다
    (코드가 "모르는 시장은 주문하지 않는다"). Cboe BZX·IEX 는 KIS 모의 주문 거래소가 아니라 일부러 뺐다 `[확인필요]`"""
    assert set(kv.US_EXCHANGE) <= set(ns.US_MARKETS)
    assert set(ns.US_MARKETS) - set(kv.US_EXCHANGE) <= {"Cboe BZX", "IEX"}, "새 미국 시장이 생겼다 — KIS 매핑을 정한다"


def test_KIS_장부_키() -> None:
    from batch.jobs import broker_mirror as bmj

    글 = (웹 / "paper.ts").read_text(encoding="utf-8")
    assert f'KR: "{bmj.BROKER}", US: "{bmj.BROKER_US}"' in 글
