"""되살아나는 날의 자동 점검 (scripts/return_check.py, docs/infra.md 25.80).

**이 파일은 일부러 소스를 긁지 않는다.** 2026-09-21 에 스스로 돌아보니, 최근에 쓴 테스트
상당수가 `expect(글).toContain(...)` 꼴이었다 — 그것은 동작이 아니라 **방금 한 편집의 모양**을
고정한다. 리팩터링을 막을 뿐 버그는 못 잡는다.

그래서 여기서는 **진짜 스키마(migrations/*.sql)를 올린 sqlite** 에 값을 넣고, 점검이
그 값을 보고 어떤 판정을 내리는지 본다. 스키마가 바뀌면 이 테스트가 깨진다 — 그것이 뜻이다.

점검 자체가 따라잡기를 망치면 안 되므로 **못 읽는 경우**를 가장 많이 본다.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import d1_usage  # noqa: E402
import return_check as rc  # noqa: E402

from tests.test_portfolio_job import MemClient  # noqa: E402

ALL_STEPS = json.dumps(
    {
        "check": {"outcome": "success"},
        "backfill": {"outcome": "failure"},
        "metrics": {"outcome": "success"},
        "scores": {"outcome": "success"},
        "signals": {"outcome": "success"},
        "targets": {"outcome": "success"},
    }
)


def 상태(p: rc.판정) -> str:
    return p.상태


class Test뒷단계:
    def test_4단계가_죽어도_5_8이_돌았으면_통과(self) -> None:
        """25.63 이 노린 바로 그 모양. `if: always() &&` 가 먹었다는 뜻이다."""
        p = rc.뒷단계가_돌았나(ALL_STEPS)

        assert p.상태 == "ok"
        assert "넷 다 돌았다" in p.본것

    def test_건너뛰었으면_고칠_곳을_알려_준다(self) -> None:
        steps = json.loads(ALL_STEPS)
        steps["scores"] = {"outcome": "skipped"}
        steps["signals"] = {"outcome": "skipped"}

        p = rc.뒷단계가_돌았나(json.dumps(steps))

        assert p.상태 == "bad"
        assert "6. 점수" in p.본것 and "7. 신호" in p.본것
        assert "4단계는 failure" in p.본것
        assert "d1-catchup.yml" in p.다음

    def test_STEPS_JSON_이_없으면_모름이다(self) -> None:
        """**모르는 것을 ✓ 로 적지 않는다.** 그게 이 점검의 값어치 전부다."""
        for 나쁜값 in (None, "", "깨짐", "[]", "{}"):
            assert rc.뒷단계가_돌았나(나쁜값).상태 == "unknown", 나쁜값

    def test_단계_id_가_바뀌면_통과가_아니라_모름이다(self) -> None:
        # 워크플로에서 id 를 바꾸면 조용히 ✓ 가 나면 안 된다
        p = rc.뒷단계가_돌았나(json.dumps({"check": {"outcome": "success"}}))

        assert p.상태 == "unknown"
        assert "step id" in p.다음


#: 따라잡기가 찍는 단계와 그때까지 쓴 행 (d1-catchup.yml 의 라벨 그대로)
_단계별 = [
    ("시작", 1_200),
    ("1. 유니버스 끝", 4_000),
    ("2. 재무 끝", 9_000),
    ("3. 업종 끝", 9_500),
    ("4. 과거 시세 끝", 72_000),
    ("5. 성과 지표 끝", 78_000),
    ("6. 점수 끝", 82_000),
    ("7. 신호 끝", 83_000),
    ("8. 장중 감시 끝", 83_400),
]


class Test사용량_읽기:
    """**읽는 쪽이 쓰는 쪽의 진짜 출력을 읽는지 본다** (2026-09-22, docs/infra.md 25.113).

    예전에는 이 자료를 손으로 적어 두었다. 그러면 `scripts/d1_usage.한줄` 의 문구가 바뀌어도
    이 테스트는 **손으로 적은 옛 문구**를 읽어 통과한다. 그동안 되살아나는 날의 점검은
    일곱 가지 중 둘을 `?` 로 내놓는다 — 하필 **그 하루가 전부인 날**에.

    그래서 자료를 `d1_usage.한줄` 로 만든다. 문구를 바꾸면 여기가 먼저 깨진다.
    """

    로그 = "\n".join(d1_usage.한줄(라벨, 쓴, 100_000) for 라벨, 쓴 in _단계별)

    def test_쓰는_쪽의_진짜_출력을_읽는다(self) -> None:
        """두 스크립트를 잇는 것은 **로그 한 줄의 모양**뿐이다. 그 이음매를 여기서 잡는다."""
        assert rc.사용량_읽기(d1_usage.한줄("4. 과거 시세 끝", 72_000, 100_000)) == [("4. 과거 시세 끝", 72_000)]

    def test_못_잰_줄도_쓰는_쪽_문구로_버린다(self) -> None:
        """`쓴 = None` 일 때의 진짜 문구를 0 으로 읽으면 안 된다."""
        assert rc.사용량_읽기(d1_usage.한줄("4. 과거 시세 끝", None, 100_000)) == []

    def test_쉼표가_든_숫자를_읽는다(self) -> None:
        읽은것 = rc.사용량_읽기(self.로그)

        assert len(읽은것) == 9
        assert 읽은것[0] == ("시작", 1_200)
        assert 읽은것[-1] == ("8. 장중 감시 끝", 83_400)

    def test_못_잰_줄은_버린다(self) -> None:
        """`[D1 사용량] …: 카운터를 읽지 못했다` 를 0 으로 읽으면 안 된다."""
        읽은것 = rc.사용량_읽기(
            "[D1 사용량] 4. 과거 시세 끝: 카운터를 읽지 못했다 (한도 100,000)\n"
            "[D1 사용량] 5. 성과 지표 끝: 오늘 78,000행 썼다 (78%), 남은 22,000행 / 한도 100,000"
        )

        assert 읽은것 == [("5. 성과 지표 끝", 78_000)]

    def test_상관없는_로그에서는_아무것도_안_읽는다(self) -> None:
        assert rc.사용량_읽기("INFO 유니버스 879종목\nERROR 뭔가 터졌다") == []


class Test여유:
    def test_5_8단계가_쓴_몫을_실측으로_돌려_준다(self) -> None:
        """**여기서 어림의 한 조각이 실측이 된다** (25.26). 83,400 - 72,000 = 11,400."""
        p = rc.여유가_맞았나(rc.사용량_읽기(Test사용량_읽기.로그))

        assert p.상태 == "ok"
        assert "11,400행 썼다" in p.본것

    def test_여유가_남아도_줄이라고_말하지_않는다(self) -> None:
        """여유 32,000 은 **세 몫의 합**이다 — 5~8단계 + 웹 크론 + 다음 날 아침 배치.

        한 몫만 재고 줄이면 다음 날 08:27 리포트가 굶는다(25.20 에서 이미 겪었다).
        """
        p = rc.여유가_맞았나(rc.사용량_읽기(Test사용량_읽기.로그))

        assert "줄이지 않는다" in p.다음
        assert "일일 배치" in p.다음
        assert "20,600행" in p.다음  # 32,000 - 11,400 이 나머지 두 몫이다

    def test_여유를_넘으면_얼마로_늘릴지_말해_준다(self) -> None:
        사용량 = [("4. 과거 시세 끝", 40_000), ("8. 장중 감시 끝", 40_000 + rc.예산여유 + 1_000)]

        p = rc.여유가_맞았나(사용량)

        assert p.상태 == "bad"
        assert f"{rc.예산여유 + 6_000:,}" in p.다음

    def test_4단계_줄이_없으면_모름이다(self) -> None:
        assert rc.여유가_맞았나([("5. 성과 지표 끝", 100)]).상태 == "unknown"
        assert rc.여유가_맞았나([]).상태 == "unknown"

    def test_자정을_넘겨_카운터가_줄면_모름이다(self) -> None:
        """UTC 자정에 D1 카운터가 리셋된다. 음수를 '0행 썼다' 로 적으면 거짓말이다."""
        p = rc.여유가_맞았나([("4. 과거 시세 끝", 90_000), ("8. 장중 감시 끝", 500)])

        assert p.상태 == "unknown"
        assert "자정" in p.다음


class Test단계별_사용량:
    def test_여덟_단계가_다_찍히면_통과(self) -> None:
        assert rc.단계별_사용량이_다_찍혔나(rc.사용량_읽기(Test사용량_읽기.로그)).상태 == "ok"

    def test_죽은_단계의_숫자를_잃으면_알려_준다(self) -> None:
        """25.52: `|| rc=$?` 가 빠지면 `bash -e` 가 사용량 줄을 통째로 건너뛴다."""
        줄들 = [줄 for 줄 in Test사용량_읽기.로그.splitlines() if not 줄.startswith("[D1 사용량] 4.")]

        p = rc.단계별_사용량이_다_찍혔나(rc.사용량_읽기("\n".join(줄들)))

        assert p.상태 == "bad"
        assert "빠진 단계: 4" in p.본것

    def test_아무것도_없으면_모름이다(self) -> None:
        assert rc.단계별_사용량이_다_찍혔나([]).상태 == "unknown"


class Test무엇이_깨웠나:
    def test_repository_dispatch_면_todo_user_1번이_풀린다(self) -> None:
        p = rc.무엇이_깨웠나("repository_dispatch")

        assert p.상태 == "ok"
        assert "todo-user 1번" in p.본것

    def test_schedule_면_아직_등록_전이다(self) -> None:
        assert rc.무엇이_깨웠나("schedule").상태 == "bad"

    def test_손으로_깨운_것으로는_알_수_없다(self) -> None:
        """**모르는 것을 ✗ 로도 적지 않는다.** 손으로 깨웠다고 등록이 안 된 것은 아니다."""
        assert rc.무엇이_깨웠나("workflow_dispatch").상태 == "unknown"
        assert rc.무엇이_깨웠나(None).상태 == "unknown"


class Test유니버스:
    def test_편입이_있으면_통과(self) -> None:
        assert rc.유니버스_판정(879, 113).상태 == "ok"

    def test_편입_0_이_시세_부족_때문이면_고장이_아니다(self) -> None:
        """25.64: 지난 스냅샷을 **지켜 낸 것**이다. ✗ 로 적으면 사람이 헛수고한다."""
        p = rc.유니버스_판정(0, 12)

        assert p.상태 == "ok"
        assert "지난 스냅샷을 지킨 것" in p.다음

    def test_시세가_넉넉한데_편입_0_이면_고장이다(self) -> None:
        assert rc.유니버스_판정(0, 200).상태 == "bad"

    def test_못_읽으면_모름이다(self) -> None:
        assert rc.유니버스_판정(None, 200).상태 == "unknown"


class Test베타:
    def test_채워졌으면_통과(self) -> None:
        assert rc.베타_판정(850, 879, 2_000).상태 == "ok"

    def test_지수가_짧으면_돌릴_명령을_알려_준다(self) -> None:
        p = rc.베타_판정(0, 879, 30)

        assert p.상태 == "bad"
        assert "index_prices" in p.다음

    def test_지수가_넉넉한데_비면_배선을_의심한다(self) -> None:
        """25.65 의 원래 고장이 이 모양이었다 — `market=` 하나가 안 넘어갔다."""
        p = rc.베타_판정(0, 879, 2_000)

        assert p.상태 == "bad"
        assert "metrics.py" in p.다음

    def test_성과_지표_자체가_0이면_베타를_탓하지_않는다(self) -> None:
        assert rc.베타_판정(0, 0, 2_000).상태 == "unknown"

    def test_최소_지수일을_metrics_에서_가져온다(self) -> None:
        """코드를 두 곳에 적으면 확인하는 쪽이 틀린 값을 본다 (25.65 가 그랬다)."""
        from batch.jobs.metrics import MIN_BENCHMARK_POINTS

        assert rc.베타_최소_지수일 == MIN_BENCHMARK_POINTS


class Test센티먼트:
    def test_적혔으면_통과(self) -> None:
        assert rc.센티먼트_판정(879, 700, 500).상태 == "ok"

    def test_감성이_아예_없으면_고장이_아니다(self) -> None:
        """뉴스가 아직 안 쌓인 것이다. ✗ 로 적으면 헛수고한다."""
        p = rc.센티먼트_판정(879, 0, 0)

        assert p.상태 == "ok"
        assert "뉴스가 쌓인 뒤" in p.다음

    def test_감성은_있는데_안_적히면_고장이다(self) -> None:
        """25.66 의 원래 고장. 총점에는 섞이는데 적히지 않아 sentiment_at_trade 가 영구 NULL 이었다."""
        p = rc.센티먼트_판정(879, 0, 500)

        assert p.상태 == "bad"
        assert "scores.py" in p.다음

    def test_점수가_0행이면_모름이다(self) -> None:
        assert rc.센티먼트_판정(0, 0, 500).상태 == "unknown"


class Test진짜_스키마에서_읽기:
    """**소스를 긁지 않고 진짜 DB 에 물어본다.** 질의가 스키마와 어긋나면 여기서 깨진다."""

    @pytest.fixture
    def client(self) -> MemClient:
        mem = MemClient()
        from batch.core import db

        db.apply_migrations(mem)  # type: ignore[arg-type]
        return mem

    def _종목(self, c: sqlite3.Connection, n: int = 3) -> None:
        for i in range(1, n + 1):
            c.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
                [i, f"00593{i}"],
            )

    def test_빈_DB_에서는_전부_0_이고_판정이_모름이_된다(self, client: MemClient) -> None:
        f = rc.모으기(client)

        assert f.편입 == 0 and f.시세거래일 == 0 and f.점수행 == 0
        상태들 = [p.상태 for p in rc.전부(None, None, "", f)]
        assert "bad" not in 상태들, "아무것도 없는 DB 를 고장으로 적으면 안 된다"

    def test_나라를_가려_센다(self, client: MemClient) -> None:
        """25.37·25.75 에서 되풀이된 모양 — 두 나라가 한 숫자에 섞이면 안 된다."""
        c = client.conn
        self._종목(c, 2)
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (9, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
        )
        for sid in (1, 2, 9):
            c.execute(
                "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                " VALUES (?, '2026-09-18', 100, 'KRW', 't', 't')",
                [sid],
            )

        assert rc.모으기(client, "KR").시세거래일 == 1
        assert rc.모으기(client, "US").시세거래일 == 1

    def test_최신_스냅샷의_편입만_센다(self, client: MemClient) -> None:
        c = client.conn
        self._종목(c, 3)
        # 옛 스냅샷은 셋 다 편입, 새 스냅샷은 하나만
        for sid in (1, 2, 3):
            c.execute(
                "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                " VALUES ('2026-09-01', ?, 1, 'KRW', 't')",
                [sid],
            )
        for sid, inc in ((1, 1), (2, 0), (3, 0)):
            c.execute(
                "INSERT INTO universe_members"
                " (snapshot_date, stock_id, included, exclude_reason, currency, created_at)"
                " VALUES ('2026-09-18', ?, ?, '시총 하한', 'KRW', 't')",
                [sid, inc],
            )

        assert rc.모으기(client).편입 == 1

    def test_베타와_센티먼트를_NULL_과_가른다(self, client: MemClient) -> None:
        c = client.conn
        self._종목(c, 2)
        for sid, beta in ((1, 1.2), (2, None)):
            c.execute(
                'INSERT INTO performance_metrics'
                ' (stock_id, as_of_date, "window", beta, data_points, calc_version, created_at)'
                " VALUES (?, '2026-09-18', '1Y', ?, 250, 1, 't')",
                [sid, beta],
            )
        for sid, sent in ((1, 12.5), (2, None)):
            c.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_score,"
                " sentiment_weight_used, weights_json, calc_version, created_at)"
                " VALUES (?, '2026-09-18', 70, '{}', ?, 0.1, '{}', 1, 't')",
                [sid, sent],
            )

        f = rc.모으기(client)

        assert (f.성과지표, f.베타있음) == (2, 1)
        assert (f.점수행, f.센티먼트있음) == (2, 1)

    def test_지수는_그_나라의_벤치마크_코드로_센다(self, client: MemClient) -> None:
        """25.65 에서 코드를 'S&P500' 으로 잘못 적어 두었다 — 그런 표는 없다."""
        from batch.jobs.metrics import BENCHMARK

        c = client.conn
        c.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
            " VALUES (?, '2026-09-18', 2600, 't', 't')",
            [BENCHMARK["KR"]],
        )

        assert rc.모으기(client, "KR").지수일 == 1
        assert rc.모으기(client, "US").지수일 == 0

    def test_표가_없어도_죽지_않고_모름이_된다(self) -> None:
        """따라잡기가 굶어 마이그레이션이 덜 돈 날에도 점검은 돌아야 한다."""
        빈것 = MemClient()

        f = rc.모으기(빈것)

        assert f.편입 is None and f.베타있음 is None


class Test출력:
    def test_고칠_것과_못_본_것을_따로_센다(self) -> None:
        판정들 = [
            rc.판정("가", "ok", "됐다"),
            rc.판정("나", "bad", "안 됐다", "이렇게 고친다"),
            rc.판정("다", "unknown", "모른다"),
        ]

        머리 = rc.머리말(판정들)

        assert "고칠 것 1건" in 머리
        assert "못 본 것 1건" in 머리

    def test_다음_할_일이_있으면_같이_찍는다(self) -> None:
        글 = rc.판정("가", "bad", "안 됐다", "여기를 본다").줄()

        assert "✗ 가: 안 됐다" in 글
        assert "→ 여기를 본다" in 글

    def test_다음이_없으면_한_줄이다(self) -> None:
        assert "\n" not in rc.판정("가", "ok", "됐다").줄()

    def test_판정_수가_확인_목록과_같다(self) -> None:
        """handoff.md 4.0 의 표가 일곱 줄이다. 늘리거나 줄이면 문서도 함께 고쳐라."""
        판정들 = rc.전부(ALL_STEPS, "repository_dispatch", Test사용량_읽기.로그, rc.사실())

        assert len(판정들) == 7

    def test_다_좋은_날에는_고칠_것이_없다(self) -> None:
        f = rc.사실(
            편입=879, 시세거래일=210, 성과지표=879, 베타있음=850,
            지수일=2_000, 점수행=879, 센티먼트있음=700, 감성행=500,
        )  # fmt: skip

        판정들 = rc.전부(ALL_STEPS, "repository_dispatch", Test사용량_읽기.로그, f)

        assert all(p.상태 == "ok" for p in 판정들), [p.줄() for p in 판정들 if p.상태 != "ok"]
        assert "고칠 것 없음" in rc.머리말(판정들)

    def test_아무것도_모르는_날에도_찍기는_한다(self) -> None:
        """Actions 가 죽었다 살아난 첫날, 대부분을 못 읽어도 **말은 해야 한다**."""
        판정들 = rc.전부(None, None, "", rc.사실())

        assert len(판정들) == 7
        assert all(p.줄().startswith("[복귀 점검]") for p in 판정들)


def test_점검이_결코_실패로_끝나지_않는다(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """**확인하는 일이 확인받는 일을 망치면 안 된다.** DB 가 통째로 터져도 0 이다."""

    class 터짐:
        def __init__(self) -> None:
            raise RuntimeError("D1 이 안 열린다")

    monkeypatch.setattr("batch.core.client.TursoClient", 터짐)
    monkeypatch.setattr(sys, "argv", ["return_check.py", "--usage-log", "없는파일.log"])

    assert rc.main() == 0
    찍힌것 = capsys.readouterr().out
    assert "DB 를 읽지 못했다" in 찍힌것
    assert "[복귀 점검] ===" in 찍힌것, "DB 가 없어도 워크플로에서 아는 것은 찍어야 한다"
