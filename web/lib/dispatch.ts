/**
 * 화면에서 GitHub Actions 배치를 깨운다 (docs/health.md 4장 "수동 실행").
 *
 * 왜: PC 가 꺼져 있어도 폰으로 전부 해야 한다(CLAUDE.md 스택). 배치가 안 돌았을 때 GitHub 앱을
 * 열어 워크플로를 찾는 대신 상태 화면에서 한 번 누른다. **자동 재실행이 아니다** — 사람이 상태를
 * 보고 누른다(docs/health.md 5장). 웹은 계산하지 않는다. 깨우기만 한다.
 *
 * repository_dispatch 만 쓴다. 포트폴리오 재계산·백테스트와 같은 토큰(Contents: write)이면 된다.
 * 이벤트 이름은 워크플로의 `types:` 와 같아야 하고, 안 맞아도 GitHub 은 204 를 주므로 테스트가 대조한다.
 */

export interface DispatchJob {
  key: string;
  label: string;
  /** 워크플로 `repository_dispatch.types` 의 이름 */
  event: string;
  /** 워크플로가 client_payload 로 읽는 것. 일일 배치는 예약 시각 밖이라 force 가 없으면 건너뛴다 */
  payload?: Record<string, string | boolean>;
  note: string;
  /** `batch_runs` 의 작업 이름·시장이 key 와 다를 때 (25.598 — 감성은 두 시장이 `sentiment` 하나를 쓴다). 없으면 key */
  run?: { name: string; market?: string };
}

export const JOBS: DispatchJob[] = [
  { key: "daily_kr", label: "국내 일일 배치", event: "daily-kr", payload: { force: true }, note: "시세 → 점수·신호 → 리포트 발송. 3~5분" },
  { key: "daily_us", label: "미국 일일 배치", event: "daily-us", payload: { force: true }, note: "전종목 시세라 15분 남짓. 장중에 돌리면 진행 중 가격은 저장하지 않는다" },
  { key: "scores", label: "점수 (국내·미국)", event: "scores", note: "유니버스 전 종목 팩터 점수. 1분" },
  { key: "signals", label: "매수 신호 (국내·미국)", event: "signals", note: "점수가 있어야 한다. 1분" },
  { key: "metrics", label: "성과 지표", event: "metrics", note: "CAGR·MDD·샤프. 몇 분" },
  { key: "universe", label: "유니버스 갱신", event: "universe", note: "주 1회 자동. 편입·제외 사유를 다시 낸다" },
  { key: "portfolio", label: "포트폴리오 재계산", event: "portfolio", note: "매매 기록으로 보유·손익을 다시 만든다" },
  { key: "signal_outcomes", label: "신호 성적표", event: "signal-outcomes", note: "지난 신호의 그 뒤 수익률 (docs/signals.md 10장). 주 1회 자동" },
  { key: "refresh_us_adjusted", label: "미국 수정주가 재수집", event: "refresh-us-adjusted", note: "배당·분할로 어긋난 종목만 5년치 다시 (docs/adjust.md 8장). 주 1회 자동" },
  { key: "earnings_calendar", label: "실적 일정 추정", event: "earnings-calendar", note: "법정 제출 기한으로 앞으로 1년치 (docs/portfolio.md 5장). 주 1회 자동" },
  { key: "disclosures_us", label: "미국 공시 수집", event: "disclosures-us", note: "SEC submissions 에서 지켜보는 종목의 최근 공시 (docs/data-sources.md 14.4). 주 1회 자동" },
  { key: "sentiment_kr", label: "국내 뉴스 감성 채점", event: "sentiment-kr", run: { name: "sentiment", market: "KR" }, note: "모인 기사를 채점해 종목별로 집계한다. 무응답 감시 대상 (25.595). 2~3분" },
  { key: "turso_return", label: "Turso 로 돌아가기", event: "turso-return", note: "Turso 가 풀렸으면 표·사람이 넣은 데이터를 옮기고 돌아간다. 아직 막혀 있으면 1분 안에 끝난다 (docs/infra.md 25.12). 풀리면 웹이 스스로 깨운다" },
];

export function findJob(key: string): DispatchJob | null {
  return JOBS.find((j) => j.key === key) ?? null;
}

export interface DispatchResult {
  dispatched: boolean;
  reason?: string;
}

export async function dispatchJob(job: DispatchJob, fetchImpl: typeof fetch = fetch): Promise<DispatchResult> {
  const token = process.env.GH_DISPATCH_TOKEN;
  const repo = process.env.GH_REPO;
  if (!token || !repo) {
    return { dispatched: false, reason: "GH_DISPATCH_TOKEN·GH_REPO 가 없어 깨울 수 없습니다. GitHub 앱 → Actions 에서 직접 실행하세요" };
  }
  try {
    const response = await fetchImpl(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: job.event, ...(job.payload ? { client_payload: job.payload } : {}) }),
      cache: "no-store",
    });
    if (response.status === 204) return { dispatched: true };
    return { dispatched: false, reason: `GitHub 이 ${response.status} 를 돌려줬습니다. 토큰 권한(Contents 쓰기)을 확인하세요` };
  } catch {
    return { dispatched: false, reason: "GitHub 호출에 실패했습니다" };
  }
}

/**
 * 수동 실행을 요청한 뒤 같은 단추를 잠가 두는 시간 (docs/infra.md 25.593). 러너가 실행 기록을 열기까지 1~2분, 일일 배치 한 번이 5~15분이라
 * 그 사이 한 번 더 누르면 줄을 서서 두 번 돈다. 15분이면 대개 기록(running)이 이어받는다 [확인필요: 작업별 실측]. 페이지를 새로 열면 풀린다
 */
export const RUN_REQUEST_LOCK_MINUTES = 15;

