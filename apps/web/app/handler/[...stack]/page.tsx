import type { Metadata } from "next";
import { StackHandler } from "@stackframe/stack";

export const metadata: Metadata = {
  title: "Account",
  robots: { index: false, follow: false },
};

export default function Handler() {
  return <StackHandler fullPage />;
}
