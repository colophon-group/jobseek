"use client";

import { useState } from "react";
import { Header } from "@/components/Header";
import { MobileMenu } from "@/components/MobileMenu";

export function HeaderShell() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      <Header onOpenMobileAction={() => setMobileOpen(true)} />
      <MobileMenu open={mobileOpen} onCloseAction={() => setMobileOpen(false)} />
    </>
  );
}
