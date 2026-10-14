package apisniffer

import (
	stdhtml "html"
	"net/url"
	"regexp"
	"strings"

	"golang.org/x/net/html"
)

func ValidateDOMDocumentFallback(raw any) error {
	if raw == nil || raw == false {
		return nil
	}
	formats, ok := raw.(map[string]any)
	if !ok {
		return ErrOptions
	}
	for kind, value := range formats {
		config, ok := value.(map[string]any)
		if !ok {
			return ErrOptions
		}
		if kind != "pdf" && kind != "doc" && kind != "docx" {
			return ErrOptions
		}
		if kind != "pdf" {
			for key := range config {
				if key != "title_source" && key != "title_pattern" && key != "location_pattern" && key != "defaults" {
					return ErrOptions
				}
			}
		}
		if _, err := PDFOptionsFromConfig(config); err != nil {
			return err
		}
	}
	return nil
}

// Word uses the same bounded regex/default helpers as PDF, while preserving
// its original HTML and its distinct filename/title fallback behavior.
func ProjectDOMWord(text, description, source string, config map[string]any, docx bool) (map[string]any, error) {
	o, err := PDFOptionsFromConfig(config)
	if err != nil {
		return nil, err
	}
	title, err := pdfCapture(text, o.TitlePattern, "", false)
	if err != nil {
		return nil, err
	}
	if title == "" && o.TitleSource == "text" {
		title = pdfHeading(text)
	}
	if title == "" && !docx {
		u, err := url.Parse(source)
		if err != nil {
			return nil, err
		}
		title = u.Path[strings.LastIndex(u.Path, "/")+1:]
		title = regexp.MustCompile(`(?i)[.]doc$`).ReplaceAllString(title, "")
		title = strings.Join(strings.Fields(regexp.MustCompile(`[_-]+`).ReplaceAllString(title, " ")), " ")
	}
	location, err := pdfCapture(text, o.LocationPattern, "", false)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"description": description}
	if title != "" {
		out["title"] = title
	}
	if location != "" {
		out["locations"] = []any{location}
	}
	for k, v := range o.Defaults {
		if out[k] == nil || out[k] == "" {
			out[k] = v
		}
	}
	return out, nil
}

func DOMWordHTMLText(description string) (string, error) {
	root, err := html.Parse(strings.NewReader(description))
	if err != nil {
		return "", err
	}
	parts := []string{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			if text := strings.TrimSpace(n.Data); text != "" {
				parts = append(parts, text)
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(root)
	return strings.Join(parts, "\n"), nil
}
func DOMLegacyWordHTML(text string) string {
	paragraphs := []string{}
	current := []string{}
	flush := func() {
		if len(current) > 0 {
			p := stdhtml.EscapeString(strings.Join(current, " "))
			p = strings.ReplaceAll(p, "&#39;", "&#x27;")
			p = strings.ReplaceAll(p, "&#34;", "&quot;")
			paragraphs = append(paragraphs, "<p>"+p+"</p>")
			current = nil
		}
	}
	for _, line := range strings.Split(text, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			current = append(current, strings.Join(strings.Fields(line), " "))
		} else {
			flush()
		}
	}
	flush()
	return strings.Join(paragraphs, "\n")
}
