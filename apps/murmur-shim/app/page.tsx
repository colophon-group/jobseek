/**
 * Root page placeholder for `apps/murmur-shim`.
 *
 * The shim has no public UI; this page exists only so Next.js builds
 * cleanly. Operators land here on the bare host while diagnosing.
 */
export default function Home() {
  return (
    <main>
      <h1>murmur-shim</h1>
      <p>Murmur subcommand and webhook routes live under /api/murmur.</p>
    </main>
  );
}
