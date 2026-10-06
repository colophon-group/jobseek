//go:build !densitybench

package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// Test-only native proxy and CA allow real HTTPS/cookies/fetch/CDP I/O for the
// exact production URL in a network-none fixture. Production constructors have
// no equivalent proxy, CA override, request field or address exemption.
type dayforceFixtureStarter struct{ binary, proxy, ca string }

func (s dayforceFixtureStarter) Start(port int) (managedProcess, error) {
	logs := &boundedBuffer{limit: maxProcessLogBytes}
	args := fixedLightpandaServeArgs(port, baselineBlockedCIDRs+",-127.0.0.2/32")
	args = append(args, "--http-proxy", s.proxy, "--ca-cert", s.ca)
	cmd := exec.Command(s.binary, args...)
	cmd.Env = append([]string(nil), lightpandaChildEnvironment...)
	attributes, e := lightpandaProcessAttributes(false)
	if e != nil {
		return nil, e
	}
	cmd.SysProcAttr = attributes
	cmd.Stdout = logs
	cmd.Stderr = logs
	if e = cmd.Start(); e != nil {
		return nil, e
	}
	return &commandProcess{command: cmd, pgid: cmd.Process.Pid, logs: logs}, nil
}

func dayforceOriginTLS(t *testing.T) (tls.Certificate, string) {
	t.Helper()
	rootKey, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	root := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Dayforce fixture CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	rootDER, e := x509.CreateCertificate(rand.Reader, root, root, &rootKey.PublicKey, rootKey)
	if e != nil {
		t.Fatal(e)
	}
	root, e = x509.ParseCertificate(rootDER)
	if e != nil {
		t.Fatal(e)
	}
	leafKey, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "jobs.dayforcehcm.com"}, DNSNames: []string{"jobs.dayforcehcm.com"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, e := x509.CreateCertificate(rand.Reader, leaf, root, &leafKey.PublicKey, rootKey)
	if e != nil {
		t.Fatal(e)
	}
	ca := filepath.Join(t.TempDir(), "origin-ca.pem")
	if os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: rootDER}), 0600) != nil {
		t.Fatal("fixture CA write failed")
	}
	return tls.Certificate{Certificate: [][]byte{der, rootDER}, PrivateKey: leafKey}, ca
}

func dayforceConnectProxy(t *testing.T, origin *httptest.Server) *httptest.Server {
	t.Helper()
	return newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "CONNECT" || r.Host != "jobs.dayforcehcm.com:443" {
			http.Error(w, "fixture scope", 403)
			return
		}
		up, e := net.DialTimeout("tcp", origin.Listener.Addr().String(), time.Second)
		if e != nil {
			http.Error(w, "fixture unavailable", 502)
			return
		}
		peer, buffer, e := w.(http.Hijacker).Hijack()
		if e != nil {
			up.Close()
			return
		}
		defer peer.Close()
		defer up.Close()
		_, _ = buffer.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		if buffer.Flush() != nil {
			return
		}
		done := make(chan struct{})
		go func() { _, _ = io.Copy(up, buffer); up.Close(); close(done) }()
		_, _ = io.Copy(peer, up)
		peer.Close()
		<-done
	}))
}

func dayforceFixtureHTML(token string) string {
	data := map[string]any{"query": map[string]any{"clientNamespace": "fixture", "careerSiteXRefCode": "EXTERNAL"}, "props": map[string]any{"pageProps": map[string]any{"dehydratedState": map[string]any{"queries": []any{map[string]any{"queryKey": []any{"site-info"}, "state": map[string]any{"data": map[string]any{"clientNamespace": "fixture", "jobBoardCode": "EXTERNAL", "jobBoardId": 42, "cultureCode": "en-US", "isoCultureCodes": []string{"en-US"}, "isDisabled": false}}}}}}}}
	encoded, _ := json.Marshal(data)
	encodedToken, _ := json.Marshal(token)
	return `<!doctype html><html><head><script id="__NEXT_DATA__" type="application/json">` + string(encoded) + `</script></head><body><script>fetch("https://jobs.dayforcehcm.com/api/geo/fixture/jobposting/search",{method:"POST",headers:{"content-type":"application/json","x-csrf-token":` + string(encodedToken) + `},body:JSON.stringify({clientNamespace:"fixture",jobBoardCode:"EXTERNAL",cultureCode:"en-US",distanceUnit:0,paginationStart:0})}).then(r=>r.json()).then(()=>document.body.dataset.primed="yes");</script></body></html>`
}

