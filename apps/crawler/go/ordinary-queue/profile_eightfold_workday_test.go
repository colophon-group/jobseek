package queue

import "testing"

func TestEightfoldWorkdayPostingRetainsOriginalJSONLDFastPath(t *testing.T) {
	c := apiDetailConfig("eightfold")
	source := "https://caci.wd1.myworkdayjobs.com/external/job/0OL-FORT-MEADE-MD/Operational-Psychologist_331333-1"
	p, err := InspectAPIDetail(jsonldBoardID, c, source, Simple)
	if err != nil || p.SourceURL != source || p.Endpoint != source || p.Profile != eightfoldDetailProfile || p.Domain != "caci.wd1.myworkdayjobs.com" {
		t.Fatal("foreign original scraper route lost", err)
	}
	for _, bad := range []string{
		"https://caci.wd1.myworkdayjobs.com/external",
		"https://caci.wd1.myworkdayjobs.com/external/job/../escape",
		"https://caci.wd1.myworkdayjobs.com/external/job/escaped%2Fidentity",
		"https://caci.wd1.myworkdayjobs.com.attacker.invalid/external/job/Engineer_123",
		source + "?proxy=1", source + "#fragment",
	} {
		if _, err := InspectAPIDetail(jsonldBoardID, c, bad, Simple); err == nil {
			t.Fatal("noncanonical Workday route gained authority")
		}
	}
	changed := apiDetailConfig("eightfold")
	changed["metadata"] = `{"scraper_type":"eightfold","scraper_config":{"render":true}}`
	if _, err := InspectAPIDetail(jsonldBoardID, changed, source, Simple); err == nil {
		t.Fatal("unproved browser semantics admitted")
	}
}
