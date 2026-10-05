package queue

import "testing"

func TestGupySelectsExplicitOrStrictPublicTenantAndBindsEndpoint(t *testing.T) {
	for index, row := range []struct {
		url, md, want string
		accepted      bool
	}{
		{"https://fixture.gupy.io/", `{"scraper_type":"json-ld"}`, "fixture", true},
		{"https://fixture.gupy.io/jobs/123?jobBoardSource=gupy_public_page", `{"tenant":"invalid!"}`, "fixture", true},
		{"https://fixture.gupy.io:443/", `{"tenant":null}`, "fixture", true},
		{"https://fixture.gupy.io/", `{"tenant":"different"}`, "different", true},
		{"https://example.com/careers", `{"tenant":"configured"}`, "configured", true},
		{"https://www.gupy.io/", `{}`, "", false},
		{"https://fixture.gupy.io/?unreviewed=1", `{}`, "", false},
		{"https://fixture.gupy.io/jobs/123?jobBoardSource=gupy_public_page&jobBoardSource=gupy_public_page", `{}`, "", false},
		{"https://fixture.gupy.io/%6aobs/123", `{}`, "", false},
		{"https://fixture.gupy.io/", `{"tenant":"one","tenant":"two"}`, "", false},
	} {
		c := profileConfig()
		c["crawler_type"], c["domain"], c["throttle_key"] = "gupy", "gupy", "gupy"
		c["board_url"], c["metadata"] = row.url, row.md
		p, err := InspectRichMonitor(profileBoardID, c)
		if (err == nil) != row.accepted || err == nil && (p.Token != row.want || p.Endpoint != "https://"+row.want+".gupy.io/") {
			t.Fatal("Gupy tenant selection/binding differs", index, p, err)
		}
	}
}
