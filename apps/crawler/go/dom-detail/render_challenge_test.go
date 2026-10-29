package dom

import "testing"

func TestCurrentChallengeDocumentsRequireInterstitialMarkers(t *testing.T) {
	for _, tc := range []struct {
		name, source, want string
	}{
		{"cloudflare", `<title>Quick Check Needed</title><script src="/cdn-cgi/challenge-platform/check.js"></script>`, "challenge"},
		{"security-iframe", `<title>Challenge Validation</title><iframe id="sec-cpt-if"></iframe><input name="verify-url">`, "challenge"},
		{"title-alone", `<title>Challenge Validation</title><a href="/jobs/1">Engineer</a>`, "okay"},
		{"iframe-with-job-content", `<title>Careers</title><iframe id="sec-cpt-if"></iframe><input name="verify-url"><a href="/jobs/1">Engineer</a>`, "okay"},
		{"ordinary-empty", `<title>Careers</title><p>No vacancies</p>`, "okay"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ClassifyDocument(tc.source, Object{}, "https://example.test/jobs")
			if err != nil || got["classification"] != tc.want {
				t.Fatal("interstitial classification differs", got, err)
			}
		})
	}
}
