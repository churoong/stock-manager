"use client";

import { useRouter } from "next/navigation";
import Icon from "@/components/Icon";

/**
 * 폰 위쪽 제목줄의 뒤로 단추 (docs/pwa.md 3.2).
 *
 * 홈 화면에 설치한 앱에는 브라우저의 뒤로 단추가 없다. 종목 상세에서 목록으로 돌아갈 길이
 * 아래 막대뿐이었다. 들어온 길이 있으면 그리로 가고, 없으면(알림을 눌러 바로 열었을 때) fallback 으로 간다.
 */
export default function BackButton({ fallback }: { fallback: string }) {
  const router = useRouter();
  return (
    <button
      type="button"
      aria-label="뒤로"
      onClick={() => {
        if (window.history.length > 1) router.back();
        else router.push(fallback);
      }}
      className="-ml-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-700 active:bg-slate-100 dark:text-slate-200 dark:active:bg-slate-800"
    >
      <Icon name="back" className="h-6 w-6" />
    </button>
  );
}
