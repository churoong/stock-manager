/**
 * Turso 가 풀리면 스스로 복귀를 깨운다 (docs/infra.md 25.12). 날짜를 박지 않고 수시로 본다.
 * 네트워크·DB 는 가짜다. 판단 순서와 "한 번만 깨운다" 를 본다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  COOLDOWN_MS,
  FAILED_COOLDOWN_MS,
  parseRequested,
  watchTursoReturn,
  type Requested,
  type WatchDeps,
} from "@/lib/tursoWatch";

const NOW = new Date("2026-10-01T00:10:00Z");

function deps(over: Partial<WatchDeps> = {}) {
  const calls: string[] = [];
  const base: WatchDeps = {
    mode: () => "auto",
    readMarker: async () => false,
    probe: async () => ({ ok: true, blocked: false }),
    lastRequested: async () => null,
    markRequested: async (request) => {
      calls.push(`mark:${request.ok}`);
    },
    dispatch: async () => {
      calls.push("dispatch");
      return { dispatched: true };
    },
    notify: async (text) => {
      calls.push(`notify:${text.split("\n")[0]}`);
    },
    now: () => NOW,
  };
  return { deps: { ...base, ...over }, calls };
}

describe("판단 순서", () => {
  it("auto 가 아니면 아무것도 보지 않는다", async () => {
    const { deps: d } = deps({ mode: () => "d1", readMarker: async () => expect.fail("볼 필요가 없다") });
    expect(await watchTursoReturn(d)).toBe("not-auto");
  });

  it("이미 돌아갔으면 Turso 를 찔러 보지 않는다", async () => {
    const { deps: d } = deps({ readMarker: async () => true, probe: async () => expect.fail("볼 필요가 없다") });
    expect(await watchTursoReturn(d)).toBe("returned");
  });

  it("D1 을 못 읽으면 옮길 수도 없으니 다음에 본다", async () => {
    const { deps: d, calls } = deps({ readMarker: async () => null });
    expect(await watchTursoReturn(d)).toBe("unknown");
    expect(calls).toEqual([]);
  });

  it("아직 한도로 막혀 있으면 조용히 끝난다", async () => {
    const { deps: d, calls } = deps({ probe: async () => ({ ok: false, blocked: true }) });
    expect(await watchTursoReturn(d)).toBe("blocked");
    expect(calls).toEqual([]);
  });

  it("한도가 아닌 장애는 풀린 것이 아니다", async () => {
    const { deps: d, calls } = deps({ probe: async () => ({ ok: false, blocked: false }) });
    expect(await watchTursoReturn(d)).toBe("down");
    expect(calls).toEqual([]);
  });
});

describe("풀렸을 때", () => {
  it("적고 → 깨우고 → 알린다", async () => {
    const { deps: d, calls } = deps();
    expect(await watchTursoReturn(d)).toBe("dispatched");
    // 깨우기 전에 "깨우는 중" 을, 깨운 뒤 결과를 적는다
    expect(calls).toEqual([
      "mark:null", "dispatch", "mark:true", "notify:🔓 Turso 가 풀렸습니다. 자동 복귀를 시작합니다",
    ]);
  });

  it("쿨다운 안에 깨웠으면 다시 깨우지 않는다 — 워크플로가 도는 중이다", async () => {
    const recent = new Date(NOW.getTime() - COOLDOWN_MS + 60_000).toISOString();
    const { deps: d, calls } = deps({ lastRequested: async () => ({ at: recent, ok: true }) });
    expect(await watchTursoReturn(d)).toBe("cooldown");
    expect(calls).toEqual([]);
  });

  it("쿨다운이 지났는데도 표시가 없으면 다시 깨운다 (첫 복귀가 실패했을 수 있다)", async () => {
    const old = new Date(NOW.getTime() - COOLDOWN_MS - 60_000).toISOString();
    const { deps: d } = deps({ lastRequested: async () => ({ at: old, ok: true }) });
    expect(await watchTursoReturn(d)).toBe("dispatched");
  });

  it("시각을 적지 못해도(D1 쓰기 한도) 깨운다", async () => {
    const { deps: d, calls } = deps({
      markRequested: async () => {
        throw new Error("daily row write limit");
      },
    });
    expect(await watchTursoReturn(d)).toBe("dispatched");
    expect(calls).toContain("dispatch");
  });

  it("깨우지 못하면 무엇을 누르면 되는지 알린다", async () => {
    const { deps: d, calls } = deps({
      dispatch: async () => ({ dispatched: false, reason: "GH_DISPATCH_TOKEN·GH_REPO 가 없어 깨울 수 없습니다" }),
    });
    expect(await watchTursoReturn(d)).toBe("dispatch-failed");
    expect(calls.at(-1)).toBe("notify:🔓 Turso 가 풀렸는데 복귀 작업을 깨우지 못했습니다");
  });

  it("알림이 실패해도 깨운 것은 깨운 것이다", async () => {
    const { deps: d } = deps({
      notify: async () => {
        throw new Error("텔레그램 HTTP 500");
      },
    });
    expect(await watchTursoReturn(d)).toBe("dispatched");
  });

  it("깨우지 못한 뒤에는 반나절 동안 같은 알림을 되풀이하지 않는다 — 그 사이는 하루 한 번 예약 실행이 맡는다", async () => {
    const hourAgo = new Date(NOW.getTime() - 3_600_000).toISOString();
    const { deps: d, calls } = deps({ lastRequested: async () => ({ at: hourAgo, ok: false }) });
    expect(await watchTursoReturn(d)).toBe("cooldown");
    expect(calls).toEqual([]);

    const longAgo = new Date(NOW.getTime() - FAILED_COOLDOWN_MS - 60_000).toISOString();
    const again = deps({ lastRequested: async () => ({ at: longAgo, ok: false }) });
    expect(await watchTursoReturn(again.deps)).toBe("dispatched");
  });
});

describe("저장된 기록 읽기", () => {
  it("JSON 기록을 읽고, 깨진 값은 없는 것으로 본다", () => {
    expect(parseRequested('{"at":"2026-10-01T00:00:00.000Z","ok":false}')).toEqual({ at: "2026-10-01T00:00:00.000Z", ok: false });
    expect(parseRequested('{"at":"2026-10-01T00:00:00.000Z"}')).toEqual({ at: "2026-10-01T00:00:00.000Z", ok: null });
    expect(parseRequested("깨진 값")).toBeNull();
    expect(parseRequested(null)).toBeNull();
  });
});

/**
 * 감시를 어느 경로가 부르는가 (2026-09-21, docs/infra.md 25.47).
 *
 * **왜 이것을 테스트하나.** `watchTursoReturn()` 이 아무리 옳아도 **아무도 부르지 않으면**
 * Turso 자동 복귀는 일어나지 않는다. 그리고 그것은 조용하다 — 오류도, 알림도 없다.
 * 그냥 영영 D1 에 머문다.
 *
 * 2026-09-19 에 `cron/health` 와 `cron/news` 가 **호출 기록을 한 번도 남기지 않은 것**을
 * 확인했다(infra 25.19, 원인 미확정). 그 둘이 감시를 부르는 **유일한** 경로였다.
 * `cron/intraday` 는 기록이 남아 있어 실제로 불린다 — 거기에 하나 더 얹었다.
 *
 * 이 테스트는 **셋 중 하나가 조용히 빠지는 것**을 막는다.
 */
