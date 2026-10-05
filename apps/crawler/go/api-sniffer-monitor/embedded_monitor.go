package apisniffer

import (
	"encoding/json"
	"regexp"
	"strings"
	"unicode/utf8"
)

var nextdataMonitorScript = regexp.MustCompile(`(?s)<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>`)
var phenomCanvasMonitor = regexp.MustCompile(`phApp\.ddo\s*=\s*`)

// ParseEmbeddedMonitorDocument shares the existing precise JSON/RSC/React
// Router decoder while retaining the monitor's first, strict NextData marker.
// Detail extraction deliberately keeps its existing last-script behavior.
func ParseEmbeddedMonitorDocument(source, kind string) (*Document, error) {
	if len(source) > 16<<20 || !utf8.ValidString(source) {
		return nil, ErrEmbedded
	}
	if kind == "reactrouter" || kind == "rsc" {
		return embeddedDocument(source, map[string]any{"source": kind})
	}
	if kind == "phenom_canvas" {
		m := phenomCanvasMonitor.FindStringIndex(source)
		if m == nil {
			return nil, ErrEmbedded
		}
		text := strings.TrimLeft(source[m[1]:], " \t\r\n")
		if !strings.HasPrefix(text, "{") {
			return nil, ErrEmbedded
		}
		// Decode just the first complete object; trailing JavaScript belongs
		// outside the data. RawMessage retains original number spelling/order.
		var raw json.RawMessage
		if json.NewDecoder(strings.NewReader(text)).Decode(&raw) != nil {
			return nil, ErrEmbedded
		}
		return Decode(raw)
	}
	m := nextdataMonitorScript.FindStringSubmatch(source)
	if m == nil {
		return nil, ErrEmbedded
	}
	return Decode([]byte(m[1]))
}
