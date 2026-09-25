package teamtailorrss

import (
	"strings"
	"testing"
)

const sample = `<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:tt="https://teamtailor.com/locations" version="2.0"><channel>
<item><link>https://careers.example.com/jobs/123</link><title>Engineer</title>
<description><![CDATA[<p>Work &amp; learn</p>]]></description>
<pubDate>Fri, 25 Sep 2026 12:00:00 +0000</pubDate><guid>guid-123</guid>
<remoteStatus>Fully Remote</remoteStatus>
<tt:locations><tt:location><tt:city>Zurich</tt:city><tt:country>Switzerland</tt:country></tt:location></tt:locations>
<tt:department>Product</tt:department><tt:role>Engineering</tt:role></item>
<item><link>https://careers.example.com/jobs/456</link><title>Designer</title>
<remoteStatus>Remote</remoteStatus><tt:locations></tt:locations></item>
<item><title>Unpublished item without a link</title></item>
</channel></rss>`

func TestParsePageRichTeamtailorFields(t *testing.T) {
	jobs, items, err := ParsePage([]byte(sample))
	if err != nil {
		t.Fatal(err)
	}
	if items != 3 || len(jobs) != 2 {
		t.Fatalf("items=%d jobs=%d", items, len(jobs))
	}
	first := jobs[0]
	if first.Title == nil || *first.Title != "Engineer" || first.Description == nil || *first.Description != "<p>Work & learn</p>" {
		t.Fatalf("unexpected first rich fields: %+v", first)
	}
	if len(first.Locations) != 1 || first.Locations[0] != "Zurich, Switzerland" || first.JobLocationType == nil || *first.JobLocationType != "remote" {
		t.Fatalf("unexpected first location: %+v", first)
	}
	if first.Metadata["id"] != "guid-123" || first.Metadata["department"] != "Product" || first.Metadata["role"] != "Engineering" {
		t.Fatalf("unexpected metadata: %+v", first.Metadata)
	}
	if len(jobs[1].Locations) != 1 || jobs[1].Locations[0] != "Remote" {
		t.Fatalf("missing fully remote fallback: %+v", jobs[1])
	}
}

func TestParsePageRejectsPartialAndNonXML(t *testing.T) {
	for _, body := range []string{"feed disabled", "<html></html>", strings.TrimSuffix(sample, "</rss>")} {
		if _, _, err := ParsePage([]byte(body)); err == nil {
			t.Fatalf("accepted invalid RSS %q", body[:min(16, len(body))])
		}
	}
}
