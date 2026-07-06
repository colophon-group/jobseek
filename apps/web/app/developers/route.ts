import { discoveryNotFound, discoveryNotFoundHead } from "@/lib/discovery-responses";

export function GET() {
  return discoveryNotFound();
}

export function HEAD() {
  return discoveryNotFoundHead();
}

