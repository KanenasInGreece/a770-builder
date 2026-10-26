# Replace TLS trust material that still contains the compromised emSign root.
# The server binary stays the one in BASE. A virtualenv gets pip 26.2.2 and
# certifi 2026.10.23, checked by hash, so a bundle fetched through the old
# trust store cannot be swapped. A Debian pip that cannot be uninstalled
# keeps its package and receives that same bundle in place of the compromised
# file. Every file named uv is replaced with the uv 0.13.5 binary, and every
# file named uvx with the uvx binary, both copied by digest. The build
# refuses to finish while emSign ECC Root CA - G3, or a certificate whose
# SHA-256 is one of the compromised intermediates, remains in a bundle,
# in a uv or uvx binary, or in the system store. Hash-named trust files
# (.0, .1, .2, and so on) are read the same way, including when the name
# is a symlink, and so are DER files. A planted fingerprint file is not
# the list. Java cacerts, NSS databases, shared libraries, Rust rlibs,
# and webpki-roots sources are text-scanned. A shared library is not
# replaced with a certificate. openai_harmony, outlines_core, and the
# SGLang extensions _server and _multimodal are left in place. update-ca-certificates drops a certificate
# only when its conf line starts with !. The line is marked, the store is
# rebuilt, then the certificate file and the line are removed. The certifi
# bundle is not copied onto the system store, and a symlink is not followed
# while another bundle is replaced. A directory that cannot be listed, or
# a bundle that cannot be read, fails the build.
ARG BASE
FROM ${BASE}
USER root
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /tmp/uv-0.13.5
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uvx /tmp/uvx-0.13.5
COPY compose/ca-trust-check.sh /tmp/ca-trust-check.sh
RUN set -eu; \
    . /tmp/ca-trust-check.sh; \
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
        if [ -n "$pem" ] && [ -f "$pem" ]; then \
          if bundle_has_root "$pem"; then \
            ca_strip_refused "$pem" /tmp/ca-clean.pem || { echo "could not read $pem" >&2; exit 1; }; \
            if bundle_has_root /tmp/ca-clean.pem; then echo "the patched bundle still has a compromised certificate" >&2; exit 1; \
            else rc=$?; if [ "$rc" -eq 2 ]; then echo "could not read the patched bundle" >&2; exit 1; fi; fi; \
            clean=/tmp/ca-clean.pem; break; \
          else rc=$?; if [ "$rc" -eq 1 ]; then clean=$pem; break; fi; fi; \
        fi; \
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
      if bundle_has_root /etc/ssl/certs/ca-certificates.crt; then \
        ca_clear_bundle /etc/ssl/certs/ca-certificates.crt "$clean" || { echo "could not read the system bundle" >&2; exit 1; }; \
      else rc=$?; if [ "$rc" -eq 2 ]; then echo "could not read the system bundle" >&2; exit 1; fi; fi; \
      if bundle_has_root /etc/ssl/certs/ca-certificates.crt; then echo "the system bundle still trusts a compromised emSign certificate" >&2; exit 1; \
      else rc=$?; if [ "$rc" -eq 2 ]; then echo "could not read the system bundle" >&2; exit 1; fi; fi; \
    fi; \
    : > /tmp/ca-pems; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      ca_append_trust_files /tmp/ca-pems "$dir" || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r bundle; do \
      [ -n "$bundle" ] || continue; \
      if [ "$bundle" = "$clean" ]; then continue; fi; \
      ca_dispose_bundle "$bundle" "$clean" || { echo "could not read $bundle" >&2; exit 1; }; \
    done < /tmp/ca-pems; \
    : > /tmp/ca-bundles; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      ca_append_trust_files /tmp/ca-bundles "$dir" || { echo "could not list $dir" >&2; exit 1; }; \
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
    if [ "$bad" = 1 ]; then echo "a compromised emSign certificate is still in a trust bundle" >&2; exit 1; fi; \
    : > /tmp/ca-embedded; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      ca_append_embedded_files /tmp/ca-embedded "$dir" || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r carrier; do \
      [ -n "$carrier" ] || continue; \
      if [ -f "$carrier" ]; then \
        ca_clear_embedded "$carrier" || { echo "could not read $carrier" >&2; exit 1; }; \
      fi; \
    done < /tmp/ca-embedded; \
    : > /tmp/ca-embedded-left; \
    for dir in /*; do \
      base=$(basename "$dir"); \
      case "$base" in proc|sys|dev|run) continue;; esac; \
      if [ ! -d "$dir" ]; then continue; fi; \
      ca_append_embedded_files /tmp/ca-embedded-left "$dir" || { echo "could not list $dir" >&2; exit 1; }; \
    done; \
    while read -r carrier; do \
      [ -n "$carrier" ] || continue; \
      if [ ! -f "$carrier" ]; then continue; fi; \
      if ca_embedded_refused "$carrier"; then \
        echo "$carrier" >&2; bad=1; \
      else \
        rc=$?; \
        if [ "$rc" -ne 1 ]; then echo "could not read $carrier" >&2; exit 1; fi; \
      fi; \
    done < /tmp/ca-embedded-left; \
    if [ "$bad" = 1 ]; then echo "a bundled trust store still trusts emSign ECC Root CA - G3" >&2; exit 1; fi; \
    rm -f /tmp/ca-pems /tmp/ca-bundles /tmp/ca-embedded /tmp/ca-embedded-left /tmp/ca-fingerprints /tmp/ca-clean.pem; \
    if [ -n "${CA_OWN_DIR:-}" ]; then rm -rf "$CA_OWN_DIR"; fi
