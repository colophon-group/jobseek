package oraclehcm

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestCopiedRequisitionIDsRetainInventoryAndDetailIdentity(t *testing.T) {
	o := testOptions(t, nil)
	ids := []string{"123", "EXL_RB_1(2)", "EXL_RB_3(2)"}
	rows := []any{}
	for _, id := range ids {
		rows = append(rows, map[string]any{"Id": id, "Title": "Engineer", "PrimaryLocation": "Zurich"})
	}
	body, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"TotalJobsCount": len(rows), "requisitionList": rows}}})
	calls := 0
	got, err := Discover(context.Background(), o, func(_ context.Context, endpoint string) ([]byte, error) {
		calls++
		if endpoint != o.Endpoint() {
			t.Fatal("copy IDs changed inventory authority")
		}
		return body, nil
	})
	if err != nil || got.Truncated || calls != 1 || len(got.Jobs) != len(ids) {
		t.Fatal("copy IDs truncated a complete inventory", got, err, calls)
	}
	for n, id := range ids {
		if got.Jobs[n].URL != o.JobURL(id) {
			t.Fatal("public requisition ID was rewritten")
		}
		for _, path := range []string{"/job/", "/requisitions/preview/"} {
			source := "https://" + o.Host + "/hcmUI/CandidateExperience/en/sites/" + o.Site + path + id
			host, site, parsedID, ok := Candidate(source, true)
			if !ok || host != o.Host || site != o.Site || parsedID != id {
				t.Fatal("public copy URL lost identity", id)
			}
			endpoint, err := DetailEndpoint(source, nil)
			if err != nil || !strings.Contains(endpoint, `ById;Id="`+id+`",siteNumber=`+o.Site) {
				t.Fatal("detail finder lost full copy ID", id, err)
			}
		}
		endpoint, err := DetailEndpoint("https://careers.example.com/jobs/"+id, map[string]any{"host": o.Host, "site": o.Site})
		if err != nil || !strings.Contains(endpoint, `ById;Id="`+id+`",siteNumber=`+o.Site) {
			t.Fatal("configured vanity copy lost trusted tenant/id", err)
		}
	}
}

func TestCopyIDsDoNotAdmitEncodedAliasesOrFinderInjection(t *testing.T) {
	o := testOptions(t, nil)
	for _, id := range []string{"EXL_RB_1%282%29", "EXL%2FRB_1(2)", `EXL_RB_1(2)",siteNumber=CX_2`, "EXL_RB_1(2)/extra", "EXL_RB_1(2);other", strings.Repeat("x", 129)} {
		source := o.JobURL(id)
		if _, _, _, ok := Candidate(source, true); ok {
			t.Fatal("unsafe public copy spelling accepted", id)
		}
		if _, err := DetailEndpoint(source, nil); !errors.Is(err, ErrOptions) {
			t.Fatal("unsafe detail finder admitted", id, err)
		}
		if _, err := DetailEndpoint("https://careers.example.com/jobs/"+id, map[string]any{"host": o.Host, "site": o.Site}); !errors.Is(err, ErrOptions) {
			t.Fatal("unsafe configured finder admitted", id, err)
		}
	}
}
