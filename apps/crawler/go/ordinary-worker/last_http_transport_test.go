package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func verifiedLastHTTPFixture(t *testing.T, handler http.Handler) *http.Client {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	transport.inner.TLSClientConfig.ServerName = "example.com"
	transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
	}
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "8.8.8.8:443" && address != "8.8.8.8:1443" && address != "8.8.8.8:1444" {
			t.Error("session request escaped verified public endpoint")
		}
		return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
	}
	return client
}

type lastHTTPExchange struct {
	Method, URL, Body string
	Status            int
	RequestBody       string `json:"request_body"`
	Headers           map[string]string
	ResponseHeaders   map[string]string `json:"response_headers"`
	SetCookies        []string          `json:"set_cookies"`
}
type lastHTTPDetailCase struct {
	Name, Provider, Board, Source, Status string
	ExpectedID                            string `json:"expected_id"`
	Error                                 bool
	Job, Fields                           map[string]any
	Exchanges                             []lastHTTPExchange
}

func lastHTTPCookiePairs(raw string) []string {
	pairs := []string{}
	for _, pair := range strings.Split(raw, ";") {
		if strings.TrimSpace(pair) != "" {
			pairs = append(pairs, strings.TrimSpace(pair))
		}
	}
	slices.Sort(pairs)
	return pairs
}

