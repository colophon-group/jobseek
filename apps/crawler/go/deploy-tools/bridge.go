package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

var bridgeRevision = regexp.MustCompile(`^[0-9a-f]{40}$`)
var bridgeDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)
var bridgeOwner = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
var bridgeLineBreak = regexp.MustCompile(`\r\n|[\n\r\v\f\x1c-\x1e\x85\x{2028}\x{2029}]`)
var errBridge = errors.New("release bridge evidence rejected")

func bridgeLines(body []byte) ([]string, error) {
	if !utf8.Valid(body) {
		return nil, errBridge
	}
	lines := bridgeLineBreak.Split(string(body), -1)
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines, nil
}
func bridgeValue(lines []string, key string) (string, error) {
	var value string
	count := 0
	for _, line := range lines {
		if strings.HasPrefix(line, key+"=") {
			count++
			value = strings.TrimPrefix(line, key+"=")
		}
	}
	if count != 1 {
		return "", errBridge
	}
	return value, nil
}
func bridgeRead(root, name string) ([]byte, error) {
	f, err := regularOpen(filepath.Join(root, name))
	if err != nil {
		return nil, errBridge
	}
	defer f.Close()
	before, err := f.Stat()
	if err != nil {
		return nil, errBridge
	}
	body, err := io.ReadAll(io.LimitReader(f, 8<<20+1))
	if err != nil || len(body) > 8<<20 {
		return nil, errBridge
	}
	after, err := f.Stat()
	if err != nil || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return nil, errBridge
	}
	return body, nil
}
func bridgeExists(root, name string) bool {
	_, err := os.Lstat(filepath.Join(root, name))
	return !errors.Is(err, os.ErrNotExist)
}
func bridgeHash(body []byte) string { sum := sha256.Sum256(body); return hex.EncodeToString(sum[:]) }
func bridgeImage(owner, image, value string) bool {
	return regexp.MustCompile(`^ghcr\.io/` + regexp.QuoteMeta(owner) + `/` + regexp.QuoteMeta(image) + `@sha256:[0-9a-f]{64}$`).MatchString(value)
}

// Validate the original attested runtime epoch without reading credentials or
// granting a writer. Every compatible revision remains explicit and unique.
func runtimeAttestation(body []byte, previous, contract, compatible string) ([]string, error) {
	lines, err := bridgeLines(body)
	if err != nil || len(lines) < 4 || lines[0] != "RUNTIME_ATTESTATION_FORMAT_VERSION=1" || lines[1] != "PREVIOUS_REVISION="+previous || lines[2] != "RUNTIME_CONTRACT_SHA256="+contract {
		return nil, errBridge
	}
	seen := map[string]bool{}
	revisions := []string{}
	for _, line := range lines[3:] {
		if !strings.HasPrefix(line, "COMPATIBLE_REVISION=") {
			return nil, errBridge
		}
		revision := strings.TrimPrefix(line, "COMPATIBLE_REVISION=")
		if !bridgeRevision.MatchString(revision) || seen[revision] {
			return nil, errBridge
		}
		seen[revision] = true
		revisions = append(revisions, revision)
	}
	if revisions[0] != previous || compatible != "" && !seen[compatible] {
		return nil, errBridge
	}
	return revisions, nil
}

