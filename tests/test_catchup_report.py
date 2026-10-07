"""D1 따라잡기 결과 알림 (scripts/catchup_report.py, docs/infra.md 25.8). 네트워크를 타지 않는다."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import catchup_report as report  # noqa: E402

from batch.core import db  # noqa: E402
from tests.test_portfolio_job import MemClient  # noqa: E402

NOW = datetime(2026, 9, 19, 0, 40, tzinfo=UTC)  # 09:40 KST


class Test실패한_단계:
    def test_실패한_단계_이름을_찾는다(self) -> None:
        steps = {"check": {"outcome": "success"}, "universe": {"outcome": "success"}, "backfill": {"outcome": "failure"}}
        assert report.failed_steps(json.dumps(steps)) == ["4. 과거 시세"]

    def test_없거나_깨졌으면_빈_목록(self) -> None:
        assert report.failed_steps(None) == []
        assert report.failed_steps("깨짐") == []


class Test문장:
    def _facts(self, **over) -> report.Facts:
        base = report.Facts(
            universe=879, price_days=113, price_first="2026-04-10", price_last="2026-09-18",
            financial_companies=861, sectors=2701, metrics_1y=0, scores_as_of="2026-09-18",
            scored=761, scores_total=879, signals_as_of="2026-09-18",
            signals_by_horizon={"short": 5, "mid": 7}, morning_report_sent=True, d1_writes_today=72340,
        )  # fmt: skip
        for key, value in over.items():
            setattr(base, key, value)
        return base

    def test_끝난_날(self) -> None:
        text = report.compose(self._facts(), "success", [], NOW)
        assert text.startswith("📦 D1 따라잡기 (국내) — 09-19 09:40 KST ✅ 끝")
        assert "유니버스 879종목 · 시세 113거래일 (2026-04-10 ~ 2026-09-18)" in text
        assert "신호 12건 (단기 5 · 중기 7 · 장기 0)" in text
        # 126 은 넘기 전(113), 200 도 아직 — 남은 날을 센다
        assert "모멘텀 126거래일 ✗ 13일 남음 · 리스크 200거래일 ✗ 87일 남음" in text
        assert "오늘 D1 쓰기 72,340 / 100,000행" in text
        assert "오늘 신호를 싣습니다" not in text  # 아침 리포트가 나갔으면 되풀이하지 않는다

    def test_멈춘_날은_어디서_멈췄는지(self) -> None:
        text = report.compose(self._facts(), "failure", ["2. 과거 시세"], NOW)
        assert "❌ 멈춤: 2. 과거 시세" in text
        assert "내일 09:05 에 남은 것부터 이어서 합니다" in text

    def test_모르는_것은_물음표(self) -> None:
        text = report.compose(report.Facts(), "failure", [], NOW)
        assert "유니버스 ?종목 · 시세 ?거래일" in text
        assert "모멘텀 126거래일 ?" in text

    def test_아침_리포트가_없던_날은_저장된_근거_문장을_싣는다(self) -> None:
        signal = {
            "as_of_date": "2026-09-18", "horizon": "mid", "name_ko": "삼성전자", "ticker": "005930",
            "buy_zone_low": 248000.0, "buy_zone_high": 255000.0, "currency": "KRW",
            "rationale_text": "영업이익 증가율 32.1% (2025 사업보고서)",
        }  # fmt: skip
        text = report.compose(
            self._facts(morning_report_sent=False, top_signals=[signal], eligible_stocks=12), "success", [], NOW
        )
        assert "· 삼성전자(005930) 중기 매수 구간 248,000~255,000원" in text
        assert "  영업이익 증가율 32.1% (2025 사업보고서)" in text  # 문장을 바꾸지 않는다
        assert "… 외 11종목" in text  # 종목 수로 센다 (25.755)


def test_D1_에서_읽는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, name_ko, market, country, currency, status, sector, source, fetched_at)"
        " VALUES (1, '005930', '삼성전자', 'KOSPI', 'KR', 'KRW', 'active', '전자부품', 't', 't')"
    )
    mem.conn.execute(
        "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
        " VALUES ('2026-09-18', 1, 1, 'KRW', 't')"
    )
    for day in ("2026-09-17", "2026-09-18"):
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, ?, 252500, 'KRW', 't', 't')",
            [day],
        )
    mem.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
        " VALUES ('daily_kr', 'KR', '2026-09-18', 'schedule', '2026-09-18T23:27:00+00:00', NULL, 'skipped')"
    )
    facts = report.collect(mem, NOW)
    assert facts.universe == 1
    assert (facts.price_days, facts.price_first, facts.price_last) == (2, "2026-09-17", "2026-09-18")
    assert facts.sectors == 1
    # 2026-09-19 은 토요일이다 — 아침 배치가 원래 안 도는 날이라 "안 나갔다" 가 아니라 모른다 (docs/infra.md 25.375)
    assert facts.morning_report_sent is None
    assert facts.signals_by_horizon == {}  # 신호가 없으면 비어 있다(지어내지 않는다)


class Test알림이_따라잡기를_막지_않는다:
    """**이 알림은 곁다리다.** 읽지 못해도, 보내지 못해도 따라잡기를 실패로 만들면 안 된다.
    특히 **멈춘 날에도 메시지는 나가야 한다** — 멈췄다는 것을 알리는 것이 이 알림의 목적이다
    (docs/infra.md 25.8).

    2026-09-20 에 덮임을 재 보니 `main()` 이 통째로 검증 밖이었다. 사용자가 아침마다 실제로
    읽는 유일한 것인데도 그랬다.
    """

    @staticmethod
    def _터지는_클라이언트():
        class 터짐:
            def execute(self, *a: object, **k: object) -> object:
                raise RuntimeError("D1 응답 없음")

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a: object) -> None:
                pass

        return 터짐()

    def test_DB_를_못_읽어도_메시지가_나간다(self, monkeypatch, capsys) -> None:
        """멈춘 날은 DB 도 못 읽는 날이다. 그때 조용하면 사용자는 아무것도 모른다."""
        monkeypatch.setattr("batch.core.client.TursoClient", lambda *a, **k: self._터지는_클라이언트())
        보낸것: list[str] = []
        monkeypatch.setattr("batch.notify.telegram.send", 보낸것.append)
        monkeypatch.setattr(sys, "argv", ["catchup_report.py", "--status", "failure"])
        monkeypatch.setenv("STEPS_JSON", json.dumps({"backfill": {"outcome": "failure"}}))

        assert report.main() == 0
        assert 보낸것, "DB 를 못 읽었다고 알림까지 걸러 버리면 안 된다"
        assert "과거 시세" in 보낸것[0], "어디서 멈췄는지는 STEPS_JSON 만으로도 알 수 있다"
        # collect() 자체가 오류를 삼키므로 main 은 "읽지 못했습니다" 를 찍지 않는다.
        # 중요한 것은 **메시지가 나갔다는 것** — 그것이 이 알림의 목적이다
        assert "D1 따라잡기" in capsys.readouterr().out

    def test_텔레그램이_실패해도_0_으로_끝난다(self, monkeypatch, capsys) -> None:
        """발송 실패로 워크플로를 실패로 만들면 다음 단계까지 물린다."""
        monkeypatch.setattr("batch.core.client.TursoClient", lambda *a, **k: self._터지는_클라이언트())

        def 못보냄(_text: str) -> None:
            raise RuntimeError("텔레그램 401")

        monkeypatch.setattr("batch.notify.telegram.send", 못보냄)
        monkeypatch.setattr(sys, "argv", ["catchup_report.py"])
        monkeypatch.delenv("STEPS_JSON", raising=False)

        assert report.main() == 0
        assert "텔레그램 알림 실패" in capsys.readouterr().out

    def test_dry_run_은_보내지_않는다(self, monkeypatch) -> None:
        monkeypatch.setattr("batch.core.client.TursoClient", lambda *a, **k: self._터지는_클라이언트())

        def 불리면안된다(_text: str) -> None:
            raise AssertionError("--dry-run 인데 보냈다")

        monkeypatch.setattr("batch.notify.telegram.send", 불리면안된다)
        monkeypatch.setattr(sys, "argv", ["catchup_report.py", "--dry-run"])
        monkeypatch.delenv("STEPS_JSON", raising=False)
        assert report.main() == 0


class Test못_읽은_것은_지어내지_않는다:
    """`Facts` 의 약속이다 — 못 읽은 항목은 None 으로 둔다. 0 으로 두면 **0 건인 것처럼 보인다**."""

    @staticmethod
    def _망가진_표() -> MemClient:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute("DROP TABLE signals")
        mem.conn.execute("DROP TABLE api_usage")
        return mem

    def test_표가_없어도_수집이_터지지_않는다(self) -> None:
        facts = report.collect(self._망가진_표(), NOW)
        assert facts.signals_as_of is None
        assert facts.top_signals == []
        assert facts.d1_writes_today is None

    def test_모르는_값은_물음표로_보인다(self) -> None:
        """0 으로 보이면 '유니버스가 비었다' 로 읽힌다. 모르는 것과 없는 것은 다르다."""
        문장 = report.compose(report.Facts(), "success", [], NOW)
        assert "?" in 문장


class Test단계_이름이_워크플로와_맞는가:
    """`STEP_NAMES` ↔ `.github/workflows/d1-catchup.yml` 의 `id`·`name`.

    **왜 있나.** 따라잡기가 멈추면 이 이름이 텔레그램에 실린다 — "❌ 멈춤: 4. 과거 시세".
    Actions 로그를 못 읽는 상황에서(25.17) 그 한 줄이 **어디서 멈췄는지 아는 유일한 길**이다.

    그런데 둘은 **손으로 맞춰 둔 짝**이다. 2026-09-20 에 따라잡기 단계 순서를 바꾸면서
    번호가 어긋났고, `STEP_NAMES` 를 따로 고쳐야 했다. 안 고쳤으면 알림이 **엉뚱한 단계를
    가리켰을 것이고**, 틀렸다는 것을 알 방법이 없다 — 번호가 그럴듯하게 붙어 있으니까.
    """

    def _워크플로_단계(self) -> dict[str, str]:
        yaml = __import__("pytest").importorskip("yaml", reason="PyYAML 이 있어야 워크플로를 읽는다")
        경로 = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "d1-catchup.yml"
        데이터 = yaml.safe_load(경로.read_text(encoding="utf-8"))
        잡 = next(iter(데이터["jobs"].values()))
        return {s["id"]: str(s.get("name", "")) for s in 잡["steps"] if s.get("id")}

    def test_워크플로를_읽어_냈다(self) -> None:
        # 읽기가 빈 것을 내면 아래가 전부 통과한다
        assert len(self._워크플로_단계()) >= 8

    def test_id_가_양쪽에_똑같이_있다(self) -> None:
        단계 = self._워크플로_단계()

        빠진것 = set(단계) - set(report.STEP_NAMES)
        남는것 = set(report.STEP_NAMES) - set(단계)

        assert not 빠진것, f"워크플로에만 있는 단계: {빠진것} — 멈추면 id 그대로 찍힌다"
        assert not 남는것, f"STEP_NAMES 에만 있는 단계: {남는것} — 워크플로에서 사라졌다"

    def test_번호와_이름이_워크플로를_따른다(self) -> None:
        """짧게 줄여 쓰는 것은 괜찮지만 **앞부분이 같아야** 한다.

        워크플로 이름은 뒤에 설명이 붙는다("4. 과거 시세 (유니버스만, …)").
        알림에는 앞부분만 싣는다. 번호가 어긋나면 여기서 걸린다.
        """
        단계 = self._워크플로_단계()

        어긋난것 = [
            f"{id}: 알림 {report.STEP_NAMES[id]!r} ↔ 워크플로 {이름!r}"
            for id, 이름 in 단계.items()
            if not 이름.startswith(report.STEP_NAMES[id])
        ]

        assert not 어긋난것, "단계 이름이 워크플로와 다르다:\n  " + "\n  ".join(어긋난것)

    def test_번호가_0부터_빠짐없이_이어진다(self) -> None:
        # 번호가 튀면 사람이 "그 사이 단계는 뭐였지" 를 찾게 된다
        번호 = sorted(int(이름.split(".")[0]) for 이름 in report.STEP_NAMES.values())

        assert 번호 == list(range(len(번호))), f"번호가 이어지지 않는다: {번호}"


class Test언제쯤_추천이_나오나:
    """거래일 수를 **날짜로 옮겨** 준다 (docs/infra.md 25.85).

    2026-09-21 까지 이 알림은 "리스크 200거래일 ✗ 87일 남음" 까지만 말했다. 사람이 알고 싶은
    것은 **며칠 뒤냐**인데, 그러려면 따라잡기가 한 번에 몇 거래일을 받는지 알아야 한다 —
    그 숫자는 D1 예산·유니버스 크기·그날 재무·업종이 먼저 쓴 몫에 따라 달라진다.

    **재서 말한다.** 최근 따라잡기가 실제로 저장한 행 수를 유니버스 종목 수로 나눈다.
    못 재면 **모른다고 한다** — 지어내면 25.0 의 "재어 보지 않고 적는다" 를 되풀이하는 것이다.
    """

    def test_행을_종목_수로_나누면_거래일이다(self) -> None:
        # 880종목 × 25거래일 = 22,000행
        assert report.회당_거래일([22_000], 880) == 25.0

    def test_최근_몇_번을_평균_낸다(self) -> None:
        """하루는 들쭉날쭉하다 — 재무·업종이 예산을 먼저 쓰는 날이 있다."""
        assert report.회당_거래일([22_000, 8_800, 17_600], 880) == pytest.approx((25 + 10 + 20) / 3)

    def test_너무_옛_것은_안_본다(self) -> None:
        많이 = [22_000] * report.RATE_RUNS + [880_000]  # 마지막은 말도 안 되게 큰 옛 기록

        assert report.회당_거래일(많이, 880) == 25.0

    def test_못_재면_None_이다(self) -> None:
        """**모르는 것을 0 으로도 어림으로도 적지 않는다.**"""
        assert report.회당_거래일([], 880) is None
        assert report.회당_거래일([0, 0], 880) is None
        assert report.회당_거래일([22_000], None) is None
        assert report.회당_거래일([22_000], 0) is None

    def test_남은_거래일을_날로_바꾼다(self) -> None:
        """따라잡기는 하루 한 번 도니 남은 실행 횟수가 곧 날 수다."""
        assert "약 4일 뒤" in report.며칠_뒤(87, 25.0)  # 87/25 = 3.48 → 올림 4

    def test_올림한다(self) -> None:
        """3.1일을 3일로 적으면 그날 안 나온다. 못 미치는 쪽으로 말하지 않는다."""
        assert "약 2일 뒤" in report.며칠_뒤(26, 25.0)

    def test_이미_찼으면_아무_말도_안_한다(self) -> None:
        assert report.며칠_뒤(0, 25.0) == ""
        assert report.며칠_뒤(-5, 25.0) == ""

    def test_속도를_모르면_모른다고_한다(self) -> None:
        말 = report.며칠_뒤(87, None)

        assert "모릅니다" in 말
        assert "일 뒤" not in 말, "모르면서 날짜를 말하면 안 된다"

    def test_문장에_들어간다(self) -> None:
        facts = report.Facts(
            universe=880, price_days=113, scores_as_of="2026-09-18", recent_rows=[22_000, 22_000],
        )  # fmt: skip

        글 = report.compose(facts, "success", [], NOW)

        assert "시세가 차기까지 87거래일" in 글
        assert "약 4일 뒤" in 글
        assert "따라잡기 한 번에 약 25거래일" in 글

    def test_다_찼으면_그_줄이_없다(self) -> None:
        facts = report.Facts(universe=880, price_days=250, recent_rows=[22_000])

        assert "시세가 차기까지" not in report.compose(facts, "success", [], NOW)

    def test_속도를_모르는_날에도_남은_거래일은_말한다(self) -> None:
        """구멍이 뚫리면 침묵하지 말고 **아는 것까지는** 말한다."""
        글 = report.compose(report.Facts(universe=880, price_days=113), "success", [], NOW)

        assert "시세가 차기까지 87거래일" in 글
        assert "모릅니다" in 글


class Test속도를_진짜_DB_에서_읽는다:
    """`collect` 가 `batch_runs.step_log` 에서 적재량을 꺼내는가 (docs/infra.md 25.85).

    **진짜 스키마를 올린 sqlite 에 물어본다** — `step_log` 가 JSON 문자열 열이라
    소스 검사로는 이 경로가 맞는지 알 수 없다.
    """

    @staticmethod
    def _db() -> MemClient:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        return mem

    @staticmethod
    def _실행(mem: MemClient, status: str, step_log: str | None) -> None:
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status, step_log)"
            " VALUES ('backfill_kr', 'KR', '2026-09-18', 'schedule', '2026-09-18T00:05:00+00:00', ?, ?)",
            [status, step_log],
        )

    def test_최신_순으로_읽는다(self) -> None:
        mem = self._db()
        for rows in (10_000, 20_000, 30_000):
            self._실행(mem, "success", f'{{"rows": {rows}, "skipped_days": 0}}')

        assert report.collect(mem, NOW).recent_rows == [30_000, 20_000, 10_000]

    def test_건너뛴_실행은_안_센다(self) -> None:
        """건너뛴 날의 0 을 섞으면 속도가 **실제보다 느리게** 나와 '언제쯤' 이 늘어진다."""
        mem = self._db()
        self._실행(mem, "success", '{"rows": 22000}')
        self._실행(mem, "skipped", '{"reason": "예산"}')
        self._실행(mem, "failed", '{"rows": 0}')

        assert report.collect(mem, NOW).recent_rows == [22_000]

    def test_깨진_JSON_은_건너뛴다(self) -> None:
        mem = self._db()
        self._실행(mem, "success", "{이건 JSON 이 아니다")
        self._실행(mem, "success", '{"rows": 22000}')

        assert report.collect(mem, NOW).recent_rows == [22_000]

    def test_rows_가_없는_기록도_건너뛴다(self) -> None:
        mem = self._db()
        self._실행(mem, "partial", '{"errors": ["뭔가"]}')

        assert report.collect(mem, NOW).recent_rows == []

    def test_다른_배치의_기록은_안_본다(self) -> None:
        mem = self._db()
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status, step_log)"
            " VALUES ('financials', 'KR', '2026-09-18', 'schedule', '2026-09-18T00:05:00+00:00',"
            " 'success', '{\"rows\": 999999}')"
        )

        assert report.collect(mem, NOW).recent_rows == []

    def test_표가_없어도_죽지_않는다(self) -> None:
        """알림이 따라잡기를 막으면 안 된다."""
        assert report.collect(MemClient(), NOW).recent_rows == []


class Test시세만_세고_추천이라_말하지_않는다:
    """**단정하지 않는다** (docs/infra.md 25.85).

    처음 쓸 때는 "추천까지 87거래일 · 약 4일 뒤" 라고 적었다. 그것은 시세만 센 값이다.
    재무나 업종이 비면 팩터가 둘 이상 없어 종합 점수가 아예 안 나온다(docs/factors.md).
    2026-09-19 D1 실측이 정확히 그 꼴이었다 — 시세 88거래일, 재무 0, 업종 0.
    """

    def _facts(self, **over) -> report.Facts:
        base = {"universe": 880, "price_days": 113, "recent_rows": [22_000],
                "financial_companies": 861, "sectors": 2_701}
        return report.Facts(**{**base, **over})

    def test_다_있으면_시세만_말한다(self) -> None:
        글 = report.compose(self._facts(), "success", [], NOW)

        assert "시세가 차기까지 87거래일" in 글
        assert "다만 시세만으로는" not in 글

    def test_재무가_비면_말해_준다(self) -> None:
        글 = report.compose(self._facts(financial_companies=0), "success", [], NOW)

        assert "다만 시세만으로는 안 됩니다 — 재무도 있어야 합니다" in 글

    def test_둘_다_비면_둘_다_적는다(self) -> None:
        글 = report.compose(self._facts(financial_companies=0, sectors=0), "success", [], NOW)

        assert "재무, 업종도 있어야 합니다" in 글

    def test_못_읽은_것은_빈_것이_아니다(self) -> None:
        """`None` 은 "못 읽었다" 다. 그것을 0 으로 치면 있는데 없다고 말하게 된다 (25.74)."""
        assert report.모자란_재료(self._facts(financial_companies=None, sectors=None)) == []

    def test_시세가_다_찼으면_이_줄_자체가_없다(self) -> None:
        글 = report.compose(self._facts(price_days=250, financial_companies=0), "success", [], NOW)

        assert "다만 시세만으로는" not in 글


def test_지표_수를_나라_안에서_센다() -> None:
    """미국 지표가 더 최신이어도 국내 수가 0 이 되면 안 된다 (docs/infra.md 25.112).

    되살아나는 날의 자동 점검(25.80)이 이 수를 보고 ✓/✗ 를 찍는다. 전체에서 기준일을
    잡으면 **"국내 지표가 안 만들어졌다" 고 거짓으로 실패를 알린다.**
    같은 파일의 신호 질의는 이미 나라 안에서 잡고 있었다 — 한 파일 안에서 갈렸다.
    """
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, name_ko, market, country, currency, status, source, fetched_at)"
        " VALUES (1, '005930', '삼성전자', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, name_en, market, country, currency, status, source, fetched_at)"
        " VALUES (2, 'AAPL', 'Apple', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    for stock_id, 날 in ((1, "2026-09-18"), (2, "2026-09-22")):  # 미국이 더 최신
        mem.conn.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, mdd, data_points, calc_version, created_at)"
            " VALUES (?, ?, '1Y', -0.2, 250, 2, 't')",
            [stock_id, 날],
        )

    facts = report.collect(mem, NOW)

    assert facts.metrics_1y == 1, "국내 지표가 있는데 0 으로 셌다 — 되살아나는 날 거짓 실패를 알린다"


def test_거래일에만_아침_리포트가_빠졌다고_본다() -> None:
    """토요일·휴장일마다 "아침 리포트가 나가지 않아 오늘 신호를 싣습니다" 가 나갔다 (docs/infra.md 25.375)."""
    from datetime import date

    assert report.morning_report_due(date(2026, 9, 22)) is True  # 화요일
    assert report.morning_report_due(date(2026, 9, 19)) is False  # 토요일
    assert report.morning_report_due(date(2026, 9, 25)) is False  # 추석 연휴

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    facts = report.collect(mem, datetime(2026, 9, 22, 7, 0, tzinfo=UTC))  # 화요일 16:00 KST(장 마감 뒤), 아침 배치 기록 없음
    assert facts.morning_report_sent is False


def test_예비_실행_전이면_신호를_대신_싣지_않고_늦은_리포트를_말한다_25_871() -> None:
    """09:35 예비 실행(25.870)이 늦은 리포트를 낼 텐데 따라잡기 알림이 먼저 신호를 실으면 추천이 두 번 갔다."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    facts = report.collect(mem, datetime(2026, 9, 22, 0, 40, tzinfo=UTC))  # 화요일 09:40 KST
    assert facts.late_report_pending is True and facts.morning_report_sent is None
    본문 = report.compose(facts, "success", [], datetime(2026, 9, 22, 0, 40, tzinfo=UTC))
    assert "09:35 예비 실행" in 본문 and "오늘 신호를 싣습니다" not in 본문


