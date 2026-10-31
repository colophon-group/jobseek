package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
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

func TestInterviewebZinrecPrefixedURLsAndItalianPagination(t *testing.T) {
	board := "https://zinrec.intervieweb.it/starhotels/it/career"
	for _, test := range []struct {
		raw, want string
	}{
		{"/starhotels/jobs/assistant-manager-101/it/", "https://zinrec.intervieweb.it/starhotels/jobs/assistant-manager-101/it/"},
		{"/jobs/standard-102/en/", "https://zinrec.intervieweb.it/jobs/standard-102/en/"},
		{"/other/jobs/wrong-103/it/", ""},
		{"https://evil.example/starhotels/jobs/wrong-104/it/", ""},
		{"/starhotels/jobs/wrong-105/italian/", ""},
	} {
		got, ok := InterviewebJobURL(test.raw, board)
		if got != test.want || ok != (test.want != "") {
			t.Fatalf("canonical URL %q: got %q, %v; want %q", test.raw, got, ok, test.want)
		}
	}
	page, err := ParseInterviewebPage(`Pagina 1 di 2 <a href="/starhotels/jobs/assistant-manager-101/it/">Role</a>`, board)
	if err != nil || page.Pages != 2 || !reflect.DeepEqual(page.URLs, []string{"https://zinrec.intervieweb.it/starhotels/jobs/assistant-manager-101/it/"}) {
		t.Fatalf("Italian page not parsed: %+v, %v", page, err)
	}
}

func TestInterviewebZinrecConvergesOn27UniqueJobs(t *testing.T) {
	board := "https://zinrec.intervieweb.it/starhotels/it/career"
	o, err := TenthProviderOptionsFromMetadata("intervieweb", board, "{}")
	if err != nil {
		t.Fatal(err)
	}
	listing := func(start, end, page int) string {
		body := fmt.Sprintf("Pagina %d di 2 ", page)
		for i := start; i <= end; i++ {
			body += fmt.Sprintf(`<a href="/starhotels/jobs/role-%d/it/">Role</a>`, i)
		}
		return body
	}
	rows, err := DiscoverInterviewebSnapshot(context.Background(), o, func(_ context.Context, request Request) ([]byte, error) {
		if request.Method == "GET" {
			return []byte(`<input id="url-for-announces" value="/app.php?module=newcareer"><a data-order="name" class="active">Name</a><script>{"section":"starhotels"}</script>vacancyListCareer researchAnnounces ` + listing(1, 15, 1)), nil
		}
		if request.Method != "POST" || !strings.Contains(request.Body, "page=2") {
			t.Fatalf("unexpected pagination request: %+v", request)
		}
		body, _ := json.Marshal(map[string]any{"success": true, "data": listing(16, 27, 2)})
		return body, nil
	})
	if err != nil || len(rows) != 27 || rows[0] != "https://zinrec.intervieweb.it/starhotels/jobs/role-1/it/" {
		t.Fatalf("27-job snapshot not recovered: %d rows, %v", len(rows), err)
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
