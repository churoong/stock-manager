"""Turso 가 풀리면 자동으로 돌아간다 (docs/infra.md 25.12). `turso-return.yml` 이 부른다.

`DB_BACKEND=auto` 일 때만 일한다. 순서가 곧 안전장치다.

  1. 이미 돌아갔으면(복귀 표시가 있으면) 아무것도 하지 않는다
  2. Turso 가 아직 막혀 있으면 아무것도 하지 않는다 (웹이 수시로 다시 본다)
  3. Turso 에 마이그레이션을 먼저 적용한다 — 막혀 있던 동안 새로 생긴 표(0028~)가 없다
  4. D1 에서 사람이 넣은 데이터를 옮긴다 (종목 번호를 종목코드로 다시 맞춘다, move_user_data)
  5. **복귀 표시**를 D1 에 적는다. 이 뒤로는 Turso 가 다시 막혀도 auto 가 D1 로 튀지 않는다
  6. 텔레그램으로 한 번 알린다

GitHub Actions 출력 `moved=true` 를 남기면 워크플로의 뒤 단계(빠진 날 백필·포트폴리오 재계산)가 돈다.
백필 시작일 `backfill_from` 도 여기서 정한다 — **Turso 의 마지막 국내 시세 다음 날**이다. 날짜를 박지 않는다
(리셋 날짜도, D1 을 쓰기 시작한 날도 운영이 정하는 것이지 코드가 아는 것이 아니다).

누가 부르나: 웹이 Turso 가 풀린 것을 보고 repository_dispatch 로 깨운다(web/lib/tursoWatch.ts).
예비로 하루 한 번 예약 실행도 있다. 몇 번을 불러도 한 번만 옮긴다.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from batch.core import client as backend  # noqa: E402
from batch.core import db  # noqa: E402
from batch.core.entry import NOTHING_DONE  # noqa: E402


def open_d1():
    from batch.core.d1 import D1Client

    return D1Client()


def open_turso():
    from batch.core.turso import TursoClient

    return TursoClient()


def notify(message: str) -> None:
    try:
        from batch.notify import telegram

        telegram.send(message)
    except Exception as exc:  # noqa: BLE001 — 알림이 복귀를 막으면 안 된다
        print(f"텔레그램 알림 실패: {exc}")


@dataclass
class Outcome:
    moved: bool
    backfill_from: str | None = None  # 빠진 국내 시세를 받기 시작할 날 (moved 일 때만)
    #: 미국 시세·지수·환율·공시를 거슬러 받을 달력일. 0 이면 그 단계를 건너뛴다 (docs/infra.md 25.82·25.83)
    us_days: int = 0
    index_days: int = 0
    fx_days: int = 0
    disclosure_days: int = 0


#: Turso 의 마지막 국내 시세를 모를 때 거슬러 받을 날 수. 이미 있는 날은 백필이 건너뛰므로 넉넉해도 된다
FALLBACK_DAYS = 60
#: 구멍을 메울 때 더 얹는 여유 달력일. 경계 하루를 놓치면 그날이 영영 빈다
CATCHUP_MARGIN = 3
#: 이 워크플로가 메울 수 있는 최대 달력일. 이보다 크면 **따로 돌릴 일**이다
#: (backfill-us.yml 은 종목 구간으로 나눠 도는 큰 작업이다). 넘으면 여기까지만 메우고 말한다
MAX_CATCHUP_DAYS = 120


def backfill_start(dst) -> str:
    """Turso 에 빠진 국내 시세의 시작일 = 마지막 국내 시세 다음 날 (idx_prices_date 로 바로 찾는다, 0026)."""
    last = dst.execute(
        "SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'KR'"
    ).scalar()
    if last:
        return (date.fromisoformat(str(last)[:10]) + timedelta(days=1)).isoformat()
    return fallback_start()


def fallback_start() -> str:
    """마지막 시세를 모를 때 거슬러 받을 시작일. 이미 있는 날은 백필이 건너뛴다."""
    return (datetime.now(UTC).date() - timedelta(days=FALLBACK_DAYS)).isoformat()


def 메울_날수(마지막: str | None, 오늘: date, 없을때: str) -> tuple[int, str]:
    """(거슬러 받을 달력일, 로그에 적을 말). 0 이면 이 워크플로가 하지 않는다.

    **국내와 같은 원칙이다 — 날짜를 박지 않는다** (2026-09-18 사용자 요청).
    Turso 리셋 날짜를 모르므로 구멍의 크기도 모른다. 데이터에 물어본다.
    """
    if not 마지막:
        return 0, 없을때
    빈날 = (오늘 - date.fromisoformat(str(마지막)[:10])).days
    if 빈날 <= 1:
        return 0, f"구멍 없음 (마지막 {마지막})"
    날수 = 빈날 + CATCHUP_MARGIN
    if 날수 > MAX_CATCHUP_DAYS:
        return MAX_CATCHUP_DAYS, (
            f"구멍이 {빈날}일이라 이 워크플로가 다 메우지 못한다 (마지막 {마지막}). "
            f"{MAX_CATCHUP_DAYS}일만 메우고, 나머지는 backfill-us.yml 로 따로 돌린다"
        )
    return 날수, f"{마지막} 이후 {빈날}일이 비었다 — {날수}일을 거슬러 받는다"


def us_catchup(dst, 오늘: date) -> tuple[int, str]:
    """미국 시세의 구멍 (docs/infra.md 25.82).

    **국내에만 있던 일이다.** 2단계는 `backfill_kr` 뿐이고, 미국은 일일 배치의
    `US_LOOKBACK_DAYS=10` **달력일 고정 창**에 기대고 있었다. Turso 는 2026-09-17 에
    잠겼는데 복귀는 빨라야 10/1 이다 — 구멍이 10일보다 크면 그만큼 **영영 빈다.**
    """
    마지막 = dst.execute(
        "SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'US'"
    ).scalar()
    return 메울_날수(마지막, 오늘, "미국 시세가 아예 없다 — backfill-us.yml 로 따로 받는다")


def index_catchup(dst, 오늘: date) -> tuple[int, str]:
    """지수 일봉의 구멍 (docs/infra.md 25.82).

    지수도 `index_prices.LOOKBACK_DAYS=10` 고정 창이다. 여기가 비면 **베타**(25.65)와
    추세 필터 국면이 함께 틀어진다. 베타는 날짜를 맞춰 계산하므로(`metrics.align`)
    빈 날이 양쪽에서 빠지지만, **구멍을 사이에 둔 하루치 수익률이 2주치가 되어**
    변동성·샤프·MDD 를 왜곡한다. 값이 비는 것보다 나쁘다 — 틀린 값이 그럴듯하게 나온다.
    """
    마지막 = dst.execute("SELECT MAX(date) FROM index_prices").scalar()
    return 메울_날수(마지막, 오늘, "지수가 아예 없다 — index_prices --lookback 2000 을 따로 돌린다")


def fx_catchup(dst, 오늘: date) -> tuple[int, str]:
    """환율(USDKRW)의 구멍 (docs/infra.md 25.83).

    **가장 늦게 눈치채는 자리다.** 환율은 `fx.LOOKBACK_DAYS = 10` 고정 창인데, 그것을 부르는
    것이 **미국 일일 배치**다 — D1 로 운영하는 동안 쉬는 바로 그것이다(25.14).
    그리고 복귀 3단계는 `portfolio.yml` 과 달리 환율 단계 없이 재계산만 부른다.

    비어도 **실패하지 않는다.** `portfolio.value_series` 의 `_carry` 가 빈 날을 앞 값으로
    이어 붙인다. 그래서 미국 보유 종목의 원화 평가액이 **2주 묵은 환율**로 조용히 계산되고,
    화면에는 오늘 날짜로 찍힌다. 25.82 와 같은 모양이다 — 값이 비는 것이 아니라 틀린 값이 나온다.

    쌍 이름을 여기서 다시 적지 않는다(`fx.PAIR`). 25.65 가 코드를 잘못 적어 둔 항목이다.
    """
    from batch.services import fx

    마지막 = dst.execute("SELECT MAX(date) FROM fx_rates WHERE pair = ?", [fx.PAIR]).scalar()
    return 메울_날수(마지막, 오늘, "환율이 아예 없다 — fx --lookback 1900 을 따로 돌린다")


def disclosure_catchup(dst, 오늘: date) -> tuple[int, str]:
    """국내 공시의 구멍 (docs/infra.md 25.83).

    `disclosures_kr.DEFAULT_DAYS = 7` — 주석 문구까지 환율과 같다("며칠 빠져도 메워지도록 한 주").
    국내 일일 배치 안에서 도는데, 그 배치는 D1 을 보고 있었다. Turso 쪽은 잠긴 날에 멈춰 있다.

    **메우는 값이 공짜다.** 회사당 호출 1회이고 날짜 구간은 그 한 호출의 인자다 —
    7일을 받든 30일을 받든 **호출 수가 같다.** 안 메울 이유가 없다.

    시세·환율과 달리 여기는 앞 값을 이어 붙이지 않는다. 그냥 **기록에 구멍이 남고**,
    그동안 국내 감성이 공시 없이 계산된다(CLAUDE.md 뉴스 감성: RSS + DART 공시).
    """
    마지막 = dst.execute(
        "SELECT MAX(d.disclosed_at) FROM disclosures d JOIN stocks s ON s.id = d.stock_id"
        " WHERE s.country = 'KR'"
    ).scalar()
    return 메울_날수(마지막, 오늘, "국내 공시가 아예 없다 — disclosures_kr --days 90 --all 을 따로 돌린다")


def set_output(outcome: Outcome) -> None:
    """워크플로가 읽을 값을 적는다.

    **`moved=true` 면 `backfill_from` 도 반드시 적는다** (2026-09-21, docs/infra.md 25.57).
    예전에는 값이 있을 때만 적었다. 그러면 뒤 단계가

        python -m batch.jobs.backfill_kr --from ""

    를 부르게 되고, 빈 날짜로는 구간을 만들 수 없어 종료코드 2 로 죽는다. 그 순간
    **복귀 표시는 이미 적혀 있다** — 앱은 Turso 를 보는데 D1 로 운영한 동안의 시세가
    없고, 3번(포트폴리오 재계산)·4번(감시 대상)도 돌지 않는다. 2026-09-19 에 따라잡기가
    같은 이유(`--from` 만 주고 구간이 안 잡힘)로 매번 죽은 적이 있다(25.16).

    지금 코드에서 `backfill_start()` 는 늘 날짜를 돌려주므로 닿지 않는 길이다.
    그래도 막아 둔다 — 닿지 않는다는 것은 **오늘의 사실**이지 약속이 아니다.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"moved={'true' if outcome.moved else 'false'}\n")
            if outcome.moved:
                out.write(f"backfill_from={outcome.backfill_from or fallback_start()}\n")
                # 0 이면 워크플로가 그 단계를 건너뛴다. **늘 적는다** — 값이 없으면
                # `if: … != '0'` 이 빈 문자열과 비교되어 뜻이 흐려진다 (25.57 과 같은 이유)
                out.write(f"us_days={outcome.us_days}\n")
                out.write(f"index_days={outcome.index_days}\n")
                out.write(f"fx_days={outcome.fx_days}\n")
                out.write(f"disclosure_days={outcome.disclosure_days}\n")


