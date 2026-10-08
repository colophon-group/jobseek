package apisniffer

import (
	"net/url"
	"sort"
	"strings"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

func UnifrAccordionItems(body []byte, o UnifrOptions) (map[string]UnifrJob, error) {
	tree, err := eighthTree(body)
	if err != nil {
		return nil, err
	}
	if o.Kind != "accordion" || unifrPage(tree, o.Suffix, o.Heading) != nil {
		return nil, ErrInventory
	}
	lists := eighthSelect(tree, o.Selector)
	if len(lists) != 1 {
		return nil, ErrInventory
	}
	controls := eighthSelect(lists[0], "a[data-accordion-toggler]")
	if len(controls) == 0 || len(controls) > 50 {
		return nil, ErrInventory
	}
	out := map[string]UnifrJob{}
	for _, control := range controls {
		id := eighthAttr(control, "data-accordion-toggler")
		if id == "" || len(id) > 128 {
			return nil, ErrInventory
		}
		if _, ok := out[id]; ok {
			return nil, ErrInventory
		}
		// Compare attribute values directly: provider IDs are data, not selector code.
		panels := eighthSelect(lists[0], "div[data-accordion-content]")
		matches := 0
		description := ""
		for _, panel := range panels {
			if eighthAttr(panel, "data-accordion-content") == id {
				matches++
				if unifrText(panel) == "" {
					return nil, ErrInventory
				}
				description, err = dom.OuterHTML(panel)
				if err != nil {
					return nil, err
				}
			}
		}
		title := unifrText(control)
		if matches != 1 || title == "" {
			return nil, ErrInventory
		}
		source, err := unifrJobURL(o.URL, id)
		if err != nil {
			return nil, err
		}
		out[id] = UnifrJob{URL: source, Title: title, Description: description, Locations: []string{"Fribourg, Switzerland"}, Metadata: map[string]any{"unifr_source_id": id}}
	}
	if len(out) != len(o.ExpectedIDs) {
		return nil, ErrInventory
	}
	for _, id := range o.ExpectedIDs {
		if _, ok := out[id]; !ok {
			return nil, ErrInventory
		}
	}
	return out, nil
}

func UnifrAccordionJobs(items map[string]UnifrJob, o UnifrOptions, centralIDs map[string]bool, today time.Time) ([]UnifrJob, error) {
	for _, id := range o.ExcludedCentralIDs {
		if !centralIDs[id] {
			return nil, ErrInventory
		}
	}
	ids := make([]string, 0, len(items))
	for id := range items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	contains := func(values []string, wanted string) bool {
		for _, v := range values {
			if v == wanted {
				return true
			}
		}
		return false
	}
	out := []UnifrJob{}
	for _, id := range ids {
		if _, excluded := o.ExcludedCentralIDs[id]; excluded {
			continue
		}
		job := items[id]
		tree, err := eighthTree([]byte(job.Description))
		if err != nil {
			return nil, err
		}
		plain := unifrText(tree)
		deadline, err := UnifrDeadline(plain)
		if err != nil || deadline == "" && contains(o.DeadlineRequired, id) {
			return nil, ErrInventory
		}
		if contains(o.ImmediatelyAvailable, id) && !strings.Contains(plain, "available immediately") {
			return nil, ErrInventory
		}
		if deadline != "" && deadline < today.UTC().Format("2006-01-02") {
			continue
		}
		job.Language = "en"
		if strings.Contains(o.URL, "/de/") {
			job.Language = "de"
		}
		if deadline != "" {
			job.Extras = map[string]any{"valid_through": deadline}
		}
		out = append(out, job)
	}
	return out, nil
}

func UnifrLinkInventory(body []byte, o UnifrOptions) ([]string, error) {
	tree, err := eighthTree(body)
	if err != nil {
		return nil, err
	}
	if o.Kind != "links" || unifrPage(tree, o.Suffix, o.Heading) != nil {
		return nil, ErrInventory
	}
	base, err := url.Parse(o.URL)
	if err != nil {
		return nil, ErrOptions
	}
	seen := map[string]bool{}
	nodes := 0
	for _, link := range eighthSelect(tree, "main a[href]") {
		raw := eighthAttr(link, "href")
		relative, err := url.Parse(raw)
		if err != nil {
			return nil, ErrInventory
		}
		resource := base.ResolveReference(relative)
		if !strings.HasSuffix(strings.ToLower(resource.Path), ".pdf") {
			continue
		}
		nodes++
		value := resource.String()
		if o.RewriteFrom != "" && o.RewriteTo != "" {
			value = strings.ReplaceAll(value, o.RewriteFrom, o.RewriteTo)
			resource, err = url.Parse(value)
			if err != nil {
				return nil, ErrInventory
			}
		}
		if raw == "" || resource.Scheme != "https" || resource.Hostname() != "www.unifr.ch" || !strings.HasPrefix(resource.Path, o.PathPrefix) || resource.RawQuery != "" || resource.Fragment != "" || seen[value] {
			return nil, ErrInventory
		}
		seen[value] = true
	}
	if nodes == 0 || nodes > 50 {
		return nil, ErrInventory
	}
	out := make([]string, 0, len(seen))
	for value := range seen {
		out = append(out, value)
	}
	sort.Strings(out)
	return out, nil
}
