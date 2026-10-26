#!/bin/sh
# Refuse the October 2026 eMudhra certificates.
# A hit is the root name emSign ECC Root CA - G3, or the SHA-256 of a
# certificate in the list below. The list is the compromised root, the two
# copies of other roots that it cross-signed, and the sixteen compromised
# intermediates. AAA Certificate Services is not on this list. Neither are
# emSign Root CA - G1, emSign Root CA - C1, or emSign ECC Root CA - C3.
# The nineteen SHA-256 values are the ones published in Mozilla Bugzilla
# 2083449 (https://bugzilla.mozilla.org/show_bug.cgi?id=2083449), the bug
# description and comment 2, and republished from that bug by Tanium
# (https://www.tanium.com/blog/emudhra-ca-compromise-find-and-fix-your-exposure-with-tanium).
# GHSA-6q48-6c7w-9gw2 is the certifi advisory that removes the root.
# OpenSSL's trust directory also reads <hash>.1, <hash>.2, and so on,
# including when that name is a symlink. A PEM header that cannot be parsed
# is a failure. A file that is not a certificate is not.
# The fingerprint file is written into a directory this script creates.
# A file that already exists is not the list.

unset CA_WORK CA_FINGERPRINT_FILE CA_PRIVATE_DIR CA_OWN_DIR CA_DIR_READY

ca_private_dir() {
  if [ "${CA_DIR_READY:-}" = 1 ] && [ -n "${CA_OWN_DIR:-}" ] && [ -d "$CA_OWN_DIR" ] && [ ! -L "$CA_OWN_DIR" ]; then
    CA_WORK=$CA_OWN_DIR
    return 0
  fi
  CA_OWN_DIR=$(mktemp -d /tmp/ca-trust.XXXXXX) || return 2
  if [ -L "$CA_OWN_DIR" ] || [ ! -d "$CA_OWN_DIR" ]; then
    return 2
  fi
  CA_DIR_READY=1
  CA_WORK=$CA_OWN_DIR
}

ca_load_fingerprints() {
  ca_private_dir || return 2
  CA_FINGERPRINT_FILE=$CA_OWN_DIR/fingerprints
  if [ -L "$CA_FINGERPRINT_FILE" ]; then
    return 2
  fi
  cat > "$CA_FINGERPRINT_FILE" <<'EOF' || return 2
86A1ECBA089C4A8D3BBE2734C612BA341D813E043CF9E8A862CD5C57A36BBE6B
3094025792039A68AF6E553686410BDAB7C0D4A36B9084CBA64202E35D6EBBDD
7137FA71AD46BA686F746D1014AC57E8E6C6E9AF4AA2F73C0C44A5CF27DEBB59
42C4ACCD12C4F59597925E1A08A7A90102608543809308D50EC427E118BA1493
0164D7CEAE5BB909C272AF151FF44B1177AE9B47D9424B94F1D9558C30934D19
E7568FE6E12331F4FD7DE61DA4C7559D0F8EB192A218D4741A3BA75184333838
432EC25F0D28DE9006D97BF752F17BBB2CD41F669933FCB072D9A1148D0261F3
98B896625C0C230ADEEC3EB2CF6AAD5001ECF9156A68644E82374CE5833BC25B
CB6E15165AC802E9B3F993F16C25D41F4E81C21EBDE71AE1B012AC00BF9C7C3F
9E39DE2881273ACC225A149C72BA3E0F51424963669AB633EE1DC7DF5DB067B4
4B2460A466F95FD40113BCDCCA2CEB29B3B093921DE45474DA7DF48874C630B1
45A883F349AEDFD5F3A649B5498FFB121712BA8B448F3F2A91DBC5A766FCDDBE
62B7F9868263F72F6911FC78CEAF72C1979158B7368BF648683BB2AA39B02E72
579480AEA166290BD3C643589E795FC0D0E54E86505D180F1DFF99EFD3199512
FD9D5C2DBBD4995AB69B8D50C439D9527B00529199C2FD01B2174EB58F07021F
5F1DED4DD590AF74631290CF073B122A2AAC6A1FA479778A0BD43579BD832699
7485B51C0ED192878FA1D2F948FB529AE1842CC5086C4B67F7BE5B2039DE9489
A496E23AC0CAB8155FC9B117B97EAE368EFCBB9041AEC8804D5A308D2D01C2D8
84112972B8E59D14560AE7E7119654474AAD119990CF3722BFC1E3B91615E1FD
EOF
  if [ -L "$CA_FINGERPRINT_FILE" ] || [ ! -f "$CA_FINGERPRINT_FILE" ]; then
    return 2
  fi
  ca_lines=$(grep -c -E '^[0-9A-F]{64}$' "$CA_FINGERPRINT_FILE" || true)
  if [ "$ca_lines" -ne 19 ]; then
    return 2
  fi
  return 0
}

