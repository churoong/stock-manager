import { describe, expect, it } from "vitest";
import { representativeDisclosure, type ReactionRow } from "@/lib/disclosureReaction";
import { disclosureHit } from "@/lib/intraday";

/** 장중 공시 알림의 대표 제목 (docs/infra.md 25.1396, 공시 감사 ③) */
const 줄 = (type: string, words: string[], priority: number): ReactionRow => ({
  type, label: type, keywords: JSON.stringify(words), priority, n: 120,
  mean_pct: 1, median_pct: 1, pos_pct: 55, window_days: 5,
});
const 표 = [줄("rights", ["유상증자"], 2), 줄("buyback", ["자기주식취득"], 0)];

describe("대표 공시", () => {
  it("거래소 안내 공시(큰 접수번호)에 주요사항보고가 가려지지 않는다", () => {
    const hit = disclosureHit({ stock_id: 1, name: "가" }, {
      list: [
        { report_nm: "주요사항보고서(유상증자결정)", rcept_no: "20261015000950" },
        { report_nm: "기업설명회(IR)개최(안내공시)", rcept_no: "20261015800120" },
      ],
      total_count: 2,
    })!;
    // 경계는 여전히 최댓값 — 다음 거래일 "이미 알림" 이 흔들리지 않게
    expect(hit.data.rcept_no).toBe("20261015800120");
    const 고른 = representativeDisclosure(hit.data.items as Array<{ rcept_no: string; title: string }>, 표)!;
    expect(고른.item.title).toBe("주요사항보고서(유상증자결정)");
    expect(고른.row?.type).toBe("rights");
  });

  it("여럿이 걸리면 우선순위가 높은 쪽, 하나도 안 걸리면 첫 것(접수번호 최댓값)", () => {
    const 둘 = [{ title: "주요사항보고서(유상증자결정)" }, { title: "주요사항보고서(자기주식취득결정)" }];
    expect(representativeDisclosure(둘, 표)!.item.title).toBe("주요사항보고서(자기주식취득결정)");
    const 없음 = [{ title: "기업설명회(IR)개최" }, { title: "임원ㆍ주요주주특정증권등소유상황보고서" }];
    expect(representativeDisclosure(없음, 표)).toEqual({ item: 없음[0], row: null });
    expect(representativeDisclosure([], 표)).toBeNull();
  });
});
