//go:build integration

package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	"debug/buildinfo"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// The disposable Linux image harness independently observes the immutable image
// ID, its installed binary bytes, architecture and assets. This fixture verifies
// that evidence and exercises the extracted old runtime. It cannot authorize a
// production release or substitute for independent host/release verification.
type priorInstalledImageEvidence struct {
	Version              string            `json:"version"`
	SourceRevision       string            `json:"source_revision"`
	ImageID              string            `json:"image_id"`
	Architecture         string            `json:"architecture"`
	BinarySHA256         string            `json:"binary_sha256"`
	DockerfileSHA256     string            `json:"dockerfile_sha256"`
	InstalledAssetSHA256 map[string]string `json:"installed_asset_sha256"`
}

func decodePriorInstalledImageEvidence(body []byte, digest, binaryHash, architecture string) (priorInstalledImageEvidence, bool) {
	var evidence priorInstalledImageEvidence
	h := sha256.Sum256(body)
	if len(body) == 0 || len(body) > 64<<10 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return evidence, false
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&evidence) != nil || d.Decode(new(any)) != io.EOF {
		return evidence, false
	}
	// Require the closed canonical representation as well as its caller-bound
	// hash, so duplicate or reordered fields cannot conceal a different proof.
	canonical, err := json.Marshal(evidence)
	if err != nil || !bytes.Equal(canonical, body) || evidence.Version != "jobseek.migration.prior-ordinary-installed/v1" || evidence.SourceRevision != priorOrdinaryExecutableSource || evidence.Architecture != architecture || (architecture != "amd64" && architecture != "arm64") || evidence.BinarySHA256 != binaryHash || !ownershipSHA256.MatchString(binaryHash) || !strings.HasPrefix(evidence.ImageID, "sha256:") || !ownershipSHA256.MatchString(strings.TrimPrefix(evidence.ImageID, "sha256:")) || !ownershipSHA256.MatchString(evidence.DockerfileSHA256) || len(evidence.InstalledAssetSHA256) != 34 {
		return evidence, false
	}
	for name, hash := range evidence.InstalledAssetSHA256 {
		if name == "." || !fs.ValidPath(name) || strings.ContainsAny(name, "\\\x00") || !ownershipSHA256.MatchString(hash) {
			return evidence, false
		}
	}
	return evidence, true
}