# 0 refused, 1 not refused, 2 unreadable.
ca_cert_refused() {
  ca_subj=$(openssl x509 -in "$1" -noout -subject -issuer 2>/dev/null) || return 2
  if printf '%s\n' "$ca_subj" | grep -q "emSign ECC Root CA - G3"; then
    return 0
  fi
  ca_fp=$(openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null) || return 2
  ca_hex=$(printf '%s\n' "$ca_fp" | sed -n 's/.*Fingerprint=//p' | tr -d ':' | tr 'a-f' 'A-F')
  if [ -z "$ca_hex" ]; then
    return 2
  fi
  ca_load_fingerprints || return 2
  if grep -q -i -x -F "$ca_hex" "$CA_FINGERPRINT_FILE"; then
    return 0
  fi
  return 1
}

# Split a PEM bundle into cert-1.pem, cert-2.pem, ... under $2.
ca_split_certs() {
  ca_in=$1
  ca_outdir=$2
  mkdir -p "$ca_outdir" || return 2
  if ! openssl crl2pkcs7 -nocrl -certfile "$ca_in" -out "$ca_outdir/bundle.p7" 2>/dev/null; then
    return 2
  fi
  if ! openssl pkcs7 -in "$ca_outdir/bundle.p7" -print_certs > "$ca_outdir/bundle.certs" 2>/dev/null; then
    return 2
  fi
  awk -v dir="$ca_outdir" '
    BEGIN { n = 0; on = 0 }
    /-----BEGIN / { n++; on = 1; path = sprintf("%s/cert-%d.pem", dir, n) }
    on { print > path }
    /-----END / { on = 0; close(path) }
  ' "$ca_outdir/bundle.certs" || return 2
  if [ ! -f "$ca_outdir/cert-1.pem" ]; then
    return 2
  fi
  return 0
}

# 0 the file trusts a refused certificate, 1 it does not, 2 it cannot be read.
# A dangling symlink is not a certificate. A symlink that can be read is.
bundle_has_root() {
  if [ -L "$1" ] && [ ! -e "$1" ]; then
    return 1
  fi
  if grep -q "emSign ECC Root CA - G3" "$1"; then
    return 0
  else
    ca_rc=$?
    if [ "$ca_rc" -ne 1 ]; then
      return 2
    fi
  fi
  if grep -E -q "BEGIN( (TRUSTED|X509))? CERTIFICATE" "$1"; then
    ca_private_dir || return 2
    ca_outdir=$CA_WORK/ca-parts-$$
    rm -rf "$ca_outdir"
    if ! ca_split_certs "$1" "$ca_outdir"; then
      rm -rf "$ca_outdir"
      return 2
    fi
    ca_n=1
    ca_hit=1
    while [ -f "$ca_outdir/cert-$ca_n.pem" ]; do
      if ca_cert_refused "$ca_outdir/cert-$ca_n.pem"; then
        ca_hit=0
        break
      else
        ca_rc=$?
        if [ "$ca_rc" -eq 2 ]; then
          ca_hit=2
          break
        fi
      fi
      ca_n=$((ca_n + 1))
    done
    rm -rf "$ca_outdir"
    return "$ca_hit"
  else
    ca_rc=$?
    if [ "$ca_rc" -ne 1 ]; then
      return 2
    fi
  fi
  ca_lead=$(od -An -N1 -t x1 "$1" 2>/dev/null | tr -d ' \n') || return 2
  if [ "$ca_lead" != "30" ]; then
    return 1
  fi
  ca_private_dir || return 2
  ca_der=$CA_WORK/ca-der-$$.pem
  if ! openssl x509 -inform DER -in "$1" -out "$ca_der" 2>/dev/null; then
    rm -f "$ca_der"
    return 1
  fi
  if ca_cert_refused "$ca_der"; then
    ca_rc=0
  else
    ca_rc=$?
  fi
  rm -f "$ca_der"
  return "$ca_rc"
}

