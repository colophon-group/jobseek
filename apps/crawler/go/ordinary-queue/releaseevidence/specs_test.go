package releaseevidence

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"
)

func specFixture(t *testing.T, absent bool) (SpecCaptureConfig, generation) {
	t.Helper()
	g := fixture(t)
	d := t.TempDir()
	if err := os.Chmod(d, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(d, "scripts"), 0755); err != nil {
		t.Fatal(err)
	}
	for _, name := range specNames {
		if absent && (name == "scripts/verify-crawler-release-bridge.py" || name == "lightpanda-b0-enabled.override.yml") {
			continue
		}
		mode := os.FileMode(0644)
		if strings.HasSuffix(name, ".sh") || strings.HasSuffix(name, ".py") {
			mode = 0755
		}
		if err := os.WriteFile(filepath.Join(d, name), []byte("active "+name+" fixture-sensitive-value\n"), mode); err != nil {
			t.Fatal(err)
		}
	}
	return SpecCaptureConfig{d, g.directory, fixtureOwner, verify(t, g).SHA256()}, g
}

func captureSpecs(t *testing.T, c SpecCaptureConfig) *Specs {
	t.Helper()
	s, err := CaptureSpecs(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func specOracle(t *testing.T, s *Specs, wantSuccess bool) {
	t.Helper()
	py, err := exec.LookPath("python3")
	if err != nil {
		if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_PYTHON") == "1" {
			t.Fatal("required offline Python oracle unavailable")
		}
		t.Skip("offline Python oracle unavailable")
	}
	source, err := os.ReadFile("../../../deploy.sh")
	if err != nil {
		t.Fatal(err)
	}
	start := strings.SplitN(string(source), "extract_verified_deploy_spec_snapshot() {", 2)
	if len(start) != 2 {
		t.Fatal("deployed archive verifier missing")
	}
	code := strings.SplitN(start[1], "<<'PY'\n", 2)
	if len(code) != 2 {
		t.Fatal("deployed archive verifier framing")
	}
	code = strings.SplitN(code[1], "\nPY\n", 2)
	if len(code) != 2 {
		t.Fatal("deployed archive verifier end")
	}
	d := t.TempDir()
	archive := filepath.Join(d, "rollback.tar")
	destination := filepath.Join(d, "output")
	if os.WriteFile(archive, s.archive, 0600) != nil || os.Mkdir(destination, 0700) != nil {
		t.Fatal("oracle fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	args := append([]string{"-I", "-c", code[0], archive, destination, specPresenceName}, specNames...)
	cmd := exec.CommandContext(ctx, py, args...)
	if err := cmd.Run(); (err == nil) != wantSuccess {
		t.Fatal("deployed archive verifier disagrees with bounded fixture", err)
	}
	if wantSuccess {
		var doc specDocument
		if json.Unmarshal([]byte(s.Body()), &doc) != nil {
			t.Fatal("spec document")
		}
		for _, e := range doc.Entries {
			b, err := os.ReadFile(filepath.Join(destination, e.Name))
			if e.Present && (err != nil || digest(b) != e.SHA256) {
				t.Fatal("deployed extraction changed exact spec bytes")
			}
			if !e.Present && !errors.Is(err, os.ErrNotExist) {
				t.Fatal("deployed extraction lost absence")
			}
		}
	}
}

func TestNativeSpecCaptureRetainsCommittedComposeAndExactAbsence(t *testing.T) {
	for _, absent := range []bool{false, true} {
		c, g := specFixture(t, absent)
		s := captureSpecs(t, c)
		again := captureSpecs(t, c)
		if s.Body() != again.Body() || s.ArchiveSHA256() != again.ArchiveSHA256() || s.SHA256() != digest([]byte(s.Body())) || strings.Contains(s.Body(), "fixture-sensitive-value") || strings.Contains(s.Body(), c.DeploymentDirectory) {
			t.Fatal("unstable or disclosing spec evidence")
		}
		var d specDocument
		if json.Unmarshal([]byte(s.Body()), &d) != nil || len(d.Entries) != len(specNames) || d.FileEvidenceSHA256 != c.FileEvidenceSHA256 {
			t.Fatal("missing capture/release binding")
		}
		for _, e := range d.Entries {
			if e.Name == "docker-compose.yml" && (e.SHA256 != digest(read(t, g, e.Name)) || e.Mode != "644") {
				t.Fatal("mutable live Compose replaced committed rollback evidence")
			}
			if e.Name == "scripts/verify-crawler-release-bridge.py" && e.Present == absent {
				t.Fatal("first verifier rollout lost explicit absence")
			}
		}
		specOracle(t, s, true)
	}
}

type rawSpecMember struct {
	h tar.Header
	b []byte
}

func specMembers(t *testing.T, s *Specs) []rawSpecMember {
	t.Helper()
	r := tar.NewReader(bytes.NewReader(s.archive))
	var out []rawSpecMember
	for {
		h, err := r.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		b, err := io.ReadAll(r)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, rawSpecMember{*h, b})
	}
	return out
}
func specBytes(t *testing.T, m []rawSpecMember) []byte {
	t.Helper()
	var b bytes.Buffer
	w := tar.NewWriter(&b)
	for _, e := range m {
		h := e.h
		h.Size = int64(len(e.b))
		if err := w.WriteHeader(&h); err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write(e.b); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func TestSpecArchiveRejectsStructuralContentModeAndAbsenceDrift(t *testing.T) {
	tests := map[string]func([]rawSpecMember) []rawSpecMember{
		"duplicate":    func(m []rawSpecMember) []rawSpecMember { return append(m, m[1]) },
		"unknown path": func(m []rawSpecMember) []rawSpecMember { m[1].h.Name = "extra.sh"; return m },
		"traversal":    func(m []rawSpecMember) []rawSpecMember { m[1].h.Name = "../deploy.sh"; return m },
		"absolute":     func(m []rawSpecMember) []rawSpecMember { m[1].h.Name = "/deploy.sh"; return m },
		"symlink": func(m []rawSpecMember) []rawSpecMember {
			m[1].h.Typeflag = tar.TypeSymlink
			m[1].h.Linkname = "deploy.sh"
			m[1].b = nil
			return m
		},
		"hardlink": func(m []rawSpecMember) []rawSpecMember {
			m[1].h.Typeflag = tar.TypeLink
			m[1].h.Linkname = "deploy.sh"
			m[1].b = nil
			return m
		},
		"directory":        func(m []rawSpecMember) []rawSpecMember { m[1].h.Typeflag = tar.TypeDir; m[1].b = nil; return m },
		"mode drift":       func(m []rawSpecMember) []rawSpecMember { m[1].h.Mode = 0644; return m },
		"content drift":    func(m []rawSpecMember) []rawSpecMember { m[1].b = []byte("changed"); return m },
		"missing member":   func(m []rawSpecMember) []rawSpecMember { return append(m[:1], m[2:]...) },
		"manifest newline": func(m []rawSpecMember) []rawSpecMember { m[0].b = bytes.TrimSuffix(m[0].b, []byte("\n")); return m },
		"manifest CRLF": func(m []rawSpecMember) []rawSpecMember {
			m[0].b = []byte(strings.ReplaceAll(string(m[0].b), "\n", "\r\n"))
			return m
		},
		"manifest order": func(m []rawSpecMember) []rawSpecMember {
			m[0].b = []byte(strings.Replace(string(m[0].b), " deploy.sh\n", " deploy_helpers.sh\n", 1))
			return m
		},
		"absence residue": func(m []rawSpecMember) []rawSpecMember {
			m[0].b = append(m[0].b, []byte("ABSENT - - extra.sh\n")...)
			return m
		},
		"presence mode": func(m []rawSpecMember) []rawSpecMember { m[0].h.Mode = 0644; return m },
		"PAX": func(m []rawSpecMember) []rawSpecMember {
			m[1].h.Format = tar.FormatPAX
			m[1].h.PAXRecords = map[string]string{"comment": "extension"}
			return m
		},
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			c, _ := specFixture(t, false)
			s := captureSpecs(t, c)
			b := specBytes(t, change(specMembers(t, s)))
			_, err := DecodeSpecArchive(context.Background(), b, digest(b))
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("unsafe spec archive admitted", err)
			}
			if name == "mode drift" || name == "content drift" || name == "duplicate" || name == "unknown path" {
				specOracle(t, &Specs{archive: b}, false)
			}
		})
	}
}

func TestSpecArchiveHashTrailerEndBlocksAndOwnershipAreRequired(t *testing.T) {
	c, _ := specFixture(t, true)
	s := captureSpecs(t, c)
	for name, b := range map[string][]byte{"missing end blocks": s.archive[:len(s.archive)-1024], "nonzero trailer": append(append([]byte{}, s.archive...), append([]byte{1}, make([]byte, 511)...)...), "concatenated archive": append(append([]byte{}, s.archive...), s.archive...)} {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeSpecArchive(context.Background(), b, digest(b)); !errors.Is(err, ErrInvalid) {
				t.Fatal("archive framing admitted", err)
			}
		})
	}
	if _, err := DecodeSpecArchive(context.Background(), s.archive, strings.Repeat("e", 64)); !errors.Is(err, ErrInvalid) {
		t.Fatal("unbound archive")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := DecodeSpecArchive(ctx, s.archive, s.ArchiveSHA256()); !errors.Is(err, ErrInvalid) {
		t.Fatal("cancelled decode")
	}
	for _, kind := range []string{"missing Compose", "spec symlink", "scripts symlink", "deployment writable", "wrong release hash", "generation drift"} {
		t.Run(kind, func(t *testing.T) {
			c, g := specFixture(t, false)
			switch kind {
			case "missing Compose":
				if os.Remove(filepath.Join(c.DeploymentDirectory, "docker-compose.yml")) != nil {
					t.Fatal("fixture")
				}
			case "spec symlink":
				p := filepath.Join(c.DeploymentDirectory, "deploy.sh")
				if os.Remove(p) != nil || os.Symlink("deploy_helpers.sh", p) != nil {
					t.Fatal("fixture")
				}
			case "scripts symlink":
				if os.Rename(filepath.Join(c.DeploymentDirectory, "scripts"), filepath.Join(c.DeploymentDirectory, "saved")) != nil || os.Symlink("saved", filepath.Join(c.DeploymentDirectory, "scripts")) != nil {
					t.Fatal("fixture")
				}
			case "deployment writable":
				if os.Chmod(c.DeploymentDirectory, 0777) != nil {
					t.Fatal("fixture")
				}
			case "wrong release hash":
				c.FileEvidenceSHA256 = strings.Repeat("e", 64)
			case "generation drift":
				write(t, g, "data/occupations.csv", []byte("changed"))
			}
			if _, err := CaptureSpecs(context.Background(), c); !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), c.DeploymentDirectory) {
				t.Fatal("unsafe capture admitted", err)
			}
		})
	}
}

