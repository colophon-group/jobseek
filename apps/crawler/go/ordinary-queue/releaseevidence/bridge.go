package releaseevidence

import (
	"bytes"
	"strings"
)

// Preserve the existing verifier's legacy-to-v3 and transitive Murmur binding.
// Every source attachment is regular, bounded and hashed before parsing; secrets
// never enter the returned document or any diagnostic.
func verifyBridge(r *reader, owner string, m map[string]string, el, sl []string, env, success, compose []byte) (string, error) {
	residue := []string{"runtime-attestation.env", "legacy-source-compose.yml", "legacy-source-environment.env", "legacy-source-success.env", "legacy-source-images.override.yml"}
	legacy := map[string]bool{}
	for key := range m {
		if strings.HasPrefix(key, "LEGACY_") {
			legacy[key] = true
		}
	}
	if m["LEGACY_BRIDGE_FORMAT_VERSION"] == "" {
		if len(legacy) != 0 {
			return "", reject("generic release bridge fields")
		}
		for _, name := range residue {
			if r.exists(name) {
				return "", reject("generic release bridge residue")
			}
		}
		return "generic", nil
	}
	sourceFormat, transitive := m["LEGACY_SOURCE_RELEASE_FORMAT"], m["LEGACY_BRIDGE_TRANSITIVE"]
	if m["LEGACY_BRIDGE_FORMAT_VERSION"] != "1" || (sourceFormat != "1" && sourceFormat != "2") || (transitive != "0" && transitive != "1") {
		return "", reject("legacy bridge format")
	}
	required := []string{"LEGACY_BRIDGE_FORMAT_VERSION", "LEGACY_BRIDGE_TRANSITIVE", "LEGACY_RUNTIME_ATTESTATION_SHA256", "LEGACY_SOURCE_RELEASE_FORMAT", "LEGACY_SOURCE_REVISION", "LEGACY_SOURCE_CRAWLER_IMAGE_REF", "LEGACY_SOURCE_COMPOSE_SHA256", "LEGACY_SOURCE_ENVIRONMENT_SHA256", "LEGACY_SOURCE_SUCCESS_SHA256"}
	if sourceFormat == "2" {
		required = append(required, "LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256")
	}
	if len(legacy) != len(required) {
		return "", reject("closed bridge fields")
	}
	for _, key := range required {
		if !legacy[key] {
			return "", reject("missing bridge field")
		}
	}
	runtime, err := exact(el, "JOBSEEK_RUNTIME_CONTRACT_SHA256")
	if err != nil || !shaPattern.MatchString(runtime) {
		return "", reject("bridge runtime")
	}
	attestation, err := hashed(r, "runtime-attestation.env", m["LEGACY_RUNTIME_ATTESTATION_SHA256"], 64<<10)
	if err != nil {
		return "", err
	}
	al, err := lines(attestation)
	if err != nil || len(al) < 4 || al[0] != "RUNTIME_ATTESTATION_FORMAT_VERSION=1" || al[1] != "PREVIOUS_REVISION="+m["DATA_REVISION"] || al[2] != "RUNTIME_CONTRACT_SHA256="+runtime {
		return "", reject("runtime attestation identity")
	}
	compatible := map[string]bool{}
	for i, line := range al[3:] {
		key, value, ok := strings.Cut(line, "=")
		if !ok || key != "COMPATIBLE_REVISION" || !revisionPattern.MatchString(value) || compatible[value] || (i == 0 && value != m["DATA_REVISION"]) {
			return "", reject("runtime epoch attestation")
		}
		compatible[value] = true
	}
	sourceRevision, sourceImage := m["LEGACY_SOURCE_REVISION"], m["LEGACY_SOURCE_CRAWLER_IMAGE_REF"]
	if !revisionPattern.MatchString(sourceRevision) || !compatible[sourceRevision] || !image(owner, "jobseek-crawler", sourceImage) {
		return "", reject("legacy source outside runtime epoch")
	}
	sourceCompose, err := hashed(r, "legacy-source-compose.yml", m["LEGACY_SOURCE_COMPOSE_SHA256"], 4<<20)
	if err != nil {
		return "", err
	}
	sourceEnv, err := hashed(r, "legacy-source-environment.env", m["LEGACY_SOURCE_ENVIRONMENT_SHA256"], 4<<20)
	if err != nil {
		return "", err
	}
	sourceSuccess, err := hashed(r, "legacy-source-success.env", m["LEGACY_SOURCE_SUCCESS_SHA256"], 64<<10)
	if err != nil {
		return "", err
	}
	sel, err := lines(sourceEnv)
	if err != nil {
		return "", err
	}
	ssl, err := lines(sourceSuccess)
	if err != nil {
		return "", err
	}
	if !noKey(sel, "JOBSEEK_RUNTIME_CONTRACT_SHA256") || !noKey(ssl, "JOBSEEK_RUNTIME_CONTRACT_SHA256") {
		return "", reject("legacy source gained runtime evidence")
	}
	sourceIDs := map[string]string{}
	for _, key := range []string{"CRAWLER_IMAGE_TAG", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF", "SHIM_IMAGE_REF", "JOBSEEK_DEPLOY_REVISION"} {
		v, err := exact(sel, key)
		s, serr := exact(ssl, key)
		if err != nil || serr != nil || v != s {
			return "", reject("legacy identity pair")
		}
		sourceIDs[key] = v
	}
	if sourceIDs["JOBSEEK_DEPLOY_REVISION"] != sourceRevision || sourceIDs["CRAWLER_IMAGE_REF"] != sourceImage || !image(owner, "jobseek-crawler-browser", sourceIDs["BROWSER_IMAGE_REF"]) || !image(owner, "jobseek-murmur-shim", sourceIDs["SHIM_IMAGE_REF"]) {
		return "", reject("legacy immutable source identities")
	}
	for _, key := range []string{"CRAWLER_IMAGE_TAG", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF", "JOBSEEK_DEPLOY_REVISION"} {
		e, err := exact(el, key)
		s, serr := exact(sl, key)
		if err != nil || serr != nil || e != sourceIDs[key] || s != sourceIDs[key] {
			return "", reject("bridged runtime identity changed")
		}
	}
	shim, err := exact(el, "SHIM_IMAGE_REF")
	s, serr := exact(sl, "SHIM_IMAGE_REF")
	if err != nil || serr != nil || shim != s || !image(owner, "jobseek-murmur-shim", shim) {
		return "", reject("current bridge shim identity")
	}
	if !bytes.Equal(compose, sourceCompose) {
		return "", reject("base Compose differs from legacy source")
	}
	if sourceFormat == "1" {
		if r.exists("legacy-source-images.override.yml") || (transitive == "0" && m["HAS_IMAGE_OVERRIDE"] != "0") {
			return "", reject("format-1 legacy override residue")
		}
	} else {
		_, err := hashed(r, "legacy-source-images.override.yml", m["LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256"], 4<<20)
		if err != nil {
			return "", err
		}
		if transitive == "0" && (m["HAS_IMAGE_OVERRIDE"] != "1" || m["IMAGE_OVERRIDE_SHA256"] != m["LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256"]) {
			return "", reject("initial format-2 override changed")
		}
	}
	if transitive == "0" {
		runtimeLine := []byte("JOBSEEK_RUNTIME_CONTRACT_SHA256=" + runtime + "\n")
		if !bytes.Equal(env, append(append([]byte{}, sourceEnv...), runtimeLine...)) || !bytes.Equal(success, append(append([]byte{}, sourceSuccess...), runtimeLine...)) {
			return "", reject("initial bridge is not exact source plus runtime")
		}
	}
	return "bridge", nil
}