func TestLastHTTPOriginalMonitorFieldsThroughVerifiedSessions(t *testing.T) {
	for _, provider := range []string{"infor", "peoplesoft"} {
		var cases []struct {
			Name, Board string
			Error       bool
			Jobs        []map[string]any
			Exchanges   []lastHTTPExchange
		}
		b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_" + provider + ".json")
		if err != nil || json.Unmarshal(b, &cases) != nil {
			t.Fatal("original monitor corpus unavailable")
		}
		if directory := os.Getenv("JOBSEEK_LAST_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
			slug := "mount-sinai-health-system-south-nassau"
			if provider == "peoplesoft" {
				slug = "honeywell-aerospace-sandia"
			}
			var public struct {
				Status string
				Board  struct {
					BoardURL string `json:"board_url"`
					Metadata map[string]any
				}
				Jobs      []map[string]any
				Exchanges []lastHTTPExchange
			}
			b, err := os.ReadFile(filepath.Join(directory, "native-next-four-"+slug+"-original-public-capture1-2026-10-10.json"))
			if err != nil || json.Unmarshal(b, &public) != nil || public.Status != "complete" {
				t.Fatal("verified original public monitor unavailable")
			}
			cases = append(cases, struct {
				Name, Board string
				Error       bool
				Jobs        []map[string]any
				Exchanges   []lastHTTPExchange
			}{"public-" + slug, public.Board.BoardURL, false, public.Jobs, public.Exchanges})
		}
		for _, c := range cases {
			t.Run(provider+"/"+c.Name, func(t *testing.T) {
				o, err := api.LastHTTPOptionsFromMetadata(provider, c.Board, "{}")
				if err != nil {
					t.Fatal(err)
				}
				used := 0
				client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if used >= len(c.Exchanges) {
						t.Error("extra monitor request")
						w.WriteHeader(400)
						return
					}
					x := c.Exchanges[used]
					used++
					u, _ := url.Parse(x.URL)
					body, err := io.ReadAll(r.Body)
					if err != nil || r.Method != x.Method || !strings.EqualFold(r.Host, u.Host) || r.URL.Path != u.Path || !reflect.DeepEqual(r.URL.Query(), u.Query()) || string(body) != x.RequestBody {
						t.Error("original monitor request changed")
					}
					for k, v := range x.Headers {
						if strings.EqualFold(k, "user-agent") {
							continue
						}
						if provider == "peoplesoft" && strings.EqualFold(k, "cookie") {
							// Preserve all cookie pairs and multiplicities. Jar path
							// ordering differs between the original and Go clients.
							if !reflect.DeepEqual(lastHTTPCookiePairs(r.Header.Get(k)), lastHTTPCookiePairs(v)) {
								t.Error("original monitor cookie pairs changed")
								actual := map[string]string{}
								for _, cookie := range r.Cookies() {
									if _, duplicate := actual[cookie.Name]; duplicate {
										t.Error("duplicate cookie name", cookie.Name)
									}
									actual[cookie.Name] = cookie.Value
								}
								expected := &http.Request{Header: http.Header{"Cookie": []string{v}}}
								for _, cookie := range expected.Cookies() {
									if actual[cookie.Name] != cookie.Value {
										t.Error("cookie pair differs", cookie.Name)
									}
									delete(actual, cookie.Name)
								}
								for name := range actual {
									t.Error("extra cookie name", name)
								}
							}
							continue
						}
						if r.Header.Get(k) != v {
							t.Error("original monitor request header changed", k)
						}
					}
					for k, v := range x.ResponseHeaders {
						if !strings.EqualFold(k, "content-length") && !strings.EqualFold(k, "content-encoding") {
							w.Header().Set(k, v)
						}
					}
					for _, v := range x.SetCookies {
						w.Header().Add("Set-Cookie", v)
					}
					status := x.Status
					if status == 0 {
						status = 200
					}
					w.WriteHeader(status)
					fmt.Fprint(w, x.Body)
				}))
				config := map[string]string{"board_url": c.Board, "crawler_type": provider, "monitor_needs_browser": "0", "metadata": "{}"}
				p := queue.GreenhouseMonitorProfile{Provider: provider, Endpoint: o.ListingURL(), Profile: o.Profile()}
				out, err := FetchLastHTTPProviders(context.Background(), client, p, config, func(context.Context, time.Duration) error { return nil })
				if (err != nil) != c.Error || used != len(c.Exchanges) {
					t.Fatal("original monitor session outcome changed", err, used, len(c.Exchanges))
				}
				if c.Error {
					if len(out.Jobs) != 0 {
						t.Fatal("failed monitor retained partial prefix")
					}
					return
				}
				if len(out.Jobs) != len(c.Jobs) {
					t.Fatal("original monitor inventory changed")
				}
				byURL := map[string]RichMonitorJob{}
				for i, fields := range c.Jobs {
					if values, ok := fields["locations"].([]any); ok {
						locations := make([]string, len(values))
						for n, v := range values {
							var valid bool
							locations[n], valid = v.(string)
							if !valid {
								t.Fatal("original location fixture invalid")
							}
						}
						fields["locations"] = locations
					}
					want, err := secondaryRichJob(fields)
					if err != nil {
						t.Fatal("original field projection failed", i)
					}
					if _, duplicate := byURL[want.URL]; duplicate {
						t.Fatal("original repeated posting identity")
					}
					byURL[want.URL] = want
				}
				for i, actual := range out.Jobs {
					want, found := byURL[actual.URL]
					if !found || !reflect.DeepEqual(actual, want) {
						a, b := reflect.ValueOf(out.Jobs[i]), reflect.ValueOf(want)
						for n := 0; n < a.NumField(); n++ {
							if !reflect.DeepEqual(a.Field(n).Interface(), b.Field(n).Interface()) {
								t.Error("monitor field differs", a.Type().Field(n).Name)
							}
						}
						t.Fatal("original complete monitor fields changed", i)
					}
				}
			})
		}
	}
}