# Write $2 as $1 without refused certificates. A refused DER file becomes empty.
ca_strip_refused() {
  ca_src=$1
  ca_dest=$2
  : > "$ca_dest" || return 2
  if grep -E -q "BEGIN( (TRUSTED|X509))? CERTIFICATE" "$ca_src"; then
    :
  else
    ca_rc=$?
    if [ "$ca_rc" -ne 1 ]; then
      return 2
    fi
    return 0
  fi
  ca_private_dir || return 2
  ca_outdir=$CA_WORK/ca-strip-$$
  rm -rf "$ca_outdir"
  if ! ca_split_certs "$ca_src" "$ca_outdir"; then
    rm -rf "$ca_outdir"
    return 2
  fi
  ca_n=1
  while [ -f "$ca_outdir/cert-$ca_n.pem" ]; do
    if ca_cert_refused "$ca_outdir/cert-$ca_n.pem"; then
      :
    else
      ca_rc=$?
      if [ "$ca_rc" -eq 2 ]; then
        rm -rf "$ca_outdir"
        return 2
      fi
      cat "$ca_outdir/cert-$ca_n.pem" >> "$ca_dest" || {
        rm -rf "$ca_outdir"
        return 2
      }
    fi
    ca_n=$((ca_n + 1))
  done
  rm -rf "$ca_outdir"
  return 0
}

# A system-store file is rewritten in place. Any other bundle is replaced
# with the clean bundle. The certifi bundle is not copied onto the system store.
ca_clear_bundle() {
  ca_file=$1
  ca_clean=$2
  ca_private_dir || return 2
  case "$ca_file" in
    /etc/ssl/certs/*|/usr/share/ca-certificates/*)
      ca_strip_refused "$ca_file" "$CA_WORK/ca-stripped" || return 2
      cat "$CA_WORK/ca-stripped" > "$ca_file" || return 2
      rm -f "$CA_WORK/ca-stripped"
      ;;
    *)
      cp "$ca_clean" "$ca_file" || return 2
      ;;
  esac
}

# 0 the file contains a compromised name, 1 it does not, 2 it cannot be read.
ca_text_has_compromised() {
  if grep -a -F -q \
      -e "emSign ECC Root CA - G3" \
      -e "EM DV TLS CA - G3" \
      -e "EM EV TLS CA - G3" \
      -e "EM OV TLS CA - G3" \
      -e "EM OV CS CA - G3" "$1"; then
    return 0
  else
    ca_rc=$?
    if [ "$ca_rc" -ne 1 ]; then
      return 2
    fi
    return 1
  fi
}

# library: do not overwrite. keystore: try to drop one alias, then remove.
# removable: a build cache or an NSS database, removed when it still matches.
ca_carrier_kind() {
  case "$1" in
    *.so|*.so.*)
      case "$1" in
        */rust/target/*)
          printf '%s\n' removable
          ;;
        *)
          printf '%s\n' library
          ;;
      esac
      return 0
      ;;
    *.rlib)
      printf '%s\n' removable
      return 0
      ;;
  esac
  ca_base=$(basename "$1")
  case "$ca_base" in
    uv|uvx)
      printf '%s\n' library
      ;;
    cacerts|jssecacerts)
      printf '%s\n' keystore
      ;;
    *)
      printf '%s\n' removable
      ;;
  esac
}

# These four libraries embed webpki-roots 1.0.9. No rebuilt wheel is
# published, and the vLLM image has no Rust compiler. They are left in
# place. Any other shared library that contains a compromised name fails.
ca_residual_library() {
  case "$1" in
    */openai_harmony*.so|*/openai_harmony*.so.*)
      return 0
      ;;
    */outlines_core*.so|*/outlines_core*.so.*)
      return 0
      ;;
    */rust_extensions/_server*.so|*/rust_extensions/_server*.so.*)
      return 0
      ;;
    */rust_extensions/_multimodal*.so|*/rust_extensions/_multimodal*.so.*)
      return 0
      ;;
  esac
  return 1
}

# 0 the embedded file must be refused, 1 it need not, 2 it cannot be read.
ca_embedded_refused() {
  if ca_text_has_compromised "$1"; then
    if ca_residual_library "$1"; then
      return 1
    fi
    return 0
  else
    ca_rc=$?
    if [ "$ca_rc" -eq 2 ]; then
      return 2
    fi
    return 1
  fi
}

