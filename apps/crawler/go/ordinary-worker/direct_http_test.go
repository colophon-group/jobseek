package worker

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestDirectHTTPActualPythonContentDecodingAndEncodedMeterOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_http_decoding.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Encoding, Kind string
		Body                 string `json:"body_base64"`
		Decoded              string `json:"decoded_base64"`
		EncodedBytes         int64  `json:"encoded_bytes"`
	}
	if err := json.Unmarshal(body, &cases); err != nil || len(cases) != 25 {
		t.Fatal("missing actual pinned Python decoder captures")
	}
	byName := make(map[string]int)
	for i, row := range cases {
		byName[row.Name] = i
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		row := cases[byName[strings.TrimPrefix(request.URL.Path, "/")]]
		encoded, err := base64.StdEncoding.DecodeString(row.Body)
		if err != nil {
			t.Error(err)
			return
		}
		w.Header().Set("Content-Encoding", row.Encoding)
		w.Header().Set("Content-Length", strconv.Itoa(len(encoded)))
		_, _ = w.Write(encoded)
	}))
	t.Cleanup(server.Close)
	client, _ := directFixtureClient(t, server)
	for _, row := range cases {
		t.Run(row.Name, func(t *testing.T) {
			ctx, observation := ObserveHTTP(context.Background())
			request, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"/"+row.Name, nil)
			response, err := client.Do(request)
			if err != nil {
				t.Fatal(err)
			}
			decoded, err := io.ReadAll(response.Body)
			_ = response.Body.Close()
			kind := ""
			if err != nil {
				kind = "body_failed"
			} else {
				var inventory any
				if json.Unmarshal(decoded, &inventory) != nil {
					kind = "invalid_inventory"
				}
			}
			want, decodeError := base64.StdEncoding.DecodeString(row.Decoded)
			if decodeError != nil || kind != row.Kind || (row.Kind != "body_failed" && !bytes.Equal(decoded, want)) {
				t.Fatalf("Python decoded body/outcome differs: kind=%s want=%s decoded=%q want=%q error=%v", kind, row.Kind, decoded, want, err)
			}
			value := observation.Snapshot()
			if value.Requests != 1 || value.Responses != 1 || value.NoResponse != 0 || value.EncodedBytes != row.EncodedBytes || value.LastStatus != 200 || value.LastTransportError != "" {
				t.Fatalf("raw-stream/decoder outcome accounting differs: %+v want %d encoded bytes", value, row.EncodedBytes)
			}
		})
	}
}

func TestDirectHTTPPoolWaitAndCrossOriginIdleEvictionStayBounded(t *testing.T) {
	for _, mode := range []string{"idle_eviction", "pool_timeout"} {
		t.Run(mode, func(t *testing.T) {
			unblock := make(chan struct{})
			defer close(unblock)
			var servers []*httptest.Server
			for range 3 {
				server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Length", "1")
					w.WriteHeader(200)
					w.(http.Flusher).Flush()
					if mode == "pool_timeout" {
						select {
						case <-unblock:
						case <-r.Context().Done():
							return
						}
					}
					_, _ = io.WriteString(w, "x")
				}))
				servers = append(servers, server)
				t.Cleanup(server.Close)
			}
			client, transport := directFixtureClient(t, servers[0])
			// Reduced private fixture budget exercises the same cross-origin
			// coordinator without manufacturing 100 simultaneous TLS sockets.
			transport.requests, transport.connections = make(chan struct{}, 2), make(chan struct{}, 2)
			transport.timeout = 300 * time.Millisecond
			ctx, observation := ObserveHTTP(context.Background())
			var bodies []io.ReadCloser
			defer func() {
				for _, body := range bodies {
					_ = body.Close()
				}
			}()
			for i, server := range servers {
				request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
				response, err := client.Do(request)
				if mode == "pool_timeout" && i == 2 {
					if !errors.Is(err, context.DeadlineExceeded) {
						t.Fatalf("pool wait did not fail within operation bound: %v", err)
					}
					continue
				}
				if err != nil {
					t.Fatal(err)
				}
				bodies = append(bodies, response.Body)
				if mode == "idle_eviction" {
					_, err := io.Copy(io.Discard, response.Body)
					_ = response.Body.Close()
					if err != nil {
						t.Fatal(err)
					}
				}
				if len(transport.connections) > 2 {
					t.Fatal("global connection pool exceeded its budget")
				}
			}
			value := observation.Snapshot()
			if value.Requests != 3 || value.Requests != value.Responses+value.NoResponse || (mode == "pool_timeout" && (value.NoResponse != 1 || value.LastTransportError != "timeout")) || (mode == "idle_eviction" && value.Responses != 3) {
				t.Fatalf("pool coordinator/origin conservation differs: %+v", value)
			}
		})
	}
}

