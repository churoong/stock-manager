import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import { tradeInputSchema } from "@/lib/portfolio";
import { issueTexts } from "@/lib/validationErrors";

/**
 * **입력 오류는 한국어로, 어느 칸인지 말한다** (docs/infra.md 25.264).
 * 예전에는 매매 저장에서 가격·수량을 비우면 "Invalid input: expected number, received null" 이 두 번, 칸 이름 없이 떴다.
 */
describe("입력 오류 문구", () => {
  it("칸 이름을 붙이고 한국어로 말한다", () => {
    const r = tradeInputSchema.safeParse({
      stock_id: 1,
      side: "buy",
      trade_date: "2026-09-25",
      price: null,
      quantity: null,
    });
    expect(r.success).toBe(false);
    const 글 = issueTexts(r.error!.issues);
    expect(글.some((t) => t.startsWith("체결가:"))).toBe(true);
    expect(글.some((t) => t.startsWith("수량:"))).toBe(true);
    expect(글.join(" ")).not.toMatch(/Invalid input|expected/);
  });

  it("스키마에 적어 둔 한국어 문구는 그대로 쓴다", () => {
    const r = tradeInputSchema.safeParse({
      stock_id: 1,
      side: "buy",
      trade_date: "2026-09-25",
      price: -1,
      quantity: 1,
    });
    expect(issueTexts(r.error!.issues)).toEqual([
      "체결가: 체결가는 0 보다 커야 합니다",
    ]);
  });

  it("경로가 오류 문구를 날것으로 돌려주지 않는다", () => {
    const 앱 = join(process.cwd(), "app");
    const 날것 = readdirSync(앱, { recursive: true, withFileTypes: true })
      .filter((e) => e.isFile() && e.name.endsWith(".ts"))
      .map((e) => join(e.parentPath ?? (e as { path: string }).path, e.name))
      .filter((f) =>
        /issues\.map\(\(\w+\) => \w+\.message\)/.test(readFileSync(f, "utf-8")),
      )
      .map((f) => relative(앱, f).split(sep).join("/"));
    expect(
      날것,
      "issueTexts 를 써라 — 칸 이름 없는 영어 오류가 화면에 뜬다",
    ).toEqual([]);
  });
});
