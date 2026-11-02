package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
)

func acceptanceHash(body []byte) string { h := sha256.Sum256(body); return hex.EncodeToString(h[:]) }

// Synthetic credentials and a loopback TLS renderer exercise the native wire
// conversation. The assignment's public target is never fetched by this peer.
func acceptanceFixture(t *testing.T, mode string) (lp.Config, string, <-chan error) {
	t.Helper()
	pub, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "fixture-root"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, pub, key)
	if err != nil {
		t.Fatal(err)
	}
	ca, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	leaf := func(serial int64, server bool) (tls.Certificate, *x509.Certificate) {
		t.Helper()
		pub, priv, e := ed25519.GenerateKey(rand.Reader)
		if e != nil {
			t.Fatal(e)
		}
		cert := &x509.Certificate{SerialNumber: big.NewInt(serial), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
		if server {
			cert.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
			cert.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		}
		b, e := x509.CreateCertificate(rand.Reader, cert, ca, pub, key)
		if e != nil {
			t.Fatal(e)
		}
		parsed, e := x509.ParseCertificate(b)
		if e != nil {
			t.Fatal(e)
		}
		return tls.Certificate{Certificate: [][]byte{b}, PrivateKey: priv}, parsed
	}
	server, serverLeaf := leaf(2, true)
	client, _ := leaf(3, false)
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if mode == "clientauth" {
		roots = x509.NewCertPool()
	}
	alpn := "jobseek-lightpanda-b0/1"
	if mode == "alpn" {
		alpn = "wrong"
	}
	maxTLS := uint16(tls.VersionTLS13)
	if mode == "tls12" {
		maxTLS = tls.VersionTLS12
	}
	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: maxTLS, Certificates: []tls.Certificate{server}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots, NextProtos: []string{alpn}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	root := t.TempDir()
	pk, err := x509.MarshalPKCS8PrivateKey(client.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	contents := map[string][]byte{"ca.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), "client.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: client.Certificate[0]}), "client-key.pem": pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pk}), "ca.sha256": []byte(acceptanceHash(der) + "\n"), "server-leaf.sha256": []byte(acceptanceHash(serverLeaf.Raw) + "\n"), "server-spki.sha256": []byte(acceptanceHash(serverLeaf.RawSubjectPublicKeyInfo) + "\n")}
	for n, b := range contents {
		if e := os.WriteFile(filepath.Join(root, n), b, 0600); e != nil {
			t.Fatal(e)
		}
	}
	c, digest, err := acceptanceCredentials(root)
	if err != nil {
		t.Fatal(err)
	}
	if c.RendererAddress != "10.0.0.5:9443" || c.RendererServerName != "10.0.0.5" {
		t.Fatal("installed identity changed")
	}
	c.RendererAddress = listener.Addr().String()
	c.RendererServerName = "127.0.0.1"
	switch mode {
	case "ca":
		c.CAPin = strings.Repeat("0", 64)
	case "leaf":
		c.ServerLeafPin = strings.Repeat("0", 64)
	case "spki":
		c.ServerSPKIPin = strings.Repeat("0", 64)
	case "hostname":
		c.RendererServerName = "wrong.invalid"
	case "client":
		c.ClientCertificatePath = c.CAPath
	}
	done := make(chan error, 1)
	go func() {
		conn, e := listener.Accept()
		if e != nil {
			done <- e
			return
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
		e = func() error {
			hello := []byte(`{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`)
			if mode == "hello" {
				hello = []byte("invalid")
			}
			record, _ := framing.EncodeRecord(hello, 512)
			if e := lp.WriteAll(conn, record); e != nil {
				return e
			}
			payload, e := framing.ReadRecord(conn, 128*1024+3)
			if e != nil {
				return e
			}
			input := &runtimev1.BrowserExecutionInput{}
			if e = proto.Unmarshal(payload, input); e != nil {
				return e
			}
			expected := &runtimev1.BrowserExecutionInput{
				Assignment: &runtimev1.BrowserAssignment{Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, CapabilityClass: runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION, ServiceLane: runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA, RoutingRevision: "host-acceptance-v1"},
				Plan:       &runtimev1.BrowserPlan{ContractVersion: "crawler.runtime/v1", TargetUrl: "https://example.com/", RequiredCapabilities: []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}, Navigation: &runtimev1.NavigationPlan{WaitUntil: runtimev1.WaitCondition_WAIT_CONDITION_LOAD, TimeoutMs: 15000, OriginRequestId: "host-acceptance-public"}, OriginOperations: []*runtimev1.OriginOperationRef{{OriginRequestId: "host-acceptance-public", OperationSequence: 1, Role: "navigation", RequestFingerprint: acceptanceHash([]byte(`{"body":"","headers":[],"method":"GET","url":"https://example.com/"}`))}}},
			}
			if !proto.Equal(input, expected) {
				return errors.New("acceptance wire assignment changed")
			}
			end := []byte{1}
			if _, e = io.ReadFull(conn, end); e != nil || end[0] != 0 {
				return errors.New("missing application close")
			}
			if mode == "oversize" {
				var prefix [10]byte
				n := binary.PutUvarint(prefix[:], 2*1024*1024)
				return lp.WriteAll(conn, prefix[:n])
			}
			if mode == "budget" {
				_, e = conn.Read(end)
				return e
			}
			html := []byte("<html>Example Domain</html>")
			status := uint32(200)
			manifest := &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(html)), TotalSha256: acceptanceHash(html), Chunks: []*runtimev1.DataChunk{{Sequence: 0, SizeBytes: uint64(len(html)), Sha256: acceptanceHash(html), Storage: &runtimev1.DataChunk_InlineBody{InlineBody: html}}}}
			success := &runtimev1.BrowserSuccess{Status: &status, FinalUrl: rendererAcceptanceTarget, Html: manifest, ResourcePolicy: &runtimev1.ResourcePolicySignals{}}
			result := &runtimev1.BrowserResult{ContractVersion: "crawler.runtime/v1", Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Success{Success: success}}
			switch mode {
			case "redirect":
				success.FinalUrl = "https://example.com/redirect"
			case "status":
				status = 302
			case "empty":
				manifest.TotalSizeBytes = 0
			case "missing":
				success.Html = nil
			case "incomplete":
				manifest.Complete = false
			case "hash":
				manifest.TotalSha256 = strings.Repeat("0", 64)
			case "policy":
				v := "1"
				success.ResourcePolicy.TdmReservationHeader = &v
			case "backend":
				result.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
			case "unknown":
				result.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
			}
			payload, e = proto.MarshalOptions{Deterministic: true}.Marshal(result)
			if e != nil {
				return e
			}
			record, e = framing.EncodeRecord(payload, 2*1024*1024)
			if e != nil {
				return e
			}
			if mode == "trailing" {
				record = append(record, 1)
			}
			return lp.WriteAll(conn, record)
		}()
		done <- e
	}()
	return c, digest, done
}