func verifyReleaseBridge(root, owner string) (string, error) {
	info, err := os.Lstat(root)
	if err != nil || !filepath.IsAbs(root) || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || !bridgeOwner.MatchString(owner) {
		return "", errBridge
	}
	body, err := bridgeRead(root, "release.manifest")
	if err != nil {
		return "", err
	}
	manifest, err := bridgeLines(body)
	if err != nil {
		return "", err
	}
	value := func(lines []string, key string) string {
		v, e := bridgeValue(lines, key)
		if e != nil {
			err = errBridge
		}
		return v
	}
	if value(manifest, "RELEASE_FORMAT_VERSION") != "3" || err != nil {
		return "", errBridge
	}
	legacy := map[string]string{}
	hasBridge := false
	for _, line := range manifest {
		if strings.HasPrefix(line, "LEGACY_") {
			key, v, ok := strings.Cut(line, "=")
			if !ok {
				return "", errBridge
			}
			if _, exists := legacy[key]; exists {
				return "", errBridge
			}
			legacy[key] = v
			if key == "LEGACY_BRIDGE_FORMAT_VERSION" {
				hasBridge = true
			}
		}
	}
	residues := []string{"runtime-attestation.env", "legacy-source-compose.yml", "legacy-source-environment.env", "legacy-source-success.env", "legacy-source-images.override.yml"}
	if !hasBridge {
		if len(legacy) != 0 {
			return "", errBridge
		}
		for _, name := range residues {
			if bridgeExists(root, name) {
				return "", errBridge
			}
		}
		return "generic", nil
	}
	format, transitive := legacy["LEGACY_SOURCE_RELEASE_FORMAT"], legacy["LEGACY_BRIDGE_TRANSITIVE"]
	if legacy["LEGACY_BRIDGE_FORMAT_VERSION"] != "1" || format != "1" && format != "2" || transitive != "0" && transitive != "1" {
		return "", errBridge
	}
	expected := []string{"LEGACY_BRIDGE_FORMAT_VERSION", "LEGACY_BRIDGE_TRANSITIVE", "LEGACY_RUNTIME_ATTESTATION_SHA256", "LEGACY_SOURCE_RELEASE_FORMAT", "LEGACY_SOURCE_REVISION", "LEGACY_SOURCE_CRAWLER_IMAGE_REF", "LEGACY_SOURCE_COMPOSE_SHA256", "LEGACY_SOURCE_ENVIRONMENT_SHA256", "LEGACY_SOURCE_SUCCESS_SHA256"}
	if format == "2" {
		expected = append(expected, "LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256")
	}
	if len(legacy) != len(expected) {
		return "", errBridge
	}
	for _, key := range expected {
		if _, ok := legacy[key]; !ok {
			return "", errBridge
		}
	}
	readLines := func(name string) ([]byte, []string) {
		b, e := bridgeRead(root, name)
		if e != nil {
			err = errBridge
			return nil, nil
		}
		l, e := bridgeLines(b)
		if e != nil {
			err = errBridge
		}
		return b, l
	}
	environment, envLines := readLines("environment.env")
	success, successLines := readLines("success.env")
	revision, contract := value(manifest, "DATA_REVISION"), value(envLines, "JOBSEEK_RUNTIME_CONTRACT_SHA256")
	if err != nil || !bridgeRevision.MatchString(revision) || !bridgeDigest.MatchString(contract) || value(successLines, "JOBSEEK_RUNTIME_CONTRACT_SHA256") != contract {
		return "", errBridge
	}
	attestation, err := bridgeRead(root, "runtime-attestation.env")
	if err != nil {
		return "", err
	}
	if !bridgeDigest.MatchString(legacy["LEGACY_RUNTIME_ATTESTATION_SHA256"]) || bridgeHash(attestation) != legacy["LEGACY_RUNTIME_ATTESTATION_SHA256"] {
		return "", errBridge
	}
	sourceRevision := legacy["LEGACY_SOURCE_REVISION"]
	if !bridgeRevision.MatchString(sourceRevision) {
		return "", errBridge
	}
	if _, e := runtimeAttestation(attestation, revision, contract, sourceRevision); e != nil {
		return "", errBridge
	}
	sourceImage := legacy["LEGACY_SOURCE_CRAWLER_IMAGE_REF"]
	if !bridgeImage(owner, "jobseek-crawler", sourceImage) {
		return "", errBridge
	}
	sourceFiles := map[string][]byte{}
	for label, name := range map[string]string{"COMPOSE": "legacy-source-compose.yml", "ENVIRONMENT": "legacy-source-environment.env", "SUCCESS": "legacy-source-success.env"} {
		b, e := bridgeRead(root, name)
		if e != nil {
			return "", e
		}
		digest := legacy["LEGACY_SOURCE_"+label+"_SHA256"]
		if !bridgeDigest.MatchString(digest) || bridgeHash(b) != digest {
			return "", errBridge
		}
		sourceFiles[label] = b
	}
	sourceEnv, e := bridgeLines(sourceFiles["ENVIRONMENT"])
	if e != nil {
		return "", e
	}
	sourceSuccess, e := bridgeLines(sourceFiles["SUCCESS"])
	if e != nil {
		return "", e
	}
	for _, lines := range [][]string{sourceEnv, sourceSuccess} {
		for _, line := range lines {
			if strings.HasPrefix(line, "JOBSEEK_RUNTIME_CONTRACT_SHA256=") {
				return "", errBridge
			}
		}
	}
	identities := map[string]string{}
	for _, key := range []string{"CRAWLER_IMAGE_TAG", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF", "SHIM_IMAGE_REF", "JOBSEEK_DEPLOY_REVISION"} {
		v := value(sourceEnv, key)
		if value(sourceSuccess, key) != v {
			return "", errBridge
		}
		identities[key] = v
	}
	if err != nil || identities["JOBSEEK_DEPLOY_REVISION"] != sourceRevision || identities["CRAWLER_IMAGE_REF"] != sourceImage || !bridgeImage(owner, "jobseek-crawler-browser", identities["BROWSER_IMAGE_REF"]) || !bridgeImage(owner, "jobseek-murmur-shim", identities["SHIM_IMAGE_REF"]) {
		return "", errBridge
	}
	for _, key := range []string{"CRAWLER_IMAGE_TAG", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF", "JOBSEEK_DEPLOY_REVISION"} {
		if value(envLines, key) != identities[key] || value(successLines, key) != identities[key] {
			return "", errBridge
		}
	}
	shim := value(envLines, "SHIM_IMAGE_REF")
	if err != nil || value(successLines, "SHIM_IMAGE_REF") != shim || !bridgeImage(owner, "jobseek-murmur-shim", shim) || value(manifest, "COMPOSE_SHA256") != bridgeHash(sourceFiles["COMPOSE"]) {
		return "", errBridge
	}
	hasOverride := value(manifest, "HAS_IMAGE_OVERRIDE")
	if hasOverride != "0" && hasOverride != "1" {
		return "", errBridge
	}
	var override string
	if hasOverride == "1" {
		override = value(manifest, "IMAGE_OVERRIDE_SHA256")
	}
	if format == "1" {
		if bridgeExists(root, "legacy-source-images.override.yml") || transitive == "0" && hasOverride != "0" {
			return "", errBridge
		}
	} else {
		b, e := bridgeRead(root, "legacy-source-images.override.yml")
		if e != nil {
			return "", e
		}
		digest := legacy["LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256"]
		if !bridgeDigest.MatchString(digest) || bridgeHash(b) != digest || transitive == "0" && (hasOverride != "1" || override != digest) {
			return "", errBridge
		}
	}
	if transitive == "0" {
		line := []byte("JOBSEEK_RUNTIME_CONTRACT_SHA256=" + contract + "\n")
		if !bytes.Equal(environment, append(bytes.Clone(sourceFiles["ENVIRONMENT"]), line...)) || !bytes.Equal(success, append(bytes.Clone(sourceFiles["SUCCESS"]), line...)) {
			return "", errBridge
		}
		compose, e := bridgeRead(root, "docker-compose.yml")
		if e != nil || !bytes.Equal(compose, sourceFiles["COMPOSE"]) {
			return "", errBridge
		}
	}
	if err != nil {
		return "", errBridge
	}
	return "bridge", nil
}
