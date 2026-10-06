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
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// This peer exercises the production client/worker conversation. The pinned
// Lightpanda HTTPS/CDP implementation is separately mandatory in pilot CI.
func dayforcePipelinePeer(t *testing.T, mode string, changed func()) *NativeRenderedDetails {
	t.Helper()
	pub, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	root := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "private DF pipeline fixture"}, IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour)}
	der, e := x509.CreateCertificate(rand.Reader, root, root, pub, key)
	if e != nil {
		t.Fatal(e)
	}
	root, _ = x509.ParseCertificate(der)
	identity := func(server bool) (tls.Certificate, *x509.Certificate) {
		p, k, e := ed25519.GenerateKey(rand.Reader)
		if e != nil {
			t.Fatal(e)
		}
		leaf := &x509.Certificate{SerialNumber: big.NewInt(2), BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, NotBefore: root.NotBefore, NotAfter: root.NotAfter}
		if server {
			leaf.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
			leaf.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		} else {
			u, _ := url.Parse("spiffe://jobseek/crawler/lightpanda-b0")
			leaf.URIs = []*url.URL{u}
			leaf.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
		}
		d, e := x509.CreateCertificate(rand.Reader, leaf, root, p, key)
		if e != nil {
			t.Fatal(e)
		}
		parsed, _ := x509.ParseCertificate(d)
		return tls.Certificate{Certificate: [][]byte{d}, PrivateKey: k}, parsed
	}
	server, serverLeaf := identity(true)
	client, clientLeaf := identity(false)
	roots := x509.NewCertPool()
	roots.AddCert(root)
	listener, e := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, Certificates: []tls.Certificate{server}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots, NextProtos: []string{"jobseek-lightpanda-b0/1"}, VerifyConnection: func(s tls.ConnectionState) error {
		if len(s.PeerCertificates) != 1 || !s.PeerCertificates[0].Equal(clientLeaf) {
			return df.ErrProtocol
		}
		return nil
	}})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { listener.Close() })
	directory := t.TempDir()
	put := func(name string, b []byte) string {
		p := filepath.Join(directory, name)
		if os.WriteFile(p, b, 0600) != nil {
			t.Fatal("private fixture credential write failed")
		}
		return p
	}
	ca := put("ca.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	cert := put("client.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: client.Certificate[0]}))
	private, _ := x509.MarshalPKCS8PrivateKey(client.PrivateKey)
	keyPath := put("client-key.pem", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: private}))
	hash := func(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, e := listener.Accept()
		if e != nil {
			return
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
		write := func(v any, limit uint64) bool {
			b, e := json.Marshal(v)
			if e != nil {
				return false
			}
			record, e := framing.EncodeRecord(b, limit)
			return e == nil && lp.WriteAll(conn, record) == nil
		}
		hello := []byte(`{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`)
		record, _ := framing.EncodeRecord(hello, 512)
		if lp.WriteAll(conn, record) != nil {
			return
		}
		body, e := framing.ReadRecord(conn, df.RequestLimit)
		var request df.Request
		if e != nil || df.Decode(body, df.RequestLimit, &request) != nil || !request.Valid() {
			return
		}
		marker, e := framing.ReadRecord(conn, 1)
		if e != nil || len(marker) != 0 {
			return
		}
		ready := df.Ready{FinalURL: request.TargetURL, Status: 200, Site: request.ExpectedSite, Policy: &runtimev1.ResourcePolicySignals{}, PublisherChecked: true}
		if mode == "site-change" {
			ready.Site.JobBoardID++
		}
		if mode == "main-reserved" {
			ready.Reservation = &df.Reservation{Source: "meta"}
		}
		if !write(df.Frame{RequestID: request.RequestID, Ready: &ready}, df.FrameLimit) {
			return
		}
		for {
			raw, e := framing.ReadRecord(conn, df.CommandLimit)
			var c df.Command
			if e != nil || df.Decode(raw, df.CommandLimit, &c) != nil || !c.Valid() {
				return
			}
			if c.Finish != nil {
				if changed != nil {
					changed()
				}
				reason := "aborted"
				success := false
				if *c.Finish {
					success = true
					reason = "completed"
				}
				if mode == "cleanup" {
					success = false
					reason = "cleanup"
				}
				write(df.Frame{RequestID: request.RequestID, Sequence: c.Sequence, Closed: &df.Closed{Success: success, Reason: reason}}, df.FrameLimit)
				return
			}
			offset := *c.Offset
			page := df.Page{Offset: offset, FinalURL: request.SearchURL(), Status: 200, Policy: &runtimev1.ResourcePolicySignals{}}
			rows := []any{}
			total := 1
			if mode == "late-failure" {
				total = 26
				if offset > 0 {
					page.Status = 403
				}
			}
			if offset == 0 {
				count := 1
				if total > 1 {
					count = 25
				}
				for i := 1; i <= count; i++ {
					rows = append(rows, map[string]any{"jobPostingId": i, "jobBoardId": request.ExpectedSite.JobBoardID, "clientNamespace": request.Tenant, "jobTitle": "Senior Software Engineer", "jobDescription": "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>", "postingLocations": []any{map[string]any{"formattedAddress": "Zurich"}}})
				}
			}
			page.Body, _ = json.Marshal(map[string]any{"maxCount": total, "offset": offset, "count": len(rows), "jobPostings": rows})
			if mode == "api-reserved" {
				v := "1"
				page.Policy.TdmReservationHeader = &v
			}
			if !write(df.Frame{RequestID: request.RequestID, Sequence: c.Sequence, Page: &page}, df.FrameLimit) {
				return
			}
		}
	}()
	t.Cleanup(func() {
		listener.Close()
		select {
		case <-done:
		case <-time.After(11 * time.Second):
			t.Error("private renderer peer did not stop")
		}
	})
	r, e := NewNativeRenderedDetails(lp.Config{RendererAddress: listener.Addr().String(), RendererServerName: "127.0.0.1", CAPath: ca, ClientCertificatePath: cert, ClientKeyPath: keyPath, CAPin: hash(der), ServerLeafPin: hash(serverLeaf.Raw), ServerSPKIPin: hash(serverLeaf.RawSubjectPublicKeyInfo)})
	if e != nil {
		t.Fatal(e)
	}
	return r
}

