package feedsession

import (
	"strings"
	"testing"
)

func validFixture() Request {
	return Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), FeedURL: "https://example.com/feed?x=first&x=second&blank=", PageParameter: "page", Start: 2, Increment: 2, MaxPages: 3, Wait: "domcontentloaded", NavigationTimeoutMS: 30000, TimeoutMS: 600000}
}
func TestFeedRequestRejectsUnsupportedRoutesAndBounds(t *testing.T) {
	for name, change := range map[string]func(*Request){"credentials": func(r *Request) { r.FeedURL = "https://user:password@example.com/feed" }, "http": func(r *Request) { r.FeedURL = "http://example.com/feed" }, "fragment": func(r *Request) { r.FeedURL += "#fragment" }, "zero_start": func(r *Request) { r.Start = 0 }, "zero_increment": func(r *Request) { r.Increment = 0 }, "zero_pages": func(r *Request) { r.MaxPages = 0 }, "too_many_pages": func(r *Request) { r.MaxPages = 50002 }, "wrong_wait": func(r *Request) { r.Wait = "selector" }, "too_long": func(r *Request) { r.TimeoutMS = MaxDurationMS + 1 }, "parameter": func(r *Request) { r.PageParameter = "x&inject" }, "unpaged_bounds": func(r *Request) { r.PageParameter = "" }} {
		t.Run(name, func(t *testing.T) {
			r := validFixture()
			change(&r)
			if r.Valid() {
				t.Fatal("invalid request accepted")
			}
		})
	}
}
func TestFeedPageIdentityAndOrderedQuery(t *testing.T) {
	r := validFixture()
	if !r.Valid() {
		t.Fatal("fixture invalid")
	}
	for _, page := range []int{2, 4, 6} {
		u, e := r.PageURL(page)
		if e != nil || !strings.HasPrefix(u, "https://example.com/feed?x=first&page=") {
			t.Fatal(u, e)
		}
	}
	for _, page := range []int{0, 1, 3, 8} {
		if _, e := r.PageURL(page); e == nil {
			t.Fatal("foreign/out-of-bounds page accepted")
		}
	}
}

func TestFeedRequestRejectsArithmeticOverflowAndWhitespace(t *testing.T) {
	for _, change := range []func(*Request){
		func(r *Request) { r.Increment = int(^uint(0) >> 1) },
		func(r *Request) { r.Start = int(^uint(0) >> 1) },
		func(r *Request) { r.FeedURL += "?q=two words" },
	} {
		r := validFixture()
		change(&r)
		if r.Valid() {
			t.Fatal("overflow or ambiguous URL accepted")
		}
	}
}
func TestFeedDecodeRejectsTrailingAndUnknownFields(t *testing.T) {
	for _, raw := range []string{`{"unknown":true}`, `{} {}`, `{`} {
		var r Request
		if Decode([]byte(raw), RequestLimit, &r) == nil {
			t.Fatal(raw)
		}
	}
}
