const DISCOVERY_HEADERS = {
  "Cache-Control": "public, max-age=0, must-revalidate",
  "X-Robots-Tag": "noindex",
} as const;

export function discoveryNotFound(): Response {
  return new Response("Not found\n", {
    status: 404,
    headers: {
      ...DISCOVERY_HEADERS,
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

export function discoveryNotFoundHead(): Response {
  return new Response(null, {
    status: 404,
    headers: DISCOVERY_HEADERS,
  });
}

export function discoveryRedirect(pathname: string): Response {
  return new Response("Redirecting\n", {
    status: 308,
    headers: {
      ...DISCOVERY_HEADERS,
      "Content-Type": "text/plain; charset=utf-8",
      Location: pathname,
    },
  });
}

export function discoveryRedirectHead(pathname: string): Response {
  return new Response(null, {
    status: 308,
    headers: {
      ...DISCOVERY_HEADERS,
      Location: pathname,
    },
  });
}