describe("감시를 부르는 경로", () => {
  const 불러야_하는_곳: Record<string, string> = {
    "app/api/cron/health/route.ts": "매시 호출. 원래 자리",
    "app/api/cron/news/route.ts": "매시 호출(25.880). 부를 때마다 본다",
    "app/api/cron/intraday/route.ts":
      "호출 기록이 실제로 남는 유일한 경로. 장중만 불리지만 위 둘이 안 불릴 때의 보루",
  };

  it.each(Object.entries(불러야_하는_곳))("%s 가 감시를 부른다", (경로, 왜) => {
    const 소스 = readFileSync(join(process.cwd(), 경로), "utf-8");

    expect(소스, 왜).toContain("watchTursoReturn");
    // 감시가 실패해도 본 작업이 죽으면 안 된다
    expect(소스).toMatch(/watchTursoReturn\(\)\.catch\(/);
  });

  it("한 곳에만 기대지 않는다", () => {
    // 한 경로가 안 불리는 날 복귀가 통째로 멈춘다. 그것을 2026-09-19 에 겪을 뻔했다
    expect(Object.keys(불러야_하는_곳).length).toBeGreaterThanOrEqual(3);
  });
});

// ----------------------------------------------------------------------
// 되풀이 깨움 (docs/infra.md 25.79)
// ----------------------------------------------------------------------

describe("깨웠는데 복귀 표시가 안 생길 때", () => {
  /** 30분 쿨다운은 "워크플로가 5~10분 걸린다" 는 가정 위에 있다. 잡이 아예 못 돌면 그 가정이 깨진다 */
  function 준비(last: Requested | null, now: Date) {
    const 보낸것: string[] = [];
    const 적은것: Requested[] = [];
    const deps: WatchDeps = {
      mode: () => "auto",
      readMarker: async () => false,
      probe: async () => ({ ok: true, blocked: false }),
      lastRequested: async () => last,
      markRequested: async (r) => void 적은것.push(r),
      dispatch: async () => ({ dispatched: true }),
      notify: async (t) => void 보낸것.push(t),
      now: () => now,
    };
    return { deps, 보낸것, 적은것 };
  }

  const 기준 = new Date("2026-09-21T00:00:00Z");
  const 나중 = (분: number) => new Date(기준.getTime() + 분 * 60_000);

  it("처음에는 '시작합니다' 라고 알린다", async () => {
    const { deps, 보낸것, 적은것 } = 준비(null, 기준);

    expect(await watchTursoReturn(deps)).toBe("dispatched");
    expect(보낸것[0]).toContain("자동 복귀를 시작합니다");
    expect(적은것.at(-1)?.tries).toBe(1);
  });

  it("두 번째부터는 **다른 말**을 한다", async () => {
    const { deps, 보낸것, 적은것 } = 준비({ at: 기준.toISOString(), ok: true, tries: 1 }, 나중(111));

    expect(await watchTursoReturn(deps)).toBe("dispatched");
    expect(보낸것[0]).not.toContain("자동 복귀를 시작합니다");
    expect(보낸것[0]).toContain("2번째 깨움");
    expect(적은것.at(-1)?.tries).toBe(2);
  });

  it("되풀이 문구가 **진짜 원인**을 가리킨다", async () => {
    // 깨우기는 성공했는데 복귀 표시가 없다 = 잡이 시작조차 못 하는 것 (25.69)
    const { deps, 보낸것 } = 준비({ at: 기준.toISOString(), ok: true, tries: 1 }, 나중(111));
    await watchTursoReturn(deps);

    expect(보낸것[0]).toContain("Billing");
  });

  it("두 번 넘게 깨웠으면 **간격을 늘린다**", async () => {
    // 30분마다면 하루 48번이다. 사람이 알림을 끄고, 진짜 돌아간 날에도 모른다
    const 쌓임 = { at: 기준.toISOString(), ok: true, tries: 2 };

    expect(await watchTursoReturn(준비(쌓임, 나중(111)).deps)).toBe("cooldown");
    expect(await watchTursoReturn(준비(쌓임, 나중(60 * 11)).deps)).toBe("cooldown");
    expect(await watchTursoReturn(준비(쌓임, 나중(60 * 13)).deps)).toBe("dispatched");
  });

  it("아직 한 번뿐이면 쿨다운(110분) 뒤에 다시 깨운다", async () => {
    const 한번 = { at: 기준.toISOString(), ok: true, tries: 1 };

    expect(await watchTursoReturn(준비(한번, 나중(109)).deps)).toBe("cooldown");
    expect(await watchTursoReturn(준비(한번, 나중(111)).deps)).toBe("dispatched");
  });

  it("tries 가 없는 **옛 기록**도 읽는다", async () => {
    // 이 값이 생기기 전에 저장된 줄이 남아 있을 수 있다
    const { deps, 적은것 } = 준비({ at: 기준.toISOString(), ok: true }, 나중(111));

    expect(await watchTursoReturn(deps)).toBe("dispatched");
    expect(적은것.at(-1)?.tries).toBe(1);
  });
});

describe("깨운 횟수가 저장을 건너 살아남는다", () => {
  /**
   * **있었던 일** (docs/infra.md 25.134). `markRequested` 는 `JSON.stringify(request)` 로
   * `tries` 까지 적는데, `parseRequested` 는 `at` 과 `ok` 만 되살렸다.
   *
   * 그래서 서버리스 인스턴스가 바뀌면 `tries` 가 **0 으로 되돌아간다.** 25.79 가 넣은
   * 백오프("두 번 깨웠는데도 복귀 표시가 없으면 간격을 12시간으로")가 **한 번도 걸리지
   * 않고**, 알림 문구도 늘 "자동 복귀를 시작합니다" 에 머문다 — 그 코드의 주석이
   * "같은 문구를 되풀이하면 '시작합니다' 가 거짓말이 된다" 고 적어 둔 바로 그 상황이다.
   *
   * 하필 이것이 도는 날이 **Actions 무료 분이 없어 워크플로가 시작조차 못 하는 날**이다.
   * 깨우기는 성공(GitHub 은 204 를 준다)이므로 `ok === false` 백오프도 안 걸린다.
   * 30분마다 깨우고 30분마다 같은 알림이 간다.
   */
  it("적은 것을 그대로 되살린다", () => {
    const 적은것: Requested = { at: "2026-10-01T00:00:00Z", ok: true, tries: 3 };
    expect(parseRequested(JSON.stringify(적은것))).toEqual(적은것);
  });

  it("옛 기록에는 tries 가 없다 — 그때는 없는 채로 둔다", () => {
    // 부르는 쪽이 `last.tries ?? 0` 으로 본다. 0 을 넣어 주면 "0번 깨웠다" 가 사실이 된다
    expect(parseRequested('{"at":"2026-10-01T00:00:00Z","ok":true}')).toEqual({
      at: "2026-10-01T00:00:00Z",
      ok: true,
    });
  });

  it("tries 가 숫자가 아니면 없는 것으로 본다", () => {
    expect(parseRequested('{"at":"2026-10-01T00:00:00Z","ok":null,"tries":"셋"}')).toEqual({
      at: "2026-10-01T00:00:00Z",
      ok: null,
    });
  });

  it("두 번 깨웠는데도 표시가 없으면 간격이 12시간으로 늘어난다", async () => {
    // 저장된 값을 **JSON 을 거쳐** 읽는다. 실제 경로와 같은 길이어야 뜻이 있다
    const 저장됨 = JSON.stringify({ at: new Date(NOW.getTime() - COOLDOWN_MS - 1).toISOString(), ok: true, tries: 2 });
    const d = deps({ lastRequested: async () => parseRequested(저장됨) });

    expect(await watchTursoReturn(d.deps)).toBe("cooldown");
    expect(d.calls).toEqual([]);
  });

  it("한 번만 깨웠으면 쿨다운(110분) 뒤에 다시 깨운다", async () => {
    const 저장됨 = JSON.stringify({ at: new Date(NOW.getTime() - COOLDOWN_MS - 1).toISOString(), ok: true, tries: 1 });
    const d = deps({ lastRequested: async () => parseRequested(저장됨) });

    expect(await watchTursoReturn(d.deps)).toBe("dispatched");
  });

  it("두 번째부터는 무엇을 봐야 하는지 말한다", async () => {
    const 저장됨 = JSON.stringify({ at: new Date(NOW.getTime() - FAILED_COOLDOWN_MS - 1).toISOString(), ok: true, tries: 1 });
    const d = deps({ lastRequested: async () => parseRequested(저장됨) });

    await watchTursoReturn(d.deps);

    const 알림 = d.calls.find((c) => c.startsWith("notify:")) ?? "";
    expect(알림, "늘 '시작합니다' 라고만 한다").toContain("아직 끝나지 않았습니다");
    expect(알림).toContain("2번째");
  });
})

describe("쿨다운과 워크플로 제한 시간 (docs/infra.md 25.467)", () => {
  it("따라잡기를 기다리는 복귀가 끝나기 전에 다시 깨우지 않는다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 제한 = (이름: string) =>
      Number(/timeout-minutes:\s*(\d+)/.exec(readFileSync(join(process.cwd(), "..", ".github", "workflows", 이름), "utf-8"))?.[1]);
    const 합 = 제한("d1-catchup.yml") + 제한("turso-return.yml");
    expect(합).toBeGreaterThan(0);
    expect(COOLDOWN_MS).toBeGreaterThan(합 * 60_000);
  });
});
