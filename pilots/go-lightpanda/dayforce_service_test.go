//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
)

func dayforceRequestFixture() df.Request {
	return df.Request{Protocol: df.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), TargetURL: "https://jobs.dayforcehcm.com/fixture/EXTERNAL", Tenant: "fixture", Portal: "EXTERNAL", ExpectedSite: df.Site{JobBoardID: 1, Culture: "en-US", Cultures: []string{"en-US"}}, OffsetOverlap: 5, TimeoutMS: 10000}
}

func dayforceClientFixture(t *testing.T, f serviceTLSFixture, address string) *lp.Client {
	t.Helper()
	dir := t.TempDir()
	identity := f.client.Certificates[0]
	put := func(name string, b []byte) string {
		p := filepath.Join(dir, name)
		if os.WriteFile(p, b, 0600) != nil {
			t.Fatal("client fixture write failed")
		}
		return p
	}
	cert := put("client.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: identity.Certificate[0]}))
	key, e := x509.MarshalPKCS8PrivateKey(identity.PrivateKey)
	if e != nil {
		t.Fatal(e)
	}
	keyFile := put("key.pem", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key}))
	serverPEM, e := os.ReadFile(f.server.CertificatePath)
	if e != nil {
		t.Fatal(e)
	}
	block, _ := pem.Decode(serverPEM)
	server, e := x509.ParseCertificate(block.Bytes)
	if e != nil {
		t.Fatal(e)
	}
	c, e := lp.New(lp.Config{RendererAddress: address, RendererServerName: serviceTestIP, CAPath: f.server.CAPath, ClientCertificatePath: cert, ClientKeyPath: keyFile, CAPin: f.server.CASHA256, ServerLeafPin: dayforceTestSHA(server.Raw), ServerSPKIPin: dayforceTestSHA(server.RawSubjectPublicKeyInfo)})
	if e != nil {
		t.Fatal(e)
	}
	return c
}

func TestDayforcePinnedConversationWaitsForCleanupAndSharesCapacity(t *testing.T) {
	f := newServiceTLSFixture(t)
	cleanup := make(chan struct{})
	cleanupEntered := make(chan struct{})
	var calls atomic.Int64
	execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: f.server.serviceEgressPolicy.egressPolicy}, func(ctx context.Context, c Config, task Task) (Result, error) {
		if validateTask(task) != nil || task.Dayforce == nil {
			t.Error("invalid session task")
		}
		r := task.Dayforce.request
		e := task.Dayforce.converse(ctx, df.Ready{FinalURL: r.TargetURL, Status: 200, Site: r.ExpectedSite, Policy: &runtimev1.ResourcePolicySignals{}}, func(ctx context.Context, offset int) (df.Page, error) {
			calls.Add(1)
			return df.Page{Offset: offset, FinalURL: r.SearchURL(), Status: 200, Body: []byte(`{"maxCount":45,"jobs":[]}`), Policy: &runtimev1.ResourcePolicySignals{}}, nil
		})
		close(cleanupEntered)
		select {
		case <-cleanup:
		case <-ctx.Done():
			return Result{}, ctx.Err()
		}
		return Result{}, e
	})
	if e != nil {
		t.Fatal(e)
	}
	service, address, stop := startRuntimeV1ServiceTest(t, f, execution)
	defer stop()
	client := dayforceClientFixture(t, f, address)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	reservation, e := client.Reserve(ctx)
	if e != nil {
		t.Fatal(e)
	}
	session, ready, e := reservation.StartDayforce(ctx, dayforceRequestFixture())
	if e != nil {
		t.Fatal(e)
	}
	defer session.Close()
	if ready.Status != 200 || len(service.slots) != 1 {
		t.Fatal("session did not retain one C4 slot")
	}
	for _, offset := range []int{0, 0, 20, 40} {
		page, e := session.Search(offset)
		if e != nil || page.Offset != offset {
			t.Fatal("same-session page failed", e)
		}
	}
	done := make(chan error, 1)
	go func() { done <- session.Finish(true) }()
	select {
	case <-cleanupEntered:
	case <-time.After(time.Second):
		t.Fatal("cleanup not reached")
	}
	select {
	case <-done:
		t.Fatal("success escaped before cleanup")
	case <-time.After(20 * time.Millisecond):
	}
	close(cleanup)
	if e := <-done; e != nil {
		t.Fatal(e)
	}
	if calls.Load() != 4 {
		t.Fatal("semantic page operations differ")
	}
}

func TestDayforcePeerDisconnectCancelsLiveFetchAndDisposesSession(t *testing.T) {
	f := newServiceTLSFixture(t)
	fetching := make(chan struct{})
	disposed := make(chan struct{})
	execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: f.server.serviceEgressPolicy.egressPolicy}, func(ctx context.Context, c Config, task Task) (Result, error) {
		defer close(disposed)
		r := task.Dayforce.request
		e := task.Dayforce.converse(ctx, df.Ready{FinalURL: r.TargetURL, Status: 200, Site: r.ExpectedSite, Policy: &runtimev1.ResourcePolicySignals{}}, func(ctx context.Context, _ int) (df.Page, error) {
			close(fetching)
			<-ctx.Done()
			return df.Page{}, ctx.Err()
		})
		return Result{}, e
	})
	if e != nil {
		t.Fatal(e)
	}
	_, address, stop := startRuntimeV1ServiceTest(t, f, execution)
	defer stop()
	client := dayforceClientFixture(t, f, address)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r, e := client.Reserve(ctx)
	if e != nil {
		t.Fatal(e)
	}
	s, _, e := r.StartDayforce(ctx, dayforceRequestFixture())
	if e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { _, e := s.Search(0); done <- e }()
	select {
	case <-fetching:
	case <-time.After(time.Second):
		t.Fatal("fetch not dispatched")
	}
	cancel()
	select {
	case <-disposed:
	case <-time.After(time.Second):
		t.Fatal("disconnect did not cancel browser")
	}
	if e := <-done; e == nil {
		t.Fatal("disconnected page accepted")
	}
}

func TestDayforceInvalidOffsetIsRejectedBeforeContact(t *testing.T) {
	for _, offset := range []int{1, 25, -1, 50000} {
		t.Run(strconv.Itoa(offset), func(t *testing.T) {
			f := newServiceTLSFixture(t)
			var contacts atomic.Int64
			execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: f.server.serviceEgressPolicy.egressPolicy}, func(ctx context.Context, c Config, task Task) (Result, error) {
				r := task.Dayforce.request
				e := task.Dayforce.converse(ctx, df.Ready{FinalURL: r.TargetURL, Status: 200, Site: r.ExpectedSite, Policy: &runtimev1.ResourcePolicySignals{}}, func(context.Context, int) (df.Page, error) { contacts.Add(1); return df.Page{}, nil })
				return Result{}, e
			})
			if e != nil {
				t.Fatal(e)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, f, execution)
			defer stop()
			client := dayforceClientFixture(t, f, address)
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			r, e := client.Reserve(ctx)
			if e != nil {
				t.Fatal(e)
			}
			s, _, e := r.StartDayforce(ctx, dayforceRequestFixture())
			if e != nil {
				t.Fatal(e)
			}
			defer s.Close()
			if _, e = s.Search(offset); e == nil || contacts.Load() != 0 {
				t.Fatal("invalid first offset contacted origin")
			}
		})
	}
}

func dayforceTestSHA(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
