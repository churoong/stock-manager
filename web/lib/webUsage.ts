/**
 * **웹이 D1 에 쓰고 훑은 행을 센다** (docs/infra.md 25.124).
 *
 * 배치는 처음부터 셌다 — `batch/core/d1.D1Client._flush` 가 응답의 `rows_written`·`rows_read`
 * 를 모아 `api_usage` 에 적는다. **웹은 같은 DB 에 쓰면서 한 번도 세지 않았다.**
 * 그래서 하루 예산(쓰기 10만 · 읽기 500만)을 보는 눈이 **한쪽만 뜬 눈**이었다.
 *
 *   `db.remaining_d1_daily_writes()` 가 실제보다 넉넉한 수를 돌려준다. 그 수로
 *   따라잡기가 "오늘 이만큼 더 받아도 된다" 를 정한다 (docs/infra.md 25.20 에서
 *   한 번 넘겨 재무·업종이 이틀 내리 굶었다)
 *
 * 그 함수의 설명문은 이 사실을 **"웹은 매 요청이 따로라 카운터를 둘 곳이 없다"** 고 적어
 * 두었는데, 그 이유는 2026-09-22 에 낡았다 — `lib/apiUsage.ts` 가 웹에서 `api_usage` 에
 * 적는 길을 이미 냈다(25.105·25.117). 여기서 같은 길을 쓴다.
 *
 * ## 얼마나 자주 적나
 *
 * 적는 일 자체가 D1 쓰기를 한 번 더 쓴다. 그래서 문턱을 둔다 — 배치의
 * `core/turso.RowCounter` 와 같은 구조다.
 *
 *   쓰기  문턱 1. **쓴 요청마다 적는다.** 웹의 쓰기는 드물고 작다(장중 하트비트·알림·설정 저장).
 *         하루 2천 행 남짓이라 카운터 몫을 더해도 하루 예산의 1% 안이다.
 *         대신 **정확하다** — "적게 보이는 카운터는 없느니만 못하다"(23절)
 *   읽기  문턱 10,000. 화면 한 번이 수천 행을 훑으므로 매번 적으면 쓰기가 읽기를 따라 는다.
 *         **덜 세는 만큼은 손해가 아니라 모름이다** — 그 사실을 아래에 적어 둔다
 *
 * ## 못 세는 몫 `[확인필요: 실제로 얼마나 새는지 며칠 치 게이지로 본다]`
 *
 * 서버리스 인스턴스는 언제든 사라진다. 사라질 때 문턱 아래로 모아 둔 읽기(최대 9,999행)는
 * **그대로 잃는다.** 0 을 세는 것보다는 낫지만 정확한 수는 아니다. 쓰기는 문턱이 1 이라
 * 이 문제가 없다.
 *
 * ## Turso 에서도 센다 (2026-10-02, docs/infra.md 25.891)
 *
 * 예전에는 "웹의 Turso 경로(`/v2/pipeline`)는 응답에 훑은 행 수를 주지 않는다" 고 적고 세지 않았다.
 * 그런데 **같은 경로를 쓰는 배치**(`batch/core/turso._to_result_set`)는 `result.rows_read` 로 이달 5,545만 행을
 * 세고 있었다 — 응답에 있다. 웹 몫이 빠져 월 5억 한도의 게이지가 배치만 보는 눈이었다.
 * 배치와 같은 행(`turso_reads`, **월** 창 `YYYY-MM`)에 더한다. 쓰기는 세지 않는다(웹 쓰기는 작고, 월 쓰기 한도는
 * 배치 몫이 대부분이다 `[확인필요: 웹 쓰기 몫]`).
 */

import type { SqlValue } from "@/lib/db";
import { USAGE_ADD } from "@/lib/apiUsage";
import { usageRatio, usageTone } from "@/lib/limits";

/** 배치와 **글자까지 같아야 한다**. 정의처는 `batch/core/db.API_NAMES` (tests/test_api_usage_names.py) */
export const D1_WRITES_NAME = "d1_writes";
export const D1_READS_NAME = "d1_reads";

/** 배치의 `db.D1_DAILY_WRITE_LIMIT` · `D1_DAILY_READ_LIMIT` 과 같은 값이어야 한다 */
export const D1_DAILY_WRITE_LIMIT = 100_000;
export const D1_DAILY_READ_LIMIT = 5_000_000;

