// 계좌 화면 이름 (docs/infra.md 25.1889) — 웹 사본이 배치 정의처와 같은가
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ACCOUNT_NAMES, accountName } from "@/lib/accountNames";

describe("계좌 화면 이름", () => {
  it("배치 ACCOUNT_NAMES 와 같다", () => {
    const py = readFileSync(join(process.cwd(), "..", "batch", "services", "paper.py"), "utf8");
    const 몸 = /^ACCOUNT_NAMES: dict\[str, str\] = \{([\s\S]*?)^\}/m.exec(py)?.[1] ?? "";
    const 쌍 = Object.fromEntries([...몸.matchAll(/"([A-Z0-9]+)": "([^"]+)"/g)].map((m) => [m[1], m[2]]));
    expect(Object.keys(쌍).length).toBeGreaterThan(5);
    expect(쌍).toEqual({ ...ACCOUNT_NAMES });
  });
  it("모르는 계좌는 식별자 그대로", () => {
    expect(accountName("H")).toBe("지주사");
    expect(accountName("Z9")).toBe("Z9");
    expect(accountName(null)).toBe("");
  });
});
