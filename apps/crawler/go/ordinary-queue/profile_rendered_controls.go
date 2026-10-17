package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
)

// These complete original configurations have matching original Chromium and
// verified-TLS Lightpanda document fields. Their replacement uses normal
// Lightpanda navigation; legacy engine settings remain in the ownership hash.
// This is not general support for stealth, TLS exceptions or warmup navigation.
var renderedDOMControlSources = map[string]string{
	"4cfcc148aaa51c19e1a7329376dbbc7b005ed1d332f9bdbaf4dc24108a390ced": "ukraine.arcelormittal.com",
	"307e4384fba791cc5bb76903dd1c215f91a44eab2b00833bc0a2e4b971c758a4": "swibeco.zohorecruit.eu",
	"8c79a43fcb715f4106c4caf126b189489330464a051d8b16ac54987f88e3f0d6": "careers.hrblock.com.au",
}

func qualifiedRenderedControlSource(digest, source string) bool {
	host, ok := renderedDOMControlSources[digest]
	if !ok {
		return false
	}
	u, err := url.Parse(source)
	return err == nil && u.Scheme == "https" && u.User == nil && u.Opaque == "" &&
		strings.ToLower(u.Hostname()) == host && (u.Port() == "" || u.Port() == "443")
}

func qualifiedRenderedDOMControls(boardID string, config map[string]string, metadata map[string]json.RawMessage, source string, options map[string]any) bool {
	if options["stealth"] == nil && options["skip_ssl"] != true && options["warmup_url"] == nil {
		return false
	}
	stable, err := stableJSONLDConfig(config, metadata)
	if err != nil {
		return false
	}
	body, err := json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return false
	}
	digest := sha256.Sum256(body)
	return qualifiedRenderedControlSource(hex.EncodeToString(digest[:]), source)
}
