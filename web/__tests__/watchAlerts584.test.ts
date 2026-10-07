import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DEFAULT_FILTER, unreadIdsShown } from "@/lib/alerts";
import { targetAboveCloseWarning } from "@/lib/watch";

const 알림 = (id: number, market: string, is_read: number) => ({ id, market, trigger_type: "spike", is_read }) as never;

describe("관심·알림 감사 (docs/infra.md 25.584)", () => {
  it("모두 읽음은 지금 보이는 안 읽은 알림만 보낸다 — 빈 목록(=서버의 '전부')을 보내지 않는다", () => {
    const 목록 = [알림(1, "KR", 0), 알림(2, "US", 0), 알림(3, "US", 1)];
    expect(unreadIdsShown(목록, { ...DEFAULT_FILTER, market: "US" })).toEqual([2]);
    expect(unreadIdsShown(목록, DEFAULT_FILTER)).toEqual([1, 2]);
    const 화면 = readFileSync("components/AlertCenter.tsx", "utf8");
    expect(화면).not.toContain("markRead([])");
  });

  it("종가 이상 목표가는 경고한다(막지는 않는다)", () => {
    expect(targetAboveCloseWarning(70_000, 60_000)).toContain("곧바로");
    expect(targetAboveCloseWarning(55_000, 60_000)).toBeNull();
    expect(targetAboveCloseWarning(70_000, null)).toBeNull();
  });

  it("읽기 실패를 '없다' 로 적지 않고, 쉼표 목표가가 있던 목표가를 지우지 않는다", () => {
    const 화면 = readFileSync("components/AlertCenter.tsx", "utf8");
    expect(화면).toContain('alertsFailed ? "알림을 읽지 못했습니다." : "아직 알림이 없습니다."');
    expect(화면).toContain('failed ? "관심 종목을 읽지 못했습니다." : "관심 종목이 없습니다."');
    expect(화면).toContain('price.trim().replace(/,/g, "")');
    expect(화면).not.toContain("target_buy_price: price.trim() ? Number(price) : null");
  });
});

describe("목표가 지우기·경고 색 (25.587, 교차검증)", () => {
  it("목록에서 목표가만 지울 수 있고, 경고는 오류 칸이 아니다", () => {
    const 화면 = readFileSync("components/AlertCenter.tsx", "utf8");
    expect(화면).toContain("void patch(w.id, { target_buy_price: null })");
    expect(화면).toContain("setWarn(j.warnings?.length");
    expect(화면).not.toContain("setErr(j.warnings");
  });
});

it("다시 읽다 실패하면 옛 목록이라고 말한다 (25.588)", () => {
  expect(readFileSync("components/AlertCenter.tsx", "utf8")).toContain("새로 읽지 못해 아래는 앞서 읽은 목록입니다");
});

