import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { etfRunNote } from "@/lib/etf";

describe("일부만 끝난 ETF 판정을 화면에 알린다 (docs/infra.md 25.580, 감사)", () => {
  it("partial 이면 배치가 남긴 까닭을 싣는다", () => {
    const 줄 = etfRunNote({ status: "partial", step_log: JSON.stringify({ notes: ["3년 전 목록이 없어 이번 판정을 저장하지 않았습니다"] }) });
    expect(줄).toContain("일부만 끝났습니다");
    expect(줄).toContain("3년 전 목록");
  });
  it("'저장하지 않았습니다' 는 뒤에 있어도 맨 앞에 싣는다 (25.583)", () => {
    const 줄 = etfRunNote({ status: "partial", step_log: JSON.stringify({ notes: ["a", "b", "c", "이번 판정을 저장하지 않았습니다 — 화면은 지난 판정 그대로입니다"] }) });
    expect(줄).toContain(": 이번 판정을 저장하지 않았습니다");
  });
  it("성공이면 아무것도 붙이지 않는다", () => {
    expect(etfRunNote({ status: "success", step_log: "{}" })).toBeNull();
    expect(etfRunNote(null)).toBeNull();
  });
  it("실행 기록을 못 읽으면 까닭이 없다고 하지 않는다", () => {
    expect(etfRunNote({ status: "partial", step_log: "x" })).toContain("읽지 못했습니다");
  });
  it("경로와 화면이 그 줄을 쓴다", () => {
    expect(readFileSync("app/api/etf/route.ts", "utf8")).toContain("etfRunNote({ status: row[1], step_log: row[2] })");
    expect(readFileSync("components/EtfList.tsx", "utf8")).toContain("lastRuns[country]?.note");
  });
});

describe("위성 ETF 빈 화면 (25.581, 감사)", () => {
  it("돌았는지 batch_runs 를 보고 말한다", () => {
    const 글 = readFileSync("app/api/etf/satellite/route.ts", "utf8");
    expect(글).toContain("job_name = 'etf_satellite'");
    expect(글).toContain("범위에 든 ETF 가 없었습니다");
  });
});

describe("위성 탭도 핵심 판정이 저장을 건너뛴 까닭을 말한다 (25.588)", () => {
  it("경로가 etf 실행 기록을 읽어 etfRunNotStored 로 가른다", () => {
    const 글 = readFileSync("app/api/etf/satellite/route.ts", "utf8");
    expect(글).toContain("job_name = 'etf' AND market = ?");
    expect(글).toContain("if (etfRunNotStored(핵심말))");
  });
});

it("시험 실행의 '저장하지 않았습니다' 는 위성 탭에 싣지 않는다 (25.592)", () => {
  // 시험 실행은 건너뛰고 본 실행 가운데 가장 최근 것 (25.594)
  expect(readFileSync("app/api/etf/satellite/route.ts", "utf8")).toContain(".find((x) => !x.시험)?.말 ?? null");
});

