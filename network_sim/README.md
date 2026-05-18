# Mininet Topology Lab

This folder contains a simple, self-contained Mininet topology with:

- 2 routers (`r1`, `r2`)
- 3 switches (`s1`, `s2`, `s3`)
- 4 end hosts (`h1`, `h2`, `h3`, `h4`)

Files:

- `mininet_router_switch_topo.py`
- `mininet_network_api.py`
- `docs/topology_reference.pdf`
- `docs/network_hardening_practices.pdf`

## Topology

```text
h1 ---\
       s1 --- r1 --- s2 --- r2 --- s3 --- h3
h2 ---/                             \--- h4
```

Subnets:

- LAN A: `10.0.1.0/24` (`h1`, `h2`, gateway `r1=10.0.1.1`)
- Transit: `10.0.12.0/30` (`r1=10.0.12.1`, `r2=10.0.12.2`)
- LAN B: `10.0.2.0/24` (`h3`, `h4`, gateway `r2=10.0.2.1`)

Routers are Linux nodes with IPv4 forwarding enabled and static routes configured.

## Run

From `/home/allen/rag_agent_assn4_iitm`:

```bash
sudo python3 network_sim/mininet_router_switch_topo.py
```

Useful options:

```bash
# start, run pingAll, then open CLI
sudo python3 network_sim/mininet_router_switch_topo.py --pingall

# start, run smoke tests, and exit (no CLI)
sudo python3 network_sim/mininet_router_switch_topo.py --smoke-test --no-cli
```

## Inside Mininet CLI

Examples:

```bash
mininet> nodes
mininet> net
mininet> h1 ping -c 2 h3
mininet> h2 ping -c 2 h4
mininet> r1 ip route
mininet> r2 ip route
```

Exit with `exit` or `Ctrl-D`.

## Prerequisites

- Mininet installed (`mn`)
- Open vSwitch installed (`ovs-vsctl`)
- Run commands with `sudo`

## API Mode (for Kubernetes pod)

Run the HTTP API server directly:

```bash
sudo python3 network_sim/mininet_network_api.py
```

Endpoints:

- `GET /health`
- `GET /topology`
- `POST /implement` with JSON body: `{"request":"<user request text>"}`

`/implement` currently supports:

- baseline hardening
- disable/block telnet
- disable/block SSH forwarding
- disable/block SMB forwarding (`tcp/445`)
- restrict DNS egress (`tcp/udp 53`)
- router SSH allowlist from `h1` (`10.0.1.11`)
- block ICMP from `h1` to `h3`

If no specific action is detected, it applies baseline hardening by default
and still returns an implementation confirmation.
