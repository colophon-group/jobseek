package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func selectedFixture(t *testing.T) (ActiveSelectionConfig, generation) {
	t.Helper()
	g := fixture(t)
	deployment, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil || os.Chmod(deployment, 0700) != nil {
		t.Fatal("selected deployment fixture")
	}
	root := filepath.Join(deployment, ".crawler-release-generations")
	if os.Mkdir(root, 0700) != nil {
		t.Fatal("selected generation root fixture")
	}
	target := filepath.Join(root, "release-active")
	if os.Rename(g.directory, target) != nil {
		t.Fatal("selected generation fixture")
	}
	g.directory = target
	if os.Symlink(target, filepath.Join(deployment, ".crawler-active-release")) != nil || os.Link(filepath.Join(target, "success.env"), filepath.Join(deployment, ".crawler-deploy-success.env")) != nil {
		t.Fatal("actual deployed selection shape fixture")
	}
	f := verify(t, g)
	return ActiveSelectionConfig{deployment, target, fixtureOwner, f.SHA256()}, g
}

func TestSelectedActiveFilesBindRealPointerMarkerAndExactFiles(t *testing.T) {
	c, _ := selectedFixture(t)
	got, err := ObserveSelectedActiveFiles(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := ObserveSelectedActiveFiles(context.Background(), c)
	if err != nil || retry.SHA256() != got.SHA256() || strings.Contains(got.Body(), c.DeploymentDirectory) || strings.Contains(got.Body(), "fixture-sensitive-value") {
		t.Fatal("active selection exact retry/privacy")
	}
	var body struct {
		Version          string `json:"version"`
		FilesSHA256      string `json:"file_evidence_sha256"`
		Source           string `json:"selected_deploy_revision"`
		RuntimeAdmission bool   `json:"runtime_admission"`
	}
	if json.Unmarshal([]byte(got.Body()), &body) != nil || body.Version != "jobseek.crawler-selected-active-files/v1" || body.FilesSHA256 != c.FileEvidenceSHA256 || body.Source != fixtureRevision || body.RuntimeAdmission || digest([]byte(got.Body())) != got.SHA256() {
		t.Fatal("active selection identity/admission")
	}
}

func TestSelectedActiveFilesAgreeWithDeployedPointerLoader(t *testing.T) {
	c, g := selectedFixture(t)
	deployed, err := os.ReadFile(filepath.Join("..", "..", "..", "deploy.sh"))
	if err != nil {
		t.Fatal("deployed pointer-loader reference")
	}
	_, function, ok := strings.Cut(string(deployed), "load_active_release() {\n")
	if !ok {
		t.Fatal("deployed loader start")
	}
	function, _, ok = strings.Cut(function, "\n}\n\nverify_active_deploy_snapshot()")
	if !ok {
		t.Fatal("deployed loader boundary")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	// Execute only the actual file-only loader on private fixture paths. No
	// image, queue, SQL, credentials, selected specs or deployment effects.
	cmd := exec.CommandContext(ctx, "/bin/bash", "-euc", "load_active_release() {\n"+function+"\n}\nload_active_release\nprintf '%s\\n%s\\n' \"$ACTIVE_RELEASE_DIR\" \"$DEPLOY_SUCCESS_FILE\"")
	cmd.Env = []string{"PATH=/usr/bin:/bin", "ACTIVE_RELEASE_ROOT=" + filepath.Dir(g.directory), "ACTIVE_RELEASE_POINTER=" + filepath.Join(c.DeploymentDirectory, ".crawler-active-release")}
	out, err := cmd.CombinedOutput()
	if err != nil || string(out) != g.directory+"\n"+filepath.Join(g.directory, "success.env")+"\n" {
		t.Fatal("native selection fixture differs from actual deployed loader", err)
	}
	if _, err := ObserveSelectedActiveFiles(ctx, c); err != nil {
		t.Fatal("deployed selected files refused", err)
	}
}

func TestSelectedActiveFilesRefuseUnselectedAliasesUnsafeModesAndMarkerDrift(t *testing.T) {
	for _, fault := range []string{"wrong files", "unselected generation", "deployment alias", "generation alias", "root alias", "regular pointer", "relative pointer", "escaping pointer", "absent pointer", "absent marker", "marker symlink", "marker bytes", "world-writable deployment", "world-writable root", "world-writable generation", "world-writable marker"} {
		t.Run(fault, func(t *testing.T) {
			c, g := selectedFixture(t)
			pointer := filepath.Join(c.DeploymentDirectory, ".crawler-active-release")
			marker := filepath.Join(c.DeploymentDirectory, ".crawler-deploy-success.env")
			root := filepath.Dir(g.directory)
			var err error
			switch fault {
			case "wrong files":
				c.FileEvidenceSHA256 = strings.Repeat("b", 64)
			case "unselected generation":
				c.GenerationDirectory = filepath.Join(root, "release-not-selected")
			case "deployment alias":
				alias := filepath.Join(filepath.Dir(c.DeploymentDirectory), "alias")
				err = os.Symlink(c.DeploymentDirectory, alias)
				c.DeploymentDirectory = alias
			case "generation alias", "root alias":
				p := g.directory
				if fault == "root alias" {
					p = root
				}
				err = os.Rename(p, p+"-physical")
				if err == nil {
					err = os.Symlink(p+"-physical", p)
				}
			case "regular pointer", "relative pointer", "escaping pointer":
				err = os.Remove(pointer)
				if err == nil {
					switch fault {
					case "regular pointer":
						err = os.WriteFile(pointer, []byte(g.directory), 0600)
					case "relative pointer":
						err = os.Symlink(".crawler-release-generations/release-active", pointer)
					case "escaping pointer":
						err = os.Symlink(filepath.Dir(c.DeploymentDirectory), pointer)
					}
				}
			case "absent pointer":
				err = os.Remove(pointer)
			case "absent marker":
				err = os.Remove(marker)
			case "marker symlink":
				err = os.Remove(marker)
				if err == nil {
					err = os.Symlink(filepath.Join(g.directory, "success.env"), marker)
				}
			case "marker bytes":
				err = os.Remove(marker)
				if err == nil {
					err = os.WriteFile(marker, []byte("PRIVATE_VALUE=fixture-sensitive-value\n"), 0600)
				}
			case "world-writable deployment":
				err = os.Chmod(c.DeploymentDirectory, 0777)
			case "world-writable root":
				err = os.Chmod(root, 0777)
			case "world-writable generation":
				err = os.Chmod(g.directory, 0777)
			case "world-writable marker":
				err = os.Chmod(marker, 0666)
			}
			if err != nil {
				t.Fatal("selection fault fixture", err)
			}
			if _, err := ObserveSelectedActiveFiles(context.Background(), c); !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), c.DeploymentDirectory) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("unsafe/unselected active generation admitted/disclosed", err)
			}
		})
	}
}

