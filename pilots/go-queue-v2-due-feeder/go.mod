module github.com/colophon-group/jobseek/pilots/go-queue-v2-due-feeder

go 1.24.0

require (
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	github.com/colophon-group/jobseek/pilots/go-http-sitemap v0.0.0
	github.com/colophon-group/jobseek/pilots/go-queue-v2-admission v0.0.0
	github.com/redis/go-redis/v9 v9.14.0
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
)

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../apps/crawler/contracts

replace github.com/colophon-group/jobseek/pilots/go-http-sitemap => ../go-http-sitemap

replace github.com/colophon-group/jobseek/pilots/go-queue-v2-admission => ../go-queue-v2-admission
