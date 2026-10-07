/**
 * Cloudflare D1 어댑터 (docs/infra.md 25절).
 *
 * Turso 가 월 한도로 막힌 동안 같은 앱을 D1 위에서 돌린다. **D1 도 SQLite** 라 질의는 그대로다.
 * 다른 것은 전송 규격뿐이고, 그 차이를 이 파일이 흡수한다.
 *
 *   POST /accounts/{account}/d1/database/{database}/query
 *   본문 {"sql": "...", "params": [...]} 또는 {"batch": [{sql, params}, ...]}
 *   응답 {"result": [{"success": true, "results": [{열: 값}...], "meta": {rows_read, rows_written, last_row_id}}]}
 *
 * 행이 **열 이름을 가진 객체**로 온다(Turso 는 열 목록 + 값 배열). 같은 이름의 열이 둘이면
 * 하나로 합쳐지므로, 조인 질의에는 반드시 별칭을 붙인다.
 */

import type { ResultSet, SqlValue } from "@/lib/db";
import { noteD1Usage } from "@/lib/webUsage";

const BASE_URL = "https://api.cloudflare.com/client/v4";

/**
 * **질의 하나에 바인딩 파라미터 100개.** D1 의 하드 리밋이다
 * (developers.cloudflare.com/d1/platform/limits, 2026-09-18 확인, docs/infra.md 25.5).
 * 배치 쪽 단일 정의처는 `batch/core/d1.py` 의 `MAX_PARAMS` 이고,
 * `tests/test_d1_param_limit.py` 가 두 값이 같은지 본다.
 *
 * 넘으면 D1 이 "too many SQL variables" 로 거절한다 — 그 문구만으로는 **어느 질의인지
 * 알 수 없다.** 배치 쪽은 나눠 보내는 장치가 있는데(`d1.py`) 웹은 그냥 보내고 있었다.
 * 여기서 먼저 잡아 **어느 질의가 몇 개를 넣었는지** 말해 준다 (2026-09-21, docs/infra.md 25.90).
 */
export const MAX_PARAMS = 100;

function 파라미터_한도검사(statements: Array<{ sql: string; args?: SqlValue[] }>): void {
  for (const s of statements) {
    const n = (s.args ?? []).length;
    if (n > MAX_PARAMS) {
      throw new Error(
        `D1 은 질의 하나에 파라미터 ${MAX_PARAMS}개까지 받는데 ${n}개를 넣었습니다.` +
          ` 문장을 나눠 보내세요 (docs/infra.md 25.5). 질의: ${s.sql.slice(0, 120)}…`,
      );
    }
  }
}

function config() {
  const account = process.env.D1_ACCOUNT_ID;
  const database = process.env.D1_DATABASE_ID;
  const token = process.env.D1_API_TOKEN;
  const missing = [
    ["D1_ACCOUNT_ID", account],
    ["D1_DATABASE_ID", database],
    ["D1_API_TOKEN", token],
  ]
    .filter(([, v]) => !v)
    .map(([k]) => k);
  if (missing.length) throw new Error(`환경변수가 비어 있습니다: ${missing.join(", ")}`);
  return { account, database, token };
}

function encode(value: SqlValue): SqlValue {
  return typeof value === "boolean" ? (value ? 1 : 0) : value;
}

interface D1Result {
  success?: boolean;
  results?: Array<Record<string, unknown>>;
  /**
   * `changes` 는 **바뀐 행 수**, `rows_written` 은 **쓴 행 수(인덱스 포함)** 다.
   * 둘은 다른 수이고, 우리가 `affectedRows` 라고 부르는 것은 앞쪽이다 —
   * Turso 의 `affected_row_count` 와 같은 뜻이어야 한다 (docs/infra.md 25.152).
   * `[확인필요: D1 이 늘 changes 를 주는지 — 안 주면 아래 폴백이 옛 동작 그대로다]`
   */
  meta?: { rows_read?: number; rows_written?: number; changes?: number; last_row_id?: number };
  error?: string;
}

function toResultSet(item: D1Result): ResultSet {
  const rows = item.results ?? [];
  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
  return {
    columns,
    rows: rows.map((row) => columns.map((c) => row[c] ?? null)),
    // **인덱스 쓰기를 행 수로 세지 않는다** (docs/infra.md 25.152).
    // 이 수를 `0 인지` 가 아니라 **몇인지**로 쓰는 곳이 넷 있다(새 알림 수·받은 기사 수 등)
    affectedRows: item.meta?.changes ?? item.meta?.rows_written ?? 0,
  };
}

