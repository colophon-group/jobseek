package apisniffer

import (
	"strings"

	"html"
)

// Public Notion responses wrap records once or twice depending on endpoint.
func notionRecord(value any) map[string]any {
	outer := asEmbeddedObject(value)
	first := asEmbeddedObject(outer["value"])
	if second := asEmbeddedObject(first["value"]); len(second) > 0 {
		return second
	}
	return first
}
func notionRecords(d *Document, kind string) map[string]any {
	if d == nil {
		return nil
	}
	return asEmbeddedObject(asEmbeddedObject(asEmbeddedObject(d.Value)["recordMap"])[kind])
}
func notionPlain(value any, coerce bool, documents ...*Document) string {
	parts, _ := value.([]any)
	var out strings.Builder
	for _, part := range parts {
		row, ok := part.([]any)
		if !ok || len(row) == 0 {
			continue
		}
		if text, ok := row[0].(string); ok {
			out.WriteString(text)
		} else if coerce {
			d := new(Document)
			if len(documents) > 0 {
				d = documents[0]
			}
			text, _ := d.String(row[0])
			out.WriteString(text)
		}
	}
	return out.String()
}
func notionTitle(record map[string]any) string {
	return notionPlain(asEmbeddedObject(record["properties"])["title"], false)
}

func NotionRichText(value any) string {
	parts, _ := value.([]any)
	var out strings.Builder
	for _, part := range parts {
		segment, ok := part.([]any)
		if !ok || len(segment) == 0 {
			continue
		}
		raw, ok := segment[0].(string)
		if !ok {
			continue
		}
		text := notionEscape(raw)
		if len(segment) > 1 {
			annotations, _ := segment[1].([]any)
			for _, a := range annotations {
				annotation, ok := a.([]any)
				if !ok || len(annotation) == 0 {
					continue
				}
				switch annotation[0] {
				case "b":
					text = "<strong>" + text + "</strong>"
				case "i":
					text = "<em>" + text + "</em>"
				case "a":
					if len(annotation) > 1 {
						if href, ok := annotation[1].(string); ok {
							text = `<a href="` + notionEscape(href) + `">` + text + `</a>`
						}
					}
				}
			}
		}
		out.WriteString(text)
	}
	return out.String()
}

var notionBlockTags = map[string]string{"header": "h1", "sub_header": "h2", "sub_sub_header": "h3", "text": "p", "quote": "blockquote", "callout": "div"}

func notionRichTextValid(value any) bool {
	parts, _ := value.([]any)
	for _, part := range parts {
		segment, ok := part.([]any)
		if !ok || len(segment) == 0 {
			continue
		}
		if _, ok := segment[0].(string); !ok {
			return false
		}
		if len(segment) > 1 {
			annotations, _ := segment[1].([]any)
			for _, a := range annotations {
				annotation, ok := a.([]any)
				if !ok || len(annotation) == 0 {
					continue
				}
				if annotation[0] == "a" && len(annotation) > 1 {
					if _, ok := annotation[1].(string); !ok {
						return false
					}
				}
			}
		}
	}
	return true
}

func NotionBlocksHTML(d *Document, pageID string) (string, error) {
	blocks := notionRecords(d, "block")
	page := notionRecord(blocks[pageID])
	content, ok := page["content"].([]any)
	if !ok && page["content"] != nil {
		return "", ErrField
	}
	if len(content) > 10000 {
		return "", ErrField
	}
	parts := []string{}
	inList := false
	textOf := func(record map[string]any) string {
		return NotionRichText(asEmbeddedObject(record["properties"])["title"])
	}
	for _, id := range content {
		key, ok := id.(string)
		if !ok {
			return "", ErrField
		}
		raw, exists := blocks[key]
		if !exists {
			return "", ErrField
		}
		record := notionRecord(raw)
		kind, _ := record["type"].(string)
		if !notionRichTextValid(asEmbeddedObject(record["properties"])["title"]) {
			return "", ErrField
		}
		text := textOf(record)
		if kind != "bulleted_list" && kind != "numbered_list" && inList {
			parts = append(parts, "</ul>")
			inList = false
		}
		if tag, ok := notionBlockTags[kind]; ok {
			if strings.TrimSpace(text) != "" {
				parts = append(parts, "<"+tag+">"+text+"</"+tag+">")
			}
			continue
		}
		switch kind {
		case "bulleted_list", "numbered_list":
			if !inList {
				parts = append(parts, "<ul>")
				inList = true
			}
			parts = append(parts, "<li>"+text+"</li>")
		case "divider":
			parts = append(parts, "<hr>")
		case "toggle":
			if strings.TrimSpace(text) != "" {
				parts = append(parts, "<h3>"+text+"</h3>")
			}
			children, _ := record["content"].([]any)
			if len(children) > 10000 {
				return "", ErrField
			}
			for _, id := range children {
				key, ok := id.(string)
				if !ok {
					return "", ErrField
				}
				raw, exists := blocks[key]
				if !exists {
					return "", ErrField
				}
				child := notionRecord(raw)
				kind, _ := child["type"].(string)
				if !notionRichTextValid(asEmbeddedObject(child["properties"])["title"]) {
					return "", ErrField
				}
				text := textOf(child)
				if strings.TrimSpace(text) == "" {
					continue
				}
				if kind == "bulleted_list" || kind == "numbered_list" {
					if !inList {
						parts = append(parts, "<ul>")
						inList = true
					}
					parts = append(parts, "<li>"+text+"</li>")
				} else {
					tag := notionBlockTags[kind]
					if tag == "" {
						tag = "p"
					}
					parts = append(parts, "<"+tag+">"+text+"</"+tag+">")
				}
			}
		}
	}
	if inList {
		parts = append(parts, "</ul>")
	}
	return strings.Join(parts, "\n"), nil
}

