import { describe, expect, it } from "vitest";
import { readUpdateStatements } from "@/lib/alerts";
import { MAX_PARAMS } from "@/lib/d1";
import { buildQuery as buildRecommend, recommendQuerySchema } from "@/lib/recommend";
import { buildQuery as buildScreener, screenerFilterSchema } from "@/lib/screener";

/**
 * **화면이 만드는 질의가 D1 한도 안에 드는가** (docs/infra.md 25.90).
 *
 * D1 은 질의 하나에 바인딩 파라미터 **100개**까지 받는다. Turso 는 수천 개를 받으므로
 * 같은 코드가 Turso 에서는 멀쩡하고 **D1 에서만 거절당한다.** 지금이 그 D1 위다.
 *
 * 그리고 지금은 이 확인이 특히 중요하다 — 추천(`signals`)이 0행이라
 * **스크리너가 종목을 고르는 유일한 길**이다(25.89). 그것이 D1 에서 막히면 볼 것이 없다.
 *
 * 돌려 보지 않고 재는 방법: 질의를 **최악의 조건**으로 만들어 인자 수를 센다.
 */

/** 모든 범위 조건을 꽉 채운 최악의 스크리너 조건 */
const 꽉_채운_조건 = screenerFilterSchema.parse({
  country: "KR",
  market: "KOSPI",
  universe_only: true,
  market_cap: { min: 1000, max: 999999 },
  avg_turnover_20d: { min: 10, max: 99999 },
  per: { min: 0, max: 30 },
  pbr: { min: 0, max: 5 },
  roe: { min: 5, max: 100 },
  debt_ratio: { min: 0, max: 200 },
  operating_margin: { min: 0, max: 100 },
  revenue_growth: { min: -50, max: 500 },
  operating_income_growth: { min: -50, max: 500 },
  metric_window: "3Y",
  cagr: { min: -50, max: 200 },
  mdd: { min: 0, max: 90 }, // 낙폭 크기 (25.770·25.773)
  sharpe: { min: -3, max: 5 },
  volatility_ann: { min: 0, max: 200 },
  sector: "전자부품·컴퓨터·통신장비",
  sentiment: { min: -100, max: 100 },
  exclude_loss_making: true,
  sort_by: "market_cap",
  sort_desc: true,
  limit: 500,
});

describe("스크리너", () => {
  it("기본 조건은 한도에 한참 못 미친다", () => {
    const { args } = buildScreener(screenerFilterSchema.parse({}));

    expect(args.length).toBeLessThan(MAX_PARAMS);
  });

  it("**모든 조건을 꽉 채워도** 한도 안이다", () => {
    const { args } = buildScreener(꽉_채운_조건);

    expect(args.length, `인자 ${args.length}개 — D1 한도 ${MAX_PARAMS}`).toBeLessThanOrEqual(MAX_PARAMS);
  });

  it("얼마나 여유가 있는지 **실측으로** 박아 둔다", () => {
    // 2026-09-21 실측: 기본 4개, 모든 조건을 꽉 채워 34개. 한도 100 의 **3분의 1** 이다.
    // 2026-09-22: 성과 지표 기준일을 나라별로 잡으면서 나라 인자가 하나 늘어 **5개**가 됐다
    // (docs/infra.md 25.112). 이 테스트가 그 변화를 알려 줬다 — 숫자를 박아 둔 값어치다.
    // 2026-09-28: 비율을 배치 값(factors.raw_json)으로 옮기며 팩터 기준일·판을 나라별로 잡아 나라 인자가 둘 늘어
    // **7개**가 됐다 (docs/infra.md 25.489).
    // 2026-09-30: 성과 지표 기준일을 기간별로 잡으며 기간 인자가 하나 늘어 **8개**가 됐다 (docs/infra.md 25.775).
    // 2026-09-30: 그 기준일의 분모를 지금 유니버스 종목 수로 잡으며 나라 인자가 하나 늘어 **9개** (docs/infra.md 25.782).
    // 조건을 더할 때 이 수를 보고 판단한다 — 한도에 가까워지면 나눠 보내야 한다.
    // 어림이 아니라 잰 값이므로 상한으로 박는다(25.0 "재어 보지 않고 적는다" 의 반대)
    expect(buildScreener(screenerFilterSchema.parse({})).args.length).toBe(9);
    expect(buildScreener(꽉_채운_조건).args.length).toBeLessThanOrEqual(40);
  });

  it("한도를 넘길 만큼 조건을 늘릴 길이 없다", () => {
    // 범위 조건은 스키마가 정한 개수만큼이고 사용자가 개수를 늘릴 수 없다.
    // `limit` 은 값이지 인자 개수가 아니다 — 500 으로 올려도 인자는 안 는다
    const 작게 = buildScreener(screenerFilterSchema.parse({ ...꽉_채운_조건, limit: 1 }));
    const 크게 = buildScreener(screenerFilterSchema.parse({ ...꽉_채운_조건, limit: 500 }));

    expect(작게.args.length).toBe(크게.args.length);
  });
});

describe("추천", () => {
  it("한도 안이다", () => {
    const { args } = buildRecommend(recommendQuerySchema.parse({}), "2026-09-16");

    expect(args.length).toBeLessThanOrEqual(MAX_PARAMS);
  });

  it("조건을 다 걸어도 한도 안이다", () => {
    const { args } = buildRecommend(
      recommendQuerySchema.parse({ country: "KR", horizon: "short", market: "KOSPI" }),
      "2026-09-16",
    );

    expect(args.length).toBeLessThanOrEqual(MAX_PARAMS);
  });
});

