package releaseevidence

import (
	"context"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
)

// ActiveSelection binds verified files to the deployed active pointer and
// success marker. It does not authenticate builds, incoming/rollback selection,
// image execution, writer exclusion, SQL barriers or permission to cut over.
type ActiveSelection struct{ body, hash string }

func (s *ActiveSelection) Body() string   { return s.body }
func (s *ActiveSelection) SHA256() string { return s.hash }

type ActiveSelectionConfig struct {
	DeploymentDirectory, GenerationDirectory, Owner, FileEvidenceSHA256 string
}

var selectedGenerationName = regexp.MustCompile(`^[A-Za-z0-9._-]{1,255}$`)

func cleanSelectionPath(p string) bool {
	return filepath.IsAbs(p) && filepath.Clean(p) == p && p != "/" && len(p) <= 4096 && !strings.ContainsRune(p, 0)
}

type selectedPathIdentity struct {
	Device, Inode uint64
	UID, GID      uint32
	Mode          fs.FileMode
}

func selectedIdentity(info fs.FileInfo) (selectedPathIdentity, bool) {
	if info == nil {
		return selectedPathIdentity{}, false
	}
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok || (s.Uid != 0 && s.Uid != uint32(os.Geteuid())) {
		return selectedPathIdentity{}, false
	}
	return selectedPathIdentity{uint64(s.Dev), uint64(s.Ino), s.Uid, s.Gid, info.Mode()}, true
}

func selectedDirectory(info fs.FileInfo) bool {
	_, trusted := selectedIdentity(info)
	return trusted && info.Mode().IsDir() && info.Mode().Perm()&0022 == 0
}

type activeSelectionSnapshot struct {
	Deployment, Root, Generation, Pointer, Success selectedPathIdentity
	Target, SuccessSHA256                          string
	PointerTime, SuccessTime, SuccessSize          int64
}

// Fixed paths mirror load_active_release and publish_legacy_success_marker in
// deploy.sh. Nothing follows a caller-selected pointer or reads credentials.
func selectionSnapshot(ctx context.Context, c ActiveSelectionConfig) (*activeSelectionSnapshot, error) {
	if ctx == nil || ctx.Err() != nil || !cleanSelectionPath(c.DeploymentDirectory) || !cleanSelectionPath(c.GenerationDirectory) || !ownerPattern.MatchString(c.Owner) || !shaPattern.MatchString(c.FileEvidenceSHA256) {
		return nil, reject("explicit active selection inputs")
	}
	rootPath := filepath.Join(c.DeploymentDirectory, ".crawler-release-generations")
	name := filepath.Base(c.GenerationDirectory)
	if !selectedGenerationName.MatchString(name) || name == "." || name == ".." || filepath.Join(rootPath, name) != c.GenerationDirectory {
		return nil, reject("active generation within fixed deployment root")
	}
	paths := []string{c.DeploymentDirectory, rootPath, c.GenerationDirectory}
	identities := []selectedPathIdentity{}
	for _, path := range paths {
		physical, err := filepath.EvalSymlinks(path)
		info, statErr := os.Lstat(path)
		if err != nil || physical != path || statErr != nil || !selectedDirectory(info) {
			return nil, reject("physical protected active selection directories")
		}
		identity, _ := selectedIdentity(info)
		identities = append(identities, identity)
	}
	deployment, err := os.OpenRoot(c.DeploymentDirectory)
	if err != nil {
		return nil, reject("active deployment open")
	}
	defer deployment.Close()
	opened, err := deployment.Stat(".")
	openedID, trusted := selectedIdentity(opened)
	if err != nil || !trusted || openedID != identities[0] {
		return nil, reject("active deployment replaced")
	}
	pointer, err := deployment.Lstat(".crawler-active-release")
	pointerID, trusted := selectedIdentity(pointer)
	if err != nil || !trusted || pointer.Mode()&os.ModeSymlink == 0 {
		return nil, reject("trusted active release symlink required")
	}
	target, err := deployment.Readlink(".crawler-active-release")
	if err != nil || target != c.GenerationDirectory {
		return nil, reject("active pointer differs from requested generation")
	}
	success, err := deployment.Lstat(".crawler-deploy-success.env")
	successID, trusted := selectedIdentity(success)
	if err != nil || !trusted || !success.Mode().IsRegular() || success.Mode().Perm()&0022 != 0 || success.Size() > 64<<10 {
		return nil, reject("trusted active success marker required")
	}
	r := &reader{ctx: ctx, root: deployment, hashes: map[string]string{}}
	body, err := r.read(".crawler-deploy-success.env", 64<<10)
	if err != nil {
		return nil, reject("active success marker readback")
	}
	// Recheck all named paths and the anchored directory after reading the
	// marker. Content stays private; only its digest enters the evidence.
	for n, path := range paths {
		info, err := os.Lstat(path)
		identity, trusted := selectedIdentity(info)
		if err != nil || !trusted || identity != identities[n] {
			return nil, reject("active selection path replacement")
		}
	}
	again, err := deployment.Lstat(".crawler-active-release")
	id, trusted := selectedIdentity(again)
	if err != nil || !trusted || id != pointerID || !pointer.ModTime().Equal(again.ModTime()) {
		return nil, reject("active release pointer replacement")
	}
	againTarget, err := deployment.Readlink(".crawler-active-release")
	if err != nil || againTarget != target {
		return nil, reject("active release pointer readback")
	}
	again, err = deployment.Lstat(".crawler-deploy-success.env")
	id, trusted = selectedIdentity(again)
	if err != nil || !trusted || id != successID || success.Size() != again.Size() || !success.ModTime().Equal(again.ModTime()) || ctx.Err() != nil {
		return nil, reject("active success marker replacement")
	}
	return &activeSelectionSnapshot{identities[0], identities[1], identities[2], pointerID, successID, target, digest(body), pointer.ModTime().UnixNano(), success.ModTime().UnixNano(), success.Size()}, nil
}

