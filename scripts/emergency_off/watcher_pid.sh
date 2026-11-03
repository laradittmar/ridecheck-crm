#!/usr/bin/env bash
# PID-based watcher ownership and termination (carried from the 2026-11-01 kit, unchanged in
# its rules). No `pkill`, no `pgrep -f`, no `killall`, and no pattern matching of any kind.
#
# A watcher is ours only when ALL of these hold for its exact pid:
#   numeric; alive; its command line names THIS kit's WATCH.sh AND THIS window's config;
#   and it is neither the calling shell nor any ancestor of it.

watcher_pidfile() { echo "$EVID/watch/watcher.pid"; }

watcher_claim() {              # $1 = pid to record; atomic create via noclobber
  local f; f="$(watcher_pidfile)"
  mkdir -p "$(dirname "$f")"; chmod 700 "$(dirname "$f")"
  if ( set -o noclobber; printf '%s\n' "$1" > "$f" ) 2>/dev/null; then
    chmod 600 "$f"; return 0
  fi
  local old; old="$(cat "$f" 2>/dev/null || true)"
  if watcher_is_ours "$old"; then
    log "WATCHER: refusing to start -- pid $old already owns $f"; return 2
  fi
  log "WATCHER: stale pidfile (pid '${old:-empty}' is not our watcher); reclaiming"
  rm -f "$f"
  ( set -o noclobber; printf '%s\n' "$1" > "$f" ) 2>/dev/null && { chmod 600 "$f"; return 0; }
  return 2
}

watcher_is_ours() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  local cl; cl="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || return 1
  case "$cl" in *"$KIT_DIR/WATCH.sh"*) ;; *) return 1 ;; esac
  case "$cl" in *"$KIT_CONFIG"*) ;; *) return 1 ;; esac
  # PPid from /proc/<pid>/status (never field 4 of /proc/<pid>/stat: `comm` may hold spaces)
  local p=$$ ppid
  while [ "$p" -gt 1 ] 2>/dev/null; do
    [ "$p" = "$pid" ] && return 1
    ppid="$(awk '/^PPid:/{print $2}' "/proc/$p/status" 2>/dev/null)"
    case "$ppid" in ''|*[!0-9]*) break ;; esac
    p="$ppid"
  done
  return 0
}

# TERM the exact pid, bounded wait, then KILL that exact pid. Never a pattern, never blocks.
# When the WATCHER itself invoked KILL (KILL_INVOKED_BY_WATCHER=<its pid>) it is left alone:
# it is waiting for this very restoration and must outlive it, to report the result.
watcher_stop() {
  local f pid i; f="$(watcher_pidfile)"
  [ -f "$f" ] || { log "WATCHER: no pidfile; nothing to stop"; return 0; }
  pid="$(cat "$f" 2>/dev/null || true)"
  if [ -n "${KILL_INVOKED_BY_WATCHER:-}" ] && [ "$pid" = "$KILL_INVOKED_BY_WATCHER" ]; then
    log "WATCHER: KILL was invoked by the watcher ($pid) -- it waits for the result; not signalled"
    return 0
  fi
  if ! watcher_is_ours "$pid"; then
    log "WATCHER: pidfile holds '${pid:-empty}', which is not our live watcher; removing it only"
    rm -f "$f"; return 0
  fi
  log "WATCHER: TERM -> $pid"
  kill -TERM "$pid" 2>/dev/null
  for i in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then
    log "WATCHER: still alive after 10s; KILL -> $pid"
    kill -KILL "$pid" 2>/dev/null
    for i in $(seq 1 5); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  fi
  if kill -0 "$pid" 2>/dev/null; then
    log "WATCHER: *** pid $pid WOULD NOT DIE -- continuing to OFF restoration anyway"
    return 1
  fi
  rm -f "$f"
  log "WATCHER: stopped and pidfile removed"
  return 0
}
