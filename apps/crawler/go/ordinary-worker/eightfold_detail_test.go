package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
)

func TestEightfoldDetailMatchesActualPythonHTTP(t *testing.T) {
	type request struct{ Method, URL string }
	var corpus struct {
		HTTP []struct {
			Name, URL string
			Config    map[string]any
			Pages     map[string]struct {
				Body    string
				Status  int
				Headers map[string]string
			}
			Requests       []request
			Expected       map[string]any
			NativeReserved bool `json:"native_reserved"`
		}
	}
	body, e := os.ReadFile("testdata/python_eightfold.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.HTTP) != 18 {
		t.Fatal("actual Python detail HTTP reference missing")
	}
	for _, c := range corpus.HTTP {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := "https://" + r.Host + r.URL.String()
				requests = append(requests, request{r.Method, source})
				row, ok := c.Pages[source]
				if !ok {
					t.Error("undeclared fixture resource", source)
					w.WriteHeader(500)
					return
				}
				for key, value := range row.Headers {
					w.Header().Set(key, value)
				}
				w.WriteHeader(row.Status)
				fmt.Fprint(w, row.Body)
			}))
			got, reserved, e := fetchEightfoldDetail(context.Background(), client.client, c.URL, c.Config)
			if c.NativeReserved {
				if got != nil || reserved == nil || e != nil {
					t.Fatal("publisher reservation escaped", e)
				}
				if len(requests) > len(c.Requests) || !reflect.DeepEqual(requests, c.Requests[:len(requests)]) {
					t.Fatal("reservation emitted non-reference requests")
				}
				return
			}
			if reserved != nil {
				t.Fatal("unexpected publisher reservation")
			}
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("request history differs: got=%v expected=%v", requests, c.Requests)
			}
			if errors.Is(e, executor.ErrEmptyResult) {
				for _, v := range c.Expected {
					if v != nil {
						t.Fatal("soft miss lost real reference content")
					}
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			b, e := json.Marshal(got)
			if e != nil {
				t.Fatal(e)
			}
			var normalized map[string]any
			if json.Unmarshal(b, &normalized) != nil || !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("detail fields differ: got=%v expected=%v", normalized, c.Expected)
			}
		})
	}
}
