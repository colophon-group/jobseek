package main

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

func TestCompanyPublishingNeverPrunesUnprovenAuthority(t *testing.T) {
	for _, kind := range []string{"empty", "duplicate", "rejected_import", "missing", "count_budget", "percentage_budget", "delete_failure", "failed_convergence", "success"} {
		t.Run(kind, func(t *testing.T) {
			docs := []map[string]any{}
			remote := map[string]bool{}
			for i := 0; i < 200; i++ {
				id := fmt.Sprint(i)
				docs = append(docs, map[string]any{"id": id})
				remote[id] = true
			}
			remote["stale"] = true
			imports, censuses, deletes := 0, 0, 0
			publisher := taxonomyPublisher{
				Import: func(context.Context, string, []map[string]any) error {
					imports++
					if kind == "rejected_import" {
						return errors.New("unacknowledged")
					}
					return nil
				},
				CompanyIDs: func(context.Context) (map[string]bool, error) { censuses++; return remote, nil },
				DeleteCompany: func(_ context.Context, id string) error {
					deletes++
					if kind == "delete_failure" {
						return errors.New("failed")
					}
					if kind != "failed_convergence" {
						delete(remote, id)
					}
					return nil
				},
			}
			switch kind {
			case "empty":
				docs = nil
			case "duplicate":
				docs = append(docs, docs[0])
			case "missing":
				delete(remote, "0")
			case "count_budget":
				for i := 0; i < 6000; i++ {
					id := fmt.Sprint(i)
					remote[id] = true
					if i >= 200 {
						docs = append(docs, map[string]any{"id": id})
					}
				}
				for i := 0; i < 51; i++ {
					remote[fmt.Sprintf("stale-%d", i)] = true
				}
			case "percentage_budget":
				remote["stale-2"] = true
				remote["stale-3"] = true
			}
			deleted, err := publisher.companies(context.Background(), docs)
			if kind == "success" {
				if err != nil || deleted != 1 || censuses != 2 || deletes != 1 {
					t.Fatalf("convergence failed: %d %d %d %v", deleted, censuses, deletes, err)
				}
				return
			}
			if err == nil {
				t.Fatal("unproven authority accepted")
			}
			if kind != "delete_failure" && kind != "failed_convergence" && deletes != 0 {
				t.Fatal("pruned unproven authority")
			}
			if (kind == "empty" || kind == "duplicate") && imports != 0 {
				t.Fatal("wrote invalid authority")
			}
			if kind == "rejected_import" && censuses != 0 {
				t.Fatal("continued after rejected acknowledgement")
			}
		})
	}
}
func TestTaxonomyImportsAreBounded(t *testing.T) {
	docs := make([]map[string]any, 2501)
	batches := []int{}
	publisher := taxonomyPublisher{Import: func(_ context.Context, collection string, docs []map[string]any) error {
		if collection != "technology" {
			t.Fatal("wrong collection")
		}
		batches = append(batches, len(docs))
		return nil
	}}
	if err := publisher.upsert(context.Background(), "technology", docs); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(batches) != "[1000 1000 501]" {
		t.Fatalf("unbounded import %v", batches)
	}
}
