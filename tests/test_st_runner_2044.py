"""S·T 리플레이 실행기 (깔때기 ⑦-6, 25.2044) — `run_core(explore=True, st=True)` 가 탐색 실행에 st_s·st_t 를 더한다.

- 탐색 코호트에만(핵심 판정·코호트 1 시도 수는 그대로), 시도 수 하한 = 코호트 1 + 탐색 변형 + 둘
- 실행 행 `strategy` 는 st_s·st_t, 계좌 칸 S·T, 경고 `WARN_ST`
- 실행 기록 `st` 에 시가 덮개·계좌별 체결 합
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import core_strategy as cs


def test_탐색_실행에_S_T_리플레이를_더한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    행 = mem.execute("SELECT strategy, top_n, warnings_json, metrics_json FROM backtest_runs ORDER BY id").dicts()
    assert [r["strategy"] for r in 행] == [*cs.EXPLORE_VARIANTS, *cj.ST_VARIANTS]
    st = {r["strategy"]: r for r in 행 if r["strategy"] in cj.ST_VARIANTS}
    assert all(r["top_n"] == 10 for r in st.values())
    assert {json.loads(r["metrics_json"])["account"] for r in st.values()} == {"S", "T"}
    assert all(cj.WARN_ST in json.loads(r["warnings_json"]) for r in 행)
    assert all(json.loads(r["metrics_json"])["luck"]["n_trials"] >= cj.explore_trial_floor(True) for r in 행)
    assert cj.explore_trial_floor(True) == jb.REGISTERED_TRIALS + len(cs.EXPLORE_VARIANTS) + 2
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert 로그["params"]["st"] is True and 로그["params"]["trial_floor"] == cj.explore_trial_floor(True)
    assert set(로그["st"]["accounts"]) == {"S", "T"} and "open_coverage" in 로그["st"]
    # 결정 단위 증거는 핵심·탐색 변형만(S·T 는 DayLog 가 아니다)
    assert set(로그["evidence"]) <= set(cs.EXPLORE_VARIANTS)


def test_핵심_실행에는_붙일_수_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_core_backtest_job_1534 import _db

    _db(monkeypatch)  # 막이가 풀려도 실제 DB 로 가지 않게(25.2045)
    with pytest.raises(ValueError, match="탐색 실행"):
        cj.run_core("KR", st=True)


def test_시가가_있으면_S_T_가_실제로_사고팔고_노출이_실린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """합성 DB 에 시가(≠ 종가)를 채운 판(25.2045, 25.2044 교차검증 [권고]) — 시가 덮개 100%, T 가 체결하고 S 가 스윙 까닭으로
    판다. 판정판 노출 칸이 실제 값(예전 `rebalances=[]` 는 "투자한 달 0%" 거짓 경고)이고 낮은 덮개 경고는 없다."""
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    mem.conn.execute("UPDATE prices SET open = ROUND(close * 0.995, 2)")
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    st = 로그["st"]
    assert st["open_coverage"] == 1.0
    assert st["accounts"]["T"]["fills"] > 0 and "단타종가" in st["accounts"]["T"]["sold"]
    assert st["accounts"]["S"]["sells"] >= 1 and set(st["accounts"]["S"]["sold"]) <= {"스윙손절", "스윙목표", "스윙기한"}
    행 = {r["strategy"]: r for r in mem.execute("SELECT strategy, metrics_json, warnings_json FROM backtest_runs"
                                               ).dicts()}  # fmt: skip
    지표 = json.loads(행["st_s"]["metrics_json"])
    assert 지표["invested_share"] > 0 and 지표["avg_holdings"] > 0
    경고 = json.loads(행["st_s"]["warnings_json"])
    assert not any("투자한 달 0%" in w for w in 경고) and not any("시가 덮개" in w for w in 경고)
    # T 는 하루 끝 보유가 0 이어도 장중 보유가 노출로 잡힌다(25.2046 — 예전엔 st_t 만 "투자한 달 0%")
    t지표 = json.loads(행["st_t"]["metrics_json"])
    assert t지표["invested_share"] > 0 and t지표["avg_holdings"] > 0
    assert not any(w.startswith("st_t: 투자한 달 0%") for w in json.loads(행["st_t"]["warnings_json"]))
    # 비중이 금액이 아니라 자본 대비 몫인지(돌연변이 `x / 자본` → `x` 가 지나던 자리)
    assert 0 < 지표["avg_holdings"] < 50


def test_시가가_없으면_S_T_줄에_덮개_경고를_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    경고 = json.loads(mem.execute("SELECT warnings_json FROM backtest_runs WHERE strategy = 'st_t'").scalar())
    assert any(w.startswith("S·T 시가 덮개 0.0%") for w in 경고)


def test_S_T_리플레이가_깨져도_탐색_변형은_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.services import swing_day_replay as sdr
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)

    def 깨짐(*_a, **_k):
        raise RuntimeError("시험 — 일부러 깨뜨림")

    monkeypatch.setattr(sdr, "simulate_account", 깨짐)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    assert [r[0] for r in mem.execute("SELECT strategy FROM backtest_runs ORDER BY id").rows] == list(cs.EXPLORE_VARIANTS)
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert "RuntimeError" in 로그["st"]["error"]



def test_S_뒤_T_에서_깨져도_덮개와_S_합이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """기록을 인자로 넘겨 채우므로 부분 실패에도 이미 잰 값이 남는다(25.2046)."""
    from batch.services import swing_day_replay as sdr
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    원래 = sdr.simulate_account

    def T만_깨짐(account, **kw):  # noqa: ANN001, ANN003
        if account == "T":
            raise RuntimeError("시험 — T 만 깨뜨림")
        return 원래(account, **kw)

    monkeypatch.setattr(sdr, "simulate_account", T만_깨짐)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert "RuntimeError" in 로그["st"]["error"] and 로그["st"]["open_coverage"] == 0.0 and "S" in 로그["st"]["accounts"]
    assert "st_s" in [r[0] for r in mem.execute("SELECT strategy FROM backtest_runs").rows]
