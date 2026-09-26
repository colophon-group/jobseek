package recruitee

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
)

func TestLiveClientNegotiatesHTTP2WithCustomDialer(t *testing.T) {
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.ProtoMajor != 2 {
			t.Errorf("negotiated %s; want HTTP/2", request.Proto)
		}
		_, _ = io.WriteString(w, `{"offers":[]}`)
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
	result, err := Fetch(context.Background(), client, "acme")
	if err != nil || result.Requests != 1 || result.Responses != 1 {
		t.Fatalf("HTTP/2 fetch failed: %#v, %v", result, err)
	}
}

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

func TestFetchSingleResponseAndGone(t *testing.T) {
	client := &fakeClient{status: 200, body: `{"offers":[]}`, header: http.Header{}}
	result, err := Fetch(context.Background(), client, "acme")
	if err != nil || client.calls != 1 || result.Requests != 1 || result.Responses != 1 ||
		result.Status != 200 || result.FinalURL != "https://acme.recruitee.com/api/offers" {
		t.Fatalf("unexpected success: %#v, %v", result, err)
	}
	client = &fakeClient{status: 404, body: "gone", header: http.Header{}}
	result, err = Fetch(context.Background(), client, "acme")
	if err == nil || result.ErrorKind != "gone" || len(result.Jobs) != 0 || client.calls != 1 {
		t.Fatalf("unexpected gone outcome: %#v, %v", result, err)
	}
}

func TestFetchHonorsTDMHeaderBeforeBody(t *testing.T) {
	client := &fakeClient{status: 200, body: `{"offers":[]}`, header: http.Header{"Tdm-Reservation": {"1"}}}
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
