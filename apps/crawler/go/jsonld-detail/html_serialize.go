package jsonld

import (
	"fmt"
	"io"
	"strings"

	"golang.org/x/net/html"
)

var voidElements = map[string]bool{"area": true, "base": true, "br": true, "col": true, "embed": true, "hr": true, "img": true, "input": true, "link": true, "meta": true, "param": true, "source": true, "track": true, "wbr": true}
var rawElements = map[string]bool{"script": true, "style": true, "xmp": true, "iframe": true, "noembed": true, "noframes": true, "plaintext": true}

func escapeHTML(s string, attribute bool) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "\u00a0", "&nbsp;")
	if attribute {
		return strings.ReplaceAll(s, `"`, "&quot;")
	}
	s = strings.ReplaceAll(s, "<", "&lt;")
	return strings.ReplaceAll(s, ">", "&gt;")
}

// Lexbor's HTML5 serialization preserves apostrophes and emits HTML void
// elements without an XML slash. x/net's Render uses different escaping.
func renderHTML(w io.Writer, node *html.Node) error {
	switch node.Type {
	case html.TextNode:
		s := node.Data
		if node.Parent == nil || !rawElements[node.Parent.Data] {
			s = escapeHTML(s, false)
		}
		_, err := io.WriteString(w, s)
		return err
	case html.CommentNode:
		_, err := io.WriteString(w, "<!--"+node.Data+"-->")
		return err
	case html.ElementNode:
		if _, err := io.WriteString(w, "<"+node.Data); err != nil {
			return err
		}
		for _, a := range node.Attr {
			key := a.Key
			if a.Namespace != "" {
				key = a.Namespace + ":" + key
			}
			if _, err := io.WriteString(w, " "+key+`="`+escapeHTML(a.Val, true)+`"`); err != nil {
				return err
			}
		}
		if _, err := io.WriteString(w, ">"); err != nil {
			return err
		}
		if voidElements[node.Data] {
			return nil
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			if err := renderHTML(w, child); err != nil {
				return err
			}
		}
		_, err := io.WriteString(w, "</"+node.Data+">")
		return err
	case html.DocumentNode:
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			if err := renderHTML(w, child); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("unsupported HTML node %d", node.Type)
	}
}
