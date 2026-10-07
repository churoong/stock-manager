import { execute } from "@/lib/db";
import { localDate } from "@/lib/market";

/**
 * 크론 경로의 **호출 기록** (docs/intraday.md 7장).
 *
 * 왜 남기나: 외부 크론(cron-job.org)이 실제로 부르고 있는지는 **우리 쪽 기록이 없으면
 * 알 길이 없다.** `cron/health` 와 `cron/news` 가 한 번도 안 불린 것을 알아챈 것도
 * 이 표가 비어 있어서였다(docs/infra.md 25.19).
 *
 * **2026-09-21 까지 같은 함수가 네 경로에 따로 있었다** (intraday·health·news·news-kr).
 * SQL 은 글자 하나까지 같았고 `job`·`market` 만 달랐다. 그런데 `day` 를 내는 방식은 이미
 * 갈라지기 시작해서, 셋은 손으로 `+9` 시간을 더했고 하나만 공용 함수를 썼다 —
 * 25.59 에서 본 것과 같은 모양이다. 갈라지기 전에 하나로 줄인다.
 *
 * **실패해도 본 작업을 막지 않는다.** 기록을 못 남기는 것이 크론을 멈출 이유는 아니다.
 * 한도로 막힌 동안에는 쓰기 자체를 하면 안 되므로 부르는 쪽이 아예 건너뛴다(25.6).
 */
export async function recordHeartbeat(
  job: string,
  /** 시장 표시. 시장을 가리지 않는 작업은 'ALL' */
  market: "KR" | "US" | "ALL",
  now: Date,
  outcome: string,
  detail: Record<string, unknown>,
): Promise<void> {
  // **미국 작업도 한국 날짜로 센다.** 이 숫자를 읽는 사람이 한국에 있고, "오늘 몇 번
  // 불렸나" 는 그 사람의 하루를 뜻한다. 시장 현지 날짜가 아니다 — 일부러 그렇다
  const day = localDate("KR", now);
  await execute(
    `INSERT INTO cron_heartbeats (job, market, called_at, outcome, detail, calls_today, day)
     VALUES (?, ?, ?, ?, ?, 1, ?)
     ON CONFLICT (job, market) DO UPDATE SET called_at = excluded.called_at, outcome = excluded.outcome,
       detail = excluded.detail, day = excluded.day,
       calls_today = CASE WHEN cron_heartbeats.day = excluded.day THEN cron_heartbeats.calls_today + 1 ELSE 1 END`,
    [job, market, now.toISOString(), outcome, JSON.stringify(detail), day],
  ).catch(() => undefined);
}
