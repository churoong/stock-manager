/** 국내 기사 종목 매칭: 이름 뒤 한글 덩어리 전체가 조사여야 한다 (docs/infra.md 25.546, 감사 재현). */

import { describe, expect, it } from "vitest";
import { matchStocks } from "@/lib/newsKr";

const 짧은 = [
  { stock_id: 1, name: "HLB" }, { stock_id: 2, name: "에코프로" }, { stock_id: 3, name: "HD현대" },
  { stock_id: 4, name: "BGF" }, { stock_id: 5, name: "삼성전자" },
];

describe("국내 종목명 매칭 (25.546)", () => {
  it("조사 첫 글자로 시작하는 다른 회사 이름에는 붙지 않는다", () => {
    for (const 제목 of ["HLB이노베이션, 3분기 적자 확대", "에코프로에이치엔 유상증자 결정", "HD현대에너지솔루션 급락",
      "BGF에코머티리얼즈 공장 화재", "삼성전자로지텍 노조 파업"]) {
      expect(matchStocks(제목, 짧은), 제목).toEqual([]);
    }
  });

  it("진짜 조사는 그대로 붙는다", () => {
    expect(matchStocks("삼성전자가 반도체 투자 확대", 짧은).map((s) => s.stock_id)).toEqual([5]);
    expect(matchStocks("에코프로에서 화재", 짧은).map((s) => s.stock_id)).toEqual([2]);
    expect(matchStocks("HD현대, 수주 소식", 짧은).map((s) => s.stock_id)).toEqual([3]);
    expect(matchStocks("삼성전자株 강세", 짧은).map((s) => s.stock_id)).toEqual([5]);
  });

  it("복합 조사도 붙는다 (25.548, 교차검증)", () => {
    for (const 제목 of ["삼성전자에선 신중론", "삼성전자에서도 감산", "삼성전자로선 부담", "삼성전자와는 별개",
      "삼성전자로부터 수주", "삼성전자랑 협력"]) {
      expect(matchStocks(제목, 짧은).map((s) => s.stock_id), 제목).toEqual([5]);
    }
  });
});
