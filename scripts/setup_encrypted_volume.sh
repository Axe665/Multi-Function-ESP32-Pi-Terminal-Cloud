#!/usr/bin/env bash
# 给云 Postgres 数据目录做 LUKS 加密卷（文档 §8.2）。
#
# 文件模式（本机可测/演示）：
#   sudo scripts/setup_encrypted_volume.sh --file /var/lib/mt-cloud.img --size 20G
# 块设备模式（生产）：
#   sudo scripts/setup_encrypted_volume.sh --device /dev/sdb
#
# 完成后把输出的 CLOUD_PGDATA 写进 docker/.env，再重启 postgres：
#   docker compose up -d postgres
set -euo pipefail

NAME="${CLOUD_LUKS_NAME:-mt-cloud-data}"
MOUNT="${CLOUD_LUKS_MOUNT:-/srv/modular-terminal-cloud}"
IMAGE=""
SIZE="20G"
DEVICE=""
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --file) IMAGE="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --mount) MOUNT="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "未知参数：$1"; exit 2 ;;
  esac
done

if [ -z "$IMAGE" ] && [ -z "$DEVICE" ]; then
  echo "用法：$0 --file <镜像文件> [--size 20G] | --device <块设备> [--mount /srv/...]"
  exit 2
fi

run() {
  echo "+ $*"
  [ "$DRY" = 1 ] || "$@"
}

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
  echo "需要 root：sudo $0 ..."
  exit 1
fi

if [ -n "$IMAGE" ]; then
  if [ "$DRY" = 1 ]; then
    SOURCE="<loop:$IMAGE>"
  else
    if [ ! -f "$IMAGE" ]; then
      truncate -s "$SIZE" "$IMAGE"
    fi
    LOOP="$(losetup --find --show "$IMAGE")"
    SOURCE="$LOOP"
  fi
else
  SOURCE="$DEVICE"
fi

if [ "$DRY" = 0 ] && cryptsetup isLuks "$SOURCE"; then
  echo "已是 LUKS 卷，直接打开：$SOURCE"
else
  echo "警告：$SOURCE 将被格式化，里面的数据会丢失。"
  run cryptsetup luksFormat --type luks2 --batch-mode "$SOURCE"
fi

if [ ! -e "/dev/mapper/$NAME" ]; then
  run cryptsetup open "$SOURCE" "$NAME"
fi
run mkdir -p "$MOUNT"
if ! mountpoint -q "$MOUNT"; then
  if ! blkid "/dev/mapper/$NAME" >/dev/null 2>&1; then
    run mkfs.ext4 -L "$NAME" "/dev/mapper/$NAME"
  fi
  run mount "/dev/mapper/$NAME" "$MOUNT"
fi

cat <<EOF

加密卷已就绪：/dev/mapper/$NAME → $MOUNT
在 docker/.env 里加：

  CLOUD_PGDATA=$MOUNT

然后：cd docker && docker compose up -d postgres

注意：
- 需要开机自动挂载时，把 /dev/mapper/$NAME 写进 /etc/crypttab 与 /etc/fstab；
- LUKS 口令/密钥文件不要和备份放在同一块盘；
- 现有 pgdata 卷里的数据不会自动搬过去，先 pg_dump 再导入。
EOF
