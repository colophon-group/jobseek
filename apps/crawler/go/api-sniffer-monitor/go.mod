module github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor

go 1.26.0

require (
	github.com/colophon-group/jobseek/apps/crawler/go/dom-detail v0.0.0
	github.com/jmespath/go-jmespath v0.4.0
	golang.org/x/net v0.59.0
)

require (
	github.com/andybalholm/cascadia v1.3.3 // indirect
	github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail v0.0.0 // indirect
	github.com/dlclark/regexp2/v2 v2.8.0 // indirect
	golang.org/x/text v0.42.0 // indirect
)

replace github.com/colophon-group/jobseek/apps/crawler/go/dom-detail => ../dom-detail

replace github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail => ../jsonld-detail
