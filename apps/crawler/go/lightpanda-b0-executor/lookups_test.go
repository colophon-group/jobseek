package executor

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func TestNativeLookupFieldsMatchPython(t *testing.T) {
	var oracle struct {
		Occupations, Seniorities, Technologies map[string]int64
		Rates                                  map[string]float64
		Titles                                 []struct {
			Titles         []string
			EmploymentType *string `json:"employment_type"`
			IDs            []*int64
		}
		Descriptions []struct {
			Description   string
			TechnologyIDs []int64 `json:"technology_ids"`
			Salary        []json.RawMessage
			Experience    []*float64
		}
	}
	raw, err := os.ReadFile("testdata/python_lookups.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &oracle); err != nil {
		t.Fatal(err)
	}
	l := &NativeLookups{occupations: oracle.Occupations, seniorities: oracle.Seniorities, technologies: oracle.Technologies, rates: oracle.Rates}
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range oracle.Titles {
		emp := ""
		if c.EmploymentType != nil {
			emp = *c.EmploymentType
		}
		occupation, seniority, err := l.ResolveTitles(matcher, c.Titles, emp)
		if err != nil || !reflect.DeepEqual([]*int64{occupation, seniority}, c.IDs) {
			t.Fatalf("title fields differ: %v %v %v", c.Titles, occupation, seniority)
		}
	}
	for _, c := range oracle.Descriptions {
		fields, err := l.DescriptionFields(matcher, c.Description)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(fields.TechnologyIDs, c.TechnologyIDs) || !reflect.DeepEqual([]*float64{fields.ExperienceMin, fields.ExperienceMax}, c.Experience) {
			t.Fatalf("description fields differ: %+v", fields)
		}
		actual, err := json.Marshal([]any{fields.SalaryMin, fields.SalaryMax, fields.SalaryCurrency, fields.SalaryPeriod, fields.SalaryEUR})
		if err != nil {
			t.Fatal(err)
		}
		expected, err := json.Marshal(c.Salary)
		if err != nil {
			t.Fatal(err)
		}
		if string(actual) != string(expected) {
			t.Fatalf("salary fields differ: %s / %s", actual, expected)
		}
	}
	delete(l.seniorities, "intern")
	_, seniority, err := l.ResolveTitles(matcher, []string{"Senior Software Engineer"}, "intern")
	if err != nil || seniority != nil {
		t.Fatal("unmapped internship must override seniority to NULL")
	}
	delete(l.occupations, "data-engineer")
	occupation, _, err := l.ResolveTitles(matcher, []string{"Data Engineer", "Software Engineer"}, "")
	if err != nil || occupation == nil || *occupation != 41 {
		t.Fatal("unmapped earlier title blocked later match")
	}
}
