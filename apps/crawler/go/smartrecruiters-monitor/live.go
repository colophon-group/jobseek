package smartrecruiters

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/html"
)

const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
const accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
const MaxRunBytes = 512 << 20

type Failure struct {
	Kind     string `json:"kind"`
	URL      string `json:"url"`
	Attempts int    `json:"attempts,omitempty"`
	Status   int    `json:"status,omitempty"`
	Location string `json:"location,omitempty"`
	MaxBytes int    `json:"max_bytes,omitempty"`
	Source   string `json:"source,omitempty"`
	Policy   string `json:"policy,omitempty"`
}

func (f *Failure) Error() string { return fmt.Sprintf("SmartRecruiters %s failed", f.Kind) }

type FetchResult struct {
	Inventory
	Requests  int      `json:"requests"`
	Responses int      `json:"responses"`
	Bytes     int64    `json:"bytes"`
	Failure   *Failure `json:"failure,omitempty"`
	Error     string   `json:"error,omitempty"`
}
type requestDoer interface {
	Do(*http.Request) (*http.Response, error)
}

var detailPathID = regexp.MustCompile(`^[\p{L}\p{N}_-]{1,128}$`)

type fetcher struct {
	token       string
	client      requestDoer
	pause       Pause
	retain      func(string, []byte)
	mu          sync.Mutex
	result      FetchResult
	reservation *Failure
}

