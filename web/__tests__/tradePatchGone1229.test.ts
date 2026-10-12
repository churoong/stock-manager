// 거래 수정이 0행을 고쳤으면(읽은 뒤 다른 탭이 지움) 200 이 아니라 404, 다시 계산도 부르지 않는다 (docs/infra.md 25.1229)
import { beforeEach, describe, expect, it, vi } from "vitest";

let updateAffected = 0;
vi.mock("@/lib/db", async () => {
  const actual = await vi.importActual<typeof import("@/lib/db")>("@/lib/db");
  return {
    ...actual,
    execute: vi.fn(async (sql: string) => {
      if (sql.startsWith("SELECT stock_id, side, trade_date")) {
        return {
          columns: ["stock_id", "side", "trade_date", "price", "quantity", "fee", "tax", "memo"],
          rows: [[1, "buy", "2026-09-01", 70000, 10, null, null, "old"]],
          affectedRows: 0,
        };
      }
      if (sql.startsWith("UPDATE trades")) return { columns: [], rows: [], affectedRows: updateAffected };
      return { columns: [], rows: [], affectedRows: 0 };
    }),
  };
});
const recalc = vi.fn(async () => ({ dispatched: true }));
vi.mock("@/lib/portfolio", async () => {
  const actual = await vi.importActual<typeof import("@/lib/portfolio")>("@/lib/portfolio");
  return { ...actual, requestRecalc: () => recalc() };
});

async function 고침(body: object) {
  const { PATCH } = await import("@/app/api/trades/[id]/route");
  return PATCH(new Request("http://x/api/trades/5", { method: "PATCH", body: JSON.stringify(body) }), {
    params: Promise.resolve({ id: "5" }),
  });
}

describe("거래 수정 0행", () => {
  beforeEach(() => recalc.mockClear());
  it("지워진 뒤면 404, 다시 계산 없음", async () => {
    updateAffected = 0;
    const res = await 고침({ fee: 150 });
    expect(res.status).toBe(404);
    expect(recalc).not.toHaveBeenCalled();
  });
  it("평소엔 200 과 다시 계산", async () => {
    updateAffected = 1;
    const res = await 고침({ fee: 150 });
    expect(res.status).toBe(200);
    expect(recalc).toHaveBeenCalledTimes(1);
  });
});
