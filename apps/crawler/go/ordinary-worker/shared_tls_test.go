package worker

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func explicitTLSFixtureClient(t *testing.T, handler http.HandlerFunc, skip, http2 bool) *VerifiedDirectHTTP {
	t.Helper()
	server := httptest.NewUnstartedServer(handler)
	server.EnableHTTP2 = http2
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	t.Cleanup(server.Close)
	key, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	root := x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Unrelated fixture CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, e := x509.CreateCertificate(rand.Reader, &root, &root, &key.PublicKey, key)
	if e != nil {
		t.Fatal(e)
	}
	bundle := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	client, e := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle, InternalHosts: []string{"example.com"}, SkipSSL: skip, EnableHTTP2: http2})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(client.CloseIdleConnections)
	transport := client.client.Transport.(*directTransport)
	// A protected fixture dialer sends the original public hostname to this
	// owned TLS server. The protected fixture CA deliberately does not trust this server.
	destination := server.Listener.Addr().String()
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "example.com:443" {
			t.Error("unexpected dial target")
			return nil, ErrUnsafeURL
		}
		return (&net.Dialer{}).DialContext(ctx, network, destination)
	}
	return client
}

func TestExplicitHTTPMonitorTLSExceptionDoesNotRelaxDefaultOrNetworkPolicy(t *testing.T) {
	for _, http2 := range []bool{false, true} {
		for _, skip := range []bool{false, true} {
			client := explicitTLSFixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Host != "example.com" || http2 != (r.ProtoMajor == 2) {
					t.Error("original origin/protocol changed")
				}
				io.WriteString(w, "complete body")
			}, skip, http2)
			response, e := client.client.Get("https://example.com/careers")
			if !skip {
				if e == nil {
					response.Body.Close()
					t.Fatal("default certificate validation was bypassed")
				}
			} else {
				if e != nil {
					t.Fatal(e)
				}
				body, e := io.ReadAll(response.Body)
				response.Body.Close()
				if e != nil || string(body) != "complete body" {
					t.Fatal("explicit exception did not produce complete body", e)
				}
			}
			for _, url := range []string{"http://127.0.0.1/", "https://localhost/"} {
				response, e := client.client.Get(url)
				if e == nil {
					response.Body.Close()
					t.Fatal("TLS exception relaxed unrelated network policy")
				}
			}
		}
	}
	if _, e := NewVerifiedDirectHTTP(DirectHTTPConfig{SkipSSL: true}); e == nil {
		t.Fatal("exception bypassed protected CA startup input")
	}
}
