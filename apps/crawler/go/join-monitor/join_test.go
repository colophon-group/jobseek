package join

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestFrozenPythonPages(t *testing.T) {
	body, err := os.ReadFile("testdata/python_pages.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name          string `json:"name"`
		HTML          string `json:"html"`
		First         bool   `json:"first"`
		Fill          string `json:"fill"`
		Prefix        int    `json:"prefix"`
		Suffix        int    `json:"suffix"`
		Expected      Page   `json:"expected"`
		ExpectedError bool   `json:"expected_error"`
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			html := strings.Repeat(tc.Fill, tc.Prefix) + tc.HTML + strings.Repeat(tc.Fill, tc.Suffix)
			page, err := ParsePage([]byte(html), "acme", tc.First)
			if tc.ExpectedError {
				if err == nil {
					t.Fatal("accepted a Python parser failure")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			urls, err := UniqueSorted([]Page{page})
			if err != nil || page.PageCount != tc.Expected.PageCount || !reflect.DeepEqual(urls, tc.Expected.URLs) {
				t.Fatalf("got %#v, want %#v: %v", Page{URLs: urls, PageCount: page.PageCount}, tc.Expected, err)
			}
		})
	}
}

func samplePage(items, count string) []byte {
	return []byte(`<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"initialState":{"jobs":{"items":` + items + `,"pagination":{"pageCount":` + count + `}}}}}}</script></html>`)
}

func TestParsePageAndUniqueSorted(t *testing.T) {
	page, err := ParsePage(samplePage(`[{"idParam":"1-engineer"},{"idParam":"2-designer"},{"other":"skip"}]`, "2"), "acme", true)
	if err != nil {
		t.Fatal(err)
	}
	if page.PageCount != 2 || len(page.URLs) != 2 || page.URLs[0] != "https://join.com/companies/acme/1-engineer" {
		t.Fatalf("unexpected page: %#v", page)
	}
	urls, err := UniqueSorted([]Page{page, {URLs: []string{page.URLs[0], "https://join.com/companies/acme/3-other"}}})
	if err != nil || len(urls) != 3 || urls[2] != "https://join.com/companies/acme/3-other" {
		t.Fatalf("unexpected unique URLs: %#v, %v", urls, err)
	}
}

func TestParsePageFailsClosedOnIncompleteInventory(t *testing.T) {
	for _, body := range [][]byte{
		[]byte("<html>No script</html>"),
		samplePage(`[]`, "2"),
		samplePage(`[{"idParam":"one"}]`, `"not-a-count"`),
	} {
		if _, err := ParsePage(body, "acme", true); err == nil {
			t.Fatalf("expected error for %s", body)
		}
	}
	if _, err := ParsePage(samplePage(`[]`, "2"), "acme", false); err == nil {
		t.Fatal("empty required page must fail")
	}
}

func TestBoardURLAndPageURL(t *testing.T) {
	for _, raw := range []string{
		"http://join.com/companies/acme",
		"https://other.example/companies/acme",
		"https://join.com/companies/other",
		"https://join.com/companies/acme?page=3",
	} {
		if err := ValidateBoard(raw, "acme"); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	board := "https://www.join.com/companies/acme/"
	if err := ValidateBoard(board, "acme"); err != nil {
		t.Fatal(err)
	}
	page, err := PageURL(board, 2)
	if err != nil || !strings.HasSuffix(page, "/?page=2") {
		t.Fatalf("unexpected page URL %s: %v", page, err)
	}
}
