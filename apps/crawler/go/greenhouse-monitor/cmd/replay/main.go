// replay applies the production Go parser to captured Greenhouse bytes.
// It performs no network request and is used for exact Python/Go comparisons.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

func main() {
	body, err := io.ReadAll(io.LimitReader(os.Stdin, greenhouse.MaxBodyBytes+1))
	if err == nil && len(body) <= greenhouse.MaxBodyBytes {
		var inventory greenhouse.Inventory
		inventory, err = greenhouse.Parse(body)
		if err == nil {
			err = json.NewEncoder(os.Stdout).Encode(inventory)
		}
	} else if err == nil {
		err = fmt.Errorf("Greenhouse response exceeded 64 MiB")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
