#!/usr/bin/env python3
"""用 ffmpeg 生成无头测试读取的媒体样本。

    python3 tests/make_samples.py [输出目录]

Windows 和 macOS/Linux 都能跑：这就是旧 make_samples.sh 那套命令，只是不再需要
POSIX shell。ffmpeg/ffprobe 取 FFMPEG/FFPROBE 环境变量，没设就用 PATH 里的。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'out')
FFMPEG = os.environ.get('FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'


def run(*args):
    argv = [FFMPEG, '-v', 'error', '-y'] + list(args)
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError('%s\n%s' % (' '.join(argv),
                                       (proc.stdout or b'').decode('utf-8', 'replace')))


def tagged(path, acodec):
    """带标签、内嵌封面的音频。

    音频按容器重新编码，因为 PCM 没法直接拷进 mp3。
    """
    run('-i', os.path.join(OUT, 'tone.wav'), '-i', os.path.join(OUT, 'cover.jpg'),
        '-map', '0:a', '-map', '1:v', '-c:a', acodec, '-c:v', 'copy',
        '-disposition:v:0', 'attached_pic',
        '-metadata', 'title=The Original Title',
        '-metadata', 'artist=Original Author',
        '-metadata', 'album=Original Series',
        '-metadata', 'track=3/12',
        # calibre 的标签对应 keywords，不是流派；这里顺手写一个 genre，用来证明它被忽略。
        '-metadata', 'keywords=Audiobook; Fiction',
        '-metadata', 'genre=Legacy Genre',
        '-metadata', 'comment=Original description text.',
        '-metadata', 'publisher=Original Publisher',
        '-metadata', 'date=2011-05-03',
        path)


def main():
    if not os.path.isfile(FFMPEG) and shutil.which(FFMPEG) is None:
        print('ffmpeg not found; set FFMPEG to the binary', file=sys.stderr)
        return 1

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    # --- 原始素材 ---------------------------------------------------------
    run('-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-ac', '1',
        os.path.join(OUT, 'tone.wav'))
    run('-f', 'lavfi', '-i', 'testsrc=duration=2:size=320x240:rate=10',
        '-pix_fmt', 'yuv420p', os.path.join(OUT, 'video.mp4'))
    run('-f', 'lavfi', '-i', 'color=c=0x2266aa:s=120x120:d=1', '-frames:v', '1',
        os.path.join(OUT, 'cover.jpg'))
    run('-f', 'lavfi', '-i', 'color=c=0xaa2266:s=120x120:d=1', '-frames:v', '1',
        os.path.join(OUT, 'cover2.jpg'))

    # --- 带标签的音频，内嵌封面 --------------------------------------------
    # 没有 ogg 样本：ffmpeg 在重编码音频时无法把封面以 attached_pic 塞进 ogg，
    # 测试也用不到它。
    for name, acodec in (('tagged.mp3', 'libmp3lame'), ('tagged.m4a', 'aac'),
                         ('tagged.m4b', 'aac'), ('tagged.flac', 'flac')):
        try:
            tagged(os.path.join(OUT, name), acodec)
        except RuntimeError as err:
            # build 里没编进 flac 编码器时会被拒绝。
            print('  (%s sample skipped: container rejected the command)' % name)
            print('   %s' % str(err).splitlines()[-1])
            broken = os.path.join(OUT, name)
            if os.path.exists(broken):
                os.remove(broken)

    # --- 有标签但没有内嵌封面的视频 ----------------------------------------
    # 这里的视频流绝不能被当成封面。
    run('-i', os.path.join(OUT, 'video.mp4'), '-i', os.path.join(OUT, 'tone.wav'),
        '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac',
        '-metadata', 'title=A Video Lecture',
        '-metadata', 'artist=Some Presenter',
        '-metadata', 'comment=Recorded talk.',
        '-metadata', 'date=2020',
        os.path.join(OUT, 'tagged_video.mp4'))

    run('-i', os.path.join(OUT, 'video.mp4'), '-i', os.path.join(OUT, 'tone.wav'),
        '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac',
        '-metadata', 'title=Matroska Talk',
        '-metadata', 'artist=Another Presenter',
        os.path.join(OUT, 'tagged_video.mkv'))

    # --- 对照组：完全不带标签 ---------------------------------------------
    run('-i', os.path.join(OUT, 'tone.wav'), '-c:a', 'libmp3lame', '-q:a', '9',
        os.path.join(OUT, 'untagged.mp3'))
    run('-i', os.path.join(OUT, 'tone.wav'), '-c:a', 'aac',
        os.path.join(OUT, 'untagged.m4a'))

    print('Samples written to %s' % OUT)
    for name in sorted(os.listdir(OUT)):
        print('  %s' % name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
