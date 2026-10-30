#!/bin/sh
# movie_av.c and audioconv_av.c against a real FFmpeg (tools/media_test.c), in the dev container:
#   docker run --rm -v "$PWD":/src -w /src nx2steamos-dev sh nxnative/dm/steamos/tools/media_test.sh [<title ID>]
# It installs the ffmpeg command there (the encoder and ffprobe), makes a 12-frame H.264 clip with B-frames and 1 s of AAC, and decodes both through the host's code.
set -eu
game=${1:-0100B99019412000}
D=$(cd "$(dirname "$0")/../.." && pwd); W=$(mktemp -d)
command -v ffmpeg >/dev/null || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null; }
ffmpeg -loglevel error -f lavfi -i testsrc=size=128x96:rate=30 -frames:v 12 -pix_fmt yuv420p -c:v libx264 -bf 2 -g 12 -x264-params aud=1 -movflags +faststart "$W/t.mp4"
ffmpeg -loglevel error -i "$W/t.mp4" -c copy -bsf:v h264_mp4toannexb -f h264 "$W/t.h264"
ffprobe -loglevel error -select_streams v -show_entries packet=pts_time -of csv=p=0 "$W/t.mp4" > "$W/pts.txt"
ffmpeg -loglevel error -i "$W/t.mp4" -fps_mode passthrough -f rawvideo -pix_fmt yuv420p "$W/ref.yuv"   # frame for frame: no frames added or dropped for a constant rate
ffmpeg -loglevel error -f lavfi -i sine=frequency=440:sample_rate=48000 -t 1 -ac 2 -c:a aac -b:a 128k -f adts "$W/t.aac"
python3 "$D/games/game.py" header "$game" "$W/game_profile.h"
cc -O1 -w -include "$D/android/compat/include/darwin_compat.h" -I"$D/steamos/compat/include" -I"$D/android/compat/include" -I"$D/host" -I"$W" -I"$D/steamos" -D_GNU_SOURCE -DDM_STEAMOS=1 \
   -o "$W/media_test" "$D/steamos/tools/media_test.c" "$D/steamos/movie_av.c" "$D/steamos/audioconv_av.c" "$D/steamos/av_dyn.c" -ldl -lm -lpthread
"$W/media_test" "$W/t.h264" "$W/pts.txt" "$W/ref.yuv" 128 96 "$W/t.aac"
