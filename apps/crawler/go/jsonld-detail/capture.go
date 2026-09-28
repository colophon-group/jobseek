package jsonld

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
)

// Retain only already fetched and policy-checked parser input, four jobs per
// explicitly selected host. Never fetch, overwrite, or emit credentials.
func captureText(rawURL string, body []byte) {
	u, err := url.Parse(rawURL)
	if err != nil || len(body) > maxResponseBytes {
		return
	}
	host := strings.ToLower(u.Hostname())
	selected := false
	for _, h := range strings.Split(os.Getenv("JSONLD_CAPTURE_HOSTS"), ",") {
		selected = selected || trim(h) == host
	}
	if !selected {
		return
	}
	digest := sha256.Sum256(body)
	hostDigest := sha256.Sum256([]byte(host))
	payload, err := json.Marshal(map[string]any{"url": rawURL, "encoding": "utf-8", "body_base64": base64.StdEncoding.EncodeToString(body), "body_sha256": fmt.Sprintf("%x", digest)})
	if err != nil {
		return
	}
	for slot := 1; slot <= 4; slot++ {
		path := fmt.Sprintf("/tmp/jobseek-jsonld-go-detail-%x-%d.json", hostDigest[:8], slot)
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if os.IsExist(err) {
			stored, err := os.ReadFile(path)
			var old struct {
				URL string `json:"url"`
			}
			if err == nil && json.Unmarshal(stored, &old) == nil && old.URL == rawURL {
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
}
