package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestOriginalAutomaticAPIHTTPFieldsAndRequests(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_auto_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name             string
		BoardURL         string `json:"board_url"`
		Metadata         json.RawMessage
		Expected         []map[string]any
		Truncated, Error bool
		URLOnly          bool `json:"url_only"`
		Calls            []struct {
			Method, URL, Body string
			Headers           map[string]string
		}
		Responses []struct {
			Page   int
			Size   *int
			Status int
			Data   json.RawMessage
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 17 {
		t.Fatal("actual Python automatic HTTP oracle missing")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, config := configuredAPIFixture(string(c.Metadata))
			config["board_url"] = c.BoardURL
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				if calls >= len(c.Calls) {
					t.Fatal("more requests than Python")
				}
				expected := c.Calls[calls]
				calls++
				body := ""
				if r.Body != nil {
					bytes, _ := io.ReadAll(r.Body)
					body = string(bytes)
				}
				if r.Method != expected.Method || r.URL.String() != expected.URL || body != expected.Body {
					t.Fatal("request shape changed", r.URL.String(), expected.URL)
				}
				for k, v := range expected.Headers {
					if r.Header.Get(k) != v {
						t.Fatal("configured header changed", k)
					}
				}
				q := r.URL.Query()
				page, _ := strconv.Atoi(q.Get("page"))
				size, _ := strconv.Atoi(q.Get("limit"))
				if q.Get("page") == "" {
					page, _ = strconv.Atoi(q.Get("offset"))
				}
				var data []byte
				status := 200
				for _, response := range c.Responses {
					if page == response.Page && (response.Size == nil || *response.Size == size) {
						data = response.Data
						status = response.Status
						break
					}
				}
				if data == nil {
					data = []byte(`{"jobs":[]}`)
				}
				return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(string(data))), Request: r}, nil
			})}
			found, e := discoverAPISnifferInventory(context.Background(), client, p, config)
			if (e != nil) != c.Error || calls != len(c.Calls) {
				t.Fatal("outcome or request count changed", e, calls, len(c.Calls))
			}
			if e != nil {
				return
			}
			fields := []map[string]any{}
			for _, j := range found.Jobs {
				if j.URLOnly != c.URLOnly {
					t.Fatal("URL-only result changed")
				}
				metadata, extras := j.Metadata, j.Extras
				if metadata == nil {
					metadata = map[string]any{}
				}
				if extras == nil {
					extras = map[string]any{}
				}
				fields = append(fields, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": metadata, "extras": extras})
			}
			actual, _ := json.Marshal(fields)
			var normalized []map[string]any
			json.Unmarshal(actual, &normalized)
			if found.Truncated != c.Truncated || !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("automatic fields changed: %s; expected %v", actual, c.Expected)
			}
		})
	}
}
