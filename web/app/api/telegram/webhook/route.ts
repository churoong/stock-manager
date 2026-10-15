import { NextResponse } from "next/server";
import { execute } from "@/lib/db";
import { tokenMatches } from "@/lib/intraday";
import { sendTelegram } from "@/lib/telegram";
import { ANSWER_TIMEOUT_MS, answer, parseQuery, withTimeout } from "@/lib/telegramQa";

/**
 * 텔레그램 질의응답 웹훅 (docs/telegram-qa.md, docs/infra.md 25.1004).
 *
 * 텔레그램이 부르는 경로라 로그인 쿠키가 없다 — `proxy.ts` 가 이 경로 하나만 열어 둔다. 대신 두 겹으로 지킨다.
 * 1. 머리글 `X-Telegram-Bot-Api-Secret-Token` 이 `TELEGRAM_WEBHOOK_SECRET` 와 같아야 한다(웹훅을 걸 때 텔레그램에 준 값)
 * 2. 말이 본인 대화방(`TELEGRAM_CHAT_ID`)에서 와야 한다. 다른 대화방의 말에는 답하지 않는다(데이터 제3자 제공 금지)
 * 비밀값이 없으면 경로 자체가 없는 것처럼 404. **언제나 200 으로 끝낸다** — 아니면 텔레그램이 같은 말을 계속 다시 보낸다.
 */
/**
 * 함수 실행 상한을 적어 둔다 (docs/infra.md 25.1387, 텔레그램 감사). 답 만들기 상한 8초(`ANSWER_TIMEOUT_MS`) + 발송 제한
 * 10초(`sendTelegram`)면 18초를 넘을 수 있는데, 선언이 없으면 플랫폼 기본 상한에 맡겨진다 [확인필요: Vercel 무료 플랜 기본값].
 * 그 전에 죽으면 200 이 없어 텔레그램이 같은 말을 다시 보낸다(25.1133 이 막으려던 모양). 장중·무응답 경로와 같은 30초
 */
export const maxDuration = 30;

export async function POST(request: Request) {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET?.trim();
  const chatId = process.env.TELEGRAM_CHAT_ID?.trim();
  if (!secret || !chatId) return NextResponse.json({ error: "not found" }, { status: 404 });
  if (!tokenMatches(request.headers.get("x-telegram-bot-api-secret-token"), secret)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const update = (await request.json().catch(() => null)) as {
    message?: { text?: string; chat?: { id?: number | string; type?: string } };
  } | null;
  const message = update?.message;
  if (!message?.text || String(message.chat?.id ?? "") !== chatId) return NextResponse.json({ ok: true });
  // **개인 대화방에만 답한다** (25.1007, 교차검증). `TELEGRAM_CHAT_ID` 가 단체방이면 그 방 누구나 /보유 로 손익·비중을 받는다
  if (message.chat?.type !== "private") return NextResponse.json({ ok: true });
  const query = parseQuery(message.text);
  if (!query) return NextResponse.json({ ok: true });
  let text: string;
  try {
    // **DB 가 답하지 않아도 제때 200 으로 끝낸다** (25.1133, 텔레그램 감사 재현). `execute` 의 fetch 에는 시간 제한이 없어 함수가
    // 시간 초과로 죽으면 텔레그램이 같은 말을 다시 보내고, DB 가 살아난 뒤 답이 늦게 여러 번 갔다
    text = await withTimeout(answer(query, execute), ANSWER_TIMEOUT_MS);
  } catch (error) {
    text = `답을 만들지 못했습니다 — DB 를 읽지 못했습니다 (${error instanceof Error ? error.message.slice(0, 120) : "알 수 없음"})`;
  }
  try {
    await sendTelegram(text);
  } catch {
    // 보내지 못해도 200 — 다시 받아도 같은 실패다
  }
  return NextResponse.json({ ok: true });
}
