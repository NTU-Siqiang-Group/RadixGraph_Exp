#!/usr/bin/env bash
# Shared by native Docker builds and direct compiler-driven builds.
# Docker's TARGETARCH uses amd64/arm64; compiler triples use x86_64/aarch64.
case "${TARGETARCH:-$("${CXX:-g++}" -dumpmachine)}" in
    amd64|x86_64|x86_64-*) TARGETARCH=amd64 ;;
    arm64|aarch64|aarch64-*) TARGETARCH=arm64 ;;
    *) echo "Unsupported architecture: ${TARGETARCH:-compiler target}; expected amd64 or arm64" >&2; exit 2 ;;
esac
export TARGETARCH
