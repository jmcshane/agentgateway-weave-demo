#!/usr/bin/env bash
# Reads the most recent conversation back from Weave's agent spans and prints its span tree.
# Usage: scripts/verify.sh [conversation-id]   (defaults to the newest conversation)
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

PROJECT="${WANDB_ENTITY}/${WANDB_PROJECT}"

query() {
  curl -sS --fail-with-body -u "api:${WANDB_API_KEY}" https://trace.wandb.ai/agents/spans/query \
    -H 'content-type: application/json' -d "$1"
}

CONV="${1:-$(query "$(jq -nc --arg p "$PROJECT" '{
    project_id: $p, limit: 1,
    query: {"$expr": {"$not": [{"$eq": [{"$getField": "conversation_id"}, {"$literal": ""}]}]}},
    sort_by: [{field: "started_at", direction: "desc"}]
  }')" | jq -r '.spans[0].conversation_id // empty')}"

if [ -z "$CONV" ]; then
  echo "no conversation found in $PROJECT" >&2
  exit 1
fi

SPANS="$(query "$(jq -nc --arg p "$PROJECT" --arg c "$CONV" '{
    project_id: $p, limit: 200, include_details: true, include_costs: true,
    query: {"$expr": {"$eq": [{"$getField": "conversation_id"}, {"$literal": $c}]}},
    sort_by: [{field: "started_at", direction: "asc"}]
  }')")"

if ! jq -e '.spans | length > 0' <<< "$SPANS" > /dev/null; then
  echo "no spans for conversation $CONV in $PROJECT" >&2
  exit 1
fi

echo "conversation $CONV"

# A span whose parent never reached Weave is printed as a root and flagged.
jq -r '
  .spans as $s
  | ($s | map(.span_id)) as $ids
  | def children($id): $s | map(select(.parent_span_id == $id));
    def line: if .operation_name == "chat"
      then "chat \(.response_model)  in=\(.input_tokens) cached=\(.cache_read_input_tokens) out=\(.output_tokens) reasoning=\(.reasoning_tokens) cost=$\(.total_cost_usd // "n/a")"
      else (if .operation_name == "" then .span_name else .operation_name + (if .tool_name != "" then " " + .tool_name else "" end) end) end;
    def tree($depth): ("  " * $depth) + line, (children(.span_id)[] | tree($depth + 1));
  $s
  | map(select(.parent_span_id == "" or (.parent_span_id as $p | $ids | index($p) | not)))
  | .[]
  | "trace \(.trace_id)" + (if .parent_span_id == "" then "" else "  (parent span \(.parent_span_id) is missing from Weave)" end),
    tree(1)
' <<< "$SPANS"
