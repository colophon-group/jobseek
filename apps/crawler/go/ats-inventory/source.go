package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type inventoryHTTPClient interface {
	Do(*http.Request) (*http.Response, error)
}
type inventorySourceOptions struct {
	Retention                   int
	MaxSnapshotAgeDays          int
	MaxCacheBytes               int64
	MaxTotalDrop, MaxFamilyDrop float64
	FamilyDropMinRows           int
	MinimumCandidateCoveragePct float64
}

func defaultInventorySourceOptions() inventorySourceOptions {
	return inventorySourceOptions{Retention: 7, MaxSnapshotAgeDays: 30, MaxCacheBytes: 256 * 1024 * 1024, MaxTotalDrop: .10, MaxFamilyDrop: .30, FamilyDropMinRows: 20, MinimumCandidateCoveragePct: 99}
}

type inventorySource struct {
	CacheDir, ManifestURL string
	Client                inventoryHTTPClient
	Options               inventorySourceOptions
	Now                   func() time.Time
}

func newInventorySource(cache string, client inventoryHTTPClient, manifest string, opts inventorySourceOptions) (*inventorySource, error) {
	if opts.Retention < 1 || opts.MaxCacheBytes <= manifestLimit {
		return nil, sourceError("invalid inventory cache limits")
	}
	if e := trustedArtifactURL(manifest); e != nil {
		return nil, e
	}
	if client == nil {
		return nil, sourceError("inventory HTTP client is required")
	}
	return &inventorySource{CacheDir: cache, Client: client, ManifestURL: manifest, Options: opts, Now: time.Now}, nil
}
func (s *inventorySource) objectPath(digest string) (string, error) {
	if !inventoryDigestPattern.MatchString(digest) {
		return "", sourceError("invalid object checksum")
	}
	return filepath.Join(s.CacheDir, "objects", digest), nil
}
func (s *inventorySource) currentPath() string { return filepath.Join(s.CacheDir, "current.json") }
func (s *inventorySource) readState() (map[string]any, error) {
	body, e := os.ReadFile(s.currentPath())
	if errors.Is(e, os.ErrNotExist) {
		return nil, nil
	}
	if e != nil {
		return nil, sourceError("current inventory pointer is corrupt")
	}
	state, e := decodeJSONMap(body)
	if e != nil || !sourceSchemaVersion(state["schema_version"]) {
		return nil, sourceError("current inventory pointer has unsupported schema")
	}
	return state, nil
}
func (s *inventorySource) loadSnapshot(state map[string]any) (inventorySnapshot, error) {
	var empty inventorySnapshot
	manifestSHA, e := stateDigest(state, "manifest_sha256")
	if e != nil {
		return empty, e
	}
	inventorySHA, e := stateDigest(state, "inventory_sha256")
	if e != nil {
		return empty, e
	}
	manifestPath, _ := s.objectPath(manifestSHA)
	raw, e := readValidInventoryObject(manifestPath, manifestSHA, manifestLimit)
	if e != nil {
		return empty, e
	}
	manifest, e := decodeJSONMap(raw)
	if e != nil {
		return empty, e
	}
	details, e := validateManifest(manifest)
	if e != nil {
		return empty, e
	}
	if details.InventorySHA256 != inventorySHA || state["generated_at"] != details.GeneratedAt {
		return empty, sourceError("current pointer does not match manifest")
	}
	inventoryPath, _ := s.objectPath(inventorySHA)
	if !validInventoryObject(inventoryPath, inventorySHA, details.InventorySize, inventoryLimit) {
		return empty, sourceError("current inventory object is corrupt")
	}
	rows, counts, e := validateInventory(inventoryPath, details)
	if e != nil {
		return empty, e
	}
	expected, e := stateFamilyCounts(state)
	if e != nil || !equalFamilyCounts(counts, expected) {
		return empty, sourceError("current family counts do not match cached inventory")
	}
	coverage, e := inventoryCoverage(counts, s.Options.MinimumCandidateCoveragePct)
	if e != nil {
		return empty, e
	}
	validated, e := stateString(state, "validated_at")
	if e != nil {
		return empty, e
	}
	return inventorySnapshot{ManifestSHA256: manifestSHA, ManifestETag: inventoryETagValue(state["manifest_etag"]), InventorySHA256: inventorySHA, GeneratedAt: details.GeneratedAt, ValidatedAt: validated, Manifest: manifest, Rows: rows, FamilyCounts: counts, Coverage: coverage, NewFamilies: []string{}, RemovedFamilies: []string{}}, nil
}
func equalFamilyCounts(a, b map[string]int) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}
func (s *inventorySource) loadCurrent() (inventorySnapshot, error) {
	state, e := s.readState()
	if e != nil {
		return inventorySnapshot{}, e
	}
	if state == nil {
		return inventorySnapshot{}, sourceError("no current inventory snapshot")
	}
	return s.loadSnapshot(state)
}
func (s *inventorySource) Sync(ctx context.Context) (inventorySnapshot, error) {
	for _, name := range []string{"objects", "snapshots"} {
		if e := os.MkdirAll(filepath.Join(s.CacheDir, name), 0755); e != nil {
			return inventorySnapshot{}, e
		}
	}
	unlock, e := acquireInventoryLock(filepath.Join(s.CacheDir, "source.lock"))
	if e != nil {
		return inventorySnapshot{}, e
	}
	defer unlock()
	return s.syncLocked(ctx)
}
func (s *inventorySource) syncLocked(ctx context.Context) (inventorySnapshot, error) {
	var empty inventorySnapshot
	previousState, e := s.readState()
	if e != nil {
		previousState = nil
	}
	var recovery map[string]any
	var etag any
	if previousState != nil {
		etag = previousState["manifest_etag"]
	}
	raw, newETag, notModified, e := s.fetchManifest(ctx, etag)
	if e != nil {
		return empty, e
	}
	if notModified {
		if previousState == nil {
			return empty, sourceError("source returned 304 without cached snapshot")
		}
		previous, loadError := s.loadSnapshot(previousState)
		if loadError == nil {
			previous.ETagRevalidated = true
			previousState["last_checked_at"] = inventoryNowISO(s.Now())
			if newETag != nil {
				previousState["manifest_etag"] = *newETag
			}
			if e = atomicInventoryJSON(s.currentPath(), previousState); e != nil {
				return empty, e
			}
			if e = s.pruneCache(previous.ManifestSHA256); e != nil {
				return empty, e
			}
			return previous, nil
		}
		raw, newETag, notModified, e = s.fetchManifest(ctx, nil)
		if e != nil {
			return empty, e
		}
		if notModified || raw == nil {
			return empty, sourceError("source returned 304 during unconditional cache recovery")
		}
		recovery = previousState
		previousState = nil
	}
	if raw == nil {
		return empty, sourceError("manifest response missing body")
	}
	manifestSHA := sha256Bytes(raw)
	manifest, e := decodeJSONMap(raw)
	if e != nil {
		return empty, e
	}
	details, e := validateManifest(manifest)
	if e != nil {
		return empty, e
	}
	var previous *inventorySnapshot
	if previousState != nil {
		loaded, e := s.loadSnapshot(previousState)
		if e != nil {
			previousState = nil
		} else {
			previous = &loaded
			old, _ := parseInventoryTime(loaded.GeneratedAt)
			now, _ := parseInventoryTime(details.GeneratedAt)
			if now.Before(old) {
				return empty, sourceError("refusing an older inventory manifest")
			}
		}
	} else if recovery != nil {
		value, e := stateString(recovery, "generated_at")
		if e != nil {
			return empty, e
		}
		old, e := parseInventoryTime(value)
		if e != nil {
			return empty, e
		}
		now, _ := parseInventoryTime(details.GeneratedAt)
		if now.Before(old) {
			return empty, sourceError("refusing an older recovering inventory manifest")
		}
	}
	manifestPath, _ := s.objectPath(manifestSHA)
	manifestExisted := validInventoryObject(manifestPath, manifestSHA, int64(len(raw)), manifestLimit)
	if e = s.storeBytesObject(manifestSHA, raw); e != nil {
		return empty, e
	}
	inventoryPath, _ := s.objectPath(details.InventorySHA256)
	inventoryExisted := validInventoryObject(inventoryPath, details.InventorySHA256, details.InventorySize, inventoryLimit)
	cleanNew := func() {
		if !inventoryExisted {
			_ = removeInventoryFile(inventoryPath)
		}
		if !manifestExisted {
			_ = removeInventoryFile(manifestPath)
		}
	}
	if !inventoryExisted {
		if e = s.fetchObject(ctx, details.InventoryURL, details.InventorySHA256, details.InventorySize, inventoryLimit); e != nil {
			cleanNew()
			return empty, e
		}
	}
	rows, counts, e := validateInventory(inventoryPath, details)
	if e != nil {
		cleanNew()
		return empty, e
	}
	oldCounts := map[string]int{}
	oldRows := []inventoryRow{}
	if previous != nil {
		oldCounts = previous.FamilyCounts
		oldRows = previous.Rows
	} else if recovery != nil {
		oldCounts, e = stateFamilyCounts(recovery)
		if e != nil {
			cleanNew()
			return empty, e
		}
	}
	if previous != nil || recovery != nil {
		if e = s.validateShrink(oldCounts, counts); e != nil {
			cleanNew()
			return empty, e
		}
	}
	c, e := inventoryCoverage(counts, s.Options.MinimumCandidateCoveragePct)
	if e != nil {
		cleanNew()
		return empty, e
	}
	validated := inventoryNowISO(s.Now())
	baseline := previousState
	if baseline == nil {
		baseline = recovery
	}
	changed := baseline == nil || baseline["manifest_sha256"] != manifestSHA || baseline["inventory_sha256"] != details.InventorySHA256
	changedURLs := 0
	if previousState != nil {
		changedURLs = countChangedURLs(oldRows, rows)
	}
	state := map[string]any{"schema_version": 1, "manifest_sha256": manifestSHA, "manifest_etag": newETag, "inventory_sha256": details.InventorySHA256, "inventory_size": details.InventorySize, "generated_at": details.GeneratedAt, "validated_at": validated, "last_checked_at": validated, "family_counts": cloneFamilyCounts(counts)}
	metadataBytes, e := pythonStateSize(state)
	if e != nil {
		cleanNew()
		return empty, e
	}
	if int64(len(raw))+details.InventorySize+int64(metadataBytes*2)+4096 > s.Options.MaxCacheBytes {
		cleanNew()
		return empty, sourceError("validated live snapshot exceeds cache limit")
	}
	if e = atomicInventoryJSON(filepath.Join(s.CacheDir, "snapshots", manifestSHA+".json"), state); e != nil {
		return empty, e
	}
	if e = atomicInventoryJSON(s.currentPath(), state); e != nil {
		return empty, e
	}
	if e = s.pruneCache(manifestSHA); e != nil {
		return empty, e
	}
	return inventorySnapshot{ManifestSHA256: manifestSHA, ManifestETag: newETag, InventorySHA256: details.InventorySHA256, GeneratedAt: details.GeneratedAt, ValidatedAt: validated, Manifest: manifest, Rows: rows, FamilyCounts: counts, Coverage: c, Changed: changed, NewFamilies: differenceFamilies(counts, oldCounts), RemovedFamilies: differenceFamilies(oldCounts, counts), ChangedURLs: changedURLs}, nil
}
func (s *inventorySource) validateShrink(previous, current map[string]int) error {
	oldTotal, newTotal := 0, 0
	for _, n := range previous {
		oldTotal += n
	}
	for _, n := range current {
		newTotal += n
	}
	if oldTotal > 0 && float64(newTotal) < float64(oldTotal)*(1-s.Options.MaxTotalDrop) {
		return sourceError("inventory shrank unexpectedly")
	}
	for family, old := range previous {
		if old >= s.Options.FamilyDropMinRows && float64(current[family]) < float64(old)*(1-s.Options.MaxFamilyDrop) {
			return sourceError("inventory family shrank unexpectedly")
		}
	}
	return nil
}
func (s *inventorySource) request(ctx context.Context, endpoint, accept string, headers map[string]string) (*http.Response, error) {
	if e := trustedArtifactURL(endpoint); e != nil {
		return nil, e
	}
	req, e := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if e != nil {
		return nil, sourceError("artifact request is invalid")
	}
	req.Header.Set("Accept", accept)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	response, e := s.Client.Do(req)
	if e != nil {
		return nil, sourceError("artifact request failed")
	}
	if response.Request != nil {
		if e = trustedArtifactURL(response.Request.URL.String()); e != nil {
			response.Body.Close()
			return nil, e
		}
	}
	return response, nil
}
func inventoryContentLength(response *http.Response) (int64, error) {
	values, exists := response.Header["Content-Length"]
	if !exists || len(values) == 0 {
		return -1, nil
	}
	n, e := strconv.ParseInt(strings.TrimSpace(values[0]), 10, 64)
	if e != nil || n < 0 {
		return 0, sourceError("invalid Content-Length")
	}
	return n, nil
}
func (s *inventorySource) fetchManifest(ctx context.Context, etag any) ([]byte, *string, bool, error) {
	headers := map[string]string{}
	if etag != nil {
		value, ok := etag.(string)
		if !ok || !safeInventoryETag(value) {
			return nil, nil, false, sourceError("cached manifest ETag is invalid")
		}
		headers["If-None-Match"] = value
	}
	response, e := s.request(ctx, s.ManifestURL, "application/json", headers)
	if e != nil {
		return nil, nil, false, e
	}
	defer response.Body.Close()
	var resultETag *string
	if values, exists := response.Header["Etag"]; exists && len(values) > 0 {
		if !safeInventoryETag(values[0]) {
			return nil, nil, false, sourceError("manifest ETag is invalid")
		}
		resultETag = inventoryStringPointer(values[0])
	}
	if response.StatusCode == http.StatusNotModified {
		if resultETag == nil {
			resultETag = inventoryETagValue(etag)
		}
		return nil, resultETag, true, nil
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, nil, false, sourceError("manifest HTTP status is not successful")
	}
	declared, e := inventoryContentLength(response)
	if e != nil {
		return nil, nil, false, e
	}
	if declared > manifestLimit {
		return nil, nil, false, sourceError("manifest Content-Length exceeds byte limit")
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, manifestLimit+1))
	if e != nil || int64(len(raw)) > manifestLimit {
		return nil, nil, false, sourceError("manifest body exceeds byte limit or is incomplete")
	}
	return raw, resultETag, false, nil
}
func (s *inventorySource) fetchObject(ctx context.Context, endpoint, digest string, size, limit int64) error {
	if e := trustedArtifactURL(endpoint); e != nil {
		return e
	}
	target, e := s.objectPath(digest)
	if e != nil {
		return e
	}
	dir := filepath.Join(s.CacheDir, "objects")
	file, e := os.CreateTemp(dir, "download-")
	if e != nil {
		return e
	}
	temp := file.Name()
	defer os.Remove(temp)
	defer file.Close()
	response, e := s.request(ctx, endpoint, "text/csv", nil)
	if e != nil {
		return e
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return sourceError("artifact HTTP status is not successful")
	}
	declared, e := inventoryContentLength(response)
	if e != nil {
		return e
	}
	if declared > limit {
		return sourceError("artifact Content-Length exceeds byte limit")
	}
	h := sha256.New()
	n, e := io.Copy(io.MultiWriter(file, h), io.LimitReader(response.Body, limit+1))
	if e != nil {
		return e
	}
	if n > limit {
		return sourceError("artifact exceeds byte limit")
	}
	if n != size {
		return sourceError("artifact size mismatch")
	}
	if hex.EncodeToString(h.Sum(nil)) != digest {
		return sourceError("artifact SHA256 mismatch")
	}
	if e = file.Sync(); e != nil {
		return e
	}
	if e = file.Close(); e != nil {
		return e
	}
	if e = os.Rename(temp, target); e != nil {
		return e
	}
	return fsyncInventoryDirectory(dir)
}
func (s *inventorySource) storeBytesObject(digest string, body []byte) error {
	path, e := s.objectPath(digest)
	if e != nil {
		return e
	}
	if validInventoryObject(path, digest, int64(len(body)), manifestLimit) {
		return nil
	}
	return atomicInventoryBytes(path, body)
}
func (s *inventorySource) pruneCache(current string) error {
	snapshots := filepath.Join(s.CacheDir, "snapshots")
	for _, pattern := range []string{filepath.Join(s.CacheDir, ".current.json.*"), filepath.Join(snapshots, ".*.json.*")} {
		matches, e := filepath.Glob(pattern)
		if e != nil {
			return e
		}
		for _, path := range matches {
			if e = removeInventoryFile(path); e != nil {
				return e
			}
		}
	}
	type candidate struct {
		at                  time.Time
		path                string
		state               map[string]any
		manifest, inventory string
	}
	candidates := []candidate{}
	paths, e := filepath.Glob(filepath.Join(snapshots, "*.json"))
	if e != nil {
		return e
	}
	for _, path := range paths {
		body, e := os.ReadFile(path)
		var state map[string]any
		var at time.Time
		var manifest, inventory string
		if e == nil {
			state, e = decodeJSONMap(body)
		}
		if e == nil {
			var v string
			v, e = stateString(state, "validated_at")
			if e == nil {
				at, e = parseInventoryTime(v)
			}
		}
		if e == nil {
			manifest, e = stateDigest(state, "manifest_sha256")
		}
		if e == nil {
			inventory, e = stateDigest(state, "inventory_sha256")
		}
		if e != nil {
			if e = removeInventoryFile(path); e != nil {
				return e
			}
			continue
		}
		candidates = append(candidates, candidate{at, path, state, manifest, inventory})
	}
	sort.SliceStable(candidates, func(i, j int) bool { return candidates[i].at.After(candidates[j].at) })
	keep := []candidate{}
	cutoff := s.Now().AddDate(0, 0, -s.Options.MaxSnapshotAgeDays)
	for _, c := range candidates {
		if c.manifest == current || len(keep) < s.Options.Retention && !c.at.Before(cutoff) {
			keep = append(keep, c)
		} else if e = removeInventoryFile(c.path); e != nil {
			return e
		}
	}
	pruneObjects := func() error {
		referenced := map[string]bool{}
		for _, c := range keep {
			referenced[c.manifest] = true
			referenced[c.inventory] = true
		}
		entries, e := os.ReadDir(filepath.Join(s.CacheDir, "objects"))
		if e != nil {
			return e
		}
		for _, entry := range entries {
			path := filepath.Join(s.CacheDir, "objects", entry.Name())
			info, e := os.Stat(path)
			if e == nil && info.Mode().IsRegular() && !referenced[entry.Name()] {
				if e = removeInventoryFile(path); e != nil {
					return e
				}
			}
		}
		return nil
	}
	if e = pruneObjects(); e != nil {
		return e
	}
	for inventoryDirectorySize(s.CacheDir) > s.Options.MaxCacheBytes {
		oldest := -1
		for i, c := range keep {
			if c.manifest != current && (oldest < 0 || c.at.Before(keep[oldest].at)) {
				oldest = i
			}
		}
		if oldest < 0 {
			break
		}
		if e = removeInventoryFile(keep[oldest].path); e != nil {
			return e
		}
		keep = append(keep[:oldest], keep[oldest+1:]...)
		if e = pruneObjects(); e != nil {
			return e
		}
	}
	return nil
}