func normalPause(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	transport.MaxConnsPerHost = 12
	transport.MaxIdleConnsPerHost = 12
	return &http.Client{Transport: transport, Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}
func metaReservation(body []byte) (int, string) {
	text := []rune(strings.ToValidUTF8(string(body), "\ufffd"))
	if len(text) > 65536 {
		text = text[:65536]
	}
	excerpt := string(text)
	if !strings.Contains(strings.ToLower(excerpt), "tdm-") {
		return -1, ""
	}
	tokenizer := html.NewTokenizer(strings.NewReader(excerpt))
	reservation := -1
	policy := ""
	for {
		kind := tokenizer.Next()
		if kind == html.ErrorToken {
			break
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		tag := tokenizer.Token()
		if tag.Data != "meta" {
			continue
		}
		attrs := map[string]string{}
		for _, attr := range tag.Attr {
			attrs[attr.Key] = attr.Val
		}
		switch strings.ToLower(attrs["name"]) {
		case "tdm-reservation":
			switch trim(attrs["content"]) {
			case "0":
				reservation = 0
			case "1":
				reservation = 1
			}
		case "tdm-policy":
			policy = attrs["content"]
		}
	}
	return reservation, policy
}
func (f *fetcher) once(ctx context.Context, endpoint string, limit int) (Object, int, string, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Scheme != "https" || parsed.Host != "api.smartrecruiters.com" || parsed.User != nil || parsed.Fragment != "" {
		return nil, 0, "", errors.New("unexpected provider endpoint")
	}
	base := "/v1/companies/" + f.token + "/postings"
	if parsed.Path == base {
		query := parsed.Query()
		offset, offsetErr := strconv.Atoi(query.Get("offset"))
		if parsed.RawQuery != "" && (len(query) != 2 || query.Get("limit") != "100" || offsetErr != nil || offset < 0 || offset > MaxJobs) {
			return nil, 0, "", errors.New("invalid list endpoint")
		}
	} else if !strings.HasPrefix(parsed.Path, base+"/") || parsed.RawQuery != "" || !detailPathID.MatchString(strings.TrimPrefix(parsed.Path, base+"/")) {
		return nil, 0, "", errors.New("unexpected tenant/detail endpoint")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, 0, "", err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", accept)
	f.mu.Lock()
	if f.result.Bytes > MaxRunBytes {
		f.mu.Unlock()
		return nil, 0, "", &Failure{Kind: "run_body_limit", URL: endpoint, MaxBytes: MaxRunBytes}
	}
	f.result.Requests++
	f.mu.Unlock()
	response, err := f.client.Do(req)
	if err != nil {
		return nil, 0, "", err
	}
	defer response.Body.Close()
	f.mu.Lock()
	f.result.Responses++
	f.mu.Unlock()
	status := response.StatusCode
	location := response.Header.Get("Location")
	if trim(strings.Join(response.Header.Values("TDM-Reservation"), ", ")) == "1" {
		return nil, status, "", &Failure{Kind: "tdm", URL: endpoint, Source: "header", Policy: strings.Join(response.Header.Values("TDM-Policy"), ", ")}
	}
	if status != 200 {
		return nil, status, location, nil
	}
	body, readErr := io.ReadAll(io.LimitReader(response.Body, int64(limit)+1))
	f.mu.Lock()
	f.result.Bytes += int64(len(body))
	total := f.result.Bytes
	f.mu.Unlock()
	if len(body) > limit {
		return nil, status, "", &Failure{Kind: "body_limit", URL: endpoint, MaxBytes: limit}
	}
	if total > MaxRunBytes {
		return nil, status, "", &Failure{Kind: "run_body_limit", URL: endpoint, MaxBytes: MaxRunBytes}
	}
	if readErr != nil {
		return nil, status, "", readErr
	}
	reserved, policy := metaReservation(body)
	if reserved == 1 {
		if policy == "" {
			policy = strings.Join(response.Header.Values("TDM-Policy"), ", ")
		}
		return nil, status, "", &Failure{Kind: "tdm", URL: endpoint, Source: "meta", Policy: policy}
	}
	data, err := decodeObject(body)
	if err == nil && f.retain != nil {
		f.retain(endpoint, body)
	}
	return data, status, "", err
}
func (f *fetcher) get(ctx context.Context, endpoint string, limit int) (Object, error) {
	lastStatus := 0
	for attempt := 0; attempt < 3; attempt++ {
		data, status, location, err := f.once(ctx, endpoint, limit)
		var failure *Failure
		if errors.As(err, &failure) {
			if failure.Kind == "tdm" {
				f.mu.Lock()
				if f.reservation == nil || failure.URL < f.reservation.URL {
					f.reservation = failure
				}
				f.mu.Unlock()
			}
			return nil, err
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if err == nil && status == 200 {
			return data, nil
		}
		lastStatus = status
		if err != nil {
			lastStatus = 0
		} else if !(status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599) {
			return nil, &Failure{Kind: "pagination", URL: endpoint, Attempts: attempt + 1, Status: status, Location: location}
		}
		if attempt < 2 {
			delay := time.Duration(float64((500*time.Millisecond)<<attempt) * (0.5 + rand.Float64()))
			if err = f.pause(ctx, delay); err != nil {
				return nil, err
			}
		}
	}
	return nil, &Failure{Kind: "pagination", URL: endpoint, Attempts: 3, Status: lastStatus}
}
func Fetch(ctx context.Context, boardURL string, metadata Object) (FetchResult, error) {
	return fetchWith(ctx, boardURL, metadata, newClient(), normalPause)
}

// FetchWithClient retains the existing complete snapshot/pagination contract
// using the native worker's process-owned verified and observed HTTP client.
func FetchWithClient(ctx context.Context, boardURL string, metadata Object, client interface {
	Do(*http.Request) (*http.Response, error)
}) (FetchResult, error) {
	if client == nil {
		return FetchResult{}, errors.New("SmartRecruiters inventory client unavailable")
	}
	return fetchWith(ctx, boardURL, metadata, client, normalPause)
}
func fetchWith(ctx context.Context, boardURL string, metadata Object, client requestDoer, pause Pause) (FetchResult, error) {
	f := &fetcher{client: client, pause: pause}
	opt, err := OptionsFromMetadata(boardURL, metadata)
	f.token = opt.Token
	if err == nil {
		f.result.Inventory, err = Discover(ctx, opt, f.get, pause)
	}
	// Concurrent detail failures cannot erase a publisher reservation observed
	// by another completed request. All workers have joined before this read.
	f.mu.Lock()
	if f.reservation != nil {
		err = f.reservation
	}
	f.mu.Unlock()
	if err != nil {
		f.result.Inventory = Inventory{}
		f.result.Error = err.Error()
		var failure *Failure
		if errors.As(err, &failure) {
			f.result.Failure = failure
		}
	}
	return f.result, err
}
