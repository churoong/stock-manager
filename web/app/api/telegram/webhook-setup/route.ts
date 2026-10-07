import { NextResponse } from "next/server";

/**
 * 텔레그램 질의응답 켜기·끄기 (docs/telegram-qa.md, 25.1004). 설정 화면의 단추가 부른다(로그인 뒤).
 *
 * 켜기 = 텔레그램에 `setWebhook`(이 앱의 `/api/telegram/webhook`, 비밀값 `TELEGRAM_WEBHOOK_SECRET`, 말만 받기).
 * 끄기 = `deleteWebhook`. 주소는 지금 요청이 들어온 출처로 만든다 — 저장소·문서에 웹앱 주소를 적지 않는다.
 * 웹훅이 걸려 있으면 텔레그램 `getUpdates` 는 쓸 수 없다(테스트 발송의 "대화방 번호 후보" 안내가 비게 된다).
 */
export async function POST(request: Request) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET?.trim();
  if (!token) return NextResponse.json({ error: "TELEGRAM_BOT_TOKEN 이 비어 있습니다" }, { status: 400 });
  const body = (await request.json().catch(() => ({}))) as { action?: string };
  const on = body.action !== "off";
  if (on && (!secret || secret.length < 16 || !/^[A-Za-z0-9_-]+$/.test(secret))) {
    return NextResponse.json(
      { error: "Vercel 환경변수 TELEGRAM_WEBHOOK_SECRET 에 16자 이상 영문·숫자·_·- 값을 넣고 다시 배포하세요" },
      { status: 400 },
    );
  }
  const url = `${new URL(request.url).origin}/api/telegram/webhook`;
  const [method, payload] = on
    ? ["setWebhook", { url, secret_token: secret, allowed_updates: ["message"], drop_pending_updates: true }]
    : ["deleteWebhook", { drop_pending_updates: true }];
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    const r = (await response.json().catch(() => ({}))) as { ok?: boolean; description?: string };
    // description 에 토큰은 들어가지 않는다
    if (!r.ok) return NextResponse.json({ error: r.description ?? `텔레그램 HTTP ${response.status}` }, { status: 502 });
    return NextResponse.json({
      message: on ? "켰습니다 — 봇에게 종목 이름을 보내 보세요" : "껐습니다 — 봇이 더는 답하지 않습니다",
    });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "텔레그램에 연결하지 못했습니다" }, { status: 502 });
  }
}
