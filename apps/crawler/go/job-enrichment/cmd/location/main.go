package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

type request struct {
	ID           uint64   `json:"id"`
	Operation    string   `json:"operation"`
	Locations    []string `json:"locations"`
	LocationType string   `json:"location_type"`
	Language     string   `json:"language"`
	Tracking     bool     `json:"tracking"`
	Negative     []string `json:"negative"`
	LocationID   int64    `json:"location_id"`
}

func main() {
	directory := flag.String("data-dir", "", "private location index directory")
	flag.Parse()
	if *directory == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "private index directory required")
		os.Exit(2)
	}
	resolver, err := enrichment.OpenLocations(filepath.Join(*directory, "locations.sqlite"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "location index unavailable")
		os.Exit(1)
	}
	defer resolver.Close()
	output := json.NewEncoder(os.Stdout)
	output.SetEscapeHTML(false)
	if output.Encode(map[string]any{"ready": true, "protocol": 1}) != nil {
		os.Exit(1)
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64<<10), 16<<20)
	for scanner.Scan() {
		var input request
		if json.Unmarshal(scanner.Bytes(), &input) != nil {
			fmt.Fprintln(os.Stderr, "invalid bounded request")
			os.Exit(1)
		}
		response := map[string]any{"id": input.ID}
		switch input.Operation {
		case "location_resolve":
			if len(input.Locations) > 10000 {
				err = fmt.Errorf("too many locations")
				break
			}
			var result []enrichment.LocationResult
			var misses []string
			var locationMisses []enrichment.LocationMiss
			result, misses, locationMisses, err = resolver.Resolve(input.Locations, input.LocationType, input.Language, input.Tracking, input.Negative)
			response["locations"] = result
			response["lookup_misses"] = misses
			response["location_misses"] = locationMisses
		case "location_ancestors":
			var ids []int64
			ids, err = resolver.Ancestors(input.LocationID)
			response["ancestors"] = ids
		case "location_display":
			var name *string
			name, err = resolver.DisplayName(input.LocationID)
			response["name"] = name
		default:
			err = fmt.Errorf("unsupported operation")
		}
		if err != nil {
			response = map[string]any{"id": input.ID, "error": "location operation failed"}
		}
		if output.Encode(response) != nil {
			os.Exit(1)
		}
	}
	if scanner.Err() != nil {
		fmt.Fprintln(os.Stderr, "input exceeds bound or read failed")
		os.Exit(1)
	}
}
