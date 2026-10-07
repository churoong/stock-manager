import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { costDefaultsNote } from "@/lib/backtest";

describe("백테스트 비용의 기본값 표시는 항목별 (docs/infra.md 25.358)", () => {
  it("기본값인 항목만 적는다", () => {
    expect(
      costDefaultsNote('{"commission_pct":0.005,"defaults":["slippage"]}'),
    ).toBe("(기본값·아직 실측 아님: 슬리피지)");
    expect(costDefaultsNote('{"defaults":[]}')).toBe("(모두 설정값)");
  });

  it("기록이 없는 옛 실행은 모른다고 한다", () => {
    expect(costDefaultsNote('{"commission_pct":0.015}')).toBe(
      "(설정값·기본값 구분 기록 없음)",
    );
  });

  it("머리말이 늘 '기본값' 을 붙이지 않는다", () => {
    const src = readFileSync("components/BacktestView.tsx", "utf8");
    expect(src).not.toContain("(기본값은 아직 실측 아님)");
    expect(src).toContain("costDefaultsNote(first.costs_json)");
  });
});
