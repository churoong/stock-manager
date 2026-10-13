// 연금 세율 두 칸은 나라 탭과 무관하게 보인다 (docs/infra.md 25.1300, 세후 적립 감사 #6)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { taxSimLines } from "@/lib/taxSim";

const src = readFileSync(join(__dirname, "../components/SettingsForm.tsx"), "utf8");

describe("연금 세율 칸", () => {
  it("국내·미국 갈래가 닫힌 뒤에 있다", () => {
    const 갈래 = src.indexOf('{country === "KR" ? (');
    const 닫힘 = src.indexOf("        )}\n", 갈래);
    for (const label of ['label="연금소득세 (시뮬레이션용)"', 'label="연금저축 세액공제율 (시뮬레이션용)"']) {
      expect(src.indexOf(label)).toBeGreaterThan(닫힘);
    }
  });
  it("미입력 줄이 어디서 넣는지 말한다", () => {
    const v = taxSimLines({
      assumption: { monthly_krw: 500000, price_return_pct: 5, dist_yield_pct: 2 },
      rows: [{ key: "pension_kr_listed_foreign", label: "연금저축", years: 10, contributed: 6e7, after_tax: null, tax: null, missing: ["pension_income_pct"] }],
    });
    expect(v?.groups[0].lines[0]).toContain("설정 화면");
  });
});
