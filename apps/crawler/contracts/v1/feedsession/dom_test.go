package feedsession

import "testing"

func TestDOMTraversalKeepsOriginalRootAndQuerySequence(t *testing.T) {
	for _, tc := range []struct {
		name, root       string
		start, increment int
		want             string
	}{
		{"ordinary", "https://example.com/jobs?lang=de&empty=&lang=en", 1, 1, "https://example.com/jobs?lang=de&lang=en&page=2"},
		{"negative-start", "https://example.com/jobs?page=-1&lang=de", -1, 1, "https://example.com/jobs?page=0&lang=de"},
		{"offset", "https://example.com/jobs?lang=de", 0, 20, "https://example.com/jobs?lang=de&page=20"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := DOMTraversal{Parameter: "page", PageStart: tc.start, Increment: tc.increment, Attempts: 3}
			root, e := d.PageURL(tc.root, 1)
			if e != nil || root != tc.root {
				t.Fatal("root changed", e)
			}
			tail, e := d.PageURL(tc.root, 2)
			if e != nil || tail != tc.want {
				t.Fatal("query order/value changed", tail, e)
			}
		})
	}
}

func TestDOMTraversalDoesNotLoosenExistingFeedWire(t *testing.T) {
	r := Request{Protocol: Protocol, RequestID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ConfigFingerprint: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", FeedURL: "https://example.com/jobs", Start: 1, Increment: 1, MaxPages: 100, Wait: "domcontentloaded", NavigationTimeoutMS: 30000, TimeoutMS: 600000}
	if r.Valid() {
		t.Fatal("feed acquired unconfigured pagination")
	}
	r.DOM = &DOMTraversal{Parameter: "pr", PageStart: -1, Increment: 1, Attempts: 3}
	if !r.Valid() {
		t.Fatal("valid DOM traversal refused")
	}
	for _, change := range []func(*Request){func(r *Request) { r.Start = 0 }, func(r *Request) { r.Increment = 2 }, func(r *Request) { r.PageParameter = "page" }, func(r *Request) { r.MaxPages = 10001 }, func(r *Request) { r.DOM.Attempts = 6 }, func(r *Request) { r.DOM.PageStart = -2 }, func(r *Request) { r.DOM.Parameter = "page\n" }} {
		c := r
		d := *r.DOM
		c.DOM = &d
		change(&c)
		if c.Valid() {
			t.Fatal("invalid DOM traversal accepted")
		}
	}
}