func directFixtureClient(t *testing.T, server *httptest.Server) (*http.Client, *directTransport) {
	t.Helper()
	bundle := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw})
	client, err := NewDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle, InternalHosts: []string{"127.0.0.1"}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.CloseIdleConnections)
	return client, client.Transport.(*directTransport)
}

func TestDirectHTTPVerifiedTLSReuseCompressionAndOriginAccounting(t *testing.T) {
	var requests, connections atomic.Int64
	payload := []byte(`{"jobs":[{"absolute_url":"https://example.com/jobs/1","title":"Engineer"}]}`)
	var encoded bytes.Buffer
	writer := gzip.NewWriter(&encoded)
	_, _ = writer.Write(payload)
	_ = writer.Close()
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.ProtoMajor != 1 || r.ProtoMinor != 1 || r.Header.Get("Accept-Encoding") != "gzip, deflate" || r.Header.Get("User-Agent") != ordinaryUserAgent || r.Header.Get("Accept") != ordinaryAccept {
			t.Error("effective native HTTP defaults changed")
		}
		body, _ := io.ReadAll(r.Body)
		if len(body) != 0 || len(r.TransferEncoding) != 0 || r.Header.Get("Content-Length") != "" {
			t.Error("non-replayable GET changed wire body/encoding")
		}
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Content-Length", strconv.Itoa(encoded.Len()))
		_, _ = w.Write(encoded.Bytes())
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.StartTLS()
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	if client.Timeout != 0 || cap(transport.connections) != 100 || cap(transport.requests) != 100 || transport.inner.MaxIdleConns != 20 || transport.inner.IdleConnTimeout != 5*time.Second || transport.timeout != 30*time.Second {
		t.Fatal("effective pool/per-operation budget changed")
	}
	ctx, observation := ObserveHTTP(context.Background())
	for range 2 {
		request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
		response, err := client.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		got, err := io.ReadAll(response.Body)
		_ = response.Body.Close()
		if err != nil || !bytes.Equal(got, payload) || response.TLS == nil || len(response.TLS.VerifiedChains) == 0 {
			t.Fatalf("verified native body differs: %q %v", got, err)
		}
	}
	value := observation.Snapshot()
	if requests.Load() != 2 || connections.Load() != 1 || value.Requests != 2 || value.Responses != 2 || value.NoResponse != 0 || value.EncodedBytes != int64(2*encoded.Len()) || value.LastStatus != 200 {
		t.Fatalf("reuse/wire-byte/conservation mismatch: requests=%d connections=%d %+v", requests.Load(), connections.Load(), value)
	}
}

func TestDirectHTTPDoesNotRetryFailedReusedGET(t *testing.T) {
	var requests atomic.Int64
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) == 2 {
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			_ = connection.Close()
			return
		}
		_, _ = io.WriteString(w, "okay")
	}))
	t.Cleanup(server.Close)
	client, _ := directFixtureClient(t, server)
	ctx, observation := ObserveHTTP(context.Background())
	for i := range 2 {
		request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
		response, err := client.Do(request)
		if i == 0 {
			if err != nil {
				t.Fatal(err)
			}
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
		} else if err == nil {
			_ = response.Body.Close()
			t.Fatal("failed reused GET was silently retried")
		}
	}
	value := observation.Snapshot()
	if requests.Load() != 2 || value.Requests != 2 || value.Responses != 1 || value.NoResponse != 1 {
		t.Fatalf("hidden retry or origin accounting loss: requests=%d %+v", requests.Load(), value)
	}
}

func TestDirectHTTPPreservesExplicitRequestHeaderOverrides(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") != "fixture-agent" || r.Header.Get("Accept") != "application/json" || r.Header.Get("Accept-Encoding") != "" || r.Header.Get("Connection") != "close" {
			t.Error("client defaults replaced explicit request headers")
		}
		_, _ = io.WriteString(w, "okay")
	}))
	t.Cleanup(server.Close)
	client, _ := directFixtureClient(t, server)
	request, _ := http.NewRequest("GET", server.URL, nil)
	request.Header.Set("User-Agent", "fixture-agent")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Accept-Encoding", "")
	request.Header.Set("Connection", "close")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, err = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
}

func TestDirectHTTPRedirectPrivateHostRefusedBeforeOriginAccounting(t *testing.T) {
	var requests atomic.Int64
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		w.Header().Set("Location", "http://10.0.0.1/private")
		w.WriteHeader(302)
	}))
	t.Cleanup(server.Close)
	client, _ := directFixtureClient(t, server)
	ctx, observation := ObserveHTTP(context.Background())
	request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
	_, err := client.Do(request)
	value := observation.Snapshot()
	if !errors.Is(err, ErrUnsafeURL) || requests.Load() != 1 || value.Requests != 1 || value.Responses != 1 || value.NoResponse != 0 || len(value.Hosts) != 1 || value.Hosts[0] != "127.0.0.1" {
		t.Fatalf("refused private hop entered origin/failure accounting: %v %+v", err, value)
	}
}

