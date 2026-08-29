# Crawler runtime contract v1 activation record

This record activates `contracts/v1/runtime.proto` as the authoritative crawler
runtime wire contract in crawler release `0.13.590`. Activation packages and
validates the cross-language boundary; it does not enable a production Go
worker, change crawler routing, or retire the retained JSON schemas.

## Provenance

The activation candidate is based on repository commit
`7b2cade04c04a955ae5d63d6003e65cd1fa15be1`. Its reviewed predecessors are:

| Lane | Pull request | Reviewed head | Merge commit |
| --- | --- | --- | --- |
| Runtime IDL | #8049 | `80dccec9ef4c7c710ed4e13f02ba7c547e572362` | `47b108c5e974f9bdb50aac69519755c2bfc2584a` |
| Framing and compatibility corpus | #8060 | `b0816915b01748296506ae587b52347cc4e262c9` | `9c5b0e77a266e2b57decb22c5cc303bd181a9089` |
| Adjacent-version policy | #8078 | `f143ce2b13117a0b288b7c092318c006419675f4` | `16de23c3792f0e5584d1b4b8863f08b31e5db8cd` |
| Control and source identity corpus | #8087 | `b22417cb5c7cc7edfe92d3e9c0d0e4d7525a4070` | `0dc6083707f1dca983129084e75a3471a6299ae1` |
| Privacy and redaction corpus | #8094 | `19cdb1c50a792c2e6c1d1a7522109350dd17e19a` | `7cca01c6e3df90aa32f10723ef1a50e9ea8c171c` |
| Semantic conformance corpus | #8152 | `fb7fc32935e0ea55bf3261977c04b65175b81da8` | `9b57f265fa688f886b745fbeda8a552f990422b2` |

## Pinned toolchain and package boundary

- Python: `3.13.15`
- Go language and CI toolchain: `1.24.0`
- `grpcio-tools`: `1.76.0` (`libprotoc 31.1`)
- Python `protobuf`: `6.33.0`
- `protoc-gen-go`: `1.36.10`
- Go `google.golang.org/protobuf`: `1.36.10`
- Go module: `github.com/colophon-group/jobseek/apps/crawler/contracts`
- Generated Go import: `github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go`
- Installed Python import: `jobseek_runtime_v1.runtime_pb2`

The Python wheel contains the generated module plus `framing/` and
`privacy_registry.json`. The crawler image installs the non-editable wheel and
removes the source contract tree, so its smoke test exercises only installed
artifacts. The Go package is verified from an external temporary consumer
module, not only from inside this repository.

## Immutable evidence

| Artifact | SHA-256 |
| --- | --- |
| `runtime.proto` | `9255752c976b970510fbc1d772321cb731d9f54bb2de6f0bf7193398a19c3191` |
| Compiled descriptor (`libprotoc 31.1`) | `2c1296977c9c14680727a970387b969ac530da6ca5e19c95805fce9726f761d0` |
| Introduction baseline descriptor | `4c868612152349cdbc8c548158e21005d90c0ba6c2ec736203a58105d30b6775` |
| Generated Go binding | `e3e5912814b599392f0bcf97bc010d2618838b9d6ee2aa9a4d2151cb7e986e8c` |
| Generated Python package initializer | `7861f55459f5e7d7df98624cee112da2d0dacaf9305a9f47c13cc02406a77e5a` |
| Generated Python binding | `141b860bf3b3062e537afcb564bdcf8ca5a0946ebf4d3dbfa960fd31e41f93e8` |
| Framing vectors | `44b41d2fb17d6a91970c768fe43e36c44fd5c97e94e4ca055a4ac17fb1be35ad` |
| Control manifest | `3dfc42e4cfac960a8fd4ea65e4ae0ceb0e884bd49c69e939264f017cff042dab` |
| Source-identity vectors | `71bc4fba1ec5aaff10bfed703c0ea7a4c434c741959dedeca7e2e09f8f539e14` |
| Redaction manifest | `da1e93c18540760a3cfa0de8cd7fef950deea33b41839a368f6c776bbc02582f` |
| Semantics manifest | `bfc59c59aed862ec88ce6356318240fb196aefefedf4b00d650d01d352685635` |
| Adjacent-version manifest | `30ab7b605c6bad5d459357d0a3b0b7134598d2260bf2e5fd9133a45f636cf3ca` |
| Adjacent-version vectors | `86b9ebd9f041b8adcd20f1001327b1d126d9f90d1419bbb76868a4649bab193a` |
| Privacy registry | `91f0cd53a6e8778c95687b9d1c5dc81cf4a3ab940d6dcdc6d6c62177c78c6be4` |

## Activation gates

The pre-activation packaging head
`cd9ea4265151019b3ba29fe8575b2bd149250088` passed hosted workflow run
`33250862204`, including deterministic regeneration, the complete shared
Python corpus, Go race and vet checks, an external Go consumer, an isolated
built-wheel import, and installed-image parity with `/app/contracts` absent.

Activation additionally requires all checks on the final candidate head:

1. deterministic generation and generated-manifest verification;
2. Python and Go conformance, semantics, compatibility, privacy, race, and vet
   suites;
3. isolated wheel and installed-image parity smoke tests;
4. active crawler versioning, deploy, runtime-digest, and ATS inventory policy
   tests for every change under `contracts/v1/**`;
5. Required CI, Workflow Security, CodeQL, and independent review.

The former #8071 exact-file bypass and all inactive-v1 exclusions are revoked
by this release. Any later `contracts/v1/**` edit is an ordinary active crawler
runtime change: it changes the runtime digest, requires a monotonic crawler
version bump, and participates in crawler and ATS deployment classification.