def 복귀_문구(옮김) -> str:
    """복귀 알림. **옮기지 못한 것이 있으면 성공이라고 말하지 않는다** (2026-09-21, docs/infra.md 25.81).

    예전에는 늘 이랬다.

        ✅ Turso 로 돌아왔습니다
        D1 에서 입력한 매매·관심종목·설정을 옮겼습니다(종목 번호를 다시 맞춤).

    그런데 `move_user_data` 는 **번호를 못 맞춘 종목의 행을 버린다.** 버렸다는 사실은
    Actions 로그에만 남고, 이 함수를 부르는 쪽은 그것을 보지도 않았다. 그래서
    매매 기록이 사라진 날에도 사람은 **"옮겼습니다" 라는 초록 메시지**만 받았다.
    매매는 사람이 넣은 것이라 다른 곳에 없다(CLAUDE.md 매매 규칙) —
    **가장 조용히 잃을 수 있는 데이터에 가장 밝은 메시지가 붙어 있었다.**

    지금은 마스터를 함께 옮기므로 대개 온전하다. 그래도 남는 것이 있으면 말한다.
    """
    꼬리 = "빠진 날 시세와 포트폴리오는 이어서 채웁니다."
    마스터 = f" 대상에 없던 종목 {옮김.carried}개는 마스터째 옮겼습니다." if 옮김.carried else ""
    if 옮김.온전한가:
        return (
            "✅ Turso 로 돌아왔습니다\n"
            f"D1 에서 입력한 매매·관심종목·설정을 옮겼습니다(종목 번호를 다시 맞춤).{마스터} {꼬리}"
        )
    return (
        "⚠️ Turso 로 돌아왔지만 **옮기지 못한 것이 있습니다**\n"
        f"종목을 맞추지 못한 {len(옮김.unmapped)}개: {', '.join(옮김.unmapped[:10])}\n"
        "이 종목에 달린 기록은 Turso 에 없습니다. **원본은 D1 에 그대로 있습니다** — "
        "종목 마스터를 갱신한 뒤 '사용자 데이터 옮기기' 를 다시 돌리면 들어옵니다.\n"
        f"{마스터.strip()} {꼬리}".strip()
    )


