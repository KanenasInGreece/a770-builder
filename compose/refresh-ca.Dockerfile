# Replace TLS trust material that still contains the compromised emSign root.
# The server binary stays the one in BASE. A virtualenv gets pip 26.2.2 and
# certifi 2026.10.23, checked by hash, so a bundle fetched through the old
# trust store cannot be swapped. A Debian pip that cannot be uninstalled
# keeps its package and receives that same bundle in place of the compromised
# file. Every file named uv is replaced with the uv 0.13.5 binary, and every
# file named uvx with the uvx binary, both copied by digest. The build
# refuses to finish while emSign ECC Root CA - G3 remains in a bundle,
# in a uv or uvx binary, or in the system store. update-ca-certificates
# drops a certificate only when its conf line starts with !. The line is
# marked, the store is rebuilt, then the certificate file and the line are
# removed. The certifi bundle is not copied onto the system store, and a
# symlink is not followed while another bundle is replaced. A directory
# that cannot be listed, or a bundle that cannot be read, fails the build.
ARG BASE
FROM ${BASE}
USER root
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /tmp/uv-0.13.5
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uvx /tmp/uvx-0.13.5
RUN set -eu; \
    bundle_has_root() { \
      if grep -q "emSign ECC Root CA - G3" "$1"; then return 0; \
      else rc=$?; if [ "$rc" -ne 1 ]; then return 2; fi; fi; \
      if openssl crl2pkcs7 -nocrl -certfile "$1" -out /tmp/ca-one.p7 2>/dev/null; then \
        if openssl pkcs7 -in /tmp/ca-one.p7 -print_certs -noout > /tmp/ca-one.subj 2>/dev/null; then \
          if grep -q "emSign ECC Root CA - G3" /tmp/ca-one.subj; then return 0; fi; \
        fi; \
      fi; \
      return 1; \
    }; \
    : > /tmp/ca-tools; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
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
    if [ -f /etc/ca-certificates.conf ]; then \
      sed 's|^mozilla/emSign_ECC_Root_CA_-_G3\.crt$|!mozilla/emSign_ECC_Root_CA_-_G3.crt|' /etc/ca-certificates.conf > /tmp/ca-conf; \
      cat /tmp/ca-conf > /etc/ca-certificates.conf; \
      rm -f /tmp/ca-conf; \
    fi; \
    if [ -x /usr/sbin/update-ca-certificates ]; then update-ca-certificates || { echo "could not rebuild the system trust store" >&2; exit 1; }; fi; \
    rm -f /usr/share/ca-certificates/mozilla/emSign_ECC_Root_CA_-_G3.crt /etc/ssl/certs/emSign_ECC_Root_CA_-_G3.pem; \
    if [ -d /etc/ssl/certs ]; then find /etc/ssl/certs -type l -lname '*emSign_ECC_Root_CA_-_G3*' -exec rm -f {} \;; fi; \
    if [ -f /etc/ca-certificates.conf ]; then \
      grep -v 'emSign_ECC_Root_CA_-_G3' /etc/ca-certificates.conf > /tmp/ca-conf || true; \
      cat /tmp/ca-conf > /etc/ca-certificates.conf; \
      rm -f /tmp/ca-conf; \
    fi; \
    if [ -x /usr/sbin/update-ca-certificates ]; then update-ca-certificates || { echo "could not rebuild the system trust store" >&2; exit 1; }; fi; \
    rm -f /usr/share/ca-certificates/mozilla/emSign_ECC_Root_CA_-_G3.crt /etc/ssl/certs/emSign_ECC_Root_CA_-_G3.pem; \
    if [ -d /etc/ssl/certs ]; then find /etc/ssl/certs -type l -lname '*emSign_ECC_Root_CA_-_G3*' -exec rm -f {} \;; fi; \
    left=$(find /etc/ssl/certs /usr/share/ca-certificates -name 'emSign_ECC_Root_CA_-_G3*' 2>/dev/null || true); \
    links=$(find /etc/ssl/certs -type l -lname '*emSign_ECC_Root_CA_-_G3*' 2>/dev/null || true); \
    if [ -n "$left" ] || [ -n "$links" ]; then \
      printf '%s\n' "$left" "$links" >&2; \
      echo "the system store still has emSign ECC Root CA - G3" >&2; exit 1; \
    fi; \
    if [ -f /etc/ca-certificates.conf ] && grep -q 'emSign_ECC_Root_CA_-_G3' /etc/ca-certificates.conf; then \
      echo "ca-certificates.conf still names emSign ECC Root CA - G3" >&2; exit 1; \
    fi; \
    if [ -f /etc/ssl/certs/ca-certificates.crt ]; then \
      if ! command -v openssl >/dev/null 2>&1; then echo "openssl is missing; cannot read the system bundle" >&2; exit 1; fi; \
      if ! openssl crl2pkcs7 -nocrl -certfile /etc/ssl/certs/ca-certificates.crt -out /tmp/ca-system.p7; then echo "could not read the system bundle" >&2; exit 1; fi; \
      if ! openssl pkcs7 -in /tmp/ca-system.p7 -print_certs -noout > /tmp/ca-system.subj; then echo "could not read the system bundle" >&2; exit 1; fi; \
      if grep -q "emSign ECC Root CA - G3" /tmp/ca-system.subj; then echo "the system bundle still trusts emSign ECC Root CA - G3" >&2; exit 1; \
      else rc=$?; if [ "$rc" -ne 1 ]; then echo "could not read the system bundle" >&2; exit 1; fi; fi; \
      rm -f /tmp/ca-system.p7 /tmp/ca-system.subj; \
    fi; \
    : > /tmp/ca-pems; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' \) >> /tmp/ca-pems || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r bundle; do \
      [ -n "$bundle" ] || continue; \
      if [ -L "$bundle" ]; then continue; fi; \
      if [ "$bundle" = "$clean" ]; then continue; fi; \
      if [ "$bundle" = /etc/ssl/certs/ca-certificates.crt ]; then continue; fi; \
      if bundle_has_root "$bundle"; then cp "$clean" "$bundle"; \
      else rc=$?; if [ "$rc" -eq 2 ]; then echo "could not read $bundle" >&2; exit 1; fi; fi; \
    done < /tmp/ca-pems; \
    : > /tmp/ca-bundles; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      find "$dir" -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' -o -name ca-certificates.crt \) >> /tmp/ca-bundles || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    bad=0; \
    while read -r bundle; do \
      [ -n "$bundle" ] || continue; \
      if bundle_has_root "$bundle"; then \
        echo "$bundle" >&2; bad=1; \
      else \
        rc=$?; \
        if [ "$rc" -eq 2 ]; then echo "could not read $bundle" >&2; exit 1; fi; \
      fi; \
    done < /tmp/ca-bundles; \
    if [ "$bad" = 1 ]; then echo "emSign ECC Root CA - G3 is still in a trust bundle" >&2; exit 1; fi; \
    : > /tmp/ca-uv; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
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
    rm -f /tmp/ca-pems /tmp/ca-bundles /tmp/ca-uv /tmp/ca-one.p7 /tmp/ca-one.subj