func verifyPriorInstalledImageEvidence(t *testing.T, binary, data, expectedHash string) {
	t.Helper()
	path, digest := os.Getenv("JOBSEEK_ORDINARY_PRIOR_IMAGE_PROOF_FILE"), os.Getenv("JOBSEEK_ORDINARY_PRIOR_IMAGE_PROOF_SHA256")
	if runtime.GOOS != "linux" || !filepath.IsAbs(path) {
		t.Fatal("prior installed image requires explicit Linux image evidence")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 64<<10 {
		t.Fatal("prior installed image evidence file unsafe")
	}
	body, err := os.ReadFile(path)
	evidence, ok := decodePriorInstalledImageEvidence(body, digest, expectedHash, runtime.GOARCH)
	if err != nil || !ok {
		t.Fatal("prior installed image evidence does not bind source/image/binary/architecture/assets")
	}
	metadata, err := buildinfo.ReadFile(binary)
	if err != nil {
		t.Fatal("prior installed binary build metadata missing")
	}
	settings := map[string]string{}
	for _, setting := range metadata.Settings {
		settings[setting.Key] = setting.Value
	}
	if settings["GOOS"] != "linux" || settings["GOARCH"] != evidence.Architecture {
		t.Fatal("prior installed binary platform differs from image")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	dockerfile, err := exec.CommandContext(ctx, "git", "show", priorOrdinaryExecutableSource+":apps/crawler/Dockerfile").Output()
	dockerHash := sha256.Sum256(dockerfile)
	if err != nil || hex.EncodeToString(dockerHash[:]) != evidence.DockerfileSHA256 {
		t.Fatal("prior image Dockerfile differs from immutable source")
	}
	seen := 0
	err = filepath.WalkDir(data, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		name, err := filepath.Rel(data, path)
		info, statErr := entry.Info()
		if err != nil || statErr != nil || !info.Mode().IsRegular() || info.Size() > 64<<20 || evidence.InstalledAssetSHA256[name] == "" {
			return ErrAuthorityLost
		}
		actual, err := os.ReadFile(path)
		expected, gitErr := exec.CommandContext(ctx, "git", "show", priorOrdinaryExecutableSource+":apps/crawler/data/"+name).Output()
		h := sha256.Sum256(actual)
		if err != nil || gitErr != nil || !bytes.Equal(actual, expected) || hex.EncodeToString(h[:]) != evidence.InstalledAssetSHA256[name] {
			return ErrAuthorityLost
		}
		seen++
		return nil
	})
	if err != nil || seen != len(evidence.InstalledAssetSHA256) {
		t.Fatal("prior image assets missing, extra, unsafe or different from immutable source")
	}
	t.Logf("verified prior image_id=%s architecture=%s dockerfile_sha256=%s assets=%d", evidence.ImageID, evidence.Architecture, evidence.DockerfileSHA256, seen)
}

func TestPriorInstalledImageEvidenceRejectsDrift(t *testing.T) {
	assets := map[string]string{}
	for i := byte('A'); i < 'A'+34; i++ {
		assets[fmt.Sprintf("asset-%02d.csv", i)] = strings.Repeat("a", 64)
	}
	// Actual installed data includes nested images/epfl/icon.png and logo.png,
	// not only flat CSV names. Safe relative trees must retain every asset.
	delete(assets, "asset-65.csv")
	delete(assets, "asset-66.csv")
	assets["images/epfl/icon.png"] = strings.Repeat("a", 64)
	assets["images/epfl/logo.png"] = strings.Repeat("a", 64)
	valid := priorInstalledImageEvidence{"jobseek.migration.prior-ordinary-installed/v1", priorOrdinaryExecutableSource, "sha256:" + strings.Repeat("b", 64), "amd64", strings.Repeat("c", 64), strings.Repeat("d", 64), assets}
	marshal := func(v priorInstalledImageEvidence) ([]byte, string) {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(b)
		return b, hex.EncodeToString(h[:])
	}
	body, digest := marshal(valid)
	if _, ok := decodePriorInstalledImageEvidence(body, digest, valid.BinarySHA256, "amd64"); !ok {
		t.Fatal("valid closed fixture evidence rejected")
	}
	for _, mutate := range []func(*priorInstalledImageEvidence){
		func(v *priorInstalledImageEvidence) { v.SourceRevision = strings.Repeat("e", 40) },
		func(v *priorInstalledImageEvidence) { v.ImageID = "jobseek-crawler:latest" },
		func(v *priorInstalledImageEvidence) { v.Architecture = "arm64" },
		func(v *priorInstalledImageEvidence) { v.BinarySHA256 = strings.Repeat("e", 64) },
		func(v *priorInstalledImageEvidence) { v.DockerfileSHA256 = "" },
		func(v *priorInstalledImageEvidence) { v.InstalledAssetSHA256 = map[string]string{} },
	} {
		candidate := valid
		mutate(&candidate)
		body, digest := marshal(candidate)
		if _, ok := decodePriorInstalledImageEvidence(body, digest, valid.BinarySHA256, "amd64"); ok {
			t.Fatal("drifted prior image fixture evidence admitted")
		}
	}
	for _, candidate := range [][]byte{append(append([]byte{}, body...), '\n'), bytes.Replace(body, []byte(`"version":`), []byte(`"extra":true,"version":`), 1), bytes.Replace(body, []byte(`"version":`), []byte(`"source_revision":"bad","version":`), 1)} {
		h := sha256.Sum256(candidate)
		if _, ok := decodePriorInstalledImageEvidence(candidate, hex.EncodeToString(h[:]), valid.BinarySHA256, "amd64"); ok {
			t.Fatal("noncanonical or open prior image proof admitted")
		}
	}
	if _, ok := decodePriorInstalledImageEvidence(body, strings.Repeat("f", 64), valid.BinarySHA256, "amd64"); ok {
		t.Fatal("wrong prior image evidence hash admitted")
	}
	for _, name := range []string{"/escape", ".", "images/../escape", "images//escape", "images\\escape", "images/./escape"} {
		candidate := valid
		candidate.InstalledAssetSHA256 = map[string]string{}
		for key, value := range valid.InstalledAssetSHA256 {
			candidate.InstalledAssetSHA256[key] = value
		}
		delete(candidate.InstalledAssetSHA256, "images/epfl/icon.png")
		candidate.InstalledAssetSHA256[name] = strings.Repeat("a", 64)
		body, digest := marshal(candidate)
		if _, ok := decodePriorInstalledImageEvidence(body, digest, valid.BinarySHA256, "amd64"); ok {
			t.Fatal("unsafe installed asset path admitted")
		}
	}
}