#: 복귀 뒤 단계(빠진 날 메우기·재계산)가 **모두 성공한 뒤** 워크플로 마지막 단계가 Turso 에 적는 표시 (25.469).
#: 복귀 표시(`RETURN_MARKER`)는 1단계가 적는다 — 그래야 뒤 단계의 파이썬이 Turso 를 본다. 그런데 뒤 단계가 실패하면
#: 다음 실행이 "이미 돌아갔습니다" 로 끝나 **다시 메울 길이 없었다.** 이 표시가 없으면 다음 실행이 뒤 단계만 다시 돈다
FILL_DONE_MARKER = "db_return_fill_done_at"
#: 옮긴 순간에 잰 빠진 구간 (25.473, 교차검증). 다시 돌 때 Turso 의 "마지막 날" 로 다시 재면, 그 사이 일일 배치가
#: 어제 시세를 넣어 **가운데 구멍이 안 보인다** — 처음 잰 구간을 지난 날 수만큼 늘려 다시 쓴다
FILL_PLAN_KEY = "db_return_fill_plan"
#: 끝 표시 없이 이만큼 다시 돌면 멈추고 사람에게 알린다 — 뒤 단계가 영구히 실패하면 날마다 돌며 Actions 분을 쓴다
MAX_FILL_RETRIES = 3


