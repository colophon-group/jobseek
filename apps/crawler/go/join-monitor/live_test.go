package join

import (
	"context"
	"io"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"testing"
)

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
	} {
		if !tdmMetaReserved([]byte(body)) {
			t.Fatalf("missed reservation: %s", body)
		}
	}
}
