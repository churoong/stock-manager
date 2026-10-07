import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 배치는 국내 매도 세율 칸을 "증권거래세 + 농특세 합" 으로 읽는다. 화면은 "증권거래세" 라고만 적어 체결 내역의 거래세(0.05%)만
// 옮겨 적으면 실현손익의 매도세가 0.15%p 적게 잡히고 백테스트 2026년 세율도 낮아졌다 (docs/infra.md 25.912, 감사)
describe("설정의 매도 거래세 칸 (25.912)", () => {
  it("합계라는 것을 이름과 안내로 말한다", () => {
    const 글 = readFileSync("components/SettingsForm.tsx", "utf-8");
    expect(글).toContain('label="매도 거래세 (증권거래세 + 농특세 합)"');
    expect(글).toContain("농어촌특별세를 더한 값");
  });
});
