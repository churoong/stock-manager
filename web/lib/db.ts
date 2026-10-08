/**
 * Turso 접속.
 *
 * 파이썬 쪽 batch/core/turso.py 와 같은 HTTP 경로(/v2/pipeline)를 쓴다.
 * 공식 클라이언트 라이브러리는 401을 돌려주므로 쓰지 않는다.
 *
 * 값에 타입이 붙어 오간다. 정수도 문자열로 실려 오므로 되돌려야 한다.
 * 이 변환이 어긋나면 모든 수치가 조용히 틀어진다.
 */

type Cell =
  | { type: "null" }
  | { type: "integer"; value: string }
  | { type: "float"; value: number }
  | { type: "text"; value: string }
  | { type: "blob"; base64: string };

export type SqlValue = string | number | boolean | null;

function host(databaseUrl: string): string {
  let h = databaseUrl.trim();
  for (const prefix of ["libsql://", "https://", "http://", "wss://", "ws://"]) {
    if (h.startsWith(prefix)) {
      h = h.slice(prefix.length);
      break;
    }
  }
  return h.split("/")[0];
}

function encode(value: SqlValue): Cell {
  if (value === null || value === undefined) return { type: "null" };
  if (typeof value === "boolean") return { type: "integer", value: String(value ? 1 : 0) };
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? { type: "integer", value: String(value) }
      : { type: "float", value };
  }
  return { type: "text", value: String(value) };
}

function decode(cell: Cell): unknown {
  switch (cell.type) {
    case "null":
      return null;
    case "integer":
      return Number(cell.value);
    case "float":
      return cell.value;
    case "blob":
      return cell.base64;
    default:
      return cell.value;
  }
}

export interface ResultSet {
  columns: string[];
  rows: unknown[][];
  affectedRows: number;
}

export function rowsToObjects<T = Record<string, unknown>>(rs: ResultSet): T[] {
  return rs.rows.map((row) => {
    const obj: Record<string, unknown> = {};
    rs.columns.forEach((name, i) => {
      obj[name] = row[i];
    });
    return obj as T;
  });
}

function endpoint(): string {
  const url = process.env.TURSO_DATABASE_URL;
  if (!url) throw new Error("환경변수 TURSO_DATABASE_URL 이 비어 있습니다");
  return `https://${host(url)}/v2/pipeline`;
}

export async function execute(sql: string, args: SqlValue[] = []): Promise<ResultSet> {
  const results = await batch([{ sql, args }]);
  return results[0];
}

/**
 * 어느 DB 를 쓰는가 (docs/infra.md 25절).
 *
 * Turso 가 월 한도로 막힌 동안 Cloudflare D1 로 옮겨 운영한다. 둘 다 SQLite 라 질의는 같다.
 * 환경변수 하나로 오간다 — 되돌리는 날 코드를 고치지 않으려고 이렇게 뒀다.
 */
export function dbBackend(): "turso" | "d1" | "auto" {
  const value = (process.env.DB_BACKEND ?? "turso").trim().toLowerCase();
  return value === "d1" ? "d1" : value === "auto" ? "auto" : "turso";
}

/** 복귀를 마쳤다는 표시. D1 의 settings 에 있다 (batch/core/client.RETURN_MARKER 와 같은 이름) */
export const RETURN_MARKER = "db_return_done_at";
/** auto 판정을 이만큼 재사용한다. 요청마다 D1 을 찔러 보면 느려진다 */
const RESOLVE_TTL_MS = 60_000;
/** D1 을 못 읽어 Turso 상태로 정했을 때는 짧게만 믿는다. 잠깐의 D1 장애로 1분 동안 다른 DB 를 쓰면 안 된다 */
const FALLBACK_TTL_MS = 5_000;
let resolved: { value: "turso" | "d1"; at: number; ttl: number } | null = null;

/**
 * auto 모드의 판정 (docs/infra.md 25.12). batch/core/client.decide 와 **같은 표**다.
 *
 * **복귀 표시가 판정의 중심이다.** Turso 가 살아났다는 것만으로는 돌아가지 않는다 — 리셋 직후의
 * Turso 에는 막혀 있던 동안 생긴 표(0028~)도, D1 에서 입력한 매매도 없다. 복귀 스크립트가
 * 그것들을 옮기고 표시를 남긴 뒤에야 Turso 로 간다.
 *   복귀 표시 있음 → Turso (다시 막혀도 D1 로 튀지 않는다) · 없음 → D1
 *   D1 을 못 읽어 모름(null) → 살아 있으면 Turso · 한도로 막혔으면 D1 · 다른 장애는 Turso
 */
export function decideBackend(
  tursoOk: boolean,
  quotaBlocked: boolean,
  returned: boolean | null,
): "turso" | "d1" {
  if (returned === true) return "turso";
  if (returned === false) return "d1";
  if (tursoOk) return "turso";
  return quotaBlocked ? "d1" : "turso";
}

