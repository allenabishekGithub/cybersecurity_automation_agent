#!/usr/bin/env python3
"""
Generate lightweight PDF reference documents for RAG uploads.
"""

from __future__ import annotations

from pathlib import Path
from typing import List


def _escape_pdf_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _chunks(lines: List[str], page_size: int) -> List[List[str]]:
    return [lines[i:i + page_size] for i in range(0, len(lines), page_size)]


def write_simple_pdf(path: Path, title: str, lines: List[str], lines_per_page: int = 40) -> None:
    pages = _chunks(lines, lines_per_page)
    objects: List[bytes] = []

    # 1: catalog, 2: pages
    # 3..N: page objects
    # ...: content stream objects
    # ...: single font object

    num_pages = len(pages)
    first_page_obj = 3
    first_content_obj = first_page_obj + num_pages
    font_obj = first_content_obj + num_pages

    # Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    # Pages (kids are page objects 3..)
    kids = " ".join(f"{first_page_obj + i} 0 R" for i in range(num_pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>".encode("utf-8"))

    # Build content streams and page objects
    content_streams: List[bytes] = []
    for page_idx, page_lines in enumerate(pages):
        header = f"{title} (Page {page_idx + 1}/{num_pages})"
        draw_lines = [header, "-" * min(len(header), 90), *page_lines]

        stream_lines = [
            "BT",
            "/F1 11 Tf",
            "50 790 Td",
        ]
        for i, line in enumerate(draw_lines):
            escaped = _escape_pdf_text(line)
            if i == 0:
                stream_lines.append(f"({escaped}) Tj")
            else:
                stream_lines.append("0 -16 Td")
                stream_lines.append(f"({escaped}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("utf-8")
        content_streams.append(stream)

    # Page objects
    for page_idx in range(num_pages):
        content_obj = first_content_obj + page_idx
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
            f"/Contents {content_obj} 0 R >>"
        )
        objects.append(page_obj.encode("utf-8"))

    # Content stream objects
    for stream in content_streams:
        stream_obj = (
            b"<< /Length " + str(len(stream)).encode("utf-8") + b" >>\n"
            b"stream\n" + stream + b"\nendstream"
        )
        objects.append(stream_obj)

    # Font object
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Serialize PDF
    out = bytearray()
    out.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]

    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("utf-8"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("utf-8"))

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    out.extend(trailer.encode("utf-8"))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def main() -> None:
    docs_dir = Path(__file__).resolve().parent

    topology_lines = [
        "Topology Brief: Mininet Routers + Switches + Hosts",
        "",
        "Node Roles",
        "  r1, r2 : Linux routers (IP forwarding enabled)",
        "  s1, s2, s3 : L2 Open vSwitch switches",
        "  h1, h2 : User/client subnet",
        "  h3, h4 : Server subnet",
        "",
        "Links",
        "  h1--s1, h2--s1, r1--s1",
        "  r1--s2--r2 (transit segment)",
        "  r2--s3, h3--s3, h4--s3",
        "",
        "IP Plan",
        "  LAN A:     10.0.1.0/24 (gateway r1=10.0.1.1)",
        "  Transit:   10.0.12.0/30 (r1=10.0.12.1, r2=10.0.12.2)",
        "  LAN B:     10.0.2.0/24 (gateway r2=10.0.2.1)",
        "",
        "Routing",
        "  r1 static route: 10.0.2.0/24 via 10.0.12.2",
        "  r2 static route: 10.0.1.0/24 via 10.0.12.1",
        "",
        "Security-Relevant Control Points",
        "  1) Router FORWARD chain: east-west policy enforcement",
        "  2) Router INPUT chain: control-plane protection",
        "  3) Sysctl hardening knobs on routers",
        "",
        "Implementation Actions Supported by Simulator API",
        "  - baseline hardening (sysctl + invalid-state drops)",
        "  - disable/block telnet forwarding (tcp/23)",
        "  - block ICMP from h1 to h3",
        "",
        "Verification Signals",
        "  - Reachability probe h2->h4 for baseline health",
        "  - Policy probe h1->h3 ICMP expected fail when blocked",
        "  - iptables rule presence checks",
    ]

    hardening_lines = [
        "Network Hardening Best Practices (Reference for RAG)",
        "",
        "1. Segmentation and Least Privilege",
        "  - Separate user, management, and service planes.",
        "  - Permit only required east-west and north-south flows.",
        "  - Apply deny-by-default in forwarding policy.",
        "",
        "2. Router/Switch Control Plane Protection",
        "  - Disable unnecessary services (for example telnet).",
        "  - Restrict management protocols to trusted sources.",
        "  - Drop invalid conntrack state packets.",
        "",
        "3. Baseline Kernel/Network Stack Hardening",
        "  - Disable ICMP redirects handling where unnecessary.",
        "  - Disable sending redirects from routers.",
        "  - Ignore broadcast ICMP echo requests.",
        "",
        "4. Monitoring and Verification",
        "  - Verify policy outcomes with active probes (ping/tcp tests).",
        "  - Collect change records and implementation timestamps.",
        "  - Confirm both intended connectivity and intended blocks.",
        "",
        "5. Secure Change Management",
        "  - Make changes idempotent (safe to re-apply).",
        "  - Store exact command/action logs for each request.",
        "  - Provide explicit success/failure confirmation.",
        "",
        "6. Common Immediate Controls",
        "  - Block plaintext protocols (telnet, legacy cleartext mgmt).",
        "  - Limit ICMP where abuse is a concern, preserving diagnostics.",
        "  - Enforce source/destination based forwarding filters.",
        "",
        "7. Operational Notes",
        "  - Test in emulated topologies before production rollout.",
        "  - Keep an emergency rollback path for policy changes.",
        "  - Reassess hardening controls after topology changes.",
    ]

    write_simple_pdf(docs_dir / "topology_reference.pdf", "Topology Reference", topology_lines)
    write_simple_pdf(
        docs_dir / "network_hardening_practices.pdf",
        "Network Hardening Practices",
        hardening_lines,
    )


if __name__ == "__main__":
    main()
