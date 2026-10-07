import Icon from "@/components/Icon";

/**
 * 화면 제목과 설명 (docs/pwa.md 3.2).
 *
 * **폰에서는 본문 제목을 숨긴다.** 위쪽 제목줄(Nav)이 이미 화면 이름을 보여 준다. 두 번 나오면
 * 내용만 아래로 밀린다(2026-09-18 사용자 "화면이 너무 길다"). 설명도 폰에서는 접어 두고
 * "이 화면은" 을 눌러야 펼친다. 넓은 화면은 전과 같다.
 *
 * `doc` 은 규칙 문서 경로다. 넓은 화면에만 적는다 — 폰에서는 열 수도 없는 파일 경로가 자리만 차지했다.
 */
export default function PageHeader({
  title,
  doc,
  children,
}: {
  title: string;
  doc?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-3 sm:mb-4">
      <h1 className="mb-1 hidden text-base font-semibold sm:block">{title}</h1>
      {children ? (
        <>
          <p className="hidden text-xs leading-relaxed text-slate-500 sm:block dark:text-slate-400">
            {children}
            {doc ? (
              <>
                {" "}규칙:{" "}
                <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">{doc}</code>
              </>
            ) : null}
          </p>
          <details className="group sm:hidden">
            <summary className="-ml-1 inline-flex min-h-11 cursor-pointer list-none items-center gap-1 px-1 text-xs text-slate-500 dark:text-slate-400 [&::-webkit-details-marker]:hidden">
              <Icon name="info" className="h-4 w-4" />
              이 화면은
            </summary>
            <p className="mb-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{children}</p>
          </details>
        </>
      ) : null}
    </div>
  );
}
