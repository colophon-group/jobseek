module github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-supervisor

go 1.26.0

require (
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	github.com/redis/go-redis/v9 v9.14.0
	google.golang.org/protobuf v1.36.10
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	golang.org/x/net v0.59.0 // indirect
)

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../contracts

require github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy => ../publisher-policy
