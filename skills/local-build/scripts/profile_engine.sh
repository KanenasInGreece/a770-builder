#!/usr/bin/env bash
# _profile_engine <profile> — stdout is the engine this skill will serve for that row.
# The caller defines a770b_profile_var and die. An engine this skill does not serve, or a
# backend that contradicts the named engine, is refused. An omitted engine follows the backend.
_profile_engine(){
  local name="$1" engine backend
  engine=$(a770b_profile_var "$name" ENGINE)
  backend=$(a770b_profile_var "$name" BACKEND)
  case "$engine" in
    "")
      case "$backend" in
        ""|vulkan|sycl) printf '%s\n' "llama.cpp"; return 0;;
        vllm) printf '%s\n' "vllm"; return 0;;
        *) die "backend '$backend' is not one this harness serves (vulkan, sycl, vllm)" || return $?;;
      esac
      ;;
    llama.cpp)
      case "$backend" in
        ""|vulkan|sycl) printf '%s\n' "llama.cpp"; return 0;;
        *) die "profile $name engine llama.cpp does not match backend $backend" || return $?;;
      esac
      ;;
    vllm)
      case "$backend" in
        vllm) printf '%s\n' "vllm"; return 0;;
        *) die "profile $name engine vllm does not match backend $backend" || return $?;;
      esac
      ;;
    *)
      die "profile $name engine '$engine' is not one this skill serves (llama.cpp, vllm)" || return $?
      ;;
  esac
}
