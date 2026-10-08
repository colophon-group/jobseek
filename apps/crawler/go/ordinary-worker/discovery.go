package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

const ordinaryUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
const ordinaryAccept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

// DiscoveryError retains a bounded symbolic outcome, never the upstream body,
// URL, headers or transport exception. Context cancellation remains unwrap-able.
type DiscoveryError struct {
	Kind   string
	Status int
	cause  error
}

func (e *DiscoveryError) Error() string {
	if e.Status != 0 {
		return "Greenhouse discovery " + e.Kind + " (HTTP " + strconv.Itoa(e.Status) + ")"
	}
	return "Greenhouse discovery " + e.Kind
}

func (e *DiscoveryError) Unwrap() error { return e.cause }

// GreenhouseResponse observes the final response from a sealed-client fetch.
// Successful inventories require complete bodies; RSS publisher headers may
// stop the fetch before reading a body. Its private
// fields prevent a caller from changing the resource that emitted a signal.
// This observation is not claim/write authority. RunGreenhouseClaim consumes
// only its own completed sealed-client response under an installed opaque claim.
type GreenhouseResponse struct {
	endpoint, finalURL string
	location           string
	status, bytes      int
	contentType        string
	reserved           bool
	providerDisabled   bool
	reservationSource  string
	policy             *string
	domVerification    *domVerificationBinding
}

func (r *GreenhouseResponse) Endpoint() string { return r.endpoint }
func (r *GreenhouseResponse) FinalURL() string { return r.finalURL }
func (r *GreenhouseResponse) Status() int      { return r.status }
func (r *GreenhouseResponse) Bytes() int       { return r.bytes }
func (r *GreenhouseResponse) Reserved() bool   { return r.reserved }
func (r *GreenhouseResponse) PolicyURL() *string {
	if r.policy == nil {
		return nil
	}
	value := *r.policy
	return &value
}

type GreenhouseDiscovery struct {
	Inventory greenhouse.Inventory
	Response  *GreenhouseResponse
}

// DiscoverGreenhouse performs the ordinary monitor's one logical GET using a
// process-owned client. The client owns verified transport, per-operation
// deadlines, SSRF on every hop, redirects, cookies, pooling and egress meters;
// this adapter does not substitute a per-request pilot client or add retries.
// Complete reading, publisher checks, status checks and parsing precede any
// inventory delivery. The inherited 64 MiB native bound is an admission limit.
func DiscoverGreenhouse(ctx context.Context, client *http.Client, token string) (GreenhouseDiscovery, error) {
	var result GreenhouseDiscovery
	endpoint, err := greenhouse.TokenURL(token)
	if err != nil || client == nil {
		return result, &DiscoveryError{Kind: "invalid_configuration"}
	}
	if err := ctx.Err(); err != nil {
		return result, &DiscoveryError{Kind: "request_failed", cause: err}
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_configuration"}
	}
	request.Header.Set("User-Agent", ordinaryUserAgent)
	request.Header.Set("Accept", ordinaryAccept)
	response, err := client.Do(request)
	if err != nil {
		// A refused redirect may return both response and error. net/http has
		// already closed its body; it is not a completed monitor response.
		return result, &DiscoveryError{Kind: "request_failed", cause: ctx.Err()}
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, greenhouse.MaxBodyBytes+1))
	if err != nil {
		return result, &DiscoveryError{Kind: "body_failed", cause: ctx.Err()}
	}
	if len(body) > greenhouse.MaxBodyBytes {
		return result, &DiscoveryError{Kind: "body_limit"}
	}
	if err := ctx.Err(); err != nil {
		return result, &DiscoveryError{Kind: "body_failed", cause: err}
	}
	if response.Request == nil || response.Request.URL == nil {
		return result, &DiscoveryError{Kind: "invalid_response"}
	}
	reservation, policy := greenhouseHeaders(response.Header)
	observed := &GreenhouseResponse{endpoint: endpoint, finalURL: response.Request.URL.String(), status: response.StatusCode, bytes: len(body), reserved: reservation == "1", policy: policy}
	result.Response = observed
	if observed.reserved {
		return result, &DiscoveryError{Kind: "publisher_reserved", Status: response.StatusCode}
	}
	if response.StatusCode == http.StatusNotFound {
		return result, &DiscoveryError{Kind: "provider_gone", Status: response.StatusCode}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return result, &DiscoveryError{Kind: "http_status", Status: response.StatusCode}
	}
	body, err = greenhouseJSONBytes(body)
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", Status: response.StatusCode}
	}
	inventory, err := greenhouse.Parse(body)
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", Status: response.StatusCode}
	}
	if err := ctx.Err(); err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", cause: err}
	}
	result.Inventory = inventory
	return result, nil
}

