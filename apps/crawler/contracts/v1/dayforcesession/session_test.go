package dayforcesession

import (
	"encoding/json"
	"strings"
	"testing"
)

func requestFixture() Request {
	return Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), TargetURL: "https://jobs.dayforcehcm.com/en-US/fixture/EXTERNAL", Tenant: "fixture", Portal: "EXTERNAL", ExpectedSite: Site{JobBoardID: 1, Culture: "en-US", Cultures: []string{"en-US"}}, TimeoutMS: 600000}
}

func TestClosedRequestIdentityAndUnambiguousEnvelopes(t *testing.T) {
	r := requestFixture()
	raw, _ := json.Marshal(r)
	var got Request
	if Decode(raw, RequestLimit, &got) != nil || !got.Valid() {
		t.Fatal("valid request rejected")
	}
	for _, value := range []string{string(raw) + "{}", strings.Replace(string(raw), `"tenant":"fixture"`, `"tenant":"fixture","tenant":"foreign"`, 1), strings.Replace(string(raw), `"tenant":"fixture"`, `"tenant":"fixture","script":"fetch('foreign')"`, 1), strings.Replace(string(raw), `"protocol":`, `"Protocol":`, 1)} {
		if Decode([]byte(value), RequestLimit, &got) == nil {
			t.Fatal("ambiguous request accepted")
		}
	}
	for _, u := range []string{"http://jobs.dayforcehcm.com/fixture/EXTERNAL", "https://foreign.example/fixture/EXTERNAL", "https://jobs.dayforcehcm.com:443/fixture/EXTERNAL", "https://jobs.dayforcehcm.com/other/EXTERNAL", "https://jobs.dayforcehcm.com/fixture/other", "https://jobs.dayforcehcm.com/fixture/EXTERNAL#x", "https://jobs.dayforcehcm.com/fixture/EXTERNAL?x=1", "https://jobs.dayforcehcm.com/%66ixture/EXTERNAL"} {
		bad := r
		bad.TargetURL = u
		if bad.Valid() {
			t.Fatal("foreign/noncanonical target accepted")
		}
	}
}

func TestBoundedCommandsHaveExactlyOneVariant(t *testing.T) {
	zero := 0
	finish := true
	for _, c := range []Command{{}, {Sequence: 1}, {Sequence: 1, Offset: &zero, Finish: &finish}, {Offset: &zero}} {
		if c.Valid() {
			t.Fatal("invalid command accepted")
		}
	}
	if !(Command{Sequence: 1, Offset: &zero}).Valid() || !(Command{Sequence: 2, Finish: &finish}).Valid() {
		t.Fatal("valid variant rejected")
	}
	for _, raw := range []string{`{"sequence":1,"offset":0,"offset":25}`, `{"sequence":1,"offset":0,"headers":{"x-csrf-token":"private"}}`, `{"sequence":1,"offset":0} false`} {
		var c Command
		if Decode([]byte(raw), CommandLimit, &c) == nil {
			t.Fatal("injected command accepted")
		}
	}
}
