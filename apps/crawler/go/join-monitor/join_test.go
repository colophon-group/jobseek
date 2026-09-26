package join

import (
	"strings"
	"testing"
)

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
