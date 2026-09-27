package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestRegistryCSVAndSQLArgumentsMatchPython(t *testing.T) {
	var cases []struct {
		Name   string                   `json:"name"`
		CSV    map[string]string        `json:"csv"`
		Tables map[string]registryTable `json:"tables"`
		Plan   any                      `json:"plan"`
	}
	path := os.Getenv("REGISTRY_TEST_FIXTURE")
	if path == "" {
		path = "testdata/registry_fixture.json"
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			tables := map[string]registryTable{}
			for name, raw := range c.CSV {
				table, err := parseRegistryCSV([]byte(raw))
				if err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(table, c.Tables[name]) {
					t.Fatalf("CSV %s cells differ: %+v expected %+v", name, table, c.Tables[name])
				}
				tables[name] = table
			}
			plan, err := registryTaxonomyPlan(tables)
			if err != nil {
				t.Fatal(err)
			}
			companies, err := registryCompanyPlan(tables["companies"], tables["company_descriptions"])
			if err != nil {
				t.Fatal(err)
			}
			plan = append(plan, companies...)
			for _, statement := range plan {
				if _, err := registrySQL(statement.Key); err != nil {
					t.Fatal(err)
				}
			}
			encoded, err := json.Marshal(plan)
			if err != nil {
				t.Fatal(err)
			}
			var actual any
			if err = json.Unmarshal(encoded, &actual); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actual, c.Plan) {
				expected, _ := json.Marshal(c.Plan)
				t.Fatalf("plan mismatch\nactual=%s\nexpected=%s", encoded, expected)
			}
		})
	}
}
func TestRegistryCSVRejectsInvalidInput(t *testing.T) {
	for _, body := range [][]byte{[]byte("a,a\nx,y\n"), []byte("a,b\nx,y,z\n"), []byte("a\n\"unterminated"), {0xff}} {
		if _, err := parseRegistryCSV(body); err == nil {
			t.Fatalf("accepted %q", body)
		}
	}
}

func TestRegistryNullSlugsCannotBecomeEmptyIdentities(t *testing.T) {
	table, err := parseRegistryCSV([]byte("slug,en\n,Orphan\n"))
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"occupation_domains", "occupations", "seniority"} {
		if _, err := registryTaxonomyPlan(map[string]registryTable{kind: table}); err == nil {
			t.Fatalf("accepted null %s identity", kind)
		}
	}
	plan, err := registryCompanyPlan(registryTable{}, table)
	if err != nil {
		t.Fatal(err)
	}
	// The Python description join ignores NULL slugs. Never redirect these
	// descriptions onto a company whose (distinct) natural key is empty text.
	if len(plan) != 1 || plan[0].Args[0].([]*string)[0] != nil {
		t.Fatalf("description null identity was rewritten: %#v", plan)
	}
}
