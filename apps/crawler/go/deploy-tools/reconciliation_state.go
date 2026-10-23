package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

var reconciliationRevision = regexp.MustCompile("^[0-9a-f]{40}$")
var reconciliationDigest = regexp.MustCompile("^[0-9a-f]{64}$")

func readReconciliationValue(root, name string, pattern *regexp.Regexp, label string) (string, error) {
	file, err := regularOpen(filepath.Join(root, name))
	if err != nil {
		return "", fmt.Errorf("reconciliation %s is unavailable", label)
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil {
		return "", fmt.Errorf("reconciliation %s is unavailable", label)
	}
	value := strings.Trim(string(raw), " \t\n\r\v\f\x1c\x1d\x1e\x1f")
	if len(raw) > 4096 || !pattern.MatchString(value) {
		return "", fmt.Errorf("reconciliation %s is invalid", label)
	}
	return value, nil
}

func installReconciliationValue(root, name, value string, uid, gid int) (err error) {
	file, err := os.CreateTemp(root, "."+name+".")
	if err != nil {
		return err
	}
	path := file.Name()
	defer func() { file.Close(); os.Remove(path) }()
	if _, err = file.WriteString(value + "\n"); err != nil {
		return err
	}
	if err = file.Sync(); err != nil {
		return err
	}
	if err = file.Chmod(0640); err != nil {
		return err
	}
	if uid != -1 || gid != -1 {
		if err = file.Chown(uid, gid); err != nil {
			return err
		}
	}
	if err = file.Sync(); err != nil {
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if err = os.Rename(path, filepath.Join(root, name)); err != nil {
		return err
	}
	return syncDirectory(root)
}

func installReconciliationState(root, revision, digest string, uid, gid int) error {
	if !reconciliationRevision.MatchString(revision) {
		return errors.New("deployment revision must be a full lowercase Git commit SHA")
	}
	if !reconciliationDigest.MatchString(digest) {
		return errors.New("wrapper digest must be a full lowercase SHA-256")
	}
	if err := os.MkdirAll(root, 0750); err != nil {
		return err
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("reconciliation state directory is unavailable")
	}
	if err := os.Chmod(root, 0750); err != nil {
		return err
	}
	if uid != -1 || gid != -1 {
		if err := os.Chown(root, uid, gid); err != nil {
			return err
		}
	}
	if err := installReconciliationValue(root, "deployed-sha", revision, uid, gid); err != nil {
		return err
	}
	return installReconciliationValue(root, "wrapper-sha256", digest, uid, gid)
}

func runReconciliationState(args []string, stdout, stderr io.Writer) int {
	global := flag.NewFlagSet("jobseek-reconciliation-state", flag.ContinueOnError)
	global.SetOutput(stderr)
	root := global.String("state-root", "/var/lib/jobseek-reconciliation", "reconciliation state directory")
	if err := global.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	rest := global.Args()
	if len(rest) == 0 {
		fmt.Fprintln(stderr, "ERROR: expected install or check")
		return 2
	}
	command := rest[0]
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(stderr)
	revision, digest, group := "", "", "deploy"
	if command == "install" {
		flags.StringVar(&revision, "revision", "", "full reviewed source revision")
		flags.StringVar(&digest, "wrapper-sha256", "", "installed wrapper SHA-256")
		flags.StringVar(&group, "group", "deploy", "state reader group")
	} else if command == "check" {
		flags.StringVar(&revision, "expected-revision", "", "expected deployment source revision")
		flags.StringVar(&digest, "expected-wrapper-sha256", "", "expected installed wrapper SHA-256")
	} else {
		fmt.Fprintln(stderr, "ERROR: expected install or check")
		return 2
	}
	if err := flags.Parse(rest[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if flags.NArg() != 0 {
		return 2
	}
	if command == "install" && (revision == "" || digest == "") {
		fmt.Fprintln(stderr, "ERROR: install requires revision and wrapper digest")
		return 2
	}
	reject := func(err error) int { fmt.Fprintln(stderr, "ERROR:", err); return 1 }
	if command == "install" {
		if os.Geteuid() != 0 {
			return reject(errors.New("revision installation must run as root"))
		}
		if !reconciliationRevision.MatchString(sourceRevision) || revision != sourceRevision {
			return reject(errors.New("deployment revision does not match the compiled source"))
		}
		selected, err := user.LookupGroup(group)
		if err != nil {
			return reject(fmt.Errorf("required group '%s' is unavailable", group))
		}
		gid, err := strconv.Atoi(selected.Gid)
		if err != nil {
			return reject(errors.New("required group identity is invalid"))
		}
		if err := installReconciliationState(*root, revision, digest, 0, gid); err != nil {
			return reject(err)
		}
	}
	actualRevision, err := readReconciliationValue(*root, "deployed-sha", reconciliationRevision, "deployment revision")
	if err != nil {
		return reject(err)
	}
	if command == "check" && revision != "" && actualRevision != revision {
		return reject(errors.New("deployed reconciliation revision does not match the expected SHA"))
	}
	actualDigest, err := readReconciliationValue(*root, "wrapper-sha256", reconciliationDigest, "wrapper digest")
	if err != nil {
		return reject(err)
	}
	if command == "check" && digest != "" && actualDigest != digest {
		return reject(errors.New("deployed reconciliation wrapper does not match the expected SHA-256"))
	}
	if !reconciliationRevision.MatchString(sourceRevision) || actualRevision != sourceRevision {
		return reject(errors.New("deployed reconciliation revision does not match the compiled source"))
	}
	fmt.Fprintf(stdout, "verified reconciliation wrapper %s at revision %s\n", actualDigest, actualRevision)
	return 0
}
