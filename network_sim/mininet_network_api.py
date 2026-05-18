#!/usr/bin/env python3
"""
Mininet network simulator API.

Starts a fixed topology and exposes HTTP endpoints to:
  - inspect topology/health
  - implement hardening actions
  - return implementation confirmations
"""

from __future__ import annotations

import json
import logging
import signal
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Tuple

from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import Node, Switch
from mininet.topo import Topo


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("mininet-network-api")


class BridgeSwitch(Switch):
    """Linux bridge switch using brctl — no OVS kernel module required."""

    def start(self, controllers):
        self.cmd(f"brctl addbr {self.name}")
        for intf in self.intfList():
            if self.name in intf.name:
                self.cmd(f"brctl addif {self.name} {intf.name}")
        self.cmd(f"ip link set {self.name} up")

    def stop(self, deleteIntfs=True):
        self.cmd(f"ip link set {self.name} down")
        self.cmd(f"brctl delbr {self.name}")
        super().stop(deleteIntfs=deleteIntfs)

    def connected(self):
        return True


class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")

    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        super().terminate()


class RouterSwitchTopo(Topo):
    def build(self):
        s1 = self.addSwitch("s1")
        s2 = self.addSwitch("s2")
        s3 = self.addSwitch("s3")

        r1 = self.addNode("r1", cls=LinuxRouter)
        r2 = self.addNode("r2", cls=LinuxRouter)

        h1 = self.addHost("h1", ip="10.0.1.11/24", defaultRoute="via 10.0.1.1")
        h2 = self.addHost("h2", ip="10.0.1.12/24", defaultRoute="via 10.0.1.1")
        h3 = self.addHost("h3", ip="10.0.2.11/24", defaultRoute="via 10.0.2.1")
        h4 = self.addHost("h4", ip="10.0.2.12/24", defaultRoute="via 10.0.2.1")

        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(r1, s1, intfName1="r1-eth0", params1={"ip": "10.0.1.1/24"})

        self.addLink(r1, s2, intfName1="r1-eth1", params1={"ip": "10.0.12.1/30"})
        self.addLink(r2, s2, intfName1="r2-eth0", params1={"ip": "10.0.12.2/30"})

        self.addLink(r2, s3, intfName1="r2-eth1", params1={"ip": "10.0.2.1/24"})
        self.addLink(h3, s3)
        self.addLink(h4, s3)


