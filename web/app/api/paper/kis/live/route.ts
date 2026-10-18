import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  BATCH_RUNNING_SQL,
  BATCH_RUNNING_WINDOW_MS,
  KIS_VTS_BASE,
  LIVE_ANY_GAP_MS,
  LIVE_ANY_KEY,
  LIVE_CALL_GAP_MS,
  LIVE_MIN_GAP_MS,
  LIVE_THROTTLE_SQL,
  LIVE_TIMEOUT_MS,
  VTS_SAVED_ROW_SQL,
  kisErrorCode,
  liveRequests,
  parseKrLive,
  parseUsLive,
  US_EXCHANGES,
  throttleKey,
  vtsAccount,
  vtsConfigured,
  vtsUrlOk,
  type VtsMarket,
} from "@/lib/kisVts";
import { localDate } from "@/lib/market";

/** KIS 두 번(각 8초 상한) + 간격 + DB — 기본 함수 시간(10초)을 넘을 수 있다(25.1499, 검토 [권장] 4) */
export const maxDuration = 30;

/**
 * KIS모의투자 열 때 조회 B (docs/paper.md 15.7, docs/infra.md 25.1498) — 그 시장 잔고·오늘 체결을 KIS 모의에서 바로 받아 고른 칸만 돌려준다.
 * **POST** — 다른 사이트의 링크(최상위 이동, SameSite=Lax 쿠키)로 KIS 호출을 일으키지 못하게 미들웨어의 교차 사이트 검사를 거친다.
 * 조회만 한다: 주문·토큰 발급 경로가 없다. 계좌번호·토큰·KIS 원시 응답은 돌려주지 않는다.
 */
export async function POST(request: Request) {
  let market: VtsMarket;
  try {
    const body = (await request.json()) as { market?: unknown };
    if (body.market !== "KR" && body.market !== "US") return NextResponse.json({ errors: ["시장은 KR 또는 US"] }, { status: 400 });
    market = body.market;
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  if (!vtsConfigured()) return NextResponse.json({ off: "Vercel 환경변수에 KIS모의투자 키가 없어 열 때 조회는 꺼져 있습니다 — 배치 기록만 보입니다" });
  const now = new Date();
  try {
    // 일일 배치가 도는 동안은 부르지 않는다 — 배치 주문이 초당 한도에 걸려 거부로 적힌다
    const 돌고있음 = (await execute(BATCH_RUNNING_SQL, [new Date(now.getTime() - BATCH_RUNNING_WINDOW_MS).toISOString()])).rows.length > 0;
    if (돌고있음) return NextResponse.json({ busy: "일일 배치가 KIS모의투자에 주문을 내는 중입니다 — 끝난 뒤에 다시 조회해 주세요" });
    // 시장 상관없이 3초 — 국내·미국을 같은 초에 부르면 초당 한도에 걸린다
    const 모두 = await execute(LIVE_THROTTLE_SQL, [
      LIVE_ANY_KEY, JSON.stringify(now.toISOString()), now.toISOString(), new Date(now.getTime() - LIVE_ANY_GAP_MS).toISOString(),
    ]);
    if (모두.affectedRows === 0) return NextResponse.json({ wait: "방금 다른 시장을 조회했습니다 — 몇 초 뒤 다시 눌러 주세요" });
    // 같은 시장 30초 — 조건부 upsert 한 문장(원자적). 0 행이면 30초 안에 누가 이미 불렀다
    const 막힘 = await execute(LIVE_THROTTLE_SQL, [
      throttleKey(market), JSON.stringify(now.toISOString()), now.toISOString(), new Date(now.getTime() - LIVE_MIN_GAP_MS).toISOString(),
    ]);
    if (막힘.affectedRows === 0) return NextResponse.json({ wait: "30초 안에 다시 조회할 수 없습니다 — 모의 호출 한도가 낮습니다" });
    const 토큰 = rowsToObjects<{ token: string; expires_at: string }>(await execute(VTS_SAVED_ROW_SQL))[0];
    if (!토큰 || Date.parse(String(토큰.expires_at)) <= now.getTime())
      return NextResponse.json({ off: "KIS모의투자 토큰이 없습니다 — 다음 일일 배치가 받으면 조회됩니다" });
    const acct = vtsAccount()!;
    const 받은: unknown[] = [];
    let 더있음 = false;
    for (const [i, q] of liveRequests(market, acct, localDate(market, now)).entries()) {
      if (i) await new Promise((r) => setTimeout(r, LIVE_CALL_GAP_MS));
      const url = `${KIS_VTS_BASE}${q.path}?${new URLSearchParams(q.params).toString()}`;
      if (!vtsUrlOk(url, q.tr)) return NextResponse.json({ errors: ["KIS모의투자 조회 경로가 아닙니다"] }, { status: 500 });
      let payload: unknown;
      try {
        const r = await fetch(url, {
          headers: {
            "content-type": "application/json; charset=utf-8", authorization: `Bearer ${토큰.token}`,
            appkey: process.env.KIS_VTS_APP_KEY!, appsecret: process.env.KIS_VTS_APP_SECRET!, tr_id: q.tr, tr_cont: "", custtype: "P",
          },
          signal: AbortSignal.timeout(LIVE_TIMEOUT_MS),
          cache: "no-store",
          // 리디렉트를 따라가지 않는다 — 다른 호스트로 넘어가면 앱키·시크릿 머리글이 그대로 따라간다(25.1499, 검토 [참고] 6)
          redirect: "error",
        });
        payload = await r.json().catch(() => null);
        더있음 ||= ["M", "F"].includes(r.headers.get("tr_cont") ?? "");
      } catch (e) {
        // 예외 문장에는 요청 주소(`?CANO=…`)가 실린다 — 이름만
        return NextResponse.json({ errors: [`KIS모의투자 응답 없음 (${e instanceof Error ? e.name : "오류"})`] }, { status: 502 });
      }
      const 코드 = kisErrorCode(payload);
      if (코드) return NextResponse.json({ errors: [`KIS모의투자 조회 실패 ${코드}${코드 === "EGW00201" ? " — 초당 한도, 잠시 뒤 다시" : ""}`] }, { status: 502 });
      받은.push(payload);
    }
    const 시각 = new Date().toISOString();
    const 거래소수 = US_EXCHANGES.length;
    const live =
      market === "KR" ? parseKrLive(받은[0], 받은[1], 시각) : parseUsLive(받은.slice(0, 거래소수), 받은[거래소수], 받은[거래소수 + 1], 시각);
    return NextResponse.json({ live: 더있음 ? { ...live, more: true } : live });
  } catch (e) {
    return NextResponse.json({ errors: [`조회 실패 (${e instanceof Error ? e.name : "오류"})`] }, { status: 500 });
  }
}
