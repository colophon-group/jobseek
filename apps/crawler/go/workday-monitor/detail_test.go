package workday

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestProjectDetailPreservesWorkdayFields(t *testing.T) {
	payload := []byte(`{"jobPostingInfo":{"title":"Senior Engineer","jobDescription":"<p>Build software</p>","location":"Santa Clara, CA","additionalLocations":["Austin, TX","Santa Clara, CA","Remote"],"timeType":"Full-time","remoteType":"Flexible","startDate":"2024-01-15","jobReqId":"JR001"}}`)
	content, err := ProjectDetail(payload, "freseniusglobal", nil)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(content)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"title": "Senior Engineer", "description": "<p>Build software</p>",
		"locations":       []any{"Santa Clara, CA", "Austin, TX", "Remote"},
		"employment_type": "Full-time", "job_location_type": "hybrid",
		"date_posted": "2024-01-15", "metadata": map[string]any{"jobReqId": "JR001"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("detail content = %#v", got)
	}
}

func TestProjectDetailRejectsInvalidUpstreamShape(t *testing.T) {
	for _, payload := range []string{
		`{}`, `{"jobPostingInfo":null}`, `{"jobPostingInfo":[]}`,
		`{"jobPostingInfo":{"additionalLocations":"Austin"}}`,
		`{"jobPostingInfo":{"country":42}}`,
	} {
		if _, err := ProjectDetail([]byte(payload), "tenant", nil); err == nil {
			t.Fatalf("accepted invalid Workday detail %s", payload)
		}
	}
}

func TestWorkdayLocationNormalizationMatchesEstablishedCases(t *testing.T) {
	cases := []struct {
		raw, country, tenant string
		aliases              []string
		want                 string
	}{
		{"US-AR-SPRINGDALE-BLDG 1 ~ 275 E Robinson Ave", "", "", nil, "Springdale, AR, US"},
		{"AU-NSW-NOWRA-039 ~ 39 Wugan St", "", "", nil, "Nowra, NSW, AU"},
		{"Sg  Singapore", "", "", nil, "Sg, Singapore"},
		{"New York, NY, United States", "", "", nil, "New York, NY, United States"},
		{"Store 1272-South Franklin-maurices-Colby, KS 67701", "United States of America", "maurices", nil, "Colby, KS, United States of America"},
		{"Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660", "United States of America", "maurices", []string{"maurice"}, "Pflugerville, TX, United States of America"},
		{"Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660", "United States of America", "maurices", nil, "Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660"},
		{"Store 9999-Test Mall-maurices-Winston-Salem, NC 27101", "United States of America", "maurices", nil, "Winston-Salem, NC, United States of America"},
		{"Store 9999-Unknown-Winston-Salem, NC 27101", "United States of America", "maurices", nil, "Store 9999-Unknown-Winston-Salem, NC 27101"},
	}
	for _, test := range cases {
		got := normalizeWorkdayLocation(test.raw, test.country, test.tenant, test.aliases)
		if got != test.want {
			t.Errorf("normalize %q = %q, want %q", test.raw, got, test.want)
		}
	}
}
