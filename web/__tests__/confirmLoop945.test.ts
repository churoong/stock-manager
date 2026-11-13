/** 저장 전 확인 루프 하나 (docs/infra.md 25.945) — 셋이 따로 있던 "그래도 저장할까요?" 를 묶었다. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CONFIRM_KINDS, confirmFlags, confirmNeeded, confirmPrompt, confirmSeenAt } from "@/lib/confirm";
import { tradeBody } from "@/lib/portfolio";

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

// 중복 409 의 `seen_at` 을 "그래도 저장" 에 `confirm_after` 로 되돌려 보낸다 (docs/infra.md 25.2228) —
// 안 보내면 서버가 409 의 앞 행을 확인 요청의 재전송으로 보고 1분 안의 진짜 두 번째 체결을 0행으로 버렸다
describe("confirmSeenAt — 확인 루프가 seen_at 을 되돌려 보낸다", () => {
  const T0 = "2026-10-10T01:00:00.000Z";
  const 기본 = { stockId: 1, currency: "KRW", side: "buy" as const, date: "2026-10-08", price: 70000, quantity: 10, fx: null, fee: null, tax: null, horizon: "long" as const, memo: "" };

  it("중복 물음의 seen_at 을 집고, 다른 물음은 앞서 집은 값을 지킨다", () => {
    expect(confirmSeenAt({ confirm_needed: "duplicate", seen_at: T0 }, "duplicate")).toBe(T0);
    expect(confirmSeenAt({ confirm_needed: "price_unit" }, "price_unit", T0)).toBe(T0);
    expect(confirmSeenAt({ confirm_needed: "duplicate" }, "duplicate")).toBeUndefined(); // 옛 서버 — 싣지 않는다
    expect(confirmSeenAt({ confirm_needed: "duplicate", seen_at: 5 }, "duplicate")).toBeUndefined();
  });

  it("루프 한 바퀴: 409(중복, seen_at) → 다음 본문에 confirm_after → 체결가 409 뒤에도 남는다", () => {
    let confirmed: ReadonlySet<"duplicate" | "price_unit" | "size"> = new Set();
    let seenAt: string | undefined;
    for (const 응답 of [{ confirm_needed: "duplicate", seen_at: T0 }, { confirm_needed: "price_unit" }]) {
      const 물음 = confirmNeeded(응답, confirmed)!;
      seenAt = confirmSeenAt(응답, 물음, seenAt);
      confirmed = new Set([...confirmed, 물음]);
      const 본문 = tradeBody({ ...기본, confirmed, seenAt });
      expect(본문.confirm_duplicate).toBe(true);
      expect(본문.confirm_after).toBe(T0);
    }
  });

  it("confirmFlags 는 중복을 확인했을 때만 confirm_after 를 싣는다", () => {
    expect(confirmFlags(new Set(["duplicate"]), T0)).toEqual({ confirm_duplicate: true, confirm_after: T0 });
    expect(confirmFlags(new Set(["price_unit"]), T0)).toEqual({ confirm_price: true });
    expect(confirmFlags(new Set(["duplicate"]))).toEqual({ confirm_duplicate: true });
  });

  it("폼 둘이 seen_at 을 다음 submit 으로 넘긴다", () => {
    const 화면 = readFileSync(join(process.cwd(), "components/PortfolioView.tsx"), "utf-8");
    expect(화면.match(/return submit\(new Set\(\[\.\.\.confirmed, 물음\]\), confirmSeenAt\(j, 물음, seenAt\)\);/g)?.length).toBe(2);
    expect(화면).toContain("confirmed, seenAt,\n");
    expect(화면).toContain("confirmed, seenAt })");
  });
});
