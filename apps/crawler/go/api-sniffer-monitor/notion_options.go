package apisniffer

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type NotionOptions struct {
	Subdomain, Hint, BoardURL, TitleExclude string
	Nested                                  bool
	CollectionIndex                         *int
	URLFilter                               any
	Include, Exclude                        map[string]string
}

var notionSubdomain = regexp.MustCompile(`^[\pL\pN_-]{1,128}$`)
var notionUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var notionNonHex = regexp.MustCompile(`[^0-9a-fA-F]`)

func NotionURL(raw string) (string, string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || !strings.HasSuffix(u.Host, ".notion.site") || len(raw) > 8192 {
		return "", "", ErrOptions
	}
	sub := strings.TrimSuffix(u.Host, ".notion.site")
	if !notionSubdomain.MatchString(sub) {
		return "", "", ErrOptions
	}
	path := strings.Trim(u.EscapedPath(), "/")
	parts := strings.Split(path, "/")
	hint := parts[len(parts)-1]
	hex := notionNonHex.ReplaceAllString(hint, "")
	if len(hex) >= 32 {
		raw := hex[len(hex)-32:]
		hint = raw[:8] + "-" + raw[8:12] + "-" + raw[12:16] + "-" + raw[16:20] + "-" + raw[20:]
	}
	return sub, hint, nil
}

func NotionOptionsFromMetadata(board, raw string) (NotionOptions, error) {
	o := NotionOptions{BoardURL: board, Include: map[string]string{}, Exclude: map[string]string{}}
	var err error
	o.Subdomain, o.Hint, err = NotionURL(board)
	if err != nil {
		return o, err
	}
	if raw == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	if err != nil {
		return o, err
	}
	for key, value := range m {
		switch key {
		case "include_nested":
			v, ok := value.(bool)
			if !ok {
				return o, ErrOptions
			}
			o.Nested = v
		case "collection_index":
			if value != nil {
				n, ok := value.(json.Number)
				if !ok {
					return o, ErrOptions
				}
				v, err := n.Int64()
				if err != nil || v < 0 || v > 10000 {
					return o, ErrOptions
				}
				index := int(v)
				o.CollectionIndex = &index
			}
		case "title_exclude":
			if value != nil {
				text, ok := value.(string)
				if !ok || len(text) > 8192 {
					return o, ErrOptions
				}
				if _, err := dom.CompileURLPattern("(?i)" + text); err != nil {
					return o, ErrOptions
				}
				o.TitleExclude = text
			}
		case "url_filter":
			o.URLFilter = value
			if value != nil {
				switch filter := value.(type) {
				case string:
					if _, err := dom.CompileURLPattern(filter); err != nil || len(filter) > 8192 {
						return o, ErrOptions
					}
				case map[string]any:
					for key, value := range filter {
						if key != "include" && key != "exclude" {
							return o, ErrOptions
						}
						text, ok := value.(string)
						if !ok || len(text) > 8192 {
							return o, ErrOptions
						}
						if _, err := dom.CompileURLPattern(text); err != nil {
							return o, ErrOptions
						}
					}
				default:
					return o, ErrOptions
				}
			}
		case "property_filter":
			if value != nil {
				filter, ok := value.(map[string]any)
				if !ok {
					return o, ErrOptions
				}
				for key, value := range filter {
					target := o.Include
					if key == "exclude" {
						target = o.Exclude
					} else if key != "include" {
						return o, ErrOptions
					}
					rules, ok := value.(map[string]any)
					if !ok || len(rules) > 128 {
						return o, ErrOptions
					}
					for key, value := range rules {
						text, ok := value.(string)
						if !ok || len(key) > 256 || len(text) > 8192 {
							return o, ErrOptions
						}
						target[key] = text
					}
				}
			}
		case "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor":
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}

func NotionPageURL(subdomain, id string) (string, error) {
	if !notionSubdomain.MatchString(subdomain) || !notionUUID.MatchString(id) {
		return "", ErrInventory
	}
	return "https://" + subdomain + ".notion.site/" + strings.ReplaceAll(id, "-", ""), nil
}
func (o NotionOptions) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	if u.Host == "www.notion.so" {
		return u.Path == "/api/v3/getPublicPageData"
	}
	if u.Host != o.Subdomain+".notion.site" {
		return false
	}
	switch u.Path {
	case "/api/v3/getPublicPageData", "/api/v3/loadPageChunk", "/api/v3/queryCollection":
		return true
	}
	_, id, err := NotionURL(raw)
	return err == nil && notionUUID.MatchString(id)
}
