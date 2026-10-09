package queue

import "encoding/json"

// These root annotations do not alter DOM/API/sitemap inventory extraction.
// The existing canonical detail-success query reads rescrape_policy freshly
// from job_board. Keep both annotations in the immutable board binding.
func sharedServiceAnnotationProvider(provider string) bool {
	return provider == "dom" || provider == "api_sniffer" || provider == "sitemap"
}

func validateSharedServiceAnnotations(md map[string]json.RawMessage) error {
	if raw, present := md["rescrape_policy"]; present {
		var policy string
		if json.Unmarshal(raw, &policy) != nil || policy != "never" {
			return ErrUnsupportedProfile
		}
	}
	if raw, present := md["defaults"]; present {
		if _, err := profileMetadataFields(string(raw), nil); err != nil {
			return ErrUnsupportedProfile
		}
	}
	return nil
}
