"""시세 사본을 Turso 에 맞춘다 — 워크플로가 캐시를 꺼낸 뒤 부른다 (docs/infra.md 25.888).

    python scripts/price_replica.py sync --path .price-replica/prices.db             # 비었으면 진도 문을 보고 만든다
    python scripts/price_replica.py sync --path .price-replica/prices.db --no-build  # 비었으면 만들지 않는다

처음 만들 때는 시세 전부(2026-10-16 약 2,066만 행)를 한 번 읽는다 — Turso 월 읽기 진도 문(`turso_read_gate.decide`)이
허락할 때만. 못 만들거나 맞추다 실패해도 **0 으로 끝난다**: 사본은 `usable=0` 이 되고
작업은 Turso 에서 읽는다(예전과 같다).
출력 `changed=true` 면 워크플로가 캐시에 새로 넣는다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import client as backend  # noqa: E402
from batch.core import db  # noqa: E402
from batch.core import price_replica as rep  # noqa: E402

#: 처음 만들 때 진도 문에 내미는 예상 읽기 — 시세 표 행 수 + 종목·검증 몫. 2026-10-02 약 730만이었는데 미국 시세 백필로
#: 2026-10-16 약 2,066만(전체 훑기 실측)이 됐다 — 옛 값이면 진도 문이 비용을 3분의 1 로 잡았다 (docs/infra.md 25.1537)
BUILD_ESTIMATE = 21_000_000


def _gate_allows_build(remote) -> tuple[bool, str]:
    spec = importlib.util.spec_from_file_location("turso_read_gate", Path(__file__).with_name("turso_read_gate.py"))
    gate = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(gate)  # type: ignore[union-attr]
    남음 = db.remaining_read_budget_or_none(remote)
    used = None if 남음 is None else db.TURSO_MONTHLY_READ_LIMIT - 남음
    # 진도선은 보지 않는다 (25.1135) — 사본이 있으면 일일 배치의 시세 읽기가 줄어 1~2주 안에 만든 값을 돌려받는다
    return gate.decide(used, BUILD_ESTIMATE, datetime.now(UTC), pace=False)


def _output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def just_synced(path: str, minutes: int, now: datetime | None = None) -> bool:
    """**방금 맞춘 사본인가** (docs/infra.md 25.1035). 일일 배치는 점수·신호 직전에 사본을 맞춘다(25.1033) — 그 뒤로
    시세를 쓰는 단계가 없어, 배치 뒤 맞추기가 같은 일을 한 번 더 했다(종목 표 약 8천 행 + 표본 검증 약 2.5만 행 +
    그날 범위 다시 받기). 다음 맞추기는 `synced_at - TOUCH_MARGIN` 뒤에 끝난 실행의 고친 범위를 보므로 이 실행이
    고친 것도 놓치지 않는다. 파일이 없거나 못 쓴다고 적혀 있거나 시각을 모르면 False(맞춘다)."""
    if not Path(path).exists():
        return False
    try:
        conn = rep.open_replica(path)
        try:
            meta = rep._meta(conn)
        finally:
            conn.close()
        synced = datetime.fromisoformat(meta["synced_at"])
    except Exception:  # noqa: BLE001 — 모르면 맞춘다
        return False
    if meta.get("usable") != "1" or meta.get("backend") != "turso":
        return False
    return timedelta(0) <= (now or datetime.now(UTC)) - synced <= timedelta(minutes=minutes)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="시세 사본 맞추기")
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--path", required=True)
    parser.add_argument("--no-build", action="store_true", help="사본이 비었으면 만들지 않는다")
    parser.add_argument(
        "--ohl-rebuild", action="store_true",
        help="예전 사본(시가 열 없음)을 진도 문을 거쳐 새로 만든다 — 시간 여유가 큰 작업 하나에만(25.2032)",
    )
    parser.add_argument(
        "--fresh-minutes", type=int, default=0,
        help="이 분 안에 맞춘 쓸 수 있는 사본이면 Turso 에 붙지 않고 그대로 넣는다(0: 늘 맞춘다)",
    )
    args = parser.parse_args()

    if args.fresh_minutes > 0 and just_synced(args.path, args.fresh_minutes):
        print(f"[시세 사본] {args.fresh_minutes}분 안에 맞춘 사본이다 — 다시 맞추지 않고 그대로 넣는다")
        _output("changed", "true")
        return 0

    try:
        종류 = backend.resolved_backend()
        remote = backend.TursoClient(use_replica=False)
    except Exception as error:  # noqa: BLE001 — 사본은 곁다리다. 못 맞추면 작업이 Turso 에서 읽는다
        print(f"[시세 사본] Turso 에 붙지 못했다 ({error}) — 이번에는 쓰지 않는다")
        return 0
    conn = rep.open_replica(args.path)
    try:
        allow = False
        비었나 = conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone() is None
        # 예전 사본(시가·고가·저가 열 없음, 25.2027)도 새로 만들기와 같은 비용이라 같은 진도 문을 거친다.
        # 그 다시 만들기는 `--ohl-rebuild` 를 준 작업 하나(지표 — 60분)만 한다(25.2032): 15분 작업(신호 성적표)은
        # 2,100만 행 다시 받기를 끝내지 못하고 잘려 캐시에 못 넣으면 다음 작업이 또 읽고, 같은 밤 두 작업
        # (지표·밴드)이 겹치면 두 번 만든다
        if not args.no_build and (비었나 or (args.ohl_rebuild and not rep.has_ohl(conn))) and 종류 == "turso":
            allow, 글 = _gate_allows_build(remote)
            print(f"[시세 사본] {'처음 만들기' if 비었나 else '시가 열 더해 새로 만들기'} — {글}")
        읽기_전 = remote.reads.total
        report = rep.sync(remote, conn, backend=종류, allow_build=allow)
        report["rows_read"] = remote.reads.total - 읽기_전
        print("[시세 사본] " + json.dumps(report, ensure_ascii=False))
        _output("changed", "true" if "stocks" in report else "false")  # 맞추기를 실제로 했으면 새로 넣는다
    except Exception as error:  # noqa: BLE001 — 맞추다 깨지면 쓰지 않는다고 적고 넘어간다(작업은 Turso 에서 읽는다)
        print(f"[시세 사본] 맞추지 못했다 ({error}) — 이번에는 쓰지 않는다")
        try:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('usable', '0')")
            conn.commit()
        except Exception as inner:  # noqa: BLE001 — 표시도 못 하면 신선도 시간이 지나 저절로 안 쓴다
            print(f"[시세 사본] 쓰지 않음 표시도 못 했다: {inner}")
        _output("changed", "false")
    finally:
        conn.close()
        remote.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
