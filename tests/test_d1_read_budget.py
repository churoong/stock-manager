"""D1 의 **하루 읽기** 예산을 센다 (docs/infra.md 25.122).

**있었던 일.** `record_rows_written` 은 처음부터 두 벌을 적었다 — Turso 월 누계와
D1 하루 누계(`d1_writes`). 짝이 되는 `record_rows_read` 는 **월 누계만** 적었다.
그래서 D1 위에서 도는 동안 읽은 행은 `turso_reads` 에만 쌓였고, 그 자리의 한도는
**5억(월)** 이다. D1 의 실제 예산은 **500만(하루)** 이다.

무엇이 안 됐나.

* `/status` 의 읽기 게이지는 하루 예산을 다 써도 1% 를 가리킨다. 80% 경고가 울릴 수 없다
* `check_read_budget()` 은 300만 행짜리 백테스트를 언제나 통과시킨다 — 잣대가 100배 넓다
* infra 의 여러 결정이 "읽기는 넉넉하다(하루 500만 행)" 를 근거로 삼았는데,
  **그 넉넉함을 뒷받침할 실측이 하나도 없었다.** 셀 자리가 없었으니 잴 수도 없었다

2026-09-18 에 계정을 통째로 막은 것은 쓰기가 아니라 **읽기**였다(infra 24절).
쓰기 쪽 장치만 만들어 두고 읽기를 빼놓은 것은 「절반만 지킨 규칙」이다(25.0).
"""

from __future__ import annotations

import pytest

from batch.core import d1, db
from tests.test_portfolio_job import MemClient


def _mem(monkeypatch: pytest.MonkeyPatch, backend: str | None) -> MemClient:
    if backend is None:
        monkeypatch.delenv("DB_BACKEND", raising=False)
    else:
        monkeypatch.setenv("DB_BACKEND", backend)
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    return mem


class Test하루_창에_적는다:
    def test_D1_이면_오늘_읽기를_따로_센다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        db.record_rows_read(mem, 400_000)  # type: ignore[arg-type]
        db.record_rows_read(mem, 100_000)  # type: ignore[arg-type]

        assert db.d1_reads_today(mem) == 500_000  # type: ignore[arg-type]
        assert db.remaining_d1_daily_reads(mem) == db.D1_DAILY_READ_LIMIT - 500_000  # type: ignore[arg-type]
        rows = mem.conn.execute("SELECT api_name, window_type FROM api_usage ORDER BY api_name").fetchall()
        assert ("d1_reads", "day") in rows, "D1 하루 읽기 누계가 없다"
        assert ("turso_reads", "month") in rows, "월 누계를 잃으면 Turso 로 돌아갔을 때 못 본다"

    def test_Turso_면_하루_읽기_카운터를_쓰지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Turso 한도는 월 단위다. 하루 행을 만들면 화면에 뜻 없는 게이지가 하나 는다."""
        mem = _mem(monkeypatch, None)
        db.record_rows_read(mem, 10)  # type: ignore[arg-type]
        n = mem.conn.execute("SELECT COUNT(*) FROM api_usage WHERE api_name = 'd1_reads'").fetchone()[0]
        assert n == 0

    def test_한도를_넘기면_상태가_blocked_다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """게이지가 색을 바꾸는 자리다. 80%/100% 는 CLAUDE.md 비용 규칙이다."""
        mem = _mem(monkeypatch, "d1")
        db.record_rows_read(mem, int(db.D1_DAILY_READ_LIMIT * 0.85))  # type: ignore[arg-type]
        상태 = mem.conn.execute("SELECT state FROM api_usage WHERE api_name = 'd1_reads'").fetchone()[0]
        assert 상태 == "warn"

        db.record_rows_read(mem, db.D1_DAILY_READ_LIMIT)  # type: ignore[arg-type]
        상태 = mem.conn.execute("SELECT state FROM api_usage WHERE api_name = 'd1_reads'").fetchone()[0]
        assert 상태 == "blocked"

    def test_못_읽으면_모른다고_한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 과 '모름' 을 섞지 않는다 (`d1_writes_today` 와 같은 약속, 25.55)."""
        monkeypatch.setenv("DB_BACKEND", "d1")
        mem = MemClient()  # 마이그레이션 전이라 api_usage 가 없다
        assert db.d1_reads_today(mem) is None  # type: ignore[arg-type]
        # 계산에 쓰는 쪽은 일부러 다르다 — 한 번 못 읽었다고 그날 작업을 멈추지 않는다
        assert db.remaining_d1_daily_reads(mem) == db.D1_DAILY_READ_LIMIT  # type: ignore[arg-type]


