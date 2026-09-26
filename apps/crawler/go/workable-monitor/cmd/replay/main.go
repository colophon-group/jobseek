package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

type replayClient struct {
	pages []json.RawMessage
	next  int
}

func (client *replayClient) Do(request *http.Request) (*http.Response, error) {
	if client.next >= len(client.pages) {
		return nil, errors.New("offline Workable replay exhausted its pages")
	}
	body := client.pages[client.next]
	client.next++
	return &http.Response{
		StatusCode: 200,
		Body:       io.NopCloser(bytes.NewReader(body)),
		Request:    request,
	}, nil
}

func main() {
	var input struct {
		Slug  string            `json:"slug"`
		Pages []json.RawMessage `json:"pages"`
	}
	decoder := json.NewDecoder(os.Stdin)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil || len(input.Pages) == 0 {
		fmt.Fprintln(os.Stderr, "offline Workable replay requires a slug and pages")
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	client := &replayClient{pages: input.Pages}
	result, err := workable.Fetch(ctx, client, input.Slug, func(context.Context, time.Duration) error { return nil })
	if err != nil || client.next != len(client.pages) {
		fmt.Fprintln(os.Stderr, "offline Workable replay failed or left unused pages")
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result.Inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