class MininetController:
    def __init__(self):
        self.net = Mininet(
            topo=RouterSwitchTopo(),
            switch=BridgeSwitch,
            controller=None,
            autoSetMacs=True,
        )
        self.applied_changes: List[Dict[str, object]] = []

    def start(self):
        LOGGER.info("Starting Mininet topology")
        self.net.start()
        self._configure_routes()

    def stop(self):
        LOGGER.info("Stopping Mininet topology")
        self.net.stop()

    def _configure_routes(self):
        r1 = self.net["r1"]
        r2 = self.net["r2"]
        r1.cmd("ip route replace 10.0.2.0/24 via 10.0.12.2")
        r2.cmd("ip route replace 10.0.1.0/24 via 10.0.12.1")

    def _router_cmd(self, router: str, cmd: str) -> str:
        return self.net[router].cmd(cmd).strip()

    def _iptables_rule_exists(self, router: str, chain: str, rule: str) -> bool:
        out = self._router_cmd(router, f"iptables -C {chain} {rule}; echo $?")
        status = out.splitlines()[-1].strip() if out else "1"
        return status == "0"

    def _ensure_iptables_rule(
        self, router: str, chain: str, rule: str, *, prepend: bool = False
    ) -> bool:
        if self._iptables_rule_exists(router, chain, rule):
            return False
        op = "-I" if prepend else "-A"
        self._router_cmd(router, f"iptables {op} {chain} {rule}")
        return True

    def _apply_baseline_hardening(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            self._router_cmd(router, "sysctl -w net.ipv4.conf.all.accept_redirects=0")
            self._router_cmd(router, "sysctl -w net.ipv4.conf.all.send_redirects=0")
            self._router_cmd(router, "sysctl -w net.ipv4.icmp_echo_ignore_broadcasts=1")

            if self._ensure_iptables_rule(
                router, "INPUT", "-m conntrack --ctstate INVALID -j DROP"
            ):
                applied.append(f"{router}: INPUT drop invalid packets")
            if self._ensure_iptables_rule(
                router, "FORWARD", "-m conntrack --ctstate INVALID -j DROP"
            ):
                applied.append(f"{router}: FORWARD drop invalid packets")
        if not applied:
            applied.append("Baseline hardening already present on routers")
        return applied

    def _apply_disable_telnet(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router, "FORWARD", "-p tcp --dport 23 -j DROP"
            ):
                applied.append(f"{router}: block forwarded telnet (tcp/23)")
        if not applied:
            applied.append("Telnet block rule already present")
        return applied

    def _apply_block_icmp_h1_h3(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router,
                "FORWARD",
                "-s 10.0.1.11 -d 10.0.2.11 -p icmp -j DROP",
            ):
                applied.append(f"{router}: block ICMP h1 -> h3")
        if not applied:
            applied.append("ICMP block h1 -> h3 already present")
        return applied

    def _apply_block_ssh_forwarding(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router, "FORWARD", "-p tcp --dport 22 -j DROP"
            ):
                applied.append(f"{router}: block forwarded SSH (tcp/22)")
        if not applied:
            applied.append("SSH forwarding block rule already present")
        return applied

    def _apply_block_smb_forwarding(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router, "FORWARD", "-p tcp --dport 445 -j DROP"
            ):
                applied.append(f"{router}: block forwarded SMB (tcp/445)")
        if not applied:
            applied.append("SMB forwarding block rule already present")
        return applied

    def _apply_dns_egress_restriction(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router, "FORWARD", "-p udp --dport 53 -j DROP"
            ):
                applied.append(f"{router}: restrict forwarded DNS UDP (53)")
            if self._ensure_iptables_rule(
                router, "FORWARD", "-p tcp --dport 53 -j DROP"
            ):
                applied.append(f"{router}: restrict forwarded DNS TCP (53)")
        if not applied:
            applied.append("DNS egress restriction rules already present")
        return applied

    def _apply_router_ssh_allowlist_h1(self) -> List[str]:
        applied = []
        for router in ("r1", "r2"):
            if self._ensure_iptables_rule(
                router,
                "INPUT",
                "-p tcp --dport 22 -s 10.0.1.11 -j ACCEPT",
                prepend=True,
            ):
                applied.append(f"{router}: allow SSH mgmt from h1 (10.0.1.11)")
            if self._ensure_iptables_rule(
                router,
                "INPUT",
                "-p tcp --dport 22 -j DROP",
                prepend=False,
            ):
                applied.append(f"{router}: deny other SSH mgmt sources")
        if not applied:
            applied.append("Router SSH allowlist policy already present")
        return applied

    def _ping(self, src: str, dst_ip: str) -> bool:
        out = self.net[src].cmd(f"ping -c 1 -W 1 {dst_ip}")
        return "1 received" in out or "1 packets received" in out

    def _forward_drop_present(self, router: str, dport: int) -> bool:
        rules = self._router_cmd(router, "iptables -S FORWARD")
        return f"--dport {dport}" in rules and "-j DROP" in rules

    def _verify(self, request: str) -> List[str]:
        checks = []
        req = request.lower()

        # Baseline reachability check (expected true unless a rule blocks it)
        checks.append(
            "Reachability h2->h4: "
            + ("OK" if self._ping("h2", "10.0.2.12") else "FAILED")
        )

        if "icmp" in req and "h1" in req and "h3" in req and "block" in req:
            checks.append(
                "Policy check h1->h3 ICMP blocked: "
                + ("OK" if not self._ping("h1", "10.0.2.11") else "FAILED")
            )

        if "telnet" in req:
            if self._forward_drop_present("r1", 23):
                checks.append("Policy check telnet forward block: OK")
            else:
                checks.append("Policy check telnet forward block: FAILED")

        if "ssh" in req:
            if self._forward_drop_present("r1", 22):
                checks.append("Policy check SSH forward block: OK")
            else:
                checks.append("Policy check SSH forward block: FAILED")

        if "smb" in req or "445" in req:
            if self._forward_drop_present("r1", 445):
                checks.append("Policy check SMB forward block: OK")
            else:
                checks.append("Policy check SMB forward block: FAILED")

        if "dns" in req:
            if self._forward_drop_present("r1", 53):
                checks.append("Policy check DNS egress restriction: OK")
            else:
                checks.append("Policy check DNS egress restriction: FAILED")

        if "allowlist" in req and "ssh" in req:
            rules = self._router_cmd("r1", "iptables -S INPUT")
            has_allow = "-s 10.0.1.11" in rules and "--dport 22" in rules and "-j ACCEPT" in rules
            has_deny = "--dport 22" in rules and "-j DROP" in rules
            if has_allow and has_deny:
                checks.append("Policy check router SSH allowlist from h1: OK")
            else:
                checks.append("Policy check router SSH allowlist from h1: FAILED")

        return checks

    def implement(self, request: str) -> Dict[str, object]:
        req = request.lower()
        changes: List[str] = []
        actions: List[str] = []

        if any(token in req for token in ("harden", "hardening", "baseline")):
            actions.append("baseline_hardening")
            changes.extend(self._apply_baseline_hardening())

        if "telnet" in req and any(token in req for token in ("disable", "block")):
            actions.append("disable_telnet")
            changes.extend(self._apply_disable_telnet())

        if "ssh" in req and any(token in req for token in ("disable", "block", "deny")):
            actions.append("block_ssh_forwarding")
            changes.extend(self._apply_block_ssh_forwarding())

        if "smb" in req or "445" in req:
            if any(token in req for token in ("disable", "block", "deny", "restrict")):
                actions.append("block_smb_forwarding")
                changes.extend(self._apply_block_smb_forwarding())

        if "dns" in req and any(token in req for token in ("restrict", "block", "deny")):
            actions.append("restrict_dns_egress")
            changes.extend(self._apply_dns_egress_restriction())

        if "allowlist" in req and "ssh" in req:
            actions.append("router_ssh_allowlist_h1")
            changes.extend(self._apply_router_ssh_allowlist_h1())

        if (
            "icmp" in req
            and "h1" in req
            and "h3" in req
            and any(token in req for token in ("block", "deny", "disable"))
        ):
            actions.append("block_icmp_h1_h3")
            changes.extend(self._apply_block_icmp_h1_h3())

        if not actions:
            actions.append("baseline_hardening_default")
            changes.extend(self._apply_baseline_hardening())

        verification = self._verify(request)
        record = {
            "timestamp": int(time.time()),
            "request": request,
            "actions": actions,
            "changes": changes,
            "verification": verification,
        }
        self.applied_changes.append(record)
        return {
            "implemented": True,
            "actions": actions,
            "changes": changes,
            "verification": verification,
            "applied_count": len(self.applied_changes),
        }

    def topology(self) -> Dict[str, object]:
        links: List[Tuple[str, str]] = []
        for link in self.net.links:
            links.append((link.intf1.node.name, link.intf2.node.name))
        host_ips = {
            name: self.net[name].IP()
            for name in ("h1", "h2", "h3", "h4")
        }
        router_routes = {
            name: self._router_cmd(name, "ip route show")
            for name in ("r1", "r2")
        }
        router_forward_rules = {
            name: self._router_cmd(name, "iptables -S FORWARD")
            for name in ("r1", "r2")
        }
        router_input_rules = {
            name: self._router_cmd(name, "iptables -S INPUT")
            for name in ("r1", "r2")
        }
        return {
            "nodes": sorted(self.net.nameToNode.keys()),
            "links": links,
            "host_ips": host_ips,
            "router_routes": router_routes,
            "router_forward_rules": router_forward_rules,
            "router_input_rules": router_input_rules,
            "applied_changes": len(self.applied_changes),
            "recent_changes": self.applied_changes[-3:],
        }


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_handler(controller: MininetController, ready: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: Dict[str, object]):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                if ready.is_set():
                    self._send_json(200, {"status": "ok"})
                else:
                    self._send_json(503, {"status": "starting"})
                return
            if self.path == "/topology":
                if not ready.is_set():
                    self._send_json(503, {"status": "starting"})
                    return
                self._send_json(200, controller.topology())
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/implement":
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8"))
                request_text = str(payload.get("request", "")).strip()
                if not request_text:
                    self._send_json(400, {"error": "request is required"})
                    return
                result = controller.implement(request_text)
                self._send_json(200, result)
            except Exception as exc:  # pragma: no cover
                LOGGER.exception("Error handling implement request")
                self._send_json(500, {"error": str(exc)})

        def log_message(self, fmt: str, *args):
            LOGGER.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def main():
    setLogLevel("warning")
    controller = MininetController()
    ready = threading.Event()
    stop_event = threading.Event()

    def _shutdown(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start HTTP server before Mininet so readiness probes can reach it immediately.
    # /health returns 503 until ready.set() is called after Mininet finishes starting.
    server = ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(controller, ready))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Mininet API server listening on 0.0.0.0:8080 (topology starting...)")

    controller.start()
    ready.set()
    LOGGER.info("Mininet topology ready")

    while not stop_event.is_set():
        time.sleep(0.5)

    server.shutdown()
    server.server_close()
    controller.stop()


if __name__ == "__main__":
    main()
