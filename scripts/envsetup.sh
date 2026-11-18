# Source this file before running the project:
#   source scripts/envsetup.sh
#
# The sandbox has no working pip (TLS-intercepting proxy), so the scientific
# Python stack was installed from Ubuntu .deb packages into a user prefix.
# This script points the interpreter at that prefix.

_AFS_PREFIX="${AFS_PY_PREFIX:-$HOME/.local/pylibs}"

export PYTHONPATH="$_AFS_PREFIX/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"

if [ -d "$_AFS_PREFIX/usr/lib/x86_64-linux-gnu" ]; then
  _afs_libs="$(find "$_AFS_PREFIX/usr/lib/x86_64-linux-gnu" -type d 2>/dev/null | tr '\n' ':')"
  export LD_LIBRARY_PATH="${_afs_libs}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# Headless-friendly defaults; the GUI dashboard overrides the backend itself.
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplcfg}"
export MATPLOTLIBRC="$_AFS_PREFIX/usr/share/matplotlib/mpl-data/matplotlibrc"
mkdir -p "$MPLCONFIGDIR" 2>/dev/null

unset _AFS_PREFIX _afs_libs
