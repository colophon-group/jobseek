package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestJobConvoDetailActualOriginalRequestsAndFields(t *testing.T) {
	var cases []struct {
		Mode, Source string
		Config       struct{ Locale string }
		Exchanges    []struct {
			URL, Method, Accept, Body string
			Status                    int
		}
		Expected map[string]any
		Error    bool
	}
	b, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jobconvo_detail_requests.json")
	if e != nil || json.Unmarshal(b, &cases) != nil || len(cases) != 12 {
		t.Fatal("original detail requests missing", e)
	}
	for _, c := range cases {
		t.Run(c.Mode, func(t *testing.T) {
			req, _, e := api.JobConvoDetailRequest(c.Source, c.Config.Locale)
			if e != nil {
				t.Fatal(e)
			}
			p := queue.WorkdayDetailProfile{Profile: "jobconvo.public-detail/v1", SourceURL: c.Source, Endpoint: req.URL, APILocale: c.Config.Locale}
			calls := 0
			client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
				index := calls
				calls++
				if index >= len(c.Exchanges) {
					t.Fatal("extra detail request")
				}
				x := c.Exchanges[index]
				if r.URL.String() != x.URL || r.Method != x.Method || r.Header.Get("Accept") != x.Accept {
					t.Error("original detail request changed")
				}
				return &http.Response{StatusCode: x.Status, Header: http.Header{"Content-Type": {"application/json"}, "Location": {"https://foreign.example/"}}, Body: io.NopCloser(strings.NewReader(x.Body)), Request: r}, nil
			})}
			got, reserved, e := fetchJobConvoDetail(context.Background(), client, p)
			if calls != len(c.Exchanges) || reserved != nil {
				t.Fatal("one-shot detail request changed", calls, reserved)
			}
			if c.Error || len(c.Expected) == 0 {
				if e == nil {
					t.Fatal("original empty/failed detail accepted")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			a, _ := json.Marshal(got)
			b, _ := json.Marshal(c.Expected)
			var av, bv any
			json.Unmarshal(a, &av)
			json.Unmarshal(b, &bv)
			if !reflect.DeepEqual(av, bv) {
				t.Fatalf("original detail fields changed: got %s want %s", a, b)
			}
		})
	}
}
