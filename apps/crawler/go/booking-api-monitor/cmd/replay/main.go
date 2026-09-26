package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	bookingapi "github.com/colophon-group/jobseek/apps/crawler/go/booking-api-monitor"
)

func main() {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, (2<<20)+1))
	if err != nil || len(input) > 2<<20 {
		fmt.Fprintln(os.Stderr, "Booking replay input exceeded 2 MiB")
		os.Exit(1)
	}
	inventory, err := bookingapi.Parse(input)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
