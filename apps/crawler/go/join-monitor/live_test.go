package join

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestRedirectHeadersCookiesAndRequestAccounting(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Accept") != accept || r.Header.Get("User-Agent") != userAgent {
			t.Error("request did not preserve Python headers")
		}
		if r.URL.Path == "/companies/acme" {
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "kept", Path: "/", Secure: true})
			http.Redirect(w, r, "/redirected?"+r.URL.RawQuery, http.StatusFound)
			return
		}
		cookie, err := r.Cookie("session")
		if err != nil || cookie.Value != "kept" {
			t.Error("redirect lost its session cookie")
		}
		page := r.URL.Query().Get("page")
		if page == "" {
			page = "1"
		}
		_, _ = w.Write(samplePage(`[{"idParam":"`+page+`-job"}]`, "2"))
	}))
	defer server.Close()
	client := newClient()
	defer client.CloseIdleConnections()
	transport := client.Transport.(*http.Transport)
	transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} // Local test server only.
	result, err := Fetch(context.Background(), client, "https://join.com/companies/acme", "acme")
	if err != nil || len(result.URLs) != 2 || result.Requests != 4 || result.Responses != 4 || result.FinalURL != "https://join.com/redirected?page=2" {
		t.Fatalf("redirected pagination failed: %#v, %v", result, err)
	}
}

type largeClient struct{ filler string }

func (c largeClient) Do(request *http.Request) (*http.Response, error) {
	page := request.URL.Query().Get("page")
	if page == "" {
		page = "1"
	}
	reader := io.MultiReader(strings.NewReader(string(samplePage(`[{"idParam":"`+page+`-job"}]`, "24"))), strings.NewReader(c.filler))
	return &http.Response{StatusCode: 200, Body: io.NopCloser(reader), Request: request, Header: http.Header{}}, nil
}

func TestLargeInventoryHasPerPageMemoryBound(t *testing.T) {
	result, err := Fetch(context.Background(), largeClient{strings.Repeat("x", 3<<20)}, "https://join.com/companies/acme", "acme")
	if err != nil || len(result.URLs) != 24 || result.Requests != 24 || result.Bytes <= 64<<20 {
		t.Fatalf("complete inventory rejected by aggregate body size: %#v, %v", result, err)
	}
}

func TestReadInactivityClosesBody(t *testing.T) {
	reader, writer := io.Pipe()
	defer writer.Close()
	body := &readIdleBody{ReadCloser: reader, timeout: 10 * time.Millisecond}
	_, err := body.Read(make([]byte, 1))
	if err != context.DeadlineExceeded {
		t.Fatalf("blocked body read was not timed out: %v", err)
	}
}

type cancelledClient struct{}

func (cancelledClient) Do(request *http.Request) (*http.Response, error) {
	<-request.Context().Done()
	return nil, request.Context().Err()
}

func TestCancellationDoesNotRetryOrPublish(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, err := Fetch(ctx, cancelledClient{}, "https://join.com/companies/acme", "acme")
	if err != context.Canceled || len(result.URLs) != 0 || result.Requests != 1 || result.Responses != 0 {
		t.Fatalf("cancelled inventory leaked or retried: %#v, %v", result, err)
	}
}

func TestLiveClientNegotiatesHTTP2WithCustomDialer(t *testing.T) {
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.ProtoMajor != 2 {
			t.Errorf("negotiated %s; want HTTP/2", request.Proto)
		}
		_, _ = w.Write(samplePage(`[{"idParam":"1-job"}]`, "1"))
	}))
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()

	client := newClient()
	defer client.CloseIdleConnections()
	transport := client.Transport.(*http.Transport)
	transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} // Local test server only.
	result, err := Fetch(context.Background(), client, "https://join.com/companies/acme", "acme")
	if err != nil || result.Requests != 1 || result.Responses != 1 || len(result.URLs) != 1 {
		t.Fatalf("HTTP/2 fetch failed: %#v, %v", result, err)
	}
}

type fakeClient struct {
	mu       sync.Mutex
	statuses map[int]int
	counts   map[int]int
	pages    int
}

func (client *fakeClient) Do(request *http.Request) (*http.Response, error) {
	page := 1
	if raw := request.URL.Query().Get("page"); raw != "" {
		page, _ = strconv.Atoi(raw)
	}
	client.mu.Lock()
	client.counts[page]++
	status := client.statuses[page]
	client.mu.Unlock()
	if status == 0 {
		status = 200
	}
	content := samplePage(`[{"idParam":"`+strconv.Itoa(page)+`-job"}]`, strconv.Itoa(client.pages))
	if status != 200 {
		content = []byte("unavailable")
	}
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(string(content))), Request: request, Header: http.Header{}}, nil
}

