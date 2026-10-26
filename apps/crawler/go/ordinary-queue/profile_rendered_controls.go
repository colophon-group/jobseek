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
	"0ff92fedff84d362463b42e14fbef686101e479d4a1207eb473328abb114d635": "www.alten.be",
	"6098bafc7a62677d713378f1bc6340aec62193d44310ad9734eed3b9d9154951": "www.alten.it",
	"b33d5ca058fa34a665a80d101b30325e5a37791c021dae98e2c99b758c60848b": "www.alten.nl",
	"4d3d026a387035153d7a7398025da7dceaa22806dd6f3e8c43d19b5ef11b5281": "www.alten.pt",
	"87126eb2caa7161d15ef399c541abf22fd3199247cf4be85cadbb356f8b56df9": "www.alten.ch",
	"21eb5da49bafd59b3814b98b1d22f5e342867701319dc26a9ab5e140d8d86710": "careers.oac.edu.au",
	"cdf9ca5e9c0183dd978dfdbb75b4fe6176b52ad2113440f9faa5e74da8f4da26": "careers.glasswall.com",
	"0d2111c8d5d0f405738c10c5c44959841e9352479343e81a94c2b3e7586d9024": "www.unhcr.org",
	"bede99ae62e708ea2e6d775ee9fba8652b0cb3b6fbb7a01e8b26369a25cb27fd": "rrd.zohorecruit.in",
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