func TestLightpandaDayforceSessionIntegration(t *testing.T) {
	binary := integrationBinary(t)
	const token = "SyntheticDayforceTokenForFixture_1234567890"
	certificate, ca := dayforceOriginTLS(t)
	var mu sync.Mutex
	offsets := []int{}
	invalid := false
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != "jobs.dayforcehcm.com" {
			http.Error(w, "foreign host", 403)
			return
		}
		switch r.URL.Path {
		case "/fixture/EXTERNAL":
			http.SetCookie(w, &http.Cookie{Name: "dayforce-session", Value: "fixture-cookie", Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteLaxMode})
			w.Header().Set("Content-Type", "text/html")
			_, _ = io.WriteString(w, dayforceFixtureHTML(token))
		case "/api/geo/fixture/jobposting/search":
			var payload struct {
				Tenant   string `json:"clientNamespace"`
				Portal   string `json:"jobBoardCode"`
				Culture  string `json:"cultureCode"`
				Distance int    `json:"distanceUnit"`
				Offset   int    `json:"paginationStart"`
			}
			cookie, e := r.Cookie("dayforce-session")
			bad := r.Method != "POST" || r.Header.Get("X-Csrf-Token") != token || e != nil || cookie.Value != "fixture-cookie" || json.NewDecoder(io.LimitReader(r.Body, 8192)).Decode(&payload) != nil || payload.Tenant != "fixture" || payload.Portal != "EXTERNAL" || payload.Culture != "en-US" || payload.Distance != 0
			mu.Lock()
			offsets = append(offsets, payload.Offset)
			invalid = invalid || bad
			mu.Unlock()
			if bad {
				http.Error(w, "invalid private session", 403)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Tdm-Reservation", "0")
			rows := []any{}
			for id := payload.Offset + 1; id <= 45 && len(rows) < 25; id++ {
				rows = append(rows, map[string]any{"jobPostingId": id, "jobBoardId": 42, "clientNamespace": "fixture", "jobTitle": "Engineer", "jobDescription": "<p>Build reliable services.</p>"})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"maxCount": 45, "offset": payload.Offset, "count": len(rows), "jobPostings": rows})
		default:
			http.NotFound(w, r)
		}
	}))
	listener, e := net.Listen("tcp4", "127.0.0.2:0")
	if e != nil {
		t.Fatal(e)
	}
	origin.Listener = listener
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	origin.StartTLS()
	defer origin.Close()
	proxy := dayforceConnectProxy(t, origin)
	fixture := newServiceTLSFixture(t)
	runner := func(ctx context.Context, c Config, task Task) (Result, error) {
		c.Binary = binary
		c.EgressPolicy = defaultEgressPolicy()
		return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
	}
	execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, runner)
	if e != nil {
		t.Fatal(e)
	}
	service, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
	defer stop()
	client := dayforceClientFixture(t, fixture, address)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	held, e := client.Reserve(ctx)
	if e != nil {
		t.Fatal(e)
	}
	request := dayforceRequestFixture()
	request.ExpectedSite.JobBoardID = 42
	request.TimeoutMS = 15000
	session, ready, e := held.StartDayforce(ctx, request)
	if e != nil {
		t.Fatal("pinned Dayforce navigation failed", e)
	}
	defer session.Close()
	if !ready.PublisherChecked || ready.Site.JobBoardID != 42 || ready.Reservation != nil || len(service.slots) != 1 {
		t.Fatal("browser bootstrap or C4 reservation differs")
	}
	for _, offset := range []int{0, 20, 20, 40} {
		p, e := session.Search(offset)
		if e != nil || p.TransportFailed || p.Status != 200 || p.Policy == nil || p.Policy.GetTdmReservationHeader() != "0" {
			t.Fatal("real browser fetch failed", e)
		}
	}
	if e = session.Finish(true); e != nil {
		t.Fatal("cleanup proof failed", e)
	}
	mu.Lock()
	got := append([]int(nil), offsets...)
	bad := invalid
	mu.Unlock()
	if bad || len(got) != 5 || got[0] != 0 || got[1] != 0 || got[2] != 20 || got[3] != 20 || got[4] != 40 {
		t.Fatal("physical first-party requests/cookie/CSRF/session sequence differ")
	}
	if failure := service.failure(); failure != nil && !errors.Is(failure, context.Canceled) {
		t.Fatal("service became unsound", failure)
	}
}
