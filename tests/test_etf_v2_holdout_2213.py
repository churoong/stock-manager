"""45회차 (나) 홀드아웃 1회 실행 경로 (scripts/etf_v2_holdout.py, docs/factors.md 12.48) — 네트워크 없이 합성 계열로."""

from __future__ import annotations

import ast
import math
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(뿌리 / "scripts"))

import etf_v2_holdout as h  # noqa: E402

from batch.services import etf_tactical_v2 as v2  # noqa: E402
from batch.services import etf_theme as th  # noqa: E402

yaml = pytest.importorskip("yaml")
WF = 뿌리 / ".github" / "workflows" / "etf-v2-holdout.yml"


def _평일(시작: str, 끝: str) -> list[str]:
    d, e, out = date.fromisoformat(시작), date.fromisoformat(끝), []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _걷기(날: list[str], seed: int, drift: float = 0.0003, vol: float = 0.012) -> list[tuple[str, float]]:
    r = random.Random(seed)
    v, out = 100.0, []
    for d in 날:
        v *= math.exp(drift + vol * r.gauss(0, 1) + 0.004 * math.sin(len(out) / 180))
        out.append((d, v))
    return out


_날 = _평일("1999-11-01", "2020-03-31")
_자료: dict[str, list[tuple[str, float]]] = {"^KS11": _걷기(_날, 1), "^GSPC": _걷기(_날, 2)}
for _i, _t in enumerate(h.ETFS):
    # 상장일은 짧은 시험 구간(아래 _FIRSTS) 안에 들도록 앞당긴 합성값 — 늦은 상장의 꼴만 본다
    _시작 = {"XLRE": "2001-10-08", "XLC": "2003-03-19", "SH": "2001-06-21"}.get(_t, "1999-11-01")
    _자료[_t] = _걷기([d for d in _날 if d >= _시작], 10 + _i)


def _fetch(자료: dict | None = None):  # noqa: ANN202
    받은: list[list[str]] = []

    def f(tickers):  # noqa: ANN001, ANN202
        받은.append(list(tickers))
        return dict(_자료 if 자료 is None else 자료)

    f.받은 = 받은  # type: ignore[attr-defined]
    return f


#: 시험은 짧은 구간으로 돈다(리플레이 속도) — `run` 은 날짜만 받고, 문서의 달 검사는 `operating_first`·`main` 몫이다.
#: 실제 기본값(2019-05-01·2019-08-01)은 `test_구간은_…`·`test_main_…` 이 돈다
_FIRSTS = {"X": "2003-05-01", "Y": "2003-08-01"}


def _돌림(자료: dict | None = None, firsts: dict | None = None) -> tuple[int, list[str]]:
    줄: list[str] = []
    rc = h.run(_fetch(자료), firsts or _FIRSTS, 줄.append)
    return rc, 줄


@pytest.fixture(scope="module")
def 정상() -> tuple[int, list[str]]:
    return _돌림()


# ---------------------------------------------------------------------------------------------------------------------
# 출력 꼴
# ---------------------------------------------------------------------------------------------------------------------
def test_요약_꼴(정상) -> None:  # noqa: ANN001
    rc, 줄 = 정상
    글 = "\n".join(줄)
    assert rc == 0, 글
    assert "판정 불가" not in 글
    assert 줄[0].startswith("[ETF 판 2 홀드아웃")
    for 머리 in ("X KOSPI(^KS11)", "Y S&P 500(^GSPC)", "Y 테마"):
        assert sum(x.startswith(머리) for x in 줄) == 1, 머리
    # 시장마다: 문 (a)(b)(c) 한 줄 + TWR·MDD·연 뒤집힘·상승장 현금 비율, 판1·보유 대비
    문 = [x for x in 줄 if x.strip().startswith("문 (a)")]
    assert len(문) == 2 and all("(b)" in x and "(c)" in x and "→" in x for x in 문)
    assert "(문헌 표본 안)" not in 문[0] and "(문헌 표본 안)" in 문[1]  # Y 만 문헌 표본 안 표시(12.48)
    for 열쇠 in ("TWR 판2", "MDD 판2", "연 뒤집힘 판2", "상승장 날 현금 비율 판2"):
        assert sum(x.strip().startswith(열쇠) for x in 줄) == 2, 열쇠
    assert all(
        "판1" in x and ("보유" in x or "뒤집힘" in x or "현금" in x)
        for x in 줄
        if x.strip().startswith(("TWR 판2", "MDD 판2"))
    )
    테마 = [x for x in 줄 if x.strip().startswith("문 (i)")]
    assert len(테마) == 1 and "(ii)" in 테마[0] and "(iii)" in 테마[0]
    assert 줄[-1] == "끝 — 모든 문 판정함"