def test_성공_뒤에_늦게_온_skipped_줄이_있어도_나갔다고_본다() -> None:
    """마지막 한 줄만 봐서, 성공 뒤 늦게 온 예비 cron 의 skipped 때문에 "아침 리포트가 나가지 않았다" 가 나갔다
    (docs/infra.md 25.433)."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for 시각, 상태 in (("2026-09-21T23:27:00+00:00", "success"), ("2026-09-22T01:30:00+00:00", "skipped")):
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('daily_kr', 'KR', '2026-09-21', 'schedule', ?, NULL, ?)",
            [시각, 상태],
        )
    facts = report.collect(mem, datetime(2026, 9, 22, 4, 24, tzinfo=UTC))  # 화요일 13:24 KST
    assert facts.morning_report_sent is True


def test_근거표가_없는_신호는_대신_싣는_추천에서_뺀다() -> None:
    """절대 규칙 "근거표를 만들 수 없는 추천은 표시하지 않는다" (docs/infra.md 25.752, 리포트 감사)."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid, ticker, name, 상태 in ((1, "005930", "삼성전자", "active"), (2, "000660", "SK하이닉스", "active"),
                                 (3, "035420", "NAVER", "active"), (4, "051910", "LG화학", "excluded")):
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, name_ko, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'KOSPI', 'KR', 'KRW', ?, 't', 't')",
            [sid, ticker, name, 상태],
        )
    좋은 = '{"criteria": [{"label": "매출 성장", "display": "18%", "threshold": "> 0", "source": "dart", "value": 0.18}]}'
    # 3: 비어 있지는 않지만 웹이 못 그리는 근거 / 4: 제외된 종목 (25.822·25.827, 교차검증)
    for sid, data in ((1, 좋은), (2, "{}"), (3, '{"criteria": [{"label": "x"}]}'), (4, 좋은)):
        mem.conn.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,"
            " currency, tranche_plan, target_price, stop_price, suggested_weight_pct, suggested_amount,"
            " size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)"
            " VALUES (?, '2026-09-18', 'mid', '실적 모멘텀', 68000, 71000, 'KRW', '[]', 85000, 60000, 10.0, 0,"
            " 1.0, 0, '근거', ?, 1, 't')",
            [sid, data],
        )
    facts = report.collect(mem, NOW)
    assert [s["ticker"] for s in facts.top_signals] == ["005930"]  # 제외 종목은 아예 읽지 않는다
    assert facts.no_evidence == 2
    facts.morning_report_sent = False
    assert "근거표를 만들 수 없는 2종목은 싣지 않았습니다" in report.compose(facts, "success", [], NOW)


