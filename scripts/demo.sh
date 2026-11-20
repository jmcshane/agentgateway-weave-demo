#!/usr/bin/env bash
# Sends a three-turn conversation through the gateway to Kimi K2.6 on CoreWeave.
# All turns share a long system prompt, so later turns usually read most of their
# input from the prompt cache (hits are best-effort and vary between runs). Run scripts/verify.sh afterwards to read the
# conversation back from Weave.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

GW="http://localhost:${GATEWAY_PORT:-4000}"
MODEL="${MODEL:-coreweave/moonshotai/Kimi-K2.6}"
CONV="conv-$(date +%s)"

# ~5k token system prompt, random per run so the first turn is a cache miss.
SYSTEM=$(python3 -c "
import random
r = random.Random()
print('You are a terse travel assistant. Reference data: ' +
      ' '.join(f'City {i}: code {r.randint(0, 10**9)}.' for i in range(500)))")

ask() {
  local question=$1
  local body
  body=$(jq -nc --arg m "$MODEL" --arg s "$SYSTEM" --arg q "$question" '{
    model: $m, max_tokens: 800, temperature: 0.6, top_p: 0.95,
    messages: [{role: "system", content: $s}, {role: "user", content: $q}]
  }')

  curl -sS "$GW/v1/chat/completions" \
    -H 'content-type: application/json' \
    -H "x-conversation-id: $CONV" \
    -H 'x-agent-name: travel-helper' \
    -H "x-user-id: ${USER:-demo}" \
    -d "$body" |
    jq -r '"prompt=\(.usage.prompt_tokens) cached=\(.usage.prompt_tokens_details.cached_tokens // 0) completion=\(.usage.completion_tokens)\n  -> \(.choices[0].message.content // .error.message)"'
}

echo "conversation $CONV"
ask "Best month to visit Lisbon? One sentence."
ask "What is the code for City 7? One sentence."
ask "And City 42? One sentence."

echo
echo "Conversations: https://forge.coreweave.com/agent-lens/${WANDB_ENTITY}/${WANDB_PROJECT}/conversations"
echo "Spans are exported in batches; wait ~10s, then run scripts/verify.sh."
