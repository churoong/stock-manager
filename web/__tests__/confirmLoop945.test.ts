/** 저장 전 확인 루프 하나 (docs/infra.md 25.945) — 셋이 따로 있던 "그래도 저장할까요?" 를 묶었다. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CONFIRM_KINDS, confirmFlags, confirmNeeded, confirmPrompt } from "@/lib/confirm";

describe("confirmNeeded", () => {
  it("서버가 지금 묻는 종류 하나를 고르고, 이미 확인한 것은 다시 묻지 않는다", () => {
    expect(confirmNeeded({ confirm_needed: "duplicate", duplicate: true }, new Set())).toBe("duplicate");
    expect(confirmNeeded({ confirm_needed: "price_unit" }, new Set(["duplicate"]))).toBe("price_unit");
    expect(confirmNeeded({ confirm_needed: "size" }, new Set(["size"]))).toBeNull();
    expect(confirmNeeded({ errors: ["다른 오류"] }, new Set())).toBeNull();
  });

  it("옛 깃발만 온 응답(배포 사이)도 읽는다", () => {
    expect(confirmNeeded({ duplicate: true }, new Set())).toBe("duplicate");
    expect(confirmNeeded({ price_unit: true }, new Set())).toBe("price_unit");
    expect(confirmNeeded({ size_unit: true }, new Set())).toBe("size");
  });

  it("모르는 종류는 묻지 않는다 — 서버가 잘못 보내도 화면이 멈추지 않는다", () => {
    expect(confirmNeeded({ confirm_needed: "weird" }, new Set())).toBeNull();
  });
});

describe("confirmFlags", () => {
  it("확인한 것만 싣고 안 한 것은 false 로도 보내지 않는다", () => {
    expect(confirmFlags(new Set())).toEqual({});
    expect(confirmFlags(new Set(["duplicate", "size"]))).toEqual({ confirm_duplicate: true, confirm_size: true });
  });

  it("종류마다 둘째 문장이 있다", () => {
    for (const k of CONFIRM_KINDS) expect(confirmPrompt(k, ["사유"])).toMatch(/^사유\n\n.+확인을 눌러 저장하세요\.$/);
  });
});

describe("서버와 화면이 같은 이름을 쓴다", () => {
  const 읽기 = (p: string) => readFileSync(join(process.cwd(), p), "utf-8");

  it("두 저장 경로가 confirm_needed 를 준다", () => {
    const 매매 = 읽기("app/api/trades/route.ts");
    const 배당 = 읽기("app/api/dividends/route.ts");
    expect(매매).toContain('confirm_needed: "duplicate"');
    expect(매매).toContain('confirm_needed: "price_unit"');
    expect(배당).toContain('confirm_needed: "duplicate"');
    expect(배당).toContain('confirm_needed: "size"');
  });

  it("폼 둘이 같은 루프를 쓰고 위치 인자 확인이 남아 있지 않다", () => {
    const 화면 = 읽기("components/PortfolioView.tsx");
    expect(화면.match(/confirmNeeded\(j, confirmed\)/g)?.length).toBe(2);
    expect(화면).not.toMatch(/submit\((true|false),/);
    expect(화면).not.toContain("j.duplicate");
    expect(화면).not.toContain("j.price_unit");
    expect(화면).not.toContain("j.size_unit");
  });
});
