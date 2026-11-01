package apisniffer

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

const nycPeopleSoftBoard = "https://careers.nychhc.org/psc/hrtam/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL?Action=U&Page=HRS_APP_SCHJOB_FL"

func partitionPage(ids []string, total int, unit string, changedDuplicate bool) string {
	rows := ""
	for index, id := range ids {
		title := "Nurse"
		if changedDuplicate && index > 0 {
			title = "Changed"
		}
		rows += fmt.Sprintf(`<li id="HRS_AGNT_RSLT_I$0_row_%d">
<span id="SCH_JOB_TITLE$%d">%s</span>
<span id="HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID$%d">%s</span>
<span id="LOCATION$%d">Queens</span>
<span id="HRS_BU_DESCR$%d">%s</span>
<span id="HRS_CLS_DT_DESCR$%d">12/31/2026</span></li>`, index, index, title, index, id, index, index, unit, index)
	}
	return fmt.Sprintf(`<html><body><h2>Search Results List</h2><b>%d</b> jobs found
<form><input name="ICStateNum" value="1" />
<div class="facet_group_box"><h2>Business Unit</h2>
<input type="checkbox" id="PTS_SELECT$5" name="PTS_SELECT$5" aria-label="Apply ELMHURST (2) filter" />
<input type="checkbox" id="PTS_SELECT$6" name="PTS_SELECT$6" aria-label="Apply WOODHULL (2) filter" /></div>
<div id="HRS_AGNT_RSLT_I">%s</div></form></body></html>`, total, rows)
}

func TestPeopleSoftBusinessUnitFacetSourceAndDuplicateContract(t *testing.T) {
	o, err := LastHTTPOptionsFromMetadata("peoplesoft", nycPeopleSoftBoard, `{"partition_by_business_unit":true,"allow_identical_duplicate_ids":["2001"]}`)
	if err != nil || !o.PartitionByBusinessUnit {
		t.Fatal("NYC opt-in was rejected", err)
	}
	if _, err := LastHTTPOptionsFromMetadata("peoplesoft", nycPeopleSoftBoard, `{"partition_by_business_unit":"true"}`); err == nil {
		t.Fatal("non-boolean opt-in accepted")
	}
	if _, err := LastHTTPOptionsFromMetadata("peoplesoft", strings.Replace(nycPeopleSoftBoard, "careers.nychhc.org", "jobs.example.com", 1), `{"partition_by_business_unit":true,"allow_identical_duplicate_ids":["2001"]}`); err == nil {
		t.Fatal("unqualified origin accepted")
	}
	page := partitionPage([]string{"1001"}, 4, "ELMHURST", false)
	facets, err := peopleSoftBusinessUnitFacets(page, 4)
	if err != nil || len(facets) != 2 || facets[0].Name != "ELMHURST" || facets[1].Count != 2 {
		t.Fatal("source facet rejected", err, facets)
	}
	form, err := peopleSoftFacetForm(page, facets[0].Action)
	if err != nil || !strings.Contains(form, "ICAction=PTS_SELECT%245") || !strings.Contains(form, "PTS_SELECT%245=Y") {
		t.Fatal("facet action was not source-backed", err, form)
	}
	if _, err := peopleSoftBusinessUnitFacets(page, 5); err == nil {
		t.Fatal("facets did not reconcile with system total")
	}
	if _, err := peopleSoftBusinessUnitFacets(strings.Replace(page, "WOODHULL (2)", "WOODHULL (500)", 1), 502); err == nil {
		t.Fatal("500-row ceiling was accepted")
	}
	duplicate := partitionPage([]string{"2001", "2001"}, 2, "WOODHULL", false)
	if _, _, err := ParsePeopleSoftListing(duplicate, o); err == nil {
		t.Fatal("legacy strict parser accepted a repeated ID")
	}
	if jobs, count, err := parsePeopleSoftListing(duplicate, o, true); err != nil || len(jobs) != 2 || count != 2 {
		t.Fatal("identical source duplicate was rejected", err, len(jobs), count)
	}
	if _, _, err := parsePeopleSoftListing(partitionPage([]string{"2001", "2001"}, 2, "WOODHULL", true), o, true); err == nil {
		t.Fatal("conflicting duplicate was accepted")
	}
}