/** 배치의 `db.API_NAMES` 와 같은 이름, `db.TURSO_MONTHLY_READ_LIMIT` 과 같은 값 (25.891) */
export const TURSO_READS_NAME = "turso_reads";
export const TURSO_MONTHLY_READ_LIMIT = 500_000_000;

/** 쓰기는 매번 적는다 — 드물고 작고, 무엇보다 **정확해야** 한다 */
export const WRITE_FLUSH_ROWS = 1;
/** 읽기는 모았다가 적는다. 화면 한 번이 수천 행이라 매번 적으면 쓰기가 따라 는다 */
export const READ_FLUSH_ROWS = 10_000;

/** D1 한도는 자정 UTC 에 풀린다. 한국 날짜가 아니다 (batch/core/db._utc_today 와 같다) */
export function utcToday(now: Date): string {
  return now.toISOString().slice(0, 10);
}

/** 아직 적지 않은 몫. 모듈 전역이라 **한 인스턴스가 사는 동안**만 이어진다 */
const pending = { writes: 0, reads: 0 };

/** 적는 중에 일어난 쓰기를 또 세러 들어오지 않게 한다 (batch 쪽 `_recording` 과 같은 뜻) */
let recording = false;

/** 테스트와 인스턴스 경계용. 지금 모아 둔 몫 */
export function peekPending(): { writes: number; reads: number } {
  return { ...pending };
}

export function resetPending(): void {
  pending.writes = 0;
  pending.reads = 0;
}

/**
 * 문턱을 넘었나. 넘었으면 적을 몫을 돌려주고 모아 둔 것을 비운다.
 *
 * **`wroteNow` 가 필요한 이유** (2026-09-22, docs/infra.md 25.133).
 * 카운터를 적는 그 문장도 행을 쓰고, 그 몫은 다음 번으로 넘어온다(`noteD1Usage`).
 * 그래서 첫 쓰기 뒤에는 `pending.writes` 가 **영영 0 이 되지 않는다.** 모아 둔 수만
 * 보고 판단하면 그 뒤로는 **읽기만 하는 질의까지 매번 카운터 왕복을 하나 더 만든다** —
 * 화면 한 번에 왕복이 두 배가 된다.
 *
 * 그래서 쓰기 쪽은 "이번에 실제로 썼나" 로 문을 연다. 넘어온 몫은 버리지 않고
 * **다음 쓰기에 얹혀** 함께 적힌다. 읽기 쪽은 양이 크므로 그대로 문턱으로 본다.
 */
export function takeDue(wroteNow: boolean): { writes: number; reads: number } | null {
  const 쓰기차례 = wroteNow && pending.writes >= WRITE_FLUSH_ROWS;
  if (!쓰기차례 && pending.reads < READ_FLUSH_ROWS) return null;
  const due = { ...pending };
  resetPending();
  return due;
}

/**
 * 적을 문장들. **무엇을 적는지 따로 볼 수 있게 순수 함수로 뺐다** — 경로가 무엇을 쓰는지
 * 훑는 검사(`__tests__/intraday.test.ts`)가 이 파일을 읽어도 놀라지 않도록 한 곳에 모은다.
 */
export function usageStatements(
  due: { writes: number; reads: number },
  now: Date,
): Array<{ sql: string; args: SqlValue[] }> {
  const day = utcToday(now);
  const stamp = now.toISOString();
  const out: Array<{ sql: string; args: SqlValue[] }> = [];
  const 한줄 = (name: string, rows: number, limit: number) => {
    // 상태는 **더한 뒤의 수**로 판정해야 하는데 그 수는 아직 모른다. 모아 둔 몫만으로
    // 판정하면 늘 `ok` 가 된다 — 그래서 `unknown` 을 적고, 실제 판정은 배치가 같은 행을
    // 갱신할 때와 `/status` 가 게이지를 그릴 때 한다 (usageTone 이 같은 식을 쓴다)
    const tone = usageTone(usageRatio(rows, limit), 80);
    out.push({
      sql: USAGE_ADD,
      args: [name, day, rows, limit, 80, tone === "ok" ? "unknown" : tone, stamp, stamp],
    });
  };
  if (due.writes > 0) 한줄(D1_WRITES_NAME, due.writes, D1_DAILY_WRITE_LIMIT);
  if (due.reads > 0) 한줄(D1_READS_NAME, due.reads, D1_DAILY_READ_LIMIT);
  return out;
}

