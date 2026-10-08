package worker

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func pdfFingerprintSource(source string) (string, string, error) {
	u, err := url.Parse(source)
	if err != nil {
		return "", "", errPDFBinary
	}
	u.Fragment = ""
	fields := strings.Split(u.RawQuery, "&")
	token := ""
	for i, field := range fields {
		key, _, _ := strings.Cut(field, "=")
		decoded, err := url.QueryUnescape(key)
		if err != nil {
			return "", "", errPDFBinary
		}
		if decoded != "_jobseek_fp" {
			continue
		}
		if key != "_jobseek_fp" || i != len(fields)-1 || token != "" || !strings.HasPrefix(field, "_jobseek_fp=") {
			return "", "", errPDFBinary
		}
		token = strings.TrimPrefix(field, "_jobseek_fp=")
		if !regexp.MustCompile(`^[0-9a-f]{24}$`).MatchString(token) {
			return "", "", errPDFBinary
		}
		u.RawQuery = strings.Join(fields[:i], "&")
	}
	if len(fields) > 100 {
		return "", "", errPDFBinary
	}
	return u.String(), token, nil
}

func pdfSameOrigin(a, b *url.URL) bool {
	port := func(u *url.URL) string {
		if u.Port() != "" {
			return u.Port()
		}
		if u.Scheme == "https" {
			return "443"
		}
		return "80"
	}
	return strings.EqualFold(a.Scheme, b.Scheme) && strings.EqualFold(a.Hostname(), b.Hostname()) && port(a) == port(b)
}

func pdfVerifyFingerprint(source, token string, r *http.Response, body []byte) error {
	encoding := strings.TrimSpace(r.Header.Get("Content-Encoding"))
	if encoding != "" && !strings.EqualFold(encoding, "identity") {
		return errPDFBinary
	}
	media := strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]))
	if media != "application/pdf" {
		return errPDFBinary
	}
	etag := r.Header.Get("ETag")
	if len(etag) < 10 || len(etag) > 512 || etag[0] != '"' || etag[len(etag)-1] != '"' || strings.Contains(etag[1:len(etag)-1], "\"") {
		return errPDFBinary
	}
	for _, c := range etag {
		if c < 0x20 || c == 0x7f {
			return errPDFBinary
		}
	}
	modified, err := http.ParseTime(r.Header.Get("Last-Modified"))
	if err != nil {
		return errPDFBinary
	}
	length, err := strconv.ParseInt(r.Header.Get("Content-Length"), 10, 64)
	if err != nil || length < 1 || length > 20<<20 || length != int64(len(body)) {
		return errPDFBinary
	}
	payload := strings.Join([]string{source, etag, modified.UTC().Format("2006-01-02T15:04:05+00:00"), strconv.FormatInt(length, 10), media}, "\n")
	actual := fmt.Sprintf("%x", sha256.Sum256([]byte(payload)))[:24]
	if subtle.ConstantTimeCompare([]byte(actual), []byte(token)) != 1 {
		return errPDFBinary
	}
	return nil
}

func fetchPDFDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	o, err := api.PDFOptionsFromConfig(p.PDFConfig)
	if err != nil || client == nil || p.Profile != "pdf.public-detail/v1" || p.Endpoint != p.SourceURL {
		return nil, nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if len(o.Headers) > 0 {
		sealed.Jar = nil
	}
	client = &sealed
	base, token, err := pdfFingerprintSource(p.SourceURL)
	if err != nil {
		return nil, nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 240*time.Second)
	defer cancel()
	initial, err := url.Parse(p.SourceURL)
	if err != nil {
		return nil, nil, errPDFBinary
	}
	initial.Fragment = ""
	current := initial.String()
	maxHops := 20
	if token != "" || len(o.Headers) > 0 {
		maxHops = 5
	}
	for hop := 0; hop <= maxHops; hop++ {
		u, err := url.Parse(current)
		if err != nil || u.User != nil || u.Fragment != "" || len(current) > 8192 || u.Scheme != "https" && u.Scheme != "http" || (maxHops == 5 && !pdfSameOrigin(initial, u)) {
			return nil, nil, errPDFBinary
		}
		requestCtx, cancelRequest := context.WithTimeout(ctx, 30*time.Second)
		request, err := http.NewRequestWithContext(requestCtx, "GET", current, nil)
		if err != nil {
			cancelRequest()
			return nil, nil, errPDFBinary
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		if token != "" {
			request.Header.Set("Accept-Encoding", "identity")
		}
		for k, v := range o.Headers {
			request.Header.Set(k, v)
		}
		r, err := client.Do(request)
		if err != nil {
			cancelRequest()
			if ctx.Err() != nil {
				return nil, nil, ctx.Err()
			}
			return nil, nil, errPDFBinary
		}
		reservation, policyURL := r.Header.Get("TDM-Reservation"), r.Header.Get("TDM-Policy")
		if failure := policy.Check(&runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}, "", current); failure != nil {
			r.Body.Close()
			cancelRequest()
			if reserved, ok := failure.(*policy.Reservation); ok {
				return nil, reserved, nil
			}
			return nil, nil, failure
		}
		redirect := r.StatusCode == 301 || r.StatusCode == 302 || r.StatusCode == 307 || r.StatusCode == 308 || maxHops == 20 && r.StatusCode == 303
		if redirect {
			locations := r.Header.Values("Location")
			target, e := r.Location()
			r.Body.Close()
			cancelRequest()
			if hop == maxHops || len(locations) != 1 || e != nil {
				return nil, nil, errPDFBinary
			}
			target.Fragment = ""
			current = target.String()
			continue
		}
		if r.StatusCode < 200 || r.StatusCode >= 300 {
			status := r.StatusCode
			r.Body.Close()
			cancelRequest()
			return nil, nil, &executor.NavigationHTTPError{RequestedURL: p.SourceURL, ResponseURL: current, Status: uint32(status)}
		}
		limit := int64(40 << 20)
		if token != "" {
			limit = 20 << 20
		}
		if r.ContentLength > limit {
			r.Body.Close()
			cancelRequest()
			return nil, nil, errPDFBinary
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
		r.Body.Close()
		cancelRequest()
		if err != nil || int64(len(body)) > limit || !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF")) {
			return nil, nil, errPDFBinary
		}
		if token != "" {
			if err := pdfVerifyFingerprint(base, token, r, body); err != nil {
				return nil, nil, err
			}
		}
		content, err := extractPDFBinary(ctx, body, p.SourceURL, o)
		return content, nil, err
	}
	return nil, nil, errPDFBinary
}
