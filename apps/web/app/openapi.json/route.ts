import {
  discoveryRedirect,
  discoveryRedirectHead,
} from "@/lib/discovery-responses";

export function GET() {
  return discoveryRedirect("/api/openapi.json");
}

export function HEAD() {
  return discoveryRedirectHead("/api/openapi.json");
}
