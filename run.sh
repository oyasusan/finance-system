#!/bin/bash
# 日本新興市場・小型株モニター 起動スクリプト
# .venv はSDカードがシンボリックリンク非対応のため ~/finance-system/.venv を使用
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$HOME/finance-system/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
  echo "仮想環境が見つかりません: $VENV_PYTHON"
  echo "以下を実行してセットアップしてください:"
  echo "  cd ~/finance-system && uv venv && uv pip install -r requirements.txt"
  exit 1
fi

"$VENV_PYTHON" "$SCRIPT_DIR/monitor.py" "$@"
