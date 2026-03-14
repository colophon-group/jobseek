import "server-only";

export function matchesBasicAuthorization(
  authorization: string | null,
  expectedToken: string | undefined,
): boolean {
  if (!authorization || !expectedToken) return false;
  const [scheme, token] = authorization.split(" ", 2);
  return scheme === "Basic" && token === expectedToken;
}
