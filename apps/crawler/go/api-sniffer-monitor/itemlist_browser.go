package apisniffer

import (
	"encoding/json"
	"golang.org/x/net/html"
	"math"
	"reflect"
	"regexp"
	"strings"
	"unicode/utf8"
)

// ItemListBrowserExpression is the existing Miniso listing projection. Only
// this complete expression is admitted; publisher text never selects code.
const ItemListBrowserExpression = "({jobs:Array.from(document.scripts).filter(s=>s.type===\"application/ld+json\").map(s=>JSON.parse(s.textContent)).find(x=>x[\"@type\"]===\"ItemList\").itemListElement.filter(j=>!/pool de talentos/i.test(j.name))})"

var itemListTalentPool = regexp.MustCompile(`[pP][oO][oO][lL] [dD][eE] [tT][aA][lL][eE][nN][tT][oO][sS]`)

func ParseItemListBrowserDocument(source string) (*Document, error) {
	if len(source) > 16<<20 || !utf8.ValidString(source) {
		return nil, ErrEmbedded
	}
	tree, err := browserDocument(source, true)
	if err != nil {
		return nil, err
	}
	documents := []*Document{}
	// Array.map parses every matching script before Array.find selects a list.
	for _, script := range inlineSelectedNodes(tree, "script") {
		kind, _ := lastDetailAttribute(script, "type")
		if kind != "application/ld+json" {
			continue
		}
		var text strings.Builder
		for child := script.FirstChild; child != nil; child = child.NextSibling {
			if child.Type == html.TextNode {
				text.WriteString(child.Data)
			}
		}
		d, e := Decode([]byte(text.String()))
		if e != nil {
			return nil, ErrEmbedded
		}
		documents = append(documents, d)
	}
	for _, d := range documents {
		if d.Value == nil {
			return nil, ErrEmbedded
		}
		root, ok := d.Value.(map[string]any)
		if !ok || root["@type"] != "ItemList" {
			continue
		}
		items, ok := root["itemListElement"].([]any)
		if !ok {
			return nil, ErrEmbedded
		}
		rows := make([]any, 0, len(items))
		for _, item := range items {
			if item == nil {
				return nil, ErrEmbedded
			}
			row, ok := item.(map[string]any)
			if !ok {
				return nil, ErrEmbedded
			}
			name, ok := row["name"].(string)
			if ok && itemListTalentPool.MatchString(name) {
				continue
			}
			rows = append(rows, item)
		}
		// JSON.parse and the original browser serialization round numbers to IEEE754.
		var round func(any) any
		round = func(value any) any {
			switch v := value.(type) {
			case json.Number:
				f, _ := v.Float64()
				if math.IsInf(f, 0) || math.IsNaN(f) {
					return nil
				}
				raw, _ := json.Marshal(f)
				return json.Number(raw)
			case map[string]any:
				for k, x := range v {
					v[k] = round(x)
				}
			case []any:
				for n, x := range v {
					v[n] = round(x)
				}
			}
			return value
		}
		result := map[string]any{"jobs": rows}
		round(result)
		d.order[reflect.ValueOf(result)] = []string{"jobs"}
		d.Value = result
		return d, nil
	}
	return nil, ErrEmbedded
}
