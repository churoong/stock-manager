/** 복기 목록이 300 에서 말없이 잘렸다 (docs/infra.md 25.936, 감사). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { REVIEWS, REVIEW_LIMIT } from "@/lib/review";

function 가짜(n: number) {
  vi.doMock("@/lib/db", () => ({
    execute: async (sql: string) =>
      sql === REVIEWS
        ? { columns: ["buy_trade_id"], rows: Array.from({ length: n }, (_, i) => [i]), affectedRows: 0 }
        : { columns: [], rows: [], affectedRows: 0 },
    rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
      rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]]))),
  }));
}

describe("복기 목록 잘림", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    vi.resetModules();
  });

  const 부르기 = async () => {
    const { GET } = await import("@/app/api/review/route");
    return (await (await GET()).json()) as { reviews: unknown[]; truncated: boolean };
  };

  it("질의는 한도보다 하나 더 읽는다", () => {
    expect(REVIEWS).toContain(`LIMIT ${REVIEW_LIMIT + 1}`);
  });

  it("넘치면 한도까지만 주고 잘렸다고 말한다", async () => {
    vi.resetModules();
    가짜(REVIEW_LIMIT + 1);
    const j = await 부르기();
    expect(j.reviews.length).toBe(REVIEW_LIMIT);
    expect(j.truncated).toBe(true);
  });

  it("딱 한도면 잘리지 않았다", async () => {
    vi.resetModules();
    가짜(REVIEW_LIMIT);
    expect((await 부르기()).truncated).toBe(false);
  });

  it("화면이 복기 잘림을 안내한다", () => {
    const src = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");
    expect(src).toContain("복기 목록은 최근 ${REVIEW_LIMIT}건만 보입니다");
  });
});