func TestLastHTTPPairedOriginalFieldsThroughVerifiedSessions(t *testing.T) {
	cases := []lastHTTPDetailCase{}
	for _, provider := range []string{"infor", "peoplesoft"} {
		b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_" + provider + "_detail.json")
		var original []lastHTTPDetailCase
		if err != nil || json.Unmarshal(b, &original) != nil {
			t.Fatal("original paired corpus unavailable")
		}
		for i := range original {
			c := &original[i]
			c.Provider = provider
			c.Fields = c.Job
			if c.Fields == nil && !c.Error {
				c.Fields = map[string]any{}
			}
			if provider == "peoplesoft" {
				body := c.Source
				c.Board = "https://fixture.example/psc/site/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
				base, err := api.LastHTTPOptionsFromMetadata(provider, c.Board, "{}")
				if err != nil {
					t.Fatal(err)
				}
				id := c.ExpectedID
				if id == "" {
					id = "42"
				}
				c.Source = base.PeopleSoftJobURL(id)
				c.Exchanges = []lastHTTPExchange{{Method: "GET", URL: base.Origin + "/psp/site/EMPLOYEE/HRMS/?cmd=logout", Body: "<html>Anonymous session</html>"}, {Method: "GET", URL: c.Source, Body: body}}
			}
		}
		if provider == "infor" {
			c := original[0]
			c.Name = "anonymous-sso-return-with-new-cookies"
			bootstrap, soap := c.Exchanges[0], c.Exchanges[1]
			u, _ := url.Parse(c.Source)
			sso := "https://" + strings.ToUpper(u.Host) + "/sso/SSOServlet?_action=LOGINASSERT"
			c.Exchanges = []lastHTTPExchange{
				{Method: "GET", URL: c.Source, Status: 302, ResponseHeaders: map[string]string{"Location": sso}, SetCookies: []string{"SESSION=synthetic; Path=/; Secure"}},
				{Method: "GET", URL: sso, Status: 302, ResponseHeaders: map[string]string{"Location": c.Source}, Headers: map[string]string{"Cookie": "SESSION=synthetic"}},
				bootstrap, soap,
			}
			c.Exchanges[2].Headers = map[string]string{"Cookie": "SESSION=synthetic"}
			c.Exchanges[2].SetCookies = []string{"ps_theme=broken theme; Path=/; Secure", "bare_cookie; Path=/; Secure"}
			c.Exchanges[3].Headers = map[string]string{}
			for k, v := range soap.Headers {
				if strings.EqualFold(k, "cookie") {
					v = "SESSION=synthetic; " + v
				}
				c.Exchanges[3].Headers[k] = v
			}
			original = append(original, c)
		}
		cases = append(cases, original...)
	}
	if directory := os.Getenv("JOBSEEK_LAST_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
		for _, slug := range []string{"mount-sinai-health-system-south-nassau", "honeywell-aerospace-sandia"} {
			b, err := os.ReadFile(filepath.Join(directory, "native-next-four-"+slug+"-original-paired-public-capture1-2026-10-10.json"))
			var public []lastHTTPDetailCase
			if err != nil || json.Unmarshal(b, &public) != nil || len(public) != 4 {
				t.Fatal("verified paired public capture unavailable")
			}
			for i := range public {
				public[i].Name = fmt.Sprintf("public-%s-%d", slug, i)
				if public[i].Status != "complete" {
					t.Fatal("original public detail incomplete")
				}
			}
			cases = append(cases, public...)
		}
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			o, err := api.LastHTTPDetailOptionsFromConfig(c.Provider, c.Board, c.Source, "{}")
			if err != nil {
				t.Fatal("original detail binding rejected", err)
			}
			used := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if used >= len(c.Exchanges) {
					t.Error("extra session request")
					w.WriteHeader(400)
					return
				}
				x := c.Exchanges[used]
				used++
				u, _ := url.Parse(x.URL)
				body, err := io.ReadAll(r.Body)
				if err != nil || r.Method != x.Method || !strings.EqualFold(r.Host, u.Host) || r.URL.Path != u.Path || !reflect.DeepEqual(r.URL.Query(), u.Query()) || string(body) != x.RequestBody {
					t.Error("original paired request changed")
				}
				for k, v := range x.Headers {
					if strings.EqualFold(k, "user-agent") {
						continue
					}
					if r.Header.Get(k) != v {
						t.Error("original paired request header changed", k)
					}
				}
				for k, v := range x.ResponseHeaders {
					if !strings.EqualFold(k, "content-length") && !strings.EqualFold(k, "content-encoding") {
						w.Header().Set(k, v)
					}
				}
				for _, v := range x.SetCookies {
					w.Header().Add("Set-Cookie", v)
				}
				status := x.Status
				if status == 0 {
					status = 200
				}
				w.WriteHeader(status)
				fmt.Fprint(w, x.Body)
			}))
			p := queue.WorkdayDetailProfile{SourceURL: c.Source, Endpoint: o.Endpoint, Profile: o.Profile(), HTTPAPIConfig: map[string]any{"board_url": c.Board}}
			fields, reserved, err := fetchLastHTTPDetail(context.Background(), client, p, func(context.Context, time.Duration) error { return nil })
			if (err != nil) != c.Error || reserved != nil || used != len(c.Exchanges) {
				t.Fatal("original session outcome changed", err, used, len(c.Exchanges))
			}
			if c.Error {
				if fields != nil {
					t.Fatal("failed detail retained content")
				}
				return
			}
			for k, v := range fields {
				if v == nil {
					delete(fields, k)
				}
			}
			b, _ := json.Marshal(fields)
			var actual map[string]any
			json.Unmarshal(b, &actual)
			for key, value := range actual {
				if value == nil {
					delete(actual, key)
				}
			}
			if !reflect.DeepEqual(actual, c.Fields) {
				for key := range actual {
					if !reflect.DeepEqual(actual[key], c.Fields[key]) {
						t.Error("original complete field changed", key)
					}
				}
				for key := range c.Fields {
					if _, ok := actual[key]; !ok {
						t.Error("original field missing", key)
					}
				}
				t.Fatal("original paired fields differ")
			}
		})
	}
}

