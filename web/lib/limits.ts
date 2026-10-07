/**
 * 무료 한도 판정 (CLAUDE.md 비용 규칙: **80% 경고 · 100% 중단**).
 *
 * **왜 별도 파일인가.** 이 규칙은 원래 `lib/health.ts` 안에 있었는데, 한도를 *쓰는* 쪽
 * (`lib/apiUsage.ts` → 장중 경로)이 생기면서 그 파일을 가져와야 했다. 그런데 `health.ts`
 * 에는 무응답 알림을 저장하는 문장이 들어 있어서, **장중 경로가 무엇을 쓰는지 훑는 검사**
 * (`__tests__/intraday.test.ts` 「쓰기 문장의 대상 표」)가 그 문장까지 세게 된다.
 * 규칙을 두 벌로 베끼는 대신 규칙만 따로 뗐다 — 25.0 의 「한 규칙이 두 곳에 있다」.
 *
 * (그 검사는 글자를 훑으므로 **주석에 적은 SQL 도 센다.** 여기 SQL 을 적지 않는 이유다.)
 *
 * 파이썬 쪽 짝은 `batch/core/db.evaluate_limit_state` 다. 같은 식이어야 한다.
 */

/** 한도 대비 사용률. 한도를 모르면 null 이고 화면은 "한도 모름" 을 찍는다 */
export function usageRatio(callCount: number, limitValue: number | null): number | null {
  if (limitValue === null || limitValue <= 0) return null;
  return callCount / limitValue;
}

/** 80% 경고·100% 차단은 CLAUDE.md 비용 규칙의 문턱이다. **모르는 것을 ok 라고 부르지 않는다** */
export function usageTone(ratio: number | null, warnAtPct: number): "ok" | "warn" | "blocked" | "unknown" {
  if (ratio === null) return "unknown";
  if (ratio >= 1) return "blocked";
  if (ratio * 100 >= warnAtPct) return "warn";
  return "ok";
}

/**
 * 게이지 한 줄의 이름과 단위.
 *
 * `api_usage` 에는 **뜻이 다른 세 가지**가 섞여 있다 (docs/infra.md 25.123).
 *   바깥 API 호출 횟수 (`dart_opendart` …)
 *   우리가 쓰거나 훑은 행 수 (`d1_writes` `d1_reads` `turso_*`)
 *   **지금 얼마나 찼나** — 바이트 (`d1_db_size`)
 *
 * 마지막 것을 그대로 찍으면 `524,288,000 / 524,288,000` 이다. 아무도 못 읽는다.
 * 표 이름도 마찬가지다 — `d1_db_size` 는 이 화면을 여는 이유를 말해 주지 않는다.
 */
export const USAGE_LABEL: Record<string, string> = {
  dart_opendart: "DART OpenAPI (일)",
  krx_openapi: "한국거래소 Open API (일)",
  sec_edgar: "SEC EDGAR",
  nasdaqtrader_symdir: "나스닥 심볼 목록",
  yfinance: "yfinance",
  yahoo_quotesummary: "야후 펀드 프로필",
  turso_writes: "Turso 쓴 행 (월)",
  turso_reads: "Turso 훑은 행 (월)",
  d1_writes: "D1 쓴 행 (오늘)",
  d1_reads: "D1 훑은 행 (오늘)",
  d1_db_size: "D1 DB 용량 (리셋 없음)",
};

/** 바이트로 세는 자리. 여기에 있는 이름은 MB 로 찍는다 */
const BYTE_GAUGES = new Set(["d1_db_size"]);

/** 게이지에 붙일 이름. 모르는 이름은 **그대로 보여 준다** — 새 카운터가 조용히 사라지면 안 된다 */
export function usageLabel(apiName: string): string {
  return USAGE_LABEL[apiName] ?? apiName;
}

/** 게이지의 숫자 한 조각. 바이트면 MB, 아니면 천 단위 구분 */
export function usageAmount(apiName: string, value: number | null): string {
  if (value === null) return "한도 모름";
  if (!BYTE_GAUGES.has(apiName)) return value.toLocaleString();
  return `${(value / 1_048_576).toFixed(0)}MB`;
}
