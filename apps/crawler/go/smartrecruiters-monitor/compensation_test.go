package smartrecruiters

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestDirectCompensation(t *testing.T) {
	for _, tc := range []struct {
		name  string
		input string
		want  any
	}{
		{"hourly-minimum", `{"min":17.5,"currency":"USD","period":"HOURLY"}`, Object{"min": json.Number("17.5"), "max": nil, "currency": "USD", "unit": "hour"}},
		{"annual-range", `{"min":130000,"max":150000,"currency":"USD","period":"YEARLY"}`, Object{"min": json.Number("130000"), "max": json.Number("150000"), "currency": "USD", "unit": "year"}},
		{"zero-maximum", `{"max":0,"currency":"EUR","period":"MONTHLY"}`, Object{"min": nil, "max": json.Number("0"), "currency": "EUR", "unit": "month"}},
		{"no-bounds", `{"currency":"USD","period":"HOURLY"}`, nil},
		{"nested-precedence", `{"min":17.5,"salary":{"min":20,"currency":"USD","period":"hourly"}}`, Object{"min": json.Number("20"), "max": nil, "currency": "USD", "unit": "hour"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			posting, err := decodeObject([]byte(`{"compensation":` + tc.input + `}`))
			if err != nil {
				t.Fatal(err)
			}
			job, err := ParseDetail(posting)
			if err != nil || !reflect.DeepEqual(job.BaseSalary, tc.want) {
				t.Fatalf("salary=%v want=%v err=%v", job.BaseSalary, tc.want, err)
			}
		})
	}
}
