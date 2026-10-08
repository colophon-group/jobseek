package apisniffer

import (
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type NotionPage struct {
	ID, Title  string
	Properties map[string]string
}
type NotionCollection struct{ ID, ViewID string }

func NotionChildPages(d *Document, parent string, nested bool) ([]NotionPage, error) {
	blocks := notionRecords(d, "block")
	root := notionRecord(blocks[parent])
	content, _ := root["content"].([]any)
	pending := []string{}
	for i := len(content) - 1; i >= 0; i-- {
		if id, ok := content[i].(string); ok {
			pending = append(pending, id)
		}
	}
	pages := []NotionPage{}
	seen := map[string]bool{}
	for len(pending) > 0 {
		id := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		if seen[id] {
			continue
		}
		if len(seen) >= 10000 {
			return nil, ErrInventory
		}
		seen[id] = true
		raw, exists := blocks[id]
		if !exists {
			return nil, ErrInventory
		}
		record := notionRecord(raw)
		if len(record) == 0 {
			return nil, ErrInventory
		}
		if alive, exists := record["alive"]; exists && alive == false {
			continue
		}
		if record["type"] == "page" {
			if !notionUUID.MatchString(id) || !notionRichTextValid(asEmbeddedObject(record["properties"])["title"]) {
				return nil, ErrInventory
			}
			pages = append(pages, NotionPage{ID: id, Title: notionTitle(record)})
			if !nested {
				continue
			}
		}
		content, _ := record["content"].([]any)
		for i := len(content) - 1; i >= 0; i-- {
			if id, ok := content[i].(string); ok {
				pending = append(pending, id)
			}
		}
	}
	return pages, nil
}

func NotionCollectionViews(d *Document) ([]NotionCollection, error) {
	blocks := notionRecords(d, "block")
	out := []NotionCollection{}
	for _, id := range d.ObjectKeys(blocks) {
		record := notionRecord(blocks[id])
		if record["type"] != "collection_view" && record["type"] != "collection_view_page" {
			continue
		}
		collection, _ := record["collection_id"].(string)
		views, _ := record["view_ids"].([]any)
		if collection != "" && len(views) > 0 {
			view, ok := views[0].(string)
			if !ok || !notionUUID.MatchString(collection) || !notionUUID.MatchString(view) {
				return nil, ErrInventory
			}
			out = append(out, NotionCollection{collection, view})
		}
	}
	if len(out) > 10000 {
		return nil, ErrInventory
	}
	return out, nil
}

func NotionFilterPages(pages []NotionPage, o NotionOptions, collection bool) ([]NotionPage, error) {
	out := []NotionPage{}
	for _, page := range pages {
		if o.TitleExclude != "" {
			pattern, err := dom.CompileURLPattern("(?i)" + o.TitleExclude)
			if err != nil {
				return nil, err
			}
			match, err := pattern.FindStringMatch(page.Title)
			if err != nil {
				return nil, err
			}
			if match != nil {
				continue
			}
		}
		keep := true
		if collection {
			for name, value := range o.Exclude {
				if strings.ToLower(page.Properties[name]) == strings.ToLower(value) {
					keep = false
				}
			}
			for name, value := range o.Include {
				if strings.ToLower(page.Properties[name]) != strings.ToLower(value) {
					keep = false
				}
			}
		}
		if keep {
			out = append(out, page)
		}
	}
	return out, nil
}

func NotionCollectionRows(d *Document) ([]NotionPage, error) {
	root := asEmbeddedObject(d.Value)
	result := asEmbeddedObject(asEmbeddedObject(asEmbeddedObject(root["result"])["reducerResults"])["collection_group_results"])
	ids, ok := result["blockIds"].([]any)
	if !ok && result["blockIds"] != nil {
		return nil, ErrInventory
	}
	if len(ids) >= 300 || result["hasMore"] == true {
		return nil, ErrInventory
	}
	blocks := notionRecords(d, "block")
	schema := notionSchema(d)
	out := []NotionPage{}
	for _, id := range ids {
		key, ok := id.(string)
		if !ok || !notionUUID.MatchString(key) {
			return nil, ErrInventory
		}
		raw, exists := blocks[key]
		if !exists {
			return nil, ErrInventory
		}
		record := notionRecord(raw)
		if !notionRichTextValid(asEmbeddedObject(record["properties"])["title"]) {
			return nil, ErrInventory
		}
		properties := map[string]string{}
		source := asEmbeddedObject(record["properties"])
		for _, id := range notionSchemaKeys(d) {
			property := schema[id]
			if id == "title" {
				continue
			}
			text := notionPlain(source[id], false)
			if strings.TrimSpace(text) != "" {
				properties[property.Name] = text
			}
		}
		out = append(out, NotionPage{key, notionTitle(record), properties})
	}
	return out, nil
}