// ObserveSelectedActiveFiles must execute under the shared host mutation lock.
// It performs filesystem reads only and refuses unselected or aliased files.
func ObserveSelectedActiveFiles(ctx context.Context, c ActiveSelectionConfig) (*ActiveSelection, error) {
	return observeSelectedActiveFiles(ctx, c, nil)
}

func observeSelectedActiveFiles(ctx context.Context, c ActiveSelectionConfig, hook func(string)) (*ActiveSelection, error) {
	before, err := selectionSnapshot(ctx, c)
	if err != nil {
		return nil, err
	}
	if hook != nil {
		hook("selection_observed")
	}
	files, err := VerifyFiles(ctx, c.GenerationDirectory, c.Owner)
	if err != nil || files.SHA256() != c.FileEvidenceSHA256 {
		return nil, reject("selected active file evidence differs")
	}
	var f document
	if json.Unmarshal([]byte(files.Body()), &f) != nil || f.FileSHA256["success.env"] != before.SuccessSHA256 {
		return nil, reject("selected generation and live success marker differ")
	}
	if hook != nil {
		hook("selected_files_verified")
	}
	after, err := selectionSnapshot(ctx, c)
	if err != nil || *before != *after || ctx.Err() != nil {
		return nil, reject("complete active selection readback")
	}
	identity, _ := json.Marshal(before)
	body, err := json.Marshal(struct {
		Version               string `json:"version"`
		RuntimeAdmission      bool   `json:"runtime_admission"`
		FileEvidenceSHA256    string `json:"file_evidence_sha256"`
		DeploymentSHA256      string `json:"deployment_directory_sha256"`
		GenerationSHA256      string `json:"generation_directory_sha256"`
		SelectionSHA256       string `json:"selection_identity_sha256"`
		LiveSuccessSHA256     string `json:"live_success_sha256"`
		SelectedDeploySource  string `json:"selected_deploy_revision"`
		SelectedDataSource    string `json:"selected_data_revision"`
		RuntimeContractSHA256 string `json:"runtime_contract_sha256"`
	}{"jobseek.crawler-selected-active-files/v1", false, files.SHA256(), digest([]byte(c.DeploymentDirectory)), digest([]byte(c.GenerationDirectory)), digest(identity), before.SuccessSHA256, f.DeployRevision, f.DataRevision, f.RuntimeContractSHA256})
	if err != nil {
		return nil, reject("active selection encoding")
	}
	return &ActiveSelection{string(body), digest(body)}, nil
}
