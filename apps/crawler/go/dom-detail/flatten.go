package dom

import (
	stdhtml "html"
	"io"
	"strings"
	"unicode"

	"golang.org/x/net/html"
)

type Element struct {
	Tag   string            `json:"tag"`
	Attrs map[string]string `json:"attrs"`
	Text  string            `json:"text"`
}
type stackEntry struct {
	tag     string
	attrs   map[string]string
	skipped bool
}
type flattener struct {
	elements                     []Element
	stack                        []stackEntry
	skipDepth                    int
	text                         []string
	blockTag                     string
	blockAttrs                   map[string]string
	inTitle, inHeaderH1          bool
	titleText, headerText        []string
	headerAttrs                  map[string]string
	includeHidden, includeHeader bool
}

func tags(s string) map[string]bool {
	m := map[string]bool{}
	for _, t := range strings.Fields(s) {
		m[t] = true
	}
	return m
}

var skipTags = tags("script style noscript svg path meta link iframe object embed head template")
var noiseTags = tags("nav footer header")
var inlineTags = tags("a abbr acronym b bdo big br button cite code dfn em i img input kbd label map mark q ruby s samp select small span strong sub sup textarea time tt u var wbr")
var voidTags = tags("area base br col embed hr img input link meta param source track wbr")

func pySpace(r rune) bool  { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func trim(s string) string { return strings.TrimFunc(s, pySpace) }
func normalized(parts []string) string {
	return strings.Join(strings.FieldsFunc(strings.Join(parts, ""), pySpace), " ")
}
func (p *flattener) flush() {
	text := normalized(p.text)
	if text != "" {
		tag := p.blockTag
		if tag == "" {
			tag = "?"
		}
		attrs := p.blockAttrs
		if attrs == nil {
			attrs = map[string]string{}
		}
		p.elements = append(p.elements, Element{tag, attrs, text})
	}
	p.text = nil
}
func (p *flattener) flushHeader() {
	if text := normalized(p.headerText); text != "" {
		p.elements = append(p.elements, Element{"h1", p.headerAttrs, text})
	}
	p.inHeaderH1 = false
	p.headerText = nil
	p.headerAttrs = nil
}
func (p *flattener) start(tag string, attrs map[string]string) {
	if tag == "title" {
		p.inTitle = true
	}
	if p.skipDepth > 0 {
		var root *stackEntry
		nav := false
		for i := range p.stack {
			entry := &p.stack[i]
			if root == nil && entry.skipped {
				root = entry
			}
			nav = nav || entry.tag == "nav"
		}
		if tag == "h1" && root != nil && root.tag == "header" && !nav && root.attrs["aria-hidden"] != "true" {
			if _, hidden := root.attrs["hidden"]; !hidden {
				p.inHeaderH1 = true
				p.headerAttrs = attrs
				p.headerText = nil
			}
		} else if tag == "br" && p.inHeaderH1 {
			p.headerText = append(p.headerText, " ")
		}
		if !voidTags[tag] {
			p.stack = append(p.stack, stackEntry{tag, attrs, true})
			p.skipDepth++
		}
		return
	}
	_, hidden := attrs["hidden"]
	if skipTags[tag] || noiseTags[tag] && !(tag == "header" && p.includeHeader) || !p.includeHidden && (attrs["aria-hidden"] == "true" || hidden) {
		if !voidTags[tag] {
			p.stack = append(p.stack, stackEntry{tag, attrs, true})
			p.skipDepth = 1
		}
		return
	}
	if tag == "br" {
		p.text = append(p.text, " ")
	}
	if !voidTags[tag] {
		p.stack = append(p.stack, stackEntry{tag, attrs, false})
	}
	if !inlineTags[tag] && !voidTags[tag] {
		p.flush()
		if _, exists := attrs["data-title"]; !exists {
			for i := len(p.stack) - 2; i >= 0; i-- {
				entry := p.stack[i]
				if title, exists := entry.attrs["data-title"]; !entry.skipped && exists {
					copied := map[string]string{}
					for k, v := range attrs {
						copied[k] = v
					}
					copied["data-title"] = title
					attrs = copied
					break
				}
			}
		}
		p.blockTag = tag
		p.blockAttrs = attrs
	}
}
func (p *flattener) end(tag string) {
	if tag == "title" {
		p.inTitle = false
	}
	if tag == "h1" && p.inHeaderH1 {
		p.flushHeader()
	}
	if voidTags[tag] {
		return
	}
	poppedSkipped := 0
	for i := len(p.stack) - 1; i >= 0; i-- {
		if p.stack[i].tag == tag {
			for _, e := range p.stack[i:] {
				if e.skipped {
					poppedSkipped++
				}
			}
			p.stack = p.stack[:i]
			break
		}
	}
	if poppedSkipped > 0 {
		p.skipDepth = max(0, p.skipDepth-poppedSkipped)
		return
	}
	if p.skipDepth > 0 {
		return
	}
	if !inlineTags[tag] {
		p.flush()
		p.blockTag = ""
		p.blockAttrs = nil
		for i := len(p.stack) - 1; i >= 0; i-- {
			e := p.stack[i]
			if !e.skipped && !inlineTags[e.tag] {
				p.blockTag = e.tag
				p.blockAttrs = e.attrs
				break
			}
		}
	}
}
func (p *flattener) data(text string) {
	if p.inTitle {
		p.titleText = append(p.titleText, text)
	}
	if p.inHeaderH1 {
		p.headerText = append(p.headerText, text)
	}
	if p.skipDepth == 0 {
		p.text = append(p.text, text)
	}
}

// Flatten follows HTMLParser's token stack, including optional/malformed end
// tags. An HTML5 tree would imply closures which change the canonical fields.
func Flatten(source string, includeHidden, includeHeader bool) ([]Element, error) {
	p := flattener{elements: []Element{}, includeHidden: includeHidden, includeHeader: includeHeader}
	z := html.NewTokenizer(strings.NewReader(source))
	for {
		kind := z.Next()
		if kind == html.ErrorToken {
			if err := z.Err(); err != io.EOF {
				return nil, err
			}
			break
		}
		switch kind {
		case html.StartTagToken, html.SelfClosingTagToken:
			t := z.Token()
			attrs := map[string]string{}
			for _, a := range t.Attr {
				attrs[a.Key] = a.Val
			}
			p.start(t.Data, attrs)
			if t.Data != "script" && t.Data != "style" {
				z.NextIsNotRawText()
			}
			if kind == html.SelfClosingTagToken {
				p.end(t.Data)
			}
		case html.EndTagToken:
			p.end(z.Token().Data)
		case html.TextToken:
			text := string(z.Raw())
			if len(p.stack) == 0 || (p.stack[len(p.stack)-1].tag != "script" && p.stack[len(p.stack)-1].tag != "style") {
				text = stdhtml.UnescapeString(text)
			}
			p.data(text)
		}
	}
	p.flush()
	if p.inHeaderH1 {
		p.flushHeader()
	}
	if text := normalized(p.titleText); text != "" {
		p.elements = append([]Element{{"title", map[string]string{}, text}}, p.elements...)
	}
	return p.elements, nil
}
