#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

K8S_IMAGE_LOAD_MODE="${K8S_IMAGE_LOAD_MODE:-none}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    RAG_IMAGE="${RAG_IMAGE:-${K8S_IMAGE_REGISTRY}/rag-assistant:dev}"
    MININET_IMAGE="${MININET_IMAGE:-${K8S_IMAGE_REGISTRY}/mininet-sim:dev}"
else
    RAG_IMAGE="${RAG_IMAGE:-rag-assistant:dev}"
    MININET_IMAGE="${MININET_IMAGE:-mininet-sim:dev}"
fi

cd "${ROOT_DIR}"
docker build -f devops/docker/Dockerfile -t "${RAG_IMAGE}" .
docker build -f devops/docker/Mininet.Dockerfile -t "${MININET_IMAGE}" .

echo "Built image: ${RAG_IMAGE}"
echo "Built image: ${MININET_IMAGE}"