// httpx decodes the entire header set as ASCII, UTF-8, or Latin-1, then joins
// repeated values with ", ". Duplicate literal flags must not collapse to 1.
func greenhouseHeaders(headers http.Header) (string, *string) {
	latin1 := false
	for name, values := range headers {
		if !utf8.ValidString(name) {
			latin1 = true
		}
		for _, value := range values {
			if !utf8.ValidString(value) {
				latin1 = true
			}
		}
	}
	decode := func(name string) string {
		value := strings.Join(headers.Values(name), ", ")
		if latin1 {
			var decoded strings.Builder
			for _, octet := range []byte(value) {
				decoded.WriteRune(rune(octet))
			}
			value = decoded.String()
		}
		return value
	}
	reservation := strings.TrimFunc(decode("Tdm-Reservation"), func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	policy := decode("Tdm-Policy")
	if policy == "" {
		return reservation, nil
	}
	return reservation, &policy
}

var errJSONEncoding = errors.New("invalid Greenhouse JSON encoding")

// Python response.json loads bytes, ignoring Content-Type's charset. Match
// JSON's BOM/zero-byte UTF-8/16/32 detection and strict Unicode decoding;
// encoding/json alone silently replaces invalid raw UTF-8.
func greenhouseJSONBytes(body []byte) ([]byte, error) {
	width, little, skip := 1, false, 0
	if len(body) >= 4 {
		switch {
		case string(body[:4]) == "\x00\x00\xfe\xff":
			width, skip = 4, 4
		case string(body[:4]) == "\xff\xfe\x00\x00":
			width, little, skip = 4, true, 4
		case body[0] == 0 && body[1] == 0 && body[2] == 0:
			width = 4
		case body[1] == 0 && body[2] == 0 && body[3] == 0:
			width, little = 4, true
		}
	}
	if width == 1 && len(body) >= 2 {
		switch {
		case string(body[:2]) == "\xfe\xff":
			width, skip = 2, 2
		case string(body[:2]) == "\xff\xfe":
			width, little, skip = 2, true, 2
		case body[0] == 0 && (len(body) < 4 || body[2] == 0):
			width = 2
		case body[1] == 0 && (len(body) < 4 || body[3] == 0):
			width, little = 2, true
		}
	}
	if width == 1 {
		if len(body) >= 3 && string(body[:3]) == "\xef\xbb\xbf" {
			body = body[3:]
		}
		if !utf8.Valid(body) {
			return nil, errJSONEncoding
		}
		return body, nil
	}
	body = body[skip:]
	if len(body)%width != 0 {
		return nil, errJSONEncoding
	}
	unit := func(i int) uint32 {
		var value uint32
		for j := 0; j < width; j++ {
			position := j
			if little {
				position = width - j - 1
			}
			value = value<<8 | uint32(body[i+position])
		}
		return value
	}
	decoded := make([]byte, 0, len(body))
	for i := 0; i < len(body); i += width {
		value := unit(i)
		if width == 2 && value >= 0xd800 && value <= 0xdbff {
			if i+width >= len(body) {
				return nil, errJSONEncoding
			}
			low := unit(i + width)
			if low < 0xdc00 || low > 0xdfff {
				return nil, errJSONEncoding
			}
			value = 0x10000 + (value-0xd800)*0x400 + low - 0xdc00
			i += width
		}
		if value > utf8.MaxRune || value >= 0xd800 && value <= 0xdfff {
			return nil, errJSONEncoding
		}
		decoded = utf8.AppendRune(decoded, rune(value))
	}
	return decoded, nil
}
