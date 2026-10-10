import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { tradeEditErrors, tradeEditSchema } from "@/lib/portfolio";

/**
 * **메모·수수료·세금은 제자리에서 고친다** (docs/infra.md 25.1116, 웹 매매 감사 재현).
 *
 * 고치기가 "지우고 다시 넣기" 뿐이라 다시 넣은 매수가 새 id 를 받았고, 배치가 같은 날 매수를 id 순으로 선입선출해
 * 메모 하나만 고쳐도 같은 날 70,000·80,000 매수 뒤 매도의 실현손익이 +5만 → −5만이 됐다.
 */
const 매수 = { stock_id: 1, side: "buy", trade_date: "2026-10-08", price: 70000, quantity: 10, fee: 100, tax: null, memo: null };
const 매도 = { ...매수, side: "sell", price: 75000 };

describe("고칠 수 있는 칸과 규칙", () => {
  it("메모·수수료·세금만 받는다 — 체결가·날짜는 지우고 다시 넣는다", () => {
    expect(tradeEditSchema.safeParse({ memo: "분할 1차", fee: 150 }).success).toBe(true);
    expect(tradeEditSchema.safeParse({ price: 1 }).success).toBe(false);
    expect(tradeEditSchema.safeParse({ trade_date: "2026-10-09" }).success).toBe(false);
  });

  it("넣을 때와 같은 규칙으로 다시 본다", () => {
    expect(tradeEditErrors(매수, { memo: "x" })).toEqual([]);
    expect(tradeEditErrors(매수, { tax: 10 }).join()).toContain("매수에는 세금을 적지 않습니다");
    expect(tradeEditErrors(매수, { fee: 800_000 }).join()).toContain("체결금액");
    expect(tradeEditErrors(매도, { fee: 400_000, tax: 400_000 }).join()).toContain("합이 체결금액보다 큽니다");
  });

  it("경로는 그 세 칸만 같은 id 에 쓰고, 화면에 [고치기] 가 있다", () => {
    const 경로 = readFileSync(join(process.cwd(), "app", "api", "trades", "[id]", "route.ts"), "utf-8");
    expect(경로).toContain("export async function PATCH");
    expect(경로).toContain("UPDATE trades SET memo = ?, fee = ?, tax = ?, updated_at = ? WHERE id = ?");
    const 화면 = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");
    expect(화면).toContain('method: "PATCH"');
  });
});
