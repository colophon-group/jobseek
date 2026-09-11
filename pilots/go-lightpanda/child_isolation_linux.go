//go:build linux && !densitybench

package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
)

const (
	runtimeV1ChildIsolationCheckFlag = "--runtime-v1-child-isolation-check"
	runtimeV1ChildIsolationProbeFlag = "--runtime-v1-child-isolation-probe"
	controllerCapabilitiesHex        = "00000000000000e0"
	noCapabilitiesHex                = "0000000000000000"
)

func attestRuntimeV1ChildIsolation(privateKeyPath string) error {
	if err := attestExactProcessStatus(
		"/proc/self/status",
		lightpandaControllerUID,
		lightpandaControllerGID,
		controllerCapabilitiesHex,
		controllerCapabilitiesHex,
		controllerCapabilitiesHex,
		controllerCapabilitiesHex,
		controllerCapabilitiesHex,
		"",
	); err != nil {
		return errors.New("runtime-v1 controller privilege boundary is invalid")
	}
	if groups, err := os.Getgroups(); err != nil || len(groups) != 0 {
		return errors.New("runtime-v1 controller supplementary groups are invalid")
	}

	key, err := os.Open(privateKeyPath)
	if err != nil {
		return errors.New("runtime-v1 controller cannot read its private key")
	}
	defer key.Close()
	info, err := key.Stat()
	metadata, ok := infoSyscallStat(info)
	if err != nil || !ok || !info.Mode().IsRegular() || info.Mode().Perm() != 0o400 ||
		metadata.Nlink != 1 || metadata.Uid != lightpandaControllerUID ||
		metadata.Gid != lightpandaControllerGID {
		return errors.New("runtime-v1 server private key ownership is invalid")
	}
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, key.Fd(), syscall.F_GETFD, 0)
	if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
		return errors.New("runtime-v1 server private key descriptor is inheritable")
	}

	attributes, err := lightpandaProcessAttributes(true)
	if err != nil {
		return errors.New("runtime-v1 child identity is unavailable")
	}
	command := isolatedChildCommand(
		"/usr/local/bin/go-lightpanda",
		runtimeV1ChildIsolationProbeFlag,
		privateKeyPath,
		strconv.Itoa(os.Getpid()),
		strconv.FormatUint(uint64(key.Fd()), 10),
	)
	command.Env = append([]string(nil), lightpandaChildEnvironment...)
	command.SysProcAttr = attributes
	output := &boundedBuffer{limit: maxProcessLogBytes}
	command.Stdout = output
	command.Stderr = output
	if err := command.Run(); err != nil {
		return fmt.Errorf("runtime-v1 child privilege boundary probe failed: %s", output.String())
	}
	if output.String() != "" {
		return errors.New("runtime-v1 child privilege boundary probe emitted unexpected output")
	}
	return nil
}

func runRuntimeV1ChildIsolationProbe(args []string) error {
	if len(args) != 3 || os.Getppid() <= 0 {
		return fmt.Errorf(
			"invalid child isolation probe arguments: count=%d parent=%d",
			len(args),
			os.Getppid(),
		)
	}
	parentPID, err := strconv.Atoi(args[1])
	if err != nil || parentPID != os.Getppid() || parentPID <= 0 {
		return errors.New("invalid child isolation probe parent")
	}
	parentKeyFD, err := strconv.ParseUint(args[2], 10, 31)
	if err != nil || parentKeyFD < 3 {
		return errors.New("invalid child isolation probe descriptor")
	}
	if err := attestExactProcessStatus(
		"/proc/self/status",
		lightpandaChildUID,
		lightpandaChildGID,
		noCapabilitiesHex,
		noCapabilitiesHex,
		noCapabilitiesHex,
		controllerCapabilitiesHex,
		noCapabilitiesHex,
		strconv.FormatUint(uint64(lightpandaChildGID), 10),
	); err != nil {
		return err
	}
	if groups, err := os.Getgroups(); err != nil || len(groups) != 1 || groups[0] != lightpandaChildGID {
		return errors.New("child retained supplementary groups")
	}
	environment := append([]string(nil), os.Environ()...)
	expectedEnvironment := append([]string(nil), lightpandaChildEnvironment...)
	sort.Strings(environment)
	sort.Strings(expectedEnvironment)
	if strings.Join(environment, "\x00") != strings.Join(expectedEnvironment, "\x00") {
		return errors.New("child inherited an unexpected environment")
	}

	for _, path := range []string{
		args[0],
		fmt.Sprintf("/proc/%d/fd/%d", parentPID, parentKeyFD),
		fmt.Sprintf("/proc/%d/environ", parentPID),
		fmt.Sprintf("/proc/%d/mem", parentPID),
		fmt.Sprintf("/proc/%d/root%s", parentPID, args[0]),
	} {
		if _, err := os.ReadFile(path); !errors.Is(err, os.ErrPermission) {
			return errors.New("child can read controller credentials or process state")
		}
	}
	if err := syscall.Kill(parentPID, 0); !errors.Is(err, syscall.EPERM) {
		return errors.New("child can signal the controller")
	}
	if err := attestNoInheritedFileDescriptors(); err != nil {
		return err
	}
	return nil
}

func attestExactProcessStatus(
	path string,
	uid uint32,
	gid uint32,
	capInheritable string,
	capPermitted string,
	capEffective string,
	capBounding string,
	capAmbient string,
	groups string,
) error {
	contents, err := os.ReadFile(path)
	if err != nil || len(contents) == 0 || len(contents) > 64*1024 {
		return errors.New("invalid process status")
	}
	fields := make(map[string]string)
	for _, line := range strings.Split(strings.TrimSuffix(string(contents), "\n"), "\n") {
		name, value, found := strings.Cut(line, ":")
		if found {
			fields[name] = strings.TrimSpace(value)
		}
	}
	expectedUID := fmt.Sprintf("%d\t%d\t%d\t%d", uid, uid, uid, uid)
	expectedGID := fmt.Sprintf("%d\t%d\t%d\t%d", gid, gid, gid, gid)
	if fields["Uid"] != expectedUID || fields["Gid"] != expectedGID || fields["Groups"] != groups ||
		fields["CapInh"] != capInheritable || fields["CapPrm"] != capPermitted ||
		fields["CapEff"] != capEffective || fields["CapBnd"] != capBounding ||
		fields["CapAmb"] != capAmbient || fields["NoNewPrivs"] != "1" {
		return errors.New("process privilege status is invalid")
	}
	return nil
}

func attestNoInheritedFileDescriptors() error {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return errors.New("child file descriptor inventory is unavailable")
	}
	for _, entry := range entries {
		descriptor, err := strconv.Atoi(entry.Name())
		if err != nil || descriptor < 0 {
			return errors.New("child file descriptor inventory is invalid")
		}
		if descriptor <= 2 {
			continue
		}
		if target, err := os.Readlink(filepath.Join("/proc/self/fd", entry.Name())); err == nil {
			if target == "anon_inode:[eventpoll]" || target == "anon_inode:[eventfd]" {
				continue
			}
			return fmt.Errorf("child inherited unexpected descriptor %d: %s", descriptor, target)
		} else if !errors.Is(err, os.ErrNotExist) {
			return errors.New("child file descriptor inventory is invalid")
		}
	}
	return nil
}

func infoSyscallStat(info os.FileInfo) (*syscall.Stat_t, bool) {
	if info == nil {
		return nil, false
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	return metadata, ok
}
