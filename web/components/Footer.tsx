import Link from "next/link";

/**
 * 모든 화면 하단에 붙는 고지.
 *
 * 투자 책임 고지는 기준 문서의 절대 규칙이다.
 *
 * FRED 고지는 이용약관상 의무다. **지금 FRED 를 부르는 코드는 없다**
 * (2026-09-23 확인, docs/infra.md 25.160 — 무위험수익률은 설정 화면 수동 입력).
 * 그래도 문구를 미리 두는 쪽을 골랐다: 붙이는 날 이 줄을 기억할 사람이 없다.
 *
 * 시스템 상태 링크가 여기 있는 이유: 무언가 이상할 때만 여는 화면이라 넓은 화면의 위쪽 메뉴를
 * 늘리지 않았다 (docs/health.md 4장). 폰에서는 **더보기**가 이 화면들로 가는 길이다
 * (2026-09-18 — 전에는 폰에서 백테스트·일일 리포트도 이 고지 옆 작은 링크로만 갔다).
 */

import { DISCLAIMER } from "@/lib/notice";
export default function Footer() {
  return (
    <footer className="mt-16 border-t border-slate-200 px-4 py-6 text-xs leading-relaxed text-slate-500 dark:border-slate-800 dark:text-slate-400">
      <p className="font-medium text-slate-700 dark:text-slate-300">{DISCLAIMER}</p>
      <p className="mt-2">
        This product uses the FRED® API but is not endorsed or certified by the
        Federal Reserve Bank of St. Louis.
      </p>
      <p className="mt-1">
        시세·재무 데이터 출처는 한국거래소, 금융감독원 전자공시, 야후 파이낸스입니다.
        개인 참고용이며 외부에 제공할 수 없습니다.
      </p>
      <p className="mt-2 hidden gap-3 sm:flex">
        <Link href="/status" className="underline">
          시스템 상태
        </Link>
      </p>
    </footer>
  );
}