describe("알림 읽음 처리 — 개수가 사용자 입력이다", () => {
  it("많이 보내도 문장마다 한도 안으로 나뉜다", () => {
    const ids = Array.from({ length: 200 }, (_, i) => i + 1);

    for (const s of readUpdateStatements(ids, true)) {
      expect(s.args.length, `한 문장에 ${s.args.length}개`).toBeLessThanOrEqual(MAX_PARAMS);
    }
  });

  it("나눠도 빠뜨리지 않는다", () => {
    const ids = Array.from({ length: 200 }, (_, i) => i + 1);
    const 보낸것 = readUpdateStatements(ids, true).flatMap((s) => s.args);

    expect(new Set(보낸것).size).toBe(200);
  });

  it("하나도 안 보내면 문장 하나(전체 갱신)다", () => {
    const 문장들 = readUpdateStatements([], true);

    expect(문장들).toHaveLength(1);
    expect(문장들[0].args).toEqual([]);
  });
});

describe("바뀐 행과 쓴 행은 다른 수다 (docs/infra.md 25.152)", () => {
  /**
   * `affectedRows` 를 **0 인지**가 아니라 **몇인지**로 쓰는 곳이 넷 있다 —
   * 장중 새 알림 수, 국내·미국 뉴스에서 받은 기사 수, 알림 읽음 처리 수.
   * D1 의 `rows_written` 은 **인덱스 쓰기까지** 세므로 그 수가 부풀려진다.
   * Turso 의 `affected_row_count` 와 같은 뜻인 `meta.changes` 를 먼저 본다.
   */
  async function 한번(meta: Record<string, number>) {
    const { vi } = await import("vitest");
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ success: true, result: [{ success: true, results: [], meta }] }), { status: 200 }),
      ),
    );
    for (const [k, v] of Object.entries({ D1_ACCOUNT_ID: "a", D1_DATABASE_ID: "b", D1_API_TOKEN: "c" })) {
      vi.stubEnv(k, v);
    }
    const { d1Batch } = await import("@/lib/d1");
    const [rs] = await d1Batch([{ sql: "DELETE FROM trades WHERE id = ?", args: [1] }]);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    return rs;
  }

  it("changes 가 오면 그것을 쓴다", async () => {
    const rs = await 한번({ changes: 1, rows_written: 4 });
    expect(rs.affectedRows, "인덱스 쓰기를 행 수로 셌다").toBe(1);
  });

  it("changes 가 없으면 옛 동작 그대로다", async () => {
    // 폴백이 없으면 `0 인지` 판정까지 망가진다 — 404 가 잘못 뜬다
    const rs = await 한번({ rows_written: 4 });
    expect(rs.affectedRows).toBe(4);
  });

  it("아무것도 없으면 0", async () => {
    expect((await 한번({})).affectedRows).toBe(0);
  });

  it("0 은 0 이다 — 지울 것이 없었다", async () => {
    const rs = await 한번({ changes: 0, rows_written: 0 });
    expect(rs.affectedRows).toBe(0);
  });
});

/**
 * **문장 하나가 실패한 것을 "빈 결과" 로 넘기지 않는다** (docs/infra.md 25.186).
 *
 * `payload.success` 는 요청 전체를 말한다. 각 항목에도 `success`·`error` 가 있고
 * `D1Result` 가 처음부터 그렇게 선언하고 있었는데 **아무도 안 봤다.**
 * `toResultSet` 은 `results` 가 없으면 행 0개를 돌려주므로, 실패한 문장이
 * "데이터가 없다" 와 **똑같은 모양**으로 부르는 쪽에 도착한다.
 */
describe("문장 하나가 실패하면 말한다", () => {
  async function 응답(result: unknown) {
    const { vi } = await import("vitest");
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ success: true, result }), { status: 200 })),
    );
    for (const [k, v] of Object.entries({ D1_ACCOUNT_ID: "a", D1_DATABASE_ID: "b", D1_API_TOKEN: "c" })) {
      vi.stubEnv(k, v);
    }
    const { d1Batch } = await import("@/lib/d1");
    try {
      return { rows: await d1Batch([{ sql: "SELECT 1" }, { sql: "SELECT 2" }]), error: null as Error | null };
    } catch (e) {
      return { rows: null, error: e as Error };
    } finally {
      vi.unstubAllGlobals();
      vi.unstubAllEnvs();
    }
  }

  it("success:false 인 문장이 있으면 던진다", async () => {
    const { error } = await 응답([
      { success: true, results: [{ a: 1 }], meta: {} },
      { success: false, error: "no such column: 엉뚱", meta: {} },
    ]);

    expect(error, "조용히 빈 결과로 넘겼다").not.toBeNull();
    expect(error!.message).toContain("no such column");
    expect(error!.message, "몇 번째 문장인지 말해야 고칠 수 있다").toContain("2번째");
  });

  it("error 만 있어도 던진다", async () => {
    const { error } = await 응답([{ results: [], error: "SQLITE_BUSY", meta: {} }]);

    expect(error).not.toBeNull();
    expect(error!.message).toContain("SQLITE_BUSY");
  });

  it("멀쩡한 응답은 그대로 지난다", async () => {
    const { rows, error } = await 응답([
      { success: true, results: [{ a: 1 }], meta: {} },
      { success: true, results: [], meta: {} },
    ]);

    expect(error).toBeNull();
    expect(rows).toHaveLength(2);
    expect(rows![0].rows).toEqual([[1]]);
  });

  it("빈 결과와 실패를 구별한다", async () => {
    // **이것이 이 검사의 이유다.** 둘은 예전에 같은 모양이었다
    const 빈것 = await 응답([{ success: true, results: [], meta: {} }]);
    const 실패 = await 응답([{ success: false, error: "터짐", meta: {} }]);

    expect(빈것.error).toBeNull();
    expect(빈것.rows![0].rows).toEqual([]);
    expect(실패.error).not.toBeNull();
  });
});
