package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestRegistryOccupationMatchesPython(t *testing.T) {
	path := os.Getenv("REGISTRY_OCCUPATION_TEST_FIXTURE")
	if path == "" {
		path = "testdata/registry_occupation_fixture.json"
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Table   registryTable `json:"table"`
		Samples []struct {
			Raw, Normalized string
			Slug            *string
		} `json:"samples"`
	}
	if err = json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	resolver := newRegistryOccupationResolver(fixture.Table)
	for _, sample := range fixture.Samples {
		if got := normalizeRegistryOccupation(sample.Raw); got != sample.Normalized {
			t.Fatalf("normalize %q: %q expected %q", sample.Raw, got, sample.Normalized)
		}
		want := ""
		if sample.Slug != nil {
			want = *sample.Slug
		}
		if got := resolver.Match(sample.Raw); got != want {
			t.Fatalf("match %q: %q expected %q", sample.Raw, got, want)
		}
	}
	t.Logf("matched %d Python occupation samples", len(fixture.Samples))
}

func TestRegistryPostgresPendingMissesPreserveExistingFields(t *testing.T) {
	ctx, conn, observer := registryTestDatabase(t)
	_, err := conn.Exec(ctx, `
INSERT INTO occupation(id,slug) VALUES(12,'software-engineer');
INSERT INTO seniority(id,slug) VALUES(8,'senior');
INSERT INTO technology(id,slug,name) VALUES(9,'go','Go');
INSERT INTO company(slug,name) VALUES('fixture','Fixture');
INSERT INTO job_posting(company_id,source_url,enrichment) SELECT id,'https://fixture.invalid/empty','{"occupation":"Software Developer","seniority":"senior","technologies":["Go"]}'::jsonb FROM company;
INSERT INTO job_posting(company_id,source_url,enrichment,occupation_id,seniority_id,technology_ids) SELECT id,'https://fixture.invalid/filled','{"occupation":"Software Developer","seniority":"senior","technologies":["Go"]}'::jsonb,99,99,ARRAY[9] FROM company;
INSERT INTO taxonomy_miss(taxonomy,raw_value,sample_value) VALUES('occupation','software developer','Software Developer'),('seniority','senior','senior'),('technology','go','Go'),('occupation','unmatched','unmatched');`)
	if err != nil {
		t.Fatal(err)
	}
	occupations, err := parseRegistryCSV([]byte("slug,en,aliases\nsoftware-engineer,Software Engineer,Software Developer\n"))
	if err != nil {
		t.Fatal(err)
	}
	technologies, err := parseRegistryCSV([]byte("slug,name\ngo,Go\n"))
	if err != nil {
		t.Fatal(err)
	}
	tables := map[string]registryTable{"occupations": occupations, "technologies": technologies}
	tx, err := conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	count, err := resolveRegistryMisses(ctx, tx, tables)
	if err != nil || count != 3 {
		t.Fatalf("resolve %d %v", count, err)
	}
	var visible int
	if err = observer.QueryRow(ctx, "SELECT count(*) FROM taxonomy_miss WHERE status='resolved'").Scan(&visible); err != nil || visible != 0 {
		t.Fatalf("miss resolution escaped transaction: %d %v", visible, err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	var correct bool
	if err = conn.QueryRow(ctx, `SELECT occupation_id=12 AND seniority_id=8 AND technology_ids=ARRAY[9] FROM job_posting WHERE source_url='https://fixture.invalid/empty'`).Scan(&correct); err != nil || !correct {
		t.Fatalf("missing backfill: %v %v", correct, err)
	}
	if err = conn.QueryRow(ctx, `SELECT occupation_id=99 AND seniority_id=99 AND technology_ids=ARRAY[9] FROM job_posting WHERE source_url='https://fixture.invalid/filled'`).Scan(&correct); err != nil || !correct {
		t.Fatalf("existing enrichment overwritten: %v %v", correct, err)
	}
	tx, err = conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	count, err = resolveRegistryMisses(ctx, tx, tables)
	if err != nil || count != 0 {
		t.Fatalf("repeat resolution %d %v", count, err)
	}
}