def test_요약은_스크립트_함수와_같은_값을_싣는다() -> None:
    rows = v2.holdout_rows(_자료["^KS11"], "2003-05-01")
    res = h.market_gate(rows, "X", "2003-05-01")
    g = res["gate"]
    줄 = h.market_lines("X", res)
    assert f"{res['v2']['twr_pct']:+.1f}%" in 줄[2] and f"{res['v1']['twr_pct']:+.1f}%" in 줄[2]
    assert ("통과" if g["a"] else "실패") in 줄[1].split("(b)")[0]
    assert res["v2"]["from"] == res["v1"]["from"] and res["v2"]["to"] == res["v1"]["to"] == rows[-1][0]
    날 = [d for d, _c in rows]
    assert 날[날.index(res["v2"]["from"]) + 1] == v2.common_start(rows)  # 첫 as_of = 13번째 월말 다음 거래일


def test_사용자_자료가_없다(정상) -> None:  # noqa: ANN001
    """지수 수준 집계뿐 — 계좌 금액·수량·보유 낱말이 나오지 않는다"""
    글 = "\n".join(정상[1])
    for 낱말 in ("수량", "원)", "평가액", "보유 종목", "주문"):
        assert 낱말 not in 글


# ---------------------------------------------------------------------------------------------------------------------
# 판정 불가 — 물러서지 않는다
# ---------------------------------------------------------------------------------------------------------------------
def test_지수가_없으면_판정_불가_테마도() -> None:
    rc, 줄 = _돌림({k: v for k, v in _자료.items() if k != "^GSPC"})
    assert rc == 1
    i = 줄.index("Y S&P 500(^GSPC) — etf_tactical_v2 문")
    assert 줄[i + 1] == "  판정 불가: ^GSPC 계열을 받지 못했다"
    assert any("판정 불가: ^GSPC 계열이 판정 불가라 테마도" in x for x in 줄)
    assert any(x.strip().startswith("문 (a)") for x in 줄)  # X 는 그대로 판정


def test_늦게_시작하면_판정_불가_다른_구간으로_물러서지_않는다() -> None:
    자료 = dict(_자료)
    자료["^KS11"] = [(d, c) for d, c in _자료["^KS11"] if d >= "2001-01-01"]
    rc, 줄 = _돌림(자료)
    assert rc == 1
    i = 줄.index("X KOSPI(^KS11) — etf_tactical_v2 문")
    assert "판정 불가" in 줄[i + 1] and "2001-01" in 줄[i + 1]
    assert not 줄[i + 2].strip().startswith("구간")


def test_끝이_짧으면_판정_불가() -> None:
    자료 = dict(_자료)
    자료["^GSPC"] = [(d, c) for d, c in _자료["^GSPC"] if d < "2003-06-01"]
    rc, 줄 = _돌림(자료)
    assert rc == 1 and any("판정 불가: ^GSPC 마지막 행 2003-05-30" in x for x in 줄)


@pytest.mark.parametrize("빠짐", ["XLK", "SPY", "SH", "XLRE"])
def test_테마_계열이_없으면_판정_불가(빠짐: str) -> None:
    rc, 줄 = _돌림({k: v for k, v in _자료.items() if k != 빠짐})
    assert rc == 1
    i = 줄.index("Y 테마 — etf_theme_v1 문 ①")
    assert 줄[i + 1] == f"  판정 불가: {빠짐} 계열을 받지 못했다"
    assert sum(x.strip().startswith("문 (a)") for x in 줄) == 2  # 시장 문은 그대로


def test_업종이_늦게_시작하면_판정_불가_SH_XLRE_는_늦어도_된다() -> None:
    자료 = dict(_자료)
    자료["XLE"] = [(d, c) for d, c in _자료["XLE"] if d >= "2000-02-01"]
    rc, 줄 = _돌림(자료)
    assert rc == 1 and any("판정 불가: XLE 첫 행" in x for x in 줄)


