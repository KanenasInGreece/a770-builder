#!/usr/bin/env bash
# Sourced, never executed: the caller defines die, and env.sh has already
# defaulted the compose file to the Vulkan yaml. This replaces that file for
# the named backend and refuses an unknown name, so the Vulkan file cannot stay.
select_serve_envelope() {
  case "$1" in
    vulkan|sycl) A770B_COMPOSE_FILE="$A770B_PROJECT/compose/a770-$1.yaml" ;;
    vllm) A770B_COMPOSE_FILE="$A770B_PROJECT/compose/vllm.yaml" ;;
    sglang) A770B_COMPOSE_FILE="$A770B_PROJECT/compose/b70-sglang.yaml" ;;
    *) die "backend '$1' is not one this harness serves (vulkan, sycl, vllm, sglang)" ;;
  esac
  A770B_COMPOSE_ENV_FILE="${A770B_COMPOSE_FILE%.yaml}.env"
  A770B_SERVED_BACKEND="$1"
  export A770B_COMPOSE_FILE A770B_COMPOSE_ENV_FILE A770B_SERVED_BACKEND
}
