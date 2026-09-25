package pinpoint

import (
	"context"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"
)

type fakeClient struct {
	status int
	body   string
	header http.Header
	calls  int
}

func (client *fakeClient) Do(request *http.Request) (*http.Response, error) {
	client.calls++
	return &http.Response{
		StatusCode: client.status, Body: io.NopCloser(strings.NewReader(client.body)),
		Request: request, Header: client.header,
	}, nil
}

func TestFetchSingleResponseAndHTTPError(t *testing.T) {
	client := &fakeClient{status: 200, body: `{"data":[]}`, header: http.Header{}}
	result, err := Fetch(context.Background(), client, "acme")
	if err != nil || client.calls != 1 || result.Requests != 1 || result.Responses != 1 ||
		result.Status != 200 || result.FinalURL != "https://acme.pinpointhq.com/postings.json" {
		t.Fatalf("unexpected success: %#v, %v", result, err)
	}
	client = &fakeClient{status: 404, body: "gone", header: http.Header{}}
	result, err = Fetch(context.Background(), client, "acme")
	if err == nil || result.ErrorKind != "" || len(result.Jobs) != 0 || client.calls != 1 || result.Status != 404 {
		t.Fatalf("unexpected HTTP error: %#v, %v", result, err)
	}
}

func TestFetchHonorsTDMHeaderBeforeBody(t *testing.T) {
	client := &fakeClient{status: 200, body: `{"data":[]}`, header: http.Header{"Tdm-Reservation": {"1"}}}
	result, err := Fetch(context.Background(), client, "acme")
	if err == nil || result.ErrorKind != "tdm" || result.Bytes != 0 || len(result.Jobs) != 0 {
		t.Fatalf("unexpected TDM outcome: %#v, %v", result, err)
	}
	if !tdmMetaReserved([]byte(strings.Repeat("x", 1024) + "<meta content=1 name=tdm-reservation>")) {
		t.Fatal("missed late TDM meta declaration")
	}
}

func TestPublicAddressesOnly(t *testing.T) {
	for _, raw := range []string{"127.0.0.1", "10.0.0.1", "192.0.2.1", "2001:db8::1"} {
		if publicAddress(netip.MustParseAddr(raw)) {
			t.Fatalf("accepted non-public address %s", raw)
		}
	}
	if !publicAddress(netip.MustParseAddr("8.8.8.8")) {
		t.Fatal("rejected public address")
	}
}
