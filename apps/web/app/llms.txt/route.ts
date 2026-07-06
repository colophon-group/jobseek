import {
  discoveryRedirect,
  discoveryRedirectHead,
} from "@/lib/discovery-responses";

export function GET() {
  return discoveryRedirect("/.well-known/llms.txt");
}

export function HEAD() {
  return discoveryRedirectHead("/.well-known/llms.txt");
}
