/** 종목 상세 감사의 남은 하 셋 (docs/infra.md 25.811) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { overlayNote, scoreStaleNote } from "@/lib/stockDetail";

describe("종목 상세 남은 하", () => {
  it("14일 넘게 멈춘 점수는 그렇다고 말한다", () => {
    expect(scoreStaleNote("2026-07-01", "2026-10-01")).toContain("92일 전 점수입니다");
    expect(scoreStaleNote("2026-09-20", "2026-10-01")).toBeNull(); // 긴 연휴(11일)는 조용
    expect(scoreStaleNote(null, "2026-10-01")).toBeNull();
    // 경계: 14일은 조용, 15일은 경고 (25.817, 교차검증)
    expect(scoreStaleNote("2026-09-17", "2026-10-01")).toBeNull();
    expect(scoreStaleNote("2026-09-16", "2026-10-01")).toContain("15일 전");
  });

  it("신호 선에는 신호 기준일을 붙인다 — 평균 단가 선만이면 붙이지 않는다", () => {
    const 가격 = (v: unknown) => String(v);
    expect(overlayNote([{ price: 100, label: "목표", kind: "target" }], "ok", "2026-09-29", null, "KRW", 가격))
      .toBe("선 (신호 2026-09-29 기준): 목표 100");
    expect(overlayNote([{ price: 90, label: "내 평균 단가", kind: "cost" }], "ok", "2026-09-29", { quantity: 1 }, "KRW", 가격))
      .toBe("선: 내 평균 단가 90");
  });

  it("확인한 매도 플래그는 흐리게 '확인함' 으로", () => {
    const 화면 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(화면).toContain('${f.dismissed_at ? "opacity-60" : ""}');
    expect(화면).toContain('{f.dismissed_at ? " · 확인함" : ""}');
  });
});
