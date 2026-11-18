#!/bin/bash
set -u
ROOT="$PWD/root"
PKGDIR="$PWD/deps"
mkdir -p "$PKGDIR"
export LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/lib/x86_64-linux-gnu"
for round in $(seq 1 12); do
  missing=$( (ldd "$ROOT/usr/bin/mpv"; ldd "$ROOT/usr/lib/x86_64-linux-gnu/libmpv.so.2") 2>/dev/null | grep -oE 'lib[^ ]+ => not found' | awk '{print $1}' | sort -u)
  [ -z "$missing" ] && { echo "ALL_RESOLVED round=$round"; break; }
  echo "== round $round: $(echo "$missing" | wc -l) missing =="
  for lib in $missing; do
    pkg=$(dpkg -S "$lib" 2>/dev/null | head -1 | cut -d: -f1 | sed 's/:amd64//')
    [ -z "$pkg" ] && pkg=$(apt-file search "/$lib" 2>/dev/null | head -1 | cut -d: -f1)
    [ -z "$pkg" ] && { echo "  ??? cannot map $lib"; continue; }
    if apt-get download "$pkg" >/dev/null 2>&1; then
      for d in ${pkg}_*.deb; do dpkg-deb -x "$d" "$ROOT"/ && mv "$d" "$PKGDIR"/ 2>/dev/null; done
      echo "  + $pkg ($lib)"
    else
      echo "  !! download failed $pkg"
    fi
  done
done
