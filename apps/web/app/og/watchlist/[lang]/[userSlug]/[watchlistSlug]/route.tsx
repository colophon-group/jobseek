import { notFound } from "next/navigation";

/** Legacy public watchlist previews are retired with public watchlist access. */
export function GET(): never {
  notFound();
}
