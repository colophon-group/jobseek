package enrichment

import (
	stdhtml "html"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/net/html"
)

var droppedHTMLTags = map[string]bool{
	"script": true, "style": true, "noscript": true, "iframe": true,
	"object": true, "embed": true, "svg": true, "math": true,
	"canvas": true, "template": true, "head": true,
}

var allowedHTMLTags = map[string]bool{
	"a": true, "b": true, "blockquote": true, "br": true, "code": true,
	"em": true, "h1": true, "h2": true, "h3": true, "h4": true,
	"h5": true, "h6": true, "hr": true, "i": true, "li": true,
	"ol": true, "p": true, "pre": true, "s": true, "strong": true,
	"u": true, "ul": true,
}

// Python's escaped-markup detector uses Unicode whitespace and word boundaries.
// Keep the boundary outside RE2's ASCII-only \b class.
var escapedHTMLTag = regexp.MustCompile(`(?i)&lt;[\t\n\v\f\r \x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}\x{001c}-\x{001f}]*/?[\t\n\v\f\r \x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}\x{001c}-\x{001f}]*(?:blockquote|strong|pre|code|br|em|ul|ol|l[iİı]|h[1-6]|p|a|b|[iİı]|u|s)`)

var pythonCharRef = regexp.MustCompile(`&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)`)
var htmlTextEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\u00a0", "&nbsp;")
var htmlAttrEscape = strings.NewReplacer("&", "&amp;", "\"", "&quot;", "<", "&lt;", ">", "&gt;", "\u00a0", "&nbsp;")

func pythonUnescape(s string) string {
	return pythonCharRef.ReplaceAllStringFunc(s, func(ref string) string {
		if !strings.HasPrefix(ref, "&#") {
			return stdhtml.UnescapeString(ref)
		}
		num := strings.TrimSuffix(ref[2:], ";")
		base := 10
		if num[0] == 'x' || num[0] == 'X' {
			base, num = 16, num[1:]
		}
		cp, err := strconv.ParseUint(num, base, 32)
		if err != nil || cp > 0x10ffff || cp >= 0xd800 && cp <= 0xdfff || cp == 0 {
			return "\ufffd"
		}
		// Python html.unescape removes these noncharacters/control references.
		// Windows-1252 references 0x80..0x9f are mapped before that exclusion.
		if cp >= 0x80 && cp <= 0x9f {
			return stdhtml.UnescapeString("&#" + strconv.FormatUint(cp, 10) + ";")
		}
		if cp >= 1 && cp <= 8 || cp == 11 || cp >= 14 && cp <= 31 || cp == 127 || cp >= 0xfdd0 && cp <= 0xfdef || cp&0xffff >= 0xfffe {
			return ""
		}
		return string(rune(cp))
	})
}

func decodeEscapedHTML(s string) string {
	for _, match := range escapedHTMLTag.FindAllStringIndex(s, -1) {
		if next, _ := utf8.DecodeRuneInString(s[match[1]:]); match[1] == len(s) || !word(next) {
			return pythonUnescape(s)
		}
	}
	return s
}

// NormalizeDescriptionHTML owns the shared monitor/detail normalization stage.
// The document parse deliberately matches the legacy body context, including
// HTML5 foster parenting and formatting-element reconstruction.
func NormalizeDescriptionHTML(description string) (*string, error) {
	raw := strings.TrimFunc(description, space)
	if raw == "" {
		return nil, nil
	}
	tree, err := html.ParseWithOptions(strings.NewReader(decodeEscapedHTML(raw)), html.ParseOptionEnableScripting(false))
	if err != nil {
		return nil, err
	}
	var body *html.Node
	var findBody func(*html.Node)
	findBody = func(n *html.Node) {
		if n.Type == html.ElementNode && n.Data == "body" {
			body = n
			return
		}
		for c := n.FirstChild; c != nil && body == nil; c = c.NextSibling {
			findBody(c)
		}
	}
	findBody(tree)
	if body == nil {
		return nil, nil
	}
	var clean func(*html.Node)
	clean = func(n *html.Node) {
		for child := n.FirstChild; child != nil; {
			next := child.NextSibling
			if child.Type == html.ElementNode && droppedHTMLTags[child.Data] {
				n.RemoveChild(child)
			} else {
				clean(child)
			}
			child = next
		}
		if n.Type != html.ElementNode || n == body {
			return
		}
		if allowedHTMLTags[n.Data] {
			n.Attr = nil
		} else if n.FirstChild != nil {
			// Lexbor unwrap leaves empty unknown elements intact, including
			// their attributes. Preserve this existing canonical-byte behavior.
			for n.FirstChild != nil {
				child := n.FirstChild
				n.RemoveChild(child)
				n.Parent.InsertBefore(child, n)
			}
			n.Parent.RemoveChild(n)
		}
	}
	clean(body)
	var out strings.Builder
	var emit func(*html.Node)
	emit = func(n *html.Node) {
		switch n.Type {
		case html.TextNode:
			// Lexbor escapes only the HTML text delimiters, retaining quotes.
			text := htmlTextEscape.Replace(n.Data)
			out.WriteString(text)
		case html.CommentNode:
			out.WriteString("<!--")
			out.WriteString(n.Data)
			out.WriteString("-->")
		case html.ElementNode:
			out.WriteString("<" + n.Data)
			for _, attr := range n.Attr {
				value := htmlAttrEscape.Replace(attr.Val)
				out.WriteString(" " + attr.Key + "=\"" + value + "\"")
			}
			out.WriteString(">")
			for c := n.FirstChild; c != nil; c = c.NextSibling {
				emit(c)
			}
			if !voidHTMLTags[n.Data] {
				out.WriteString("</" + n.Data + ">")
			}
		}
	}
	for child := body.FirstChild; child != nil; child = child.NextSibling {
		emit(child)
	}
	cleaned := strings.TrimFunc(out.String(), space)
	if cleaned == "" {
		return nil, nil
	}
	cleaned = strings.ReplaceAll(strings.ReplaceAll(cleaned, "\u00a0", " "), "&nbsp;", " ")
	return &cleaned, nil
}

var voidHTMLTags = map[string]bool{
	"area": true, "base": true, "br": true, "col": true, "embed": true,
	"hr": true, "img": true, "input": true, "link": true, "meta": true,
	"param": true, "source": true, "track": true, "wbr": true,
}
