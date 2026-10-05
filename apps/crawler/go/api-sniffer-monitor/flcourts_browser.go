package apisniffer

import (
	"encoding/json"
	"math"
	"reflect"
	"strings"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
)

// FloridaCourtsBrowserExpression is the existing configured expression. Only
// this exact document transform is admitted; arbitrary JavaScript is not run.
const FloridaCourtsBrowserExpression = "(() => { const root=JSON.parse(document.querySelector('#__NEXT_DATA__').textContent); const parsed=new DOMParser().parseFromString(root.props.pageProps.pageData.description.html5,'text/html'); const payload=JSON.parse(parsed.querySelector('.JobListings_ibexa').dataset.content); return {items:payload.items.map(({location,content})=>({url:'https://www.flcourts.gov'+location.url,...content.fields}))}; })()"

// ParseFloridaCourtsBrowserDocument preserves the browser's HTML5 selectors,
// attribute decoding, JSON number rounding and fields-over-url spread order.
// Missing or malformed input fails the entire inventory before any write.
func ParseFloridaCourtsBrowserDocument(source string) (*Document, error) {
	if len(source) > 16<<20 || !utf8.ValidString(source) {
		return nil, ErrEmbedded
	}
	root, err := browserDocument(source, true)
	if err != nil {
		return nil, err
	}
	marker := cascadia.Query(root, cascadia.MustCompile("#__NEXT_DATA__"))
	if marker == nil {
		return nil, ErrEmbedded
	}
	var text strings.Builder
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			text.WriteString(n.Data)
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(marker)
	d, err := Decode([]byte(text.String()))
	if err != nil {
		return nil, ErrEmbedded
	}
	raw, err := Search(d.Value, "props.pageProps.pageData.description.html5")
	markup, ok := raw.(string)
	if err != nil || !ok {
		return nil, ErrEmbedded
	}
	nested, err := browserDocument(markup, false)
	if err != nil {
		return nil, err
	}
	listing := cascadia.Query(nested, cascadia.MustCompile(".JobListings_ibexa"))
	if listing == nil {
		return nil, ErrEmbedded
	}
	content := ""
	for _, attr := range listing.Attr {
		if attr.Key == "data-content" && attr.Namespace == "" {
			content = attr.Val
			break
		}
	}
	d, err = Decode([]byte(content))
	if err != nil {
		return nil, ErrEmbedded
	}
	value, err := Search(d.Value, "items")
	items, ok := value.([]any)
	if err != nil || !ok {
		return nil, ErrEmbedded
	}
	rows := make([]any, 0, len(items))
	for _, item := range items {
		row, ok := item.(map[string]any)
		if !ok {
			return nil, ErrEmbedded
		}
		location, lok := row["location"].(map[string]any)
		fieldsParent, cok := row["content"].(map[string]any)
		if !lok || !cok {
			return nil, ErrEmbedded
		}
		// Supported provider records expose a string URL and an object of
		// fields. Refuse other shapes rather than inventing identities.
		link, lok := location["url"].(string)
		fields, fok := fieldsParent["fields"].(map[string]any)
		if !lok || !fok {
			return nil, ErrEmbedded
		}
		out := map[string]any{"url": "https://www.flcourts.gov" + link}
		keys := []string{"url"}
		for _, key := range d.ObjectKeys(fields) {
			if key != "url" {
				keys = append(keys, key)
			}
			out[key] = fields[key]
		}
		d.order[reflect.ValueOf(out)] = keys
		rows = append(rows, out)
	}
	result := map[string]any{"items": rows}
	d.order[reflect.ValueOf(result)] = []string{"items"}
	// JSON.parse in the configured browser expression uses IEEE-754, unlike
	// the ordinary embedded monitor's precise JSON numbers.
	var round func(any) any
	round = func(v any) any {
		switch x := v.(type) {
		case json.Number:
			f, _ := x.Float64()
			if math.IsInf(f, 0) || math.IsNaN(f) {
				return nil // JSON.stringify's representation of non-finite values.
			}
			b, _ := json.Marshal(f)
			return json.Number(b)
		case map[string]any:
			for k, value := range x {
				x[k] = round(value)
			}
		case []any:
			for i, value := range x {
				x[i] = round(value)
			}
		}
		return v
	}
	round(result)
	d.Value = result
	return d, nil
}

func browserDocument(source string, scripting bool) (*html.Node, error) {
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(scripting))
	if err != nil {
		return nil, ErrEmbedded
	}
	// Browser document selectors cannot see template DocumentFragments.
	for _, node := range cascadia.QueryAll(tree, cascadia.MustCompile("template")) {
		if node.Namespace == "" {
			for node.FirstChild != nil {
				node.RemoveChild(node.FirstChild)
			}
		}
	}
	return tree, nil
}
