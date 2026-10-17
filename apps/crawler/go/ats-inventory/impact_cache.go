package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"syscall"
	"time"
	"unicode/utf16"
)

type impactCacheOptions struct {
	MaxCacheBytes, MaxArtifactBytes, MinFreeBytes int64
	Retention, MaxAgeDays                         int
}

func defaultImpactCacheOptions() impactCacheOptions {
	return impactCacheOptions{768 * 1024 * 1024, 512 * 1024 * 1024, 512 * 1024 * 1024, 3, 30}
}

type impactCache struct {
	CacheDir string
	Client   inventoryHTTPClient
	Options  impactCacheOptions
	Now      func() time.Time
}

func newImpactCache(dir string, client inventoryHTTPClient, opts impactCacheOptions) (*impactCache, error) {
	if opts.MaxCacheBytes < 1024*1024 || opts.MaxArtifactBytes < 1 || opts.MaxArtifactBytes > opts.MaxCacheBytes || opts.MinFreeBytes < 0 || opts.Retention < 1 || client == nil {
		return nil, impactError("invalid impact cache options")
	}
	return &impactCache{dir, client, opts, time.Now}, nil
}
func (c *impactCache) currentPath() string { return filepath.Join(c.CacheDir, "current.json") }
func (c *impactCache) familyPath(family, digest string) (string, error) {
	if !inventoryFamilyPattern.MatchString(family) || !inventoryDigestPattern.MatchString(digest) {
		return "", impactError("invalid cached family key")
	}
	p := filepath.Join(c.CacheDir, "families", family)
	if e := os.MkdirAll(p, 0755); e != nil {
		return "", e
	}
	return filepath.Join(p, digest+".json"), nil
}

