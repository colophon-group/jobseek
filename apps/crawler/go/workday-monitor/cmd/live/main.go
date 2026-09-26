// live executes one configured Workday list cycle. The Python worker remains
// the sole scheduler and database writer for this bounded origin handoff.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

type output struct {
	URLs            []string `json:"urls,omitempty"`
	Advertised      int      `json:"advertised,omitempty"`
	Requests        int      `json:"requests"`
	Responses       int      `json:"responses"`
	TransportErrors int      `json:"transport_errors"`
	Bytes           int64    `json:"bytes"`
	Recovered       bool     `json:"recovered,omitempty"`
	Error           string   `json:"error,omitempty"`
	ErrorKind       string   `json:"error_kind,omitempty"`
	Status          int      `json:"status,omitempty"`
	Attempts        int      `json:"attempts,omitempty"`
	TDMPolicy       string   `json:"tdm_policy,omitempty"`
}

func run() error {
	var site workday.Site
	flag.StringVar(&site.Company, "company", "", "configured Workday company")
	flag.StringVar(&site.Instance, "instance", "", "configured Workday instance")
	flag.StringVar(&site.Name, "site", "", "configured Workday site")
	flag.Parse()
	if flag.NArg() != 0 {
		return errors.New("unexpected positional argument")
	}
	poster, err := workday.NewLivePoster(site)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := workday.DiscoverSingleSite(ctx, site, poster.Post)
	message := output{Requests: poster.Requests, Responses: poster.Responses, TransportErrors: poster.TransportErrors, Bytes: poster.Bytes}
	if err == nil {
		message.URLs = result.URLs
		message.Advertised = result.Advertised
		message.Recovered = result.Recovered
	} else {
		message.Error = err.Error()
		var fetch *workday.FetchError
		var reserved *workday.ReservationError
		if errors.As(err, &fetch) {
			message.Status = fetch.Status
			message.Attempts = fetch.Attempts
			message.ErrorKind = fetch.Kind
		}
		if errors.As(err, &reserved) {
			message.TDMPolicy = reserved.PolicyURL
			message.Error = "tdm-reservation=1"
		}
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(message); encodeErr != nil {
		return encodeErr
	}
	return err
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
