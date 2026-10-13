package apisniffer

import "testing"

func TestSessionProviderScopeBindsIdentityAndRejectsUnportedControls(t *testing.T) {
	board := "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=6007"
	metadata := `{"partner_id":"25416","site_id":"6007","scraper_type":"skip"}`
	options, listing, e := NativeBrowserOptions("brassring", board, metadata)
	if e != nil || listing != board || options.Inventory.Endpoint != "https://sjobs.brassring.com/TGnewUI/Search/Ajax/MatchedJobs" {
		t.Fatal(e)
	}
	for _, bad := range []string{`{"partner_id":"1"}`, `{"site_id":"1"}`, `{"proxy":true}`, `{"stealth":true}`, `{"persistent_context":true}`, `{"channel":"chrome"}`, `{"actions":[]}`, `{"api_url":"https://elsewhere.test"}`} {
		if _, _, e := NativeBrowserOptions("brassring", board, bad); e == nil {
			t.Fatal("unproved control/identity admitted")
		}
	}
	for _, source := range []string{"https://sjobs.brassring.com/TGnewUI/Search/Ajax/MatchedJobs", "https://sjobs.brassring.com/TGnewUI/Search/Ajax/ProcessSortAndShowMoreJobs"} {
		if !BrassRingAjaxMatches(board, source, "POST") || BrassRingAjaxMatches(board, source, "GET") {
			t.Fatal("fixed method/path scope")
		}
	}
	for _, source := range []string{"https://other.brassring.com/TGnewUI/Search/Ajax/MatchedJobs", "https://sjobs.brassring.com/private", "https://user:password@sjobs.brassring.com/TGnewUI/Search/Ajax/MatchedJobs"} {
		if BrassRingAjaxMatches(board, source, "POST") {
			t.Fatal("foreign capture scope")
		}
	}
	if NativeBrowserRequestMatches("brassring", board, metadata, Request{Method: "POST", URL: options.Inventory.Endpoint}) {
		t.Fatal("UI session acquired arbitrary replay authority")
	}
	if !SessionListingMatches("brassring", board, board) || SessionListingMatches("brassring", board, board+"&jobid=1") {
		t.Fatal("listing/detail navigation ambiguity")
	}
}

func TestNjoynSessionLimitsPreserveOriginalSettingAndBounds(t *testing.T) {
	pages, attempts, wait, navigation, e := NjoynSessionLimits(`{"max_pages":100,"snapshot_attempts":8,"page_wait_ms":-1,"page_change_timeout_ms":80000}`)
	if e != nil || pages != 100 || attempts != 3 || wait != 0 || navigation != 60000 {
		t.Fatal("original setting/bounds", e)
	}
	if _, _, _, _, e := NjoynSessionLimits(`{"max_pages":0}`); e == nil {
		t.Fatal("invalid page limit became successful default")
	}
}