def _gaps(dst) -> dict:
    """Turso 에 빠진 구간. 옮기기 뒤에도, 뒤 단계를 다시 돌 때도 같은 함수로 Turso 에 묻는다(날짜를 박지 않는다)."""
    start = backfill_start(dst)
    print(f"빠진 국내 시세는 {start} 부터 받습니다 (Turso 의 마지막 국내 시세 다음 날)")
    오늘 = datetime.now(UTC).date()
    us_days, us_why = us_catchup(dst, 오늘)
    index_days, index_why = index_catchup(dst, 오늘)
    fx_days, fx_why = fx_catchup(dst, 오늘)
    disc_days, disc_why = disclosure_catchup(dst, 오늘)
    print(f"미국 시세: {us_why}")
    print(f"지수 일봉: {index_why}")
    print(f"환율: {fx_why}")
    print(f"국내 공시: {disc_why}")
    return {"start": start, "us_days": us_days, "index_days": index_days, "fx_days": fx_days, "disc_days": disc_days}


def _save_plan(dst, plan: dict) -> None:
    import json

    stamp = db.now_iso()
    dst.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        [FILL_PLAN_KEY, json.dumps(plan, ensure_ascii=False), stamp],
    )


def _load_plan(dst) -> dict | None:
    import json

    rs = dst.execute("SELECT value FROM settings WHERE key = ?", [FILL_PLAN_KEY])
    if not rs.rows:
        return None
    try:
        plan = json.loads(rs.rows[0][0])
    except (TypeError, ValueError):
        return None
    return plan if isinstance(plan, dict) else None


