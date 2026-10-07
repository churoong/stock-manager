/** DB 를 못 읽는 날의 로그인 시도 제한 — 서버 메모리 (docs/infra.md 25.843) */
import { describe, expect, it } from "vitest";
import { MEMORY_GLOBAL_MAX_FAILURES, MEMORY_PER_IP_MAX_FAILURES, memoryClear, memoryReserve, memoryReset } from "@/lib/loginGuard";

describe("메모리 시도 제한", () => {
  it("주소별 3번, 15분이 지나면 풀린다, 성공하면 지운다", () => {
    memoryReset();
    const t0 = Date.parse("2026-10-01T06:00:00Z");
    for (let i = 0; i < MEMORY_PER_IP_MAX_FAILURES; i++) expect(memoryReserve("a", t0 + i).allowed).toBe(true);
    const 막힘 = memoryReserve("a", t0 + 10);
    expect(막힘.allowed === false && 막힘.reason).toBe("ip");
    expect(memoryReserve("a", t0 + 15 * 60_000 + 1).allowed).toBe(true);
    memoryClear("a");
    expect(memoryReserve("a", t0 + 15 * 60_000 + 2).allowed).toBe(true);
    memoryReset();
  });

  it("여러 주소로 나눠 와도 전체 기준(5번)에서 막는다", () => {
    memoryReset();
    const t0 = Date.parse("2026-10-01T06:00:00Z");
    for (let i = 0; i < MEMORY_GLOBAL_MAX_FAILURES; i++) expect(memoryReserve(`ip${i}`, t0 + i).allowed).toBe(true);
    const v = memoryReserve("새주소", t0 + 100);
    expect(v.allowed === false && v.reason).toBe("global");
    memoryReset();
  });
});
