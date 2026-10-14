package dom

import (
	"strings"
	"testing"
)

func TestRenderedPolicyAcceptsCompleteContractSizedDocuments(t *testing.T) {
	for _, size := range []int{1<<20 + 1, 2_000_000} {
		body := strings.Repeat(" ", size-len("<h1>Engineer</h1>")) + "<h1>Engineer</h1>"
		classification, err := ClassifyRendered(body, Object{}, "https://jobs.example.net/job/42")
		if err != nil || classification["classification"] != "okay" {
			t.Fatal("complete renderer document rejected", size, err)
		}
		body = strings.Repeat(" ", size-len("<title>Just a moment")) + "<title>Just a moment"
		classification, err = ClassifyRendered(body, Object{}, "https://jobs.example.net/job/42")
		if err != nil || classification["classification"] != "challenge" {
			t.Fatal("challenge at document tail ignored", size, err)
		}
	}
	if _, err := ClassifyRendered(strings.Repeat("x", 2_000_001), Object{}, "https://jobs.example.net/job/42"); err == nil {
		t.Fatal("oversized document accepted")
	}
}
