import { describe, expect, it } from "vitest";
import { disclosureHit } from "@/lib/intraday";

/** 공시 알림의 "외 N건" 이 받은 쪽 수(최대 10)로 잘리지 않는가 (docs/infra.md 25.288). */
describe("disclosureHit", () => {
  const 종목 = { stock_id: 1, name: "에이" };
  const 열건 = Array.from({ length: 10 }, (_, i) => ({
    report_nm: ` 공시${i} `,
    rcept_no: `2026${9 - i}`, // DART 는 새 것부터 준다
  }));

  it("건수는 total_count 를 쓴다", () => {
    const hit = disclosureHit(종목, { list: 열건, total_count: 15 });
    expect(hit?.message).toBe('에이: 새 공시 "공시0" 외 14건');
    expect(hit?.data.count).toBe(15);
  });

  it("total_count 가 없거나 이상하면 받은 건수", () => {
    expect(disclosureHit(종목, { list: 열건.slice(0, 3) })?.data.count).toBe(3);
    expect(
      disclosureHit(종목, { list: 열건.slice(0, 3), total_count: "x" })?.data
        .count,
    ).toBe(3);
  });

  it("한 건이면 '외' 를 붙이지 않고, 없으면 알림도 없다", () => {
    expect(
      disclosureHit(종목, { list: 열건.slice(0, 1), total_count: 1 })?.message,
    ).toBe('에이: 새 공시 "공시0"');
    expect(disclosureHit(종목, { list: [] })).toBeNull();
  });

  it("응답 순서와 상관없이 가장 큰 접수번호를 경계로 적는다 (25.636)", () => {
    const 오름 = [
      { report_nm: "먼저", rcept_no: "20260929000001" },
      { report_nm: "나중", rcept_no: "20260929000003" },
      { report_nm: "가운데", rcept_no: "20260929000002" },
    ];
    const hit = disclosureHit(종목, { list: 오름, total_count: 3 });
    expect(hit?.data.rcept_no).toBe("20260929000003");
    expect(hit?.message).toBe('에이: 새 공시 "나중" 외 2건');
  });

  it("못 받은 쪽을 셀 수 없으면 '이상' 을 붙인다 (25.636)", () => {
    const hit = disclosureHit(종목, {
      list: 열건.slice(0, 4),
      total_count: 4,
      atLeast: true,
    });
    expect(hit?.message).toBe('에이: 새 공시 "공시0" 외 3건 이상');
  });
});
