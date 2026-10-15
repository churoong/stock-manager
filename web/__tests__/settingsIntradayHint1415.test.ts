import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 설정 화면 장중 알림 안내가 KIS 실시간 도입 뒤의 시세 경로와 맞는다 (docs/infra.md 25.1415, 장중·설정 감사).
// 예전 문구 "국내 장중 시세는 약 20분 지연입니다" 는 KIS 키가 있는 사용자에게 거짓이었다 — 지연 표시는 야후로 물러난 줄에만 붙는다
describe("장중 알림 안내 문구", () => {
  it("국내는 KIS 실시간이 먼저이고 지연은 물러난 종목만이라고 말한다", () => {
    const form = readFileSync("components/SettingsForm.tsx", "utf8");
    expect(form).not.toContain("국내 장중 시세는 약 20분 지연입니다");
    expect(form).toContain("KIS 실시간 시세를 먼저 쓰고");
    // 알림 줄에 붙는 실제 표시와 같은 낱말
    expect(readFileSync("lib/intraday.ts", "utf8")).toContain(
      "지연 시세 ${kstHm(quote.time)} 기준",
    );
  });
});
