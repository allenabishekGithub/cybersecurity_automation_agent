#!/usr/bin/env python3
"""
Simple Mininet topology with routers, switches, and end hosts.

Topology:
  LAN A: h1, h2 -- s1 -- r1
  Transit:      r1 -- s2 -- r2
  LAN B: h3, h4 -- s3 -- r2

Subnets:
  LAN A   : 10.0.1.0/24
  Transit : 10.0.12.0/30
  LAN B   : 10.0.2.0/24
"""

from __future__ import annotations

import argparse

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import Node, OVSKernelSwitch
from mininet.topo import Topo


class LinuxRouter(Node):
    """A Node with IPv4 forwarding enabled."""

    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")

    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        super().terminate()


class RouterSwitchTopo(Topo):
    """2 routers, 3 switches, 4 hosts."""

    def build(self):
        # Access switches (standalone L2 behavior without external controller).
        s1 = self.addSwitch("s1", failMode="standalone")
        s2 = self.addSwitch("s2", failMode="standalone")
        s3 = self.addSwitch("s3", failMode="standalone")

        # Routers.
        r1 = self.addNode("r1", cls=LinuxRouter)
        r2 = self.addNode("r2", cls=LinuxRouter)

        # End hosts.
        h1 = self.addHost("h1", ip="10.0.1.11/24", defaultRoute="via 10.0.1.1")
        h2 = self.addHost("h2", ip="10.0.1.12/24", defaultRoute="via 10.0.1.1")
        h3 = self.addHost("h3", ip="10.0.2.11/24", defaultRoute="via 10.0.2.1")
        h4 = self.addHost("h4", ip="10.0.2.12/24", defaultRoute="via 10.0.2.1")

        # LAN A
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(r1, s1, intfName1="r1-eth0", params1={"ip": "10.0.1.1/24"})

        # Transit
        self.addLink(r1, s2, intfName1="r1-eth1", params1={"ip": "10.0.12.1/30"})
        self.addLink(r2, s2, intfName1="r2-eth0", params1={"ip": "10.0.12.2/30"})

        # LAN B
        self.addLink(r2, s3, intfName1="r2-eth1", params1={"ip": "10.0.2.1/24"})
        self.addLink(h3, s3)
        self.addLink(h4, s3)


def configure_routes(net: Mininet) -> None:
    """Configure router static routes for cross-subnet reachability."""
    r1 = net["r1"]
    r2 = net["r2"]

    r1.cmd("ip route replace 10.0.2.0/24 via 10.0.12.2")
    r2.cmd("ip route replace 10.0.1.0/24 via 10.0.12.1")


def show_state(net: Mininet) -> None:
    """Print basic node and route information."""
    info("\n*** Router interface/route state\n")
    for name in ("r1", "r2"):
        node = net[name]
        info(f"\n{name} interfaces:\n")
        info(node.cmd("ip -4 -br addr show"))
        info(f"{name} routes:\n")
        info(node.cmd("ip route show"))

    info("\n*** Host default routes\n")
    for name in ("h1", "h2", "h3", "h4"):
        node = net[name]
        info(f"{name}: ")
        info(node.cmd("ip route show default"))


def run_smoke_tests(net: Mininet) -> None:
    """Run a couple of quick end-to-end pings."""
    info("\n*** Smoke tests\n")
    info("h1 -> h3\n")
    info(net["h1"].cmd("ping -c 2 10.0.2.11"))
    info("h2 -> h4\n")
    info(net["h2"].cmd("ping -c 2 10.0.2.12"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mininet topology: 2 routers, 3 switches, 4 hosts."
    )
    parser.add_argument(
        "--pingall",
        action="store_true",
        help="Run Mininet pingAll() after startup.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run two explicit cross-subnet ping checks.",
    )
    parser.add_argument(
        "--no-cli",
        action="store_true",
        help="Do not open Mininet CLI; run selected tests and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    topo = RouterSwitchTopo()
    net = Mininet(topo=topo, switch=OVSKernelSwitch, controller=None, autoSetMacs=True)

    info("*** Starting network\n")
    net.start()
    configure_routes(net)
    show_state(net)

    if args.pingall:
        info("\n*** pingAll\n")
        net.pingAll()

    if args.smoke_test:
        run_smoke_tests(net)

    if args.no_cli:
        info("\n*** Stopping network (no CLI mode)\n")
        net.stop()
        return

    info("\n*** Starting Mininet CLI (type 'exit' or Ctrl-D to stop)\n")
    CLI(net)
    info("\n*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    main()
