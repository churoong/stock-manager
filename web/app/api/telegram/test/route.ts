import { NextResponse } from "next/server";

/**
 * 텔레그램 테스트 발송.
 *
 * 설정 화면에서 누르면 실제로 한 통 보낸다. 배치가 도는 날 아침에
 * 처음 알게 되는 것보다, 지금 확인하는 편이 낫다.
 *
 * **대화방 번호(`TELEGRAM_CHAT_ID`)가 비어 있으면 보내지 않는다** (docs/infra.md 25.584, 감사). 예전에는 `getUpdates` 로
 * 최근에 봇에게 말을 건 대화방을 골라 보냈다 — 테스트는 "보냈습니다" 인데 실제 장중 발송(`lib/telegram.sendTelegram`)은
 * 같은 조건에서 예외를 던져, **설정을 확인하는 버튼이 거꾸로 답했다.** 봇 이름이 저장소에 적혀 있어 모르는 사람에게 갈 수도 있었다
 * (25.392 는 이 동작을 배치에서만 없앴다). 이제 최근 대화방 번호는 **넣을 값의 후보로 알려 주기만** 한다.
 */


async function callTelegram(method: string, payload: unknown) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    throw new Error("환경변수 TELEGRAM_BOT_TOKEN 이 비어 있습니다");
  }

  const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });

  const body = (await response.json().catch(() => ({}))) as {
    ok?: boolean;
    description?: string;
    result?: unknown;
    parameters?: { retry_after?: number };
  };

  if (response.status === 429) {
    const wait = body.parameters?.retry_after ?? 5;
    throw new Error(`텔레그램 호출 제한입니다. ${wait}초 후 다시 시도하세요`);
  }
  if (!body.ok) {
    // description 에 토큰은 들어가지 않는다. 그대로 보여도 안전하다.
    throw new Error(body.description ?? `텔레그램 ${method} 실패`);
  }
  return body.result;
}

async function resolveChatId(): Promise<string> {
  const configured = process.env.TELEGRAM_CHAT_ID?.trim();
  if (configured) return configured;

  // 보내지 않는다. 최근 대화방 번호를 **후보로만** 알려 준다 — 장중 발송과 같은 조건에서 실패해야 테스트가 뜻이 있다
  let 후보 = "";
  try {
    const updates = (await callTelegram("getUpdates", { limit: 10 })) as Array<{
      message?: { chat?: { id?: number } };
      channel_post?: { chat?: { id?: number } };
    }>;
    for (const update of [...updates].reverse()) {
      const id = update.message?.chat?.id ?? update.channel_post?.chat?.id;
      if (id !== undefined) {
        후보 = String(id);
        break;
      }
    }
  } catch {
    // 후보를 못 찾아도 알릴 말은 같다
  }
  throw new Error(
    "환경변수 TELEGRAM_CHAT_ID 가 비어 있어 보내지 않았습니다 — 이대로면 장중 알림도 나가지 않습니다."
      + (후보 ? ` 최근 봇에게 말을 건 대화방 번호는 ${후보} 입니다. 본인 것이 맞으면 Vercel 환경변수에 넣으세요` : " 봇에게 말을 건 뒤 대화방 번호를 환경변수에 넣으세요"),
  );
}

export async function POST() {
  try {
    const chatId = await resolveChatId();
    const now = new Date().toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });

    await callTelegram("sendMessage", {
      chat_id: chatId,
      text: `설정 화면에서 보낸 테스트입니다.\n${now} KST`,
      disable_web_page_preview: true,
    });

    return NextResponse.json({ ok: true, message: "테스트 메시지를 보냈습니다" });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "발송에 실패했습니다" },
      { status: 500 },
    );
  }
}
