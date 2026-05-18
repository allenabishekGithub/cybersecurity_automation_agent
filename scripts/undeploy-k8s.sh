#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST_DIR="${ROOT_DIR}/devops/kubernetes"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

NAMESPACE="${K8S_NAMESPACE:-rag-agent}"

kubectl -n "${NAMESPACE}" delete -f "${MANIFEST_DIR}" --ignore-not-found=true
kubectl -n "${NAMESPACE}" delete secret rag-assistant-secret --ignore-not-found=true
kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