func TestSelectedActiveFilesRefuseReplacementAcrossCompleteObservation(t *testing.T) {
	for _, seam := range []string{"selection_observed", "selected_files_verified"} {
		for _, fault := range []string{"same-target pointer", "same-byte marker", "deployment replacement"} {
			t.Run(seam+"/"+fault, func(t *testing.T) {
				c, g := selectedFixture(t)
				hook := func(phase string) {
					if phase != seam {
						return
					}
					var err error
					switch fault {
					case "same-target pointer":
						p := filepath.Join(c.DeploymentDirectory, ".crawler-active-release")
						// Keep the old inode alive so this deterministically tests
						// replacement even on inode-reusing local filesystems.
						err = os.Rename(p, p+"-previous")
						if err == nil {
							err = os.Symlink(g.directory, p)
						}
					case "same-byte marker":
						p := filepath.Join(c.DeploymentDirectory, ".crawler-deploy-success.env")
						err = os.Rename(p, p+"-previous")
						if err == nil {
							err = os.WriteFile(p, read(t, g, "success.env"), 0600)
						}
					case "deployment replacement":
						err = os.Rename(c.DeploymentDirectory, c.DeploymentDirectory+"-previous")
						if err == nil {
							err = os.Mkdir(c.DeploymentDirectory, 0700)
						}
					}
					if err != nil {
						t.Fatal("selection replacement fixture", err)
					}
				}
				if _, err := observeSelectedActiveFiles(context.Background(), c, hook); !errors.Is(err, ErrInvalid) {
					t.Fatal("active selection replacement admitted", err)
				}
			})
		}
	}
}