func TestImmutableSpecArchiveRetentionAndExactRetry(t *testing.T) {
	c, _ := specFixture(t, true)
	s := captureSpecs(t, c)
	parent := t.TempDir()
	if os.Chmod(parent, 0700) != nil {
		t.Fatal("fixture")
	}
	p := filepath.Join(parent, "rollback.tar")
	if err := RetainSpecArchive(context.Background(), p, s); err != nil {
		t.Fatal(err)
	}
	before, err := os.Lstat(p)
	if err != nil || before.Mode().Perm() != 0600 {
		t.Fatal("retained archive mode")
	}
	if err := RetainSpecArchive(context.Background(), p, s); err != nil {
		t.Fatal(err)
	}
	after, err := os.Lstat(p)
	if err != nil || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) {
		t.Fatal("exact retry replaced immutable evidence")
	}
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 1 {
		t.Fatal("owned temporary residue")
	}
	if os.WriteFile(p, []byte("drift"), 0600) != nil {
		t.Fatal("fixture")
	}
	if err := RetainSpecArchive(context.Background(), p, s); !errors.Is(err, ErrInvalid) {
		t.Fatal("retention overwrote drift")
	}
	if b, err := os.ReadFile(p); err != nil || string(b) != "drift" {
		t.Fatal("drift evidence destroyed")
	}
	for _, kind := range []string{"symlink", "mode", "parent", "cancelled"} {
		t.Run(kind, func(t *testing.T) {
			parent := t.TempDir()
			if os.Chmod(parent, 0700) != nil {
				t.Fatal("fixture")
			}
			p := filepath.Join(parent, "rollback.tar")
			ctx := context.Background()
			switch kind {
			case "symlink":
				if os.Symlink("missing", p) != nil {
					t.Fatal("fixture")
				}
			case "mode":
				if os.WriteFile(p, s.archive, 0644) != nil || os.Chmod(p, 0644) != nil {
					t.Fatal("fixture")
				}
			case "parent":
				if os.Chmod(parent, 0755) != nil {
					t.Fatal("fixture")
				}
			case "cancelled":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			if err := RetainSpecArchive(ctx, p, s); !errors.Is(err, ErrInvalid) {
				t.Fatal("unsafe retention admitted", err)
			}
		})
	}
}

