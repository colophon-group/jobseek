// detail-live fetches and projects one selected Workday detail URL.
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
	Content         *workday.DetailContent `json:"content,omitempty"`
	Gone            bool                   `json:"gone,omitempty"`
	Requests        int                    `json:"requests"`
	Responses       int                    `json:"responses"`
	TransportErrors int                    `json:"transport_errors"`
	Bytes           int64                  `json:"bytes"`
	Status          int                    `json:"status,omitempty"`
	TDMPolicy       string                 `json:"tdm_policy,omitempty"`
	Error           string                 `json:"error,omitempty"`
	ErrorKind       string                 `json:"error_kind,omitempty"`
}

func run() error {
	var source string
	var aliases stringList
	flag.StringVar(&source, "url", "", "canonical Workday job URL")
	flag.Var(&aliases, "facility-tenant-alias", "configured location facility tenant alias (repeatable)")
	flag.Parse()
	if flag.NArg() != 0 {
		return errors.New("unexpected positional argument")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := workday.FetchWorkdayDetail(ctx, source, aliases)
	message := output{
		Gone: result.Gone, Requests: result.Requests, Responses: result.Responses,
		TransportErrors: result.TransportErrors, Bytes: result.Bytes, Status: result.Status,
	}
	if err == nil && !result.Gone {
		message.Content = &result.Content
	}
	if err != nil {
		message.Error = err.Error()
		var fetch *workday.DetailFetchError
		var reserved *workday.ReservationError
		if errors.As(err, &fetch) {
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

type stringList []string

func (list *stringList) String() string { return fmt.Sprint([]string(*list)) }
func (list *stringList) Set(value string) error {
	*list = append(*list, value)
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