func TestDirectHTTPRedirectCookiesRejectMalformedUpstreamValues(t *testing.T) {
	var requests atomic.Int64
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.URL.Path == "/" {
			w.Header().Add("Set-Cookie", "session=retained; Path=/")
			w.Header().Add("Set-Cookie", "bad=AOÛT; Path=/")
			w.Header().Add("Set-Cookie", `quoted="okay"; Path=/`)
			w.Header().Add("Set-Cookie", "empty=; Path=/")
			w.Header().Set("Location", "/final")
			w.WriteHeader(302)
			return
		}
		// Actual _Rfc6265CookiePolicy/httpx replay, including quote/empty values.
		if r.Header.Get("Cookie") != `session=retained; quoted="okay"; empty=` {
			t.Errorf("native redirect cookie differs: %q", r.Header.Get("Cookie"))
		}
		_, _ = io.WriteString(w, "okay")
	}))
	t.Cleanup(server.Close)
	client, _ := directFixtureClient(t, server)
	ctx, observation := ObserveHTTP(context.Background())
	request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, err = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	value := observation.Snapshot()
	if err != nil || requests.Load() != 2 || value.Requests != 2 || value.Responses != 2 || value.NoResponse != 0 {
		t.Fatal("redirect cookie/origin conservation failed")
	}
}

func TestDirectHTTPRechecksDNSOnReusedConnectionAndPinsAnswers(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, "okay") }))
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	// The test trusts the fixture certificate's example.com SAN and routes
	// only the physical socket to the local fixture; guard inputs stay public.
	endpoint := strings.Replace(server.URL, "127.0.0.1", "example.com", 1)
	var lookups, dials atomic.Int64
	transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
		if lookups.Add(1) == 2 {
			return []netip.Addr{netip.MustParseAddr("8.8.8.8"), netip.MustParseAddr("127.0.0.1")}, nil
		}
		return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
	}
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		dials.Add(1)
		if host, _, _ := net.SplitHostPort(address); host != "8.8.8.8" {
			t.Error("validated DNS answer not pinned in dial")
		}
		return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
	}
	ctx, observation := ObserveHTTP(context.Background())
	for i := range 2 {
		request, _ := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
		response, err := client.Do(request)
		if i == 0 {
			if err != nil {
				t.Fatal(err)
			}
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
		} else if !errors.Is(err, ErrUnsafeURL) {
			t.Fatal("DNS rebinding was admitted on a reused connection")
		}
	}
	value := observation.Snapshot()
	if lookups.Load() != 2 || dials.Load() != 1 || value.Requests != 1 || value.Responses != 1 {
		t.Fatalf("per-hop validation/pinning changed: lookup=%d dial=%d %+v", lookups.Load(), dials.Load(), value)
	}
}

func TestDirectHTTPUnknownTLSRootFailsAndNoUnsafeFallback(t *testing.T) {
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("unverified TLS request reached server") }))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	transport.inner.TLSClientConfig.RootCAs = x509.NewCertPool() // configured trust excludes fixture root
	ctx, observation := ObserveHTTP(context.Background())
	request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
	if _, err := client.Do(request); err == nil {
		t.Fatal("unverified TLS certificate accepted")
	}
	value := observation.Snapshot()
	if value.Requests != 1 || value.Responses != 0 || value.NoResponse != 1 {
		t.Fatal("TLS failure lost origin conservation")
	}
	if _, err := NewDirectHTTP(DirectHTTPConfig{}); err == nil {
		t.Fatal("missing pinned roots substituted system trust")
	}
}

func TestDirectHTTPOperationDeadlineResetsAcrossSlowBodyAndCancels(t *testing.T) {
	for _, mode := range []string{"slow_complete", "read_timeout", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Advertise the expected body. On cancellation the handler can
				// observe the disconnect and return before the client read, so an
				// unframed empty response could otherwise finish successfully.
				w.Header().Set("Content-Length", "8")
				w.WriteHeader(200)
				w.(http.Flusher).Flush()
				for range 8 {
					if mode != "slow_complete" {
						<-r.Context().Done()
						return
					}
					time.Sleep(20 * time.Millisecond)
					_, _ = io.WriteString(w, "x")
					w.(http.Flusher).Flush()
				}
			}))
			t.Cleanup(server.Close)
			client, transport := directFixtureClient(t, server)
			transport.timeout = 80 * time.Millisecond
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			request, _ := http.NewRequestWithContext(ctx, "GET", server.URL, nil)
			response, err := client.Do(request)
			if err != nil {
				t.Fatal(err)
			}
			if mode == "cancel" {
				cancel()
			}
			body, err := io.ReadAll(response.Body)
			_ = response.Body.Close()
			if mode == "slow_complete" {
				if err != nil || len(body) != 8 {
					t.Fatalf("total elapsed time replaced per-read deadline: %d %v", len(body), err)
				}
			} else if err == nil {
				t.Fatal("read timeout/cancellation supplied complete body")
			}
		})
	}
}
