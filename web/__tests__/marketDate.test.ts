import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { localDate } from "@/lib/market";

/**
 * 시장 현지 날짜 (docs/infra.md 25.59).
 *
 * **왜 있나.** 같은 질문에 답이 둘 있었다. `intraday.ts` 는 IANA 시간대로 제대로 냈고,
 * `health.ts` 의 `localDateOf` 는 미국을 **늘 UTC-5** 로 봤다. 주석에는 "날짜 경계만 보므로
 * -5 로 충분하다" 고 적혀 있었지만, 날짜 경계야말로 오프셋이 바꾸는 바로 그것이다.
 *
 * 서머타임(EDT, UTC-4) 동안 **매일 04:00~04:59 UTC 한 시간씩** 하루 전 날짜가 나왔다.
 * 2026년으로 세면 238시간이다. 무응답 감시는 매시 도니 그 시간대를 3월 9일부터 11월 1일까지
 * **매일** 지난다. 그 한 시간의 판단은 어제 세션을 보고 내려지고, `health_alerts.local_date`
 * 와 `/status` 의 "오늘" 도 하루 어긋난다.
 *
 * 고친 방식은 계산을 고치는 것이 아니라 **하나만 남기는 것**이다. 시간대 계산을 손으로 하면
 * 언젠가 또 틀린다.
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");

describe("현지 날짜", () => {
  it("한국과 미국이 다른 날일 수 있다", () => {
    const now = new Date("2026-09-18T02:00:00Z"); // 한국 11시, 뉴욕 전날 밤 10시

    expect(localDate("KR", now)).toBe("2026-09-18");
    expect(localDate("US", now)).toBe("2026-09-17");
  });

  it("한국은 UTC 15시에 이미 다음 날이다", () => {
    const now = new Date("2026-07-04T15:00:00Z"); // KST 익일 00시

    expect(localDate("KR", now)).toBe("2026-07-05");
  });

  describe("서머타임 — 고친 이유가 바로 여기다", () => {
    // 고정 -5 로 계산하면 아래 EDT 사례들이 전부 하루 전으로 나왔다
    it.each([
      ["겨울(EST, UTC-5) 04:30 UTC 는 아직 전날", "2026-01-15T04:30:00Z", "2026-01-14"],
      ["서머타임 시작 첫날(EDT) 04:30 UTC 는 이미 당일", "2026-03-09T04:30:00Z", "2026-03-09"],
      ["한여름(EDT) 04:30 UTC 는 당일 00:30", "2026-07-04T04:30:00Z", "2026-07-04"],
      ["서머타임 마지막 날(EDT) 04:30 UTC 는 당일", "2026-11-01T04:30:00Z", "2026-11-01"],
      ["서머타임 끝난 다음 날(EST) 04:30 UTC 는 전날", "2026-11-02T04:30:00Z", "2026-11-01"],
    ])("%s", (_이름, 시각, 기대) => {
      expect(localDate("US", new Date(시각))).toBe(기대);
    });

    it("한국은 서머타임이 없어 언제나 UTC+9 와 같다", () => {
      for (const 달 of [1, 4, 7, 10]) {
        const now = new Date(Date.UTC(2026, 달 - 1, 15, 4, 30));
        const 손계산 = new Date(now.getTime() + 9 * 3_600_000).toISOString().slice(0, 10);
        expect(localDate("KR", now)).toBe(손계산);
      }
    });

    it("2026년에 고정 -5 와 어긋나는 시각이 238개다", () => {
      // 실측을 테스트로 굳혀 둔다. 숫자가 변하면 시간대 규칙이 바뀐 것이다
      let 어긋남 = 0;
      for (let h = 0; h < 24 * 365; h++) {
        const t = new Date(Date.UTC(2026, 0, 1, 0, 30) + h * 3_600_000);
        const 고정 = new Date(t.getTime() - 5 * 3_600_000).toISOString().slice(0, 10);
        if (localDate("US", t) !== 고정) 어긋남 += 1;
      }
      expect(어긋남).toBe(238);
    });
  });
});

describe("답이 하나뿐인가", () => {
  it("고정 오프셋 사본(localDateOf)이 남아 있지 않다", () => {
    for (const 경로 of ["web/lib/health.ts", "web/lib/intraday.ts", "web/app/api/status/route.ts"]) {
      expect(읽기(경로), `${경로} 에 옛 사본 이름이 남아 있다`).not.toContain("localDateOf");
    }
  });

  it("시간대 계산은 market.ts 한 곳에만 있다", () => {
    // 다른 파일이 제 Intl 계산을 들면 또 갈라진다
    expect(읽기("web/lib/market.ts")).toContain("America/New_York");
    for (const 경로 of ["web/lib/health.ts", "web/lib/intraday.ts"]) {
      expect(읽기(경로), `${경로} 가 제 시간대 계산을 들고 있다`).not.toContain("America/New_York");
    }
  });

  it("현지 날짜를 쓰는 곳이 모두 같은 함수를 부른다", () => {
    // 호출 기록의 날짜는 heartbeat.ts 로 옮겼다(25.60). 장중 경로는 alerts.trade_date 에만 쓴다
    for (const 경로 of [
      "web/app/api/cron/intraday/route.ts",
      "web/app/api/status/route.ts",
      "web/lib/health.ts",
      "web/lib/heartbeat.ts",
    ]) {
      expect(읽기(경로), `${경로} 가 localDate 를 쓰지 않는다`).toContain("localDate(");
    }
  });

  it("손으로 더한 +9 시간이 남아 있지 않다", () => {
    // 한국은 서머타임이 없어 +9 가 틀리진 않지만, 그 모양을 미국 경로에 베끼면 25.59 가 된다.
    // 날짜를 내는 자리에는 남기지 않는다 (시:분만 보는 조용시간·표시용은 예외다)
    for (const 경로 of [
      "web/app/api/cron/intraday/route.ts",
      "web/app/api/cron/health/route.ts",
      "web/app/api/cron/news/route.ts",
      "web/app/api/cron/news-kr/route.ts",
    ]) {
      expect(읽기(경로), `${경로} 에 손으로 더한 날짜 계산이 남아 있다`).not.toContain(
        '9 * 3600_000).toISOString().slice(0, 10)',
      );
    }
  });
});
