package documentactions

import (
	"strings"
	"testing"
)

func TestDocumentWarmupIsBoundToDetailOrigin(t *testing.T) {
	target := "https://www.caterer.com/job/role/l-oscar-london-job123"
	for _, warmup := range []string{
		"http://www.caterer.com/jobs/loscar-london",
		"https://caterer.com/jobs/loscar-london",
		"https://www.caterer.com.evil.test/jobs/loscar-london",
		"https://user:secret@www.caterer.com/jobs/loscar-london",
		"https://www.caterer.com/jobs/loscar-london#fragment",
		"https://www.caterer.com/jobs/loscar-london\n",
		"https://www.caterer.com:443/jobs/loscar-london",
	} {
		if SameOriginWarmup(target, warmup) {
			t.Fatalf("unbound warmup admitted: %q", warmup)
		}
	}
	good := "https://www.caterer.com/jobs/loscar-london"
	if !SameOriginWarmup(target, good) {
		t.Fatal("same-origin warmup rejected")
	}
	r := Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: []byte("plan"), LargeDocument: true, WarmupURL: good}
	if !r.Valid() {
		t.Fatal("bounded document warmup rejected")
	}
	r.LargeDocument = false
	if r.Valid() {
		t.Fatal("ordinary document action session admitted warmup")
	}
}