# ---------------------------------------------------------------------------------------------------------------------
# 구간 자르기 — 2000-01 ~ 운영 첫 행 전날
# ---------------------------------------------------------------------------------------------------------------------
def test_구간은_2000_01_부터_운영_첫_행_전날까지() -> None:
    rows = h.window(_자료["^KS11"], "^KS11", "2019-05-01")
    assert rows[0][0] == "2000-01-03" and rows[-1][0] == "2019-04-30"
    assert all(v2.HOLDOUT_FROM <= d < "2019-05-01" for d, _c in rows)
    res = h.market_gate(rows, "X", "2019-05-01")
    assert res["v2"]["to"] == "2019-04-30" and res["v1"]["to"] == "2019-04-30"
    # 운영 첫 행을 그 달 안 늦은 날로 주면 끝이 늘 뿐(그 전날까지)
    assert h.window(_자료["^KS11"], "^KS11", "2019-05-03")[-1][0] == "2019-05-02"


def test_테마도_같은_자리에서_자른다(정상) -> None:  # noqa: ANN001
    rc, 줄 = 정상
    구간 = [x for x in 줄 if x.strip().startswith("구간")]
    assert len(구간) == 3
    assert "~2003-04-30" in 구간[0] and "~2003-07-31" in 구간[1] and "~2003-07-31" in 구간[2]
    assert 구간[1].split("·")[0] == 구간[2].split("·")[0]  # 짝과 테마가 같은 시작·끝


def test_받는_심볼() -> None:
    f = _fetch()
    h.run(f, _FIRSTS, lambda _s: None)
    assert f.받은 == [["^KS11", "^GSPC", *th.UNIVERSE, "SPY", "SH"]]


def test_운영_첫_행_기본값과_검사() -> None:
    assert h.operating_first("X", "") == "2019-05-01" and h.operating_first("Y", None) == "2019-08-01"
    assert h.operating_first("X", "2019-05-02") == "2019-05-02"
    with pytest.raises(ValueError):
        h.operating_first("Y", "2019-09-03")  # 문서의 달이 아니면 돌지 않는다
    assert h.main(["--us-first", "2020-01-02"], fetch=_fetch()) == 2


def test_main_은_주입한_자료로_돈다(capsys) -> None:  # noqa: ANN001
    assert h.main([], fetch=_fetch()) == 0
    글 = capsys.readouterr().out
    assert "X 운영 첫 행 2019-05-01 (기본값" in 글 and "구간 끝 2019-04-30" in 글 and "구간 끝 2019-07-31" in 글


# ---------------------------------------------------------------------------------------------------------------------
# Turso 없음 · 워크플로
# ---------------------------------------------------------------------------------------------------------------------
def test_Turso_를_부르지_않는다() -> None:
    글 = (뿌리 / "scripts" / "etf_v2_holdout.py").read_text(encoding="utf-8")
    나무 = ast.parse(글)
    모듈 = {a.name for n in ast.walk(나무) if isinstance(n, ast.Import) for a in n.names}
    모듈 |= {f"{n.module}.{a.name}" for n in ast.walk(나무) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not [m for m in 모듈 if "core" in m or "turso" in m.lower() or "d1" in m.lower() or "sqlite" in m]
    # 새 인터프리터에서 불러도 DB 모듈이 딸려 오지 않는다
    r = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts'); import etf_v2_holdout as h; "
         "h.yahoo_fetch.__code__; print(sorted(m for m in sys.modules if m.startswith('batch.core') or 'turso' in m))"],
        cwd=뿌리, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert r.stdout.strip() == "[]", r.stdout


def test_워크플로는_손으로만_돌고_ops_tee_로_보낸다() -> None:
    글 = WF.read_text(encoding="utf-8")
    data = yaml.safe_load(글)
    on = data.get("on", data.get(True))
    assert set(on) == {"workflow_dispatch"}
    assert data["permissions"] == {"contents": "read", "issues": "write"}
    job = next(iter(data["jobs"].values()))
    assert job["timeout-minutes"] <= 60
    runs = [str(s.get("run", "")) for s in job["steps"]]
    assert "pip install -r requirements.txt" in runs
    실행 = [r for r in runs if "etf_v2_holdout.py" in r]
    assert len(실행) == 1 and "set -o pipefail" in 실행[0]
    assert "2>&1 | python scripts/ops_tee.py etf-v2-holdout.txt" in 실행[0]
    내보냄 = [s for s in job["steps"] if "publish_output.py" in str(s.get("run", ""))]
    assert len(내보냄) == 1 and "always()" in str(내보냄[0]["if"]) and "etf-v2-holdout.txt" in 내보냄[0]["run"]
    assert "TURSO" not in 글 and "D1_" not in 글 and "price-replica" not in 글
