package worker

import (
	"math"
	"strings"
	"testing"
)

func TestAuraDescriptionPreservesLocationAndCampaignDisclosures(t *testing.T) {
	for _, test := range []struct {
		name      string
		locations []map[string]any
		campus    bool
		ny, pl    bool
	}{
		{"london", []map[string]any{{"Name": "London"}}, false, false, false},
		{"new-york", []map[string]any{{"Name": "New York office"}}, false, true, false},
		{"san-francisco", []map[string]any{{"Name": "San Francisco"}}, false, true, false},
		{"chicago", []map[string]any{{"Name": "Chicago"}}, false, true, false},
		{"poland", []map[string]any{{"Name": "Warsaw, Poland"}}, false, false, true},
		{"both", []map[string]any{{"Name": "Chicago"}, {"Name": "Poland"}}, false, true, true},
		{"campus-excluded", []map[string]any{{"Name": "New York"}}, true, false, false},
		{"no-location", nil, false, false, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			job := map[string]any{"Is_Campus_Campaign_Event__c": test.campus, "Currency__c": "USD", "Base_Compensation_Low__c": float64(100000), "Base_Compensation_High__c": float64(120000), "Frequency__c": "Annually"}
			document, err := composeAuraDescription(job, test.locations, "<p>Original role description.</p>", "San Francisco,New York,Chicago,Poland")
			if err != nil || !strings.Contains(document, "Original role description.") || strings.Contains(document, "NY, CA, and IL") != test.ny || strings.Contains(document, "PL-based applicants") != test.pl {
				t.Fatal("original campaign/location disclosure behavior changed")
			}
			if (test.ny || test.pl) && !strings.Contains(document, "USD 100000 and USD 120000 annually") {
				t.Fatal("pay range, currency or frequency lost")
			}
			if test.ny && test.pl && strings.Index(document, "NY, CA, and IL") > strings.Index(document, "PL-based applicants") {
				t.Fatal("original disclosure order changed")
			}
		})
	}
}

func TestAuraDescriptionRejectsMalformedRequiredData(t *testing.T) {
	for _, job := range []map[string]any{
		{"Is_Campus_Campaign_Event__c": "false"},
		{"Base_Compensation_Low__c": math.Inf(1)},
		{"Frequency__c": []string{"Annually"}},
	} {
		if _, err := composeAuraDescription(job, []map[string]any{{"Name": "New York"}}, "<p>Original.</p>", "New York"); err == nil {
			t.Fatal("malformed publisher data produced compensation content")
		}
	}
	if _, err := composeAuraDescription(nil, nil, strings.Repeat("x", 4_000_001), "New York"); err == nil {
		t.Fatal("oversized description accepted")
	}
	if _, err := composeAuraDescription(nil, []map[string]any{{"Name": 1}}, "<p>Original.</p>", "New York"); err == nil {
		t.Fatal("invalid location produced content")
	}
}
