from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
GO_FIELDS = {
    "max_frame_bytes": "MaxFrameBytes",
    "max_inline_body_bytes": "MaxInlineBodyBytes",
    "max_artifact_chunk_bytes": "MaxArtifactChunkBytes",
    "max_monitor_batches": "MaxMonitorBatches",
    "max_output_items": "MaxOutputItems",
    "max_in_flight_frames": "MaxInFlightFrames",
    "max_active_duration_ms": "MaxActiveDurationMs",
    "max_browser_actions": "MaxBrowserActions",
    "max_browser_captures": "MaxBrowserCaptures",
    "max_browser_evaluations": "MaxBrowserEvaluations",
    "max_http_transfer_bytes": "MaxHttpTransferBytes",
    "max_browser_transfer_bytes": "MaxBrowserTransferBytes",
    "max_execution_frames": "MaxExecutionFrames",
    "max_artifact_count": "MaxArtifactCount",
    "max_artifact_total_bytes": "MaxArtifactTotalBytes",
    "max_retry_after_ms": "MaxRetryAfterMs",
}


def render_go() -> str:
    values = json.loads((ROOT / "limits.json").read_text())
    if set(values) != set(GO_FIELDS):
        raise ValueError("limits.json fields differ from the generated Go mapping")
    fields = "\n".join(f"\t\t{go_name}: {values[name]}," for name, go_name in GO_FIELDS.items())
    return f"""// Code generated from ../../limits.json by tools/generate_limits.py. DO NOT EDIT.

package conformance

import runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"

func generatedHardLimits() *runtimev1.Limits {{
\treturn &runtimev1.Limits{{
{fields}
\t}}
}}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-out", type=Path, required=True)
    args = parser.parse_args()
    args.go_out.write_text(render_go())


if __name__ == "__main__":
    main()
