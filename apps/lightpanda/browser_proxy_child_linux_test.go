//go:build linux

package main

import (
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestPinnedProxyChildExecPortIsolation(t *testing.T) {
	if _, err := pinnedProxyNetworkABI(); err != nil {
		t.Skip("requires Linux network Landlock ABI 4; installed qualification must run this case without skips")
	}
	allowed, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer allowed.Close()
	denied, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer denied.Close()
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command(binary, "-test.run=^TestPinnedProxyChildPortFixture$", "--", "sandbox", allowed.Addr().String(), denied.Addr().String())
	output, err := command.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "proxy port isolated across exec") {
		t.Fatalf("child network boundary failed: %v: %s", err, output)
	}
}

// Re-execute the test binary after restriction, so the assertion covers the
// browser's eventual exec boundary rather than only the calling Go thread.
func TestPinnedProxyChildPortFixture(t *testing.T) {
	separator := -1
	for i, arg := range os.Args {
		if arg == "--" {
			separator = i
			break
		}
	}
	if separator < 0 {
		return
	}
	args := os.Args[separator+1:]
	if len(args) != 3 {
		t.Fatal("invalid isolated fixture")
	}
	if args[0] == "sandbox" {
		_, port, err := net.SplitHostPort(args[1])
		if err != nil {
			t.Fatal("invalid fixture port")
		}
		binary, err := os.Executable()
		if err != nil {
			t.Fatal(err)
		}
		if err := execPinnedProxyChild([]string{port, binary, "-test.run=^TestPinnedProxyChildPortFixture$", "--", "check", args[1], args[2]}); err != nil {
			t.Fatal("network sandbox could not be enforced")
		}
		return
	}
	if args[0] != "check" {
		t.Fatal("invalid isolated fixture mode")
	}
	connection, err := net.DialTimeout("tcp4", args[1], time.Second)
	if err != nil {
		t.Fatal("allowed proxy port was blocked")
	}
	connection.Close()
	connection, err = net.DialTimeout("tcp4", args[2], time.Second)
	if connection != nil {
		connection.Close()
	}
	if !errors.Is(err, syscall.EACCES) {
		t.Fatal("another loopback port was reachable")
	}
	for _, item := range os.Environ() {
		if !strings.HasPrefix(item, "LIGHTPANDA_") {
			t.Fatal("controller environment crossed replacement exec")
		}
	}
	_, port, _ := net.SplitHostPort(args[1])
	if _, err := strconv.ParseUint(port, 10, 16); err != nil {
		t.Fatal("invalid accepted fixture")
	}
	fmt.Println("proxy port isolated across exec")
}
