module github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy

go 1.26.0

require (
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	golang.org/x/net v0.59.0
)

require google.golang.org/protobuf v1.36.10 // indirect

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../contracts