function errorText(payload: { errors?: Array<{ code?: number; message?: string }> }): string {
  const first = payload.errors?.[0];
  return first ? `${first.code ?? ""} ${first.message ?? ""}`.trim() : "알 수 없는 오류";
}

export async function d1Batch(
  statements: Array<{ sql: string; args?: SqlValue[] }>,
): Promise<ResultSet[]> {
  if (statements.length === 0) return [];
  파라미터_한도검사(statements);
  const { account, database, token } = config();
  const body =
    statements.length === 1
      ? { sql: statements[0].sql, params: (statements[0].args ?? []).map(encode) }
      : { batch: statements.map((s) => ({ sql: s.sql, params: (s.args ?? []).map(encode) })) };

  const response = await fetch(`${BASE_URL}/accounts/${account}/d1/database/${database}/query`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (response.status === 401 || response.status === 403) {
    throw new Error("D1 인증 실패. D1_API_TOKEN 의 권한(D1 편집)과 계정 ID 를 확인하세요");
  }
  const payload = (await response.json().catch(() => ({}))) as {
    success?: boolean;
    result?: D1Result[];
    errors?: Array<{ code?: number; message?: string }>;
  };
  if (!response.ok || payload.success === false) {
    const 원문 = errorText(payload);
    // 한도면 **사람 말을 앞에** 둔다 (docs/infra.md 25.877). 화면 대부분이 이 글을 그대로 보여 줘, 2026-10-02 아침 추천 화면이 영어 원문만
    // 띄웠다. 원문은 뒤에 남긴다 — `lib/db.quotaReason` 이 원문의 "row read limit" 등으로 한도를 알아본다
    const 한도 = /row read limit/i.test(원문)
      ? "DB 하루 읽기 한도(500만 행)에 걸려 매일 09:00 KST 까지 화면을 읽지 못합니다. "
      : /row write limit/i.test(원문)
        ? "DB 하루 쓰기 한도(10만 행)에 걸려 매일 09:00 KST 까지 저장하지 못합니다. "
        : "";
    throw new Error(`${한도}D1 실패(HTTP ${response.status}): ${원문}`);
  }
  const items = payload.result ?? [];

  // **문장 하나가 실패한 것을 "빈 결과" 로 넘기지 않는다** (2026-09-23, docs/infra.md 25.186).
  //
  // 위의 `payload.success` 는 **요청 전체**를 말한다. 그런데 응답의 각 항목에도
  // `success` 와 `error` 가 있다(이 파일의 `D1Result` 가 처음부터 그렇게 적고 있었다).
  // 그것을 아무도 안 봤다 — `toResultSet` 은 `results` 가 없으면 행 0개를 돌려주므로,
  // 실패한 문장이 **"데이터가 없다"** 와 똑같은 모양으로 부르는 쪽에 도착한다.
  // 화면은 "아직 없습니다" 를 그리고, 쓰기라면 `affectedRows: 0` 으로 조용히 지나간다.
  // 25.0 「잃고서 초록으로 알린다」.
  //
  // `[확인필요: D1 REST 가 문장 하나만 실패할 때 최상위 success 를 true 로 주는지.
  //  문서에서 확인하지 못했다. 다만 이 검사는 그때만 값을 하고, 아니어도 해롭지 않다]`
  const 실패한것 = items
    .map((item, i) => ({ i, item }))
    .filter(({ item }) => item.success === false || item.error);
  if (실패한것.length > 0) {
    const 말 = 실패한것
      .map(({ i, item }) => `${i + 1}번째 문장: ${item.error ?? "실패"}`)
      .join(" · ");
    throw new Error(
      `D1 문장 ${실패한것.length}개가 실패했습니다(요청 자체는 성공으로 왔습니다): ${말}`,
    );
  }

  // **쓰고 훑은 행을 센다** (docs/infra.md 25.124). 배치는 처음부터 셌는데 웹은 안 셌다 —
  // 같은 DB 의 같은 하루 예산을 쓰면서 한쪽만 보이면 남은 예산 계산이 틀린다.
  // 여기가 웹에서 D1 으로 나가는 **유일한 문**이라 여기 한 곳에서만 센다 (23절에서 배운 것)
  let written = 0;
  let read = 0;
  for (const item of items) {
    written += item.meta?.rows_written ?? 0;
    read += item.meta?.rows_read ?? 0;
  }
  await noteD1Usage(written, read, new Date(), (sts) => d1Batch(sts));

  return items.map(toResultSet);
}