# 0 clean or removed, 2 unreadable or a library that still contains a name.
# A shared library is not replaced with a certificate. AAA Certificate
# Services is not an alias this function deletes.
ca_clear_embedded() {
  if ca_text_has_compromised "$1"; then
    :
  else
    ca_rc=$?
    if [ "$ca_rc" -eq 2 ]; then
      return 2
    fi
    return 0
  fi
  ca_kind=$(ca_carrier_kind "$1")
  case "$ca_kind" in
    library)
      if ca_residual_library "$1"; then
        return 0
      fi
      return 2
      ;;
    keystore)
      if command -v keytool >/dev/null 2>&1; then
        keytool -delete -keystore "$1" -storepass changeit \
          -alias "emsigneccrootcag3 [jdk]" >/dev/null 2>&1 || true
      fi
      if ca_text_has_compromised "$1"; then
        rm -f "$1" || return 2
      else
        ca_rc=$?
        if [ "$ca_rc" -eq 2 ]; then
          return 2
        fi
      fi
      ;;
    removable)
      rm -f "$1" || return 2
      ;;
  esac
  return 0
}

# A hash symlink is removed. Its target is rewritten only when it is a
# certificate file. A shared library is not overwritten.
ca_dispose_bundle() {
  ca_file=$1
  ca_clean=$2
  if [ -L "$ca_file" ]; then
    if [ ! -e "$ca_file" ]; then
      rm -f "$ca_file" || return 2
      return 0
    fi
    ca_target=$(readlink -f "$ca_file") || return 2
    case "$ca_target" in
      *.so|*.so.*|*.rlib|*webpki-roots*)
        ca_leave=0
        ;;
      *)
        ca_base=$(basename "$ca_target")
        case "$ca_base" in
          uv|uvx)
            ca_leave=0
            ;;
          *)
            ca_leave=1
            ;;
        esac
        ;;
    esac
    if [ "$ca_leave" -eq 0 ]; then
      if ca_text_has_compromised "$ca_target"; then
        ca_hit=0
      else
        ca_rc=$?
        if [ "$ca_rc" -eq 2 ]; then
          return 2
        fi
        ca_hit=1
      fi
      rm -f "$ca_file" || return 2
      if [ "$ca_hit" -eq 0 ]; then
        case "$ca_target" in
          *.so|*.so.*)
            if ca_residual_library "$ca_target"; then
              return 0
            fi
            return 2
            ;;
        esac
        ca_base=$(basename "$ca_target")
        case "$ca_base" in
          uv|uvx)
            return 2
            ;;
        esac
      fi
      return 0
    fi
    if bundle_has_root "$ca_file"; then
      rm -f "$ca_file" || return 2
      if [ -f "$ca_target" ] && [ ! -L "$ca_target" ]; then
        ca_clear_bundle "$ca_target" "$ca_clean" || return 2
      fi
    else
      ca_rc=$?
      if [ "$ca_rc" -eq 2 ]; then
        return 2
      fi
    fi
    return 0
  fi
  if bundle_has_root "$ca_file"; then
    ca_clear_bundle "$ca_file" "$ca_clean" || return 2
  else
    ca_rc=$?
    if [ "$ca_rc" -eq 2 ]; then
      return 2
    fi
  fi
  return 0
}

# Certificate files OpenSSL will read, and hash symlinks that name one.
# A trust-directory file is the 8-character subject hash, then .0, .1, .2, ...
# A library name such as libfoo.so.1 is not that file.
ca_append_trust_files() {
  ca_dest=$1
  ca_dir=$2
  find "$ca_dir" \( -type f -o -type l \) \( \
      -name '*.pem' -o -name '*.crt' -o -name '*.cer' -o -name '*.der' \
      -o -name 'ca-certificates.crt' \
      -o -regex '.*/[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]\.[0-9][0-9]*$' \
    \) >> "$ca_dest"
}

# Copies of a trust list that are not certificate files: uv, Java, NSS,
# shared libraries, Rust rlibs, and webpki-roots sources. These are text
# scans. They are not parsed as certificates.
ca_append_embedded_files() {
  ca_dest=$1
  ca_dir=$2
  find "$ca_dir" -type f \( \
      -name uv -o -name uvx \
      -o -name cacerts -o -name jssecacerts \
      -o -name cert8.db -o -name cert9.db \
      -o -name 'libnssckbi.so' -o -name 'libnssckbi.so.*' \
      -o -name '*.so' -o -name '*.so.*' \
      -o -name '*.rlib' \
      -o -path '*webpki-roots*' \
      -o -path '*/hf_xet/*' \
    \) >> "$ca_dest"
}