func dayforcePipelineHTML(disabled bool) string {
	data := map[string]any{"query": map[string]any{"clientNamespace": "fixture", "careerSiteXRefCode": "EXTERNAL"}, "props": map[string]any{"pageProps": map[string]any{"dehydratedState": map[string]any{"queries": []any{map[string]any{"queryKey": []any{"site-info"}, "state": map[string]any{"data": map[string]any{"clientNamespace": "fixture", "jobBoardCode": "EXTERNAL", "jobBoardId": 42, "cultureCode": "en-US", "isoCultureCodes": []string{"en-US"}, "isDisabled": disabled}}}}}}}}
	b, _ := json.Marshal(data)
	return `<script id="__NEXT_DATA__">` + string(b) + `</script>`
}

func TestRealDayforceSessionOwnedCanonicalPolicyFailuresAndSettlement(t *testing.T) {
	for _, mode := range []string{"new", "main-reserved", "api-reserved", "site-change", "late-failure", "cleanup", "changed-binding", "gone-http", "disabled-http"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "dayforce", `{"tenant":"fixture","portal":"EXTERNAL","scraper_type":"skip","offset_overlap":5}`, "https://jobs.dayforcehcm.com/fixture/EXTERNAL", queue.Browser, queue.Simple)
			ctx := context.Background()
			if claim, e := f.a.Claim(ctx, queue.Simple); e != nil || claim != nil {
				t.Fatal("simple worker claimed browser monitor", e)
			}
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal(e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			var changed func()
			if mode == "changed-binding" {
				changed = func() {
					if _, e := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"offset_overlap":10}'::jsonb WHERE id=$1::uuid`, f.board); e != nil {
						t.Error(e)
					}
				}
			}
			renderer := dayforcePipelinePeer(t, mode, changed)
			http := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "GET" || r.URL.Path != "/fixture/EXTERNAL" || r.Host != "jobs.dayforcehcm.com" {
					t.Error("HTTP bootstrap left source")
				}
				if mode == "gone-http" {
					w.WriteHeader(410)
					return
				}
				_, _ = io.WriteString(w, dayforcePipelineHTML(mode == "disabled-http"))
			}))
			result, e := RunGreenhouseClaim(ctx, f.a, claim, http, richPipelinePreparer(t, f), circuits, renderer)
			if mode == "changed-binding" {
				if e == nil || result.Settled {
					t.Fatal("changed binding retained authority")
				}
				return
			}
			if e != nil || result == nil || !result.Settled {
				t.Fatal("browser pipeline did not settle", e)
			}
			var failures, gone int
			var reserved bool
			if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
				t.Fatal(e)
			}
			var due time.Time
			if e = f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&due); e != nil {
				t.Fatal(e)
			}
			score, e := f.r.ZScore(ctx, "monitors_browser:dayforce", f.board).Result()
			if e != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 {
				t.Fatal("browser deadline/lease conservation differs", e)
			}
			if mode == "main-reserved" || mode == "api-reserved" {
				if !reserved || failures != 0 || result.Batches.Inserted != 0 {
					t.Fatal("reservation wrote content")
				}
				return
			}
			if mode == "gone-http" || mode == "disabled-http" {
				if gone != 1 || failures != 0 || result.Batches.Inserted != 0 {
					t.Fatal("gone evidence failed")
				}
				return
			}
			if mode != "new" {
				if failures != 1 || result.Batches.Inserted != 0 {
					t.Fatal("failed session wrote a successful prefix")
				}
				return
			}
			var id, title, html string
			var detail bool
			if e = f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, "https://jobs.dayforcehcm.com/en-US/fixture/EXTERNAL/jobs/1").Scan(&id, &title, &detail); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid LIMIT 1", id).Scan(&html); e != nil || title != "Senior Software Engineer" || detail || !strings.Contains(html, "100000-120000") {
				t.Fatal("canonical rich content/skip detail differs", e)
			}
			var currency string
			var locations, technologies []int32
			if e = f.pg.QueryRow(ctx, "SELECT salary_currency,location_ids,technology_ids FROM job_posting WHERE id=$1::uuid", id).Scan(&currency, &locations, &technologies); e != nil || currency != "CHF" || len(locations) != 1 || locations[0] != 2 || len(technologies) != 1 || technologies[0] != 4 {
				t.Fatal("canonical salary/location/technology enrichment differs", e)
			}
		})
	}
}
