import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { perShareNote } from "@/lib/portfolio";
import { withTimeout } from "@/lib/telegramQa";

/** 배당 주당×수량 대조, 텔레그램 답 시간 상한 (docs/infra.md 25.1133) */
describe("주당 배당 × 수량", () => {
  it("1% 넘게 다르면 말한다", () => {
    expect(perShareNote(1, 10, 300000)).toContain("다릅니다");
    expect(perShareNote(361, 100, 36100)).toBeNull();
    expect(perShareNote(0.2425, 10, 2.43)).toBeNull();
    expect(perShareNote(null, 10, 300000)).toBeNull();
    expect(perShareNote(1, null, 300000)).toBeNull();
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "dividends", "route.ts"), "utf-8");
    expect(원문).toContain("perShareNote(input.amount_per_share, input.quantity, input.gross_amount)");
  });
});

describe("텔레그램 답 시간 상한", () => {
  it("상한 안이면 값, 넘으면 오류", async () => {
    await expect(withTimeout(Promise.resolve("답"), 50)).resolves.toBe("답");
    await expect(withTimeout(new Promise(() => {}), 20)).rejects.toThrow("답하지 않았습니다");
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "telegram", "webhook", "route.ts"), "utf-8");
    expect(원문).toContain("withTimeout(answer(query, execute), ANSWER_TIMEOUT_MS)");
  });
});
