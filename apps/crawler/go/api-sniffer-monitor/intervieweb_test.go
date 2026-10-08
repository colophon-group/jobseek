package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestInterviewebMatchesActualPythonURLsCountersAndProtocol(t *testing.T) {
	var corpus struct {
		URLs []struct {
			Raw       string
			Canonical *string
		}
		Pages []struct {
			Name, Body string
			Pages      *int
			Error      bool
			URLs       []string
		}
		Protocol []struct {
			Name, Body string
			Value      []string
			Error      bool
		}
	}
	raw, err := os.ReadFile("testdata/python_intervieweb.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.URLs) != 12 || len(corpus.Pages) != 6 || len(corpus.Protocol) != 7 {
		t.Fatal("actual Python corpus unavailable")
	}
	base := "https://fixture.intervieweb.it/en/career"
	for _, c := range corpus.URLs {
		actual, ok := InterviewebJobURL(c.Raw, base)
		if ok != (c.Canonical != nil) || ok && actual != *c.Canonical {
			t.Fatal("canonical application URL byte identity changed", c.Raw, actual, c.Canonical)
		}
	}
	for _, c := range corpus.Pages {
		t.Run(c.Name, func(t *testing.T) {
			p, err := ParseInterviewebPage(c.Body, base)
			if (err != nil) != c.Error || !c.Error && (p.Pages != *c.Pages || !reflect.DeepEqual(p.URLs, c.URLs)) {
				t.Fatal("counter or application URL inventory changed", p, err)
			}
		})
	}
	o, _ := TenthProviderOptionsFromMetadata("intervieweb", base, "{}")
	for _, c := range corpus.Protocol {
		t.Run(c.Name, func(t *testing.T) {
			p, err := ParseInterviewebPage(c.Body, base)
			if err != nil {
				t.Fatal(err)
			}
			endpoint, section, order, err := InterviewebPaginationProtocol(c.Body, p, o)
			if (err != nil) != c.Error || !c.Error && !reflect.DeepEqual([]string{endpoint, section, order}, c.Value) {
				t.Fatal("pagination endpoint, section or ordering changed", endpoint, section, order, err)
			}
		})
	}
}

func TestInterviewebCompleteSnapshotRejectsChangedRepeatedAndFailedPages(t *testing.T) {
	for _, mode := range []string{"complete", "changed", "repeated", "empty", "invalid-json", "false-success"} {
		t.Run(mode, func(t *testing.T) {
			o, _ := TenthProviderOptionsFromMetadata("intervieweb", "https://fixture.intervieweb.it/en/career", "{}")
			calls := 0
			rows, err := DiscoverInterviewebSnapshot(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				calls++
				if !o.ResourceMatches(r.URL) {
					t.Fatal("unbound endpoint")
				}
				if calls == 1 {
					return []byte(`<input id="url-for-announces" value="/app.php?module=newcareer"><a data-order="name" class="active">Name</a><script>{"section":"jobs"}</script>vacancyListCareer researchAnnounces Page 1 of 2 <a href="/jobs/one/en/">One</a>`), nil
				}
				if r.Method != "POST" || !strings.Contains(r.Body, "page=2") || r.Headers.Get("Referer") != o.BoardURL {
					t.Fatal("form replay changed")
				}
				body := `Page 2 of 2 <a href="/jobs/two/en/">Two</a>`
				if mode == "changed" {
					body = strings.Replace(body, "of 2", "of 3", 1)
				}
				if mode == "repeated" {
					body = strings.Replace(body, "two", "one", 1)
				}
				if mode == "empty" {
					body = "Page 2 of 2"
				}
				if mode == "invalid-json" {
					return []byte("not JSON"), nil
				}
				payload, _ := json.Marshal(map[string]any{"success": mode != "false-success", "data": body})
				return payload, nil
			})
			if calls != 2 || mode == "complete" && (err != nil || len(rows) != 2) || mode != "complete" && (err == nil || len(rows) != 0) {
				t.Fatal("partial pagination accepted or complete inventory lost", calls, rows, err)
			}
		})
	}
}
