package queue

import (
	"reflect"
	"testing"
)

func TestPersonioRichProfilePreservesLanguagesAndOriginalBinding(t *testing.T) {
	config := profileConfig()
	config["crawler_type"], config["board_url"] = "personio", "https://Fixture.jobs.personio.com/"
	config["metadata"] = `{"scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.Provider != "personio" || p.Profile != "personio.xml-skip/v1" || p.Endpoint != "https://fixture.jobs.personio.com/xml?language=en" || !reflect.DeepEqual(p.BackfillLanguages, []string{"de"}) {
		t.Fatal("Personio inferred source/default languages differ", p, err)
	}
	config["metadata"] = `{"slug":"explicit","language":"de","backfill_languages":["fr","en"],"scraper_type":"skip"}`
	explicit, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || explicit.Token != "explicit" || explicit.Language != "de" || !reflect.DeepEqual(explicit.BackfillLanguages, []string{"fr", "en"}) || explicit.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("Personio explicit configuration lost source/order/binding", explicit, err)
	}
	for _, backfill := range []string{"null", "[]"} {
		config["metadata"] = `{"scraper_type":"skip","backfill_languages":` + backfill + `}`
		p, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || backfill == "null" && len(p.BackfillLanguages) != 1 || backfill == "[]" && len(p.BackfillLanguages) != 0 {
			t.Fatal("null/default and disabled backfill conflated", p, err)
		}
	}
	for _, md := range []string{
		`{"scraper_type":"skip","language":null}`,
		`{"scraper_type":"skip","language":"EN"}`,
		`{"scraper_type":"skip","backfill_languages":["de","de"]}`,
		`{"scraper_type":"skip","backfill_languages":["de","invalid"]}`,
		`{"scraper_type":"skip","slug":"bad/slash"}`,
		`{"scraper_type":"skip","scraper_config":{"enrich":["title"]}}`,
		`{"scraper_type":"skip","job_filter":{"title":"Engineer"}}`,
	} {
		config["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unimplemented Personio configuration admitted", md)
		}
	}
	config["metadata"] = `{"scraper_type":"skip","slug":"fixture"}`
	config["board_url"] = "https://fixture.jobs.personio.io/"
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("unsupported preferred Personio domain admitted")
	}
}
