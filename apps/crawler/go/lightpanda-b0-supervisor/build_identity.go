package main

import (
	"encoding/json"
	"errors"
	"io"
	"runtime"
)

const buildIdentitySchema = "jobseek.lightpanda-b0.build-identity/v1"

// This reads the image-linked revision without acquiring runtime authority.
// Trimpath builds omit linker flags from debug/buildinfo, so those flags cannot
// authenticate the linked sourceRevision value.
func writeBuildIdentity(output io.Writer) error {
	if !hex160.MatchString(sourceRevision) {
		return errors.New("missing or invalid image-linked source revision")
	}
	return json.NewEncoder(output).Encode(struct {
		Schema         string `json:"schema"`
		SourceRevision string `json:"source_revision"`
		GOOS           string `json:"goos"`
		GOARCH         string `json:"goarch"`
		GoVersion      string `json:"go_version"`
	}{buildIdentitySchema, sourceRevision, runtime.GOOS, runtime.GOARCH, runtime.Version()})
}
