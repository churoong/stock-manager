import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import { ifMissingTable } from "@/lib/db";

/**
 * **못 읽은 것을 "없다" 로 말하지 않는다** (docs/infra.md 25.163).
 *
 * 25.74 에서 배운 것이다 — `/api/alerts` 의 호출 기록이 그랬다. 질의가 실패하면 빈
 * 목록이 되고, 화면은 "호출 기록 없음" 이라 적었다. 그 글자는 **"외부 크론이 안 부른다"**
 * 는 진단으로 읽힌다(docs/todo-user.md 2번이 바로 그 진단이다).
 *
 * 그때 고친 것은 `/api/alerts` 한 곳이고, **같은 모양이 네 곳 더 있었다.**
 * 그중 `/api/status` 는 "크론이 부르고 있나" 를 답하려고 만든 화면이다.
 *
 * 여기서 두 가지를 본다.
 *   1. 실패를 삼키는 `.catch(() => …)` 가 **전부 사유와 함께 적혀 있는가**
 *   2. 삼킨 자리가 화면에서 "없음" 과 구별되는가
 */

const 웹 = process.cwd();

/** 실패를 조용히 값으로 바꾸는 모양 */
const 삼키는_무늬 =
  /\.catch\(\s*(?:\(\s*[^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*(?:\[\s*\]|null|undefined|0|\{\s*\}|false|true)/;

/**
 * 삼켜도 되는 자리와 **왜 되는지**. 새로 생기면 여기 적어야 통과한다.
 * 열쇠는 `파일::그 줄에만 있는 조각` 이다 — 줄 번호는 곧 어긋난다.
 */
const 삼켜도_되는_곳: Record<string, string> = {
  "lib/kis.ts::parseStrength(await response.json().catch(":
    "체결강도는 알림에 붙이는 곁다리 한 줄이다(25.991). 응답이 깨졌으면 줄 없이 알림만 나간다 — 알림을 막지 않는다",
  "lib/kis.ts::DELETE FROM api_tokens":
    "무효 토큰 지우기는 곁다리다(25.986). 못 지우면 다음 호출이 다시 실패하고 다시 지운다 — 그 호출의 시세는 야후로 이미 받았다",
  "lib/db.ts::noteTursoReads(":
    "웹 Turso 읽기를 월 카운터에 더하는 곁다리다(25.891). 못 적어도 본 질의 결과는 그대로 돌려줘야 한다 — 못 적은 몫은 되돌려 다음에 적는다",
  "app/api/alerts/route.ts::execute(ALERT_TOTALS)":
    "전체·안 읽은 수는 곁다리다(25.805). null 이면 화면이 예전처럼 목록 안에서 센다 — 알림 목록 전체를 500 으로 만들지 않는다",
  "app/api/cron/health/route.ts::watchTursoReturn()":
    "Turso 복귀 감시는 곁다리다. 실패해도 본 작업(무응답 감시)을 막으면 안 된다",
  "app/api/cron/intraday/route.ts::watchTursoReturn()":
    "위와 같다. 장중 알림이 곁다리 때문에 멈추면 안 된다",
  "app/api/cron/news/route.ts::watchTursoReturn()":
    "위와 같다. 뉴스 수집이 곁다리 감시 때문에 멈추면 안 된다",
  "app/api/cron/intraday/route.ts::as Built[]":
    "감시 목록 나이(25.162)는 알림에 붙이는 **말 한 줄**이다. 못 읽어도 알림은 나가야 한다",
  "app/api/cron/intraday/route.ts::readUsage(":
    "한도를 못 읽으면 `null` 이고, 부르는 쪽이 `usage &&` 로 가린다 — 빈 값이 아니라 모름으로 다룬다",
  "app/api/cron/intraday/route.ts::sent_at = NULL WHERE id":
    "발송 실패 뒤 잡은 알림 풀기(25.538)다. 못 풀어도 10분 뒤 잡음이 묵어 다시 대기로 읽힌다 — 원래 오류는 그대로 던진다",
  "app/api/cron/intraday/route.ts::addUsage(":
    "호출 수 기록 실패가 시세 알림을 막으면 안 된다. 기록은 다음 호출이 이어서 센다",
  "app/api/screener/presets/route.ts::request.json()": "본문이 JSON 이 아니면 `null` → zod 가 400 으로 답한다",
  "app/api/watchlist/route.ts::request.json()": "본문이 JSON 이 아니면 `null` → zod 가 400 으로 답한다",
  "app/api/watchlist/[id]/route.ts::request.json()": "본문이 JSON 이 아니면 `null` → zod 가 400 으로 답한다",
  "app/api/screener/route.ts::catch(() => null)":
    "0건 진단 조회다. **실패하면 진단하지 않는다** — `null` 을 그대로 넘기면 `whyEmpty` 가"
    + " '유니버스가 비었습니다' 라고 단정해 사람을 엉뚱한 데로 보낸다 (25.163)",
  "components/ServiceWorker.tsx::serviceWorker.register": "설치 실패는 화면 동작과 무관하다 (docs/pwa.md)",
  "lib/heartbeat.ts::).catch(() => undefined)": "호출 기록 쓰기 실패가 크론 본 작업을 막으면 안 된다",
  "app/api/cron/health/route.ts::WHERE id = ?\", [row.id]).catch(": "발송 실패 뒤 잡은 알림을 푸는 쓰기 — 풀기 실패는 원래 발송 오류에 묻힌다(중복보다 빠짐이 낫다, 25.593)",
  "app/api/trades/[id]/route.ts::requestRecalc().catch(": "이미 실패한 삭제의 뒤처리다 — 재계산 요청이 실패해도 원래 오류(500)를 그대로 돌려준다 (25.591)",
  "lib/tursoWatch.ts::deps.lastRequested()": "지난 요청 기록을 못 읽으면 `null` — 부르는 쪽이 '모름' 으로 다룬다",
  "lib/tursoWatch.ts::ok: null, tries: 시도":
    "표시 쓰기 실패가 복귀 요청 자체를 막으면 안 된다. 다음 호출이 다시 본다",
  "lib/tursoWatch.ts::ok: result.dispatched":
    "결과 표시 쓰기 실패가 복귀 요청을 되돌리면 안 된다. 다음 호출이 다시 본다",
  "lib/tursoWatch.ts::deps.notify(": "알림 발송 실패가 복귀 절차를 되돌리면 안 된다",
};

function 소스들(): string[] {
  const out: string[] = [];
  for (const 자리 of ["lib", "app", "components"]) {
    for (const entry of readdirSync(join(웹, 자리), { recursive: true, withFileTypes: true })) {
      if (!entry.isFile()) continue;
      if (!/\.tsx?$/.test(entry.name)) continue;
      const dir = relative(웹, entry.parentPath ?? (entry as { path: string }).path);
      if (dir.split(sep).includes("__tests__")) continue;
      out.push(join(dir, entry.name).split(sep).join("/"));
    }
  }
  return out.sort();
}

function 삼키는_줄들(): Array<{ 파일: string; 줄: string }> {
  const out: Array<{ 파일: string; 줄: string }> = [];
  for (const 파일 of 소스들()) {
    const 글 = readFileSync(join(웹, 파일), "utf-8");
    for (const 줄 of 글.split("\n")) {
      // 설명문에 예로 적은 것은 코드가 아니다
      if (/^\s*[*/]/.test(줄)) continue;
      if (삼키는_무늬.test(줄)) out.push({ 파일, 줄: 줄.trim() });
    }
  }
  return out;
}

describe("실패를 삼키는 자리", () => {
  it("훑어 냈다", () => {
    expect(소스들().length, "소스를 너무 적게 찾았다 — 훑는 자리가 바뀌었다").toBeGreaterThan(50);
    expect(삼키는_줄들().length, "하나도 못 찾았다 — 무늬가 안 맞는다").toBeGreaterThan(5);
  });

  it("전부 사유가 적혀 있다", () => {
    const 모르는것 = 삼키는_줄들().filter(
      ({ 파일, 줄 }) =>
        !Object.keys(삼켜도_되는_곳).some((k) => {
          const [f, needle] = k.split("::");
          return f === 파일 && 줄.includes(needle);
        }),
    );

    expect(
      모르는것.map((x) => `${x.파일}: ${x.줄}`),
      "실패를 조용히 값으로 바꾸는 자리가 늘었다. `ifMissingTable` 을 쓰거나," +
        " 삼켜도 되는 이유를 `삼켜도_되는_곳` 에 적어라 (docs/infra.md 25.163)",
    ).toEqual([]);
  });

  it("목록이 낡지 않았다", () => {
    const 줄들 = 삼키는_줄들();
    const 사라진것 = Object.keys(삼켜도_되는_곳).filter((k) => {
      const [f, needle] = k.split("::");
      return !줄들.some((x) => x.파일 === f && x.줄.includes(needle));
    });

    expect(사라진것, "목록에만 남은 자리 — 지워라").toEqual([]);
  });

  it("사유가 비어 있지 않다", () => {
    const 짧은것 = Object.entries(삼켜도_되는_곳).filter(([, v]) => v.trim().length < 20);
    expect(짧은것.map(([k]) => k)).toEqual([]);
  });
});

describe("표가 없을 때만 삼키는 도우미", () => {
  it("표가 없으면 대신할 값을 준다", () => {
    expect(ifMissingTable([] as number[])(new Error("no such table: signal_checks"))).toEqual([]);
  });

  it("다른 실패는 다시 던진다", () => {
    // 한도·인증이 삼켜지면 화면이 "아직 없습니다" 라고 적는다 — 그것이 25.163 이다
    expect(() => ifMissingTable([])(new Error("blocked: upgrade your plan"))).toThrow(/blocked/);
    expect(() => ifMissingTable([])(new Error("401 unauthorized"))).toThrow(/401/);
  });

  it("Error 가 아닌 것도 본다", () => {
    expect(ifMissingTable("x")("no such table: t")).toBe("x");
    expect(() => ifMissingTable("x")("그냥 실패")).toThrow();
  });

  it("삼켜도 되는 자리에서 실제로 쓴다", () => {
    for (const 파일 of ["lib/stockDetail.ts", "app/api/recommend/route.ts"]) {
      expect(readFileSync(join(웹, 파일), "utf-8"), `${파일} 이 다시 아무 실패나 삼킨다`).toContain(
        "ifMissingTable(",
      );
    }
  });
});

describe("시스템 상태 화면", () => {
  it("경로가 못 읽은 이유를 함께 준다", () => {
    const 글 = readFileSync(join(웹, "app", "api", "status", "route.ts"), "utf-8");

    expect(글).toContain("read_errors");
    for (const key of ["usage", "health_alerts", "cron", "sessions"]) {
      expect(글, `${key} 를 못 읽은 것이 조용히 빈 목록이 된다`).toContain(`읽되_이유를_남긴다("${key}")`);
    }
  });

  it("화면이 '없음' 과 '못 읽음' 을 가른다", () => {
    const 글 = readFileSync(join(웹, "components", "StatusView.tsx"), "utf-8");

    expect(글).toContain("read_errors");
    // **못 읽었으면 크론을 다시 등록하라고 시키지 않는다**
    expect(글).toContain("호출 기록을 읽지 못했습니다");
    expect(글).toContain('못읽음("sessions")');
    expect(글).toContain('못읽음("freshness")');
  });

  it("거래일을 못 읽었을 때 '늦음' 판정을 믿지 말라고 말한다", () => {
    const 글 = readFileSync(join(웹, "components", "StatusView.tsx"), "utf-8");
    // sessions 를 못 읽으면 모든 대상이 trading_day:false 가 되어 **쉬는 날처럼** 보인다
    expect(글).toContain("거래일인지 가리지 못했습니다");
  });
});

describe("매매를 지울 때", () => {
  it("확인하지 못한 것을 '문제 없음' 으로 만들지 않는다", () => {
    const 글 = readFileSync(join(웹, "app", "api", "trades", "[id]", "route.ts"), "utf-8");

    expect(글).toContain("남은 매도를 확인하지 못했습니다");
    expect(글, "빈 경고 목록은 '확인했고 괜찮다' 와 같아 보인다").not.toContain(".catch(() => [] as string[])");
  });
});
