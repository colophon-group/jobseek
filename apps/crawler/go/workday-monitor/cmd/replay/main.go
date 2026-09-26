// replay checks a completed, locally captured Workday cycle without making
// network requests. It is an evidence tool for the first exclusive cohort.
package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

type traceRecord struct {
	Schema          string `json:"schema"`
	APIURL          string `json:"api_url"`
	Sequence        *int   `json:"sequence"`
	Method          string `json:"method"`
	URL             string `json:"url"`
	RequestBodyB64  string `json:"request_body_b64"`
	Status          int    `json:"status"`
	ResponseBodyB64 string `json:"response_body_b64"`
	Complete        *bool  `json:"complete"`
	Requests        int    `json:"requests"`
	Responses       int    `json:"responses"`
}

func readTrace(path string) (string, []traceRecord, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", nil, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), 2*1024*1024)
	line := 0
	apiURL := ""
	var responses []traceRecord
	footer := false
	for scanner.Scan() {
		line++
		var record traceRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return "", nil, fmt.Errorf("invalid trace JSON at line %d: %w", line, err)
		}
		switch {
		case line == 1 && record.Schema == "jobseek.workday-replay/v1" && record.APIURL != "":
			apiURL = record.APIURL
		case line == 1:
			return "", nil, errors.New("trace header is missing or unsupported")
		case footer:
			return "", nil, errors.New("trace has records after its footer")
		case record.Sequence != nil:
			if *record.Sequence != len(responses) || record.Method != "POST" || record.URL != apiURL || record.Status == 0 {
				return "", nil, fmt.Errorf("trace response %d has invalid identity or sequence", len(responses))
			}
			responses = append(responses, record)
		case record.Complete != nil:
			if !*record.Complete || record.Requests != len(responses) || record.Responses != len(responses) || len(responses) == 0 {
				return "", nil, errors.New("trace footer does not prove a complete cycle")
			}
			footer = true
		default:
			return "", nil, fmt.Errorf("unexpected trace record at line %d", line)
		}
	}
	if err := scanner.Err(); err != nil {
		return "", nil, err
	}
	if !footer {
		return "", nil, errors.New("trace is incomplete")
	}
	return apiURL, responses, nil
}

func run() error {
	var path string
	var site workday.Site
	flag.StringVar(&path, "trace", "", "completed local Workday .jsonl trace")
	flag.StringVar(&site.Company, "company", "", "configured Workday company token")
	flag.StringVar(&site.Instance, "instance", "", "configured Workday wd instance")
	flag.StringVar(&site.Name, "site", "", "configured Workday site")
	flag.Parse()
	if path == "" || flag.NArg() != 0 {
		return errors.New("trace and configured Workday identity are required")
	}
	apiURL, responses, err := readTrace(path)
	if err != nil {
		return err
	}
	consumed := 0
	poster := func(_ context.Context, url string, body []byte) ([]byte, error) {
		if consumed == len(responses) {
			return nil, errors.New("Go requested more Workday pages than the trace contains")
		}
		record := responses[consumed]
		consumed++
		want, err := base64.StdEncoding.DecodeString(record.RequestBodyB64)
		if err != nil {
			return nil, fmt.Errorf("invalid request base64 at response %d: %w", consumed-1, err)
		}
		if url != apiURL || string(body) != string(want) {
			return nil, fmt.Errorf("Go request differs from capture at response %d", consumed-1)
		}
		if record.Status != 200 {
			return nil, fmt.Errorf("response %d has status %d; retry policy replay is not yet supported", consumed-1, record.Status)
		}
		return base64.StdEncoding.DecodeString(record.ResponseBodyB64)
	}
	result, err := workday.DiscoverSingleSite(context.Background(), site, poster)
	if err != nil {
		return err
	}
	if consumed != len(responses) {
		return fmt.Errorf("Go consumed %d of %d captured Workday responses", consumed, len(responses))
	}
	sort.Strings(result.URLs)
	digest := sha256.Sum256([]byte(strings.Join(result.URLs, "\n")))
	return json.NewEncoder(os.Stdout).Encode(struct {
		URLs       int    `json:"urls"`
		Advertised int    `json:"advertised"`
		Requests   int    `json:"requests"`
		Recovered  bool   `json:"recovered"`
		URLSHA256  string `json:"url_sha256"`
	}{len(result.URLs), result.Advertised, consumed, result.Recovered, fmt.Sprintf("%x", digest)})
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
