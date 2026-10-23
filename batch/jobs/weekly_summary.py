"""주간 운영 요약 — 일요일 아침 텔레그램 한 통 (docs/health.md 7장, docs/infra.md 25.943).

지난 7일 동안 사람이(또는 세션이) `batch_runs` 를 훑어 손으로 확인하던 네 가지를 한 메시지로 보낸다.

  1. 실행 결과 — 작업마다 성공·부분·실패·건너뜀 수. 전부 성공이면 한 줄
  2. 되풀이된 경고·실패 사유 — 같은 머리말로 묶어 많은 순
  3. Turso 월 읽기 — 쓴 몫과 달의 진도(진도보다 앞서면 표시)
  4. 수정주가 — 국내 일일 배치가 "기업행위로 보이는 종목" 을 알린 뒤 `adjust_kr` 이 돌았는지
  5. 오래 안 돈 예약 작업 — 기대 주기를 넘긴 것

**읽기만 한다.** 쓰는 것은 자기 실행 기록(`batch_runs`) 한 줄뿐이다. 알림이지 판단이 아니다 —
점수·신호·매매를 바꾸지 않는다. 무응답 감시(docs/health.md 1~3장)는 "오늘 안 돈 것" 을 그날 알리고,
이것은 "지난주에 무슨 일이 있었나" 를 모아 말한다. 둘은 겹치지 않는다.

실행
  python -m batch.jobs.weekly_summary            # 보낸다
  python -m batch.jobs.weekly_summary --dry-run  # 찍기만
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.notify import telegram
from batch.services import adjust as adj
from batch.services import column_rot, postmortem

log = logging.getLogger("weekly_summary")

JOB_NAME = "weekly_summary"
#: 돌아보는 날수
WINDOW_DAYS = 7
#: 되풀이된 경고로 치는 최소 횟수. 한 번 난 것은 그날 실패 알림이 이미 말했다
REPEAT_MIN = 2
#: 경고를 묶는 머리말 길이 — 숫자·종목이 달라도 같은 종류면 한 줄로
PREFIX_LEN = 24
#: 한 절에 싣는 최대 줄 수. 텔레그램 한 조각(3,900자)에 들어가게
MAX_LINES = 8

#: **예약 작업의 기대 주기(일)** — 이보다 오래 성공이 없으면 "오래 안 돈 작업" 에 싣는다.
#: 값은 워크플로 cron 의 주기에 여유(휴장·한 번 건너뜀)를 더한 것이다. 주기가 바뀌면 여기도 바꾼다 —
#: `tests/test_weekly_summary_943.py` 가 예약 워크플로의 작업 이름이 여기 있는지 본다
EXPECTED_DAYS: dict[str, int] = {
    "daily_kr": 4, "daily_us": 4, "sentiment": 4, "monitor_targets": 4, "index_prices": 4,
    "portfolio": 4, "sell_flags": 4, "scores": 4, "signals": 4,  # 거래일마다. 연휴(최장 11일)는 오탐이 될 수 있다
    "kis_flows": 4,  # 거래일마다 (25.987). KIS 가 30일만 주므로 길게 멈추면 그 구간은 영영 빈다
    "disclosure_reaction": 4,  # 평일마다 모은다 (25.996). 빠져도 아무도 말하지 않았다 (25.1224)
    "broker_stats": 10,  # 금요일만 기록을 연다 (25.995)
    "metrics": 10, "universe": 10, "valuation_bands": 10, "signal_outcomes": 10, "earnings_calendar": 10,
    "backup": 10,  # 주 1회
    # 국내 재무는 **주 1회**(월요일, 화요일 재시도)다 — 월 1회 칸에 있어 다섯 번 내리 빠져도 41일째에야 말했다
    # (docs/infra.md 25.1285, 재무 감사 재현). 새 사업보고서가 점수에 늦게 드는 것을 한 달 넘게 몰랐다
    "financials": 10,
    # 배당은 **연 2회**(4·5월 5일, dividends.yml) — "월 2회 · 20일" 이라 적혀 한 해 열한 달
    # "마지막 실행 165일 전 (기대 20일 안)" 이 떴다(docs/infra.md 25.1371, 주간 요약 감사 재현).
    # 5월 실행 뒤 다음 4월까지 약 335일이라 그보다 넉넉히 둔다. /status(`health.ts` every: "year")와 같은 뜻
    "dividends": 370,
    "us_financials": 40, "sectors": 40, "accumulation": 40, "etf": 40, "etf_satellite": 40,  # 월 1회
    "backtest": 40,  # 매달 3일 (25.942)
}  # fmt: skip

_기업행위 = re.compile(r"기업행위로 보이는 종목 \d+개: (.+?)\.")
#: 일일 배치가 그 자리에서 수정주가를 다시 냈다는 표시 — `daily._detect_kr_actions` 의 문장과 같아야 한다 (25.1270)
바로_냄 = "국내 수정주가를 바로 다시 냈습니다"
_종목코드 = re.compile(r"\b(\d{6})\b")


@dataclass(frozen=True)
class Run:
    job_name: str
    market: str | None
    status: str
    started_at: str
    error_text: str | None
    warnings: tuple[str, ...]
    #: 건너뛴 까닭(`step_log.decision`) — 예정된 건너뜀을 가른다 (25.1372)
    decision: str | None = None


#: **예정된 건너뜀** — 고장이 아니다 (docs/infra.md 25.1372, 주간 요약 감사 재현). 미국 일일 배치는 서머타임을 덮으려고
#: 12:27·13:27 UTC(25.1678 부터 14:27 예비까지 셋) 걸려 있어 겨울에는 12:27 이 늘 "개장까지 123분 남아 아직
#: 이릅니다" 로 건너뛰고,
#: 휴장일은 트리거마다 건너뜀을 남긴다. 이것을 문제로 세어 겨울 내내 "daily_us: 성공 4 · 건너뜀 4" 가 실리고
#: "전부 성공" 이 나오지 않았다 — 진짜 놓침("이미 장이 끝났습니다")과 구별되지 않았다
_예정된_건너뜀 = ("휴장일", "아직 이릅니다")


def load_runs(client: TursoClient, since: str) -> list[Run]:
    rows = client.execute(
        "SELECT job_name, market, status, started_at, error_text, step_log FROM batch_runs"
        " WHERE started_at >= ? AND job_name <> ? ORDER BY started_at",
        [since, JOB_NAME],
    ).dicts()
    out: list[Run] = []
    for r in rows:
        경고: tuple[str, ...] = ()
        까닭: str | None = None
        try:
            로그 = json.loads(r["step_log"]) if r["step_log"] else {}
            if isinstance(로그, dict) and isinstance(로그.get("warnings"), list):
                경고 = tuple(str(w) for w in 로그["warnings"])
            if isinstance(로그, dict) and isinstance(로그.get("decision"), str):
                까닭 = 로그["decision"]
        except (TypeError, ValueError):
            pass
        out.append(Run(str(r["job_name"]), r["market"], str(r["status"]), str(r["started_at"]), r["error_text"], 경고,
                       까닭))  # fmt: skip
    return out


def load_last_success(client: TursoClient) -> dict[str, str]:
    """작업마다 마지막으로 **돈** 시각 — 성공과 부분 성공. 시장은 합친다(한쪽만 멈춘 것은 그날 무응답 감시가 말한다).

    `partial` 도 센다 (25.946 첫 실행에서 고침). 일일 배치는 포트폴리오 경고 하나로 거의 매일 partial 로 끝나,
    성공만 보면 "daily_kr 마지막 성공 18일 전" 처럼 **매일 돈 작업이 멈춘 것으로** 실렸다. 실패·건너뜀은 안 돈 것이다.
    """
    rows = client.execute(
        "SELECT job_name, MAX(finished_at) AS last FROM batch_runs WHERE status IN ('success', 'partial')"
        " GROUP BY job_name"
    ).rows
    return {str(j): str(t) for j, t in rows if t}


def status_counts(runs: list[Run]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in runs:
        칸 = out.setdefault(
            r.job_name,
            {"success": 0, "partial": 0, "failed": 0, "skipped": 0, "running": 0, "dryrun": 0, "planned_skip": 0},
        )
        # 예정된 건너뜀(휴장일·서머타임 이른 cron)은 문제가 아니다 (25.1372) — 따로 센다
        if r.status == "skipped" and r.decision and any(k in r.decision for k in _예정된_건너뜀):
            칸["planned_skip"] += 1
            continue
        # 손으로 돌린 확인 실행(`dryrun`, 보내지 않음)은 **미완이 아니다** (25.1274, 리포트 감사) — 예전엔 아는 다섯
        # 상태 밖이라 "미완" 으로 세어 멈춘 실행처럼 보였다
        칸[r.status if r.status in 칸 else "running"] += 1
    return out


def repeated_reasons(runs: list[Run]) -> list[tuple[str, int]]:
    """같은 머리말의 경고·실패 사유를 묶어 많은 순. `REPEAT_MIN` 미만은 뺀다 — 한 번 난 것은 그날 알림이 말했다."""
    묶음: dict[str, tuple[str, int]] = {}
    for r in runs:
        if r.status == "dryrun":
            continue  # 확인 실행의 경고는 운영의 되풀이가 아니다 (25.1304) — 한 번 난 경고가 확인 한 번으로 ×2 가 됐다
        글들 = list(r.warnings) + ([r.error_text] if r.status == "failed" and r.error_text else [])
        for 글 in 글들:
            머리 = 글[:PREFIX_LEN]
            보기, n = 묶음.get(머리, (글, 0))
            묶음[머리] = (보기, n + 1)
    return sorted(((보기, n) for 보기, n in 묶음.values() if n >= REPEAT_MIN), key=lambda x: -x[1])


def adjust_needed(runs: list[Run]) -> list[str]:
    """국내 일일 배치가 기업행위를 알린 종목 가운데, 그 알림 **뒤에** `adjust_kr` 성공 실행이 없는 것.

    `adjust_kr` 은 종목 하나만 돌릴 수 있어(25.924) 뒤 실행이 그 종목을 덮었다고 단정할 수 없다 — 그래서
    뒤 실행이 있으면 종목을 빼되, 글에는 실행 수를 함께 적는다(`compose`). 뒤 실행이 하나라도 있으면 빼는 쪽을
    골랐다 — 전 종목 실행(325만 행)을 부추기지 않으려는 선택이다.
    """
    수정실행 = sorted(r.started_at for r in runs if r.job_name == "adjust_kr" and r.status == "success")
    남은: dict[str, None] = {}
    for r in runs:
        if r.job_name != "daily_kr" or r.status == "dryrun":
            continue
        for w in r.warnings:
            m = _기업행위.search(w)
            if not m:
                continue
            # **일일 배치가 이미 바로 다시 낸 종목은 뺀다** (docs/infra.md 25.1270, 리포트 감사 재현). 25.1009 부터
            # 20종목까지는 그 자리에서 국내 수정주가를 다시 내고 "바로 다시 냈습니다" 라 적는데, 이 정규식이 그 문장에
            # 도 걸려 끝난 종목을 매주 "국내 수정주가" 로 다시 돌리라 했다(`adjust_stocks` 는 실행 기록이 없다)
            if 바로_냄 in w:
                continue
            if any(t > r.started_at for t in 수정실행):
                continue
            for code in _종목코드.findall(m.group(1)):
                남은[code] = None
    return list(남은)


def _외(전체: int) -> list[str]:
    """`MAX_LINES` 로 자른 목록의 나머지 수 — 표시 없이 자르면 나머지 멈춘 작업·실패가 알림에서 사라진다
    (25.1762, 결함 루프 L2)"""
    return [f"  … 외 {전체 - MAX_LINES}개 (웹 /status)"] if 전체 > MAX_LINES else []


def stale_jobs(last_success: dict[str, str], now: datetime) -> list[tuple[str, int | None]]:
    """기대 주기를 넘긴 작업. (이름, 며칠 전) — 한 번도 돈 적(성공·부분)이 없으면 None."""
    out: list[tuple[str, int | None]] = []
    for job, limit in EXPECTED_DAYS.items():
        last = last_success.get(job)
        if last is None:
            out.append((job, None))
            continue
        try:
            지난 = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
        except ValueError:
            continue
        if 지난 > limit:
            out.append((job, 지난))
    return out


#: 비용 규칙의 경고 선 — "80% 도달 시 경고, 100% 에서 중단" (CLAUDE.md 비용 규칙)
QUOTA_WARN_SHARE = 0.8


def reads_line(used: int | None, limit: int, now: datetime, label: str = "Turso 월 읽기") -> str:
    """Turso 월 읽기 — 쓴 몫과 달의 진도. 진도보다 앞서면 ⚠, **80% 를 넘으면 진도와 상관없이 ⚠** (docs/infra.md 25.1374,
    주간 요약 감사 재현). 예전에는 진도만 봐서 10-28 에 420M/500M(84%, 진도 90%)이 아무 표시 없이 나갔다 — 비용 규칙의
    "80% 경고" 가 텔레그램에는 없었다(/status 게이지에만 있었다)."""
    if used is None:
        return f"{label}: 재지 못했습니다"
    일수 = monthrange(now.year, now.month)[1]
    진도 = now.day / 일수
    몫 = used / limit if limit else 0.0
    표시 = (" ⚠ 진도보다 앞섭니다" if 몫 > 진도 + 0.05
            else f" ⚠ {QUOTA_WARN_SHARE:.0%} 넘음 — 한도 경고 선" if 몫 >= QUOTA_WARN_SHARE else "")  # fmt: skip
    return f"{label}: {used / 1e6:,.1f}M / {limit / 1e6:,.0f}M ({몫:.0%}, 달 진도 {진도:.0%}){표시}"


#: 사후 분석이 **어디까지 보고했나** — 보고한 진입일의 마지막 날 (배치만 쓰는 기록 키, 25.1151).
#: 예전에는 "진입일 36~27일 전" 창이었는데, 성적표는 화요일 06:07 KST 에 돌아 그때 시세가 그 전 금요일까지라
#: 일요일 기준 20거래일 성적이 나와 있는 진입일은 대개 창 **앞**이었다 — 2026 달력 모의 154 진입일 가운데
#: 13일만 보고됐다
POSTMORTEM_THROUGH_KEY = "postmortem_through"
#: 기록이 없거나 오래 묵었을 때 거슬러 볼 한계(달력일). 첫 실행이 몇 달치를 한꺼번에 내지 않게 막는다
POSTMORTEM_BACKSTOP_DAYS = 45
#: 사후 분석에서 따져 볼 손실의 상한 — 손실 하나마다 시세를 몇 줄씩 읽는다
POSTMORTEM_MAX = 30

#: 업종 평균도 **분할만 반영한 가격**으로 (25.1150, 감사 재현) — 손실 종목의 `ret_20d` 가 그 잣대다.
#: 원 종가로 내면 동종 6개 가운데 하나가 1:5 분할한 주에 업종 평균이 −16% 가 되고 주된 원인이 "업종" 으로 바뀌었다.
#: **두 날만 읽지 않는다** (25.1194) — 진입일이 국내 수정주가 가운데 빈 행이면 그 동종의 기준이 원 종가(10배)라
#: 같은 일이 났다. 앞뒤 `SECTOR_PAD_DAYS` 를 붙여 읽고 `adjust.split_only_prices` 로 메운 뒤 두 날 값을 견준다
SECTOR_PAD_DAYS = 10
SECTOR_ROWS_SQL = (
    "SELECT p.stock_id, p.date, p.adj_close, p.close, s.country FROM stocks s"
    " JOIN prices p ON p.stock_id = s.id AND p.date >= ? AND p.date <= ? AND p.close > 0"
    " WHERE s.country = ? AND s.sector = ? AND s.id <> ? AND s.asset_type = 'stock' ORDER BY p.stock_id, p.date"
)


def sector_avg(
    client: TursoClient, entry: str, exit_: str, country: str, sector: str, sid: int
) -> tuple[float | None, int]:
    """같은 나라·업종 다른 종목들의 진입일→끝날 평균 수익률과 종목 수. 두 날 값이 다 있는 종목만 센다."""
    시작 = (date.fromisoformat(entry[:10]) - timedelta(days=SECTOR_PAD_DAYS)).isoformat()
    끝 = (date.fromisoformat(exit_[:10]) + timedelta(days=SECTOR_PAD_DAYS)).isoformat()
    묶음: dict[int, list[dict]] = {}
    for sid_, d, a, c, ctry in client.execute(SECTOR_ROWS_SQL, [시작, 끝, country, sector, sid]).rows:
        묶음.setdefault(int(sid_), []).append({"date": d, "adj_close": a, "close": c, "country": ctry})
    수익: list[float] = []
    for 줄 in 묶음.values():
        값 = {str(r["date"]): px for r, px in zip(줄, adj.split_only_prices(줄, str(줄[0]["country"])), strict=True)}
        a, b = 값.get(entry), 값.get(exit_)
        if a and b and a > 0 and b > 0:
            수익.append(b / a - 1)
    return (sum(수익) / len(수익) if 수익 else None), len(수익)


def _나라별(through: str | dict | None) -> dict[str, str]:
    """보고 기록을 나라별로 (25.1269). 예전 기록(문자열 하나)은 두 나라에 같이 쓴다"""
    if isinstance(through, dict):
        return {str(k): str(v) for k, v in through.items() if v}
    return {"KR": through, "US": through} if through else {}


def load_losses(
    client: TursoClient, now: datetime, through: str | dict | None = None, 끝: list[dict] | None = None
) -> tuple[int, int, list[postmortem.Loss]]:
    """지난 보고(`through`) 뒤 20거래일 성적이 나온 신호와 그 가운데 손실의 몫 (docs/reports.md 3.8, 25.998·25.1151).

    `끝` 을 주면 이번에 본 진입일의 마지막 날을 **나라별로** 넣는다 — 보낸 뒤 `through` 로 적는다.

    **보고 기록은 나라마다 따로** (docs/infra.md 25.1269, 리포트 감사 재현). 예전엔 두 나라를 합친 최댓값 하나라,
    휴장일이 다른 주(국내 추석·한글날, 미국 추수감사절)에 성적이 덜 익은 나라의 사이 진입일이 다음 주에 익어도
    `entry_date > 기록` 에 걸려 **영영 보고되지 않았다** — 10-11 기준 국내는 09-07, 미국은 09-11 까지 익어 국내
    09-08~09-11 이 빠졌다."""
    한계 = (now - timedelta(days=POSTMORTEM_BACKSTOP_DAYS)).date().isoformat()
    앞 = _나라별(through)
    rows = client.execute(
        "SELECT o.stock_id, COALESCE(s.name_ko, s.name_en, s.ticker), o.horizon, o.entry_date, o.ret_20d,"
        " o.bench_ret_20d, s.sector, s.country FROM signal_outcomes o JOIN stocks s ON s.id = o.stock_id"
        # `days_available >= 20` 을 걸지 않는다 (25.1149) — 성적표는 상장폐지·거래정지로 끊긴 신호의 `ret_20d` 를
        # 마지막 종가로 채운다(25.696). 그 조건은 `ret_20d IS NOT NULL` 과 겹치고 **끊긴 손실만** 걸러 냈다(생존편향)
        " WHERE o.entry_date > CASE s.country WHEN 'KR' THEN ? ELSE ? END AND o.ret_20d IS NOT NULL",
        [max(앞.get("KR", ""), 한계), max(앞.get("US", ""), 한계)],
    ).rows
    if 끝 is not None and rows:
        새 = dict(앞)
        for r in rows:
            나라 = "KR" if str(r[7]) == "KR" else "US"
            새[나라] = max(새.get(나라, ""), str(r[3]))
        끝.append(새)
    # **한 종목·한 진입일은 한 번만 센다** (docs/infra.md 25.1271, 리포트 감사 재현). 같은 날 단기·중기·장기 신호가
    # 함께 나면 진입가·20거래일 수익이 같은 줄이 기간마다 와서, 손실·원인 집계가 세 배가 되고 예 세 자리가 같은 종목으로
    # 찼다(`self_grade` 는 이미 종목·날로 하나만 센다). 기간은 "short·mid" 처럼 함께 적는다
    묶음: dict[tuple[int, str], list] = {}
    for r in rows:
        묶음.setdefault((int(r[0]), str(r[3])), []).append(r)
    순서 = {"short": 0, "mid": 1, "long": 2}
    rows = [[*줄[0][:2], "·".join(sorted({str(x[2]) for x in 줄}, key=lambda h: 순서.get(h, 9))), *줄[0][3:]]
            for 줄 in 묶음.values()]  # fmt: skip
    losses = []
    손실들 = sorted((r for r in rows if float(r[4]) < 0), key=lambda r: float(r[4]))
    for r in 손실들[:POSTMORTEM_MAX]:
        sid, entry = int(r[0]), str(r[3])
        # 20거래일째. 끊긴 신호는 그만큼 없으니 마지막 종가 날 — 성적표가 `ret_20d` 를 채운 그 날이다 (25.1149)
        exit_ = client.execute(
            "SELECT date FROM prices WHERE stock_id = ? AND date > ? ORDER BY date LIMIT 1 OFFSET 19", [sid, entry]
        ).scalar() or client.execute(
            "SELECT MAX(date) FROM prices WHERE stock_id = ? AND date > ?", [sid, entry]
        ).scalar()
        sec_ret, peers = None, 0
        if exit_ and r[6]:
            sec_ret, peers = sector_avg(client, entry, str(exit_), str(r[7]), str(r[6]), sid)
        공시 = client.execute(
            "SELECT title FROM disclosures WHERE stock_id = ? AND disclosed_at > ? AND disclosed_at <= ?"
            " ORDER BY disclosed_at LIMIT 1",
            [sid, entry, exit_ or entry],
        ).scalar()
        losses.append(postmortem.Loss(str(r[1]), str(r[2]), float(r[4]), None if r[5] is None else float(r[5]),
                                      sec_ret, peers,
                                      str(공시) if 공시 else None))  # fmt: skip
    return len(rows), len(손실들), losses


#: `compose` 의 쓰기 줄 — 넘기지 않았다는 표시 (None 은 "못 읽음" 이라 따로 둔다)
_안줌: object = object()


def compose(
    runs: list[Run], last_success: dict[str, str], now: datetime, reads_used: int | None,
    read_limit: int = db.TURSO_MONTHLY_READ_LIMIT,
    rot_lines: list[str] | None = None,
    postmortem_lines: list[str] | None = None,
    writes_used: int | None | object = _안줌,
    paper_lines: list[str] | None = None,
) -> str:
    """메시지 본문. 순수 함수 — 테스트가 그대로 돌린다.

    `writes_used` 는 이번 달 쓴 Turso 쓰기 행(못 읽으면 None, 25.1380). 넘기지 않으면 쓰기 줄을 싣지 않는다.

    `rot_lines` 는 데이터 열 점검 절(`services/column_rot.render`, docs/health.md 7장). None 이면 절을 싣지 않는다.
    """
    # **머리 날짜는 사용자(한국) 날짜** (docs/infra.md 25.1274, 리포트 감사 재현). 예약은 일 07:41 KST = 토 22:41 UTC 라
    # UTC 날짜로 적으면 일요일에 받은 요약이 "10-03 ~ 10-10" 으로 하루 늦었다 — 실제로 덮는 창은 한국 10-04 ~ 10-11 이다
    끝 = now.astimezone(cal.market_tz("KR")).date() if now.tzinfo else now.date()
    시작 = 끝 - timedelta(days=WINDOW_DAYS)
    lines = [f"[주간 운영 요약] {시작} ~ {끝}", ""]

    counts = status_counts(runs)
    문제 = {j: c for j, c in counts.items() if c["partial"] or c["failed"] or c["skipped"] or c["running"]}
    총 = sum(sum(c.values()) for c in counts.values())
    lines.append(f"실행 {총}건" + ("" if 문제 else " — 전부 성공"))
    심한순 = sorted(문제.items(), key=lambda x: -(x[1]["failed"] * 3 + x[1]["partial"] + x[1]["skipped"]))
    for j, c in 심한순[:MAX_LINES]:
        조각 = [f"성공 {c['success']}"]
        조각 += [f"{이름} {c[키]}" for 키, 이름 in (("partial", "부분"), ("failed", "실패"), ("skipped", "건너뜀"),
                                                      ("running", "미완"), ("dryrun", "확인 실행"))
                 if c[키]]  # fmt: skip
        lines.append(f"  {j}: {' · '.join(조각)}")
    lines += _외(len(심한순))

    되풀이 = repeated_reasons(runs)
    if 되풀이:
        lines += ["", f"되풀이된 경고·실패 사유 ({REPEAT_MIN}회 이상)"]
        for 글, n in 되풀이[:MAX_LINES]:
            한줄 = 글.replace("\n", " ")
            lines.append(f"  ×{n} {한줄[:120]}{'…' if len(한줄) > 120 else ''}")
        lines += _외(len(되풀이))

    lines += ["", reads_line(reads_used, read_limit, now)]
    # **월 쓰기도 함께** (docs/infra.md 25.1380, 주간 요약 감사) — 9월에 계정을 막은 것은 쓰기 한도였는데 요약에 없었다.
    # 같은 80% 규칙. 값을 주지 않은 옛 호출(시험)은 줄을 싣지 않는다
    if writes_used is not _안줌:
        쓴 = writes_used if isinstance(writes_used, int) else None
        lines.append(reads_line(쓴, db.TURSO_MONTHLY_WRITE_LIMIT, now, "Turso 월 쓰기"))

    남은 = adjust_needed(runs)
    수정실행 = sum(1 for r in runs if r.job_name == "adjust_kr" and r.status == "success")
    if 남은:
        lines += ["", f"국내 수정주가를 다시 내야 할 종목 {len(남은)}개: {', '.join(남은[:10])}"
                  + (" …" if len(남은) > 10 else "")
                  + ' — Actions → "국내 수정주가" (종목 하나씩 돌리면 읽기가 작습니다)']  # fmt: skip
    elif 수정실행:
        lines += ["", f"국내 수정주가: 이번 주 {수정실행}번 돌렸고 그 뒤 새 기업행위 알림은 없습니다"]

    묵은 = stale_jobs(last_success, now)
    if 묵은:
        lines += ["", "기대 주기를 넘긴 예약 작업"]
        # 심한 것부터 — 기대 주기 대비 넘긴 정도, 돈 기록 없음이 맨 앞(25.1762: 정의 순서로 앞 8개만 실려
        # 뒤의 멈춘 작업이 안 보였다)
        묵은 = sorted(묵은, key=lambda x: -(float("inf") if x[1] is None else x[1] / EXPECTED_DAYS[x[0]]))
        for job, 지난 in 묵은[:MAX_LINES]:
            언제 = "돈 기록 없음" if 지난 is None else f"마지막 실행 {지난}일 전"
            lines.append(f"  {job}: {언제} (기대 {EXPECTED_DAYS[job]}일 안)")
        lines += _외(len(묵은))

    if rot_lines:
        lines += ["", *rot_lines]

    # 틀린 추천 사후 분석 (docs/reports.md 3.8, 25.998)
    if postmortem_lines:
        lines += ["", *postmortem_lines]

    # 모의 투자 계좌 (docs/paper.md 6장, 25.1447)
    if paper_lines:
        lines += ["", *paper_lines]

    lines += ["", "자세한 것은 웹 /status 와 이슈 #1 의 운영 출력에 있습니다"]
    return "\n".join(lines)


#: 다시 보내기 막기 (25.1015) — 늦게 온 GitHub 예약과 따라잡기가 겹쳐도 한 통. **같은 주(한국 일요일 기준)에 보낸 것만**
#: 막는다 (docs/infra.md 25.1375, 주간 요약 감사). 예전에는 "5일 안에 보낸 것" 이라, 따라잡기가 화요일 낮에 늦게 보낸
#: 요약이 나흘 열 시간 뒤 토요일 밤(한국 일요일 아침) 정규 예약을 막아 그다음 주 요약이 통째로 빠졌다
RESEND_GUARD_DAYS = 8
ALREADY_SENT_SQL = (
    "SELECT MAX(started_at) FROM batch_runs WHERE job_name = 'weekly_summary' AND status IN ('success', 'partial')"
    " AND started_at >= ?"
)


#: 실행 품질 (docs/paper.md 13장, 25.1452) — 실제 매매·리포트 주문표·매도 플래그를 읽기만 한다
EXEC_TRADES_SQL = "SELECT stock_id, side, trade_date, price FROM trades WHERE trade_date >= ? AND trade_date <= ?"
EXEC_ITEMS_SQL = (
    "SELECT r.trade_date AS report_date, i.stock_id, i.payload_json FROM daily_reports r"
    " CROSS JOIN report_items i ON i.report_id = r.id"
    " WHERE r.trade_date >= ? AND r.trade_date <= ? AND i.section = ? AND i.stock_id IS NOT NULL"
)
EXEC_FLAGS_SQL = (
    "SELECT stock_id, reason_code, level, first_seen_date AS first_seen, MAX(as_of_date) AS last_seen"
    " FROM sell_flags"
    # 발생(first_seen_date)마다 따로 — 묶으면 꺼졌다 다시 선 플래그가 첫 발생에 합쳐져 그 사이 매매를 덮었다(25.1577)
    " WHERE as_of_date >= ? GROUP BY stock_id, reason_code, level, first_seen_date"
)


#: 실행 품질 절을 실은 달("YYYY-MM") — 가상모의투자 월간 복기 표시(`paper.MONTHLY_REVIEW_KEY`)와 따로
#: (25.1760, 결함 루프 L2: 한 표시를 같이 쓰면 첫 일요일에 이 절만 깨진 달은 8~14일 따라잡기가 없어 통째로 빠지고,
#: 저 절만 깨진 달은 이 절이 두 번 나갔다)
EXEC_REVIEW_KEY = "exec_quality_review_sent"


def execution_due(client: TursoClient, now: datetime) -> bool:
    """이번 요약에 지난달 실행 품질 절을 싣나 — 1~7일, 못 실었으면 14일까지 따라잡기(25.1571·25.1760)"""
    from batch.jobs import paper as paper_job

    return paper_job.review_due(client, now.astimezone(cal.market_tz("KR")).date(), EXEC_REVIEW_KEY)


def execution_lines(client: TursoClient, now: datetime) -> list[str]:
    """월간 복기의 실행 품질 절 — 한국 날짜로 그달 1~7일에 지난달을 (docs/paper.md 13장, 25.1452)."""
    from batch.services import execution_quality as eq
    from batch.services import reports as rp_store

    오늘 = now.astimezone(cal.market_tz("KR")).date()
    if not execution_due(client, now):
        return []
    끝 = 오늘.replace(day=1) - timedelta(days=1)
    시작 = 끝.replace(day=1)
    앞 = (시작 - timedelta(days=eq.PLAN_LOOKBACK_DAYS)).isoformat()
    매매 = client.execute(EXEC_TRADES_SQL, [시작.isoformat(), 끝.isoformat()]).dicts()
    if not 매매:
        return []
    계획, 추천 = [], []
    for 절, 담을곳 in ((rp_store.SECTION_BUY, 계획), (rp_store.SECTION_RECOMMEND, 추천)):
        for r in client.execute(EXEC_ITEMS_SQL, [앞, 끝.isoformat(), 절]).dicts():
            짐 = json.loads(r["payload_json"] or "{}")
            담을곳.append({"stock_id": r["stock_id"], "report_date": r["report_date"],
                         "prices": [t.get("price_at_or_below") for t in 짐.get("tranches") or []]})  # fmt: skip
    플래그 = client.execute(EXEC_FLAGS_SQL, [(시작 - timedelta(days=eq.FLAG_LOOKBACK_DAYS)).isoformat()]).dicts()
    return eq.lines(eq.measure(매매, 계획, 추천, 플래그, 시작.isoformat(), 끝.isoformat()))


def week_key(moment: datetime) -> str:
    """그 시각이 속한 주 — 한국 날짜로 그 시각 이하의 가장 가까운 일요일.
    요약은 일요일 아침(한국)에 지난 7일을 보낸다"""
    하루 = moment.astimezone(cal.market_tz("KR")).date() if moment.tzinfo else moment.date()
    return (하루 - timedelta(days=(하루.weekday() + 1) % 7)).isoformat()


def already_sent_this_week(last_sent: str | None, now: datetime) -> bool:
    if not last_sent:
        return False
    try:
        보낸때 = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00"))
    except ValueError:
        return True  # 읽지 못하면 보낸 것으로 — 두 통보다 한 통이 낫다(예전 동작)
    if 보낸때.tzinfo is None:
        보낸때 = 보낸때.replace(tzinfo=UTC)
    return week_key(보낸때) == week_key(now)


def run(dry_run: bool = False, force: bool = False) -> int:
    client = TursoClient()
    now = datetime.now(UTC)
    if not (dry_run or force):
        보냄 = client.execute(ALREADY_SENT_SQL, [(now - timedelta(days=RESEND_GUARD_DAYS)).isoformat()]).scalar()
        if already_sent_this_week(보냄, now):
            print(f"주간 요약: {str(보냄)[:16]} 에 이미 보냈습니다 — 건너뜀 (--force 로 다시 보낸다)")
            client.close()
            return 0
    run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=now.date().isoformat())
    try:
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
        runs = load_runs(client, since)
        last = load_last_success(client)
        남음 = db.remaining_read_budget_or_none(client)
        used = None if 남음 is None else db.TURSO_MONTHLY_READ_LIMIT - 남음
        쓰기남음 = db.remaining_write_budget_or_none(client)
        쓰기 = None if 쓰기남음 is None else db.TURSO_MONTHLY_WRITE_LIMIT - 쓰기남음
        # 조용히 썩는 열 (docs/health.md 7장, 25.948) — 최근 7일을 그 전 90일과 견준다. 표 하나가 실패해도 요약은 나간다
        rot, checked, rot_errors = column_rot.scan(
            client,
            (now - timedelta(days=column_rot.RECENT_DAYS)).date().isoformat(),
            (now - timedelta(days=column_rot.RECENT_DAYS + column_rot.BASELINE_DAYS)).date().isoformat(),
        )
        rot_lines = column_rot.render(rot, checked, MAX_LINES) + [f"  (못 본 표: {e})" for e in rot_errors[:2]]
        # 틀린 추천 사후 분석 (25.998) — 읽기에 실패하면 절만 빠진다(요약은 나간다)
        본_끝: list[dict] = []
        try:
            총, 손실수, 손실 = load_losses(client, now, db.get_setting(client, POSTMORTEM_THROUGH_KEY), 본_끝)
            사후 = postmortem.render(총, 손실, 손실수)
        except Exception as exc:  # noqa: BLE001 — 곁다리 절이다
            사후 = [f"지난 추천 사후 분석을 못 했습니다 ({type(exc).__name__})"]
        # 모의 투자 계좌 (docs/paper.md 6장, 25.1447) — 곁다리 절. 못 읽으면 그렇다고 한 줄
        월간 = False
        try:
            from batch.jobs import paper as paper_job

            모의 = paper_job.summary_section(client, now)
            # 월간 복기를 **실제로 실었을 때만** 표시한다
            # — 절이 깨져 오류 한 줄만 나간 날에 표시하면 따라잡기가 사라졌다
            # (25.1572, 25.1571 반박 [권고] 4)
            월간 = any("월간 복기" in x for x in 모의)
        except Exception as exc:  # noqa: BLE001 — 곁다리 절이다
            모의 = [f"가상모의투자 절을 만들지 못했습니다 ({type(exc).__name__})"]
        # 실전 준비도 한 장 (25.1735, 아이디어 ④) — 운용 요약 마지막 줄의 관문·준비 목록. 맨 위에 — 시스템의 목적이다.
        # 읽기 한 행. 못 읽으면 그 한 줄만
        try:
            from batch.jobs import desk as desk_job
            from batch.services import desk as desk_svc

            줄 = client.execute(desk_job.DESK_LAST_SQL).rows
            요약 = json.loads(줄[0][0]) if 줄 and 줄[0][0] else {}
            준비 = desk_svc.readiness_lines(요약.get("gate"), 요약.get("readiness"), 요약.get("gate_error"))
            모의 = [*준비, "", *모의]
        except Exception as exc:  # noqa: BLE001 — 곁다리 절이다
            모의 = [f"실전 준비도를 만들지 못했습니다 ({type(exc).__name__})", "", *모의]
        # 실제 매매의 실행 품질 (docs/paper.md 13장, 25.1452) — 월 첫 요약에만. 곁다리. **깨지지 않고 만들었을 때만**
        # 그달을 표시한다(매매가 없어 빈 절이면 "보낼 것 없음" 이라 표시 — 25.1760)
        실행 = False
        try:
            실행_때 = execution_due(client, now)
            모의 = [*모의, *execution_lines(client, now)]
            실행 = 실행_때
        except Exception as exc:  # noqa: BLE001 — 곁다리 절이다
            모의 = [*모의, f"실행 품질 절을 만들지 못했습니다 ({type(exc).__name__})"]
        text = compose(runs=runs, last_success=last, now=now, reads_used=used, writes_used=쓰기, rot_lines=rot_lines,
                       postmortem_lines=사후, paper_lines=모의)  # fmt: skip
        print(text)
        sent = 0
        뒤경고: list[str] = []
        if not dry_run:
            sent = len(telegram.send(text))
            # 보낸 뒤에만 넘긴다 — 찍기만 하는 실행이 다음 주 보고를 먹지 않게
            if 본_끝:
                # **보낸 뒤의 실패는 실행을 실패로 만들지 않는다** (docs/infra.md 25.1225). 예전에는 여기서 올라가
                # `failed` 로 닫혔고, 다시 보내지 않는 검사는 성공만 봐 따라잡기·재실행이 요약을 한 번 더 보냈다
                try:
                    db.set_setting(client, POSTMORTEM_THROUGH_KEY, 본_끝[0])
                except Exception as exc:  # noqa: BLE001 — 이미 보냈다. 다음 주 사후 분석이 같은 손실을 한 번 더 말할 뿐이다
                    뒤경고.append(f"사후 분석 진척을 적지 못함: {type(exc).__name__}")
            if 월간:
                # 월간 복기를 실어 보냈다 — 8~14일 따라잡기가 두 번 싣지 않게(25.1571)
                try:
                    from batch.jobs import paper as paper_job

                    db.set_setting(client, paper_job.MONTHLY_REVIEW_KEY,
                                   now.astimezone(cal.market_tz("KR")).date().strftime("%Y-%m"))  # fmt: skip
                except Exception as exc:  # noqa: BLE001 — 이미 보냈다. 다음 주에 한 번 더 실릴 뿐이다
                    뒤경고.append(f"월간 복기 표시를 적지 못함: {type(exc).__name__}")
            if 실행:
                try:
                    db.set_setting(client, EXEC_REVIEW_KEY,
                                   now.astimezone(cal.market_tz("KR")).date().strftime("%Y-%m"))  # fmt: skip
                except Exception as exc:  # noqa: BLE001 — 이미 보냈다. 다음 주에 한 번 더 실릴 뿐이다
                    뒤경고.append(f"실행 품질 표시를 적지 못함: {type(exc).__name__}")
        db.finish_batch_run(
            # **확인 실행은 `dryrun`** (docs/infra.md 25.1304, 운영 감사 재현) — 예전엔 `success` 로 닫아 다시
            # 보내지 않는 검사가 "이미 보냈다" 로 읽었다. 화·수에 손으로 확인하면 그 주 일요일 요약이 통째로 빠지고
            # /status 는 초록이었다. 일일 배치(`daily.py`)도 `dryrun` 이다
            client, run_id, status="dryrun" if dry_run else "partial" if 뒤경고 else "success",
            step_log={"runs": len(runs), "sent_chunks": sent, "stale": [j for j, _ in stale_jobs(last, now)],
                      "adjust_needed": adjust_needed(runs),
                      "rot": [f.text for f in rot], "rot_checked": checked, "rot_errors": rot_errors,
                      **({"warnings": 뒤경고} if 뒤경고 else {})},
        )  # fmt: skip
        return 0
    except Exception as exc:
        if not db.quota_reason(exc):  # 한도는 `guard()` 가 skipped 로 닫는다 (docs/infra.md 25.1223)
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
        raise
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="주간 운영 요약")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 찍기만")
    parser.add_argument("--force", action="store_true", help="이번 주에 이미 보냈어도 다시 보낸다")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(guard(main))
