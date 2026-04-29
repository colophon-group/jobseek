/**
 * Minimal root layout for `apps/murmur-shim`.
 *
 * The shim has no public UI surface — it exists purely to host the
 * Murmur API routes under `/api/murmur/**`. This layout only exists so
 * Next.js' router accepts the project as a valid app-dir build.
 */
export const metadata = {
  title: "murmur-shim",
  description: "Murmur webhook + subcommand routes (Hetzner side).",
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
