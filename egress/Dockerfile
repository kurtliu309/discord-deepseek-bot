# Egress allowlist proxy for the bot. Config + allowlist are baked in (no mounts).
# Base: Canonical's Squid image (Ubuntu 24.04), pinned by digest.
FROM ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c

COPY squid.conf /etc/squid/squid.conf
COPY allowlist.txt /etc/squid/allowlist.txt
RUN chmod 0644 /etc/squid/squid.conf /etc/squid/allowlist.txt \
 && /usr/sbin/squid -k parse -f /etc/squid/squid.conf

# uid/gid 13 = "proxy" in this image.
USER 13:13
EXPOSE 3128
ENTRYPOINT ["/usr/sbin/squid", "-f", "/etc/squid/squid.conf", "-N"]
