package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func adfTestConfig() map[string]string {
	return map[string]string{"board_url": "https://example.com/jobs", "crawler_type": "dom", "company_id": "22222222-2222-4222-8222-222222222222", "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "example.com", "domain": "example.com", "monitor_needs_browser": "1", "scraper_needs_browser": "0", "metadata": `{"render":true,"oracle_adf_job_ids":{"max_items":100,"max_scan":1000},"url_filter":"[?]jobId=","scraper_type":"skip"}`}
}

func adfTestRows(offset, count int, more bool) string {
	s := ""
	for i := offset; i < offset+count; i++ {
		s += fmt.Sprintf(`<div role="gridcell" data-afrRK="%d" id="pt1:r1:0:lv1:%d:li1"><span id="row%d:ol1">Engineer %d</span><span id="row%d:of4">Paris</span></div>`, i, i, i, i, i)
	}
	if more {
		s += fmt.Sprintf(`<a id="pt1:r1:0:lv1::fchmrlnk%d" data-afrfchIndx="%d">More</a>`, offset+count-1, offset+count-1)
	}
	return s
}

func adfTestPartial(id, fragment, state string) string {
	return `<partial-response><changes><update id="` + id + `"><![CDATA[` + fragment + `]]></update><update id="javax.faces.ViewState"><![CDATA[` + state + `]]></update></changes></partial-response>`
}

func TestOracleADFFullIdentityAndLateFailures(t *testing.T) {
	for _, mode := range []string{"complete", "exact-page-multiple", "missing-state", "repeated-page", "malformed-xml", "post-failure", "late-reserved-header", "late-reserved-fragment", "missing-detail", "detail-reserved", "reordered-identity", "bound-exceeded", "redirect"} {
		t.Run(mode, func(t *testing.T) {
			config := adfTestConfig()
			if mode == "bound-exceeded" {
				config["metadata"] = strings.Replace(config["metadata"], `"max_items":100`, `"max_items":3`, 1)
			}
			p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if e != nil {
				t.Fatal("ADF test configuration rejected")
			}
			o, e := queue.OracleADFMonitorOptions(config)
			if e != nil {
				t.Fatal(e)
			}
			var mu sync.Mutex
			posts := 0
			detailCalls := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.UserAgent() != "jobseek-crawler/1.0" {
					t.Error("crawler UA changed")
				}
				if r.Method == "POST" {
					mu.Lock()
					defer mu.Unlock()
					posts++
					if r.ParseForm() != nil || r.PostForm.Get("javax.faces.ViewState") != fmt.Sprint(posts) || r.URL.Query().Get("Adf-Window-Id") != "fresh" {
						t.Error("fresh ADF session lost")
					}
					cookie, e := r.Cookie("session")
					if e != nil || cookie.Value != "fresh" {
						t.Error("operation session missing")
					}
					if posts == 1 {
						if r.PostForm.Get("event") != "pt1:r1:0:lv1" || !strings.Contains(r.PostForm.Get("event.pt1:r1:0:lv1"), `<k v="index"><n>1</n>`) {
							t.Error("ADF fetch index changed")
						}
						if mode == "post-failure" {
							w.WriteHeader(503)
							return
						}
						if mode == "redirect" {
							w.Header().Set("Location", "https://foreign.example/jobs")
							w.WriteHeader(302)
							return
						}
						if mode == "late-reserved-header" {
							w.Header().Set("TDM-Reservation", "1")
						}
						fragment := adfTestRows(2, 2, false)
						if mode == "repeated-page" {
							fragment = adfTestRows(0, 2, false)
						}
						if mode == "late-reserved-fragment" {
							fragment += `<meta name="tdm-reservation" content="1">`
						}
						body := adfTestPartial("pt1:r1:0:lv1", fragment, "2")
						if mode == "malformed-xml" {
							body = body[:len(body)-4]
						}
						fmt.Fprint(w, body)
						return
					}
					if posts != 2 || r.PostForm.Get("event") != "pt1:r1:0:lv1,pt1:r1:0:lv1:0:l1_view" {
						t.Error("ADF first View protocol changed")
					}
					fmt.Fprint(w, adfTestPartial("pt1:r1", `<a href="/jobs?jobId=100">Permalink</a>`, "3"))
					return
				}
				if id := r.URL.Query().Get("jobId"); id != "" {
					mu.Lock()
					detailCalls++
					mu.Unlock()
					if r.Header.Get("Cookie") != "" {
						t.Error("ADF numeric details must be stateless")
					}
					for i := 0; i < 4; i++ {
						if id == fmt.Sprint(100-i) {
							if mode == "missing-detail" && i == 2 {
								w.WriteHeader(404)
								return
							}
							if mode == "detail-reserved" && i == 2 {
								w.Header().Set("TDM-Reservation", "1")
							}
							j := i
							if mode == "reordered-identity" {
								j = 3 - i
							}
							fmt.Fprintf(w, `<span id="d:ot3">%s posted</span><span id="d:ot1"><label>Engineer %d</label></span><span id="d:ot5">Paris</span>`, id, j)
							return
						}
					}
					w.WriteHeader(404)
					return
				}
				http.SetCookie(w, &http.Cookie{Name: "session", Value: "fresh", Path: "/"})
				body := `<form id="f1"><input type="hidden" name="org.apache.myfaces.trinidad.faces.FORM" value="f1"><input type="hidden" name="Adf-Window-Id" value="fresh"><input type="hidden" name="javax.faces.ViewState" value="1"></form><script>AdfPage.PAGE.setPageId('1');</script>` + adfTestRows(0, 2, true)
				if mode == "missing-state" {
					body = strings.ReplaceAll(body, "javax.faces.ViewState", "absent")
				}
				fmt.Fprint(w, body)
			}))
			found, e := discoverOracleADF(context.Background(), client, p, config, o, func(context.Context, time.Duration) error { return nil })
			if mode == "complete" || mode == "exact-page-multiple" {
				if e != nil || len(found.Jobs) != 4 || posts != 2 || detailCalls != 32 {
					t.Fatal("complete ordered identity scan failed", e)
				}
				if !domVerificationResponseMatches(p, config, found.Response) {
					t.Fatal("ADF observation lost canonical claim binding")
				}
			} else if e == nil || len(found.Jobs) != 0 {
				t.Fatal("partial ADF inventory leaked", mode)
			}
			if mode == "post-failure" || mode == "redirect" || mode == "missing-state" {
				kind, status := "oracle_adf_pagination_http_status", 503
				if mode == "redirect" {
					status = 302
				} else if mode == "missing-state" {
					kind, status = "oracle_adf_session", 0
				}
				var failure *DiscoveryError
				if !errors.As(e, &failure) || failure.Kind != kind || failure.Status != status || mode != "missing-state" && posts != 1 {
					t.Fatal("ADF failure lost phase/status or retried a mutating action")
				}
			}
			if strings.Contains(mode, "reserved") && (found.Response == nil || !found.Response.reserved || !domVerificationResponseMatches(p, config, found.Response)) {
				t.Fatal("late publisher reservation lost canonical binding")
			}
		})
	}
}

