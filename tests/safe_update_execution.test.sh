#!/usr/bin/env bash
set -euo pipefail

readonly source_script="${1:-scripts/safe_update.sh}"
readonly test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

readonly project_root="$test_root/project"
readonly fake_bin="$test_root/bin"
readonly git_log="$test_root/git.log"
readonly systemctl_log="$test_root/systemctl.log"
readonly script_copy="$test_root/safe_update.sh"
readonly active_units="three-bucket-dca.service three-bucket-dca-update.timer three-bucket-dca-halving.timer"

mkdir -p "$project_root/.venv/bin" "$project_root/scripts" "$fake_bin"
touch "$project_root/requirements.txt" "$project_root/backup.db"
sed "s|^readonly project_root=.*$|readonly project_root=\"$project_root\"|" \
  "$source_script" > "$script_copy"

cat > "$fake_bin/sudo" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "cp" ]]; then
  if [[ "${SAFE_UPDATE_CP:-ok}" == "fail" ]]; then
    exit 1
  fi
  exit 0
fi
exec "$@"
EOF

cat > "$fake_bin/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SAFE_UPDATE_SYSTEMCTL_LOG"
case "$1" in
  stop)
    if [[ "${SAFE_UPDATE_HALVING_MISSING:-no}" == "yes" && "$*" == *"three-bucket-dca-halving"* ]]; then
      exit 5
    fi
    exit 0
    ;;
  daemon-reload|enable|start)
    exit 0
    ;;
  is-active)
    case "$SAFE_UPDATE_STATE" in
      active) exit 0 ;;
      inactive|error) exit 1 ;;
    esac
    ;;
  show)
    if [[ "$*" == *"LoadState"* ]]; then
      if [[ "${SAFE_UPDATE_HALVING_MISSING:-no}" == "yes" && "$*" == *"three-bucket-dca-halving"* ]]; then
        printf '%s\n' "not-found"
      else
        printf '%s\n' "loaded"
      fi
      exit 0
    fi
    if [[ "$SAFE_UPDATE_STATE" == "error" ]]; then
      exit 1
    fi
    if [[ "$SAFE_UPDATE_STATE" == "active" ]]; then
      printf '%s\n' "active"
      exit 0
    fi
    unit="${@: -1}"
    if grep -F "stop " "$SAFE_UPDATE_SYSTEMCTL_LOG" | grep -Fq "$unit"; then
      printf '%s\n' "inactive"
    elif [[ " $SAFE_UPDATE_ACTIVE_UNITS " == *" $unit "* ]]; then
      printf '%s\n' "active"
    else
      printf '%s\n' "inactive"
    fi
    exit 0
    ;;
esac
exit 2
EOF

cat > "$fake_bin/git" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SAFE_UPDATE_GIT_LOG"
if [[ "${SAFE_UPDATE_GIT:-ok}" == "fail" ]]; then
  exit 1
fi
exit 0
EOF

cat > "$project_root/.venv/bin/python" <<EOF
#!/usr/bin/env bash
case "\${SAFE_UPDATE_BACKUP:-valid}" in
  fail) exit 1 ;;
  missing) printf '%s\n' "$project_root/missing.db" ;;
  valid) printf '%s\n' "$project_root/backup.db" ;;
esac
EOF

cat > "$project_root/.venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
if [[ "${SAFE_UPDATE_PIP:-ok}" == "fail" ]]; then
  exit 1
fi
exit 0
EOF

chmod +x "$script_copy" "$fake_bin/sudo" "$fake_bin/systemctl" "$fake_bin/git" \
  "$project_root/.venv/bin/python" "$project_root/.venv/bin/pip"

assert_active_units_restored() {
  local unit
  for unit in $active_units; do
    grep -q "^start .*$unit" "$systemctl_log" || {
      echo "active unit was not restored: $unit" >&2
      return 1
    }
  done
}

run_must_fail_before_pull() {
  local state="$1"
  local backup="$2"
  local with_env="$3"

  : > "$git_log"
  : > "$systemctl_log"
  if [[ "$with_env" == "yes" ]]; then
    : > "$project_root/three-bucket-dca.env"
  else
    rm -f -- "$project_root/three-bucket-dca.env"
  fi

  set +e
  PATH="$fake_bin:$PATH" \
    SAFE_UPDATE_STATE="$state" \
    SAFE_UPDATE_BACKUP="$backup" \
    SAFE_UPDATE_ACTIVE_UNITS="$active_units" \
    SAFE_UPDATE_GIT_LOG="$git_log" \
    SAFE_UPDATE_SYSTEMCTL_LOG="$systemctl_log" \
    bash "$script_copy" >/dev/null 2>&1
  local status=$?
  set -e

  [[ $status -ne 0 ]] || {
    echo "expected update to fail: state=$state backup=$backup env=$with_env" >&2
    return 1
  }
  [[ ! -s "$git_log" ]] || {
    echo "git pull ran before prerequisites passed: state=$state backup=$backup env=$with_env" >&2
    return 1
  }
  if [[ "$with_env" == "no" ]]; then
    ! grep -q '^stop ' "$systemctl_log" || {
      echo "units were stopped before the environment preflight completed" >&2
      return 1
    }
  elif [[ "$backup" != "valid" && "$state" == "inactive" ]]; then
    assert_active_units_restored
  fi
}

run_post_stop_failure_must_restore() {
  local failure="$1"

  : > "$git_log"
  : > "$systemctl_log"
  : > "$project_root/three-bucket-dca.env"

  set +e
  PATH="$fake_bin:$PATH" \
    SAFE_UPDATE_STATE=inactive \
    SAFE_UPDATE_BACKUP=valid \
    SAFE_UPDATE_GIT="$([[ "$failure" == "git" ]] && echo fail || echo ok)" \
    SAFE_UPDATE_PIP="$([[ "$failure" == "pip" ]] && echo fail || echo ok)" \
    SAFE_UPDATE_CP="$([[ "$failure" == "cp" ]] && echo fail || echo ok)" \
    SAFE_UPDATE_ACTIVE_UNITS="$active_units" \
    SAFE_UPDATE_GIT_LOG="$git_log" \
    SAFE_UPDATE_SYSTEMCTL_LOG="$systemctl_log" \
    bash "$script_copy" >/dev/null 2>&1
  local status=$?
  set -e

  [[ $status -ne 0 ]] || {
    echo "expected update to fail at $failure" >&2
    return 1
  }
  assert_active_units_restored
}

run_must_fail_before_pull active valid yes
run_must_fail_before_pull error valid yes
run_must_fail_before_pull inactive valid no
run_must_fail_before_pull inactive fail yes
run_must_fail_before_pull inactive missing yes
run_post_stop_failure_must_restore git
run_post_stop_failure_must_restore pip
run_post_stop_failure_must_restore cp

: > "$git_log"
: > "$systemctl_log"
: > "$project_root/three-bucket-dca.env"
PATH="$fake_bin:$PATH" \
  SAFE_UPDATE_STATE=inactive \
  SAFE_UPDATE_BACKUP=valid \
  SAFE_UPDATE_HALVING_MISSING=yes \
  SAFE_UPDATE_ACTIVE_UNITS="$active_units" \
  SAFE_UPDATE_GIT_LOG="$git_log" \
  SAFE_UPDATE_SYSTEMCTL_LOG="$systemctl_log" \
  bash "$script_copy" >"$test_root/success.log" 2>&1 || {
    cat "$test_root/success.log" >&2
    exit 1
  }
grep -qx "pull --ff-only" "$git_log"
