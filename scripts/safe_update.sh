#!/usr/bin/env bash
set -euo pipefail

readonly project_root="/home/ubuntu/three-bucket-dca"
readonly env_file="$project_root/three-bucket-dca.env"
readonly required_units=(
  three-bucket-dca-update.timer
  three-bucket-dca-update.service
  three-bucket-dca.service
)
readonly optional_units=(
  three-bucket-dca-halving.timer
  three-bucket-dca-halving.service
)
declare -a installed_optional_units=()
declare -a units_to_restore=()

restore_units_on_failure() {
  local status=$?
  trap - EXIT
  if (( status != 0 )) && ((${#units_to_restore[@]})); then
    echo "更新失败，正在恢复原先运行的服务和定时器" >&2
    if ! sudo systemctl start "${units_to_restore[@]}"; then
      echo "自动恢复失败，请手动启动：${units_to_restore[*]}" >&2
    fi
  fi
  exit "$status"
}

remember_active_state() {
  local unit="$1"
  local active_state
  if ! active_state=$(sudo systemctl show --property=ActiveState --value "$unit"); then
    echo "更新已中止：无法确认 $unit 的原始状态" >&2
    exit 1
  fi
  case "$active_state" in
    active|activating|reloading)
      units_to_restore+=("$unit")
      ;;
  esac
}

verify_inactive() {
  local unit="$1"
  local active_state
  if ! active_state=$(sudo systemctl show --property=ActiveState --value "$unit"); then
    echo "更新已中止：无法确认 $unit 的状态" >&2
    exit 1
  fi
  if [[ "$active_state" != "inactive" ]]; then
    echo "更新已中止：$unit 未完全停止（ActiveState=$active_state）" >&2
    exit 1
  fi
}

cd "$project_root"

if [[ ! -f "$env_file" ]]; then
  echo "更新已中止：找不到环境文件 $env_file" >&2
  exit 1
fi
if [[ ! -x .venv/bin/python || ! -x .venv/bin/pip ]]; then
  echo "更新已中止：Python 虚拟环境不完整" >&2
  exit 1
fi
set -a
source "$env_file"
set +a

for unit in "${required_units[@]}"; do
  remember_active_state "$unit"
done
for unit in "${optional_units[@]}"; do
  if ! load_state=$(sudo systemctl show --property=LoadState --value "$unit"); then
    echo "更新已中止：无法确认 $unit 是否已安装" >&2
    exit 1
  fi
  if [[ "$load_state" != "not-found" ]]; then
    installed_optional_units+=("$unit")
    remember_active_state "$unit"
  fi
done

trap restore_units_on_failure EXIT
sudo systemctl stop "${required_units[@]}"
for unit in "${required_units[@]}"; do
  verify_inactive "$unit"
done

for unit in "${installed_optional_units[@]}"; do
  sudo systemctl stop "$unit"
  verify_inactive "$unit"
done

backup_path=$(.venv/bin/python scripts/backup_for_update.py)
[[ -f "$backup_path" ]] || {
  echo "更新已中止：备份文件不存在 $backup_path" >&2
  exit 1
}
echo "数据库已备份：$backup_path"

git pull --ff-only
.venv/bin/pip install -r requirements.txt
sudo cp deploy/three-bucket-dca.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.timer /etc/systemd/system/
sudo cp deploy/three-bucket-dca-halving.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-halving.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable three-bucket-dca-halving.timer
sudo systemctl start three-bucket-dca.service three-bucket-dca-update.timer three-bucket-dca-halving.timer
trap - EXIT