func TestNativeSpecSetMatchesDeployedPresenceContract(t *testing.T) {
	b, err := os.ReadFile("../../../deploy.sh")
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`(?s)DEPLOY_SPEC_FILES=\(\n(.*?)\n\)`).FindStringSubmatch(string(b))
	if len(m) != 2 || !reflect.DeepEqual(strings.Fields(m[1]), specNames) {
		t.Fatal("native archive silently omitted or reordered a deployed spec")
	}
}

func TestNativeDecoderAcceptsActualDeployedPythonUSTARWriter(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_PYTHON") == "1" {
			t.Fatal("required offline archive writer missing")
		}
		t.Skip("offline Python writer unavailable")
	}
	c, _ := specFixture(t, true)
	s := captureSpecs(t, c)
	members := specMembers(t, s)
	d := t.TempDir()
	snapshot := filepath.Join(d, "snapshot")
	if os.Mkdir(snapshot, 0700) != nil {
		t.Fatal("fixture")
	}
	names := make([]string, 0, len(members))
	for _, m := range members {
		p := filepath.Join(snapshot, m.h.Name)
		if os.MkdirAll(filepath.Dir(p), 0755) != nil || os.WriteFile(p, m.b, os.FileMode(m.h.Mode)) != nil || os.Chmod(p, os.FileMode(m.h.Mode)) != nil {
			t.Fatal("snapshot fixture")
		}
		names = append(names, m.h.Name)
	}
	source, err := os.ReadFile("../../../deploy.sh")
	if err != nil {
		t.Fatal(err)
	}
	part := strings.SplitN(string(source), "snapshot_active_deploy_specs() {", 2)
	if len(part) != 2 {
		t.Fatal("deployed writer missing")
	}
	part = strings.SplitN(part[1], "<<'PY'\n", 2)
	if len(part) != 2 {
		t.Fatal("deployed writer framing")
	}
	part = strings.SplitN(part[1], "\nPY\n", 2)
	if len(part) != 2 {
		t.Fatal("deployed writer end")
	}
	archive := filepath.Join(d, "actual-python.tar")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	args := append([]string{"-I", "-c", part[0], snapshot, archive}, names...)
	if err := exec.CommandContext(ctx, py, args...).Run(); err != nil {
		t.Fatal("actual deployed USTAR writer failed", err)
	}
	b, err := os.ReadFile(archive)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeSpecArchive(context.Background(), b, digest(b))
	if err != nil {
		t.Fatal("native decoder rejected actual deployed USTAR", err)
	}
	var original, legacy specDocument
	if json.Unmarshal([]byte(s.Body()), &original) != nil || json.Unmarshal([]byte(got.Body()), &legacy) != nil || !reflect.DeepEqual(original.Entries, legacy.Entries) || original.PresenceSHA256 != legacy.PresenceSHA256 {
		t.Fatal("legacy archive changed exact native presence/mode/hash identities")
	}
}