def test_근거표_읽기() -> None:
    assert report._criteria('{"criteria": [1]}') == [1]
    assert report._criteria("깨짐") == []
    assert report._criteria(None) == []
    assert report._criteria({"criteria": "x"}) == []


def test_근거표가_없어_모두_뺀_날도_뺐다고_말한다() -> None:
    """블록 전체가 생략되어 경고까지 사라졌다 (docs/infra.md 25.755, 교차검증)."""
    facts = report.Facts(morning_report_sent=False, no_evidence=3, signals_as_of="2026-09-18")
    문장 = report.compose(facts, "success", [], NOW)
    assert "근거표를 만들 수 없는 3종목은 싣지 않았습니다" in 문장
    assert "실을 신호가 없습니다" in 문장 and "싣습니다 (" not in 문장 and "펼쳐 봅니다" not in 문장  # 25.757


def test_외_N종목은_실을_수_있는_종목으로_센다() -> None:
    signal = {"currency": "KRW", "name_ko": "삼성전자", "ticker": "005930", "horizon": "mid",
              "buy_zone_low": 1.0, "buy_zone_high": 2.0, "rationale_text": "근거"}  # fmt: skip
    facts = report.Facts(
        morning_report_sent=False, top_signals=[signal], eligible_stocks=1, no_evidence=1,
        signals_by_horizon={"mid": 3}, signals_as_of="2026-09-18",
    )
    문장 = report.compose(facts, "success", [], NOW)
    assert "… 외" not in 문장  # 예전에는 행 3 − 1 = "외 2건"


def test_한도로_건너뛴_단계를_끝이라고_하지_않는다_25_875() -> None:
    """2026-10-01 — 6·7단계가 읽기 한도로 건너뛰었는데 알림은 "✅ 끝 · 신호 0건" 이었다(감사 재현)."""
    로그 = (
        "[읽기 문] 6. 점수: 들어갑니다\n"
        "건너뜀: Cloudflare D1 하루 읽기 한도(500만 행)에 걸렸습니다. 매일 09:00 KST(자정 UTC)에 풀립니다\n"
        "건너뜀: 이미 6일 안에 돌았습니다\n"
    )
    한도 = report.quota_skips(로그)
    assert len(한도) == 1 and "읽기 한도" in 한도[0]
    본문 = report.compose(report.Facts(), "success", [], NOW, quota=한도, db_unread=True)
    assert "✅" not in 본문 and "⛔ DB 한도로 1단계 건너뜀" in 본문
    assert "신호 읽지 못함" in 본문 and "신호 0건" not in 본문
