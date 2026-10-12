package queue

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// This source's original three-job inventory matches a self-identifying
// Lightpanda document. Keep its original configuration in the ownership hash;
// only this exact parser/navigation configuration has replacement evidence.
func selfIdentifyingDOMSource(config map[string]string, md map[string]json.RawMessage) bool {
	if config["crawler_type"] != "dom" || config["board_url"] != "https://www.airbelgium.com/jobs/" {
		return false
	}
	stable := map[string]any{}
	for key, raw := range md {
		if monitorRuntimeFields[key] || key == "_monitor_config_fingerprint" {
			continue
		}
		var value any
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		if d.Decode(&value) != nil {
			return false
		}
		stable[key] = value
	}
	body, err := json.Marshal(stable)
	if err != nil {
		return false
	}
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:]) == "9b937f207cc9a9451c305dc45bc2d4beb81bead6d383f128ea703fad099ae7fa"
}
