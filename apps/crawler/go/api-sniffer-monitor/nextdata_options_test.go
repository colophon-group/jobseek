package apisniffer

import "testing"

func TestNextdataPageURLsPreservePythonQueryGroupingAndZeroStart(t *testing.T) {
	o, err := NextdataOptionsFromMetadata("https://example.com/jobs?tag=a&empty=&page=9&tag=b&bare", `{"path":"jobs","url_template":"https://example.com/jobs/{id}","pagination":{"path":"pagination","page_count":"pages","start":0}}`)
	if err != nil {
		t.Fatal(err)
	}
	page, err := o.PageURL(1)
	if err != nil || page != "https://example.com/jobs?tag=a&tag=b&empty=&page=1&bare=" {
		t.Fatal(page, err)
	}
	if !o.ResourceMatches(page) || o.ResourceMatches("https://example.com/jobs?tag=a&tag=b&empty=&page=1&bare=&extra=1") || o.ResourceMatches("https://example.com/jobs?tag=a&tag=b&empty=&page=99999999&bare=") {
		t.Fatal("page authority escaped configured query")
	}
}

func TestNextdataEmptyInventoryNeedsAuthoritativePaginationEvidence(t *testing.T) {
	o, err := NextdataOptionsFromMetadata("https://example.com/jobs", `{"path":"jobs","url_template":"https://example.com/jobs/{id}","pagination":{"path":"pagination","total_records":"total","page_size":10}}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range []struct {
		payload string
		pages   int
		ok      bool
	}{
		{`{"pagination":{"total":0}}`, 0, true},
		{`{"pagination":{"total":21}}`, 3, true},
		{`{"pagination":{"total":"20"}}`, 2, true},
		{`{"pagination":{}}`, 0, false},
		{`{"pagination":{"total":-1}}`, 0, false},
	} {
		d, err := Decode([]byte(row.payload))
		if err != nil {
			t.Fatal(err)
		}
		n, total, err := d.NextdataPageCount(o)
		if (err == nil) != row.ok || err == nil && (n != row.pages || total == nil) {
			t.Fatal(row.payload, n, total, err)
		}
	}
}