// Preserve Python's sorted, ASCII-escaped JSON representation for cache digests.
func impactJSONBytes(value any, pretty bool) ([]byte, error) {
	raw, e := json.Marshal(value)
	if e != nil {
		return nil, e
	}
	var canonical any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if e = decoder.Decode(&canonical); e != nil {
		return nil, e
	}
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if e = encoder.Encode(canonical); e != nil {
		return nil, e
	}
	var escaped bytes.Buffer
	for _, r := range output.String() {
		if r < 128 {
			escaped.WriteRune(r)
		} else {
			for _, u := range utf16.Encode([]rune{r}) {
				escaped.WriteString(`\u`)
				s := strconv.FormatUint(uint64(u), 16)
				for i := len(s); i < 4; i++ {
					escaped.WriteByte('0')
				}
				escaped.WriteString(s)
			}
		}
	}
	if !pretty {
		return escaped.Bytes(), nil
	}
	var indented bytes.Buffer
	if e = json.Indent(&indented, bytes.TrimSuffix(escaped.Bytes(), []byte("\n")), "", "  "); e != nil {
		return nil, e
	}
	indented.WriteByte('\n')
	return indented.Bytes(), nil
}
func (c *impactCache) loadCurrent(changed bool, downloads int) (impactSnapshot, error) {
	var empty impactSnapshot
	raw, e := boundedInventoryFile(c.currentPath(), impactDerivedLimit)
	if e != nil {
		return empty, impactError("impact current pointer is unreadable")
	}
	m, e := decodeJSONMap(raw)
	if e != nil {
		return empty, impactError("impact current pointer is corrupt")
	}
	if e = impactVersions(m); e != nil {
		return empty, e
	}
	digest, e := stateDigest(m, "snapshot_sha256")
	if e != nil {
		return empty, impactError("impact snapshot checksum is invalid")
	}
	raw, e = readValidInventoryObject(filepath.Join(c.CacheDir, "snapshots", digest+".json"), digest, impactDerivedLimit)
	if e != nil {
		return empty, impactError("impact snapshot is unreadable or corrupt")
	}
	snapshot, e := decodeImpactSnapshot(raw, digest, changed, downloads)
	if e != nil {
		return empty, e
	}
	if m["manifest_sha256"] != snapshot.ManifestSHA256 || m["inventory_sha256"] != snapshot.InventorySHA256 || m["generated_at"] != snapshot.GeneratedAt {
		return empty, impactError("impact pointer does not match snapshot")
	}
	return snapshot, nil
}
func (c *impactCache) Sync(ctx context.Context, inventory inventorySnapshot) (impactSnapshot, error) {
	for _, name := range []string{"families", "snapshots", "downloads"} {
		if e := os.MkdirAll(filepath.Join(c.CacheDir, name), 0755); e != nil {
			return impactSnapshot{}, e
		}
	}
	unlock, e := acquireInventoryLock(filepath.Join(c.CacheDir, "impact.lock"))
	if e != nil {
		return impactSnapshot{}, e
	}
	defer unlock()
	entries, e := os.ReadDir(filepath.Join(c.CacheDir, "downloads"))
	if e != nil {
		return impactSnapshot{}, e
	}
	for _, entry := range entries {
		path := filepath.Join(c.CacheDir, "downloads", entry.Name())
		if info, e := os.Stat(path); e == nil && info.Mode().IsRegular() {
			if e = removeInventoryFile(path); e != nil {
				return impactSnapshot{}, e
			}
		}
	}
	return c.syncLocked(ctx, inventory)
}
func (c *impactCache) syncLocked(ctx context.Context, inventory inventorySnapshot) (impactSnapshot, error) {
	var empty impactSnapshot
	rows, e := eligibleInventoryRows(inventory.Rows)
	if e != nil {
		return empty, e
	}
	artifacts := map[string]*impactArtifact{}
	digests := map[string]*string{}
	for _, family := range sortedImpactFamilies(rows) {
		artifact, e := jobImpactArtifact(inventory.Manifest, family)
		if e != nil {
			return empty, e
		}
		artifacts[family] = artifact
		digests[family] = nil
		if artifact != nil {
			digests[family] = inventoryStringPointer(artifact.SHA256)
		}
	}
	if previous, e := c.loadCurrent(false, 0); e == nil && sameImpactInputs(previous, inventory, digests) {
		if e = c.pruneCache(); e != nil {
			return empty, e
		}
		return previous, nil
	}
	summaries := map[string]*impactFamilySummary{}
	downloads := 0
	for _, family := range sortedImpactFamilies(rows) {
		artifact := artifacts[family]
		if artifact == nil {
			summaries[family] = nil
			continue
		}
		summary, downloaded, e := c.ensureFamilySummary(ctx, *artifact)
		if e != nil {
			return empty, e
		}
		summaries[family] = &summary
		if downloaded {
			downloads++
		}
	}
	companies, reports := resolveImpactCompanies(rows, summaries)
	derived := inventoryNowISO(c.Now())
	payload := map[string]any{"schema_version": 1, "algorithm_version": impactAlgorithmVersion, "manifest_sha256": inventory.ManifestSHA256, "inventory_sha256": inventory.InventorySHA256, "generated_at": inventory.GeneratedAt, "derived_at": derived, "family_artifacts": digests, "family_reports": reports, "companies": companies}
	body, e := impactJSONBytes(payload, false)
	if e != nil {
		return empty, e
	}
	if int64(len(body)) > impactDerivedLimit {
		return empty, impactError("compact snapshot exceeds byte limit")
	}
	if inventoryDirectorySize(c.CacheDir)+int64(len(body))+16*1024 > c.Options.MaxCacheBytes {
		return empty, impactError("compact snapshot cannot fit configured cache")
	}
	digest := sha256Bytes(body)
	if _, e = decodeImpactSnapshot(body, digest, true, downloads); e != nil {
		return empty, e
	}
	if e = atomicInventoryBytes(filepath.Join(c.CacheDir, "snapshots", digest+".json"), body); e != nil {
		return empty, e
	}
	pointer := map[string]any{"schema_version": 1, "algorithm_version": impactAlgorithmVersion, "snapshot_sha256": digest, "manifest_sha256": inventory.ManifestSHA256, "inventory_sha256": inventory.InventorySHA256, "generated_at": inventory.GeneratedAt, "validated_at": derived}
	body, e = impactJSONBytes(pointer, true)
	if e != nil {
		return empty, e
	}
	if e = atomicInventoryBytes(c.currentPath(), body); e != nil {
		return empty, e
	}
	published, e := c.loadCurrent(true, downloads)
	if e != nil {
		return empty, e
	}
	if e = c.pruneCache(); e != nil {
		return empty, e
	}
	return published, nil
}
func (c *impactCache) ensureFamilySummary(ctx context.Context, artifact impactArtifact) (impactFamilySummary, bool, error) {
	var empty impactFamilySummary
	path, e := c.familyPath(artifact.Family, artifact.SHA256)
	if e != nil {
		return empty, false, e
	}
	load := func() (impactFamilySummary, error) {
		raw, e := boundedInventoryFile(path, impactDerivedLimit)
		if e != nil {
			return empty, e
		}
		return decodeImpactFamily(raw, artifact)
	}
	if summary, e := load(); e == nil {
		return summary, false, nil
	}
	if e = c.assertDownloadFits(artifact); e != nil {
		return empty, false, e
	}
	f, e := os.CreateTemp(filepath.Join(c.CacheDir, "downloads"), "job-artifact-")
	if e != nil {
		return empty, false, e
	}
	temp := f.Name()
	f.Close()
	defer os.Remove(temp)
	if e = c.downloadArtifact(ctx, artifact, temp); e != nil {
		return empty, false, e
	}
	summary, e := deriveImpactFamily(ctx, temp, artifact, inventoryNowISO(c.Now()))
	if e != nil {
		return empty, false, e
	}
	body, e := impactJSONBytes(summary, false)
	if e != nil {
		return empty, false, e
	}
	if e = atomicInventoryBytes(path, body); e != nil {
		return empty, false, e
	}
	summary, e = load()
	if e != nil {
		_ = removeInventoryFile(path)
		return empty, false, e
	}
	return summary, true, nil
}
func (c *impactCache) assertDownloadFits(artifact impactArtifact) error {
	if artifact.Size > c.Options.MaxArtifactBytes {
		return impactError("artifact exceeds per-artifact limit")
	}
	if inventoryDirectorySize(c.CacheDir)+artifact.Size+2*1024*1024 > c.Options.MaxCacheBytes {
		return impactError("artifact cannot fit impact cache")
	}
	var stats syscall.Statfs_t
	if e := syscall.Statfs(c.CacheDir, &stats); e != nil {
		return impactError("cannot determine free disk space")
	}
	free := int64(stats.Bavail) * int64(stats.Bsize)
	if free-artifact.Size < c.Options.MinFreeBytes {
		return impactError("artifact violates free-space reserve")
	}
	return nil
}
func (c *impactCache) downloadArtifact(ctx context.Context, artifact impactArtifact, target string) error {
	source := inventorySource{Client: c.Client}
	response, e := source.request(ctx, artifact.URL, "application/vnd.apache.parquet", nil)
	if e != nil {
		return impactError("artifact request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return impactError("artifact HTTP status is unsuccessful")
	}
	declared, e := inventoryContentLength(response)
	if e != nil || declared >= 0 && declared != artifact.Size {
		return impactError("artifact Content-Length differs from manifest")
	}
	f, e := os.OpenFile(target, os.O_WRONLY|os.O_TRUNC, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	h := sha256.New()
	n, e := io.Copy(io.MultiWriter(f, h), io.LimitReader(response.Body, artifact.Size+1))
	if e != nil {
		return e
	}
	if n != artifact.Size || n > c.Options.MaxArtifactBytes {
		return impactError("artifact size differs from manifest")
	}
	if hex.EncodeToString(h.Sum(nil)) != artifact.SHA256 {
		return impactError("artifact checksum differs from manifest")
	}
	return f.Sync()
}
func (c *impactCache) pruneCache() error {
	current := ""
	if raw, e := boundedInventoryFile(c.currentPath(), impactDerivedLimit); e == nil {
		if m, e := decodeJSONMap(raw); e == nil {
			current, _ = m["snapshot_sha256"].(string)
		}
	}
	type snapshot struct {
		at           time.Time
		path, digest string
		payload      map[string]any
	}
	snapshots := []snapshot{}
	paths, e := filepath.Glob(filepath.Join(c.CacheDir, "snapshots", "*.json"))
	if e != nil {
		return e
	}
	for _, path := range paths {
		digest := filepath.Base(path)
		digest = digest[:len(digest)-5]
		raw, e := readValidInventoryObject(path, digest, impactDerivedLimit)
		var m map[string]any
		var at time.Time
		if e == nil {
			m, e = decodeJSONMap(raw)
		}
		if e == nil {
			var value string
			value, e = impactString(m, "derived_at", 0, false)
			if e == nil {
				at, e = parseInventoryTime(value)
			}
		}
		if e != nil {
			if e = removeInventoryFile(path); e != nil {
				return e
			}
			continue
		}
		snapshots = append(snapshots, snapshot{at, path, digest, m})
	}
	sort.SliceStable(snapshots, func(i, j int) bool { return snapshots[i].at.After(snapshots[j].at) })
	cutoff := c.Now().AddDate(0, 0, -c.Options.MaxAgeDays)
	kept := []snapshot{}
	for _, s := range snapshots {
		if s.digest == current || len(kept) < c.Options.Retention && !s.at.Before(cutoff) {
			kept = append(kept, s)
		} else if e = removeInventoryFile(s.path); e != nil {
			return e
		}
	}
	referenced := map[string]map[string]bool{}
	for _, s := range kept {
		artifacts, ok := s.payload["family_artifacts"].(map[string]any)
		if !ok {
			continue
		}
		for family, value := range artifacts {
			if digest, ok := value.(string); ok {
				if referenced[family] == nil {
					referenced[family] = map[string]bool{}
				}
				referenced[family][digest] = true
			}
		}
	}
	families, e := os.ReadDir(filepath.Join(c.CacheDir, "families"))
	if e != nil {
		return e
	}
	for _, family := range families {
		if !family.IsDir() {
			continue
		}
		paths, e := filepath.Glob(filepath.Join(c.CacheDir, "families", family.Name(), "*.json"))
		if e != nil {
			return e
		}
		for _, path := range paths {
			base := filepath.Base(path)
			digest := base[:len(base)-5]
			if !referenced[family.Name()][digest] {
				if e = removeInventoryFile(path); e != nil {
					return e
				}
			}
		}
	}
	return nil
}
