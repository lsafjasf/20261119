#!/bin/bash
set -u
ROOT="$PWD/root"; PKGDIR="$PWD/deps"; mkdir -p "$PKGDIR"
export LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/lib/x86_64-linux-gnu"
map_pkg() {
  local lib="$1" base pkg
  base="${lib%.so*}"          # e.g. libavcodec, libva-drm
  case "$base" in
    libjpeg) echo "libjpeg-turbo8"; return;;
    libXpresent) echo "libxpresent1"; return;;
    libXv) echo "libxv1"; return;;
  esac
  # 1) exact package name guess with soname version
  local ver="${lib#*.so.}"; ver="${ver%%.*}"
  for cand in "${base}${ver}" "${base}-${ver}" "$base"; do
    if apt-cache show "$cand" >/dev/null 2>&1; then echo "$cand"; return; fi
  done
  # 2) search packages providing a file named exactly this lib
  pkg=$(apt-cache search --names-only "^${base}" 2>/dev/null | awk '{print $1}' | while read p; do
    apt-cache show "$p" 2>/dev/null | grep -q "^Filename: " && echo "$p"; break
  done | head -1)
  echo "$pkg"
}
for round in $(seq 1 15); do
  missing=$( (ldd "$ROOT/usr/bin/mpv"; ldd "$ROOT/usr/lib/x86_64-linux-gnu/"*.so* 2>/dev/null) | grep -oE 'lib[^ ]+ => not found' | awk '{print $1}' | sort -u)
  [ -z "$missing" ] && { echo "ALL_RESOLVED round=$round"; break; }
  echo "== round $round: $(echo "$missing" | wc -l) missing =="
  for lib in $missing; do
    pkg=$(map_pkg "$lib")
    if [ -n "$pkg" ] && apt-get download "$pkg" >/dev/null 2>&1; then
      for d in ${pkg}_*.deb; do dpkg-deb -x "$d" "$ROOT"/ && mv "$d" "$PKGDIR"/ 2>/dev/null; done
      echo "  + $pkg"
    else
      echo "  ?? $lib (pkg='$pkg')"
    fi
  done
done
