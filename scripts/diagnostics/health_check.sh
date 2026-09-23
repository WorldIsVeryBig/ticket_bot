#!/bin/bash
# 全環境健康檢查 — 檢查所有 bot 運行狀態
# Usage: ./scripts/diagnostics/health_check.sh
set -o pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/your-ticket-bot-key.pem}"
AWS_HOSTS=("your-aws-host-1" "your-aws-host-2")
AWS_NAMES=("aws-node-1" "aws-node-2")
GCP_INSTANCE="${GCP_INSTANCE:-your-instance}"
GCP_ZONE="${GCP_ZONE:-asia-east1-b}"
GCP_PROJECT="${GCP_PROJECT:-your-project-id}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

echo "============================================"
echo "  Ticket Bot 全環境健康檢查"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
echo ""

# ── 1. 本機進程 ──────────────────────────────────
echo "【本機】"

# TG Bot
TG_PID=$(pgrep -f "ticket-bot.*bot.*telegram" 2>/dev/null | head -1)
if [ -n "$TG_PID" ]; then
    TG_INFO=$(ps -p "$TG_PID" -o etime=,%cpu=,%mem= 2>/dev/null | xargs)
    ok "TG Bot (PID $TG_PID): 運行中 [$TG_INFO]"
else
    fail "TG Bot: 未運行"
fi

# CLI Watch
WATCH_PID=$(pgrep -f "ticket-bot.*watch" 2>/dev/null | head -1)
if [ -n "$WATCH_PID" ]; then
    WATCH_INFO=$(ps -p "$WATCH_PID" -o etime=,%cpu=,%mem= 2>/dev/null | xargs)
    ok "CLI Watch (PID $WATCH_PID): 運行中 [$WATCH_INFO]"
else
    echo "  - CLI Watch: 未運行"
fi

# Chrome 進程
CHROME_COUNT=$(pgrep -f "chrome.*(ticket-bot|chrome_profile)" 2>/dev/null | wc -l | xargs)
if [ "$CHROME_COUNT" -gt 0 ]; then
    ok "Chrome 進程: ${CHROME_COUNT} 個"
else
    warn "Chrome 進程: 0 個"
fi

# Ollama
OLLAMA_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:11434/api/tags" 2>/dev/null)
if [ "$OLLAMA_STATUS" = "200" ]; then
    ok "Ollama: 正常"
else
    warn "Ollama: 無回應 (HTTP $OLLAMA_STATUS)"
fi

# Tixcraft 連線
TIXCRAFT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://tixcraft.com/" 2>/dev/null)
if [ "$TIXCRAFT_STATUS" = "200" ] || [ "$TIXCRAFT_STATUS" = "401" ] || [ "$TIXCRAFT_STATUS" = "403" ]; then
    ok "Tixcraft 連線: HTTP $TIXCRAFT_STATUS"
else
    fail "Tixcraft 連線: HTTP $TIXCRAFT_STATUS (超時或無法連線)"
fi

echo ""

# ── 2. AWS Tokyo ─────────────────────────────────
echo "【AWS Tokyo】"

for i in "${!AWS_HOSTS[@]}"; do
    HOST="${AWS_HOSTS[$i]}"
    NAME="${AWS_NAMES[$i]}"
    echo "  $NAME ($HOST):"

    # SSH 連線測試
    DOCKER_PS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 "ec2-user@$HOST" \
        "docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null" 2>/dev/null)

    if [ $? -ne 0 ]; then
        fail "  SSH 連線失敗"
        continue
    fi

    if [ -z "$DOCKER_PS" ]; then
        fail "  無運行中的容器"
        continue
    fi

    while IFS='|' read -r cname cstatus; do
        ok "  $cname: $cstatus"

        # 抓最後幾行 log 檢查錯誤
        RECENT_LOG=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 "ec2-user@$HOST" \
            "docker logs --tail 5 $cname 2>&1" 2>/dev/null)

        # 檢查關鍵錯誤
        if echo "$RECENT_LOG" | grep -qi "singularitylock\|SingletonLock\|profile appears to be in use"; then
            fail "  Chrome Profile Lock 錯誤！"
        fi
        if echo "$RECENT_LOG" | grep -qi "LoginExpiredError\|登入過期"; then
            fail "  登入已過期！需重新同步 cookies"
        fi
        if echo "$RECENT_LOG" | grep -qi "連續.*403\|連續.*blocked"; then
            STREAK=$(echo "$RECENT_LOG" | grep -o "連續 [0-9]*" | tail -1)
            warn "  有 403 封鎖 ($STREAK)"
        fi

        # 最新一行 log
        LAST_LINE=$(echo "$RECENT_LOG" | tail -1)
        echo "      最新: ${LAST_LINE:0:100}"
    done <<< "$DOCKER_PS"
done

echo ""

# ── 3. GCP ───────────────────────────────────────
echo "【GCP Taiwan】"

GCP_STATUS=$(gcloud compute instances describe "$GCP_INSTANCE" \
    --zone="$GCP_ZONE" --project="$GCP_PROJECT" \
    --format="get(status)" 2>/dev/null)

if [ "$GCP_STATUS" = "RUNNING" ]; then
    ok "VM 狀態: RUNNING"

    GCP_DOCKER=$(gcloud compute ssh "$GCP_INSTANCE" \
        --zone="$GCP_ZONE" --project="$GCP_PROJECT" --tunnel-through-iap \
        --command="docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null" 2>/dev/null)

    if [ -n "$GCP_DOCKER" ]; then
        while IFS='|' read -r cname cstatus; do
            ok "$cname: $cstatus"
        done <<< "$GCP_DOCKER"
    else
        echo "  - 無運行中的容器"
    fi
elif [ -n "$GCP_STATUS" ]; then
    warn "VM 狀態: $GCP_STATUS"
else
    warn "GCP VM: 無法取得狀態"
fi

echo ""
echo "============================================"
echo "  檢查完成"
echo "============================================"