func TestLastHTTPOriginalRetryPolicyAndCancellation(t *testing.T) {
	boards := map[string]string{"infor": "https://fixture.cloud.infor.com:1444/fixture/CandidateSelfService/lm?context.session.key.JobBoard=PUBLIC&context.session.key.HROrganization=1", "peoplesoft": "https://fixture.example/psc/site/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL", "papa_johns": "https://jobs.papajohns.com/jobs/", "unisante": "https://emploi.unisante.ch/index.php/offres"}
	for provider, board := range boards {
		for _, mode := range []string{"403", "503", "header-reserved", "body-reserved", "foreign-redirect", "cancelled"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				o, err := api.LastHTTPOptionsFromMetadata(provider, board, "{}")
				if err != nil {
					t.Fatal(err)
				}
				calls, waits := 0, 0
				client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("Content-Type", "text/html")
					if mode == "header-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "body-reserved" {
						w.WriteHeader(503)
						fmt.Fprint(w, `<html><head><meta name="tdm-reservation" content="1"></head></html>`)
						return
					}
					if mode == "foreign-redirect" {
						w.Header().Set("Location", "https://foreign.example/private")
						w.WriteHeader(302)
						return
					}
					status := 503
					if mode == "403" {
						status = 403
					}
					w.WriteHeader(status)
				}))
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if mode == "cancelled" {
					cancel()
				}
				observed := &lastHTTPObservation{}
				fetch, err := lastHTTPFetcher(client, o, o, func(context.Context, time.Duration) error { waits++; return nil }, observed)
				if err != nil {
					t.Fatal(err)
				}
				body, _, err := fetch(ctx, api.Request{Method: "GET", URL: board})
				want := 1
				if provider != "infor" && (mode == "403" || mode == "503") {
					want = 3
				}
				if mode == "cancelled" {
					want = 0
					if !errors.Is(err, context.Canceled) {
						t.Fatal("cancellation lost")
					}
				}
				if err == nil || body != nil || calls != want || waits != max(0, want-1) {
					t.Fatal("bounded session failure changed", calls, waits, want)
				}
				if strings.HasSuffix(mode, "reserved") {
					var reserved *policy.Reservation
					if !errors.As(err, &reserved) || observed.latest() == nil || !observed.latest().reserved {
						t.Fatal("publisher signal lost before status failure")
					}
				}
			})
		}
	}
}