def replan(plan: dict, today: date) -> dict:
    """처음 잰 구간을 오늘까지 늘린다. 0 이던 것(구멍 없음)은 그대로 0 — 그 사이 새로 빈 날은 일일 배치 몫이다."""
    지남 = max((today - date.fromisoformat(str(plan["computed_on"]))).days, 0)
    return {
        **plan,
        # 늘려도 이 워크플로가 메우는 상한을 넘지 않는다 (25.478, 교차검증 — 120 에 걸린 값이 늘어 넘었다)
        **{k: (min(int(plan[k]) + 지남, MAX_CATCHUP_DAYS) if int(plan.get(k) or 0) > 0 else 0)
           for k in ("us_days", "index_days", "fx_days", "disc_days")},
    }  # fmt: skip


def fill_done(dst) -> bool:
    rs = dst.execute("SELECT value FROM settings WHERE key = ?", [FILL_DONE_MARKER])
    return bool(rs.rows)


def mark_filled(open_dst=open_turso) -> None:
    """워크플로 마지막 단계가 부른다 — 앞 단계가 모두 성공했을 때만."""
    dst = open_dst()
    try:
        stamp = db.now_iso()
        dst.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [FILL_DONE_MARKER, f'"{stamp}"', stamp],
        )
    finally:
        dst.close()
    print("복귀 뒤 메우기를 모두 마쳤습니다")


