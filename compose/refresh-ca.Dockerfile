# Replace TLS trust material that still contains the compromised emSign root.
# The server binary stays the one in BASE. A virtualenv gets pip 26.2.2 and
# certifi 2026.10.23. A Debian pip that cannot be uninstalled keeps its
# package and receives that same bundle in place of the compromised file.
# uv 0.13.5 replaces an older uv. Cached wheels are removed. The build
# refuses to finish while emSign ECC Root CA - G3 remains in a cacert.pem
# or in the uv binary.
ARG BASE
FROM ${BASE}
USER root
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /tmp/uv-0.13.5
RUN set -eu; \
    if command -v uv >/dev/null 2>&1; then cp /tmp/uv-0.13.5 "$(command -v uv)"; fi; \
    rm -f /tmp/uv-0.13.5; \
    rm -rf /root/.cache/uv /root/.cache/pip; \
    for py in /opt/venv/bin/python3 /root/.venv/bin/python3; do \
      if [ -x "$py" ] && "$py" -c 'import pip' >/dev/null 2>&1; then \
        "$py" -m pip install --upgrade --disable-pip-version-check 'pip==26.2.2' 'certifi==2026.10.23'; \
      fi; \
    done; \
    clean=""; \
    for py in /opt/venv/bin/python3 /root/.venv/bin/python3; do \
      if [ -x "$py" ]; then \
        pem=$("$py" -c 'import certifi; print(certifi.where())' 2>/dev/null || true); \
        if [ -n "$pem" ] && [ -f "$pem" ] && ! grep -q "emSign ECC Root CA - G3" "$pem"; then clean=$pem; break; fi; \
      fi; \
    done; \
    if [ -z "$clean" ]; then echo "no patched certifi bundle was found" >&2; exit 1; fi; \
    find /opt /usr /root /etc -name cacert.pem 2>/dev/null | while read -r bundle; do \
      if [ "$bundle" != "$clean" ] && grep -q "emSign ECC Root CA - G3" "$bundle"; then cp "$clean" "$bundle"; fi; \
    done; \
    if find /opt /usr /root /etc -name cacert.pem -exec grep -l "emSign ECC Root CA - G3" {} + >/tmp/ca-leftover; then \
      echo "emSign ECC Root CA - G3 is still in:" >&2; cat /tmp/ca-leftover >&2; exit 1; \
    fi; \
    if command -v uv >/dev/null 2>&1 && grep -a -q "emSign ECC Root CA - G3" "$(command -v uv)"; then \
      echo "uv still trusts emSign ECC Root CA - G3" >&2; exit 1; \
    fi
