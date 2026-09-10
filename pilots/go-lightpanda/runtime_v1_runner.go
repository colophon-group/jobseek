package main

import (
	"context"
	"errors"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

// runtimeV1Runner is a dormant in-process bridge from the typed runtime-v1
// adapter to the existing one-shot Lightpanda lifecycle. It owns no service,
// queue, retry, fallback, persistence, or production-routing behavior.
type runtimeV1Runner struct {
	config Config
	run    taskRunner
}

var _ lightpandaadapter.Runner = runtimeV1Runner{}

// Run translates the already-bound B0/B1 input into exactly one one-shot
// invocation. The called lifecycle returns only after process cleanup, which
// lets cleanup failure remain authoritative over concurrent cancellation.
func (runner runtimeV1Runner) Run(
	ctx context.Context,
	bound lightpandaadapter.BoundInput,
) lightpandaadapter.RunnerOutcome {
	input := bound.Input()
	if input == nil || input.Plan == nil || input.Plan.Navigation == nil ||
		len(input.Plan.Evaluations) > 1 {
		return internalRunnerFailure(bound)
	}

	task := Task{URL: input.Plan.TargetUrl}
	if len(input.Plan.Evaluations) == 1 {
		evaluation := input.Plan.Evaluations[0]
		if evaluation == nil {
			return internalRunnerFailure(bound)
		}
		limit := evaluation.MaxResultBytes
		if limit > uint64(maxExpressionResult) {
			limit = uint64(maxExpressionResult)
		}
		if limit == 0 {
			return internalRunnerFailure(bound)
		}
		task.Evaluation = &TaskEvaluation{
			Expression:     evaluation.Expression,
			MaxResultBytes: int(limit),
		}
	}

	config := runner.config
	config.TaskTimeout = time.Duration(input.Plan.Navigation.TimeoutMs) * time.Millisecond
	run := runner.run
	if run == nil {
		run = runTask
	}
	result, err := run(ctx, config, task)
	if err != nil {
		contextErr := ctx.Err()
		switch {
		case errors.Is(err, errCleanupUnproved):
			return lightpandaadapter.NewRunnerCleanupFailure(bound)
		case errors.Is(err, errResourceLimit):
			return lightpandaadapter.NewRunnerFailure(bound, lightpandaadapter.ProviderFailure{
				Code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
				Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
			})
		case errors.Is(err, context.DeadlineExceeded) &&
			errors.Is(contextErr, context.DeadlineExceeded):
			return lightpandaadapter.NewRunnerFailure(bound, lightpandaadapter.ProviderFailure{
				Code:        runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
				Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
			})
		case errors.Is(err, context.Canceled) && errors.Is(contextErr, context.Canceled):
			return lightpandaadapter.NewRunnerFailure(bound, lightpandaadapter.ProviderFailure{
				Code:        runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
				Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
			})
		default:
			return internalRunnerFailure(bound)
		}
	}
	if err := validateResult(task, result); err != nil {
		if errors.Is(err, errResourceLimit) {
			return lightpandaadapter.NewRunnerFailure(bound, lightpandaadapter.ProviderFailure{
				Code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
				Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
			})
		}
		return internalRunnerFailure(bound)
	}

	status := uint32(result.Status)
	html := []byte(result.HTML)
	if html == nil {
		html = []byte{}
	}
	raw := &lightpandaadapter.RawSuccess{
		FinalURL: result.FinalURL,
		Status:   &status,
		HTML:     html,
	}
	if len(input.Plan.Evaluations) == 1 {
		raw.Evaluations = []lightpandaadapter.RawEvaluation{{
			EvaluationID: input.Plan.Evaluations[0].EvaluationId,
			JSON:         append([]byte(nil), result.Expression...),
		}}
	}
	return lightpandaadapter.NewRunnerSuccess(bound, raw)
}

func internalRunnerFailure(bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
	return lightpandaadapter.NewRunnerFailure(bound, lightpandaadapter.ProviderFailure{
		Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	})
}
