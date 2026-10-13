package queue

import (
	"encoding/json"
	cards "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
)

const inlineCardBoardURL = "https://deutsche-technikberatung.my.site.com/dtbjobs/s/bewerberseite-zentrale"
const inlineCardSourceSHA = "d9bd6dc22fd01b311375bc770b37f73fcd4d40464a70d3cd0fdd849019222d98"

func InlineCardOptions(config map[string]string) (*cards.InlineCards, error) {
	md, err := richProfileMetadata(config)
	if err != nil || config["crawler_type"] != "inline" || config["monitor_needs_browser"] != "1" || config["board_url"] != inlineCardBoardURL || !sourceMetadataMatches(md, inlineCardSourceSHA) {
		return nil, ErrUnsupportedProfile
	}
	c := &cards.InlineCards{ControlSelector: `span[part="formatted-rich-text"] u`, ContentSelector: `flowruntime-flow span[part="formatted-rich-text"]`, ContentIndex: 1, IdentitySelector: `label[for^="a7u"]`, IdentityAttribute: "for", IdentityPattern: `^(a7u[A-Za-z0-9]{15})-.+$`, Maximum: 500, TimeoutMS: 300000}
	if !c.Valid() {
		return nil, ErrUnsupportedProfile
	}
	return c, nil
}

func inlineCardParserClone(config map[string]string, md map[string]json.RawMessage) bool {
	_, err := InlineCardOptions(config)
	if err != nil {
		return false
	}
	for _, k := range []string{"detail_click_selector", "detail_content_selector", "detail_identity_selector", "detail_identity_attribute", "detail_identity_regex"} {
		delete(md, k)
	}
	md["item_boundary_tag"] = json.RawMessage(`"jobseek-inline-detail"`)
	return true
}
