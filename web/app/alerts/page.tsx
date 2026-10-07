import AlertCenter from "@/components/AlertCenter";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/** 알림 센터 · 관심 종목 (docs/intraday.md, Step 14). */
export default function AlertsPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/alerts" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="알림" doc="docs/intraday.md">
          정규장 동안 5분마다 보유·관심·오늘 추천 종목을 확인해 텔레그램으로 알립니다. 같은 종목·같은 사유는
          하루 한 번입니다. 시세는 지연 시세이고, 알림은 판단 정보일 뿐 점수나 매매를 바꾸지 않습니다.
        </PageHeader>
        <AlertCenter />
      </main>
      <Footer />
    </div>
  );
}
