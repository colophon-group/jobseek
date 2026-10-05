package lightpandaclient

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

func testIdentity(t *testing.T, root *x509.Certificate, key ed25519.PrivateKey, serial int64, server bool) (tls.Certificate, *x509.Certificate) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf := &x509.Certificate{SerialNumber: big.NewInt(serial), Subject: pkix.Name{CommonName: "fixture"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature}
	if server {
		leaf.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
		leaf.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
	} else {
		leaf.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
	}
	der, err := x509.CreateCertificate(rand.Reader, leaf, root, pub, key)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: priv}, parsed
}

func fixture(t *testing.T, mode string) (*Client, <-chan struct{}) {
	t.Helper()
	pub, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	root := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "fixture-root"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, root, root, pub, key)
	if err != nil {
		t.Fatal(err)
	}
	root, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	server, serverLeaf := testIdentity(t, root, key, 2, true)
	identity, _ := testIdentity(t, root, key, 3, false)
	roots := x509.NewCertPool()
	roots.AddCert(root)
	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, Certificates: []tls.Certificate{server}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots, NextProtos: []string{rendererALPN}, SessionTicketsDisabled: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	dir := t.TempDir()
	put := func(name string, body []byte) string {
		p := filepath.Join(dir, name)
		if os.WriteFile(p, body, 0600) != nil {
			t.Fatal("fixture file failed")
		}
		return p
	}
	caPath := put("ca.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	certPath := put("client.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: identity.Certificate[0]}))
	pk, err := x509.MarshalPKCS8PrivateKey(identity.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	keyPath := put("client-key.pem", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pk}))
	hash := func(b []byte) string { d := sha256.Sum256(b); return hex.EncodeToString(d[:]) }
	c, err := New(Config{RendererAddress: listener.Addr().String(), RendererServerName: "127.0.0.1", CAPath: caPath, ClientCertificatePath: certPath, ClientKeyPath: keyPath, CAPin: hash(der), ServerLeafPin: hash(serverLeaf.Raw), ServerSPKIPin: hash(serverLeaf.RawSubjectPublicKeyInfo)})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, e := listener.Accept()
		if e != nil {
			return
		}
		defer conn.Close()
		conn.SetDeadline(time.Now().Add(5 * time.Second))
		hello := canonicalHello
		if mode == "hello" {
			hello = []byte("wrong")
		}
		record, _ := framing.EncodeRecord(hello, helloFrameLimit)
		if WriteAll(conn, record) != nil {
			return
		}
		if mode == "hello" {
			return
		}
		payload, e := framing.ReadRecord(conn, inputFrameLimit)
		if e != nil {
			return
		}
		var input runtimev1.BrowserExecutionInput
		if proto.Unmarshal(payload, &input) != nil {
			return
		}
		zero := []byte{1}
		if _, e = conn.Read(zero); e != nil || zero[0] != 0 {
			return
		}
		if mode == "cancel" {
			conn.Read(zero)
			return
		}
		html := []byte("<h1>Engineer</h1>")
		status := uint32(200)
		result := &runtimev1.BrowserResult{ContractVersion: runtimeContract, Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{FinalUrl: input.Plan.TargetUrl, Status: &status, Html: &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(html)), TotalSha256: hash(html), Chunks: []*runtimev1.DataChunk{{Sequence: 0, SizeBytes: uint64(len(html)), Sha256: hash(html), Storage: &runtimev1.DataChunk_InlineBody{InlineBody: html}}}}}}}
		if mode == "backend" {
			result.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
		}
		if mode == "unknown" {
			result.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
		}
		payload, _ = proto.MarshalOptions{Deterministic: true}.Marshal(result)
		record, _ = framing.EncodeRecord(payload, resultFrameLimit)
		WriteAll(conn, record)
		if mode == "trailing" {
			WriteAll(conn, []byte{1})
		}
	}()
	return c, done
}

func TestPinnedMutualTLSConversationAndFailureBoundaries(t *testing.T) {
	for _, mode := range []string{"success", "hello", "backend", "unknown", "trailing", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			c, done := fixture(t, mode)
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			held, err := c.Reserve(ctx)
			if mode == "hello" {
				if err == nil {
					held.Close()
					t.Fatal("invalid service hello accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			defer held.Close()
			input := &runtimev1.BrowserExecutionInput{Plan: &runtimev1.BrowserPlan{TargetUrl: "https://jobs.example.com/position", Navigation: &runtimev1.NavigationPlan{TimeoutMs: 1000}}}
			if mode == "cancel" {
				go func() { time.Sleep(10 * time.Millisecond); cancel() }()
			}
			body, err := held.Execute(ctx, input)
			if mode == "success" {
				if err != nil || len(body) == 0 {
					t.Fatal("verified service result lost", err)
				}
				if _, err = held.Execute(ctx, input); err == nil {
					t.Fatal("reservation reused")
				}
			} else if err == nil {
				t.Fatal("invalid conversation accepted", mode)
			}
			held.Close()
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("conversation did not terminate")
			}
		})
	}
}
