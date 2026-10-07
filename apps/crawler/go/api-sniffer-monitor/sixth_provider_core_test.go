package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestSixthProviderInventoriesMatchActualPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_sixth_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var data struct {
		Inventories []struct {
			Provider, Scenario string
			Pages              map[string]string
			Requests           []string
			Expected           struct {
				Error, Truncated bool
				URLs             []string
			}
		}
	}
	if json.Unmarshal(raw, &data) != nil || len(data.Inventories) != 12 {
		t.Fatal("Python inventory corpus unavailable")
	}
	normalize := func(source string) string {
		u, e := url.Parse(source)
		if e != nil {
			t.Fatal(e)
		}
		u.RawQuery = u.Query().Encode()
		return u.String()
	}
	join := func(base, href string) (string, error) {
		b, e := url.Parse(base)
		if e != nil {
			return "", e
		}
		u, e := url.Parse(href)
		if e != nil {
			return "", e
		}
		return b.ResolveReference(u).String(), nil
	}
	for _, c := range data.Inventories {
		t.Run(c.Provider+"/"+c.Scenario, func(t *testing.T) {
			pages := map[string]string{}
			for source, body := range c.Pages {
				pages[normalize(source)] = body
			}
			requests := []string{}
			fetch := func(ctx context.Context, source string) (string, error) {
				source = normalize(source)
				requests = append(requests, source)
				body, ok := pages[source]
				if !ok {
					t.Fatal("request left fixture", source)
				}
				return body, nil
			}
			var urls []string
			var truncated bool
			var e error
			if c.Provider == "manatal" {
				var jobs []map[string]any
				jobs, truncated, e = DiscoverManatal(context.Background(), ManatalOptions{"tenant"}, func(ctx context.Context, source string) (*Document, error) {
					body, e := fetch(ctx, source)
					if e != nil {
						return nil, e
					}
					return Decode([]byte(body))
				})
				urls = []string{}
				for _, job := range jobs {
					urls = append(urls, job["url"].(string))
				}
			} else {
				urls, truncated, e = DiscoverHRMOS(context.Background(), HRMOSOptions{"tenant"}, fetch, join)
			}
			if (e != nil) != c.Expected.Error {
				t.Fatalf("failure differs: %v expected%+v", e, c.Expected)
			}
			if e == nil && (!reflect.DeepEqual(urls, c.Expected.URLs) || truncated != c.Expected.Truncated) {
				t.Fatalf("inventory differs: urls%v truncated%v expected%+v", urls, truncated, c.Expected)
			}
			want := []string{}
			for _, source := range c.Requests {
				want = append(want, normalize(source))
			}
			if !reflect.DeepEqual(requests, want) {
				t.Fatalf("requests differ: got%v want%v", requests, want)
			}
		})
	}
}

func TestSixthProviderCoreMatchesActualPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_sixth_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases struct {
		Manatal []struct {
			Row      json.RawMessage
			Expected map[string]any
		}
		HRMOS []struct {
			Body     string
			Expected struct {
				Error, Empty              bool
				URLs                      []string
				Total, Displayed, Current int
				LinkedMax                 int `json:"linked_max"`
			}
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases.Manatal) != 9 || len(cases.HRMOS) != 7 {
		t.Fatal("reference corpus unavailable")
	}
	for _, c := range cases.Manatal {
		d, e := Decode(c.Row)
		if e != nil {
			t.Fatal(e)
		}
		fields, e := d.ManatalJobFields(d.Value, ManatalOptions{"tenant"})
		if e != nil {
			t.Fatal(e)
		}
		body, _ := json.Marshal(fields)
		var normalized map[string]any
		json.Unmarshal(body, &normalized)
		if !reflect.DeepEqual(normalized, c.Expected) {
			t.Fatalf("Manatal differs: got%s expected%v", body, c.Expected)
		}
	}
	join := func(base, href string) (string, error) {
		b, e := url.Parse(base)
		if e != nil {
			return "", e
		}
		u, e := url.Parse(href)
		if e != nil {
			return "", e
		}
		return b.ResolveReference(u).String(), nil
	}
	for _, c := range cases.HRMOS {
		p, e := ParseHRMOSPage(c.Body, HRMOSOptions{"tenant"}, join)
		if c.Expected.Error {
			if e == nil {
				t.Fatal("invalid reference became listing")
			}
			continue
		}
		if e != nil || p.Empty != c.Expected.Empty || !reflect.DeepEqual(p.URLs, c.Expected.URLs) || !p.Empty && (p.Total != c.Expected.Total || p.Displayed != c.Expected.Displayed || p.Current != c.Expected.Current || p.LinkedMax != c.Expected.LinkedMax) {
			t.Fatalf("HRMOS differs: %+v error%v expected%+v", p, e, c.Expected)
		}
	}
}

func TestSixthProviderResourceScopes(t *testing.T) {
	m, e := ManatalOptionsFromMetadata("https://www.careers-page.com/tenant", `{}`)
	if e != nil {
		t.Fatal(e)
	}
	h, e := HRMOSOptionsFromMetadata("https://hrmos.co/pages/tenant/jobs", `{}`)
	if e != nil {
		t.Fatal(e)
	}
	for _, page := range []int{1, 2, 500} {
		if !m.ResourceMatches(m.ListingURL(page)) || !h.ResourceMatches(h.ListingURL(page)) {
			t.Fatal("bounded page rejected", page)
		}
	}
	for _, scope := range []interface{ ResourceMatches(string) bool }{m, h} {
		for _, source := range []string{"https://evil.test/", "https://hrmos.co/pages/foreign/jobs", "https://www.careers-page.com/api/v1.0/c/foreign/jobs/?page=1", "https://hrmos.co/pages/tenant/jobs?page=501"} {
			if scope.ResourceMatches(source) {
				t.Fatal("foreign resource admitted", source)
			}
		}
	}
	for _, raw := range []string{`{"proxy":true}`, `{"render":true}`, `{"skip_ssl":true}`, `{"actions":[{"type":"click"}]}`} {
		if _, e := ManatalOptionsFromMetadata("https://www.careers-page.com/tenant", raw); e == nil {
			t.Fatal("Manatal ignored transport")
		}
		if _, e := HRMOSOptionsFromMetadata("https://hrmos.co/pages/tenant/jobs", raw); e == nil {
			t.Fatal("HRMOS ignored transport")
		}
	}
	for _, raw := range []string{`{"count":true,"results":[]}`, `{"count":1.0,"results":[]}`, `{"count":-1,"results":[]}`, `{"count":0,"results":{}}`} {
		d, e := Decode([]byte(raw))
		if e != nil {
			t.Fatal(e)
		}
		if _, _, _, e = ManatalPage(d); e == nil {
			t.Fatal("invalid count/results admitted")
		}
	}
}
