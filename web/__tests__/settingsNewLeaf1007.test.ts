/** 나중에 더한 nullable 칸이 옛 저장값에 없을 때 (docs/infra.md 25.1007) */
import { describe, expect, it } from "vitest";
import { unreadableSettingNotices } from "@/lib/settings";

describe("옛 세율 설정", () => {
  it("연금 칸이 없는 옛 taxes 는 경고를 띄우지 않는다", () => {
    const 옛 = JSON.stringify({ kr_transaction_pct: 0.18, kr_dividend_pct: 15.4, us_dividend_pct: 15, us_capital_gains_pct: 22 });
    expect(unreadableSettingNotices([{ key: "taxes", value: 옛 }])).toEqual([]);
  });

  it("칸이 있는데 숫자가 아니면 여전히 알린다", () => {
    const 깨짐 = JSON.stringify({ kr_dividend_pct: "15.4%" });
    expect(unreadableSettingNotices([{ key: "taxes", value: 깨짐 }]).length).toBe(1);
  });
});
