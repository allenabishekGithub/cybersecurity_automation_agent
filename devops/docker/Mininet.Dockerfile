FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        iproute2 \
        iptables \
        net-tools \
        curl \
        bridge-utils \
        mininet \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN chmod +x /app/network_sim/mininet_network_api.py

EXPOSE 8080

CMD ["/bin/bash", "-lc", "mn -c 2>/dev/null || true && python3 /app/network_sim/mininet_network_api.py"]
