package queue

import "testing"

func TestRSSRetainedReceiptCannotActivateIdentityMigration(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "rss"
	c["board_url"] = "https://careers.example.com/"
	c["metadata"] = `{"preset":"teamtailor","scraper_type":"skip","_identity_migration_receipt":{"id":"old-migration","version":1,"retired_count":4}}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != "rss.teamtailor-skip/v1" || p.SnapshotSHA256 != configDigest(c) {
		t.Fatal("inert receipt rejected or lost source binding", e)
	}
	c["metadata"] = `{"preset":"teamtailor","scraper_type":"skip","_identity_migration_receipt":{"id":"old-migration","version":1,"retired_count":5}}`
	changed, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || changed.SnapshotSHA256 == p.SnapshotSHA256 || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("retained receipt changed without source binding", e)
	}
	c["metadata"] = `{"preset":"teamtailor","scraper_type":"skip","identity_migration":"old-migration","_identity_migration_receipt":{"id":"old-migration"}}`
	if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
		t.Fatal("unqualified active identity migration admitted")
	}
}
