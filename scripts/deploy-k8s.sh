#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
MANIFEST_DIR="${ROOT_DIR}/devops/kubernetes"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[deploy-k8s.sh] Missing ${ENV_FILE}" >&2
    echo "[deploy-k8s.sh] Copy .env.example to .env and fill in your values." >&2
    exit 1
fi

set -a
source "${ENV_FILE}"
set +a

K8S_IMAGE_LOAD_MODE="${K8S_IMAGE_LOAD_MODE:-none}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-kind}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
NAMESPACE="${K8S_NAMESPACE:-rag-agent}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    RAG_IMAGE="${RAG_IMAGE:-${K8S_IMAGE_REGISTRY}/rag-assistant:dev}"
    MININET_IMAGE="${MININET_IMAGE:-${K8S_IMAGE_REGISTRY}/mininet-sim:dev}"
else
    RAG_IMAGE="${RAG_IMAGE:-rag-assistant:dev}"
    MININET_IMAGE="${MININET_IMAGE:-mininet-sim:dev}"
fi

import_into_microk8s() {
    local image="$1"
    local tar_file
    tar_file="$(mktemp --suffix=.tar)"
    docker save "${image}" -o "${tar_file}"
    microk8s ctr image import "${tar_file}"
    rm -f "${tar_file}"
}

ensure_local_image() {
    local image="$1"
    if ! docker image inspect "${image}" >/dev/null 2>&1; then
        echo "[deploy-k8s.sh] Missing local image: ${image}" >&2
        echo "[deploy-k8s.sh] Build images first: ./scripts/build-image.sh" >&2
        exit 1
    fi
}

if [[ "${K8S_IMAGE_LOAD_MODE}" != "registry" ]]; then
    ensure_local_image "${RAG_IMAGE}"
    ensure_local_image "${MININET_IMAGE}"
fi

# Load image into cluster
case "${K8S_IMAGE_LOAD_MODE}" in
    none)
        echo "[deploy-k8s.sh] Skipping image load. Ensure your cluster can access: ${RAG_IMAGE}"
        ;;
    kind)
        kind load docker-image "${RAG_IMAGE}" --name "${KIND_CLUSTER_NAME}"
        kind load docker-image "${MININET_IMAGE}" --name "${KIND_CLUSTER_NAME}"
        ;;
    minikube)
        minikube image load "${RAG_IMAGE}"
        minikube image load "${MININET_IMAGE}"
        ;;
    registry)
        docker push "${RAG_IMAGE}"
        docker push "${MININET_IMAGE}"
        ;;
    microk8s)
        command -v microk8s >/dev/null || {
            echo "[deploy-k8s.sh] K8S_IMAGE_LOAD_MODE=microk8s but 'microk8s' command is not available." >&2
            exit 1
        }
        import_into_microk8s "${RAG_IMAGE}"
        import_into_microk8s "${MININET_IMAGE}"
        ;;
    *)
        echo "[deploy-k8s.sh] Unsupported K8S_IMAGE_LOAD_MODE='${K8S_IMAGE_LOAD_MODE}'" >&2
        exit 1
        ;;
esac

# Create namespace
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Create secret
TMP_DIR="$(mktemp -d)"
PF_PID=""
cleanup() {
    # Detach port-forward from this shell so it survives script exit.
    [[ -n "${PF_PID}" ]] && disown "${PF_PID}" 2>/dev/null || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

cat > "${TMP_DIR}/secret.env" <<EOF
OPENAI_API_KEY=${OPENAI_API_KEY}
EOF

kubectl -n "${NAMESPACE}" create secret generic rag-assistant-secret \
    --from-env-file="${TMP_DIR}/secret.env" \
    --dry-run=client -o yaml | kubectl apply -f -

# Apply manifests and set image
kubectl -n "${NAMESPACE}" apply -f "${MANIFEST_DIR}"
kubectl -n "${NAMESPACE}" set image deployment/rag-assistant rag-assistant="${RAG_IMAGE}"
kubectl -n "${NAMESPACE}" set image deployment/mininet-sim mininet-sim="${MININET_IMAGE}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    kubectl -n "${NAMESPACE}" patch deployment/rag-assistant \
        -p '{"spec":{"template":{"spec":{"containers":[{"name":"rag-assistant","imagePullPolicy":"Always"}]}}}}'
    kubectl -n "${NAMESPACE}" patch deployment/mininet-sim \
        -p '{"spec":{"template":{"spec":{"containers":[{"name":"mininet-sim","imagePullPolicy":"Always"}]}}}}'
fi

kubectl -n "${NAMESPACE}" rollout restart deployment/rag-assistant
kubectl -n "${NAMESPACE}" rollout restart deployment/mininet-sim
kubectl -n "${NAMESPACE}" rollout status deployment/rag-assistant --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deployment/mininet-sim --timeout=180s

# Kill any existing port-forward on 8501 before starting a new one
if lsof -ti tcp:8501 &>/dev/null; then
    kill "$(lsof -ti tcp:8501)" 2>/dev/null || true
    sleep 1
fi

kubectl -n "${NAMESPACE}" port-forward svc/rag-assistant 8501:8501 &
PF_PID=$!

# Give the tunnel a moment to establish
sleep 2

if kill -0 "${PF_PID}" 2>/dev/null; then
    echo ""
    echo "Deployment ready in namespace '${NAMESPACE}'."
    echo "Port-forward running in background (PID ${PF_PID})."
    echo "Open: http://localhost:8501"
    echo "Stop tunnel: kill ${PF_PID}"
else
    echo "[deploy-k8s.sh] Port-forward failed to start. Run manually:" >&2
    echo "  kubectl -n ${NAMESPACE} port-forward svc/rag-assistant 8501:8501" >&2
fi
