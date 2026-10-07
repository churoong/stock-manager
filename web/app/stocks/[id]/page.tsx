import { notFound } from "next/navigation";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import StockDetail from "@/components/StockDetail";
import { parseStockId } from "@/lib/stockDetail";

export const dynamic = "force-dynamic";

/**
 * 종목 상세 (docs/stock_detail.md, Step 10). 추천·포트폴리오·알림·스크리너에서 종목을 누르면 온다.
 */
export default async function StockPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ trade?: string; horizon?: string }>;
}) {
  const id = parseStockId((await params).id);
  if (!id) notFound();
  // 아침 리포트의 "매매 입력" 링크 (docs/infra.md 25.944): ?trade=buy&horizon=mid 로 오면 매매 폼을 그 기간으로 펼친다
  const q = await searchParams;
  const trade = q.trade === "buy" || q.trade === "sell" ? q.trade : undefined;
  const horizon = q.horizon === "short" || q.horizon === "mid" || q.horizon === "long" ? q.horizon : undefined;
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/stocks" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <StockDetail id={id} trade={trade} horizon={horizon} />
      </main>
      <Footer />
    </div>
  );
}
