package worker

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealNativeExecutableUsesCanonicalProxyDetailWithoutBoardMetadataInPostingSnapshot(t *testing.T) {
	f, _ := independentDetailOwnedSetup(t, `{"scraper_type":"json-ld","scraper_config":{"proxy":true,"defaults":{"language":"en"}}}`, "http://example.com/job/native-proxy", queue.Simple)
	ctx := context.Background()
	if f.r.HExists(ctx, "scrape:"+f.original, "metadata").Val() {
		t.Fatal("fixture no longer reproduces posting-only queue snapshot")
	}
	var calls atomic.Int32
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "GET" || r.URL.String() != "http://example.com/job/native-proxy" || r.Header.Get("Proxy-Authorization") == "" {
			t.Error("canonical detail did not use configured authenticated proxy")
			w.WriteHeader(407)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, nativeJSONLDHTML)
	}))
	defer proxy.Close()
	u, _ := url.Parse(proxy.URL)
	u.User = url.UserPassword("synthetic-user", "synthetic-password")
	e := newNativeExecutableFixture(t, f, privatePipelineReferenceDSN(t, f))
	e.env = append(e.env, "PROXY_PROVIDER=webshare", "WEBSHARE_PROXY_URL="+u.String(), "INTERNAL_HOSTS_ALLOW=127.0.0.1")
	p := e.start(t, "canonical-proxy-detail")
	waitNativeFixture(t, p, "canonical proxy detail settlement", func() bool {
		var title, html string
		err := f.pg.QueryRow(ctx, `SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id JOIN ordinary_worker_write_fence w ON w.task_id=p.id WHERE p.id=$1::uuid AND w.state='completed'`, f.original).Scan(&title, &html)
		return err == nil && title == "Senior Software Engineer" && strings.Contains(html, "Salary CHF") && calls.Load() == 1 && f.r.ZCard(ctx, "inflight:simple").Val() == 0
	})
	if err := p.command.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	if err := p.wait(t); err != nil {
		t.Fatal("installed proxy detail runtime did not drain", err)
	}
}