func TestFetchOwnsCompletePagination(t *testing.T) {
	client := &fakeClient{statuses: map[int]int{}, counts: map[int]int{}, pages: 3}
	result, err := Fetch(context.Background(), client, "https://join.com/companies/acme", "acme")
	if err != nil || len(result.URLs) != 3 || result.Requests != 3 || result.Responses != 3 {
		t.Fatalf("unexpected fetch: %#v, %v", result, err)
	}
}

func TestFailedRequiredPagePublishesNoInventoryOrLaterChunk(t *testing.T) {
	client := &fakeClient{statuses: map[int]int{2: 503}, counts: map[int]int{}, pages: 12}
	result, err := Fetch(context.Background(), client, "https://join.com/companies/acme", "acme")
	if err == nil || len(result.URLs) != 0 || client.counts[12] != 0 || client.counts[2] != 3 {
		t.Fatalf("failed page leaked inventory or fetched later chunk: %#v, %v, %#v", result, err, client.counts)
	}
}

func TestRetiredBoardReturnsGoneWithoutRetry(t *testing.T) {
	client := &fakeClient{statuses: map[int]int{1: 404}, counts: map[int]int{}, pages: 1}
	result, err := Fetch(context.Background(), client, "https://join.com/companies/acme", "acme")
	if err == nil || result.ErrorKind != "gone" || result.Status != 404 || result.Requests != 1 {
		t.Fatalf("unexpected gone outcome: %#v, %v", result, err)
	}
}

func TestPublicAddressRejectsSpecialUseRanges(t *testing.T) {
	for _, raw := range []string{"127.0.0.1", "10.0.0.1", "169.254.1.1", "192.0.2.1", "198.18.0.1", "2001:db8::1", "::ffff:127.0.0.1"} {
		ip := netip.MustParseAddr(raw)
		if publicAddress(ip.Unmap()) {
			t.Fatalf("accepted non-public IP %s", raw)
		}
	}
	for _, raw := range []string{"8.8.8.8", "2606:4700:4700::1111"} {
		if !publicAddress(netip.MustParseAddr(raw)) {
			t.Fatalf("rejected public IP %s", raw)
		}
	}
}

func TestTDMMetadataAcceptsAttributeOrder(t *testing.T) {
	for _, body := range []string{
		`<meta name="tdm-reservation" content="1">`,
		`<meta content='1' name='tdm-reservation'>`,
		`<meta name=tdm-reservation content=1>`,
		strings.Repeat("x", 1024) + `<meta content=1 name=tdm-reservation>`,
	} {
		if !tdmMetaReserved([]byte(body)) {
			t.Fatalf("missed reservation: %s", body)
		}
	}
}

type reservationPageClient struct{ source string }

func (c reservationPageClient) Do(request *http.Request) (*http.Response, error) {
	body := string(samplePage(`[{"idParam":"job"}]`, "3"))
	header := http.Header{}
	status := 200
	if request.URL.Query().Get("page") == "2" {
		header.Set("Location", "https://join.com/reserved-page")
		status = 302
	} else if request.URL.Path == "/reserved-page" {
		if c.source == "header" {
			header.Set("TDM-Reservation", "1")
			header.Set("TDM-Policy", "page-two-policy")
			status = 404
		} else {
			body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="page-two-policy">`
		}
	}
	return &http.Response{StatusCode: status, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: request}, nil
}

func TestParallelReservationRetainsActualPageAfterLaterSuccess(t *testing.T) {
	for _, source := range []string{"header", "meta"} {
		t.Run(source, func(t *testing.T) {
			result, err := Fetch(context.Background(), reservationPageClient{source}, "https://join.com/companies/acme", "acme")
			if err == nil || len(result.URLs) != 0 || result.ErrorKind != "tdm" || result.TDMSource != source || result.TDMPolicy != "page-two-policy" || result.ReservationInitialURL != "https://join.com/companies/acme?page=2" || result.ReservationURL != "https://join.com/reserved-page" || result.FinalURL != "https://join.com/companies/acme?page=3" || result.Requests != 4 {
				t.Fatalf("parallel response changed publisher evidence: %+v %v", result, err)
			}
		})
	}
}
