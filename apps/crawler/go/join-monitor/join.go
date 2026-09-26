package join

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"unicode/utf8"
)

const MaxPages = 10_000
const MaxURLs = 50_000

var slugRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var nextDataRE = regexp.MustCompile(`(?s)<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>`)

type Page struct {
	URLs      []string `json:"urls"`
	PageCount int      `json:"page_count"`
}

func ValidateBoard(boardURL, slug string) error {
	if !slugRE.MatchString(slug) {
		return errors.New("JOIN slug is not canonical")
	}
	parsed, err := url.Parse(boardURL)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Port() != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return errors.New("JOIN board URL is not canonical HTTPS")
	}
	if parsed.Hostname() != "join.com" && parsed.Hostname() != "www.join.com" {
		return errors.New("JOIN board URL has an unsupported host")
	}
	if parsed.Path != "/companies/"+slug && parsed.Path != "/companies/"+slug+"/" {
		return errors.New("JOIN board URL does not match its slug")
	}
	return nil
}

func PageURL(boardURL string, page int) (string, error) {
	if page < 2 || page > MaxPages {
		return "", errors.New("JOIN page number exceeds the bound")
	}
	parsed, err := url.Parse(boardURL)
	if err != nil {
		return "", err
	}
	query := parsed.Query()
	query.Set("page", strconv.Itoa(page))
	parsed.RawQuery = query.Encode()
	return parsed.String(), nil
}

func field(object map[string]any, path ...string) any {
	var value any = object
	for _, part := range path {
		node, ok := value.(map[string]any)
		if !ok {
			return nil
		}
		value = node[part]
	}
	return value
}

func pageCount(value any) (int, error) {
	switch raw := value.(type) {
	case json.Number:
		if n, err := raw.Int64(); err == nil && n >= 0 && n <= MaxPages {
			return int(n), nil
		}
	case string:
		if n, err := strconv.Atoi(raw); err == nil && n >= 0 && n <= MaxPages {
			return n, nil
		}
	}
	return 0, errors.New("JOIN pageCount is missing or unsupported")
}

// ParsePage extracts exactly the URL-only fields used by the configured Python
// JOIN wrapper. It fails closed on missing required pagination data.
func ParsePage(html []byte, slug string, first bool) (Page, error) {
	if !utf8.Valid(html) || utf8.RuneCount(html) > 4_000_000 {
		return Page{}, errors.New("JOIN page exceeded the Python HTML bound")
	}
	match := nextDataRE.FindSubmatch(html)
	if len(match) != 2 {
		return Page{}, errors.New("JOIN page has no __NEXT_DATA__ script")
	}
	decoder := json.NewDecoder(bytes.NewReader(match[1]))
	decoder.UseNumber()
	var root map[string]any
	if err := decoder.Decode(&root); err != nil {
		return Page{}, fmt.Errorf("JOIN embedded JSON is invalid: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Page{}, errors.New("JOIN embedded JSON has trailing content")
	}
	items, ok := field(root, "props", "pageProps", "initialState", "jobs", "items").([]any)
	if !ok {
		return Page{}, errors.New("JOIN items path did not resolve to an array")
	}
	count := 0
	if first {
		var err error
		count, err = pageCount(field(root, "props", "pageProps", "initialState", "jobs", "pagination", "pageCount"))
		if err != nil {
			return Page{}, err
		}
	}
	if first && len(items) == 0 && count > 1 {
		return Page{}, errors.New("JOIN first page was empty with more pages advertised")
	}
	if !first && len(items) == 0 {
		return Page{}, errors.New("JOIN required page was empty")
	}
	urls := make([]string, 0, len(items))
	for _, item := range items {
		object, ok := item.(map[string]any)
		if !ok {
			continue
		}
		var id string
		switch raw := object["idParam"].(type) {
		case string:
			id = raw
		case json.Number:
			id = raw.String()
		default:
			continue
		}
		urls = append(urls, "https://join.com/companies/"+slug+"/"+id)
	}
	return Page{URLs: urls, PageCount: count}, nil
}

func UniqueSorted(pages []Page) ([]string, error) {
	seen := make(map[string]struct{})
	for _, page := range pages {
		for _, jobURL := range page.URLs {
			seen[jobURL] = struct{}{}
			if len(seen) > MaxURLs {
				return nil, errors.New("JOIN URL inventory exceeded the bound")
			}
		}
	}
	urls := make([]string, 0, len(seen))
	for jobURL := range seen {
		urls = append(urls, jobURL)
	}
	sort.Strings(urls)
	return urls, nil
}
