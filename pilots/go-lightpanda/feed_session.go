package main

import (
	"context"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
)

type feedPageFetch func(context.Context, string) (Result, error)
type feedTask struct {
	request  feed.Request
	converse func(context.Context, feedPageFetch) (Result, error)
}
