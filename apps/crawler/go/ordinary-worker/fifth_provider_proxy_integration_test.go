package worker

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Run the frozen Python requests/content through an actual credentialed CONNECT
// hop, then the existing verified TLS fixture and canonical PG/Redis writer.
func fifthExecutionCases(t *testing.T) []fifthHTTPCase {
	cases := fifthHTTPCases(t)
	for _, ref := range fifthHTTPCases(t) {
		if ref.Provider != "paylocity" {
			continue
		}
		raw, _ := json.Marshal(ref)
		var copied fifthHTTPCase
		if json.Unmarshal(raw, &copied) != nil {
			t.Fatal("reference copy failed")
		}
		copied.Name += "/proxy"
		if copied.Metadata == nil {
			copied.Metadata = map[string]any{}
		}
		copied.Metadata["proxy"] = true
		cases = append(cases, copied)
	}
	return cases
}

func fifthExecutionHTTPFixture(t *testing.T, c fifthHTTPCase, requests *[]fourthHTTPRequest, mutex *sync.Mutex) *VerifiedHTTP {
	origin := fifthHTTPFixture(t, c, requests, mutex)
	if !strings.HasSuffix(c.Name, "/proxy") {
		return origin
	}
	return credentialedProxyFixture(t, origin)
}

func credentialedProxyFixture(t *testing.T, origin *VerifiedHTTP) *VerifiedHTTP {
	t.Helper()
	original := origin.client.Transport.(*directTransport)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Method != "CONNECT" || request.Header.Get("Proxy-Authorization") == "" {
			t.Error("missing actual proxy CONNECT/auth")
			w.WriteHeader(407)
			return
		}
		target, err := original.dial(request.Context(), "tcp", "8.8.8.8:443")
		if err != nil {
			t.Error(err)
			w.WriteHeader(502)
			return
		}
		client, buffered, err := w.(http.Hijacker).Hijack()
		if err != nil {
			target.Close()
			t.Error(err)
			return
		}
		_, _ = buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		_ = buffered.Flush()
		go func() { _, _ = io.Copy(target, buffered); _ = target.Close() }()
		go func() { _, _ = io.Copy(client, target); _ = client.Close() }()
	}))
	t.Cleanup(server.Close)
	endpoint, _ := url.Parse(server.URL)
	endpoint.User = url.UserPassword("synthetic-user", "synthetic-password")
	base := &directTransport{inner: original.inner.Clone(), allowed: map[string]bool{"127.0.0.1": true}, lookup: original.lookup, timeout: original.timeout, requests: original.requests, connections: original.connections, dial: (&net.Dialer{}).DialContext}
	endpoints := []*url.URL{endpoint}
	for _, username := range []string{"synthetic-user-2", "synthetic-user-3"} {
		copy := *endpoint
		copy.User = url.UserPassword(username, "synthetic-password")
		endpoints = append(endpoints, &copy)
	}
	pool, err := newLiveProxyPool(len(endpoints), -1)
	if err != nil {
		t.Fatal(err)
	}
	transport := &rotatingProxyTransport{pool: pool, base: base, endpoints: endpoints, transports: make([]*directTransport, len(endpoints))}
	client := *origin.client
	client.Transport = transport
	client.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) > 20 {
			return http.ErrUseLastResponse
		}
		if request.Response != nil {
			markProxyRedirect(request.Response, false)
		}
		return nil
	}
	sealed := &VerifiedHTTP{client: &client, proxyRequired: true}
	t.Cleanup(sealed.CloseIdleConnections)
	return sealed
}

func TestPaylocityRuntimeKeepsIndependentMonitorAndScraperTransport(t *testing.T) {
	for _, c := range []struct {
		kind     queue.Kind
		metadata string
		want     bool
	}{
		{queue.Monitor, `{"proxy":true,"scraper_config":{"proxy":false}}`, true},
		{queue.Scrape, `{"proxy":true,"scraper_config":{"proxy":false}}`, false},
		{queue.Monitor, `{"proxy":false,"scraper_config":{"proxy":true}}`, false},
		{queue.Scrape, `{"proxy":false,"scraper_config":{"proxy":true}}`, true},
		{queue.Scrape, `{"scraper_type":"json-ld","proxy":true,"scraper_config":{"proxy":true}}`, false},
	} {
		if got := runtimeUsesProxy(queue.Task{Kind: c.kind, Config: map[string]string{"crawler_type": "paylocity", "metadata": c.metadata}}); got != c.want {
			t.Fatal("monitor proxy setting changed independent scraper egress")
		}
	}
	// A direct compiled profile cannot acquire the proxy writer's client.
	_, _, err := fetchAPIDetail(context.Background(), &VerifiedHTTP{client: &http.Client{}, proxyRequired: true}, queue.WorkdayDetailProfile{Profile: "paylocity.html-detail/v1"})
	if err != queue.ErrConfiguration {
		t.Fatal("direct detail accepted proxy transport")
	}
}
