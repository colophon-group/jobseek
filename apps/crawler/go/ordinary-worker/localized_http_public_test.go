package worker

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

type localizedPublicCapture struct {
	Provider, Status string
	ObservedAt       string `json:"observed_at_utc"`
	Board            struct {
		BoardURL string `json:"board_url"`
		Metadata json.RawMessage
	}
	Jobs      []map[string]any
	Exchanges []struct {
		Method, URL, Body string
		RequestBody       string `json:"request_body"`
		Base64            string `json:"body_base64"`
		Status            int
		Headers           map[string]string
		ResponseHeaders   map[string]string `json:"response_headers"`
		Cookies           []string          `json:"set_cookies"`
	}
}

func TestLocalizedOriginalPublicVerifiedReplay(t *testing.T) {
	dir := os.Getenv("JOBSEEK_LOCALIZED_HTTP_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires private original public capture")
	}
	for _, slug := range []string{"parker-hannifin-global", "ofi-north-america", "university-of-basel-main", "national-science-center-kharkiv-institute-of-physics-and-technology-vacancies"} {
		t.Run(slug, func(t *testing.T) {
			tag := "capture1"
			if slug == "university-of-basel-main" {
				tag = "capture2"
			}
			b, e := os.ReadFile(filepath.Join(dir, "native-localized-three-"+slug+"-original-public-"+tag+"-2026-10-10.json"))
			if e != nil {
				t.Fatal("private capture unavailable")
			}
			var c localizedPublicCapture
			if json.Unmarshal(b, &c) != nil {
				t.Fatal("capture schema")
			}
			observedAt, e := time.Parse(time.RFC3339Nano, c.ObservedAt)
			if e != nil {
				t.Fatal("capture clock unavailable")
			}
			clockShift := time.Since(observedAt)
			p, config := localizedFixtureConfig(t, c.Provider, c.Board.BoardURL, c.Board.Metadata)
			var mu sync.Mutex
			used := make([]bool, len(c.Exchanges))
			issuedCookies := map[string]map[string]bool{}
			cookieRequests := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				raw := "https://" + r.Host + r.URL.String()
				requestBody, _ := io.ReadAll(r.Body)
				found := -1
				for i, x := range c.Exchanges {
					if !used[i] && strings.EqualFold(x.URL, raw) && x.Method == r.Method && x.RequestBody == string(requestBody) {
						found = i
						break
					}
				}
				if found < 0 {
					t.Error("request changed or exceeded original captured inventory")
					w.WriteHeader(400)
					return
				}
				used[found] = true
				x := c.Exchanges[found]
				if c.Provider == "prospective" {
					cookies := r.Cookies()
					if len(cookies) > 0 {
						cookieRequests++
					}
					for _, cookie := range cookies {
						if !issuedCookies[cookie.Name][cookie.Value] {
							t.Error("concurrent session sent an unissued cookie")
						}
					}
				}
				for k, v := range x.Headers {
					if strings.EqualFold(k, "user-agent") {
						continue
					}
					if strings.EqualFold(k, "cookie") {
						if c.Provider != "prospective" && !reflect.DeepEqual(lastHTTPCookiePairs(r.Header.Get(k)), lastHTTPCookiePairs(v)) {
							t.Error("original cookie projection differs")
						}
					} else if r.Header.Get(k) != v {
						t.Error("original header differs", k)
					}
				}
				for k, v := range x.ResponseHeaders {
					w.Header().Set(k, v)
				}
				for _, cookie := range x.Cookies {
					// Replay the captured session clock, keeping lifetime, deletion,
					// scope and raw value semantics. Historical short-lived cookies
					// must not expire merely because qualification runs later.
					parts := strings.Split(cookie, ";")
					for i := 1; i < len(parts); i++ {
						key, value, ok := strings.Cut(strings.TrimSpace(parts[i]), "=")
						if ok && strings.EqualFold(key, "expires") {
							if expiry, e := http.ParseTime(value); e == nil {
								parts[i] = " Expires=" + expiry.Add(clockShift).UTC().Format(http.TimeFormat)
							}
						}
					}
					w.Header().Add("Set-Cookie", strings.Join(parts, ";"))
				}
				if c.Provider == "prospective" {
					for _, cookie := range api.OriginalSessionCookies(w.Header()) {
						if issuedCookies[cookie.Name] == nil {
							issuedCookies[cookie.Name] = map[string]bool{}
						}
						issuedCookies[cookie.Name][cookie.Value] = true
					}
				}
				w.WriteHeader(x.Status)
				if x.Base64 != "" {
					binary, e := base64.StdEncoding.DecodeString(x.Base64)
					if e != nil {
						t.Error("private PDF capture invalid")
						return
					}
					w.Write(binary)
				} else {
					fmt.Fprint(w, x.Body)
				}
			}))
			out, e := FetchLocalizedHTTPProviders(context.Background(), client, p, config, func(context.Context, time.Duration) error { return nil })
			if (e == nil) != (c.Status == "complete") {
				t.Fatalf("original complete/failure classification differs: %T", e)
			}
			for _, v := range used {
				if !v {
					t.Fatal("original public request absent")
				}
			}
			if c.Provider == "prospective" && cookieRequests == 0 {
				t.Fatal("original session cookies were never returned")
			}
			if c.Status != "complete" {
				if len(out.Jobs) != 0 {
					t.Fatal("failed original prefix acquired publication authority")
				}
				return
			}
			actual := []string{}
			expected := []string{}
			for _, j := range out.Jobs {
				actual = append(actual, j.URL)
			}
			for _, j := range c.Jobs {
				expected = append(expected, j["url"].(string))
			}
			sort.Strings(actual)
			sort.Strings(expected)
			if !reflect.DeepEqual(actual, expected) {
				t.Fatal("complete original public inventory differs")
			}
		})
	}
}

