"""42회차 판정 단계 — 11-03 판정 실행 나머지 읽기 SQL (docs/infra.md 25.2180). **값을 보기 전에 커밋**.

짝 판정(ic_weighted·shrink_missing 대 composite, momentum_sector 대 momentum)의 계산 꼴을 고정한다: 날값 CAGR·샤프·MDD,
차이 월 초과의 앞 ⌊n/2⌋·뒤 반 평균, 실행한 달(부분 달)을 뺀 계열과의 경계(모름). 함께 읽는 실행 기록 칸(IC·A/B·52주 고점·
일일 상관·환율 기록)이 빠지지 않는지도 본다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

SQL = (Path(__file__).resolve().parent.parent / "scripts/diag/round42_judge_1103.sql").read_text(encoding="utf-8")
#: 월말 점 일곱 — 첫 달은 수익이 없어 월수익 여섯(06~11). 마지막 점이 실행한 달(2026-11, 부분 달)
DAYS = ("2026-05-29", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-30", "2026-11-02")
WARN_SECTOR = "업종 모멘텀: 업종 평균이 잡힌 종목이 22%(2021-11-01) — 60% 미만이라 조건이 거의 전부 통과한다."


def _db() -> sqlite3.Connection:
    k = sqlite3.connect(":memory:")
    k.executescript(
        "CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, market TEXT, status TEXT, trade_date TEXT,"
        " started_at TEXT, step_log TEXT);"
        "CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY, market TEXT, strategy TEXT, group_id TEXT, metrics_json TEXT,"
        " warnings_json TEXT, turnover_avg REAL, rebalances INT, calc_version INT, scoring_calc_version INT);"
        "CREATE TABLE backtest_curves (id INTEGER PRIMARY KEY, run_id INT, date TEXT, equity REAL);"
    )
    return k


def _실행(k: sqlite3.Connection, 그룹: str, 시장: str = "KR", 거래일: str = "2026-11-03", 기본: bool = True,
         상태: str = "success", 시작: str | None = None, 로그: dict | None = None) -> None:  # fmt: skip
    본 = {"group_id": 그룹, "params": {"default": 기본, "trigger": "schedule", "sha": "abc123"}, **(로그 or {})}
    k.execute("INSERT INTO batch_runs (job_name, market, status, trade_date, started_at, step_log) VALUES"
              " ('backtest', ?, ?, ?, ?, ?)", [시장, 상태, 거래일, 시작 or 거래일 + "T22:16:00", json.dumps(본)])  # fmt: skip


def _전략(k: sqlite3.Connection, 그룹: str, 전략: str, 월수익: list[float], 시장: str = "KR", cagr: float = 0.10,
         sharpe: float = 0.5, mdd: float = -0.30, 리밸: int = 60, 경고: list[str] | None = None) -> None:  # fmt: skip
    rid = k.execute(
        "INSERT INTO backtest_runs (market, strategy, group_id, metrics_json, warnings_json, turnover_avg, rebalances,"
        " calc_version, scoring_calc_version) VALUES (?, ?, ?, ?, ?, 0.3, ?, 9, 7)",
        [시장, 전략, 그룹, json.dumps({"cagr": cagr, "sharpe": sharpe, "mdd": mdd}), json.dumps(경고 or [], ensure_ascii=False),
         리밸],
    ).lastrowid  # fmt: skip
    eq = 1.0
    for i, d in enumerate(DAYS):
        if i:
            eq *= 1 + 월수익[i - 1]
        k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)", [rid, d, eq])


def _행(k: sqlite3.Connection) -> dict[tuple[str, str, str], dict]:
    return {(s, m, i): (json.loads(v) if v and v[:1] in "{[" else v) for s, m, i, v in k.execute(SQL)}


ZERO = [0.0] * 6


def test_통과_짝은_차이_월_초과_앞뒤_반을_두_계열로_본다() -> None:
    k = _db()
    _실행(k, "g1")
    _전략(k, "g1", "composite", ZERO, cagr=0.29, sharpe=1.0, mdd=-0.40)
    _전략(k, "g1", "ic_weighted", [0.01, 0.02, 0.03, 0.01, 0.02, 0.005], cagr=0.30, sharpe=1.1, mdd=-0.30)
    p = _행(k)[("pair", "KR", "ic_weighted")]
    assert p["base"] == "composite" and p["verdict"] == "통과"
    # 전 구간 여섯 달: 앞 셋 평균 0.02, 뒤 셋 평균 (0.01+0.02+0.005)/3
    assert p["all"][0] == 6 and p["all"][1] == pytest.approx(0.02) and p["all"][2] == pytest.approx(0.035 / 3)
    # 실행한 달(2026-11)을 뺀 다섯 달: 앞 ⌊5/2⌋ = 둘
    assert p["excl"][0] == 5 and p["excl"][1] == pytest.approx(0.015) and p["excl"][2] == pytest.approx(0.02)
    assert p["cagr"] == [0.30, 0.29] and p["sharpe"] == [1.1, 1.0] and p["mdd"] == [-0.30, -0.40]


def test_끝_달을_빼면_앞뒤_반_결론이_갈리는_줄은_모름() -> None:
    k = _db()
    _실행(k, "g1")
    _전략(k, "g1", "composite", ZERO)
    # 전 구간: 앞(0.02, 0.02, −0.10) 평균 음 → 실패. 끝 달 뺌: 앞(0.02, 0.02) 양·뒤(−0.10, 0.06, 0.06) 양 → 통과
    _전략(k, "g1", "ic_weighted", [0.02, 0.02, -0.10, 0.06, 0.06, 0.01], cagr=0.11, sharpe=0.6)
    assert _행(k)[("pair", "KR", "ic_weighted")]["verdict"] == "모름"


def test_CAGR_샤프_MDD_하나라도_못_넘으면_탈락() -> None:
    좋음 = [0.01] * 6
    for 칸, 기대 in ((dict(cagr=0.10), "탈락"), (dict(sharpe=0.5), "탈락"), (dict(mdd=-0.321), "탈락"),
                    (dict(mdd=-0.319), "통과")):  # fmt: skip
        k = _db()
        _실행(k, "g1")
        _전략(k, "g1", "composite", ZERO, cagr=0.10, sharpe=0.5, mdd=-0.30)
        값 = {"cagr": 0.12, "sharpe": 0.7, "mdd": -0.30, **칸}
        _전략(k, "g1", "shrink_missing", 좋음, **값)
        assert _행(k)[("pair", "KR", "shrink_missing")]["verdict"] == 기대, 칸


def test_앞뒤_반이_모두_양이_아니면_탈락() -> None:
    k = _db()
    _실행(k, "g1")
    _전략(k, "g1", "composite", ZERO)
    _전략(k, "g1", "ic_weighted", [-0.01, -0.01, -0.01, 0.05, 0.05, 0.05], cagr=0.12, sharpe=0.7)
    assert _행(k)[("pair", "KR", "ic_weighted")]["verdict"] == "탈락"


def test_리밸런스_36_미만과_업종_채움_경고는_판정_불가() -> None:
    k = _db()
    _실행(k, "g1")
    _전략(k, "g1", "composite", ZERO)
    _전략(k, "g1", "ic_weighted", [0.01] * 6, cagr=0.12, sharpe=0.7, 리밸=35)
    _전략(k, "g1", "momentum", ZERO, 경고=[WARN_SECTOR])
    _전략(k, "g1", "momentum_sector", [0.01] * 6, cagr=0.12, sharpe=0.7, 경고=[WARN_SECTOR])
    rows = _행(k)
    assert rows[("pair", "KR", "ic_weighted")]["verdict"] == "판정 불가"
    ms = rows[("pair", "KR", "momentum_sector")]
    assert ms["base"] == "momentum" and ms["low_coverage"] == 1 and ms["verdict"] == "판정 불가"


def test_업종_채움_경고는_momentum_sector_줄에만() -> None:
    """같은 실행 경고(`warnings`)는 모든 전략 행에 붙는다 — ic_weighted 줄을 판정 불가로 만들지 않는다"""
    k = _db()
    _실행(k, "g1")
    _전략(k, "g1", "composite", ZERO, 경고=[WARN_SECTOR])
    _전략(k, "g1", "ic_weighted", [0.01] * 6, cagr=0.12, sharpe=0.7, 경고=[WARN_SECTOR])
    p = _행(k)[("pair", "KR", "ic_weighted")]
    assert p["low_coverage"] == 0 and p["verdict"] == "통과"


def test_시장마다_판정_실행_하나만() -> None:
    k = _db()
    _실행(k, "early", 거래일="2026-11-02")  # 3~7일 밖
    _실행(k, "nd", 거래일="2026-11-03", 기본=False, 시작="2026-11-03T20:00:00")  # 기본 실행 아님
    _실행(k, "fail", 거래일="2026-11-03", 상태="failed", 시작="2026-11-03T21:00:00")
    _실행(k, "kr", 거래일="2026-11-03")
    _실행(k, "kr_late", 거래일="2026-11-04")  # 같은 달 뒤 실행 — 판정 실행이 아니다
    _실행(k, "us", 시장="US", 거래일="2026-11-03", 시작="2026-11-03T23:57:00")
    for 그룹, 시장 in (("early", "KR"), ("nd", "KR"), ("fail", "KR"), ("kr", "KR"), ("kr_late", "KR"), ("us", "US")):
        _전략(k, 그룹, "composite", ZERO, 시장=시장)
        _전략(k, 그룹, "ic_weighted", [0.01] * 6, 시장=시장, cagr=0.2 if 그룹 in ("kr", "us") else 0.01, sharpe=0.9)
    rows = _행(k)
    assert rows[("run", "KR", "judge_run")]["group_id"] == "kr" and rows[("run", "US", "judge_run")]["group_id"] == "us"
    assert rows[("run", "KR", "judge_run")]["trigger"] == "schedule" and rows[("run", "KR", "judge_run")]["sha"] == "abc123"
    assert rows[("pair", "KR", "ic_weighted")]["verdict"] == "통과" and rows[("pair", "US", "ic_weighted")]["verdict"] == "통과"
    assert rows[("strat", "KR", "ic_weighted")]["cagr"] == 0.2
    assert rows[("strat", "KR", "composite")]["scoring_calc"] == 7


def test_실행_기록_칸_IC_AB_52주_고점() -> None:
    k = _db()
    로그 = {
        "factor_ic": {"sector_mom": {"verdict": "판정 불가", "t": None, "months": 20, "coverage": 0.22},
                      "accrual": {"verdict": "탈락", "t": 0.8, "months": 58, "coverage": 0.7, "first_half": 0.01,
                                  "second_half": -0.002, "fdr": {"bh_pass": False}}},
        "factor_ab": {"variants": {"momentum_echo": {"by_market": {"KR": {"verdict": "유지", "rule_met": False}}}}},
        "high52_judge": {"verdict": "탈락", "why": ["CAGR ≤ momentum"]},
        "high52_overlap": {"vs_momentum_score": 0.71, "n": 40},
    }  # fmt: skip
    _실행(k, "g1", 로그=로그)
    rows = _행(k)
    assert rows[("ic", "KR", "sector_mom")]["verdict"] == "판정 불가"
    assert rows[("ic", "KR", "accrual")] == {"verdict": "탈락", "t": 0.8, "months": 58, "coverage": 0.7,
                                             "first_half": 0.01, "second_half": -0.002, "bh_pass": 0}  # fmt: skip
    assert rows[("ab", "KR", "momentum_echo.KR")] == {"verdict": "유지", "rule_met": 0}
    assert rows[("h52", "KR", "high52_judge")]["verdict"] == "탈락"
    assert rows[("h52", "KR", "high52_overlap")]["vs_momentum_score"] == 0.71


def test_일일_상관_환율_기록은_11_03_전후로_더한다() -> None:
    k = _db()
    for 날, 넘음, 시장 in (("2026-10-20", 1, "KR"), ("2026-11-03", 0, "KR"), ("2026-11-04", 2, "KR"), ("2026-11-04", 3, "US")):
        로그 = {"correlation": {"stocks": 9, "corr_async": {"pairs": 4, "raised": 2, "crossed": 넘음}}}
        if 시장 == "US":
            로그["fx_vol"] = {"lag0": {"n": 5, "kappa_over1": 0}, "lag1": {"n": 5, "kappa_over1": 1}}
        k.execute("INSERT INTO batch_runs (job_name, market, status, trade_date, started_at, step_log) VALUES"
                  " (?, ?, 'success', ?, ?, ?)", [f"daily_{시장.lower()}", 시장, 날, 날 + "T00:00:00", json.dumps(로그)])  # fmt: skip
    k.execute("INSERT INTO batch_runs (job_name, market, status, trade_date, started_at, step_log) VALUES"
              " ('daily_kr', 'KR', 'success', '2026-11-05', '', '{\"correlation\": {\"stocks\": 3}}')")  # 기록 없는 날
    rows = _행(k)
    assert rows[("daily", "KR", "corr_async_before_1103")] == {"runs": 1, "crossed": 1, "max_crossed": 1, "pairs": 4,
                                                               "first": "2026-10-20", "last": "2026-10-20"}  # fmt: skip
    assert rows[("daily", "KR", "corr_async_from_1103")]["runs"] == 2
    assert rows[("daily", "KR", "corr_async_from_1103")]["crossed"] == 2
    assert rows[("daily", "US", "fx_vol_from_1103")] == {"runs": 1, "lag1_over1": 1, "lag1_n": 5, "lag0_over1": 0,
                                                         "first": "2026-11-04", "last": "2026-11-04"}  # fmt: skip


def test_db_status_가_받는_읽기_전용_질의다() -> None:
    from scripts.db_status import is_read_only

    assert is_read_only(SQL)
    본문 = "\n".join(줄 for 줄 in SQL.splitlines() if not 줄.lstrip().startswith("--"))
    assert is_read_only(본문)
