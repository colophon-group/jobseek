package worker

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"google.golang.org/protobuf/proto"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func detailActionIdentity(t *testing.T, root *x509.Certificate, key ed25519.PrivateKey, serial int64, server bool) (tls.Certificate, *x509.Certificate) {
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

func detailActionTLSFixture(t *testing.T, profile queue.WorkdayDetailProfile, html, mode string, cancel context.CancelFunc) (*NativeRenderedDetails, <-chan struct{}) {
	t.Helper()
	fetchURL, fetchErr := dom.AlternateFetchURL(profile.SourceURL, profile.DOMConfig["fetch_url_transform"])
	if fetchErr != nil {
		t.Fatal(fetchErr)
	}

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
	server, serverLeaf := detailActionIdentity(t, root, key, 2, true)
	identity, _ := detailActionIdentity(t, root, key, 3, false)
	roots := x509.NewCertPool()
	roots.AddCert(root)
	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, Certificates: []tls.Certificate{server}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots, NextProtos: []string{"jobseek-lightpanda-b0/1"}, SessionTicketsDisabled: true})
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
	c, err := NewNativeRenderedDetails(lp.Config{RendererAddress: listener.Addr().String(), RendererServerName: "127.0.0.1", CAPath: caPath, ClientCertificatePath: certPath, ClientKeyPath: keyPath, CAPin: hash(der), ServerLeafPin: hash(serverLeaf.Raw), ServerSPKIPin: hash(serverLeaf.RawSubjectPublicKeyInfo)})
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
		hello := []byte(`{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`)
		record, e := framing.EncodeRecord(hello, 512)
		if e != nil || lp.WriteAll(conn, record) != nil {
			return
		}
		body, e := framing.ReadRecord(conn, actions.RequestLimit)
		var request actions.Request
		if e != nil || actions.Decode(body, actions.RequestLimit, &request) != nil || !request.Valid() {
			t.Error("native detail did not send typed actions")
			return
		}
		if request.ConfigFingerprint != profile.EffectiveBoardSHA256 || len(request.Actions) != 2 || request.Actions[0].Kind != "wait" || request.Actions[1].Kind != "click" {
			t.Error("action order or source binding differs")
		}
		var input runtimev1.BrowserExecutionInput
		if proto.Unmarshal(request.Input, &input) != nil || input.Plan.TargetUrl != fetchURL {
			t.Error("action navigation source differs")
		}
		var zero [1]byte
		if _, e := io.ReadFull(conn, zero[:]); e != nil || zero[0] != 0 {
			return
		}
		if mode == "cancel" {
			cancel()
			conn.Read(zero[:])
			return
		}
		held := heldRenderedResult(html, fetchURL, 200)
		if mode == "reserved" {
			one := "1"
			held.GetSuccess().ResourcePolicy.TdmReservationHeader = &one
		}
		payload, e := proto.Marshal(held)
		if e != nil {
			return
		}
		response := actions.Response{Protocol: actions.Protocol, RequestID: request.RequestID, ConfigFingerprint: request.ConfigFingerprint, Result: payload}
		if mode == "changed-response-binding" {
			response.ConfigFingerprint = strings.Repeat("c", 64)
		}
		body, e = json.Marshal(response)
		if e != nil {
			return
		}
		record, e = framing.EncodeRecord(body, actions.ResponseLimit)
		if e == nil {
			lp.WriteAll(conn, record)
		}
	}()
	return c, done
}
func TestNativeRenderedDetailsExecuteBoundActionsOverPinnedMutualTLS(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		for _, mode := range []string{"success", "reserved", "changed-response-binding", "cancel"} {
			t.Run(scraper+"/"+mode, func(t *testing.T) {
				options := map[string]any{"render": true, "actions": []any{map[string]any{"action": "wait", "ms": float64(0)}, map[string]any{"action": "click", "selector": "#details", "required": true}}}
				html := nativeDOMHTML
				profile := queue.WorkdayDetailProfile{BoardID: "11111111-1111-4111-8111-111111111111", CompanyID: "22222222-2222-4222-8222-222222222222", SourceURL: "https://jobs.example.net/job/42", EffectiveBoardSHA256: strings.Repeat("a", 64)}
				switch scraper {
				case "dom":
					options["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "h2", "text": "Role", "offset": 1, "field": "description", "html": true}}
					profile.Profile = "dom.rendered-detail/v1"
					profile.DOMConfig = options
				case "json-ld":
					html = nativeJSONLDHTML
					profile.Profile = "jsonld.rendered-detail/v1"
					profile.JSONLDConfig = options
				case "nextdata":
					html = nativeEmbeddedHTML
					options["path"] = "job"
					options["fields"] = map[string]any{"title": "title", "description": "description"}
					profile.Profile = "embedded.rendered-detail/v1"
					profile.EmbeddedNextdata = true
					profile.EmbeddedConfig = options
				}
				ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				renderer, done := detailActionTLSFixture(t, profile, html, mode, cancel)
				defer cancel()
				out, reserved, e := renderer.Fetch(ctx, profile)
				select {
				case <-done:
				case <-time.After(6 * time.Second):
					t.Fatal("action reservation cleanup did not finish", e)
				}
				switch mode {
				case "success":
					if e != nil || reserved != nil || out["title"] != "Senior Software Engineer" || out["description"] == nil {
						t.Fatal("post-action canonical fields differ", e, out["title"])
					}
				case "reserved":
					if e != nil || reserved == nil || out != nil {
						t.Fatal("post-action publisher policy lost", e)
					}
				case "changed-response-binding":
					if !errors.Is(e, actions.ErrActions) || out != nil || reserved != nil {
						t.Fatal("foreign action response accepted", e)
					}
				case "cancel":
					if !errors.Is(e, context.Canceled) || out != nil {
						t.Fatal("claim cancellation lost", e)
					}
				}
			})
		}
	}
}

func TestRenderedAlternateReadUsesBoundNavigationAndCanonicalDefaults(t *testing.T) {
	for _, mode := range []string{"success", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			source := "https://jobs.example.net/job/42"
			profile := queue.WorkdayDetailProfile{BoardID: "11111111-1111-4111-8111-111111111111", CompanyID: "22222222-2222-4222-8222-222222222222", SourceURL: source, Profile: "dom.rendered-detail/v1", EffectiveBoardSHA256: strings.Repeat("a", 64), DOMConfig: map[string]any{
				"render": true, "steps": []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "h2", "text": "Role", "offset": 1, "field": "description", "html": true}},
				"actions":             []any{map[string]any{"action": "wait", "ms": float64(0)}, map[string]any{"action": "click", "selector": "#details", "required": true}},
				"fetch_url_transform": map[string]any{"find": "/job/", "replace": "/print/"},
				"defaults_by_url":     map[string]any{source: map[string]any{"employment_type": "FULL_TIME"}},
			}}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			renderer, done := detailActionTLSFixture(t, profile, nativeDOMHTML, mode, cancel)
			content, reservation, err := renderer.Fetch(ctx, profile)
			select {
			case <-done:
			case <-time.After(6 * time.Second):
				t.Fatal("alternate read cleanup failed")
			}
			if mode == "success" {
				if err != nil || reservation != nil || content["title"] != "Senior Software Engineer" || content["employment_type"] != "FULL_TIME" {
					t.Fatal("alternate navigation lost canonical fields", err, content)
				}
			} else if err != nil || reservation == nil || content != nil {
				t.Fatal("alternate read lost publisher policy", err)
			}
		})
	}
}
