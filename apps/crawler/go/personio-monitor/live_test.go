package personio

import (
	"context"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"
)

type doFunc func(*http.Request) (*http.Response, error)

func (fn doFunc) Do(request *http.Request) (*http.Response, error) { return fn(request) }

func response(request *http.Request, status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status, Request: request, Header: make(http.Header),
		Body: io.NopCloser(strings.NewReader(body)),
	}
}

func TestFetchBuildsLocalizedRichJobs(t *testing.T) {
	english := `<workzag-jobs><position><id>42</id><name>Engineer</name><office>Zurich</office>` +
		`<jobDescriptions><jobDescription><value>EN role</value></jobDescription></jobDescriptions>` +
		`</position></workzag-jobs>`
	german := `<workzag-jobs><position><id>42</id><name>Ingenieur</name><office>Zürich</office>` +
		`<jobDescriptions><jobDescription><value>DE Rolle</value></jobDescription></jobDescriptions>` +
		`</position></workzag-jobs>`
	requested := []string{}
	result, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		requested = append(requested, req.URL.String())
		if req.URL.Query().Get("language") == "en" {
			return response(req, 200, english), nil
		}
		return response(req, 200, german), nil
	}), "acme", "de", "en", []string{"de"})
	if err != nil {
		t.Fatal(err)
	}
	if len(requested) != 2 || result.Requests != 2 || result.Responses != 2 || len(result.Jobs) != 1 {
		t.Fatalf("requested=%v result=%+v", requested, result)
	}
	job := result.Jobs[0]
	if job.Language == nil || *job.Language != "en" || job.Localizations == nil || job.Localizations["de"] == nil || job.Description == nil || *job.Description != "EN role" {
		t.Fatalf("unexpected localization: %+v", job)
	}
}

func TestFetchPromotesEnglishAndFallsBackToOtherDomain(t *testing.T) {
	german := `<workzag-jobs><position><id>7</id><name>Entwickler</name>` +
		`<jobDescriptions><jobDescription><value>DE Rolle</value></jobDescription></jobDescriptions>` +
		`</position></workzag-jobs>`
	english := `<workzag-jobs><position><id>7</id><name>Developer</name>` +
		`<jobDescriptions><jobDescription><value>EN role</value></jobDescription></jobDescriptions>` +
		`</position></workzag-jobs>`
	result, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		if strings.Contains(req.URL.Host, ".personio.de") {
			return response(req, 404, ""), nil
		}
		if req.URL.Query().Get("language") == "de" {
			return response(req, 200, german), nil
		}
		return response(req, 200, english), nil
	}), "acme", "de", "de", []string{"en"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Requests != 3 || len(result.Jobs) != 1 || result.Jobs[0].URL != "https://acme.jobs.personio.com/job/7" || result.Jobs[0].Title == nil || *result.Jobs[0].Title != "Developer" || result.Jobs[0].Description == nil || *result.Jobs[0].Description != "EN role" || result.Jobs[0].Language == nil || *result.Jobs[0].Language != "en" {
		t.Fatalf("unexpected cross-domain English promotion: %+v", result)
	}
}

func TestFetchHTMLFallbackAndPublicAddressGuard(t *testing.T) {
	html := `<script>"jobs":[{"id":"100","name":"Engineer","main_office":"Berlin",` +
		`"schedule":"full-time"}],"subdomain":"acme"</script>`
	result, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Path == "/xml" {
			return response(req, 404, ""), nil
		}
		return response(req, 200, html), nil
	}), "acme", "de", "en", []string{"de"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Requests != 3 || len(result.Jobs) != 1 || result.Jobs[0].URL != "https://acme.jobs.personio.de/job/100" {
		t.Fatalf("unexpected HTML fallback: %+v", result)
	}
	for _, raw := range []string{"127.0.0.1", "10.0.0.1", "192.0.2.1", "2001:db8::1"} {
		if publicAddress(netip.MustParseAddr(raw)) {
			t.Errorf("accepted unsafe address %s", raw)
		}
	}
}

func TestBackfillMissKeepsPrimarySuccess(t *testing.T) {
	feed := `<workzag-jobs><position><id>42</id><name>Engineer</name></position></workzag-jobs>`
	result, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Query().Get("language") == "de" {
			return response(req, 404, ""), nil
		}
		return response(req, 200, feed), nil
	}), "acme", "de", "en", []string{"de"})
	if err != nil || result.Status != 200 || result.FinalURL != "https://acme.jobs.personio.de/xml?language=en" || result.Requests != 2 || result.Responses != 2 || len(result.Jobs) != 1 {
		t.Fatalf("optional backfill changed primary result: %+v %v", result, err)
	}
}
