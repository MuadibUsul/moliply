"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AdminShell } from "@/components/admin/AdminShell";
import { getAdminToken } from "@/lib/api";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const isLogin = pathname === "/admin/login";

  useEffect(() => {
    if (isLogin) {
      setReady(true);
      return;
    }
    if (!getAdminToken()) {
      router.replace("/admin/login");
      return;
    }
    setReady(true);
  }, [isLogin, router]);

  if (isLogin) return <>{children}</>;

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#05090f]">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-brand-400" />
          正在验证登录状态...
        </div>
      </div>
    );
  }

  return <AdminShell>{children}</AdminShell>;
}
