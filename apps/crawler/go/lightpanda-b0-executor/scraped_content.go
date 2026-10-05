package executor

// PrepareScrapedValues mirrors core.scrape's structured extras followed by
// processing.scrape's missing-field defaults. Normalize/derive/hash remain in
// the existing Processor. Input maps and the configured defaults stay detached.
func PrepareScrapedValues(values, defaults map[string]any) (map[string]any, error) {
	out := make(map[string]any, len(values)+len(defaults))
	for k, v := range values {
		out[k] = v
	}
	if extras, ok := out["extras"].(map[string]any); ok && len(extras) > 0 {
		var description *string
		if raw, ok := out["description"].(string); ok {
			description = &raw
		}
		enriched, err := enrichRichDescription(description, extras)
		if err != nil {
			return nil, err
		}
		if enriched != nil {
			out["description"] = *enriched
		}
	}
	for k, v := range defaults {
		missing := out[k] == nil
		switch a := out[k].(type) {
		case []any:
			missing = len(a) == 0
		case []string:
			missing = len(a) == 0
		}
		if missing {
			out[k] = v
		}
	}
	return out, nil
}