func TestPeopleSoftBusinessUnitDiscoveryAccountsRawAndUnique(t *testing.T) {
	o, err := LastHTTPOptionsFromMetadata("peoplesoft", nycPeopleSoftBoard, `{"partition_by_business_unit":true,"allow_identical_duplicate_ids":["2001"]}`)
	if err != nil {
		t.Fatal(err)
	}
	bootstraps := 0
	jobs, err := DiscoverPeopleSoft(context.Background(), o, func(_ context.Context, request Request) ([]byte, error) {
		switch {
		case request.Method == "GET" && strings.Contains(request.URL, "/psp/"):
			bootstraps++
			return []byte("new anonymous session"), nil
		case request.Method == "GET" && request.URL == o.ListingURL():
			return []byte(partitionPage([]string{"1001"}, 4, "ELMHURST", false)), nil
		case request.Method == "POST" && strings.Contains(request.Body, "ICAction=PTS_SELECT%245"):
			return []byte(partitionPage([]string{"1001", "1002"}, 2, "ELMHURST", false)), nil
		case request.Method == "POST" && strings.Contains(request.Body, "ICAction=PTS_SELECT%246"):
			return []byte(partitionPage([]string{"2001", "2001"}, 2, "WOODHULL", false)), nil
		default:
			t.Fatalf("unexpected request: %+v", request)
			return nil, ErrInventory
		}
	})
	if err != nil || bootstraps != 3 || len(jobs) != 3 {
		t.Fatal("raw/unique reconciliation failed", err, bootstraps, len(jobs))
	}
	for index, id := range []string{"1001", "1002", "2001"} {
		if jobs[index].Metadata["job_id"] != id {
			t.Fatal("canonical ID changed", jobs[index].Metadata)
		}
	}
}

func TestPeopleSoftBusinessUnitDiscoveryFailsClosed(t *testing.T) {
	for _, failure := range []string{"cross_unit", "snapshot_drift", "missing_facet", "missing_tail"} {
		t.Run(failure, func(t *testing.T) {
			o, err := LastHTTPOptionsFromMetadata("peoplesoft", nycPeopleSoftBoard, `{"partition_by_business_unit":true,"allow_identical_duplicate_ids":["2001"]}`)
			if err != nil {
				t.Fatal(err)
			}
			listings := 0
			jobs, err := DiscoverPeopleSoft(context.Background(), o, func(_ context.Context, request Request) ([]byte, error) {
				switch {
				case request.Method == "GET" && strings.Contains(request.URL, "/psp/"):
					return []byte("new anonymous session"), nil
				case request.Method == "GET" && request.URL == o.ListingURL():
					listings++
					total := 4
					if failure == "snapshot_drift" && listings == 2 {
						total = 5
					}
					page := partitionPage([]string{"1001"}, total, "ELMHURST", false)
					if failure == "missing_facet" && listings == 2 {
						page = strings.Replace(page, "<h2>Business Unit</h2>", "<h2>Other facet</h2>", 1)
					}
					return []byte(page), nil
				case request.Method == "POST" && strings.Contains(request.Body, "ICAction=PTS_SELECT%245"):
					ids := []string{"1001", "1002"}
					if failure == "missing_tail" {
						ids = ids[:1]
					}
					return []byte(partitionPage(ids, 2, "ELMHURST", false)), nil
				case request.Method == "POST" && strings.Contains(request.Body, "ICAction=PTS_SELECT%246"):
					ids := []string{"2001", "2001"}
					if failure == "cross_unit" {
						ids = []string{"1001", "2001"}
					}
					return []byte(partitionPage(ids, 2, "WOODHULL", false)), nil
				default:
					t.Fatalf("unexpected request: %+v", request)
					return nil, ErrInventory
				}
			})
			if err == nil || len(jobs) != 0 {
				t.Fatal("incomplete inventory was accepted", failure, err, len(jobs))
			}
		})
	}
}
