"use client";

import { useState } from "react";
import { Header } from "@/components/Header";
import { MobileMenu } from "@/components/MobileMenu";
import { CookieBanner } from "@/components/CookieBanner";

export function HeaderShell() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="sticky top-0 z-50 backdrop-blur-md">
      <Header onOpenMobileAction={() => setMobileOpen(true)} />
      <CookieBanner />
      <MobileMenu open={mobileOpen} onCloseAction={() => setMobileOpen(false)} />
    </div>
  );
}
