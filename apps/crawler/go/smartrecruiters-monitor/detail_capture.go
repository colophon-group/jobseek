package smartrecruiters

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var captureBoardID = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

// Four exclusive, private snapshots per exact selected board and worker. Only
// bytes from the existing, policy-checked response enter this callback.
func detailCapture(boardID, sourceURL, dir string) func(string, []byte) {
	if !captureBoardID.MatchString(boardID) {
		return nil
	}
	selected := false
	for _, id := range strings.Split(os.Getenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS"), ",") {
		selected = selected || strings.TrimSpace(id) == boardID
	}
	if !selected {
		return nil
	}
	return func(endpoint string, body []byte) {
		if len(body) > DetailResponseMaxBytes {
			return
		}
		digest := sha256.Sum256(body)
		payload, err := json.Marshal(map[string]any{
			"board_id": boardID, "url": sourceURL, "endpoint": endpoint,
			"body_base64": base64.StdEncoding.EncodeToString(body),
			"body_sha256": fmt.Sprintf("%x", digest),
		})
		if err != nil {
			return
		}
		for slot := 1; slot <= 4; slot++ {
			path := filepath.Join(dir, fmt.Sprintf("jobseek-smartrecruiters-go-detail-%s-%d.json", boardID, slot))
			f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if os.IsExist(err) {
				info, statErr := os.Lstat(path)
				if statErr != nil || !info.Mode().IsRegular() || info.Size() > 2*DetailResponseMaxBytes {
					continue
				}
				stored, readErr := os.ReadFile(path)
				var old struct {
					URL string `json:"url"`
				}
				if readErr == nil && json.Unmarshal(stored, &old) == nil && old.URL == sourceURL {
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
}