/**
 * 한 번의 D1 왕복이 쓰고 훑은 행을 더하고, 문턱을 넘으면 적는다.
 *
 * `send` 는 문장을 실제로 보내는 함수다(`d1Batch`). **적는 동안 다시 들어오면 아무것도 하지
 * 않는다** — 카운터를 적는 그 쓰기를 또 세면 끝이 없다. 대신 그 몫은 다음 번에 더해진다.
 *
 * 카운터가 본 작업을 막으면 안 된다. 무슨 일이 있어도 예외를 올리지 않는다.
 */
export async function noteD1Usage(
  written: number,
  read: number,
  now: Date,
  send: (statements: Array<{ sql: string; args: SqlValue[] }>) => Promise<unknown>,
): Promise<void> {
  if (recording) {
    // 카운터를 적는 왕복이다. 그 왕복이 쓴 행도 예산을 쓰므로 **버리지 않고 모아 둔다**
    pending.writes += Math.max(0, written);
    pending.reads += Math.max(0, read);
    return;
  }
  pending.writes += Math.max(0, written);
  pending.reads += Math.max(0, read);

  const due = takeDue(written > 0);
  if (!due) return;

  recording = true;
  try {
    await send(usageStatements(due, now));
  } catch {
    // 못 적었으면 **되돌려 놓는다.** 그냥 버리면 카운터가 실제보다 적게 보인다
    pending.writes += due.writes;
    pending.reads += due.reads;
  } finally {
    recording = false;
  }
}

/** Turso 월 창. 배치 `_record_rows` 의 `datetime.now(UTC).strftime("%Y-%m")` 과 같다 */
export function utcMonth(now: Date): string {
  return now.toISOString().slice(0, 7);
}

/** `USAGE_ADD` 와 같고 창만 **월**이다 — Turso 한도는 한 달 단위다 */
export const USAGE_ADD_MONTH = USAGE_ADD.replace("VALUES (?, 'day',", "VALUES (?, 'month',");

const tursoPending = { reads: 0 };
let tursoRecording = false;

// ----------------------------------------------------------------------
// 무거운 읽기 기록 (docs/health.md 7.2, docs/infra.md 25.956)
// ----------------------------------------------------------------------

/** 한 번의 Turso 왕복이 이만큼 넘게 훑으면 기록한다. 시세 표 전체(약 700만 행)의 3% — 정상 화면 질의는 수천~수만 행이다 */
export const HEAVY_READ_ROWS = 200_000;
/** 기록 행의 job. `health_alerts` 는 무응답 알림 표지만 (job, market, local_date, kind) 유니크라 하루·질의 모양마다 한 행이 쌓인다 */
export const HEAVY_READ_JOB = "web_heavy_read";

/**
 * 같은 날 같은 질의 모양은 한 행에 **횟수와 행 수를 더한다**. `sent_at` 을 처음부터 채워 텔레그램 무응답 알림(`PENDING_HEALTH_ALERTS`,
 * sent_at IS NULL)으로는 나가지 않는다 — 운영자가 /status 와 DB 에서 읽는 기록이다
 */
export const HEAVY_READ_UPSERT = `INSERT INTO health_alerts (job, market, local_date, kind, message, data, created_at, sent_at)
VALUES (?, 'ALL', ?, ?, ?, ?, ?, ?)
ON CONFLICT (job, market, local_date, kind) DO UPDATE SET
  message = excluded.message,
  data = json_set(health_alerts.data,
    '$.count', COALESCE(json_extract(health_alerts.data, '$.count'), 1) + 1,
    '$.rows', COALESCE(json_extract(health_alerts.data, '$.rows'), 0) + json_extract(excluded.data, '$.rows'),
    '$.last_route', json_extract(excluded.data, '$.route'))`;