/** 복귀 표시가 있는가. D1 을 읽지 못하면 null (모른다) */
export async function readReturnMarker(): Promise<boolean | null> {
  try {
    const { d1Batch } = await import("@/lib/d1");
    const rs = await d1Batch([{ sql: "SELECT value FROM settings WHERE key = ?", args: [RETURN_MARKER] }]);
    return (rs[0]?.rows.length ?? 0) > 0;
  } catch {
    return null;
  }
}

/**
 * auto 일 때 Turso 가 한도로 막히면 D1 으로 넘어가는가. **넘어가지 않는다** — 2026-10-08 사용자 결정 "D1으로 넘어가지말자"
 * (docs/infra.md 25.1030). 배치 `batch/core/client.AUTO_FALLS_BACK_TO_D1` 과 같은 값. D1 은 `DB_BACKEND=d1` 일 때만
 */
export const AUTO_FALLS_BACK_TO_D1 = false;

async function resolveBackend(): Promise<"turso" | "d1"> {
  const mode = dbBackend();
  if (mode !== "auto") return mode;
  if (!AUTO_FALLS_BACK_TO_D1) return "turso";
  if (resolved && Date.now() - resolved.at < resolved.ttl) return resolved.value;

  const returned = await readReturnMarker();
  // D1 을 못 읽을 때만 Turso 를 본다
  const { ok, blocked } = returned === null ? await probeTurso() : { ok: false, blocked: false };
  const value = decideBackend(ok, blocked, returned);
  resolved = { value, at: Date.now(), ttl: returned === null ? FALLBACK_TTL_MS : RESOLVE_TTL_MS };
  return value;
}

/**
 * Turso 가 살아 있는가. 표를 읽지 않는 `SELECT 1` 한 번이라 예산을 쓰지 않는다.
 * blocked 는 **한도로** 막혔다는 뜻이다(연결 끊김 같은 다른 장애와 구별한다).
 */
export async function probeTurso(): Promise<{ ok: boolean; blocked: boolean }> {
  try {
    await tursoBatch([{ sql: "SELECT 1" }]);
    return { ok: true, blocked: false };
  } catch (error) {
    return { ok: false, blocked: quotaReason(error instanceof Error ? error.message : String(error)) !== null };
  }
}

/** 지금 실제로 쓰는 DB. auto 면 판정 결과다 (무응답 감시가 D1 임시 운영 중인지 본다) */
export async function currentBackend(): Promise<"turso" | "d1"> {
  return resolveBackend();
}

export async function batch(
  statements: Array<{ sql: string; args?: SqlValue[] }>,
): Promise<ResultSet[]> {
  if ((await resolveBackend()) === "d1") {
    const { d1Batch } = await import("@/lib/d1");
    return d1Batch(statements);
  }
  return tursoBatch(statements);
}

