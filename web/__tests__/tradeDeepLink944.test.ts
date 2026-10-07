/** 아침 리포트의 "매매 입력" 링크로 오면 종목 상세가 매매 폼을 펼친 채 연다 (docs/infra.md 25.944). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const 읽기 = (p: string) => readFileSync(join(process.cwd(), p), "utf-8");

describe("매매 딥링크", () => {
  it("페이지가 ?trade·?horizon 을 읽어 허용값만 넘긴다", () => {
    const 페이지 = 읽기("app/stocks/[id]/page.tsx");
    expect(페이지).toContain('q.trade === "buy" || q.trade === "sell" ? q.trade : undefined');
    expect(페이지).toContain('q.horizon === "short" || q.horizon === "mid" || q.horizon === "long" ? q.horizon : undefined');
    expect(페이지).toContain("<StockDetail id={id} trade={trade} horizon={horizon} />");
  });

  it("종목 상세가 폼을 펼치고 기간·매수매도를 넘긴다", () => {
    const 상세 = 읽기("components/StockDetail.tsx");
    expect(상세).toContain("initialOpen={trade !== undefined}");
    expect(상세).toContain("initialSide={trade}");
    expect(상세).toContain("useState<string | null>(horizonParam ?? null)");
    const 빠른 = 읽기("components/QuickTrade.tsx");
    expect(빠른).toContain("useState(initialOpen)");
    expect(빠른).toContain("defaultSide={initialSide}");
    expect(읽기("components/PortfolioView.tsx")).toContain('useState<"buy" | "sell">(defaultSide)');
  });

  it("배치가 만드는 주소 모양과 같다", () => {
    const 배치 = readFileSync(join(process.cwd(), "..", "batch", "notify", "report_sections.py"), "utf-8");
    expect(배치).toContain('/stocks/{pick.stock_id}?trade=buy&horizon={pick.horizon}');
  });
});
