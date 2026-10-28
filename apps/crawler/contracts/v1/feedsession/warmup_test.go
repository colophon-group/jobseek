package feedsession

import (
	"strings"
	"testing"
)

func TestWarmupIsBoundToFeedOrigin(t *testing.T) {
	r := Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), FeedURL: "https://jobs.example/feed", Start: 1, Increment: 1, MaxPages: 1, Wait: "load", NavigationTimeoutMS: 30000, TimeoutMS: 60000, WarmupURL: "https://jobs.example/careers"}
	if !r.Valid() {
		t.Fatal("same-origin warmup refused")
	}
	for _, raw := range []string{"https://foreign.example/", "http://jobs.example/", "https://user@jobs.example/", "https://jobs.example/#fragment", "https://jobs.example/\n", "https://jobs.example/" + strings.Repeat("x", 8192)} {
		r.WarmupURL = raw
		if r.Valid() {
			t.Fatal("unbound warmup admitted")
		}
	}
}
