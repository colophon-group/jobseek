# Native DOM extraction

`dom-detail-parse` reads one JSON request from stdin and emits the canonical
`JobContent` object. It has no HTTP or browser transport and never contacts an
origin. `mode=parse` accepts `html`, `config`, and an optional canonical `url`.
The `flatten` and `walk` modes support offline equivalence checks.

The extractor ports Python flattening and steps, CSS scope, Elementor careers
presets, Unicode punctuation matching, capture/lookaround regexes, explicit
input dates, fragment start, default precedence and field mapping. Regexes use a
bounded backtracking stack and a two-second match timeout. Inputs and the
parent-side output reader are bounded at 64 MiB. The async caller reaps children
on failure, cancellation and its 45-second deadline.

`DOM_GO_PARSE_ENABLED=1` enables the installed binary by default. Setting it to
`0` restores reference Python extraction. The caller still owns HTTP/browser
transport, publisher policy, retries, linked descriptions and PDF/DOCX fallbacks.
The current B0 Lightpanda reservation contract remains JSON-LD; admitting rendered
DOM profiles is a separate runtime migration step. This extractor does not imply
those stages or the entire crawler run in Go.

Normal scheduled parsing retains at most eight already fetched inputs per worker,
each at most 2 MiB, in exclusive mode-0600 `/tmp/jobseek-dom-go-parse-N.json` files.
This adds no publisher traffic. Preserve useful captures in the private durable
operator evidence directory before replacing the container. Pure preview and
linked-HTML parsing do not produce these captures.

`testdata/python_cases.json` contains frozen canonical outputs from the existing
Python extraction and DOM regression suites, all twelve current production date
formats, and raw-fragment/URL-default examples. `collect_python.py` regenerates
them offline with reference extraction selected. `verify_installed.py` checks
the installed binary in a networkless container.

```sh
go test -race ./...
go vet ./...
go mod tidy -diff
```

## Verified direct HTTP transport

`dom-detail-fetch` reads a bounded URL/options request on stdin and returns the
exact response bytes, content type, status/final URL and request accounting.
Its public document transport reuses the existing native JSON-LD DNS/IP checks,
TDM metadata parser, default public headers and body-idle bounds. It preserves
configured status retry counts, default Avature 406 retries, cookie-aware
same-origin redirects, and the separate five-hop/no-cookie/no-status-retry
contract used with configured public headers. Other redirects retain the
20-hop bound. Each response is bounded at 16 MiB; a fetch task at 10 minutes.

The DOM caller selects native fetch by default (`DOM_GO_HTTP_ENABLED=1`) only
when both config and the effective caller client use verified direct HTTP.
Proxy, insecure TLS, rendering/actions and custom/logging clients retain their
configured transport. No enabled configuration is disabled. Exact raw bytes
preserve configured codecs and PDF/DOCX detection. The existing caller still
owns decoding, gone/challenge classification, document conversion and linked
fetch; normal DOM extraction remains native. Fetch metrics use
`stage=fetch, implementation=go-dom-http`; they do not claim full scrape
ownership or whole-lane resource improvements. Reversal restores the preceding
runtime release through the supported cold procedure.