func TestRendererAcceptanceNativeConversation(t *testing.T) {
	for _, mode := range []string{"success", "ca", "leaf", "spki", "hostname", "client", "clientauth", "tls12", "alpn", "hello", "redirect", "status", "empty", "missing", "incomplete", "hash", "policy", "backend", "unknown", "trailing", "oversize", "budget"} {
		t.Run(mode, func(t *testing.T) {
			c, digest, done := acceptanceFixture(t, mode)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			if mode == "budget" {
				ctx, cancel = context.WithTimeout(ctx, 75*time.Millisecond)
				defer cancel()
			}
			started := time.Now()
			report, err := executeRendererAcceptance(ctx, c, digest, strings.Repeat("a", 40))
			if mode == "success" {
				if err != nil || report == nil || report.Status != "accepted" || report.Target != "https://example.com/" || report.HTTPStatus != 200 || report.CredentialBundleSHA256 != digest || report.CrawlerSourceRevision != strings.Repeat("a", 40) {
					t.Fatalf("positive acceptance lost: %v", err)
				}
				if e := <-done; e != nil {
					t.Fatal(e)
				}
			} else if report != nil || !errors.Is(err, errRendererAcceptance) {
				t.Fatalf("accepted invalid %s", mode)
			}
			if mode == "budget" && time.Since(started) > time.Second {
				t.Fatal("context budget was ignored")
			}
		})
	}
}

func TestRendererAcceptanceCredentialDigestAndBoundaries(t *testing.T) {
	root := t.TempDir()
	h := sha256.New()
	for _, name := range acceptanceCredentialFiles {
		body := []byte(name)
		if strings.HasSuffix(name, ".sha256") {
			body = []byte(strings.Repeat("a", 64) + "\n")
		}
		if err := os.WriteFile(filepath.Join(root, name), body, 0400); err != nil {
			t.Fatal(err)
		}
		h.Write([]byte(name + "\x00"))
		h.Write(body)
		h.Write([]byte{0})
	}
	if err := os.Chmod(root, 0711); err != nil {
		t.Fatal(err)
	}
	_, got, err := acceptanceCredentials(root)
	if err != nil || got != hex.EncodeToString(h.Sum(nil)) {
		t.Fatal("fixed credential digest changed")
	}
	key := filepath.Join(root, "client-key.pem")
	if err := os.Remove(key); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "client.pem"), key); err != nil {
		t.Fatal(err)
	}
	if _, _, err := acceptanceCredentials(root); err == nil {
		t.Fatal("credential symlink accepted")
	}
	os.Remove(key)
	os.WriteFile(key, make([]byte, 128*1024+1), 0600)
	if _, _, err := acceptanceCredentials(root); err == nil {
		t.Fatal("unbounded credential accepted")
	}
	os.Remove(key)
	os.WriteFile(key, []byte("fixture-key"), 0600)
	os.Remove(filepath.Join(root, "ca.sha256"))
	os.WriteFile(filepath.Join(root, "ca.sha256"), []byte("bad pin"), 0600)
	if _, _, err := acceptanceCredentials(root); err == nil {
		t.Fatal("invalid pin accepted")
	}
}

func TestRendererAcceptanceCommandRejectsWithoutDaemonStartup(t *testing.T) {
	t.Setenv("LOCAL_DATABASE_URL", "invalid secret endpoint")
	t.Setenv("JOBSEEK_LANGUAGE_MODEL_PATH", "invalid secret path")
	oldArgs, oldRevision := os.Args, sourceRevision
	defer func() { os.Args = oldArgs; sourceRevision = oldRevision }()
	os.Args = []string{"go-ordinary-worker", "--renderer-acceptance"}
	sourceRevision = strings.Repeat("a", 40)
	err := run()
	if !errors.Is(err, errRendererAcceptance) {
		t.Fatalf("did not use isolated command: %v", err)
	}
	if strings.Contains(err.Error(), "secret") {
		t.Fatal("configuration leaked")
	}
}
