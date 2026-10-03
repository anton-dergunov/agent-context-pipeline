#!/usr/bin/env bash
# Renders the README pictures from their HTML sources: every <name>.html in this directory, or the
# names given, becomes <name>.png at twice the size its <meta name="picture-size"> declares.
# Needs Google Chrome and pngquant. Pictures that show captured media read it from media/, which
# is cut from private captures and not committed.
#
#   assets/pictures/render.sh                 # all of them
#   assets/pictures/render.sh overview        # one
set -euo pipefail
cd "$(dirname "$0")"

chrome=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}

if (($# == 0)); then
  set -- *.html
fi

for source in "$@"; do
  name=${source%.html}
  size=$(sed -n 's/.*name="picture-size" content="\([0-9]*x[0-9]*\)".*/\1/p' "$name.html")
  if [[ -z $size ]]; then
    echo "$name.html declares no picture-size" >&2
    exit 1
  fi
  "$chrome" --headless=new --hide-scrollbars --force-device-scale-factor=2 \
    --window-size="${size%x*},${size#*x}" \
    --screenshot="$PWD/$name.png" "file://$PWD/$name.html" 2>/dev/null
  pngquant --force --skip-if-larger --output "$name.png" 256 "$name.png" || true
  echo "$name.png $(( ${size%x*} * 2 ))x$(( ${size#*x} * 2 ))"
done
