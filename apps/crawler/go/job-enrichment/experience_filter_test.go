package enrichment

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func checkExperienceReversedFilter(t testing.TB, text string) {
	t.Helper()
	text = strings.NewReplacer("İ", "i", "ı", "i").Replace(text)
	if !experienceReversedPossible(text) && experienceRules[2].MatchString(text) {
		t.Fatal("reversed-experience filter skipped a valid match")
	}
}

func TestExperienceReversedFilterPreservesOracleMatches(t *testing.T) {
	// Fail closed if a future rule adds another leading literal.
	if !strings.HasPrefix(experiencePatterns["_EXPERIENCE_REVERSED_RE"], `(?:Erfahrung|Berufserfahrung|expérience|esperienza|experiencia|ervaring)\s+`) {
		t.Fatal("reversed-experience filter needs updated rule qualification")
	}
	data, err := os.ReadFile("testdata/python_experience.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ Text string }
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		checkExperienceReversedFilter(t, c.Text)
	}
	for _, word := range []string{"Erfahrung", "Berufserfahrung", "expérience", "esperienza", "experiencia", "ervaring", "eſperıenza", "EXPERİENCİA"} {
		checkExperienceReversedFilter(t, word+" 3 years")
	}
}

func FuzzExperienceReversedFilter(f *testing.F) {
	for _, text := range []string{"Erfahrung von mindestens 5 Jahren", "expérience d'au moins 3 ans", "eſperıenza di almeno 2 anni", "EXPERİENCİA de al menos 6 años", "Experience with 2026 systems, 5 years of engineering"} {
		f.Add(text)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if len(text) > 8192 {
			t.Skip()
		}
		checkExperienceReversedFilter(t, text)
	})
}