/** 질의 모양 — 공백을 하나로 모으고 앞 60자. 같은 경로의 같은 질의가 날짜·인자만 달라도 한 모양이다 */
export function sqlShape(sql: string): string {
  return sql.replace(/\s+/g, " ").trim().slice(0, 60);
}

/** 호출 스택에서 **경로(app/…)** 를 먼저, 없으면 DB 층이 아닌 lib/ 파일 — 어느 경로가 읽었나. 못 찾으면 "?" */
export function routeFromStack(stack: string | undefined): string {
  if (!stack) return "?";
  const all = [...stack.matchAll(/\/((?:app|lib)\/[^\s:)]+)\.(?:ts|tsx|js|mjs)/g)].map((m) => m[1]);
  const app = all.find((f) => f.startsWith("app/"));
  if (app) return app;
  const lib = all.find((f) => f.startsWith("lib/") && f !== "lib/db" && f !== "lib/webUsage");
  return lib ?? all[0] ?? "?";
}

/**
 * 기록할 문장과 인자. 문턱 아래면 null. 인자가 아니라 **질의 모양·행 수·경로**만 남긴다 — 종목 번호·날짜 같은 값은 기록하지 않는다
 */
export function heavyReadRecord(
  statements: Array<{ sql: string }>,
  read: number,
  now: Date,
  stack: string | undefined,
): { sql: string; args: SqlValue[] } | null {
  if (!(read >= HEAVY_READ_ROWS) || statements.length === 0) return null;
  const shape = sqlShape(statements[0].sql) + (statements.length > 1 ? ` (+${statements.length - 1})` : "");
  const route = routeFromStack(stack);
  const stamp = now.toISOString();
  const message = `무거운 읽기 ${read.toLocaleString("en-US")}행 — ${route} · ${shape}`;
  const data = JSON.stringify({ rows: read, count: 1, route, sql: statements[0].sql.replace(/\s+/g, " ").trim().slice(0, 300) });
  return { sql: HEAVY_READ_UPSERT, args: [HEAVY_READ_JOB, stamp.slice(0, 10), shape, message, data, stamp, stamp] };
}

let heavyRecording = false;

/** 무거운 왕복을 `health_alerts` 에 남긴다. 기록이 본 질의를 막지 않는다 — 실패는 삼키고, 기록 중의 기록은 하지 않는다 */
export async function noteHeavyRead(
  statements: Array<{ sql: string }>,
  read: number,
  now: Date,
  send: (statements: Array<{ sql: string; args: SqlValue[] }>) => Promise<unknown>,
): Promise<void> {
  if (heavyRecording) return;
  const rec = heavyReadRecord(statements, read, now, new Error().stack);
  if (!rec) return;
  heavyRecording = true;
  try {
    await send([rec]);
  } catch {
    // 기록 실패는 조용히 — 본 질의 결과는 이미 손에 있다
  } finally {
    heavyRecording = false;
  }
}

export function peekTursoPending(): number {
  return tursoPending.reads;
}

export function resetTursoPending(): void {
  tursoPending.reads = 0;
}

/**
 * 한 번의 Turso 왕복이 훑은 행을 더하고, `READ_FLUSH_ROWS` 를 넘으면 `turso_reads`(월)에 적는다 (25.891).
 * D1 쪽 `noteD1Usage` 와 같은 규칙 — 적는 왕복은 다시 세러 들어오지 않고, 못 적으면 되돌려 놓고, 예외를 올리지 않는다.
 */
export async function noteTursoReads(
  read: number,
  now: Date,
  send: (statements: Array<{ sql: string; args: SqlValue[] }>) => Promise<unknown>,
): Promise<void> {
  tursoPending.reads += Math.max(0, read);
  if (tursoRecording || tursoPending.reads < READ_FLUSH_ROWS) return;
  const due = tursoPending.reads;
  tursoPending.reads = 0;
  tursoRecording = true;
  const stamp = now.toISOString();
  try {
    await send([
      {
        sql: USAGE_ADD_MONTH,
        args: [TURSO_READS_NAME, utcMonth(now), due, TURSO_MONTHLY_READ_LIMIT, 80, "unknown", stamp, stamp],
      },
    ]);
  } catch {
    tursoPending.reads += due;
  } finally {
    tursoRecording = false;
  }
}