async function tursoBatch(
  statements: Array<{ sql: string; args?: SqlValue[] }>,
): Promise<ResultSet[]> {
  const token = process.env.TURSO_AUTH_TOKEN;
  if (!token) throw new Error("환경변수 TURSO_AUTH_TOKEN 이 비어 있습니다");

  const body = {
    requests: [
      ...statements.map((s) => ({
        type: "execute" as const,
        stmt: { sql: s.sql, args: (s.args ?? []).map(encode) },
      })),
      { type: "close" as const },
    ],
  };

  const response = await fetch(endpoint(), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (response.status === 401) {
    throw new Error(
      "Turso 인증 실패(401). TURSO_AUTH_TOKEN 이 잘리지 않았는지 확인하세요",
    );
  }
  if (!response.ok) {
    throw new Error(`Turso HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    results?: Array<
      | { type: "ok"; response: { type: string; result?: RawResult } }
      | { type: "error"; error?: { message?: string } }
    >;
  };

  const out: ResultSet[] = [];
  let read = 0;
  for (const item of payload.results ?? []) {
    if (item.type === "error") {
      throw new Error(explain(item.error?.message ?? "알 수 없는 오류"));
    }
    if (item.response.type !== "execute" || !item.response.result) continue;
    const raw = item.response.result;
    read += Number(raw.rows_read ?? raw.stat?.rows_read ?? 0) || 0;
    out.push(toResultSet(raw));
  }
  // 웹의 Turso 읽기도 월 카운터에 더한다 (docs/infra.md 25.891). 카운터가 본 작업을 막지 않는다
  if (read > 0) {
    const { noteTursoReads, noteHeavyRead } = await import("@/lib/webUsage");
    await noteTursoReads(read, new Date(), (sts) => tursoBatch(sts)).catch(() => undefined);
    // 어느 경로의 어떤 질의가 많이 훑었는지 (docs/health.md 7.2, 25.956) — 월 카운터는 합만 알고 출처를 모른다
    await noteHeavyRead(statements, read, new Date(), (sts) => tursoBatch(sts));
  }
  return out;
}

/**
 * DB 오류를 사람이 읽고 **무엇을 해야 할지 아는 문장**으로 바꾼다.
 *
 * 2026-09-17 에 Turso 무료 월 한도를 넘겨 읽기·쓰기가 모두 막혔다(docs/infra.md 23절).
 * 그때 화면에 뜬 것은 영어 원문이었다. 원인을 알 수 없으면 사용자는 앱이 고장 난 줄 안다.
 * 원문도 함께 남긴다 — 다음에 다른 이유로 막혔을 때 구별해야 한다.
 */
/**
 * DB 한도에 걸린 오류인가 (docs/infra.md 25.6). 한도는 고장이 아니라 기다리면 풀리는 상태다.
 *
 * 1분·5분마다 부르는 크론 경로가 이것을 500 으로 돌려주면 cron-job.org 가 실패를 쌓다가 작업을
 * 꺼 버릴 수 있다 [확인필요: 몇 번이면 끄는지]. 그래서 크론은 한도면 200 + skipped 로 답한다.
 */
export function quotaReason(message: string): string | null {
  const text = message.toLowerCase();
  // 읽기 한도를 먼저 가린다 (docs/infra.md 25.863) — 예전에는 읽기 한도 초과도 "쓰기 한도" 로 안내했다. 배치 db.quota_reason 과 같은 규칙
  if (text.includes("row read limit")) {
    return "Cloudflare D1 하루 읽기 한도(500만 행)에 걸렸습니다. 매일 09:00 KST 에 풀립니다";
  }
  if (text.includes("daily row write limit") || (text.includes("free tier") && text.includes("limit"))) {
    return "Cloudflare D1 하루 쓰기 한도(10만 행)에 걸렸습니다. 매일 09:00 KST 에 풀립니다";
  }
  if (text.includes("operations are forbidden") || (text.includes("blocked") && text.includes("upgrade your plan"))) {
    return "Turso 월 한도에 걸렸습니다. 다음 결제 주기에 풀립니다";
  }
  return null;
}

/**
 * **표가 아직 없을 때만 삼킨다** (docs/infra.md 25.163).
 *
 * `.catch(() => [])` 는 "마이그레이션 전 DB 에서는 이 표가 없다" 를 넘기려고 붙였는데,
 * **아무 실패나 다 삼킨다.** 한도에 걸려도, 인증이 끊겨도 빈 목록이 되고 화면은
 * "아직 없습니다" 라고 적는다 — 25.74 에서 배운 것: **못 읽은 것을 없는 것으로 말하면
 * 사람이 엉뚱한 데를 고치러 간다.**
 *
 * 표가 없는 것이면 `fallback` 을 주고, 아니면 **다시 던진다.** 바깥 try/catch 가
 * `explain()` 으로 이유를 말한다.
 */
export function ifMissingTable<T>(fallback: T): (error: unknown) => T {
  return (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    if (/no such table/i.test(message)) return fallback;
    throw error;
  };
}

/**
 * "표 없음" 일 때만 대신할 질의로 넘어간다 — 다른 오류(한도·시간 초과)는 그대로 올린다 (docs/infra.md 25.667, 감사).
 * `.catch(() => 다른질의)` 는 모든 실패를 삼켜, 한 번의 일시 실패가 **기준일을 바꿔** 어제 신호를 오늘 것처럼 보였다(25.163 모양).
 */
export function ifMissingTableThen<T>(fallback: () => Promise<T>): (error: unknown) => Promise<T> {
  return async (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    if (/no such table/i.test(message)) return fallback();
    throw error;
  };
}

export function explain(message: string): string {
  const quota = quotaReason(message);
  if (quota && !/operations are forbidden/i.test(message)) return `${quota}. 원문: ${message}`;
  const blocked = /operations are forbidden|blocked/i.test(message);
  if (blocked && /read/i.test(message)) {
    return `데이터베이스 월 읽기 한도에 걸렸습니다. 다음 결제 주기에 풀립니다 (docs/infra.md 23절). 원문: ${message}`;
  }
  if (blocked && /write/i.test(message)) {
    return `데이터베이스 월 쓰기 한도에 걸렸습니다. 저장은 안 되고 조회만 됩니다 (docs/infra.md 23절). 원문: ${message}`;
  }
  if (/401|unauthorized|token/i.test(message)) {
    return `데이터베이스 인증에 실패했습니다. TURSO_AUTH_TOKEN 이 잘리지 않았는지 확인하세요. 원문: ${message}`;
  }
  return `SQL 실패: ${message}`;
}

interface RawResult {
  cols: Array<{ name?: string }>;
  rows: Cell[][];
  affected_row_count?: number;
  /** 서버가 훑은 행 수 — 월 읽기 한도가 세는 값 (배치 `turso._to_result_set` 과 같은 자리, 25.891) */
  rows_read?: number;
  stat?: { rows_read?: number };
}

function toResultSet(raw: RawResult): ResultSet {
  return {
    columns: raw.cols.map((c) => c.name ?? ""),
    rows: raw.rows.map((row) => row.map(decode)),
    affectedRows: raw.affected_row_count ?? 0,
  };
}

