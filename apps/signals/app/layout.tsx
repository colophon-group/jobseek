import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Signals — Jobseek",
  description: "Hiring signal discovery and outreach",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
