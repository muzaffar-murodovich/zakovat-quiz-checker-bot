#!/usr/bin/env bash
# Botni serverga deploy qiladi (lokal mashinadan ishga tushiriladi):
#   ./deploy/deploy.sh
set -euo pipefail

SERVER=myserver
DEST=zakovat-quiz-checker-bot

cd "$(dirname "$0")/.."

rsync -av \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '.env' \
    --exclude 'users.json' \
    --exclude 'state.json' \
    --exclude 'members.json' \
    --exclude '__pycache__' \
    --exclude '.vscode' \
    ./ "$SERVER:$DEST/"

ssh "$SERVER" "
    set -e
    cd $DEST
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
    mkdir -p ~/.config/systemd/user
    cp deploy/zakovat-bot.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable zakovat-bot >/dev/null 2>&1
    systemctl --user restart zakovat-bot
    systemctl --user --no-pager status zakovat-bot | head -8
"
