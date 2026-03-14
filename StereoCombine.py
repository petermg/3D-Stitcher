import argparse
import os
import subprocess
import tempfile
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, correlation_lags


def run(cmd):
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def extract_audio(video_path, wav_path, analyze_seconds=0, sample_rate=2000):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
    ]
    if analyze_seconds and analyze_seconds > 0:
        cmd += ["-t", str(analyze_seconds)]
    cmd += [
        "-c:a", "pcm_s16le",
        wav_path
    ]
    run(cmd)


def load_wav_mono(path):
    sr, data = wavfile.read(path)

    original_dtype = data.dtype

    if data.ndim == 2:
        data = data.mean(axis=1)

    data = data.astype(np.float32)

    if np.issubdtype(original_dtype, np.integer):
        max_val = np.iinfo(original_dtype).max
        data /= max_val

    return sr, data


def make_envelope(audio, sr, env_rate=200):
    win = max(1, sr // env_rate)
    usable = (len(audio) // win) * win
    audio = audio[:usable]

    if usable == 0:
        raise ValueError("Audio too short to analyze.")

    env = np.mean(np.abs(audio.reshape(-1, win)), axis=1)

    # emphasize changes/transients a bit
    env = np.diff(env, prepend=env[0])

    # normalize
    env -= np.mean(env)
    std = np.std(env)
    if std > 1e-9:
        env /= std

    return env, env_rate


def estimate_offset_seconds(left_wav, right_wav):
    sr_l, left_audio = load_wav_mono(left_wav)
    sr_r, right_audio = load_wav_mono(right_wav)

    if sr_l != sr_r:
        raise ValueError(f"Sample rates do not match: {sr_l} vs {sr_r}")

    left_env, env_rate = make_envelope(left_audio, sr_l)
    right_env, _ = make_envelope(right_audio, sr_r)

    corr = correlate(left_env, right_env, mode="full", method="fft")
    lags = correlation_lags(len(left_env), len(right_env), mode="full")
    best_lag = lags[np.argmax(corr)]

    # Interpretation:
    # positive lag  => right started earlier, so trim LEFT
    # negative lag  => left started earlier, so trim RIGHT
    offset_sec = best_lag / env_rate
    return offset_sec


def build_ffmpeg_command(left_video, right_video, output, offset_sec, height=1080, crf=18, preset="medium"):
    # positive offset => trim LEFT
    # negative offset => trim RIGHT
    if offset_sec > 0:
        left_trim = offset_sec
        right_trim = 0.0
    else:
        left_trim = 0.0
        right_trim = -offset_sec

    filter_parts = []

    if left_trim > 0:
        filter_parts.append(
            f"[0:v]trim=start={left_trim},setpts=PTS-STARTPTS,scale=-2:{height},setsar=1[leftv]"
        )
        filter_parts.append(
            f"[0:a]atrim=start={left_trim},asetpts=N/SR/TB[lefta]"
        )
    else:
        filter_parts.append(
            f"[0:v]setpts=PTS-STARTPTS,scale=-2:{height},setsar=1[leftv]"
        )
        filter_parts.append(
            f"[0:a]asetpts=N/SR/TB[lefta]"
        )

    if right_trim > 0:
        filter_parts.append(
            f"[1:v]trim=start={right_trim},setpts=PTS-STARTPTS,scale=-2:{height},setsar=1[rightv]"
        )
    else:
        filter_parts.append(
            f"[1:v]setpts=PTS-STARTPTS,scale=-2:{height},setsar=1[rightv]"
        )

    filter_parts.append(
        "[leftv][rightv]hstack=inputs=2:shortest=1[v]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", left_video,
        "-i", right_video,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[lefta]",
        "-c:v", "av1_nvenc",
        "-rc", "constqp", "-qp", "35",
        "-preset", preset,
#        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output
    ]

    return cmd


def main():
    parser = argparse.ArgumentParser(description="Sync two videos by audio and output side-by-side.")
    parser.add_argument("left_video", help="Left camera video")
    parser.add_argument("right_video", help="Right camera video")
    parser.add_argument("output", help="Output video filename")
    parser.add_argument("--analyze-seconds", type=int, default=300,
                        help="How many seconds of audio to analyze from the start (default: 300)")
    parser.add_argument("--height", type=int, default=1080,
                        help="Output height for each side before stacking (default: 1080)")
    parser.add_argument("--crf", type=int, default=18,
                        help="x264 CRF quality (lower = better quality, default: 18)")
    parser.add_argument("--preset", default="slow",
                        help="x264 preset (default: medium)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmpdir:
        left_wav = os.path.join(tmpdir, "left.wav")
        right_wav = os.path.join(tmpdir, "right.wav")

        extract_audio(args.left_video, left_wav, analyze_seconds=args.analyze_seconds)
        extract_audio(args.right_video, right_wav, analyze_seconds=args.analyze_seconds)

        offset_sec = estimate_offset_seconds(left_wav, right_wav)

        print()
        print(f"Estimated offset: {offset_sec:.3f} seconds")
        if offset_sec > 0:
            print("Right camera started earlier. Trimming LEFT video.")
        elif offset_sec < 0:
            print("Left camera started earlier. Trimming RIGHT video.")
        else:
            print("Videos appear already aligned.")

        cmd = build_ffmpeg_command(
            args.left_video,
            args.right_video,
            args.output,
            offset_sec,
            height=args.height,
            crf=args.crf,
            preset=args.preset
        )
        run(cmd)

        print()
        print("Done.")
        print("Output:", args.output)


if __name__ == "__main__":
    main()