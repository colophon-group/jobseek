package apisniffer

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

func TestConfiguredGymnasticsPDFOwnershipAndTitleLayouts(t *testing.T) {
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	var monitor dom.Object
	var scraper map[string]any
	for _, row := range rows[1:] {
		if row[1] == "international-gymnastics-federation-jobs" {
			if json.Unmarshal([]byte(row[4]), &monitor) != nil || json.Unmarshal([]byte(row[6]), &scraper) != nil {
				t.Fatal("configured document policy unavailable")
			}
			break
		}
	}
	proof, err := dom.PDFListingProofOptions(monitor)
	if err != nil || proof == nil {
		t.Fatal("configured ownership unavailable", err)
	}
	options, err := PDFOptionsFromConfig(scraper)
	if err != nil {
		t.Fatal("configured title unavailable", err)
	}
	headerless := "Secretary General\nFull-time (100%) | Lausanne | Reporting to the President\nYour Mission\nWorld Gymnastics is entering a new chapter.\n"
	for _, c := range []struct {
		name, text, title string
		keep, failure     bool
	}{
		{"headquarters header", "WORLD GYMNASTICS\nAccountant\nFull-time | Lausanne\nYour Mission", "Accountant", true, false},
		{"headerless headquarters", headerless, "Secretary General", true, false},
		{"Luxembourg member", "Luxembourg Gymnastics Federation\nNational Coach", "", false, false},
		{"Solothurn member", "Kunstturner -Vereinigung des Kantons Solothurn\nHead Coach", "", false, false},
		{"unknown employer", strings.ReplaceAll(headerless, "World Gymnastics is", "Our federation is"), "", false, true},
		{"different location", strings.ReplaceAll(headerless, "Lausanne", "Another location"), "", false, true},
		{"ambiguous employer", headerless + "Luxembourg Gymnastics Federation", "", false, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			keep, err := proof.OwnershipKeeps(c.text)
			if (err != nil) != c.failure || err == nil && keep != c.keep {
				t.Fatal("configured ownership differs", keep, err)
			}
			if !c.keep {
				return
			}
			fields, err := ParsePDFText(c.text, "https://www.gymnastics.sport/publicdir/opportunities/files/fixture.pdf", options)
			if err != nil || fields["title"] != c.title || !reflect.DeepEqual(fields["locations"], []any{"Lausanne"}) {
				t.Fatal("qualified document title/location differs", fields, err)
			}
		})
	}
}
