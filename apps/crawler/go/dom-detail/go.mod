module github.com/colophon-group/jobseek/apps/crawler/go/dom-detail

go 1.26.0

require (
	github.com/andybalholm/cascadia v1.3.3
	github.com/dlclark/regexp2/v2 v2.8.0
	golang.org/x/net v0.59.0
	golang.org/x/text v0.42.0
)

require github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail => ../jsonld-detail
