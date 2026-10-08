"""워크플로 YAML 이 성한가 (docs/infra.md 13절·25절).

2026-09-18 에 D1 환경변수를 스크립트로 끼워 넣다가 **들여쓰기를 망가뜨렸다.** 스텝 안의 env 와
잡 수준의 env 는 들여쓰기가 다른데 한 가지로 밀어 넣었다. 그러자 GitHub 이 파일을 읽지 못해
"workflow_dispatch 트리거가 없다" 며 실행을 거부했다 — 문법이 깨지면 조용히 사라지는 것이 아니라
**트리거째 사라진다.** 그래서 파일이 파싱되는지, 필요한 설정이 빠지지 않았는지 여기서 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML 이 있어야 워크플로를 검증한다")

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def test_워크플로가_있다() -> None:
    assert len(WORKFLOWS) > 20


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
class Test워크플로:
    def test_문법이_성하다(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "최상위가 매핑이어야 한다"
        # PyYAML 은 on: 을 불리언 True 로 읽는다(YAML 1.1). 둘 중 하나로 트리거가 있어야 한다
        assert ("on" in data) or (True in data), "트리거(on)가 없다"
        assert "jobs" in data and data["jobs"], "jobs 가 없다"

    def test_DB_를_쓰면_백엔드_설정도_넘긴다(self, path: Path) -> None:
        """Turso 자격만 넘기고 D1 설정을 빠뜨리면 전환한 날 그 작업만 옛 DB 를 본다."""
        text = path.read_text(encoding="utf-8")
        if "TURSO_AUTH_TOKEN" not in text:
            return
        for key in ("DB_BACKEND", "D1_ACCOUNT_ID", "D1_DATABASE_ID", "D1_API_TOKEN"):
            assert key in text, f"{path.name} 에 {key} 가 없다"


#: D1 임시 운영 중에 쉬는 워크플로 (docs/infra.md 25.14). 따라잡기와 겹치거나(유니버스·재무·점수…),
#: 첫 수집이 하루 쓰기 한도를 넘기거나(내부자), D1 에 없는 미국 데이터를 쓴다
PAUSE_ON_D1 = {
    "universe.yml", "financials.yml", "metrics.yml", "scores.yml", "signals.yml", "insider-kr.yml",
    "us-shares.yml", "us-financials.yml", "refresh-us-adjusted.yml", "disclosures-us.yml", "backfill-us.yml",
    "recompute.yml",
    "kis-flows.yml",  # 첫 수집이 몇만 행을 쓴다 (25.987)
    "analyze-stock.yml",  # 재무·지표 수집을 한 종목으로 부른다 — 둘 다 D1 에서 쉰다 (25.1018)
}  # fmt: skip
#: D1 에서 돌아야 하는 것. 미국 일일 배치는 쉬되 스스로 skipped 기록을 남긴다(jobs/daily.py)
RUN_ON_D1 = {"d1-catchup.yml", "daily-kr.yml", "backfill-kr.yml", "turso-return.yml", "daily-us.yml"}

#: **DB 백엔드와 상관없는 워크플로** (2026-09-21, docs/infra.md 25.76).
#:
#: 위 두 목록은 손으로 적은 것이라 **41개 중 16개만 덮고 있었다.** 나머지 25개는 어느 쪽도
#: 아니어서, 새 워크플로를 더해도 아무도 "D1 에서 이건 어떡하지" 를 묻지 않았다.
#: 목록이 낡는 전형적인 모양이다(25.0 의 "한 규칙이 두 곳"·"목록은 반드시 낡는다").
#:
#: 그래서 **셋으로 나눠 41개를 전부 적는다.** 여기 있는 것은 "D1 이든 Turso 든 상관없다" 는
#: **판단**이지 빠뜨린 것이 아니다. 사유를 한 줄씩 남긴다.
IRRELEVANT_ON_D1 = {
    # 데이터를 안 쓴다 (검사·도구)
    "tests.yml": "테스트만 돈다",
    "tests-web.yml": "테스트만 돈다",
    "step0-check.yml": "바깥 API 가 살아 있는지만 본다",
    "probe-dart-insider.yml": "스펙 확인용 probe",
    "probe-heavy-reads.yml": "스펙 확인용 probe",
    "probe-sec-filings.yml": "스펙 확인용 probe",
    "probe-etf-nport.yml": "스펙 확인용 probe (ETF 전 종목 보유, docs/data-sources.md 13.5)",
    "probe-kr-etf-pdf.yml": "스펙 확인용 probe (국내 ETF 구성종목 경로, docs/etf.md 11.5)",
    "probe-kis.yml": "스펙 확인용 probe (한국투자증권 API 클라우드 호출, docs/data-sources.md 3, infra 25.983)",
    "probe-krx-shares.yml": "스펙 확인용 probe",
    "korfinasc-bench.yml": "벤치마크. 손으로만 돌린다",
    # 백엔드를 다루는 일 자체 (D1 에서도 돌아야 한다)
    "migrate.yml": "스키마를 올리는 일. 어느 백엔드든 필요하다",
    "backup.yml": "백업은 백엔드와 무관하게 필요하다",
    "restore-backup.yml": "복구. 손으로만 돌린다",
    "restore-drill.yml": "복구 리허설. 로컬 SQLite 에만 쓰고 운영 DB 에는 기록 한 줄 (25.946)",
    "move-user-data.yml": "Turso↔D1 사이에 사람이 넣은 값을 옮긴다",
    "db-status.yml": "읽기만 한다. 막혔는지 보려고 부르는 것이다",
    # 국내 기능이라 D1 에서도 돈다 — **쉬게 할지 한 번 따져 봤고, 그대로 두기로 했다**
    "adjust-kr.yml": "국내 수정주가. 시세가 들어오는 한 이어져야 한다",
    "portfolio.yml": "사용자 매매 재계산. 사람이 입력하면 바로 돌아야 한다",
    "monitor-targets.yml": "장중 감시 대상. 웹 크론이 이것을 읽는다",
    "index-prices.yml": "지수 3행/일. 추세 필터와 베타(25.65)의 재료다",
    "sentiment-kr.yml": "국내 뉴스 감성. 쓰기가 작고 센티먼트 축이 총점에 들어간다(25.66)",
    "dividends.yml": "배당. 월 2회, 국내만",
    "earnings-calendar.yml": "실적 발표일. 주 1회, 쓰기가 작다",
    "signal-outcomes.yml": "신호 성적표. 주 1회, 읽기 위주",
    "valuation-bands.yml": "밸류에이션 밴드. 주 1회, 장기 신호의 재료다",
    "sectors.yml": "업종. 월 1회 — **따라잡기 3단계와 겹치지만** 그쪽이 30일 안에 돌았으면 건너뛴다",
    "accumulation.yml": "장기 적립 종목. 월 1회",
    "etf.yml": "ETF 프로필. 월 1회",
    "backtest.yml": "백테스트. 매달 3일 기본 실행 예약 + 수동 (25.942)",
    "weekly-summary.yml": "주간 운영 요약. 읽기만 하고 텔레그램 한 통 (25.943)",
}
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


@pytest.mark.parametrize("name", sorted(PAUSE_ON_D1))
def test_D1_에서_쉬는_워크플로는_잡_환경에_표시가_있다(name: str) -> None:
    data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    for job in data["jobs"].values():
        assert str((job.get("env") or {}).get("SKIP_ON_D1")) == "1", f"{name} 에 SKIP_ON_D1 이 없다"


@pytest.mark.parametrize("name", sorted(RUN_ON_D1))
def test_D1_에서_돌아야_하는_워크플로에는_표시가_없다(name: str) -> None:
    assert "SKIP_ON_D1" not in (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def test_모든_워크플로가_분류돼_있다() -> None:
    """**빠뜨림과 판단을 가른다** (docs/infra.md 25.76).

    새 워크플로를 더하면서 "D1 에서 이건 어떡하지" 를 묻지 않으면, 그 워크플로는 조용히
    돈다. 2026-09-21 에 41개 중 **25개가 어느 목록에도 없었다** — 그중 여덟은 예약까지
    걸린 쓰기 작업이었다. 셋 중 하나에는 반드시 들어가야 한다.
    """
    전체 = {p.name for p in WORKFLOW_DIR.glob("*.yml")}
    분류됨 = PAUSE_ON_D1 | RUN_ON_D1 | set(IRRELEVANT_ON_D1)

    빠짐 = sorted(전체 - 분류됨)
    없는것 = sorted(분류됨 - 전체)

    assert not 빠짐, (
        "어느 목록에도 없는 워크플로가 있다. D1 에서 쉴지 돌지 **정하고** 적어라:\n  "
        + "\n  ".join(빠짐)
    )
    assert not 없는것, f"목록이 없는 파일을 가리킨다: {없는것}"


def test_한_워크플로가_두_목록에_있지_않다() -> None:
    쌍 = [
        ("PAUSE/RUN", PAUSE_ON_D1 & RUN_ON_D1),
        ("PAUSE/무관", PAUSE_ON_D1 & set(IRRELEVANT_ON_D1)),
        ("RUN/무관", RUN_ON_D1 & set(IRRELEVANT_ON_D1)),
    ]
    겹침 = [f"{이름}: {sorted(s)}" for 이름, s in 쌍 if s]

    assert not 겹침, "\n".join(겹침)


def test_무관하다고_적은_것에는_사유가_있다() -> None:
    """사유 없는 목록은 다음 사람이 믿을 수 없다 — 빠뜨린 것과 구별이 안 된다."""
    빈것 = [이름 for 이름, 사유 in IRRELEVANT_ON_D1.items() if len(사유.strip()) < 5]

    assert not 빈것, f"사유가 없다: {빈것}"


def test_무관한_것에는_SKIP_ON_D1_이_없다() -> None:
    """"상관없다" 고 적어 놓고 쉬게 해 두면 둘 중 하나가 거짓말이다."""
    어긴것 = [이름 for 이름 in IRRELEVANT_ON_D1 if "SKIP_ON_D1" in (WORKFLOW_DIR / 이름).read_text(encoding="utf-8")]

    assert not 어긴것, f"무관하다면서 SKIP_ON_D1 이 있다: {어긴것}"


#: 워크플로가 출력을 파일에 한 부 받는 파이프 (25.978 — `tee` 대신, 공개 저장소면 로그에 찍지 않는다)
TEE = "| python scripts/ops_tee.py"


def test_맨_tee_는_쓰지_않는다() -> None:
    """공개 저장소로 옮기면(docs/public-repo.md) 로그를 누구나 본다. 맨 `tee` 는 리포트 본문·보유·금액을 로그에 찍는다."""
    맨tee = [p.name for p in WORKFLOWS if "| tee " in p.read_text(encoding="utf-8")]
    assert not 맨tee, f"맨 tee 를 쓰는 워크플로: {맨tee} — scripts/ops_tee.py 를 써라 (infra 25.978)"


def test_tee_를_쓰는_워크플로가_실제로_있다() -> None:
    """**아래 검사들은 `tee` 를 쓰는 블록에만 걸린다.** 어느 날 전부 사라지면 검사가
    0번 돌고 조용히 통과한다 — 그러면 배치가 죽어도 단계가 성공으로 보이고(pipefail),
    기록이 이슈로 올라가지도 않는다(25.21). 먼저 몇 개나 있는지 센다 (docs/infra.md 25.51).
    """
    # 25.978 부터 `tee` 대신 `scripts/ops_tee.py`(공개 저장소면 로그에 찍지 않는 tee)를 쓴다 — 파이프 모양은 같다
    쓰는것 = [p.name for p in WORKFLOWS if TEE in p.read_text(encoding="utf-8")]

    assert len(쓰는것) >= 5, f"tee 를 쓰는 워크플로가 {len(쓰는것)}개뿐이다: {쓰는것}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
class Test기록을_이슈로_내보내는_워크플로:
    """로그를 파일로 받아 이슈에 올리는 워크플로가 지켜야 할 것 (docs/infra.md 25.21).

    클라우드 세션은 Actions 로그 원문을 못 읽어서(25.17) 각 단계 출력을 `tee` 로 한 부 받아
    이슈에 올린다. 그런데 **`tee` 는 파이프의 마지막 명령이라 그 성공이 곧 단계의 성공이 된다** —
    `set -o pipefail` 이 없으면 배치가 죽어도 단계가 초록으로 보인다. 실패를 보려고 만든 장치가
    실패를 감추는 꼴이다.
    """

    @staticmethod
    def _run_블록(text: str) -> list[str]:
        """`run: |` 로 시작하는 여러 줄 블록들."""
        return re.findall(r"(?m)^ +run: \|\n(?:^ {8,}.*\n|^\s*\n)+", text)

    def test_tee_를_쓰면_pipefail_이_있다(self, path: Path) -> None:
        for 블록 in self._run_블록(path.read_text(encoding="utf-8")):
            if TEE not in 블록:
                continue
            assert "set -o pipefail" in 블록, (
                f"{path.name}: tee 로 파이프를 거는데 set -o pipefail 이 없다 —"
                f" 배치가 죽어도 단계가 성공으로 보인다\n{블록}"
            )

    def test_이슈로_내보내면_쓰기_권한을_준다(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        if "publish_output.py" not in text:
            return
        data = yaml.safe_load(text)
        perms = data.get("permissions") or {}
        assert perms.get("issues") == "write", f"{path.name} 에 permissions.issues: write 가 없다 — 403 이 난다"

    def test_내보내기는_멈춰도_돈다(self, path: Path) -> None:
        """멈춘 이유를 읽으려고 두는 장치다. 성공했을 때만 올리면 쓸모가 없다."""
        text = path.read_text(encoding="utf-8")
        if "publish_output.py" not in text:
            return
        data = yaml.safe_load(text)
        for job in data["jobs"].values():
            for step in job["steps"]:
                if "publish_output.py" in str(step.get("run", "")):
                    assert "always()" in str(step.get("if", "")), (
                        f"{path.name}: 내보내기 단계에 if: always() 가 없다"
                    )


#: `python -m <모듈>` 을 부르면서 구간을 반드시 줘야 하는 배치와, 그 중 하나는 있어야 하는 옵션.
#: 2026-09-19 에 `backfill_kr` 을 `--from` 만 주고 부른 워크플로 둘이 매번 종료코드 2 로
#: 죽고 있었다 (docs/infra.md 25.16). 지금은 `--from` 단독도 받지만, 구간을 아예 안 주면
#: 여전히 죽는다 — 그 실수는 여기서 잡는다
구간이_필요한_배치 = {"batch.jobs.backfill_kr": ("--days", "--from")}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_구간이_필요한_배치를_구간_없이_부르지_않는다(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for module, options in 구간이_필요한_배치.items():
        for line in text.splitlines():
            if f"-m {module}" not in line:
                continue
            # 셸 변수로 넘기는 워크플로가 있다. 그것까지 풀어 보지는 못하므로 변수가 있으면 믿는다
            if "$" in line.split(module, 1)[1]:
                continue
            assert any(option in line for option in options), (
                f"{path.name}: {module} 을 {' 나 '.join(options)} 없이 부른다 — 종료코드 2 로 죽는다\n  {line.strip()}"
            )


class Test테스트_워크플로가_도는_조건:
    """`tests.yml` · `tests-web.yml` 은 **손으로만** 돈다 (2026-10-06, docs/infra.md 25.975).

    예전(25.39)에는 푸시마다 돌되 `paths` 로 줄였다. 그래도 10월 1~6일 엿새 만에 월 무료 2,000분을 다 썼고
    (테스트만 약 600분), GitHub 이 "spending limit" 로 **일일 배치까지** 막았다. 커밋은 푸시 전에 로컬에서 전체
    테스트를 통과해야 만들어지므로 푸시마다 다시 도는 것은 중복이다. 이 검사는 누가 `push` 를 되살리면 깨진다.
    """

    def _on(self, name: str) -> dict:
        data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        on = data.get("on", data.get(True))
        return on if isinstance(on, dict) else {on: None}

    def test_파이썬_테스트가_정말_웹_소스를_읽는다(self) -> None:
        """손으로 돌릴 때도 웹을 고친 커밋이면 배치 테스트를 함께 돌려야 하는 까닭 — 전제가 바뀌면 깨진다."""
        읽는것 = [
            p.name
            for p in (Path(__file__).resolve().parent).glob("test_*.py")
            if '"web"' in p.read_text(encoding="utf-8")
        ]

        assert 읽는것

    def test_테스트는_푸시마다_돌지_않는다(self) -> None:
        for name in ("tests.yml", "tests-web.yml"):
            on = self._on(name)
            assert "push" not in on and "pull_request" not in on, f"{name} 이 다시 푸시마다 돈다 (25.975)"
            assert "workflow_dispatch" in on, f"{name} 을 손으로 돌릴 수 없다"


class Test단계가_죽어도_사용량은_잰다:
    """`d1-catchup.yml` 의 단계마다 D1 사용량 측정기가 **반드시** 돈다 (docs/infra.md 25.52).

    **왜 중요한가.** 따라잡기는 하루 한 번 돌고, 지금은 Actions 가 멈춰 있어 되살아나면
    **한 번의 실행으로 전부 알아내야 한다**(25.26 의 어림을 실측으로 바꾸는 일). 그런데
    측정기가 배치 명령 **바로 다음 줄**에 있었고, GitHub 의 기본 셸은 `bash -e` 다.
    배치가 0 이 아닌 코드로 죽으면 그 블록이 거기서 끝나 **측정기가 통째로 건너뛰어진다** —
    하필 예산을 가장 알고 싶은 순간에 그 숫자를 잃는다.

    (한도로 멈추는 경우는 `guard()` 가 0 으로 바꾸므로 원래도 쟀다. 잃는 것은 그 밖의
    고장 — 버그·네트워크 — 인데, 그때가 "어디서 예산이 샜나" 를 봐야 할 때다.)

    이제 `|| rc=$?` 로 받고 끝에 `exit $rc` 로 되돌린다. 실패는 그대로 실패로 남는다.
    """

    경로 = WORKFLOW_DIR / "d1-catchup.yml"

    def _배치_블록(self) -> list[tuple[str, str]]:
        """(단계 이름, run 블록). 배치를 부르면서 사용량을 재는 단계만."""
        data = yaml.safe_load(self.경로.read_text(encoding="utf-8"))
        잡 = next(iter(data["jobs"].values()))
        return [
            (str(s.get("name", s.get("id", "?"))), str(s["run"]))
            for s in 잡["steps"]
            if "run" in s and "d1_usage.py" in str(s["run"]) and "-m batch.jobs." in str(s["run"])
        ]

    def test_잴_단계를_찾아냈다(self) -> None:
        # 0개면 아래가 전부 공짜로 통과한다 (25.51 에서 배운 것)
        assert len(self._배치_블록()) >= 8

    def test_배치가_죽어도_측정기가_돈다(self) -> None:
        어긴것 = [
            이름
            for 이름, 블록 in self._배치_블록()
            if "|| rc=$?" not in 블록 or "exit $rc" not in 블록
        ]

        assert not 어긴것, (
            "배치 명령을 `|| rc=$?` 로 받지 않는다. bash -e 라 죽으면 그 뒤의"
            " d1_usage.py 가 건너뛰어지고 그 단계의 사용량을 잃는다:\n  " + "\n  ".join(어긴것)
        )

    def test_실패를_성공으로_바꾸지_않는다(self) -> None:
        """`|| rc=$?` 만 하고 `exit $rc` 를 잊으면 **죽은 단계가 성공으로 보인다.**

        그러면 알림이 "✅ 끝" 을 보내고 아무도 멈춘 줄 모른다. 25.21 의 pipefail 과 같은 함정이다.
        """
        for 이름, 블록 in self._배치_블록():
            assert 블록.rstrip().endswith("exit $rc"), f"{이름}: 블록이 exit $rc 로 끝나지 않는다"

    def test_앞_단계가_죽어도_뒤_단계가_돈다(self) -> None:
        """1~8 은 서로 기댈 필요가 없다 (docs/infra.md 25.63).

        **왜 중요한가.** GitHub 의 `if:` 는 아무것도 안 쓰면 `success()` 를 뜻한다. 그래서
        4번(과거 시세)이 한 번 죽으면 5~8번(성과·점수·신호·감시)이 **조용히 건너뛰어졌다.**
        그 넷은 시세를 못 받은 날에도 **있는 데이터로 할 일이 있다.** 하루 한 번뿐인 실행에서
        한 단계의 사고가 나머지 넷을 데려가면 그날의 추천이 통째로 사라진다.
        """
        data = yaml.safe_load(self.경로.read_text(encoding="utf-8"))
        잡 = next(iter(data["jobs"].values()))
        배치단계 = [
            s for s in 잡["steps"] if "run" in s and "-m batch.jobs." in str(s["run"])
        ]

        assert len(배치단계) >= 8
        어긴것 = [
            str(s.get("name", "?")) for s in 배치단계 if "always()" not in str(s.get("if", ""))
        ]
        assert not 어긴것, (
            "`if:` 에 always() 가 없으면 앞 단계가 죽을 때 조용히 건너뛰어진다:\n  "
            + "\n  ".join(어긴것)
        )

    def test_그래도_D1_일_때만_돈다(self) -> None:
        """`always()` 를 붙이면서 **백엔드 조건까지 지우면** Turso 로 돌아간 날에도 돈다."""
        data = yaml.safe_load(self.경로.read_text(encoding="utf-8"))
        잡 = next(iter(data["jobs"].values()))
        for s in 잡["steps"]:
            if "run" in s and "-m batch.jobs." in str(s["run"]):
                assert "use_d1" in str(s.get("if", "")), f"{s.get('name')}: 백엔드 조건이 없다"

    def test_측정기는_받지_않는다(self) -> None:
        """측정기 자신이 실패해도 단계는 죽지 않아야 한다 — 그 스크립트는 언제나 0 으로 끝난다.

        여기서 `|| rc=$?` 를 붙이면 **재는 일이 재어지는 일을 망친다**.
        """
        for 이름, 블록 in self._배치_블록():
            for 줄 in 블록.splitlines():
                if "d1_usage.py" in 줄:
                    assert "rc=$?" not in 줄, f"{이름}: 측정기에 rc 를 받고 있다"


class Test0번이_깨져도_조용하지_않다:
    """따라잡기의 0번 단계와, 그 결과에 매달린 보고 단계들 (docs/infra.md 25.53).

    0번은 "지금 D1 을 쓰는가" 를 알아내 `use_d1` 로 내보내고, 1~8번이 그 값을 보고 돈다.
    문제는 **9번(텔레그램)과 10번(이슈 내보내기)도 그 값을 보고 있었다**는 것이다.

    0번이 예외로 죽으면 `use_d1` 이 비고 → 열 단계가 **전부** 건너뛰어진다.
    닷새 만에 다시 도는 날 가장 먼저 깨질 만한 자리인데, 깨지면 **알림도 기록도 없이**
    끝난다. 클라우드 세션은 Actions 로그를 못 읽으므로(25.17) 이유를 알 길이 없다.
    """

    def _단계(self, 이름앞: str) -> dict:
        data = yaml.safe_load((WORKFLOW_DIR / "d1-catchup.yml").read_text(encoding="utf-8"))
        잡 = next(iter(data["jobs"].values()))
        찾음 = [s for s in 잡["steps"] if str(s.get("name", "")).startswith(이름앞)]
        assert len(찾음) == 1, f"{이름앞} 로 시작하는 단계가 {len(찾음)}개다"
        return 찾음[0]

    def test_0번은_무슨_일이_있어도_값을_적는다(self) -> None:
        블록 = str(self._단계("0.")["run"])

        assert "BACKEND=unknown" in 블록, "알아내지 못했을 때의 기본값이 없다"
        assert 'echo "use_d1=$USE" >> "$GITHUB_OUTPUT"' in 블록, "출력을 적지 않는 길이 있다"

    def test_0번의_실패_이유가_로그에_남는다(self) -> None:
        """이슈로 나가는 것은 `catchup.log` 뿐이다. 여기 안 적으면 이유가 사라진다.

        **실패한 쪽 가지**를 콕 집어 본다. 성공 줄에만 `tee` 가 있어도 "로그를 남긴다" 로
        읽히는데, 정작 필요한 것은 죽었을 때의 문구다.
        """
        블록 = str(self._단계("0.")["run"])
        실패가지 = 블록.split("else", 1)[-1]

        assert "else" in 블록, "실패했을 때의 가지가 없다"
        assert 'echo "$OUT" | python scripts/ops_tee.py -a catchup.log' in 실패가지, "파이썬이 뱉은 이유를 로그에 안 남긴다"

    def test_이슈_내보내기는_0번_결과를_보지_않는다(self) -> None:
        """0번이 깨져서 아무것도 안 돈 날이야말로 이유를 읽어야 하는 날이다."""
        조건 = str(self._단계("10.").get("if", ""))

        assert "always()" in 조건
        assert "use_d1" not in 조건, "0번이 깨지면 기록도 안 나간다"

    def test_복귀_점검도_0번_결과를_보지_않는다(self) -> None:
        """9-1 은 DB 없이도 STEPS_JSON·event 로 절반을 판정한다 (docs/infra.md 25.80).

        0번이 깨져 아무것도 안 돈 날에도 "무엇이 깨웠나"·"5~8단계가 돌았나" 는 읽을 수 있고,
        그날이야말로 읽어야 하는 날이다. `use_d1` 을 보면 그 절반까지 사라진다.
        """
        단계 = self._단계("9-1.")

        assert "always()" in str(단계.get("if", ""))
        assert "use_d1" not in str(단계.get("if", "")), "0번이 깨지면 점검도 사라진다"

    def test_복귀_점검이_판정을_코멘트_맨_위에_올린다(self) -> None:
        """`d1-usage.log` 가 `--head-file` 이라 잘리지 않는 자리다 (25.55). 거기에 **덧붙인다**."""
        블록 = str(self._단계("9-1.")["run"])

        assert "ops_tee.py -a catchup.log d1-usage.log" in 블록, "덮어쓰면 단계별 사용량이 사라진다"

    def test_텔레그램은_D1_을_쓸_때만_보낸다(self) -> None:
        # 이쪽은 반대다. Turso 로 돌아가 따라잡을 이유가 없는 날까지 알리면 소음이다
        조건 = str(self._단계("9.").get("if", ""))

        assert "always()" in 조건 and "use_d1" in 조건


def test_따라잡기_요약이_이슈에도_남는다() -> None:
    """텔레그램으로 보낸 요약이 `catchup.log` 에도 들어가는가 (docs/infra.md 25.54).

    클라우드 세션이 읽는 것은 10번 단계가 올리는 `catchup.log` **하나뿐**이다(25.17).
    9번이 만드는 요약에는 유니버스 종목 수·받은 거래일·오늘 쓴 D1 행이 한 줄로 들어 있는데,
    그것이 텔레그램으로만 가면 세션은 **"그래서 지금 몇 거래일인가" 를 사람에게 물어야 한다.**
    Actions 를 하루 한 번만 깨우기로 한 마당에(25.27) 그 한 번에서 알 수 있는 것을 흘리면 안 된다.
    """
    data = yaml.safe_load((WORKFLOW_DIR / "d1-catchup.yml").read_text(encoding="utf-8"))
    잡 = next(iter(data["jobs"].values()))
    알림 = [s for s in 잡["steps"] if "catchup_report.py" in str(s.get("run", ""))]

    assert len(알림) == 1, f"결과 알림 단계가 {len(알림)}개다"
    블록 = str(알림[0]["run"])
    assert "ops_tee.py -a catchup.log" in 블록, "요약이 텔레그램에만 간다 — 세션은 못 읽는다"
    assert "set -o pipefail" in 블록


class Test사용량_줄은_잘리지_않는다:
    """단계별 D1 사용량을 **따로 모아** 이슈 코멘트 맨 위에 붙인다 (docs/infra.md 25.55).

    따라잡기 로그는 6만 자를 넘길 수 있고, 그러면 `publish_output.truncate()` 가
    **가운데를 덜어 낸다.** 하필 가운데에 있는 것이 `4. 과거 시세 끝` 같은 줄이다 —
    하루 한 번뿐인 실행에서 가장 알고 싶은 숫자가 **길이 때문에** 사라지면 그날은 헛수고다.
    """

    def _본문(self) -> str:
        return (WORKFLOW_DIR / "d1-catchup.yml").read_text(encoding="utf-8")

    def test_사용량_줄을_따로_모은다(self) -> None:
        본문 = self._본문()
        모으는줄 = [줄 for 줄 in 본문.splitlines() if "d1_usage.py" in 줄]

        assert len(모으는줄) >= 9, f"사용량을 재는 줄이 {len(모으는줄)}개뿐이다"
        빠진것 = [줄.strip()[:60] for 줄 in 모으는줄 if "d1-usage.log" not in 줄]
        assert not 빠진것, "따로 모으지 않는 줄이 있다 — 길면 잘려 사라진다:\n  " + "\n  ".join(빠진것)

    def test_내보내기가_그_파일을_맨_위에_붙인다(self) -> None:
        assert "--head-file d1-usage.log" in self._본문()

    def test_본문_파일은_그대로다(self) -> None:
        # 앞부분만 올리고 전체 로그를 빠뜨리면 "왜 멈췄나" 를 읽을 수 없다
        assert "--file catchup.log" in self._본문()


class Test복귀_워크플로가_미국_구멍도_메운다:
    """`turso-return.yml` 의 2-1·2-2 단계 (docs/infra.md 25.82).

    복귀 스크립트가 잰 날수를 **워크플로가 실제로 쓰는가**. 출력 이름 하나가 어긋나면
    조건이 빈 문자열과 비교돼 단계가 조용히 건너뛰어지고, 미국 시세와 지수는
    일일 배치의 **고정 10일 창**에 되돌아간다 — 그러면 구멍이 영영 남는다.
    """

    @property
    def _단계들(self) -> list[dict]:
        data = yaml.safe_load((WORKFLOW_DIR / "turso-return.yml").read_text(encoding="utf-8"))
        return next(iter(data["jobs"].values()))["steps"]

    def _하나(self, 이름앞: str) -> dict:
        찾음 = [s for s in self._단계들 if str(s.get("name", "")).startswith(이름앞)]
        assert len(찾음) == 1, f"{이름앞} 로 시작하는 단계가 {len(찾음)}개다"
        return 찾음[0]

    #: (단계 이름앞, 워크플로 출력, 부르는 모듈). **싼 것부터** 늘어놓는다 — 아래 순서 검사가 이것을 본다
    메우는단계 = (
        ("2-0.", "fx_days", "batch.jobs.fx"),  # 한 심볼 한 호출
        ("2-0b.", "disclosure_days", "batch.jobs.disclosures_kr"),  # 날짜를 늘려도 호출 수가 같다
        ("2-1.", "index_days", "batch.jobs.index_prices"),  # 지수 세 줄
        ("2-2.", "us_days", "batch.jobs.backfill_us"),  # 전 종목. 한도를 가장 많이 쓴다
    )

    def test_잰_날수를_그대로_넘긴다(self) -> None:
        for 이름앞, 출력, 모듈 in self.메우는단계:
            단계 = self._하나(이름앞)

            assert f"steps.ret.outputs.{출력}" in str(단계["run"]), f"{이름앞} 가 잰 날수를 안 쓴다"
            assert 모듈 in str(단계["run"])

    def test_0_이면_돌지_않는다(self) -> None:
        """구멍이 없는 날에도 돌면 Actions 분과 Turso 쓰기를 공짜로 태운다."""
        for 이름앞, 출력, _ in self.메우는단계:
            조건 = str(self._하나(이름앞).get("if", ""))

            assert f"steps.ret.outputs.{출력} != '0'" in 조건, f"{이름앞} 의 건너뛰기 조건이 없다"
            assert "moved == 'true'" in 조건, f"{이름앞} 가 옮기지도 않은 날에 돈다"

    def test_싼_것부터_받는다(self) -> None:
        """시세가 한도를 다 태우면 환율·지수가 또 빈다 (25.20 에서 겪은 그 모양)."""
        순서 = [str(s.get("name", "")) for s in self._단계들]
        자리 = [next(i for i, 이름 in enumerate(순서) if 이름.startswith(앞)) for 앞, _, _ in self.메우는단계]

        assert 자리 == sorted(자리), f"메우는 순서가 싼 것부터가 아니다: {자리}"

    def test_포트폴리오_재계산보다_먼저_받는다(self) -> None:
        """재계산이 먼저 돌면 구멍 뚫린 시세·환율로 계산하고, 다시 돌지 않는다."""
        순서 = [str(s.get("name", "")) for s in self._단계들]
        재계산 = next(i for i, 이름 in enumerate(순서) if 이름.startswith("3."))
        마지막 = max(next(i for i, 이름 in enumerate(순서) if 이름.startswith(앞)) for 앞, _, _ in self.메우는단계)

        assert 마지막 < 재계산

    def test_재계산_전에_환율을_받는다(self) -> None:
        """`portfolio.yml` 은 재계산 전에 환율을 받는데 복귀는 안 받았다 (25.83).

        비어도 실패하지 않는 것이 함정이다 — `_carry` 가 앞 값을 이어 붙여
        미국 보유 종목이 2주 묵은 환율로 평가된다.
        """
        본문 = (WORKFLOW_DIR / "turso-return.yml").read_text(encoding="utf-8")

        assert "batch.jobs.fx" in 본문, "복귀가 환율을 한 번도 받지 않는다"

    def test_거래대금_하한을_주지_않는다(self) -> None:
        """후보만 메우면 '어떤 종목은 구멍이 있는' 상태가 남는다 — 이 항목이 고치려는 모양이다."""
        assert "--min-turnover" not in str(self._하나("2-2.")["run"])


def test_두_시장을_잇달아_돌리는_줄은_앞이_실패해도_뒤가_돈다() -> None:
    """기본 셸이 `bash -e` 라 KR 이 실패하면 US 줄이 실행되지 않았다 — 미국 주간 유니버스가 한 주 묵었다
    (docs/infra.md 25.415). 같은 모듈을 KR·US 로 잇달아 부르는 줄은 모두 `|| rc=1` 로 실패를 모은다."""
    import re

    걸린: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        줄들 = path.read_text(encoding="utf-8").splitlines()
        for i, 줄 in enumerate(줄들[:-1]):
            m = re.search(r"python -m (\S+) --market KR\b", 줄)
            잇달아 = m is not None and f"python -m {m.group(1)} --market US" in 줄들[i + 1]
            if 잇달아 and ("|| rc=1" not in 줄 or "|| rc=1" not in 줄들[i + 1]):
                걸린.append(f"{path.name}:{i + 1}")
    assert 걸린 == [], f"KR 실패가 US 를 막는 줄: {걸린}"


def test_ETF_국내_판정이_실패해도_미국과_위성은_돈다() -> None:
    """국내·미국 판정이 따로 된 단계라 한 단계 안의 `|| rc=1` 검사(25.415)가 잡지 못했다 (docs/infra.md 25.424)."""
    data = yaml.safe_load((WORKFLOW_DIR / "etf.yml").read_text(encoding="utf-8"))
    steps = {s.get("name"): s for job in data["jobs"].values() for s in job["steps"]}
    assert "!cancelled()" in steps["미국 판정"]["if"]
    assert steps["미국 판정"]["id"] == "us"
    assert "!cancelled()" in steps["위성 판정"]["if"] and "steps.us.outcome != 'failure'" in steps["위성 판정"]["if"]


def test_Turso_복귀는_한_단계가_실패해도_나머지를_메운다() -> None:
    """복귀 표시는 1단계가 적어 다시 돌 길이 없다. 2단계 실패가 환율·지수·미국 시세를 막으면 구멍이 영영 남는다
    (docs/infra.md 25.429)."""
    data = yaml.safe_load((WORKFLOW_DIR / "turso-return.yml").read_text(encoding="utf-8"))
    steps = [s for job in data["jobs"].values() for s in job["steps"]]
    뒤단계 = [s for s in steps if str(s.get("name", ""))[:2] in ("2-", "3.", "4.")]
    assert len(뒤단계) == 6
    for s in 뒤단계:
        assert "!cancelled()" in str(s.get("if", "")), s["name"]


#: DART 를 실제로 부르는 배치 모듈과, 그것을 단계로 품은 일일 배치(국내). 상수만 쓰는
#: 모듈(`dart.ANNUAL_REPORT_CODE`)은 넣지 않는다
DART_CALLERS = (
    "batch.jobs.disclosures_kr", "batch.jobs.financials", "batch.jobs.insider_kr", "batch.jobs.dividends",
    "batch.jobs.sectors", "batch.jobs.analyze_extra",
)  # fmt: skip


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_DART_를_부르는_워크플로는_키를_넘긴다(path: Path) -> None:
    """**키가 빠지면 조용히 비어 간다** (2026-10-02, docs/infra.md 25.882).

    `daily-kr.yml` 이 `DART_API_KEY` 를 넘기지 않아 일일 배치의 공시 수집이 매일
    "DART_API_KEY 가 비어 있습니다" 로 끝났다. 배치는 partial 로 성공했고 보유 종목 공시 알림은 나가지 않았다.
    """
    text = path.read_text(encoding="utf-8")
    부름 = [m for m in DART_CALLERS if m in text]
    if re.search(r"batch\.jobs\.daily\b", text) and "--market KR" in text:
        부름.append("batch.jobs.daily --market KR (공시 수집 단계)")
    if not 부름:
        return
    assert "DART_API_KEY" in text, f"{path.name} 이 {부름} 을 부르는데 DART_API_KEY 를 넘기지 않는다"
