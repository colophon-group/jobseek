package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
)

func TestMokahrDetailMatchesActualPython(t *testing.T) {
	type request struct {
		Method, URL string
		Body        any
	}
	var corpus struct {
		Projection []struct {
			Name     string
			Raw      json.RawMessage
			Cities   map[int]string
			Expected map[string]any
		}
		HTTP []struct {
			Name, URL string
			Config    map[string]string
			Pages     map[string]struct {
				Body    string
				Status  int
				Headers map[string]string
			}
			Requests       []request
			Expected       map[string]any
			NativeReserved bool `json:"native_reserved"`
			NativeEmpty    bool `json:"native_empty"`
		}
	}
	body, e := os.ReadFile("testdata/python_mokahr_detail.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Projection) != 17 || len(corpus.HTTP) != 24 {
		t.Fatal("actual Python detail corpus missing")
	}
	compare := func(t *testing.T, got, expected map[string]any) {
		t.Helper()
		b, e := json.Marshal(got)
		if e != nil {
			t.Fatal(e)
		}
		var normalized map[string]any
		if json.Unmarshal(b, &normalized) != nil || !reflect.DeepEqual(normalized, expected) {
			t.Fatalf("detail differs: got=%v expected=%v", normalized, expected)
		}
	}
	for _, c := range corpus.Projection {
		t.Run("fields/"+c.Name, func(t *testing.T) {
			d, e := api.Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			got, e := mokahrDetailValues(d.Value.(map[string]any), c.Cities)
			if e != nil {
				t.Fatal(e)
			}
			compare(t, got, c.Expected)
		})
	}
	for _, c := range corpus.HTTP {
		t.Run("http/"+c.Name, func(t *testing.T) {
			requests := []request{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := "https://" + r.Host + r.URL.String()
				body, e := io.ReadAll(r.Body)
				if e != nil {
					t.Error(e)
					w.WriteHeader(500)
					return
				}
				var value any
				if len(body) > 0 && json.Unmarshal(body, &value) != nil {
					t.Error("invalid request JSON")
					w.WriteHeader(500)
					return
				}
				requests = append(requests, request{r.Method, source, value})
				row, ok := c.Pages[source]
				if !ok {
					t.Error("undeclared resource", source)
					w.WriteHeader(500)
					return
				}
				for key, val := range row.Headers {
					w.Header().Set(key, val)
				}
				w.WriteHeader(row.Status)
				fmt.Fprint(w, row.Body)
			}))
			got, reserved, e := fetchMokahrDetail(context.Background(), client.client, c.URL, c.Config["locale"])
			if c.NativeReserved {
				if reserved == nil || got != nil || e != nil {
					t.Fatal("positive publisher policy escaped as content or soft miss", e)
				}
				if len(requests) > len(c.Requests) || !reflect.DeepEqual(requests, c.Requests[:len(requests)]) {
					t.Fatal("reserved route issued non-reference request")
				}
				return
			}
			if reserved != nil {
				t.Fatal("unexpected reservation")
			}
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("requests differ: got=%v expected=%v", requests, c.Requests)
			}
			if c.NativeEmpty {
				if !errors.Is(e, executor.ErrEmptyResult) || got != nil {
					t.Fatal("foreign provider id escaped", e)
				}
				return
			}
			if errors.Is(e, executor.ErrEmptyResult) {
				for _, value := range c.Expected {
					if value != nil {
						t.Fatal("soft miss discarded real reference content")
					}
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			compare(t, got, c.Expected)
		})
	}
}