class Test가장_좁은_문을_본다:
    """`check_read_budget` 은 **지금 도는 백엔드**의 한도로 재야 한다."""

    def test_D1_에서는_하루_예산이_막는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        db.record_rows_read(mem, 4_000_000)  # type: ignore[arg-type]  오늘 이미 400만을 훑었다

        ok, text = db.check_read_budget(mem, 3_000_000, "백테스트")  # type: ignore[arg-type]

        assert ok is False, "D1 하루 예산이 100만밖에 안 남았는데 300만 행을 읽게 뒀다"
        assert "오늘(D1)" in text, f"무엇이 막았는지 사람이 읽을 수 있어야 한다: {text}"
        assert "1,000,000행" in text

    def test_D1_에서도_여유가_있으면_통과한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        ok, _text = db.check_read_budget(mem, 100_000, "국내 수정주가")  # type: ignore[arg-type]
        assert ok is True

    def test_Turso_에서는_월_예산만_본다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """D1 하루 한도는 Turso 와 아무 상관이 없다. 남의 문턱으로 막으면 안 된다."""
        mem = _mem(monkeypatch, None)
        ok, text = db.check_read_budget(mem, 50_000_000, "백테스트")  # type: ignore[arg-type]
        assert ok is True
        assert "이번 달" in text


def test_쓰기와_읽기가_같은_모양이다() -> None:
    """**한쪽에만 있는 장치를 막는 그물이다** (25.0 「절반만 지킨 규칙」).

    읽기 쪽이 3주 동안 빠져 있었던 것은 짝이 맞는지 아무도 안 봤기 때문이다.
    이름을 하나 더 만들 때 짝도 같이 만들게 한다.
    """
    빠진것 = [
        이름
        for 이름 in (
            "D1_DAILY_READ_LIMIT",
            "d1_reads_today",
            "remaining_d1_daily_reads",
        )
        if not hasattr(db, 이름)
    ]
    assert not 빠진것, f"쓰기에는 있는데 읽기에는 없다: {빠진것}"
    assert "d1_reads" in db.API_NAMES, "카운터 이름이 API_NAMES 에 없으면 웹과 글자가 갈라진다 (25.68)"
    assert db.D1_DAILY_READ_LIMIT < db.TURSO_MONTHLY_READ_LIMIT, (
        "D1 하루 한도가 Turso 월 한도보다 크면 이 모듈의 전제가 틀린 것이다"
    )


