import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { REMARK_FOLD, remarkLong } from "@/lib/kisPaper";

/** /kis 거래내역 비고 — 긴 글은 눌러 펼친다 (docs/infra.md 25.2294) */
describe("거래내역 비고", () => {
  it("짧으면 그대로, 길면 접는다", () => {
    expect(REMARK_FOLD).toBe(24);
    expect(remarkLong(null)).toBe(false);
    expect(remarkLong(undefined)).toBe(false);
    expect(remarkLong("")).toBe(false);
    expect(remarkLong("가".repeat(24))).toBe(false);
    expect(remarkLong("가".repeat(25))).toBe(true);
    expect(
      remarkLong(
        "체결 조회에 없음 · 보낼 때: 모의 주문 응답에 KIS 오류 코드가 없습니다 — rt_cd '1'",
      ),
    ).toBe(true);
  });

  it("화면이 긴 비고를 눌러 펼치게 그린다 — 마우스 올리기(title)에만 기대지 않는다", () => {
    const 화면 = readFileSync(
      join(process.cwd(), "components", "KisPanel.tsx"),
      "utf8",
    );
    const 칸 = 화면.slice(
      화면.indexOf("{remarkLong(o.message) ? ("),
      화면.indexOf("<Pager p={p} onPage={setPage} />"),
    );
    expect(칸).toContain("<details>");
    expect(칸).toContain("<summary");
    expect(칸).toMatch(
      /whitespace-normal break-words[^>]*>\{o\.message\}<\/p>/,
    );
    // 칸 전체에 truncate 를 걸지 않는다(펼친 글까지 잘린다) — 접힌 머리에만
    expect(화면).not.toContain(
      'className="max-w-[16rem] truncate text-slate-500"',
    );
    expect(칸).toMatch(/<summary className="[^"]*truncate/);
  });
});
