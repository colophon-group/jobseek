package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// Complete public HTTP inventories match these six original browser-pagination
// configurations. Keep the original config, browser lane and board identity;
// only the inventory parser receives a static-pagination clone.
var qualifiedDOMHTTPPaginationSources = map[string]string{
	"bedd610b4c889facea980c9de65abbf1347a0f15c511de958c99adda336787d1": "667306cac8a8b8e09a8fb12c568c527bc8b2b0750875a882ac5eacea8fa1e218",
	"225608e17d06cdc3d8096599ceff0e884e8f038a9afb50e9da4a3a9b96b34edf": "7666e198a2562bc6310e08fa0dc108eb7cf123bbbbdd7a71b509ba4d51ee5e20",
	"2aebbb61a12136b739137c9f67ffd7f5ddcbcd27faf8da9c25bf4387b2cab32b": "5b6e8e29b0a997c98169058116d7a9255d19bbf0f189c7d95f3ac8814716ead7",
	"37ca7e5360b0652b381368308d194323882faea4f3b94e387ecef8155e30a8ab": "f4163a678ba9956dadf1c64c71525d1132c550caee2c4f8d8c2c85dc80514d39",
	"bc251655d8cbaeee80fa37058a52a40bb594decb3f42ac64ee0b9602f2f9f829": "62a275de663c6b973fcd5aafdd2db3e95790fe8c72766889cdf0dc20361ddcef",
	"ec9f2df9c702280c4e585abec3bff13cc7aa4084c1e2b6ade89675d03c507d35": "6164866c41cd0eeeba92ac42cc0eca1e6e69f9e0bd9bbe472283dcaf45e7c374",
}

func domHTTPPaginationConfigFingerprint(config map[string]string) string {
	if config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "1" {
		return ""
	}
	md, err := richProfileMetadata(config)
	if err != nil {
		return ""
	}
	stable, err := stableJSONLDConfig(config, md)
	if err != nil {
		return ""
	}
	body, err := json.Marshal(stable)
	if err != nil {
		return ""
	}
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:])
}

func domHTTPPaginationSourceMatches(boardID string, config map[string]string, sources map[string]string) bool {
	expected, ok := sources[domHTTPPaginationConfigFingerprint(config)]
	if !ok {
		return false
	}
	digest := sha256.Sum256([]byte(boardID))
	return expected == hex.EncodeToString(digest[:])
}

func DOMMonitorHasQualifiedHTTPPagination(boardID string, config map[string]string) bool {
	return domHTTPPaginationSourceMatches(boardID, config, qualifiedDOMHTTPPaginationSources)
}