class Test용량은_수위다:
    """D1 의 500MB 상한. **셋 중 이것만 자정에 리셋되지 않는다** (docs/infra.md 25.123).

    `batch/core/d1.FREE_DB_BYTES` 는 2026-09-22 까지 **정의만 되어 있고 아무도 읽지 않았다.**
    상수가 있으면 지키는 줄 안다 — 25.108 에서 같은 모양을 봤다.
    """

    def test_더하지_않고_덮어쓴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """횟수가 아니라 수위다. 더하면 며칠 만에 한도를 넘은 것처럼 보인다."""
        mem = _mem(monkeypatch, "d1")
        db.record_db_size(mem, 100_000_000)  # type: ignore[arg-type]
        db.record_db_size(mem, 120_000_000)  # type: ignore[arg-type]

        행 = mem.conn.execute(
            "SELECT call_count, limit_value FROM api_usage WHERE api_name = 'd1_db_size'"
        ).fetchall()
        assert len(행) == 1
        assert 행[0][0] == 120_000_000, "용량을 더해 버렸다"
        assert 행[0][1] == d1.FREE_DB_BYTES

    @pytest.mark.parametrize(
        ("비율", "기대"),
        [(0.5, "ok"), (0.79, "ok"), (0.8, "warn"), (1.0, "blocked")],
    )
    def test_한도를_보고_상태를_정한다(self, monkeypatch: pytest.MonkeyPatch, 비율: float, 기대: str) -> None:
        mem = _mem(monkeypatch, "d1")
        assert db.record_db_size(mem, int(d1.FREE_DB_BYTES * 비율)) == 기대  # type: ignore[arg-type]

    def test_상한을_바꾸면_판정이_따라_바뀐다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**상수가 판정에 쓰이는지를 본다** (25.108 의 교훈). 글만 바뀌면 장식이다.

        300MB 는 500MB 의 60% 라 `ok` 다. 상한을 350MB 로 낮추면 86% 가 되어 `warn` 이어야 한다.
        유료 플랜으로 올려 상한이 바뀌는 날 이 배선이 살아 있어야 한다.
        """
        mem = _mem(monkeypatch, "d1")
        크기 = 300 * 1024 * 1024
        assert db.record_db_size(mem, 크기) == "ok"  # type: ignore[arg-type]

        monkeypatch.setattr(d1, "FREE_DB_BYTES", 350 * 1024 * 1024)
        assert db.record_db_size(mem, 크기) == "warn", "상한을 바꿨는데 판정이 그대로다"  # type: ignore[arg-type]
        행 = mem.conn.execute("SELECT limit_value FROM api_usage WHERE api_name = 'd1_db_size'").fetchone()
        assert 행[0] == 350 * 1024 * 1024, "저장된 한도도 따라와야 화면 게이지가 맞는다"

    def test_못_재면_적지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**모르는 것을 0 으로 적지 않는다.** 0 은 "텅 비었다" 로 읽힌다."""
        mem = _mem(monkeypatch, "d1")
        mem.database_size = lambda: None  # type: ignore[attr-defined]
        assert db.measure_db_size(mem) is None  # type: ignore[arg-type]
        n = mem.conn.execute("SELECT COUNT(*) FROM api_usage WHERE api_name = 'd1_db_size'").fetchone()[0]
        assert n == 0

    def test_Turso_면_재지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, None)
        mem.database_size = lambda: 999  # type: ignore[attr-defined]
        assert db.measure_db_size(mem) is None  # type: ignore[arg-type]

    def test_D1_이면_재서_적는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        mem.database_size = lambda: 450 * 1024 * 1024  # type: ignore[attr-defined]
        assert db.measure_db_size(mem) == "warn"  # type: ignore[arg-type]
        assert mem.conn.execute(
            "SELECT call_count FROM api_usage WHERE api_name = 'd1_db_size'"
        ).fetchone()[0] == 450 * 1024 * 1024


def test_용량을_재는_작업이_하루_한_번_도는_것이다() -> None:
    """**부르는 곳이 없으면 상수를 하나 더 만든 것일 뿐이다** (25.123 이 고친 바로 그 모양).

    글자가 아니라 **호출 노드**를 센다(`ast`) — 주석이나 설명문에 적어 둔 이름은 안 센다.
    `jobs/daily` 는 매 거래일 도는 유일한 작업이고, 관리 API 를 하루 한 번만 두드린다는
    약속도 여기서 지켜진다. 다른 작업이 부르기 시작하면 이 검사가 물어 준다.
    """
    import ast
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parent.parent / "batch" / "jobs"
    부르는곳 = set()
    for path in sorted(뿌리.glob("*.py")):
        나무 = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(나무):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "measure_db_size"
            ):
                부르는곳.add(path.name)

    assert 부르는곳 == {"daily.py"}, (
        f"DB 용량을 재는 곳이 {sorted(부르는곳) or '없다'}. "
        "하루 한 번 도는 작업 하나가 재야 한다 — 아무도 안 부르면 상수만 하나 더 생긴 것이다"
    )


