/** 가격 사다리 (docs/analysis.md 12장, docs/infra.md 25.1038) — 지금 종가를 제자리에 끼운다 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ladderWithNow, type LadderData, type LadderItem } from "@/lib/analysis";

const PY = readFileSync(join(process.cwd(), "..", "batch", "services", "verdict.py"), "utf-8");
const item = (label: string, price: number, kind: LadderItem["kind"] = "band"): LadderItem => ({ label, price, kind, source: "s", dist: 0 });

describe("가격 사다리", () => {
  it("지금 종가 줄이 가격 순서의 제자리에 들어간다", () => {
    const l: LadderData = { close: 100, items: [item("a", 130), item("b", 100), item("c", 80)], touch_months: [3, 12], assumed: true };
    const order = ladderWithNow(l).map((x) => ("now" in x ? "지금" : x.label));
    expect(order).toEqual(["a", "지금", "b", "c"]);
    expect(ladderWithNow({ ...l, items: [item("a", 130)] }).map((x) => ("now" in x ? "지금" : x.label))).toEqual(["a", "지금"]);
  });

  it("배치가 내는 종류는 화면이 아는 종류뿐이다", () => {
    const kinds = new Set([...PY.matchAll(/더하기\(.+?, "([a-z]+)", "[^"]+"/g)].map((m) => m[1]));
    kinds.add("criterion");
    const known: LadderItem["kind"][] = ["high", "band", "consensus", "range", "signal", "target", "stop", "criterion"];
    for (const k of kinds) expect(known).toContain(k);
    expect(kinds.size).toBeGreaterThanOrEqual(7);
  });
});
