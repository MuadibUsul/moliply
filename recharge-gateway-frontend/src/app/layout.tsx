import type { Metadata, Viewport } from "next";
import "./globals.css";
import { BRAND } from "@/config/brand";

export const metadata: Metadata = {
  metadataBase: new URL(BRAND.storeUrl),
  title: {
    default: `${BRAND.nameEn} ${BRAND.nameCn} · ${BRAND.subtitle}`,
    template: `%s · ${BRAND.nameEn} ${BRAND.nameCn}`,
  },
  description: BRAND.description,
  keywords: [
    "MOLIPLY",
    "模力补给",
    "AI 会员",
    "软件订阅",
    "数字商品",
    "ChatGPT Plus",
    "Claude Pro",
    "自助充能",
  ],
  applicationName: BRAND.nameEn,
  authors: [{ name: BRAND.nameEn }],
  openGraph: {
    title: `${BRAND.nameEn} ${BRAND.nameCn}`,
    description: BRAND.description,
    siteName: `${BRAND.nameEn} ${BRAND.nameCn}`,
    type: "website",
    locale: "zh_CN",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: "#05080F",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark">
      <body>{children}</body>
    </html>
  );
}
