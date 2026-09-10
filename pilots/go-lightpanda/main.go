//go:build !densitybench

package main

import "os"

func main() {
	os.Exit(runCLI(os.Args[1:], os.Stdout))
}
