#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT=""
TAG=""
ARTIFACT_PATH=""
ALLOW_DIRTY="false"

AWS_REGION="ap-northeast-1"
SERVICE_NAME="exa-tts-service"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

usage() {
  cat <<EOF
Usage:
  ./scripts/publish-image.sh --env <development|staging|production> [--tag <immutable-tag>] [--artifact-path <path>] [--allow-dirty]

Examples:
  ./scripts/publish-image.sh --env development
  ./scripts/publish-image.sh --env staging --tag 20260707-abcd123
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENVIRONMENT="${2:-}"
      shift 2
      ;;
    --tag)
      TAG="${2:-}"
      shift 2
      ;;
    --artifact-path)
      ARTIFACT_PATH="${2:-}"
      shift 2
      ;;
    --allow-dirty)
      ALLOW_DIRTY="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$ENVIRONMENT" ]] || fail "--env is required"

case "$ENVIRONMENT" in
  development|staging|production) ;;
  *) fail "Invalid env: $ENVIRONMENT" ;;
esac

require_command aws
require_command docker
require_command git
require_command date

validate_tag() {
  local value="$1"

  [[ -n "$value" ]] || fail "Tag is required"

  if [[ "$value" == "latest" ]]; then
    fail "Tag 'latest' is not allowed"
  fi

  if [[ ! "$value" =~ ^[0-9]{8}-[0-9a-f]{7,12}$ ]]; then
    fail "Tag must match YYYYMMDD-shortsha, got: $value"
  fi
}

ensure_clean_git() {
  if [[ "$ALLOW_DIRTY" == "true" ]]; then
    return
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    fail "Git working tree is dirty. Commit/stash changes first, or pass --allow-dirty explicitly."
  fi
}

generate_tag() {
  local date_part
  local sha_part

  date_part="$(date -u +"%Y%m%d")"
  sha_part="$(git rev-parse --short=7 HEAD 2>/dev/null)" || fail "Unable to resolve git SHA"

  echo "${date_part}-${sha_part}"
}

case "$ENVIRONMENT" in
  development)
    REPOSITORY_NAME="exa-dev-exa-tts-service"
    BUILD_TARGET="staging"
    ;;
  staging)
    REPOSITORY_NAME="exa-stg-exa-tts-service"
    BUILD_TARGET="staging"
    ;;
  production)
    REPOSITORY_NAME="exa-prod-exa-tts-service"
    BUILD_TARGET="prod"
    ;;
esac

if [[ -z "$TAG" ]]; then
  ensure_clean_git
  TAG="$(generate_tag)"
fi

validate_tag "$TAG"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
[[ -n "$ACCOUNT_ID" && "$ACCOUNT_ID" != "None" ]] || fail "Unable to resolve AWS account id"

aws ecr describe-repositories \
  --repository-names "$REPOSITORY_NAME" \
  --region "$AWS_REGION" \
  >/dev/null 2>&1 || fail "ECR repository '$REPOSITORY_NAME' does not exist in account '$ACCOUNT_ID' region '$AWS_REGION'"

REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE_URI="${REGISTRY}/${REPOSITORY_NAME}:${TAG}"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

docker build --target "$BUILD_TARGET" -t "$FULL_IMAGE_URI" .
docker push "$FULL_IMAGE_URI"

DIGEST="$(aws ecr describe-images \
  --repository-name "$REPOSITORY_NAME" \
  --image-ids imageTag="$TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text \
  --region "$AWS_REGION")"

[[ -n "$DIGEST" && "$DIGEST" != "None" ]] || fail "Unable to resolve image digest"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [[ -z "$ARTIFACT_PATH" ]]; then
  mkdir -p artifacts
  ARTIFACT_PATH="artifacts/publish-image-${ENVIRONMENT}-${TAG}.json"
fi

cat > "$ARTIFACT_PATH" <<EOF
{
  "service": "${SERVICE_NAME}",
  "environment": "${ENVIRONMENT}",
  "repository": "${REPOSITORY_NAME}",
  "tag": "${TAG}",
  "digest": "${DIGEST}",
  "timestamp": "${TIMESTAMP}"
}
EOF

echo "Publish succeeded"
echo "Service     : ${SERVICE_NAME}"
echo "Environment : ${ENVIRONMENT}"
echo "Repository  : ${REPOSITORY_NAME}"
echo "Tag         : ${TAG}"
echo "Digest      : ${DIGEST}"
echo "Artifact    : ${ARTIFACT_PATH}"
