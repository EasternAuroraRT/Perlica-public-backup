#!/usr/bin/env bash

# 用 npsdk 虚拟环境、以 perlica 用户运行 Script/main.py
# 用法:
#   直接前台运行(开发时用):  ./run.sh
#   若当前不是 perlica, 脚本会自动 su 过去(需能 su 到 perlica)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 不是 perlica 就重新以 perlica 执行(保持 cwd 与脚本路径)
if [ "$(id -un)" != "perlica" ]; then
    exec su perlica -c "cd '$ROOT' && '$0'"
fi

echo "这个脚本用于使用 perlica 用户权限启动程序"

# 关键: 必须在 Script/ 下运行, 这样 `import modules` / `import napcat` 才正确
cd "$ROOT/Script"
exec "$ROOT/npsdk/bin/python" main.py
