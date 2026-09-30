// Package publisherpolicy checks available resource-level TDM signals. It does
// not fetch policy URLs or implement origin-file policies or a rights register.
package publisherpolicy

import (
	"errors"
	htmltext "html"
	"strings"
	"unicode"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	resourcepolicy "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/resourcepolicy"
	"golang.org/x/net/html"
)

const MetaCharacterLimit = 65536

var ErrSignals = errors.New("invalid resource policy evidence")

type Reservation struct {
	URL, Source string
	PolicyURL   *string
}

func (r *Reservation) Error() string { return "resource declared TDM reservation" }

func space(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func literal(value string) (int, bool) {
	switch strings.TrimFunc(value, space) {
	case "0":
		return 0, true
	case "1":
		return 1, true
	}
	return 0, false
}

// Check follows the existing shared Python parser: the last valid metadata
// reservation wins over a header; metadata policy wins when nonempty. The
// bound is Unicode characters, and script/style/comment literals are ignored.
func Check(signals *runtimev1.ResourcePolicySignals, body, url string) error {
	if !resourcepolicy.Valid(signals) {
		return ErrSignals
	}
	parsed, source := 0, "header"
	var policy *string
	if signals != nil {
		parsed, _ = literal(signals.GetTdmReservationHeader())
		if value := signals.GetTdmPolicyHeader(); value != "" {
			policy = &value
		}
	}
	var metaPolicy *string
	count, end := 0, len(body)
	for position := range body {
		if count == MetaCharacterLimit {
			end = position
			break
		}
		count++
	}
	excerpt := body[:end]
	if strings.Contains(strings.ToLower(excerpt), "tdm-") {
		tokenizer := html.NewTokenizer(strings.NewReader(excerpt))
		for {
			kind := tokenizer.Next()
			if kind == html.ErrorToken {
				break
			}
			if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
				continue
			}
			rawTag := string(append([]byte(nil), tokenizer.Raw()...))
			token := tokenizer.Token()
			if token.Data != "meta" {
				continue
			}
			attrs := metadataAttributes(rawTag)
			switch strings.ToLower(attrs["name"]) {
			case "tdm-reservation":
				if value, valid := literal(attrs["content"]); valid {
					parsed, source = value, "meta"
				}
			case "tdm-policy":
				if value := attrs["content"]; value != "" {
					metaPolicy = &value
				} else {
					metaPolicy = nil
				}
			}
		}
	}
	if metaPolicy != nil {
		policy = metaPolicy
	}
	if parsed == 1 {
		return &Reservation{URL: url, Source: source, PolicyURL: policy}
	}
	return nil
}

// Python HTMLParser retains duplicate attributes and its dict conversion uses
// the last value. x/net/html drops later duplicates, so inspect only the raw
// meta start tag that the tokenizer has separated from comments/text.
func metadataAttributes(raw string) map[string]string {
	attrs := map[string]string{}
	position := 1
	next := func() rune {
		if position >= len(raw) {
			return 0
		}
		r, _ := utf8.DecodeRuneInString(raw[position:])
		return r
	}
	advance := func() { _, size := utf8.DecodeRuneInString(raw[position:]); position += size }
	for position < len(raw) && !space(next()) && next() != '>' && next() != '/' {
		advance()
	}
	for position < len(raw) {
		for position < len(raw) && (space(next()) || next() == '/') {
			advance()
		}
		if position >= len(raw) || next() == '>' {
			break
		}
		start := position
		for position < len(raw) && !space(next()) && next() != '/' && next() != '>' && next() != '=' {
			advance()
		}
		if position == start {
			advance()
			continue
		}
		name := strings.ToLower(raw[start:position])
		for position < len(raw) && space(next()) {
			advance()
		}
		value := ""
		if position < len(raw) && next() == '=' {
			for position < len(raw) && next() == '=' {
				advance()
			}
			for position < len(raw) && space(next()) {
				advance()
			}
			quote := next()
			if quote == '\'' || quote == '"' {
				advance()
				start = position
				for position < len(raw) && next() != quote {
					advance()
				}
				value = raw[start:position]
				if position < len(raw) {
					advance()
				}
			} else {
				start = position
				for position < len(raw) && !space(next()) && next() != '>' {
					advance()
				}
				value = raw[start:position]
			}
		}
		attrs[name] = htmltext.UnescapeString(value)
	}
	return attrs
}
