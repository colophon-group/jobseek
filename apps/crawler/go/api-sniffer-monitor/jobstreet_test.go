package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"
)

func TestJobStreetOriginalFieldsAndEnvelopes(t *testing.T) {
	var cases []struct {
		Name, Kind, Host string
		Page             json.RawMessage
		Expected         json.RawMessage
		Total            *int
		Error            bool
	}
	body, e := os.ReadFile("testdata/python_jobstreet.json")
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(body, &cases); e != nil {
		t.Fatal(e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o := JobStreetOptions{Host: c.Host, CompanyID: "175608148114568", OrganisationID: "744981"}
			var actual any
			var err error
			if c.Kind == "page" {
				var jobs []JobStreetJob
				var total int
				jobs, total, err = ParseJobStreetPage(c.Page, o, 1)
				fields := []map[string]any{}
				for _, job := range jobs {
					fields = append(fields, job.Fields)
				}
				actual = fields
				if !c.Error && (c.Total == nil || total != *c.Total) {
					t.Fatalf("total %d", total)
				}
			} else {
				var job JobStreetJob
				job, err = ParseJobStreetDetail(c.Page, c.Host, "12345678")
				actual = job.Fields
			}
			if (err != nil) != c.Error {
				t.Fatalf("error=%v, expected refusal=%v", err, c.Error)
			}
			if c.Error {
				return
			}
			var expected any
			if e = json.Unmarshal(c.Expected, &expected); e != nil {
				t.Fatal(e)
			}
			strip := func(value any) {
				if list, ok := value.([]any); ok {
					for _, row := range list {
						delete(row.(map[string]any), "base_salary")
					}
				} else {
					delete(value.(map[string]any), "base_salary")
				}
			}
			strip(expected)
			encoded, _ := json.Marshal(actual)
			var normalized any
			_ = json.Unmarshal(encoded, &normalized)
			if !reflect.DeepEqual(normalized, expected) {
				t.Fatalf("fields differ\nactual%s\nexpected%s", encoded, c.Expected)
			}
		})
	}
}
func TestJobStreetBoundRequestsAndFailureConservation(t *testing.T) {
	board := "https://sg.jobstreet.com/companies/ehl-educational-group-172077835588736/jobs"
	o, e := JobStreetOptionsFromMetadata(board, `{"host":"sg.jobstreet.com","company_id":"172077835588736","organisation_id":"699133"}`)
	if e != nil {
		t.Fatal(e)
	}
	for _, bad := range []string{`{"host":"my.jobstreet.com"}`, `{"company_id":"123"}`, `{"organisation_id":123}`, `{"api_url":"https://evil.test"}`} {
		if _, e := JobStreetOptionsFromMetadata(board, bad); e == nil {
			t.Fatalf("accepted%s", bad)
		}
	}
	good := o.PageRequest(1)
	if !o.ResourceMatches(good.URL) || o.ResourceMatches(good.URL+"&companyid=foreign") || o.ResourceMatches(o.PageRequest(101).URL) {
		t.Fatal("tenant resource mismatch")
	}
	req, host, id, e := JobStreetDetailRequest("https://sg.jobstreet.com/job/12345678", nil)
	if e != nil || host != o.Host || id != "12345678" || req.Method != "POST" || req.URL != o.CompanyRequest().URL {
		t.Fatal("detail request")
	}
	var payload map[string]any
	_ = json.Unmarshal([]byte(req.Body), &payload)
	if payload["query"] != jobStreetDetailQuery || payload["variables"].(map[string]any)["locale"] != "en-SG" {
		t.Fatal("detail request drift")
	}
	calls := 0
	out, e := DiscoverJobStreet(context.Background(), o, func(_ context.Context, r Request) ([]byte, error) {
		calls++
		if r.URL != o.PageRequest(calls).URL {
			t.Fatal("request sequence")
		}
		if calls == 2 {
			return nil, fmt.Errorf("later refusal")
		}
		rows := []map[string]any{}
		for i := 0; i < 100; i++ {
			rows = append(rows, map[string]any{"id": fmt.Sprint(i + 1), "title": "Role", "employer": map[string]string{"id": o.OrganisationID, "companyId": o.CompanyID}})
		}
		return json.Marshal(map[string]any{"data": rows, "totalCount": 101, "solMetadata": map[string]int{"pageNumber": 1, "pageSize": 100, "totalJobCount": 101}})
	})
	if e == nil || len(out.Jobs) != 0 || calls != 2 {
		t.Fatal("partial inventory escaped")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	calls = 0
	out, e = DiscoverJobStreet(ctx, o, func(context.Context, Request) ([]byte, error) { calls++; return nil, nil })
	if e == nil || len(out.Jobs) != 0 || calls != 0 {
		t.Fatal("cancelled inventory")
	}
}
