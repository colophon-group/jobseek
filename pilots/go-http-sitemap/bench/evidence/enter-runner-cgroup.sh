#!/bin/sh
set -eu

if [ "$#" -lt 2 ]; then
  echo "usage: enter-runner-cgroup.sh /sys/fs/cgroup/NAME COMMAND [ARG ...]" >&2
  exit 64
fi

runner_cgroup=$1
shift

case "$runner_cgroup" in
  /sys/fs/cgroup/*) ;;
  *) echo "runner cgroup must be an explicit child of /sys/fs/cgroup" >&2; exit 64 ;;
esac

for control_file in cpu.max memory.max memory.swap.max pids.max cgroup.procs; do
  test -f "$runner_cgroup/$control_file" || {
    echo "missing cgroup control: $runner_cgroup/$control_file" >&2
    exit 65
  }
done

test "$(cat "$runner_cgroup/cpu.max")" = "100000 100000"
test "$(cat "$runner_cgroup/memory.max")" = "1073741824"
test "$(cat "$runner_cgroup/memory.swap.max")" = "0"
test "$(cat "$runner_cgroup/pids.max")" = "128"
test -z "$(cat "$runner_cgroup/cgroup.procs")"

printf '%s\n' "$$" > "$runner_cgroup/cgroup.procs"
exec "$@"