func TestOracleADFBootstrapFailureRetainsStatusAndRetryBudget(t *testing.T) {
	for _, status := range []int{403, 503, 404} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			config := adfTestConfig()
			profile, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if err != nil {
				t.Fatal(err)
			}
			options, err := queue.OracleADFMonitorOptions(config)
			if err != nil {
				t.Fatal(err)
			}
			var calls atomic.Int32
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				w.WriteHeader(status)
				fmt.Fprint(w, "private body with token and https://private.invalid")
			}))
			waits := 0
			found, err := discoverOracleADF(context.Background(), client, profile, config, options, func(context.Context, time.Duration) error { waits++; return nil })
			var failure *DiscoveryError
			expected := int32(3)
			if status == 404 {
				expected = 1
			}
			if !errors.As(err, &failure) || failure.Kind != "oracle_adf_bootstrap_http_status" || failure.Status != status || calls.Load() != expected || waits != int(expected)-1 || len(found.Jobs) != 0 {
				t.Fatal("bootstrap failure lost safe status, changed retries or emitted partial work")
			}
			if strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), "https://") {
				t.Fatal("ADF diagnostic exposed publisher content")
			}
		})
	}
}

// The capture combines the unchanged original browser inventory, a fresh HTTP
// form session, and every original stateless detail read. Raw public identifiers
// and session values remain in protected operator evidence, outside the repo.
func TestOracleADFOriginalPublicInventory(t *testing.T) {
	dir := os.Getenv("JOBSEEK_ORACLE_ADF_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected complete original and fresh-session HTTP captures")
	}
	read := func(name string, v any) {
		b, e := os.ReadFile(filepath.Join(dir, name))
		if e != nil || json.Unmarshal(b, v) != nil {
			t.Fatal("protected ADF capture unavailable")
		}
	}
	var original struct {
		Config   map[string]string
		Complete bool
		URLs     []string
		HTTP     []struct {
			Method, URL, Body string
			Status            int
			Headers           map[string]string
		} `json:"http_exchanges"`
	}
	read("original-full1.private.json", &original)
	var fresh struct {
		Complete bool
		Requests []struct {
			Method, URL, Body string
			Status            int
			Headers           map[string]string
			Form              map[string]string `json:"request_body"`
		}
	}
	read("http-ppr-probe4.private.json", &fresh)
	if !original.Complete || !fresh.Complete || len(original.URLs) != 52 || len(fresh.Requests) != 4 || len(original.HTTP) < 52 {
		t.Fatal("complete original ADF proof required")
	}
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", original.Config)
	if e != nil {
		t.Fatal("actual canonical ADF assignment rejected")
	}
	o, e := queue.OracleADFMonitorOptions(original.Config)
	if e != nil {
		t.Fatal(e)
	}
	byURL := map[string]int{}
	for i, x := range original.HTTP {
		byURL[x.URL] = i
	}
	step := 0
	var mu sync.Mutex
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resource := "https://" + r.Host + r.URL.String()
		if r.Method == "GET" && r.URL.Query().Get("jobId") != "" {
			i, ok := byURL[resource]
			if !ok {
				t.Error("detail outside original capture")
				w.WriteHeader(400)
				return
			}
			if r.Header.Get("Cookie") != "" {
				t.Error("original stateless detail changed")
			}
			x := original.HTTP[i]
			for k, v := range x.Headers {
				w.Header().Set(k, v)
			}
			w.WriteHeader(x.Status)
			fmt.Fprint(w, x.Body)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		if step >= len(fresh.Requests) {
			t.Error("extra ADF session request")
			w.WriteHeader(400)
			return
		}
		x := fresh.Requests[step]
		step++
		wantURL, parseErr := url.Parse(x.URL)
		if parseErr != nil || r.URL.Scheme != "" && r.URL.Scheme != wantURL.Scheme || r.Host != wantURL.Host || r.URL.Path != wantURL.Path || !reflect.DeepEqual(r.URL.Query(), wantURL.Query()) || r.Method != x.Method {
			t.Error("fresh ADF request route changed")
		}
		if r.Method == "POST" {
			if r.ParseForm() != nil {
				t.Error("invalid ADF form")
			}
			want := url.Values{}
			for k, v := range x.Form {
				want.Set(k, v)
			}
			if !reflect.DeepEqual(want, r.PostForm) {
				for k := range want {
					if !reflect.DeepEqual(want[k], r.PostForm[k]) {
						t.Error("ADF protocol field differs", k)
					}
				}
				for k := range r.PostForm {
					if want[k] == nil {
						t.Error("unexpected ADF protocol field", k)
					}
				}
			}
		}
		for k, v := range x.Headers {
			w.Header().Set(k, v)
		}
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	found, e := discoverOracleADF(context.Background(), client, p, original.Config, o, func(context.Context, time.Duration) error { return nil })
	if e != nil || found.Truncated || step != 4 {
		t.Fatal("full original ADF replay failed", e)
	}
	got := []string{}
	for _, j := range found.Jobs {
		if !j.URLOnly {
			t.Fatal("ADF URL-only writer semantics changed")
		}
		got = append(got, j.URL)
	}
	slices.Sort(got)
	slices.Sort(original.URLs)
	if !slices.Equal(got, original.URLs) {
		t.Fatal("complete original ADF URL set differs")
	}
	t.Log("all 52 original browser URLs exactly matched by native fresh-session HTTP/PPR and stateless identity scan")
}