class Test잰_수를_사람에게_말한다:
    """**80% 경고의 뒤 절반** (docs/infra.md 25.142).

    25.123 이 용량 게이지를 만들었다. 그런데 그 상태는 `step_log` 에만 들어가 **배치 기록을
    펼쳐야** 보였다. 규칙은 "80% 경고, 100% 중단"(CLAUDE.md)인데 재기만 하고 말하지
    않았으니 25.0 「절반만 지킨 규칙」이다.

    이 한도는 **셋 중 유일하게 자정에 안 풀린다.** 차면 지우기 전까지 모든 쓰기가 막히고,
    무엇을 지울지는 아직 정해 두지도 않았다(25.131). 그래서 경고는 **일찍, 사람이 읽는
    자리**(아침 리포트)에 나와야 한다.
    """

    def _잰다(self, mem: MemClient, 비율: float) -> None:
        mem.database_size = lambda: int(d1.FREE_DB_BYTES * 비율)  # type: ignore[attr-defined]
        db.measure_db_size(mem)  # type: ignore[arg-type]

    def test_넉넉하면_아무_말도_안_한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """멀쩡한 날마다 한 줄이 붙으면 경고를 안 읽게 된다."""
        mem = _mem(monkeypatch, "d1")
        self._잰다(mem, 0.5)
        assert db.db_size_note(mem) is None  # type: ignore[arg-type]

    def test_팔할을_넘으면_말한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        self._잰다(mem, 0.85)
        글 = db.db_size_note(mem)  # type: ignore[arg-type]

        assert 글 and "⚠" in 글
        assert "85%" in 글, "얼마나 찼는지 숫자가 있어야 사람이 판단한다"
        assert "자정에 풀리지 않습니다" in 글, "이 한도의 성질이 경고에 있어야 한다"

    def test_차면_더_세게_말한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        self._잰다(mem, 1.0)
        글 = db.db_size_note(mem)  # type: ignore[arg-type]

        assert 글 and "🛑" in 글
        assert "아침 리포트" in 글, "무엇을 잃는지 말해야 한다"

    def test_무엇이_채웠는지_보는_법을_알려_준다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """경고만 하고 다음 걸음을 안 알려 주면 사람이 할 수 있는 일이 없다 (25.131)."""
        mem = _mem(monkeypatch, "d1")
        self._잰다(mem, 0.9)
        글 = db.db_size_note(mem) or ""  # type: ignore[arg-type]

        assert "--tables" in 글
        assert "docs/" not in 글, "텔레그램으로 나가는 글이다. 폰에서 저장소 파일을 열 수 없다"

    def test_못_잰_날에는_말하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**어제 값으로 오늘을 말하지 않는다.** 오늘 창에 행이 없으면 조용하다."""
        mem = _mem(monkeypatch, "d1")
        mem.database_size = lambda: None  # type: ignore[attr-defined]
        db.measure_db_size(mem)  # type: ignore[arg-type]
        assert db.db_size_note(mem) is None  # type: ignore[arg-type]

    def test_말하는_일이_배치를_죽이지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, "d1")
        mem.conn.execute("DROP TABLE api_usage")
        assert db.db_size_note(mem) is None  # type: ignore[arg-type]

    def test_아침_리포트가_이것을_싣는다(self) -> None:
        """**부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 이 고친 그 모양).

        글자가 아니라 호출 노드를 세고, 그 결과가 `warnings` 에 **담기는지**까지 본다.
        담지 않으면 리포트에 안 실리고, 그러면 다시 아무도 안 본다.
        """
        import ast
        from pathlib import Path

        글 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(
            encoding="utf-8"
        )
        나무 = ast.parse(글)
        부름 = [
            n
            for n in ast.walk(나무)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "db_size_note"
        ]
        assert 부름, "아무도 db_size_note 를 부르지 않는다 — 경고가 어디에도 안 나간다"

        # **그 결과가 warnings 에 담기는지**까지 본다. `warnings.append` 는 이 파일에 여럿이라
        # 아무거나 세면 내 변경이 없어도 통과한다 — 담은 것이 **이 함수의 결과**여야 한다
        받은이름 = {
            t.id
            for n in ast.walk(나무)
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "db_size_note"
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        assert 받은이름, "db_size_note 의 결과를 아무 데도 담지 않았다"

        담는다 = [
            n
            for n in ast.walk(나무)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "warnings"
            and any(isinstance(a, ast.Name) and a.id in 받은이름 for a in n.args)
        ]
        assert 담는다, "경고를 만들어 놓고 warnings 에 담지 않으면 리포트에 안 실린다"
