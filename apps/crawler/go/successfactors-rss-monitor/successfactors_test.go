package successfactorsrss

import (
	"io"
	"strings"
	"testing"
)

func TestParsePageRichItems(t *testing.T) {
	feed := `<?xml version="1.0"?><rss xmlns:g="http://base.google.com/ns/1.0"><channel>
	<item><link>https://jobs.example.com/1</link><title>Engineer (Berlin, DE)</title>
	<description>&lt;p&gt;Build things&lt;/p&gt;</description><guid>123</guid>
	<pubDate>Fri, 25 Sep 2026 10:00:00 GMT</pubDate><g:location>Berlin, DE</g:location>
	<g:employer>Example</g:employer><g:job_function>ATS_WEBFORM</g:job_function></item>
	<item><link>https://jobs.example.com/2</link><title>Analyst (Paris, FR)</title>
	<description>&lt;strong&gt;Location:&lt;/strong&gt; Paris&lt;p&gt;Analyse&lt;/p&gt;</description>
	<g:job_function>Finance</g:job_function></item>
	<item><title>Missing URL</title></item></channel></rss>`
	jobs, count, err := ParsePage([]byte(feed))
	if err != nil {
		t.Fatal(err)
	}
	if count != 3 || len(jobs) != 2 {
		t.Fatalf("count=%d jobs=%d", count, len(jobs))
	}
	first := jobs[0]
	if first.Title == nil || *first.Title != "Engineer" || first.Description == nil || *first.Description != "<p>Build things</p>" || len(first.Locations) != 1 || first.Locations[0] != "Berlin, DE" || first.Metadata["id"] != "123" || first.Metadata["employer"] != "Example" {
		t.Fatalf("unexpected first job: %+v", first)
	}
	if _, ok := first.Metadata["job_function"]; ok {
		t.Fatalf("ATS_WEBFORM should be excluded: %+v", first.Metadata)
	}
	second := jobs[1]
	if second.Title == nil || *second.Title != "Analyst" || len(second.Locations) != 1 || second.Locations[0] != "Paris, FR" || second.Metadata["job_function"] != "Finance" {
		t.Fatalf("unexpected second job: %+v", second)
	}
}

func TestParsePageRejectsNonXMLAndMalformedXML(t *testing.T) {
	for _, body := range []string{"<html>login</html>", "<?xml version=\"1.0\"?><rss><item>"} {
		if _, _, err := ParsePage([]byte(body)); err == nil {
			t.Errorf("expected error for %q", body)
		}
	}
}

func TestParseReaderPlaceholderDescription(t *testing.T) {
	feed := `<rss><item><link>https://jobs.example.com/3</link><title>Senior Engineer</title><description>&lt;p&gt;Senior Engineer&lt;/p&gt;</description></item></rss>`
	jobs, _, err := ParseReader(strings.NewReader(feed))
	if err != nil {
		t.Fatal(err)
	}
	if len(jobs) != 1 || jobs[0].Description != nil {
		t.Fatalf("placeholder description should be empty: %+v", jobs)
	}
}

func TestParseReaderUsesFirstRepeatedFieldLikeElementTreeFind(t *testing.T) {
	feed := `<rss xmlns:g="http://base.google.com/ns/1.0"><item>` +
		`<link>https://jobs.example.com/first</link><link>https://jobs.example.com/second</link>` +
		`<g:location>Zurich</g:location><g:location>Berlin</g:location>` +
		`</item></rss>`
	jobs, _, err := ParseReader(strings.NewReader(feed))
	if err != nil {
		t.Fatal(err)
	}
	if len(jobs) != 1 || jobs[0].URL != "https://jobs.example.com/first" || len(jobs[0].Locations) != 1 || jobs[0].Locations[0] != "Zurich" {
		t.Fatalf("unexpected repeated-field precedence: %+v", jobs)
	}
}

func TestLargeFeedStreamsBeyondPilotByteLimit(t *testing.T) {
	// Reuse one half-MiB item instead of retaining the 256+ MiB feed in memory.
	const count = 513
	description := strings.Repeat("x", 512<<10)
	item := `<item><link>https://jobs.example.com/1</link><description>` + description + `</description></item>`
	readers := []io.Reader{strings.NewReader(`<rss>`)}
	for range count {
		readers = append(readers, strings.NewReader(item))
	}
	readers = append(readers, strings.NewReader(`</rss>`))
	emitted := 0
	items, jobs, truncated, err := ParseStream(io.MultiReader(readers...), func(job Job) error {
		if job.Description == nil || len(*job.Description) != len(description) {
			t.Fatal("description changed")
		}
		emitted++
		return nil
	})
	if err != nil || items != count || jobs != count || emitted != count || truncated {
		t.Fatalf("items=%d jobs=%d emitted=%d truncated=%v err=%v", items, jobs, emitted, truncated, err)
	}
}

func TestXMLReadWindowFailsClosed(t *testing.T) {
	window := &xmlReadWindow{source: strings.NewReader(strings.Repeat("x", 65)), remaining: 64}
	body, err := io.ReadAll(window)
	if err == nil || len(body) != 64 {
		t.Fatalf("bytes=%d err=%v", len(body), err)
	}
}
