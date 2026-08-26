// Code generated from extension_registry.json by tools/generate_extensions.py. DO NOT EDIT.

package conformance

import runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"

type extensionRule struct {
	version   uint32
	encoding  runtimev1.ExtensionEncoding
	contexts  map[string]bool
	validator string
}

var extensionRules = map[string]extensionRule{
	"jobseek.runtime.v1/representative-json/monitor-config":   {version: 1, encoding: runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON, contexts: map[string]bool{"manifest": true}, validator: "monitor_config"},
	"jobseek.runtime.v1/representative-json/scraper-config":   {version: 1, encoding: runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON, contexts: map[string]bool{"manifest": true}, validator: "scraper_config"},
	"jobseek.runtime.v1/representative-json/runtime-metadata": {version: 1, encoding: runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON, contexts: map[string]bool{"job_content": true, "monitor_metadata": true}, validator: "runtime_metadata"},
	"jobseek.runtime.v1/browser/evaluation-json":              {version: 1, encoding: runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON, contexts: map[string]bool{"browser_evaluation": true}, validator: "evaluation_json"},
}
