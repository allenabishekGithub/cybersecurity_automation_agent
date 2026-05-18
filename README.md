# RAG Domain Assistant — Streamlit

A single-page Streamlit app that lets a user paste their OpenAI API key,
upload PDF/TXT documents, and chat against them with full RAG: retrieval,
streamed reasoning, conversation memory, and source citations.

If the answer isn't in the uploaded documents, the bot replies:

> *"I don't have enough information in the provided documents."*

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    app.py  (Streamlit UI)                      │
│        only file that touches Streamlit; thin & declarative    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────┐
│                    manager.AppManager                          │
│    __init__: api key → IngestionManager, LayeredMemory         │
│    index_documents(): builds RetrieverManager + RAGAgent       │
└──┬──────────────┬────────────────────┬──────────────┬──────────┘
   │              │                    │              │
   ▼              ▼                    ▼              ▼
┌─────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│Ingestion│ │Retriever     │ │RAGAgent      │ │LayeredMemory │
│Manager  │ │Manager       │ │contextualise │ │summary +     │
│PDF/TXT  │ │similarity    │ │+ retrieve    │ │window +      │
│→ FAISS  │ │search top-k  │ │+ stream      │ │full history  │
└─────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

One class per file under `components/`, a manager that composes them, and a
single entrypoint (`app.py`) that knows about Streamlit.

---

## Request flow

```
User uploads files
       │
       ▼
IngestionManager      reads PDF/TXT bytes → chunks (~1000 chars each) →
                      embeds via OpenAI → stores vectors in FAISS (in RAM)
       │
       ▼
User asks a question
       │
       ▼
RAGAgent._contextualize   rewrites follow-up questions into standalone queries
                          using recent chat history
                          e.g. "what did it say?" → full self-contained question
       │
       ▼
RetrieverManager.retrieve similarity search in FAISS → top-4 chunks
       │
       ▼
RAGAgent.stream_answer    builds prompt:
                            [system: instructions + retrieved chunks]
                            [memory: rolling summary, if any]
                            [last N turns verbatim]
                            [user question]
                          streams tokens from gpt-4o-mini
       │
       ▼
LayeredMemory.add_turn    stores the completed turn; once history > 6 turns,
                          oldest turns get compressed into a rolling summary
```

> **Note:** FAISS is in-memory only. The index is lost on pod restart —
> users must re-upload files each session.

---

## How memory works

Three layers, used at different stages:

1. **Full transcript** — kept in `st.session_state.chat_log`; rendered in the
   chat UI on every rerun. Nothing the user sees is ever truncated.
2. **Recent window** — the last `window_size` turns (default 4) are passed
   verbatim to the model on every request.
3. **Rolling summary** — once the conversation grows past `summarize_after`
   turns (default 6), the oldest extra turns are folded into a running
   summary by the LLM and sent as a system message ahead of the window.

Prompt shape sent to the model on every turn:

```
[ system: grounded-answering instructions + retrieved context ]
[ system: summary of earlier conversation ]      ← only if turns > 6
[ last N turns, verbatim ]
[ new user question ]
```

The sidebar shows live stats — total turns, how many are in the window, how
many are summarised, and the current summary text.

---

## Project structure

```
assignment4_iitm/
├── app.py                       # Streamlit entrypoint (the whole UI)
├── manager.py                   # AppManager facade
├── components/
│   ├── ingestion.py             # IngestionManager  (PDF/TXT → FAISS)
│   ├── retriever.py             # RetrieverManager  (similarity search)
│   ├── memory.py                # LayeredMemory     (summary + window + log)
│   └── rag_agent.py             # RAGAgent          (contextualise → retrieve → stream)
├── devops/
│   ├── docker/
│   │   ├── Dockerfile           # python:3.11-slim, streamlit run app.py
│   │   ├── Mininet.Dockerfile   # Mininet + OVS simulator image
│   │   └── docker-compose.yml   # local Docker dev
│   └── kubernetes/
│       ├── deployment.yaml      # 1-replica pod, health probes
│       ├── service.yaml         # ClusterIP on :8501
│       ├── mininet-deployment.yaml
│       ├── mininet-service.yaml
│       └── ingress.yaml         # /rag-agent/* external route
├── network_sim/
│   ├── mininet_router_switch_topo.py
│   ├── mininet_network_api.py   # /health, /topology, /implement
│   └── docs/
│       ├── topology_reference.pdf
│       └── network_hardening_practices.pdf
├── scripts/
│   ├── build-image.sh           # docker build
│   ├── deploy-k8s.sh            # full deploy + port-forward
│   └── undeploy-k8s.sh          # teardown
├── .dockerignore
├── .env.example
├── requirements.txt
└── structural_check.py          # OOP wiring check (no API key needed)
```

---

## What's grounded vs not

| Step | Source of truth |
|---|---|
| Standalone-query rewrite | LLM uses chat history (no docs) |
| Retrieval | FAISS index built from uploaded docs |
| Answer | LLM, prompted to use ONLY the retrieved chunks |
| Refusal | Triggered when the docs don't contain the answer |

No fine-tuning, no hard-coded answers, no outside knowledge in the final
response — retrieval drives every reply.

---

## Tunables

All in `manager.AppManager.__init__`:

| Param | Default | What it does |
|---|---|---|
| `chat_model` | `gpt-4o-mini` | Reasoning + summarisation model |
| `embed_model` | `text-embedding-3-small` | Embedding model |
| `top_k` | `4` | Chunks retrieved per query |
| `window_size` | `4` | Recent turns passed verbatim |
| `summarize_after` | `6` | When to start rolling old turns into summary |

---

## Local setup (no Docker)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

No `.env` needed — paste your OpenAI API key in the sidebar. The key lives
only in `st.session_state` and is never written to disk.

Usage:

1. Paste your OpenAI API key in the sidebar.
2. Upload one or more PDF/TXT files. Click **Build index**.
3. Ask questions in the chat input. Answers stream token-by-token,
   citations appear underneath, and the **Memory** panel updates after each turn.
4. Hit **Clear conversation** to reset history (the index stays loaded).
5. Upload different files + **Build index** again to switch domains mid-session.

Sample uploadable PDFs are included in:

- `network_sim/docs/topology_reference.pdf`
- `network_sim/docs/network_hardening_practices.pdf`

When the user asks the assistant to implement a network change (for example:
`"Implement baseline hardening"` or `"Block ICMP from h1 to h3"`), the app
calls the Mininet simulator API in-cluster and appends an implementation
confirmation section with applied actions and verification checks.

Current simulator actions: baseline hardening, telnet block, SSH forward block,
SMB forward block (`tcp/445`), DNS egress restriction (`tcp/udp 53`), router
SSH allowlist from `h1`, and ICMP block (`h1`→`h3`). If a request is generic,
baseline hardening is applied by default.

The sidebar also includes a dedicated **Topology State** panel to refresh and
inspect live nodes, links, routes, and active policy rules from Mininet.

---

## Docker

### Build and run locally

```bash
cp .env.example .env          # set OPENAI_API_KEY
docker compose -f devops/docker/docker-compose.yml up --build
```

Open `http://localhost:8501`.

### Build image only

```bash
./scripts/build-image.sh
```

Produces images `rag-assistant:dev` and `mininet-sim:dev`
(override with `RAG_IMAGE=` and `MININET_IMAGE=` in `.env`).

---

## Kubernetes deployment

### How it works

```
Browser :8501
    │  (kubectl port-forward tunnel)
    ▼
Service: rag-assistant (ClusterIP :8501)
    │
    ▼
Pod: rag-assistant
    ├── OPENAI_API_KEY injected from Secret → pre-fills the sidebar
    ├── MININET_API_URL=http://mininet-sim:8080
    ├── User uploads PDF → FAISS index built in pod RAM
    ├── User asks "implement/apply/harden..." → call mininet-sim API
    ├── User asks question → OpenAI API called (outbound)
    └── Answer streamed back to browser

Service: mininet-sim (ClusterIP :8080)
    │
    ▼
Pod: mininet-sim (privileged)
    ├── Starts Mininet topology (2 routers, 3 switches, 4 hosts)
    ├── Exposes /health, /topology, /implement
    └── Applies and verifies supported hardening actions
```

The **Ingress** (`/rag-agent/*`) is for clusters with an nginx ingress
controller — it provides external access without port-forwarding.

### Kubernetes objects created

| Object | Kind | Details |
|---|---|---|
| `rag-agent` | Namespace | Isolated namespace for all resources |
| `rag-assistant` | Deployment | 1 replica, `/_stcore/health` probes |
| `rag-assistant` | Service | ClusterIP, port 8501 |
| `mininet-sim` | Deployment | 1 replica, privileged pod, `/health` probes |
| `mininet-sim` | Service | ClusterIP, port 8080 |
| `rag-assistant` | Ingress | Routes `/rag-agent/*` from outside |
| `rag-assistant-secret` | Secret | Holds `OPENAI_API_KEY` |

### Prerequisites

- `kubectl` configured and pointing at your cluster
- Docker daemon running
- One of: `microk8s`, `kind`, `minikube`, or a registry — set `K8S_IMAGE_LOAD_MODE` accordingly

### Deploy

```bash
cp .env.example .env
# Required: fill in OPENAI_API_KEY
# Required: set K8S_IMAGE_LOAD_MODE to match your cluster (microk8s / kind / minikube / registry / none)
./scripts/build-image.sh
./scripts/deploy-k8s.sh
```

`build-image.sh`:

1. Builds both Docker images (`rag-assistant`, `mininet-sim`)

`deploy-k8s.sh`:

1. Loads/pushes both images (microk8s / kind / minikube / registry, depending on `K8S_IMAGE_LOAD_MODE`)
2. Creates the `rag-agent` namespace
3. Creates the `rag-assistant-secret` from `OPENAI_API_KEY`
4. Applies all Kubernetes manifests
5. Waits for both rollouts to complete (readiness probes must pass)
6. Starts `kubectl port-forward` in the background automatically

Once done, open `http://localhost:8501`.

### Access Web UI

Open this URL in your browser:

```text
http://localhost:8501
```

If `./scripts/deploy-k8s.sh` is running, it starts port-forward automatically.
If the tunnel is not running, start it manually:

```bash
kubectl -n rag-agent port-forward svc/rag-assistant 8501:8501
```

To stop the port-forward, use the PID printed by the script:

```bash
kill <PID>
```

### Teardown

```bash
./scripts/undeploy-k8s.sh
```

Deletes all objects and removes the `rag-agent` namespace entirely.

### `.env` reference

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | _(empty)_ | Pre-populates the API key field in the UI |
| `RAG_IMAGE` | `rag-assistant:dev` | Docker image name and tag |
| `MININET_IMAGE` | `mininet-sim:dev` | Mininet simulator image name and tag |
| `APP_PORT` | `8501` | Host port for docker-compose |
| `K8S_NAMESPACE` | `rag-agent` | Kubernetes namespace |
| `K8S_IMAGE_LOAD_MODE` | `none` | How to get the image into the cluster: `microk8s` / `none` / `kind` / `minikube` / `registry` |
| `K8S_IMAGE_REGISTRY` | `localhost:32000` | Registry URL (only used when `K8S_IMAGE_LOAD_MODE=registry`) |
| `KIND_CLUSTER_NAME` | `kind` | Kind cluster name (only used when `K8S_IMAGE_LOAD_MODE=kind`) |