def run(open_src=open_d1, open_dst=open_turso) -> Outcome:
    """복귀했으면 moved=True. 할 일이 없거나 아직 막혀 있으면 moved=False.

    복귀 표시는 있는데 뒤 단계가 끝나지 않았으면(`FILL_DONE_MARKER` 없음) 옮기기 없이 **빠진 구간만 다시** 알려 준다 —
    moved=True 라 워크플로의 뒤 단계가 다시 돈다 (docs/infra.md 25.469)."""
    import move_user_data as mover

    if backend.backend() != backend.AUTO:
        print(f"{NOTHING_DONE}DB_BACKEND={backend.backend()} 이라 자동 복귀를 하지 않습니다 (auto 일 때만)")
        return Outcome(False)
    if backend.returned_to_turso():
        # 다시 도는 길도 **먼저 Turso 가 살아 있는지 본다** (25.473, 교차검증). 복귀 뒤 다시 막히면 예전에는 여기서
        # 예외가 올라와 날마다 실패 실행과 트레이스백 코멘트가 남았다
        ok, blocked = backend._probe_turso()
        if not ok:
            왜 = "한도로 막혀 있습니다" if blocked else "응답하지 않습니다"
            print(f"{NOTHING_DONE}이미 Turso 로 돌아갔는데 지금 Turso 가 {왜}. 메우기는 다음에 봅니다")
            return Outcome(False)
        dst = open_dst()
        try:
            if fill_done(dst):
                print(f"{NOTHING_DONE}이미 Turso 로 돌아갔습니다")
                return Outcome(False)
            plan = _load_plan(dst)
            if plan is None:  # 계획을 남기기 전(25.473 전)에 옮긴 DB — Turso 의 마지막 날로 잰다
                g = _gaps(dst)
                plan = {"computed_on": datetime.now(UTC).date().isoformat(), "retries": 0, **g}
            다시 = int(plan.get("retries") or 0)
            if 다시 > MAX_FILL_RETRIES:
                print(f"{NOTHING_DONE}메우기를 {MAX_FILL_RETRIES}번 다시 돌았는데 끝나지 않아 멈춰 있습니다"
                      " (사람이 볼 차례)")
                return Outcome(False)
            if 다시 == MAX_FILL_RETRIES:
                _save_plan(dst, {**plan, "retries": 다시 + 1})
                notify(f"⚠️ Turso 복귀 뒤 빠진 날 메우기가 {MAX_FILL_RETRIES}번 다시 돌아도 끝나지 않았습니다\n"
                       "GitHub → Actions → \"Turso 자동 복귀\" 의 실패 단계를 보세요. 더는 저절로 다시 돌지 않습니다 "
                       "(docs/infra.md 25.473)")  # fmt: skip
                print(f"{NOTHING_DONE}메우기 재시도 상한({MAX_FILL_RETRIES})에 닿았습니다")
                return Outcome(False)
            _save_plan(dst, {**plan, "retries": 다시 + 1})
            g = replan(plan, datetime.now(UTC).date())
            # 창은 오늘부터 거슬러 가므로 상한에 걸리면 가장 오래된 날이 빠진다 — 조용히 자르지 않는다 (25.481).
            # **실제로 잘렸을 때만** 말한다 — 딱 120 이 된 것은 잘린 것이 아니다 (25.484, 교차검증)
            지남 = max((datetime.now(UTC).date() - date.fromisoformat(str(plan["computed_on"]))).days, 0)
            잘림 = [k for k in ("us_days", "index_days", "fx_days", "disc_days")
                    if int(plan.get(k) or 0) > 0 and int(plan[k]) + 지남 > MAX_CATCHUP_DAYS]  # fmt: skip
            if 잘림:
                print(f"⚠️ {', '.join(잘림)} 가 상한 {MAX_CATCHUP_DAYS}일을 넘었습니다."
                      " 가장 오래된 날은 이 워크플로가 못 메웁니다"
                      " — 미국 시세는 backfill_us, 지수는 index_prices, 환율은 fx 작업, 공시는 disclosures_kr 를"
                      " --lookback/--days 를 늘려 따로 돌리세요")
            print(f"복귀 표시는 있는데 뒤 단계가 끝나지 않았습니다({다시 + 1}번째 다시)."
                  " 처음 잰 구간으로 다시 메웁니다")
        finally:
            dst.close()
        return Outcome(True, g["start"], us_days=g["us_days"], index_days=g["index_days"], fx_days=g["fx_days"],
                       disclosure_days=g["disc_days"])  # fmt: skip

    if not backend.d1_configured():
        # D1 을 쓰지 않는 저장소(docs/public-repo.md D8) — 옮길 것이 없다. 예전엔 Turso 가 살아 있으면 D1 을 열다
        # 실패로 끝났다
        # (2026-10-08 06:18 UTC, docs/infra.md 25.1029)
        print(f"{NOTHING_DONE}D1 값이 없어 옮길 것이 없습니다 (Turso 만 쓰는 저장소)")
        return Outcome(False)
    ok, blocked = backend._probe_turso()
    if not ok:
        왜 = "한도로 막혀 있습니다" if blocked else "응답하지 않습니다"
        # **표식을 붙인다** (docs/infra.md 25.84). 이 워크플로는 매일 09:20 KST 에 도는데,
        # 풀릴 때까지는 날마다 같은 말을 한다. 그것이 이슈 #1 의 마지막 코멘트를 덮으면
        # 15분 전에 돈 따라잡기 결과(25.80 의 복귀 점검)를 읽을 수 없다
        print(f"{NOTHING_DONE}Turso 가 아직 {왜}. 다음에 다시 봅니다")
        return Outcome(False)

    src, dst = open_src(), open_dst()
    try:
        applied = db.apply_migrations(dst)
        print(f"Turso 마이그레이션: {', '.join(applied) if applied else '새로 적용할 것 없음'}")
        옮김 = mover.run(src, dst, do_apply=True)
        g = _gaps(dst)
        _save_plan(dst, {"computed_on": datetime.now(UTC).date().isoformat(), "retries": 0, **g})
        stamp = db.now_iso()
        for client in (src, dst):
            client.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                [backend.RETURN_MARKER, f'"{stamp}"', stamp],
            )
    finally:
        src.close()
        dst.close()

    notify(복귀_문구(옮김))
    print("복귀를 마쳤습니다")
    return Outcome(
        True, g["start"], us_days=g["us_days"], index_days=g["index_days"], fx_days=g["fx_days"],
        disclosure_days=g["disc_days"],
    )


def main() -> int:
    if "--mark-filled" in sys.argv[1:]:
        mark_filled()
        return 0
    set_output(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
