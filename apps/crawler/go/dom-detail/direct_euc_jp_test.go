package dom

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"golang.org/x/text/encoding/japanese"
	"golang.org/x/text/transform"
)

func TestEUCJPListingAndDetailDecodeOriginalText(t *testing.T) {
	encoded, _, err := transform.String(japanese.EUCJP.NewEncoder(), `<html><body><h1>技術職の募集</h1><a href="?job_code=123">技術職</a><h2>Role</h2><p>勤務地：東京</p></body></html>`)
	if err != nil {
		t.Fatal(err)
	}
	for _, label := range []string{"euc_jp", "EUC-JP"} {
		t.Run(label, func(t *testing.T) {
			c, err := ListingOptions(Object{"encoding": label, "url_filter": "job_code="}, "https://example.com/jobs")
			if err != nil || c.Encoding != "euc-jp" {
				t.Fatal("original codec alias rejected", err)
			}
			decoded := jsonld.DecodeDocument([]byte(encoded), "text/html; charset="+c.Encoding)
			links, err := ListingHrefs(decoded, "a")
			if err != nil || len(links) != 1 || links[0] != "?job_code=123" || !strings.Contains(decoded, "技術職の募集") {
				t.Fatal("Japanese listing or job link changed", err)
			}
			config := directConfig(t, `,"encoding":"`+label+`"`)
			client := &http.Client{Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"text/html; charset=utf-8"}}, Body: io.NopCloser(strings.NewReader(encoded)), Request: r}, nil
			})}
			result, err := FetchDetailWithClient(context.Background(), "https://example.com/job", config, client)
			if err != nil || result.Content["title"] != "技術職の募集" || !strings.Contains(result.Content["description"].(string), "勤務地：東京") {
				t.Fatal("explicit original codec failed to override incorrect HTTP charset", err)
			}
		})
	}
}
