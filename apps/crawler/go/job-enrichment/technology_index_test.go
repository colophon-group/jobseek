package enrichment

import (
	"reflect"
	"strings"
	"testing"
)

// Keep the previous substring admission and literal matcher as an independent
// oracle. The index must preserve even the original lowercase prefilter's
// behavior where Python IGNORECASE and Unicode lowercase differ.
func legacyTechnologies(m *Matcher, raw string) []string {
	matched := []string{}
	if raw == "" {
		return matched
	}
	if strings.Contains(raw, "<") {
		raw = stripHTML(raw)
	}
	low, text := lower(raw), []rune(raw)
	for _, technology := range m.technologies {
		possible := len(technology.keywords) == 0
		for _, keyword := range technology.keywords {
			possible = possible || strings.Contains(low, keyword)
		}
		if possible {
			for _, alternative := range technology.alternatives {
				if literalMatch(text, []rune(alternative), technology.sensitive) {
					matched = append(matched, technology.slug)
					break
				}
			}
		}
	}
	return matched
}

func TestTechnologyIndexPreservesEveryTaxonomyAlternative(t *testing.T) {
	m, err := Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	for _, technology := range m.technologies {
		for _, alternative := range technology.alternatives {
			for _, raw := range []string{alternative, strings.ToUpper(alternative), "prefix" + alternative + "suffix", "<p>" + alternative + "</p>", "(" + alternative + "), " + alternative} {
				if got, want := m.Technologies(raw), legacyTechnologies(m, raw); !reflect.DeepEqual(got, want) {
					t.Fatalf("technology index changed %s alternative results/order", technology.slug)
				}
			}
		}
	}
}

func TestTechnologyIndexPreservesSharedAndEmptyKeywords(t *testing.T) {
	m := &Matcher{technologies: []technology{
		{keywords: []string{"go", "golang"}}, {keywords: []string{"go"}},
		{keywords: []string{""}}, {}, {keywords: []string{"\xff"}},
	}}
	m.indexTechnologyKeywords()
	for _, text := range []string{"", "golang", "ongoing", "\xffgo", "nothing"} {
		got := m.technologyCandidates(text)
		for i, technology := range m.technologies {
			want := len(technology.keywords) == 0
			for _, keyword := range technology.keywords {
				want = want || strings.Contains(text, keyword)
			}
			if got[i] != want {
				t.Fatalf("candidate %d differs from byte substring semantics", i)
			}
		}
	}
	if got := (&Matcher{}).Technologies("no loaded taxonomy"); len(got) != 0 {
		t.Fatal("empty taxonomy admitted a technology")
	}
}

func FuzzTechnologyIndex(f *testing.F) {
	m, err := Load("../../data")
	if err != nil {
		f.Fatal(err)
	}
	for _, text := range []string{"Go and go, C# C++ Node.js SQL", "<p>React &amp; Python</p>", "Gıt GİT ſQL Kotlin", "ongoing javascript_typescript JavaScript", "<script>Ruby</script><!--Rust-->"} {
		f.Add(text)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if len(text) > 8192 {
			t.Skip()
		}
		if !reflect.DeepEqual(m.Technologies(text), legacyTechnologies(m, text)) {
			t.Fatal("technology index changed ordered matches")
		}
	})
}
