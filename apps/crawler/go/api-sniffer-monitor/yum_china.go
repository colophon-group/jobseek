package apisniffer

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
)

const YumChinaBoardURL = "https://campus.51job.com/yumchina/project.html"
const YumChinaJobsURL = "https://campus.51job.com/yumchina/js/job.js"
const YumChinaBrowserExpression = "({jobs: jobList})"

type YumChinaResourceScope struct{}

func (YumChinaResourceScope) ResourceMatches(raw string) bool {
	return raw == YumChinaBoardURL || raw == YumChinaJobsURL
}

// The published browser expression reads a literal in this first-party asset.
// Bind the document's script reference before fetching it through verified HTTP.
func YumChinaScriptURL(source string) (string, error) {
	if len(source) > 4_000_000 || !utf8.ValidString(source) {
		return "", ErrEmbedded
	}
	root, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return "", ErrEmbedded
	}
	base, _ := url.Parse(YumChinaBoardURL)
	count := 0
	for _, script := range cascadia.QueryAll(root, cascadia.MustCompile("script[src]")) {
		for _, attr := range script.Attr {
			if attr.Key == "src" {
				ref, err := url.Parse(attr.Val)
				if err == nil && base.ResolveReference(ref).String() == YumChinaJobsURL {
					count++
				}
			}
		}
	}
	if count != 1 {
		return "", ErrEmbedded
	}
	return YumChinaJobsURL, nil
}

// Read the provider's array of string-valued records without evaluating code.
// Missing literals, expressions, duplicate keys and later mutation fail before
// any inventory is emitted; they cannot prove that a job has disappeared.
func ParseYumChinaJobs(source string) (*Document, error) {
	if len(source) > 4_000_000 || !utf8.ValidString(source) {
		return nil, ErrEmbedded
	}
	s := strings.TrimSpace(source)
	if !strings.HasPrefix(s, "let jobList") {
		return nil, ErrEmbedded
	}
	s = strings.TrimSpace(strings.TrimPrefix(s, "let jobList"))
	if !strings.HasPrefix(s, "=") {
		return nil, ErrEmbedded
	}
	p := yumLiteral{source: strings.TrimSpace(s[1:])}
	if !p.take('[') {
		return nil, ErrEmbedded
	}
	rows := []map[string]string{}
	for !p.take(']') {
		if len(rows) >= 50_000 || !p.take('{') {
			return nil, ErrEmbedded
		}
		row := map[string]string{}
		for !p.take('}') {
			p.space()
			start := p.at
			for p.at < len(p.source) && (p.source[p.at] >= 'a' && p.source[p.at] <= 'z' || p.source[p.at] == '_') {
				p.at++
			}
			key := p.source[start:p.at]
			if key != "type" && key != "title" && key != "desc" && key != "link" {
				return nil, ErrEmbedded
			}
			if _, exists := row[key]; exists || !p.take(':') {
				return nil, ErrEmbedded
			}
			value, err := p.text()
			if err != nil {
				return nil, err
			}
			row[key] = value
			if !p.take(',') {
				if !p.take('}') {
					return nil, ErrEmbedded
				}
				break
			}
		}
		if len(row) != 4 || strings.TrimSpace(row["title"]) == "" || strings.TrimSpace(row["desc"]) == "" || row["link"] == "" {
			return nil, ErrEmbedded
		}
		rows = append(rows, row)
		if !p.take(',') {
			if !p.take(']') {
				return nil, ErrEmbedded
			}
			break
		}
	}
	if !p.take(';') {
		return nil, ErrEmbedded
	}
	// Jobs can change without changing the published read-only UI controls.
	// A different nonempty control program requires renewed browser evidence.
	tail := strings.TrimSpace(strings.ReplaceAll(p.source[p.at:], "\r", ""))
	if tail != "" {
		hash := sha256.Sum256([]byte(tail))
		if hex.EncodeToString(hash[:]) != "a3831c7cebe35a17e0dd642b4646216340d9313d70fab69480f215dcbd9ed2c3" {
			return nil, ErrEmbedded
		}
	}
	body, err := json.Marshal(map[string]any{"jobs": rows})
	if err != nil {
		return nil, err
	}
	return Decode(body)
}

type yumLiteral struct {
	source string
	at     int
}

func (p *yumLiteral) space() {
	for p.at < len(p.source) && strings.ContainsRune(" \t\r\n", rune(p.source[p.at])) {
		p.at++
	}
}

func (p *yumLiteral) take(ch byte) bool {
	p.space()
	if p.at < len(p.source) && p.source[p.at] == ch {
		p.at++
		return true
	}
	return false
}

func (p *yumLiteral) text() (string, error) {
	p.space()
	if p.at >= len(p.source) || p.source[p.at] != '\'' && p.source[p.at] != '"' {
		return "", ErrEmbedded
	}
	quote := p.source[p.at]
	p.at++
	var out strings.Builder
	out.WriteByte('"')
	for p.at < len(p.source) {
		ch := p.source[p.at]
		p.at++
		if ch == quote {
			out.WriteByte('"')
			var result string
			if json.Unmarshal([]byte(out.String()), &result) != nil {
				return "", ErrEmbedded
			}
			return result, nil
		}
		if ch == '\\' {
			if p.at >= len(p.source) {
				return "", ErrEmbedded
			}
			next := p.source[p.at]
			p.at++
			if next == '\'' {
				out.WriteByte(next)
			} else if strings.ContainsRune(`"\/bfnrtu`, rune(next)) {
				out.WriteByte('\\')
				out.WriteByte(next)
			} else {
				return "", ErrEmbedded
			}
		} else {
			if ch == '"' {
				out.WriteByte('\\')
			}
			out.WriteByte(ch)
		}
	}
	return "", ErrEmbedded
}
