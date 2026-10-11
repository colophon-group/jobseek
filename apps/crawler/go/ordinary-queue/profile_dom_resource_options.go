package queue

// The pinned Lightpanda engine does not request font/media resources during
// DOM navigation. Its default therefore preserves the original lean exclusions
// used by auto after explicit anti-bot reconnaissance. This is qualified with
// physical engine requests and complete public DOM inventories, not a new
// request interceptor. Original metadata remains in the immutable config hash.
func normalizeDOMLightpandaResourceOptions(options map[string]any) error {
	if raw, exists := options["bot_protection"]; exists {
		if _, ok := raw.(bool); !ok {
			return ErrUnsupportedProfile
		}
	}
	policy := options["resource_policy"]
	if policy == "auto" {
		options["resource_policy"] = "none"
	}
	delete(options, "bot_protection")
	return nil
}
