#!/usr/bin/env zsh
# Optional zsh helper to support interactive `/sbx ...` command entry.
# Source this file once in your shell:
#   source /home/zenulabidin/Documents/stringbean/scripts/sbx-zsh-hook.zsh

if [[ -z "${__STRINGBEAN_ROOT__-}" ]]; then
  __STRINGBEAN_ROOT__="${${(%):-%N}:A:h:h}"
fi
if [[ ! -f "$__STRINGBEAN_ROOT__/scripts/sbx-zsh-hook.zsh" ]]; then
  __STRINGBEAN_ROOT__="$(cd "$(dirname "${(%):-%N}")" && cd .. && pwd)"
fi

sbx() {
  "$__STRINGBEAN_ROOT__/scripts/sbx" "$@"
}

stringbean() {
  "$__STRINGBEAN_ROOT__/scripts/sbx" "$@"
}

if [[ -n "${ZSH_VERSION-}" ]]; then
  __stringbean_accept_sbx_enter() {
    local -a args
    args=( ${(z)BUFFER} )

    if (( ${#args} > 0 )) && [[ ${args[1]} == "/sbx" ]]; then
      args[1]="$__STRINGBEAN_ROOT__/scripts/sbx"
      BUFFER="${(j: :)args}"
    fi

    zle .accept-line
  }

  zle -N __stringbean_accept_sbx_enter
  bindkey '^M' __stringbean_accept_sbx_enter
fi
