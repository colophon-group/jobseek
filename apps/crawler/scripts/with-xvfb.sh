#!/bin/sh
# Start the browser worker's virtual display without inheriting stale
# display-99 artifacts from a restarted Docker container.
set -eu

DISPLAY_VALUE=:99
RUNTIME_DIR="${XVFB_RUNTIME_DIR:-/tmp}"
LOCK_FILE="${RUNTIME_DIR}/.X99-lock"
SOCKET_FILE="${RUNTIME_DIR}/.X11-unix/X99"

case "$RUNTIME_DIR" in
    /*) ;;
    *)
        echo "with-xvfb: runtime directory must be absolute" >&2
        exit 1
        ;;
esac

# Never remove artifacts belonging to a display that is actually serving.
if xdpyinfo -display "$DISPLAY_VALUE" >/dev/null 2>&1; then
    echo "with-xvfb: display 99 is already active" >&2
    exit 1
fi

# A Docker restart preserves the container writable layer but kills every
# process in its PID namespace. If a lock still names a live process, fail
# closed instead of racing or deleting another X server's state.
if [ -r "$LOCK_FILE" ]; then
    lock_pid="$(tr -d '[:space:]' <"$LOCK_FILE")"
    case "$lock_pid" in
        ''|*[!0-9]*) ;;
        *)
            if kill -0 "$lock_pid" 2>/dev/null; then
                process_name="$(cat "/proc/${lock_pid}/comm" 2>/dev/null || true)"
                if [ "$process_name" = Xvfb ] || [ "$process_name" = Xorg ]; then
                    echo "with-xvfb: display 99 lock belongs to a live X server" >&2
                    exit 1
                fi
            fi
            ;;
    esac
fi

rm -f -- "$LOCK_FILE" "$SOCKET_FILE"

Xvfb "$DISPLAY_VALUE" -screen 0 1440x900x24 -nolisten tcp &
XVFB_PID=$!
export DISPLAY="$DISPLAY_VALUE"

ready=0
for _ in $(seq 1 50); do
    if xdpyinfo -display "$DISPLAY_VALUE" >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "with-xvfb: Xvfb :99 died before becoming ready" >&2
        exit 1
    fi
    sleep 0.2
done

if [ "$ready" != 1 ]; then
    echo "with-xvfb: X server :99 did not respond within 10s" >&2
    exit 1
fi

exec "$@"
