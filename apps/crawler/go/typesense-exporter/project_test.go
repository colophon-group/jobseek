package main

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"
)

func ptr[T any](value T) *T { return &value }

func TestCandidateOrder(t *testing.T) {
	for _, tc := range []struct {
		id, key, bucket string
		hi, lo          int64
	}{
		{"00000000-0000-0000-0000-000000000000", "----------------------", "00", -9223372036854775808, -9223372036854775808},
		{"ffffffff-ffff-ffff-ffff-ffffffffffff", "2zzzzzzzzzzzzzzzzzzzzz", "ff", 9223372036854775807, 9223372036854775807},
		{"80000000-0000-0000-0000-000000000001", "1--------------------0", "80", 0, -9223372036854775807},
	} {
		key, hi, lo, bucket, err := candidateOrder(tc.id, true)
		if err != nil {
			t.Fatal(err)
		}
		if key != tc.key || hi != tc.hi || lo != tc.lo || bucket != tc.bucket {
			t.Errorf("%s: got (%v,%v,%v,%s), want (%s,%d,%d,%s)", tc.id, key, hi, lo, bucket, tc.key, tc.hi, tc.lo, tc.bucket)
		}
	}
}

func TestProjectRichAndInactive(t *testing.T) {
	now := time.Date(2025, 6, 15, 12, 0, 0, 0, time.UTC)
	row := Row{
		ID: "00000000-0000-0000-0000-000000000001", CompanyID: "00000000-0000-0000-0000-000000000002",
		CompanyName: "TestCo", CompanySlug: "testco", Titles: []string{"Senior Engineer"}, IsActive: true,
		LocationIDs: []int{10, 11}, LocationTypes: []string{"onsite", "hybrid"},
		OccupationID: ptr(100), SeniorityID: ptr(1), TechnologyIDs: []int{50},
		EmploymentType: "full-time", ExperienceMin: ptr(1.5), ExperienceMax: ptr(2.5),
		FirstSeenAt: &now, LastSeenAt: &now, SalaryMin: ptr(int64(120000)),
		DescriptionR2Hash: ptr(int64(0)),
	}
	maps := Maps{
		LocationNames:       map[int]map[string]string{10: {"en": "Zurich"}, 11: {"en": "Winterthur"}},
		LocationTypes:       map[int]string{10: "city", 11: "city"},
		LocationAncestors:   map[int][]int{10: {10, 20, 30}, 11: {11, 20, 30}},
		OccupationNames:     map[int]string{100: "Software Engineer"},
		OccupationAncestors: map[int][]int{100: {100, 200}},
		SeniorityNames:      map[int]string{1: "Senior"}, TechnologyNames: map[int]string{50: "Python"},
	}
	doc, err := project(row, maps)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(doc["location_ids"], []int{10, 11, 20, 30}) || doc["has_content"] != true || doc["experience_min"] != 2 || doc["experience_max"] != 2 || doc["salary_min"] != int64(120000) {
		encoded, _ := json.Marshal(doc)
		t.Fatalf("rich projection mismatch: %s", encoded)
	}
	if !reflect.DeepEqual(doc["occupation_ids"], []int{100, 200}) || !reflect.DeepEqual(doc["locales"], []string{"_none"}) {
		t.Fatal("taxonomy or locale mismatch")
	}
	row.IsActive = false
	row.DescriptionR2Hash = nil
	doc, err = project(row, maps)
	if err != nil {
		t.Fatal(err)
	}
	if doc["candidate_order_key"] != nil || doc["candidate_order_hi"] != nil || doc["candidate_order_lo"] != nil || doc["has_content"] != false {
		t.Fatal("inactive projection mismatch")
	}
}
