package apisniffer

import (
	"os"
	"strings"
	"testing"
)

func TestTaleoEnterpriseLegacyJumeirahSourceInventory(t *testing.T) {
	board, err := TaleoEnterpriseLegacyFromURL("https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en")
	if err != nil || !board.ResourceMatches(board.Listing) || board.ResourceMatches("https://foreign.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en") {
		t.Fatal("legacy board identity changed", board, err)
	}
	linked, err := TaleoEnterpriseLegacyFromURL("https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/jobsearch.ftl?lang=en&portal=321050%E2%80%A6")
	if err != nil || linked != board {
		t.Fatal("official jobsearch link did not normalize to the exact joblist", linked, err)
	}
	body, err := os.ReadFile("../../tests/fixtures/taleo_enterprise/jumeirah_listing.html")
	if err != nil {
		t.Fatal(err)
	}
	jobs, err := ParseTaleoEnterpriseLegacyListing(string(body), board)
	if err != nil || len(jobs) != 19 {
		t.Fatal("official Jumeirah 19-job listing was not complete", err, len(jobs))
	}
	if jobs[0].URL != "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/jobdetail.ftl?job=26000037&lang=en" || jobs[0].Title != "Florist - Housekeeping - Jumeirah Muscat Bay" {
		t.Fatal("canonical title/URL changed", jobs[0])
	}
	if len(jobs[0].Locations) != 1 || jobs[0].Locations[0] != "Oman-Muscat" {
		t.Fatal("official geographic location changed", jobs[0].Locations)
	}
	if len(jobs) > 0 {
		broken := strings.Replace(string(body), "19 jobs found", "20 jobs found", 1)
		if _, err := ParseTaleoEnterpriseLegacyListing(broken, board); err == nil {
			t.Fatal("incomplete multi-page source admitted as complete")
		}
	}
	for _, raw := range []string{
		"https://foreign.example/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
		"https://dubaiholding.tbe.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
		"http://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
		"https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en&next=https://evil.example",
	} {
		if _, err := TaleoEnterpriseLegacyFromURL(raw); err == nil {
			t.Fatal("untrusted legacy board admitted", raw)
		}
	}
}
