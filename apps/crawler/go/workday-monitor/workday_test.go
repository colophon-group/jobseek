package workday

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestSelectedCohortSizeUsesSixteenBoundedPages(t *testing.T) {
	var offsets []int
	post := func(_ context.Context, _ string, body []byte) ([]byte, error) {
		var request listRequest
		if err := json.Unmarshal(body, &request); err != nil {
			return nil, err
		}
		if request.Limit != 20 {
			t.Fatalf("limit = %d", request.Limit)
		}
		offsets = append(offsets, request.Offset)
		count := 305 - request.Offset
		if count > 20 {
			count = 20
		}
		paths := make([]string, count)
		for i := range paths {
			paths[i] = fmt.Sprintf("/job_%03d", request.Offset+i)
		}
		return page(305, paths...), nil
	}
	got, err := DiscoverSingleSite(context.Background(), Site{"example", "wd5", "External"}, post)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.URLs) != 305 || got.Requests != 16 || got.Recovered {
		t.Fatalf("result = %+v", got)
	}
	for i, offset := range offsets {
		if want := i * 20; offset != want {
			t.Fatalf("offset %d = %d, want %d", i, offset, want)
		}
	}
}

func page(total int, paths ...string) []byte {
	items := make([]string, 0, len(paths))
	for _, path := range paths {
		items = append(items, fmt.Sprintf(`{"externalPath":%q}`, path))
	}
	return []byte(fmt.Sprintf(`{"total":%d,"jobPostings":[%s]}`, total, strings.Join(items, ",")))
}

func TestSingleSitePaginationPreservesRequestAndURLShape(t *testing.T) {
	site := Site{Company: "example", Instance: "wd5", Name: "External"}
	var bodies []string
	post := func(_ context.Context, url string, body []byte) ([]byte, error) {
		if want := "https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/jobs"; url != want {
			t.Fatalf("list URL = %q, want %q", url, want)
		}
		bodies = append(bodies, string(body))
		if len(bodies) == 1 {
			return page(3, "/a", "/b"), nil
		}
		return page(3, "/c"), nil
	}
	got, err := DiscoverSingleSite(context.Background(), site, post)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{`{"limit":20,"offset":0}`, `{"limit":20,"offset":2}`}; fmt.Sprint(bodies) != fmt.Sprint(want) {
		t.Fatalf("request bodies = %v, want %v", bodies, want)
	}
	if want := []string{
		"https://example.wd5.myworkdayjobs.com/External/a",
		"https://example.wd5.myworkdayjobs.com/External/b",
		"https://example.wd5.myworkdayjobs.com/External/c",
	}; fmt.Sprint(got.URLs) != fmt.Sprint(want) {
		t.Fatalf("URLs = %v, want %v", got.URLs, want)
	}
	if got.Requests != 2 || got.Advertised != 3 || got.Recovered {
		t.Fatalf("unexpected result metadata: %+v", got)
	}
}

func TestSingleSiteReconcilesChurnOnceAndFailsClosed(t *testing.T) {
	site := Site{Company: "example", Instance: "wd5", Name: "External"}
	call := 0
	post := func(_ context.Context, _ string, _ []byte) ([]byte, error) {
		call++
		switch call {
		case 1:
			return page(6, "/a", "/b", "/c"), nil
		case 2:
			return page(6, "/c", "/d", "/c"), nil
		case 3:
			return page(6, "/a", "/b", "/c"), nil
		case 4:
			return page(6, "/d", "/e", "/f"), nil
		default:
			t.Fatal("unexpected extra source request")
			return nil, nil
		}
	}
	got, err := DiscoverSingleSite(context.Background(), site, post)
	if err != nil {
		t.Fatal(err)
	}
	if !got.Recovered || len(got.URLs) != 6 || got.Requests != 4 {
		t.Fatalf("expected one reconciled inventory, got %+v", got)
	}

	post = func(_ context.Context, _ string, _ []byte) ([]byte, error) {
		return page(5, "/a"), nil
	}
	if _, err := DiscoverSingleSite(context.Background(), site, post); err == nil {
		t.Fatal("materially incomplete inventory was accepted")
	}
}

func TestSingleSiteCapIsExplicitlyUnsupported(t *testing.T) {
	calls := 0
	post := func(_ context.Context, _ string, _ []byte) ([]byte, error) {
		calls++
		return page(2000, "/a"), nil
	}
	_, err := DiscoverSingleSite(context.Background(), Site{"example", "wd5", "External"}, post)
	if !errors.Is(err, ErrUnsupportedInventory) || calls != 1 {
		t.Fatalf("cap result: err=%v calls=%d", err, calls)
	}
}
