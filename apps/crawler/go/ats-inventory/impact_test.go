package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestImpactParquetAndConservativeMatchingMatchPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_impact.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Case, File string
		Artifact   impactArtifact
		Summary    impactFamilySummary
		FamilyRows map[string][]inventoryRow `json:"family_rows"`
		Companies  []companyImpact
		Reports    map[string]map[string]any
	}
	if e = json.Unmarshal(raw, &cases); e != nil {
		t.Fatal(e)
	}
	for _, c := range cases {
		t.Run(c.Case, func(t *testing.T) {
			got, e := deriveImpactFamily(context.Background(), filepath.Join("testdata", c.File), c.Artifact, c.Summary.DerivedAt)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(got, c.Summary) {
				t.Fatalf("derived Parquet buckets differ from Python\ngot %#v\nwant %#v", got, c.Summary)
			}
			companies, reports := resolveImpactCompanies(c.FamilyRows, map[string]*impactFamilySummary{"greenhouse": &got, "lever": nil})
			if !reflect.DeepEqual(normalizedJSON(t, companies), normalizedJSON(t, c.Companies)) {
				t.Fatal("company impact differs from Python")
			}
			if !reflect.DeepEqual(normalizedJSON(t, reports), normalizedJSON(t, c.Reports)) {
				t.Fatal("family matching report differs from Python")
			}
			wrong := c.Artifact
			wrong.Rows++
			if _, e = deriveImpactFamily(context.Background(), filepath.Join("testdata", c.File), wrong, c.Summary.DerivedAt); e == nil {
				t.Fatal("wrong row count accepted")
			}
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			if _, e = deriveImpactFamily(ctx, filepath.Join("testdata", c.File), c.Artifact, c.Summary.DerivedAt); e == nil {
				t.Fatal("cancelled derivation accepted")
			}
		})
	}
}
