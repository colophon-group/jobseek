package join

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// Passive snapshots only: callers supply an already fetched, policy-checked
// response. Exclusive mode0600 creation never overwrites a retained response.
func captureText(directory, rawURL string, body []byte, detail bool) {
	u, err := url.Parse(rawURL)
	if err != nil || len(body) > maxResponseBytes {
		return
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) < 2 || parts[0] != "companies" || !slugRE.MatchString(parts[1]) {
		return
	}
	selected := false
	for _, slug := range strings.Split(os.Getenv("JOIN_CAPTURE_SLUGS"), ",") {
		selected = selected || strings.TrimSpace(slug) == parts[1]
	}
	if !selected {
		return
	}
	if detail {
		// Four retained jobs per selected slug, including the exact source URL.
		digest := sha256.Sum256(body)
		payload, err := json.Marshal(map[string]any{"url": rawURL, "body_base64": base64.StdEncoding.EncodeToString(body), "body_sha256": fmt.Sprintf("%x", digest)})
		if err != nil {
			return
		}
		for slot := 1; slot <= 4; slot++ {
			path := filepath.Join(directory, fmt.Sprintf("jobseek-join-go-detail-%s-%d.json", parts[1], slot))
			f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if os.IsExist(err) {
				// Do not consume another slot for the same job on a later cycle.
				stored, readErr := os.ReadFile(path)
				var old struct {
					URL string `json:"url"`
				}
				if readErr == nil && json.Unmarshal(stored, &old) == nil && old.URL == rawURL {
					return
				}
				continue
			}
			if err != nil {
				return
			}
			_, _ = f.Write(payload)
			_ = f.Close()
			return
		}
		return
	}
	page := u.Query().Get("page")
	if page == "" {
		page = "1"
	}
	if page != "1" && page != "2" {
		return
	}
	path := filepath.Join(directory, fmt.Sprintf("jobseek-join-go-%s-%s.html", parts[1], page))
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return
	}
	_, _ = f.Write(body)
	_ = f.Close()
}
