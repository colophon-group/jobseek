package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func webFormsFixture(t *testing.T, size, total int) string {
	t.Helper()
	grid, err := json.Marshal([]webFormsGridState{{UniqueID: "ctl00$Cph1$vcyS$vsGrid$ctl00", PageSize: size, PageCount: (total + size - 1) / size, VirtualItemCount: total}})
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(string(grid))
	source := `<form method="post" action="./VacanciesV2.aspx" id="form2"><input name="__EVENTTARGET"><input name="__EVENTARGUMENT"><input name="__VIEWSTATE" value="session +&amp; state"><input name="__VIEWSTATE_UNIQUE_KEY" value="fresh"><input name="same" value="one"><input name="same" value="two"></form><script>{"_gridTableViewsData":` + string(encoded) + `}</script>`
	for i := 1; i <= total && i <= size; i++ {
		source += fmt.Sprintf(`<input onclick="PrintVacancy('VacancyId=%d&amp;LocationId=2')">`, i)
	}
	return source
}

func TestWebFormsSessionAndCompleteInventoryFailClosed(t *testing.T) {
	for _, mode := range []string{"complete", "bootstrap-reserved", "post-reserved", "later-failure", "missing-session", "changed-total", "partial", "oversized-total"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if "https://"+r.Host+r.URL.String() != slaughterListing {
					t.Error("foreign WebForms request")
				}
				body := webFormsFixture(t, 10, 2)
				if calls == 1 {
					if r.Method != "GET" {
						t.Error("missing fresh listing GET")
					}
					http.SetCookie(w, &http.Cookie{Name: "session", Value: "fresh", Path: "/"})
					if mode == "bootstrap-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
					}
					if mode == "missing-session" {
						body = strings.ReplaceAll(body, `name="__VIEWSTATE"`, `name="wrong"`)
					}
					if mode == "oversized-total" {
						body = webFormsFixture(t, 10, 51)
					}
				} else {
					if calls != 2 || r.Method != "POST" {
						t.Error("unexpected request sequence")
					}
					cookie, e := r.Cookie("session")
					if e != nil || cookie.Value != "fresh" {
						t.Error("fresh operation cookie lost")
					}
					if r.ParseForm() != nil || r.PostForm.Get("__EVENTTARGET") != "ctl00$Cph1$vcyS$vsGrid" || r.PostForm.Get("__EVENTARGUMENT") != "FireCommand:ctl00$Cph1$vcyS$vsGrid$ctl00;PageSize;50" || r.PostForm.Get("__VIEWSTATE") != "session +& state" || !reflect.DeepEqual(r.PostForm["same"], []string{"one", "two"}) {
						t.Error("ordered session fields changed")
					}
					body = webFormsFixture(t, 50, 2)
					if mode == "post-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
					}
					if mode == "later-failure" {
						w.WriteHeader(503)
					}
					if mode == "changed-total" {
						body = webFormsFixture(t, 50, 3)
					}
					if mode == "partial" {
						body = strings.Replace(body, `VacancyId=2`, `BrokenId=2`, 1)
					}
				}
				fmt.Fprint(w, body)
			}))
			result, e := discoverSlaughterWebForms(context.Background(), client)
			if mode == "complete" {
				if e != nil || len(result.Jobs) != 2 || result.Truncated || calls != 2 {
					t.Fatal("complete inventory changed", e)
				}
			} else if e == nil || len(result.Jobs) != 0 {
				t.Fatal("incomplete inventory leaked authoritative prefix", mode)
			}
			if strings.Contains(mode, "reserved") && (result.Response == nil || !result.Response.reserved) {
				t.Fatal("publisher reservation lost precedence")
			}
		})
	}
}

func TestWebFormsOriginalPublicInventoryAndCanonicalActions(t *testing.T) {
	dir := os.Getenv("JOBSEEK_WEBFORMS_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original public inventory and published form captures")
	}
	read := func(name string, v any) {
		b, e := os.ReadFile(filepath.Join(dir, name))
		if e != nil || json.Unmarshal(b, v) != nil {
			t.Fatal("protected public capture unavailable")
		}
	}
	var original struct {
		Board  map[string]any
		Jobs   []struct{ URL string }
		Status string
	}
	read("native1005-interaction-slaughter-and-may-careers-original-public-capture2-2026-10-10.json", &original)
	var public struct {
		Trace []struct {
			Method, URL, Body string
			Status            int
		}
	}
	read("native1005-slaughter-public-webforms-pagesize-diagnostic2-2026-10-10.json", &public)
	if original.Status != "complete" || len(original.Jobs) != 23 || len(public.Trace) != 2 {
		t.Fatal("complete original proof unavailable")
	}
	config := map[string]string{}
	for k, v := range original.Board {
		if k == "metadata" {
			b, _ := json.Marshal(v)
			config[k] = string(b)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	profile, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil || !slaughterWebFormsConfigured(profile, config) {
		t.Fatal("canonical public actions not bound", e)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls >= len(public.Trace) {
			t.Error("extra public request")
			w.WriteHeader(400)
			return
		}
		x := public.Trace[calls]
		calls++
		if r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("published form request changed")
		}
		if calls == 2 {
			if r.ParseForm() != nil {
				t.Error("invalid public post")
			}
			want, e := slaughterPostBody(public.Trace[0].Body)
			if e != nil {
				t.Error(e)
			}
			values, e := url.ParseQuery(want)
			if e != nil || !reflect.DeepEqual(values, r.PostForm) {
				t.Error("fresh public form fields changed")
			}
		}
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	result, e := discoverSlaughterWebForms(context.Background(), client)
	if e != nil || calls != 2 || result.Truncated {
		t.Fatal("published session inventory failed", e)
	}
	got, want := []string{}, []string{}
	for _, j := range result.Jobs {
		if !j.URLOnly {
			t.Fatal("URL source acquired rich data")
		}
		got = append(got, j.URL)
	}
	for _, j := range original.Jobs {
		want = append(want, j.URL)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatal("published form differs from full original inventory", len(got), len(want))
	}
	t.Log("all 23 original URLs match the published form inventory")
}