type notionProperty struct{ Name, Kind string }

func notionSchema(d *Document) map[string]notionProperty {
	collections := notionRecords(d, "collection")
	schema := map[string]notionProperty{}
	for _, id := range d.ObjectKeys(collections) {
		properties := asEmbeddedObject(notionRecord(collections[id])["schema"])
		for _, key := range d.ObjectKeys(properties) {
			property := asEmbeddedObject(properties[key])
			name, _ := property["name"].(string)
			kind, _ := property["type"].(string)
			if name != "" {
				schema[key] = notionProperty{name, kind}
			}
		}
	}
	return schema
}

var notionPropertyMap = map[string]string{"location": "locations", "locations": "locations", "city": "locations", "office": "locations", "department": "metadata.team", "team": "metadata.team", "employment type": "employment_type", "type": "employment_type", "contract type": "employment_type", "remote": "job_location_type", "work model": "job_location_type"}

func notionSchemaKeys(d *Document) []string {
	schema := notionSchema(d)
	ordered := []string{}
	seen := map[string]bool{}
	collections := notionRecords(d, "collection")
	for _, id := range d.ObjectKeys(collections) {
		source := asEmbeddedObject(notionRecord(collections[id])["schema"])
		for _, key := range d.ObjectKeys(source) {
			if !seen[key] && schema[key].Name != "" {
				ordered = append(ordered, key)
				seen[key] = true
			}
		}
	}
	return ordered
}

// NotionDetailFields preserves the reference's first-layer and toggle rendering,
// including its list boundaries and property precedence. Missing referenced
// blocks fail closed instead of writing a partial description.
func NotionDetailFields(d *Document, pageID string, propertyMap map[string]string) (map[string]any, error) {
	page := notionRecord(notionRecords(d, "block")[pageID])
	if len(page) == 0 {
		return nil, ErrField
	}
	if !notionRichTextValid(asEmbeddedObject(page["properties"])["title"]) {
		return nil, ErrField
	}
	html, err := NotionBlocksHTML(d, pageID)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"title": notionTitle(page), "description": html, "metadata": map[string]any{}}
	mapping := map[string]string{}
	for k, v := range notionPropertyMap {
		mapping[k] = v
	}
	for k, v := range propertyMap {
		mapping[strings.ToLower(k)] = v
	}
	schema := notionSchema(d)
	properties := asEmbeddedObject(page["properties"])
	// The JSON schema order decides precedence when properties target one field.
	ordered := []string{}
	seen := map[string]bool{}
	collections := notionRecords(d, "collection")
	for _, id := range d.ObjectKeys(collections) {
		source := asEmbeddedObject(notionRecord(collections[id])["schema"])
		for _, key := range d.ObjectKeys(source) {
			if !seen[key] && schema[key].Name != "" {
				ordered = append(ordered, key)
				seen[key] = true
			}
		}
	}
	for _, key := range ordered {
		if key == "title" {
			continue
		}
		property := schema[key]
		field := mapping[strings.ToLower(property.Name)]
		if field == "" {
			continue
		}
		text := notionPlain(properties[key], true, d)
		if strings.TrimSpace(text) == "" {
			continue
		}
		if field == "locations" {
			if property.Kind == "multi_select" {
				values := []string{}
				for _, part := range strings.Split(text, ",") {
					if value := strings.TrimSpace(part); value != "" {
						values = append(values, value)
					}
				}
				out[field] = values
			} else {
				out[field] = text
			}
		} else if field == "metadata.team" {
			out["metadata"].(map[string]any)["team"] = text
		} else if field == "employment_type" || field == "job_location_type" {
			out[field] = text
		}
	}
	if _, ok := out["locations"].(string); ok {
		delete(out, "locations")
	}
	return out, nil
}

func notionEscape(value string) string {
	return strings.NewReplacer("&#39;", "&#x27;", "&#34;", "&quot;").Replace(html.EscapeString(value))
}
