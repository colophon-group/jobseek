package jsonld

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"
)

// FetchLinkedDocumentWithClient applies the code-owned linked-description
// contract. The caller validates each HTTPS target before any request, including
// redirects. Configured main-document headers are intentionally not forwarded.
func FetchLinkedDocumentWithClient(ctx context.Context, endpoint string, validate func(string) error, client *http.Client) (DocumentResult, error) {
	return fetchLinkedDocument(ctx, endpoint, validate, client, sleep)
}
func fetchLinkedDocument(ctx context.Context, endpoint string, validate func(string) error, client *http.Client, wait sleepFunc) (DocumentResult, error) {
	result := DocumentResult{}
	if client == nil || validate == nil {
		return result, errors.New("missing linked document transport")
	}
	isolated := *client
	isolated.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	visited := map[string]bool{endpoint: true}
	for hop := 0; hop <= 10; hop++ {
		if len(endpoint) > 8192 || !validEndpoint(endpoint) || validate(endpoint) != nil {
			return result, errors.New("invalid linked document endpoint")
		}
		var response *http.Response
		var body []byte
		for attempt := 0; ; attempt++ {
			request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
			if err != nil {
				return result, err
			}
			request.Header.Set("User-Agent", userAgent)
			request.Header.Set("Accept", accept)
			result.Requests++
			response, err = isolated.Do(request)
			if err != nil {
				return result, err
			}
			result.Responses++
			result.Status = response.StatusCode
			result.FinalURL = endpoint
			result.ContentType = response.Header.Get("Content-Type")
			body, err = io.ReadAll(io.LimitReader(&readIdleBody{ReadCloser: response.Body, timeout: 30 * time.Second}, maxResponseBytes+1))
			response.Body.Close()
			result.Bytes += len(body)
			if err != nil {
				return result, err
			}
			if len(body) > maxResponseBytes {
				return result, errors.New("linked document exceeds 16 MiB")
			}
			retry := result.Status == 429 || result.Status == 500 || result.Status == 502 || result.Status == 503 || result.Status == 504
			if !retry || attempt >= 2 {
				break
			}
			if err = wait(ctx, time.Duration(float64(time.Duration(1<<attempt)*500*time.Millisecond)*(0.5+rand.Float64()))); err != nil {
				return result, err
			}
		}
		status := result.Status
		if status >= 300 && status < 400 {
			if status != 301 && status != 302 && status != 303 && status != 307 && status != 308 {
				return result, errors.New("unsupported linked redirect status")
			}
			locations := response.Header.Values("Location")
			if len(locations) != 1 || strings.TrimSpace(locations[0]) == "" {
				return result, errors.New("linked redirect requires one Location")
			}
			target, err := response.Location()
			if err != nil {
				return result, err
			}
			next := target.String()
			if validate(next) != nil {
				return result, errors.New("linked redirect left allowed hosts")
			}
			if visited[next] {
				return result, errors.New("linked redirect loop")
			}
			if hop == 10 {
				return result, errors.New("linked redirect limit")
			}
			visited[next] = true
			endpoint = next
			continue
		}
		if status < 200 || status >= 300 {
			result.ErrorKind = "status"
			return result, errors.New("linked document HTTP failure")
		}
		excerpt := []rune(DecodeDocument(body, result.ContentType))
		if len(excerpt) > 64000 {
			excerpt = excerpt[:64000]
		}
		reservation, policy := tdmMetadata([]byte(string(excerpt)))
		if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" || reservation == "1" {
			result.ErrorKind = "tdm"
			result.TDMSource = "header"
			result.TDMPolicy = response.Header.Get("TDM-Policy")
			if reservation == "1" {
				result.TDMSource = "meta"
				if policy != "" {
					result.TDMPolicy = policy
				}
			}
			return result, errors.New("tdm-reservation=1")
		}
		result.Body = body
		return result, nil
	}
	return result, errors.New("linked redirect limit")
}
