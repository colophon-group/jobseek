import type { Metadata } from "next";
import type { ReactNode } from "react";
import { ThemeProvider } from "next-themes";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { MuiThemeProvider } from "@/components/ThemeProvider";
import { siteConfig } from "@/content/config";
import "./globals.css";

export const metadata: Metadata = {
  title: { template: "%s | Job Seek", default: "Job Seek" },
  metadataBase: new URL(siteConfig.url),
  openGraph: {
    type: "website",
    siteName: "Job Seek",
    locale: "en",
  },
  twitter: { card: "summary" },
};

/**
 * Inline script that sets <html lang> from the URL pathname before first paint.
 * Same pattern next-themes uses for the `class` attribute.
 * Keeps the root layout free of cookies()/headers() so public pages can be
 * statically generated.
 */
const langScript = `try{var l=location.pathname.split('/')[1];if(['en','de','fr','it'].includes(l))document.documentElement.lang=l}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preload" href="/fonts/JetBrainsMono-Medium.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
        <link rel="preload" href="/fonts/JetBrainsMono-SemiBold.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
        <link rel="preload" href="/fonts/JetBrainsMono-Bold.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
        <script dangerouslySetInnerHTML={{ __html: langScript }} />
      </head>
      <body>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
          <MuiThemeProvider>
            {children}
          </MuiThemeProvider>
        </ThemeProvider>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