func TestKIPTOriginalPublicPDFExtraction(t *testing.T) {
	dir := os.Getenv("JOBSEEK_LOCALIZED_HTTP_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires private original PDF bodies/text")
	}
	b, e := os.ReadFile(filepath.Join(dir, "native-localized-three-national-science-center-kharkiv-institute-of-physics-and-technology-vacancies-original-public-capture1-2026-10-10.json"))
	if e != nil {
		t.Fatal("capture unavailable")
	}
	var c localizedPublicCapture
	if json.Unmarshal(b, &c) != nil {
		t.Fatal("capture schema")
	}
	b, e = os.ReadFile(filepath.Join(dir, "native-localized-three-kipt-original-public-pdf-text1-2026-10-10.json"))
	if e != nil {
		t.Fatal("original pypdf truth unavailable")
	}
	var gold []struct {
		URL, Posted, Text string
		Jobs              []map[string]any
	}
	if json.Unmarshal(b, &gold) != nil {
		t.Fatal("original PDF schema")
	}
	for i, g := range gold {
		t.Run(fmt.Sprintf("pdf%d", i), func(t *testing.T) {
			var body []byte
			for _, x := range c.Exchanges {
				if x.URL == g.URL {
					body, e = base64.StdEncoding.DecodeString(x.Base64)
					if e != nil {
						t.Fatal("PDF bytes invalid")
					}
				}
			}
			if len(body) == 0 {
				t.Fatal("PDF bytes absent")
			}
			text, e := extractKIPTPDFText(context.Background(), body)
			if e != nil {
				t.Fatal(e)
			}
			jobs, e := api.ParseKIPTBulletin(g.URL, text, g.Posted, "Kharkiv, Ukraine")
			if e != nil || len(jobs) != len(g.Jobs) {
				t.Fatalf("original per-PDF vacancy classification differs %d/%d %v", len(jobs), len(g.Jobs), e)
			}
			for i, j := range jobs {
				want := g.Jobs[i]
				for key, value := range map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "date_posted": j.DatePosted} {
					if !reflect.DeepEqual(value, want[key]) {
						// This exact public PDF has one spurious pypdf space inside a
						// continuous word. Its render and word bounding box were reviewed;
						// retain Poppler's correction without permitting other text changes.
						if key == "description" && fmt.Sprintf("%x", sha256.Sum256(body)) == "3847f0f64441f682a828a9f993f41ccc6fb891c85b5ddb24a6190e584b3114c2" &&
							fmt.Sprintf("%x", sha256.Sum256([]byte(want[key].(string)))) == "9aca3b43a0ae1b0c72f9c1b1224df68192b6751e40aef25af5a852418eef3238" &&
							fmt.Sprintf("%x", sha256.Sum256([]byte(j.Description.(string)))) == "b0add8f3e76557c44a7001318414baa69375179eca2782e8cc6b7a388305711d" {
							continue
						}
						t.Error("original PDF vacancy field differs", key)
					}
				}
			}
		})
	}
}
