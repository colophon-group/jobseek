package worker

import (
	"context"
	"encoding/json"
	"errors"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"os"
	"reflect"
	"sync"
	"testing"
)

type fourthDetailCase struct {
	Provider, Name, URL string
	Config              map[string]any
	Pages               map[string]json.RawMessage
	Requests            []fourthHTTPRequest
	Expected            struct {
		Error, Empty bool
		Content      map[string]any
	}
}

func fourthDetailCases(t *testing.T) []fourthDetailCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_fourth_provider_detail.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ HTTP []fourthDetailCase }
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.HTTP) != 27 {
		t.Fatal("actual Python detail corpus unavailable")
	}
	return corpus.HTTP
}
func TestFourthProvidersMatchActualPythonDetailHTTPRequestsAndOutput(t *testing.T) {
	for _, c := range fourthDetailCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			client := fourthHTTPFixture(t, c.Pages, &requests, &mutex)
			var fields map[string]any
			var reserved *policy.Reservation
			var e error
			if c.Provider == "paycom" {
				fields, reserved, e = fetchPaycomDetailWithWait(context.Background(), client.client, queue.WorkdayDetailProfile{SourceURL: c.URL, HTTPAPIConfig: c.Config}, noSecondaryWait)
			} else {
				override, _ := c.Config["slug"].(string)
				fields, reserved, e = fetchRipplingDetail(context.Background(), client.client, c.URL, override)
			}
			if reserved != nil {
				t.Fatal("reference invented a reservation")
			}
			nativeEmpty := errors.Is(e, executor.ErrEmptyResult)
			if c.Expected.Error {
				if e == nil {
					t.Fatal("reference failure became content")
				}
			} else if e != nil && !(c.Expected.Empty && nativeEmpty) {
				t.Fatal("valid or empty content failed", e)
			}
			mutex.Lock()
			observed := append([]fourthHTTPRequest{}, requests...)
			mutex.Unlock()
			if !reflect.DeepEqual(observed, c.Requests) {
				t.Fatalf("request contract changed: actual=%v expected=%v", observed, c.Requests)
			}
			if c.Expected.Error {
				return
			}
			if nativeEmpty {
				fields = map[string]any{}
				for _, k := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
					fields[k] = nil
				}
			}
			body, _ := json.Marshal(fields)
			var normalized map[string]any
			json.Unmarshal(body, &normalized)
			if !reflect.DeepEqual(normalized, c.Expected.Content) {
				t.Fatalf("detail projection changed: actual=%s expected=%v", body, c.Expected.Content)
			}
		})
	}
}
