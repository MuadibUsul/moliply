import { CartProvider } from "@/lib/cart";
import { CartToast } from "@/components/CartToast";
import { MobileTabBar } from "@/components/MobileTabBar";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";

export default function StoreLayout({ children }: { children: React.ReactNode }) {
  return (
    <CartProvider>
      <div className="flex min-h-screen flex-col">
        <SiteHeader />
        <main className="flex-1 pb-20 lg:pb-0">{children}</main>
        <SiteFooter />
        <MobileTabBar />
        <CartToast />
      </div>
    </CartProvider>
  );
}
