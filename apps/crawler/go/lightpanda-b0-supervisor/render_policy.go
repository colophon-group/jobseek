package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"net/url"
	"os/exec"
	"strings"
	"time"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

type renderedClassifier func(context.Context, string, string, json.RawMessage) (string, error)

// The installed Go parser owns the Python-compatible gone regex semantics and
// challenge signatures. No credentials or origin authority reach this child.
func classifyRendered(ctx context.Context, html, finalURL string, config json.RawMessage) (string, error) {
	call, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	body, err := json.Marshal(struct {
		Mode   string          `json:"mode"`
		HTML   string          `json:"html"`
		URL    string          `json:"url"`
		Config json.RawMessage `json:"config"`
	}{"classify-rendered", html, finalURL, config})
	if err != nil {
		return "", errors.New("invalid rendered classification input")
	}
	command := exec.CommandContext(call, "/usr/local/bin/dom-detail-parse")
	command.Env = []string{"PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"}
	command.Stdin = bytes.NewReader(body)
	output := &policyOutput{}
	command.Stdout = output
	if err := command.Run(); err != nil {
		return "", errors.New("rendered classification failed")
	}
	var result struct {
		Classification string `json:"classification"`
	}
	decoder := json.NewDecoder(bytes.NewReader(output.body.Bytes()))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&result) != nil || decoder.Decode(new(any)) != io.EOF ||
		(result.Classification != "okay" && result.Classification != "gone" && result.Classification != "challenge") {
		return "", errors.New("invalid rendered classification result")
	}
	return result.Classification, nil
}

type policyOutput struct{ body bytes.Buffer }

func (output *policyOutput) Write(body []byte) (int, error) {
	if output.body.Len()+len(body) > 4096 {
		return 0, errors.New("classification output exceeds bound")
	}
	return output.body.Write(body)
}

// Retry decisions only use complete, canonical, digest-verified render output.
// Typed failures and HTTP errors go directly to the normal executor policy.
func validatedPolicyDocument(payload []byte) (*runtimev1.BrowserSuccess, string, error) {
	if len(payload) == 0 || len(payload) > int(resultFrameLimit) {
		return nil, "", errors.New("invalid rendered result bounds")
	}
	result := &runtimev1.BrowserResult{}
	if proto.Unmarshal(payload, result) != nil || unknownPolicyFields(result.ProtoReflect()) ||
		result.ContractVersion != runtimeContract || result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA || result.Outcome == nil {
		return nil, "", errors.New("invalid rendered result contract")
	}
	canonical, err := proto.MarshalOptions{Deterministic: true}.Marshal(result)
	if err != nil || !bytes.Equal(canonical, payload) {
		return nil, "", errors.New("noncanonical rendered result")
	}
	success := result.GetSuccess()
	if success == nil {
		return nil, "", nil
	}
	parsed, err := url.Parse(success.FinalUrl)
	if err != nil || len(success.FinalUrl) > 8192 || strings.ContainsAny(success.FinalUrl, " \t\n\r") ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" ||
		success.Status == nil || *success.Status < 100 || *success.Status > 599 || success.Html == nil ||
		len(success.ActionOutcomes)+len(success.Captures)+len(success.Evaluations)+len(success.Artifacts) != 0 {
		return nil, "", errors.New("invalid rendered success shape")
	}
	manifest := success.Html
	const chunkSize = uint64(64 * 1024)
	if !manifest.Complete || manifest.TotalSizeBytes > 1<<20 || !hex256.MatchString(manifest.TotalSha256) ||
		uint64(len(manifest.Chunks)) != (manifest.TotalSizeBytes+chunkSize-1)/chunkSize {
		return nil, "", errors.New("invalid rendered HTML manifest")
	}
	html := make([]byte, 0, manifest.TotalSizeBytes)
	for index, chunk := range manifest.Chunks {
		if chunk == nil {
			return nil, "", errors.New("missing rendered HTML chunk")
		}
		inline, ok := chunk.Storage.(*runtimev1.DataChunk_InlineBody)
		if !ok || chunk.Sequence != uint32(index) || chunk.SizeBytes != min(chunkSize, manifest.TotalSizeBytes-uint64(len(html))) ||
			uint64(len(inline.InlineBody)) != chunk.SizeBytes || !hex256.MatchString(chunk.Sha256) {
			return nil, "", errors.New("invalid rendered HTML chunk")
		}
		digest := sha256.Sum256(inline.InlineBody)
		if hex.EncodeToString(digest[:]) != chunk.Sha256 {
			return nil, "", errors.New("rendered HTML chunk digest mismatch")
		}
		html = append(html, inline.InlineBody...)
	}
	digest := sha256.Sum256(html)
	if uint64(len(html)) != manifest.TotalSizeBytes || hex.EncodeToString(digest[:]) != manifest.TotalSha256 || !utf8.Valid(html) {
		return nil, "", errors.New("rendered HTML digest or encoding mismatch")
	}
	return success, string(html), nil
}

func unknownPolicyFields(message protoreflect.Message) bool {
	if len(message.GetUnknown()) != 0 {
		return true
	}
	unknown := false
	message.Range(func(field protoreflect.FieldDescriptor, value protoreflect.Value) bool {
		if field.Kind() != protoreflect.MessageKind {
			return true
		}
		if field.IsList() {
			list := value.List()
			for index := 0; index < list.Len(); index++ {
				if unknownPolicyFields(list.Get(index).Message()) {
					unknown = true
					break
				}
			}
		} else {
			unknown = unknownPolicyFields(value.Message())
		}
		return !unknown
	})
	return unknown
}

func (s *supervisor) renderLease(ctx context.Context, initial heldReservation, current *lease, authority *leaseAuthority) ([]byte, error) {
	held := initial
	for attempt := 0; attempt <= 1; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		task := current.Task
		task.RenderAttempt = attempt
		result, err := held.execute(ctx, task)
		if err != nil || task.Envelope.ScraperType != "dom" {
			return result, err
		}
		success, html, err := validatedPolicyDocument(result)
		if err != nil {
			return nil, err
		}
		if success == nil || *success.Status >= 400 {
			return result, nil
		}
		if err := publisherpolicy.Check(success.ResourcePolicy, html, task.Envelope.SourceURL); err != nil {
			return result, nil
		}
		classifier := s.classifier
		if classifier == nil {
			classifier = classifyRendered
		}
		classification, err := classifier(ctx, html, success.FinalUrl, task.Envelope.ParserConfig)
		if err != nil {
			return nil, err
		}
		if classification != "challenge" || attempt == 1 {
			return result, nil
		}
		held.close()
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		finished, err := authority.heartbeatOnce(ctx)
		if contextErr := ctx.Err(); contextErr != nil {
			return nil, contextErr
		}
		if err != nil || finished {
			return nil, &authorityError{operation: "challenge-retry", err: errors.New("retry lease authority unavailable")}
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		held, err = s.renderer.reserve(ctx)
		if err != nil {
			return nil, err
		}
		defer held.close()
		s.logTask("renderer", "retry_bot_challenge", current, nil)
	}
	return nil, errors.New("render retry bound exceeded")
}
