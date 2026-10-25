# Replace TLS trust material that still contains the compromised emSign root.
# The server binary stays the one in BASE. A virtualenv gets pip 26.2.2 and
# certifi 2026.10.23, checked by hash, so a bundle fetched through the old
# trust store cannot be swapped. A Debian pip that cannot be uninstalled
# keeps its package and receives that same bundle in place of the compromised
# file. Every file named uv is replaced with the uv 0.13.5 binary, and every
# file named uvx with the uvx binary, both copied by digest. The build
# refuses to finish while emSign ECC Root CA - G3 remains in a cacert.pem,
# in ca-certificates.crt, or in a uv or uvx binary, and it refuses if a
# directory cannot be listed or a bundle cannot be read. The system store
# is not overwritten: a copy of the root there fails the build.
ARG BASE
FROM ${BASE}
USER root
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /tmp/uv-0.13.5
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uvx /tmp/uvx-0.13.5
RUN set -eu; \
    : > /tmp/ca-tools; \
    for dir in /opt /usr /root /usr/local /home /app /workspace /srv /var /tmp; do \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type f \( -name uv -o -name uvx \) >> /tmp/ca-tools || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r bin; do \
      [ -n "$bin" ] || continue; \
      case "$bin" in \
        */uvx) cp /tmp/uvx-0.13.5 "$bin" ;; \
        *) cp /tmp/uv-0.13.5 "$bin" ;; \
      esac; \
    done < /tmp/ca-tools; \
    rm -f /tmp/uv-0.13.5 /tmp/uvx-0.13.5 /tmp/ca-tools; \
    : > /tmp/ca-caches; \
    for dir in /opt /usr /root /home /tmp /var /srv; do \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type d \( -path '*/.cache/uv' -o -path '*/.cache/pip' \) >> /tmp/ca-caches || { echo "could not list caches in $dir" >&2; exit 1; }; \
    done; \
    while read -r cache; do \
      [ -n "$cache" ] || continue; \
      case "$cache" in \
        */.cache/uv|*/.cache/pip) rm -rf "$cache" ;; \
        *) echo "refusing to remove $cache" >&2; exit 1 ;; \
      esac; \
    done < /tmp/ca-caches; \
    rm -f /tmp/ca-caches; \
    printf '%s\n' \
      'pip==26.2.2 --hash=sha256:fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70' \
      'certifi==2026.10.23 --hash=sha256:e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1' \
      > /tmp/ca-requirements.txt; \
    for py in /opt/venv/bin/python3 /root/.venv/bin/python3; do \
      if [ -x "$py" ] && "$py" -c 'import pip' >/dev/null 2>&1; then \
        "$py" -m pip install --upgrade --disable-pip-version-check --require-hashes -r /tmp/ca-requirements.txt; \
      fi; \
    done; \
    rm -f /tmp/ca-requirements.txt; \
    clean=""; \
    for py in /opt/venv/bin/python3 /root/.venv/bin/python3; do \
      if [ -x "$py" ]; then \
        pem=$("$py" -c 'import certifi; print(certifi.where())' 2>/dev/null || true); \
        if [ -n "$pem" ] && [ -f "$pem" ] && ! grep -q "emSign ECC Root CA - G3" "$pem"; then clean=$pem; break; fi; \
      fi; \
    done; \
    if [ -z "$clean" ]; then echo "no patched certifi bundle was found" >&2; exit 1; fi; \
    : > /tmp/ca-pems; \
    for dir in /opt /usr /root /etc /app /workspace /home /srv /var /tmp; do \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -name cacert.pem >> /tmp/ca-pems || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r bundle; do \
      [ -n "$bundle" ] || continue; \
      if [ "$bundle" != "$clean" ] && grep -q "emSign ECC Root CA - G3" "$bundle"; then cp "$clean" "$bundle"; fi; \
    done < /tmp/ca-pems; \
    : > /tmp/ca-bundles; \
    for dir in /opt /usr /root /etc /app /workspace /home /srv /var /tmp; do \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type f \( -name cacert.pem -o -name ca-certificates.crt \) >> /tmp/ca-bundles || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    bad=0; \
    while read -r bundle; do \
      [ -n "$bundle" ] || continue; \
      if grep -q "emSign ECC Root CA - G3" "$bundle"; then \
        echo "$bundle" >&2; bad=1; \
      else \
        rc=$?; \
        if [ "$rc" -ne 1 ]; then echo "could not read $bundle" >&2; exit 1; fi; \
      fi; \
    done < /tmp/ca-bundles; \
    if [ "$bad" = 1 ]; then echo "emSign ECC Root CA - G3 is still in a trust bundle" >&2; exit 1; fi; \
    : > /tmp/ca-uv; \
    for dir in /opt /usr /root /usr/local /home /app /workspace /srv /var /tmp; do \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type f \( -name uv -o -name uvx \) >> /tmp/ca-uv || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r bin; do \
      [ -n "$bin" ] || continue; \
      if grep -a -q "emSign ECC Root CA - G3" "$bin"; then \
        echo "$bin" >&2; bad=1; \
      else \
        rc=$?; \
        if [ "$rc" -ne 1 ]; then echo "could not read $bin" >&2; exit 1; fi; \
      fi; \
    done < /tmp/ca-uv; \
    if [ "$bad" = 1 ]; then echo "a uv binary still trusts emSign ECC Root CA - G3" >&2; exit 1; fi; \
    rm -f /tmp/ca-pems /tmp/ca-bundles /tmp/ca-uv
