// Package admission is an inactive, dependency-injected claim-boundary
// experiment for the Go sitemap pilot.
//
// It deliberately has no Redis, database, HTTP, runtime-v1 binding, or
// deployment adapter. The package only proves that an injected claim begins
// inside worker service capacity, after both a worker and an origin slot have
// been assigned, and that terminal publication is fenced and fail-closed.
// A later adapter may translate runtime-v1 BoardManifest into ManifestIdentity
// only after validating the complete contract message.
package admission
