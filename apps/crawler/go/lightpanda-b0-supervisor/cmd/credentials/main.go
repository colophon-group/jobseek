package main

import (
	"flag"
	"fmt"
	bundle "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/claimantbundle"
	"os"
)

func run(args []string) error {
	if len(args) == 0 || args[0] != "prepare" {
		return fmt.Errorf("expected prepare")
	}
	flags := flag.NewFlagSet("prepare", flag.ContinueOnError)
	archive := flags.String("archive", "", "validated transport archive")
	digest := flags.String("archive-sha256", "", "validated archive digest")
	revision := flags.String("revision", "", "exact source revision")
	root := flags.String("root", "/home/deploy/.local/share/jobseek-lightpanda-claimant/credentials", "credential root")
	host := flags.String("service-host", "", "literal renderer IP")
	if err := flags.Parse(args[1:]); err != nil || flags.NArg() != 0 {
		return fmt.Errorf("invalid prepare arguments")
	}
	generation, err := bundle.Prepare(*archive, *digest, *revision, *root, *host)
	if err != nil {
		return err
	}
	fmt.Println(generation)
	return nil
}
func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "Lightpanda claimant credential installation failed:", err)
		os.Exit(1)
	}
}
