package jsonld

import (
	"bytes"
	"golang.org/x/net/html"
	stdhtml "html"
	"regexp"
	"strings"
)

var attributeRE = regexp.MustCompile(`([^\s/=>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?`)

// x/net drops repeated attributes (HTML browser rules), whereas Python's
// HTMLParser exposes all of them and dict(attrs) retains the last value.
func pythonAttributes(raw []byte) map[string]string {
	text := strings.TrimSuffix(string(raw), ">")
	start := strings.IndexAny(text, " \t\r\n\f")
	attrs := make(map[string]string)
	if start < 0 {
		return attrs
	}
	for _, match := range attributeRE.FindAllStringSubmatch(text[start:], -1) {
		attrs[strings.ToLower(match[1])] = stdhtml.UnescapeString(match[2] + match[3] + match[4])
	}
	return attrs
}

func htmlPrefix(body []byte, limit int) []byte {
	n := 0
	for i := range string(body) {
		if n == limit {
			return body[:i]
		}
		n++
	}
	return body
}

// The same bounded head and last literal 0/1 metadata precedence as the Python
// resource checker. Callers retain its immediate header-only rejection.
func tdmMetadata(body []byte) (reservation string, policy string) {
	z := html.NewTokenizer(bytes.NewReader(htmlPrefix(body, 65_536)))
	for {
		typeOf := z.Next()
		if typeOf == html.ErrorToken {
			return
		}
		if typeOf != html.StartTagToken && typeOf != html.SelfClosingTagToken {
			continue
		}
		raw := append([]byte(nil), z.Raw()...)
		t := z.Token()
		if t.Data != "meta" {
			continue
		}
		attrs := pythonAttributes(raw)
		switch strings.ToLower(attrs["name"]) {
		case "tdm-reservation":
			v := strings.TrimSpace(attrs["content"])
			if v == "0" || v == "1" {
				reservation = v
			}
		case "tdm-policy":
			policy = attrs["content"]
		}
	}
}

func tdmMetaReserved(body []byte) bool { r, _ := tdmMetadata(body); return r == "1" }
