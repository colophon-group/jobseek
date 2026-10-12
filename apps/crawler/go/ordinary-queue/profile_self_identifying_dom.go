package queue

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

var selfIdentifyingDOMSourceHashes = map[string]string{
	"https://www.alten.it/career/jobs/?per_page=100&sortby=date":                                      "f0c078e9a0e2a3f070d092b49046851a98e56a29ebaa43999eda77c9b750b01a",
	"https://www.alten.nl/werken-bij-alten/career-nl/":                                                "4f120f99911fc362056b05a51cbebd454c73180d74c35ca6dc5da63510f201e5",
	"https://www.alten.ch/de/career/jobs/":                                                            "b4254cbe0bbbb04ef3e9fbbae09c1df31c408725b4b43cc0f879966288d18454",
	"https://careers.glasswall.com/":                                                                  "08e009f1eec656c859e1d8c70d0dc4048fef6f81b48c91f1fbd25a4ee98644fc",
	"https://www.airbelgium.com/jobs/":                                                                "9b937f207cc9a9451c305dc45bc2d4beb81bead6d383f128ea703fad099ae7fa",
	"https://www.alten.pt/pt/career/jobs/":                                                            "756ca5c7c5a3fecf3788178070631d646135ec435527b6c7ff78dce859802cad",
	"https://www.alten.es/unete-a-alten/empleo/":                                                      "38390cfcca1eef54d39f25376e70daec6867658f723eb1d3282ca513df60b328",
	"https://www.alten.fi/en/career/jobs/":                                                            "d5bd8bfed2b3088227622eb6190a93f303f827472481d14d53b6641d73819a56",
	"https://ukraine.arcelormittal.com/people-and-careers/arcelormittal-kryvyi-rih-as-your-workplace": "ffb5b50cf54c738b0f88a8be077c25651ce93d3e0ce7c5a1c8c46401341f7eb9",
}

// Complete original inventories match verified-TLS, direct, self-identifying
// Lightpanda documents for these exact static configurations. Keep original
// controls in the ownership hash; the replacement never grants a generic
// proxy, stealth or certificate-verification override.
func selfIdentifyingDOMSource(config map[string]string, md map[string]json.RawMessage) bool {
	expected, ok := selfIdentifyingDOMSourceHashes[config["board_url"]]
	if config["crawler_type"] != "dom" || !ok {
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
	return hex.EncodeToString(digest[:]) == expected
}
