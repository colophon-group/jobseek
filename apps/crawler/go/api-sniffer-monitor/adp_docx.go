package apisniffer

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"html"
	"io"
	"regexp"
	"strings"
)

const adpWordNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

// Like defusedxml's default parser, external DTD declarations are inert;
// encoding/xml never resolves them. Entity definitions remain rejected.
var adpWordDoctype = regexp.MustCompile(`^DOCTYPE[\t\r\n ]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[\t\r\n ]+SYSTEM[\t\r\n ]+(?:"[^"]*"|'[^']*')|[\t\r\n ]+PUBLIC[\t\r\n ]+(?:"[^"]*"|'[^']*')[\t\r\n ]+(?:"[^"]*"|'[^']*'))?[\t\r\n ]*(?:\[[\t\r\n ]*\])?$`)

type adpWordNode struct {
	Name     xml.Name
	Attr     []xml.Attr
	Text     string
	Children []*adpWordNode
}

func adpWordXML(raw []byte) (*adpWordNode, error) {
	d := xml.NewDecoder(bytes.NewReader(raw))
	stack := []*adpWordNode{}
	var root *adpWordNode
	doctype := false
	for {
		token, e := d.Token()
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, e
		}
		switch t := token.(type) {
		case xml.Directive:
			if doctype || root != nil || !adpWordDoctype.MatchString(strings.TrimSpace(string(t))) {
				return nil, ErrInventory
			}
			doctype = true
		case xml.StartElement:
			n := &adpWordNode{Name: t.Name, Attr: t.Attr}
			if len(stack) == 0 {
				if root != nil {
					return nil, ErrInventory
				}
				root = n
			} else {
				parent := stack[len(stack)-1]
				parent.Children = append(parent.Children, n)
			}
			stack = append(stack, n)
		case xml.EndElement:
			if len(stack) == 0 {
				return nil, ErrInventory
			}
			stack = stack[:len(stack)-1]
		case xml.CharData:
			if len(stack) > 0 {
				stack[len(stack)-1].Text += string(t)
			} else if strings.TrimSpace(string(t)) != "" {
				return nil, ErrInventory
			}
		}
	}
	if root == nil || len(stack) != 0 {
		return nil, ErrInventory
	}
	return root, nil
}
func (n *adpWordNode) child(name string) *adpWordNode {
	for _, c := range n.Children {
		if c.Name.Space == adpWordNS && c.Name.Local == name {
			return c
		}
	}
	return nil
}
func adpWordWalk(n *adpWordNode, fn func(*adpWordNode)) {
	if n == nil {
		return
	}
	fn(n)
	for _, c := range n.Children {
		adpWordWalk(c, fn)
	}
}
func adpParagraphText(n *adpWordNode) string {
	var b strings.Builder
	adpWordWalk(n, func(v *adpWordNode) {
		if v.Name.Space == adpWordNS {
			switch v.Name.Local {
			case "t":
				b.WriteString(v.Text)
			case "tab":
				b.WriteString("\t")
			case "br", "cr":
				b.WriteString("\n")
			}
		}
	})
	return strings.TrimSpace(b.String())
}
func adpEscape(text string) string {
	return strings.ReplaceAll(html.EscapeString(text), "&#39;", "&#x27;")
}
func ADPDocxToHTML(content []byte) string {
	if len(content) > 10<<20 {
		return ""
	}
	archive, e := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if e != nil || len(archive.File) > 1024 {
		return ""
	}
	var target *zip.File
	for _, f := range archive.File {
		if f.Name == "word/document.xml" {
			target = f
		}
	}
	if target == nil || target.UncompressedSize64 > 5<<20 {
		return ""
	}
	file, e := target.Open()
	if e != nil {
		return ""
	}
	raw, e := io.ReadAll(io.LimitReader(file, (5<<20)+1))
	ce := file.Close()
	if e != nil || ce != nil || len(raw) > 5<<20 {
		return ""
	}
	root, e := adpWordXML(raw)
	if e != nil {
		return ""
	}
	var body *adpWordNode
	for _, child := range root.Children {
		adpWordWalk(child, func(n *adpWordNode) {
			if body == nil && n.Name.Space == adpWordNS && n.Name.Local == "body" {
				body = n
			}
		})
	}
	if body == nil {
		return ""
	}
	blocks, list := []string{}, []string{}
	flush := func() {
		if len(list) > 0 {
			blocks = append(blocks, "<ul>"+strings.Join(list, "")+"</ul>")
			list = nil
		}
	}
	for _, n := range body.Children {
		if n.Name.Space != adpWordNS {
			continue
		}
		switch n.Name.Local {
		case "p":
			text := adpParagraphText(n)
			if text == "" {
				flush()
				continue
			}
			escaped := strings.ReplaceAll(adpEscape(text), "\n", "<br>")
			style := ""
			ppr := n.child("pPr")
			if ppr != nil {
				if ppr.child("numPr") != nil {
					list = append(list, "<li>"+escaped+"</li>")
					continue
				}
				if ps := ppr.child("pStyle"); ps != nil {
					for _, a := range ps.Attr {
						if a.Name.Space == adpWordNS && a.Name.Local == "val" {
							style = strings.ToLower(a.Value)
						}
					}
				}
			}
			flush()
			tag := "p"
			if strings.HasPrefix(style, "heading") || style == "title" || style == "subtitle" {
				tag = "h3"
			}
			blocks = append(blocks, "<"+tag+">"+escaped+"</"+tag+">")
		case "tbl":
			flush()
			rows := []string{}
			for _, r := range n.Children {
				if r.Name.Space != adpWordNS || r.Name.Local != "tr" {
					continue
				}
				cells := []string{}
				for _, c := range r.Children {
					if c.Name.Space != adpWordNS || c.Name.Local != "tc" {
						continue
					}
					parts := []string{}
					adpWordWalk(c, func(v *adpWordNode) {
						if v.Name.Space == adpWordNS && v.Name.Local == "p" {
							if s := adpParagraphText(v); s != "" {
								parts = append(parts, s)
							}
						}
					})
					cells = append(cells, "<td>"+adpEscape(strings.Join(parts, " "))+"</td>")
				}
				if len(cells) > 0 {
					rows = append(rows, "<tr>"+strings.Join(cells, "")+"</tr>")
				}
			}
			if len(rows) > 0 {
				blocks = append(blocks, "<table>"+strings.Join(rows, "")+"</table>")
			}
		}
	}
	flush()
	return strings.Join(blocks, "\n")
}
