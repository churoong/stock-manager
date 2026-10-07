"""`batch_runs.trigger_source` — **무엇이 이 실행을 깨웠나** (docs/infra.md 25.95).

왜 있나: `/status` 화면이 이 글자를 그대로 보여 준다(`web/components/StatusView.tsx`).
그런데 2026-09-21 까지 **스물여덟 작업이 `trigger="manual"` 을 손으로 박아** 넘기고 있었다.
매월 예약으로 도는 ETF 판정도, 주간 워크플로도, DB 에는 전부 "사람이 손으로 돌렸다" 였다.

제대로 알아내는 함수는 **`jobs/daily.py` 안에만** 있었다. 한 곳에서만 맞고 나머지는 틀렸다.

**깃발이 거짓말하면 깃발이 없느니만 못하다**(docs/infra.md 25.0 "깃발이 거짓말한다").
"예약이 안 불렸나, 불렸는데 실패했나" 는 운영에서 가장 먼저 묻는 것이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from batch.core import db

뿌리 = Path(__file__).resolve().parent.parent


class Test환경에서_알아낸다:
    @pytest.mark.parametrize(
        "event,기대",
        [
            ("schedule", "schedule"),
            ("repository_dispatch", "dispatch"),
            ("workflow_dispatch", "dispatch"),
            ("push", "manual"),
            ("", "manual"),
        ],
    )
    def test_사건_이름을_옮긴다(self, monkeypatch: pytest.MonkeyPatch, event: str, 기대: str) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)

        assert db.trigger_source() == 기대

    def test_환경변수가_아예_없으면_manual(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """로컬에서 돌린 것이다. **그것은 사실이다** — 모르는 것을 지어내지 않는다."""
        monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)

        assert db.trigger_source() == "manual"


class Test기본값이_맞는_쪽이다:
    """**안 주면 틀리는 것이 아니라 맞게** 되도록 기본값을 뒀다."""

    def test_안_넘기면_환경에서_알아낸다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.test_report_picks import SqliteClient

        monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
        client = SqliteClient()
        # 연 실행은 닫는다. 안 닫으면 `db._열린_실행` 에 남아 **뒤에 도는 모든 테스트**가
        # 그것을 물려받는다 (docs/infra.md 25.135)
        run_id = db.start_batch_run(client, job_name="시험", market="KR", trade_date="2026-09-18")  # type: ignore[arg-type]
        db.finish_batch_run(client, run_id, status="success")  # type: ignore[arg-type]

        적힌것 = client.conn.execute("SELECT trigger_source FROM batch_runs").fetchone()[0]
        assert 적힌것 == "schedule"

    def test_넘기면_넘긴_것을_쓴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.test_report_picks import SqliteClient

        monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
        client = SqliteClient()
        run_id = db.start_batch_run(
            client, job_name="시험", market="KR", trade_date="2026-09-18", trigger="복귀"  # type: ignore[arg-type]
        )
        db.finish_batch_run(client, run_id, status="success")  # type: ignore[arg-type]

        적힌것 = client.conn.execute("SELECT trigger_source FROM batch_runs").fetchone()[0]
        assert 적힌것 == "복귀"


class Test손으로_박은_곳이_없다:
    """**한 규칙이 두 곳에 있으면 한 곳만 고쳐진다** (docs/infra.md 25.0)."""

    def test_trigger_를_상수로_넘기지_않는다(self) -> None:
        새는것: list[str] = []
        for path in sorted((뿌리 / "batch").rglob("*.py")):
            if path.name == "db.py":
                continue  # 정의처
            나무 = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(나무):
                if not isinstance(node, ast.Call):
                    continue
                이름 = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if 이름 != "start_batch_run":
                    continue
                for kw in node.keywords:
                    if kw.arg == "trigger" and isinstance(kw.value, ast.Constant):
                        새는것.append(f"{path.name}:{node.lineno} trigger={kw.value.value!r}")

        assert not 새는것, (
            "trigger 를 글자로 박은 자리가 있다. 안 넘기면 `db.trigger_source()` 가 알아낸다 —\n  "
            + "\n  ".join(새는것)
        )

    def test_알아내는_함수가_하나뿐이다(self) -> None:
        """복사본이 생기면 둘이 갈라진다. 실제로 `daily.py` 안에만 있어서 이 일이 났다."""
        짓는곳 = [
            path.relative_to(뿌리).as_posix()
            for path in sorted((뿌리 / "batch").rglob("*.py"))
            if 'GITHUB_EVENT_NAME' in path.read_text(encoding="utf-8")
        ]

        assert 짓는곳 == ["batch/core/db.py"], f"사건 이름을 읽는 곳이 여럿이다: {짓는곳}"


class Test실행_기록을_안_남기고_끝나는_곳:
    """**실패도 기록한다.** 기록이 없으면 "예약이 안 불렸다" 와 구별되지 않는다."""

    @staticmethod
    def _이른_반환(path: Path) -> dict[str, list[int]]:
        """함수마다 `start_batch_run` 보다 **앞선** 0 아닌 return 의 줄 번호."""
        나온것: dict[str, list[int]] = {}
        나무 = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(나무) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            시작 = min(
                (
                    n.lineno
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "start_batch_run"
                ),
                default=None,
            )
            if 시작 is None:
                continue
            이른 = [
                n.lineno
                for n in ast.walk(fn)
                if isinstance(n, ast.Return)
                and n.lineno < 시작
                and isinstance(n.value, ast.Constant)
                and n.value.value not in (0, None)
            ]
            if 이른:
                나온것[fn.name] = 이른
        return 나온것

    #: 실패(0 아닌 종료코드)를 기록 없이 돌려줘도 되는 자리와 그 사유.
    #: **사람이 손으로 부르는 명령**만 여기 들어온다 — 예약이 부르는 것은 기록해야 한다
    예외 = {
        ("etf.py", "rejudge_us"): "사람이 --rejudge 로 부르는 명령. 예약에 없다",
    }

    def test_예약으로_도는_작업은_실패도_남긴다(self) -> None:
        새는것: list[str] = []
        for path in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
            for 함수, 줄들 in self._이른_반환(path).items():
                if (path.name, 함수) in self.예외:
                    continue
                새는것.append(f"{path.name}::{함수} (줄 {줄들})")

        assert not 새는것, (
            "`start_batch_run` 보다 먼저 실패를 돌려주는 자리가 있다. `batch_runs` 에 행이 안 남아\n"
            "  화면과 무응답 감시가 **'예약이 안 불렸다' 와 똑같이** 본다.\n  " + "\n  ".join(새는것)
        )

    def test_훑기가_실제로_무언가를_보고_있다(self) -> None:
        """조용히 0개를 세면 위가 공짜로 통과한다."""
        본것 = sum(
            1
            for path in (뿌리 / "batch" / "jobs").glob("*.py")
            if "start_batch_run" in path.read_text(encoding="utf-8")
        )

        assert 본것 >= 20, f"start_batch_run 을 부르는 작업을 {본것}개밖에 못 찾았다"

    def test_예외_목록이_살아_있다(self) -> None:
        """사유를 적어 둔 예외가 **실제로 그 모양인지** 본다. 아니면 목록이 낡은 것이다."""
        for (파일, 함수), 사유 in self.예외.items():
            실제 = self._이른_반환(뿌리 / "batch" / "jobs" / 파일)
            assert 함수 in 실제, f"{파일}::{함수} 는 이제 그 모양이 아니다 — 예외에서 지워라 ({사유})"


class Test상태화면이_이_값을_보여_준다:
    """고친 것이 **사용자에게 닿는지** 확인한다. 안 보여 주면 고칠 값어치가 없다."""

    def test_status_화면이_trigger_source_를_그린다(self) -> None:
        본문 = (뿌리 / "web" / "components" / "StatusView.tsx").read_text(encoding="utf-8")

        assert re.search(r"\{r\.trigger_source", 본문), (
            "/status 가 trigger_source 를 안 그린다. 그러면 이 값을 고칠 이유가 사라진다"
        )
