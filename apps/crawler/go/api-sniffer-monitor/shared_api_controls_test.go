package apisniffer

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func TestBrowserReplayRootPageAnnotationDoesNotTruncateTraversal(t *testing.T) {
	raw := `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"max_pages":3,"pagination":{"param_name":"page","start_value":1,"increment":1}}`
	o, err := BrowserReplayOptionsFromMetadata("https://example.com/careers", raw)
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	result, err := DiscoverBrowserReplay(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
		calls++
		return Decode([]byte(fmt.Sprintf(`{"jobs":[{"id":"%d","title":"Engineer"}]}`, calls)))
	}, func(_, ref string) (string, error) { return ref, nil }, false)
	if err != nil || calls != 50 || len(result.Jobs) != 50 || result.Truncated {
		t.Fatal("root annotation changed original 50-page browser budget", calls, len(result.Jobs), result.Truncated, err)
	}
	for _, invalid := range []string{"true", "0", "501", "1.5", `"3"`} {
		if _, err := BrowserReplayOptionsFromMetadata("https://example.com/careers", strings.Replace(raw, `"max_pages":3`, `"max_pages":`+invalid, 1)); err == nil {
			t.Fatal("unbounded or malformed annotation admitted", invalid)
		}
	}
}

func TestInferredConvergenceRefusesInvalidAdvertisedTotals(t *testing.T) {
	o := Options{Path: "data.jobs"}
	for _, tc := range []struct {
		body  string
		want  int
		valid bool
	}{
		{`{"data":{"jobs":[],"total":0}}`, 0, true},
		{`{"data":{"jobs":[],"total":4},"total":9}`, 4, true},
		{`{"data":{"jobs":[]},"total":4}`, 4, true},
		{`{"data":{"jobs":[],"total":false}}`, 0, false},
		{`{"data":{"jobs":[],"total":true},"total":4}`, 0, false},
		{`{"data":{"jobs":[],"total":"4"}}`, 0, false},
		{`{"data":{"jobs":[]}}`, 0, false},
		{`{"data":{"jobs":{}} ,"total":4}`, 0, false},
	} {
		d, err := Decode([]byte(tc.body))
		if err != nil {
			t.Fatal(err)
		}
		got, valid := convergenceShape(d, o)
		if got != tc.want || valid != tc.valid {
			t.Fatal("advertised total proof changed", tc.body, got, valid)
		}
	}
}
