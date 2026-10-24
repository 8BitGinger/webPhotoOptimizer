import argparse
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageOps, ImageSequence
from tqdm import tqdm

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.jfif', '.tif', '.tiff', '.webp', '.bmp', '.gif')
VIDEO_EXTS = ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mpg', '.mpeg', '.ts', '.mts', '.m2ts', '.vob', '.wmv', '.flv')

IMAGE_FORMATS = {
    'png': 'PNG',
    'jpg': 'JPEG',
    'webp': 'WEBP',
    'tiff': 'TIFF',
    'bmp': 'BMP',
    'gif': 'GIF',
}
VIDEO_FORMATS = ('mp4', 'webm', 'mov', 'mkv', 'gif')
AUDIO_EXTS = ('.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wma',
              '.aif', '.aiff', '.alac', '.amr', '.ape', '.wv')
AUDIO_FORMATS = ('mp3', 'm4a', 'opus', 'flac')
LOSSY_AUDIO_FORMATS = ('mp3', 'm4a', 'opus')
ANIMATED_FORMATS = ('GIF', 'WEBP', 'PNG')  # image formats Pillow can save with multiple frames

LOSSLESS = 'lossless'  # special quality value: no added compression loss
FIXED_QUALITY_FORMATS = ('png', 'tiff', 'bmp', 'gif', 'flac')  # the quality setting has no effect


# ---------- helpers ----------

def parse_ratio(text):
    """'16:9' -> (16, 9). Empty/'original' -> None."""
    if not text or text.lower() in ('original', 'none'):
        return None
    m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*', text)
    if not m or float(m.group(1)) <= 0 or float(m.group(2)) <= 0:
        raise ValueError(f"Invalid ratio '{text}' (use e.g. 16:9, 1:1, 4:5)")
    return float(m.group(1)), float(m.group(2))


def parse_size(text):
    """'1920x1080' -> (1920, 1080). Empty/'original' -> None."""
    if not text or text.lower() in ('original', 'none'):
        return None
    m = re.fullmatch(r'\s*(\d+)\s*[x*]\s*(\d+)\s*', text.lower())
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        raise ValueError(f"Invalid size '{text}' (use e.g. 1920x1080)")
    return int(m.group(1)), int(m.group(2))


def find_ffmpeg():
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


class Cancelled(Exception):
    """Raised when a running ffmpeg job was stopped on request."""


FFMPEG_PROGRESS_LINE = re.compile(r'^[a-z_0-9:]+=')  # key=value lines from ffmpeg's -progress output


def run_ffmpeg(cmd, duration=None, watch=None):
    """Run ffmpeg capturing its output. Without a console (pythonw) Windows would open a window per call,
    and a closed stdin keeps ffmpeg from waiting on keyboard input.

    With watch, ffmpeg's progress is read as it runs: watch(fraction) is called about twice a second
    (fraction is 0-1 of duration seconds, or None if the length is unknown) and returns True to
    stop ffmpeg, which raises Cancelled.
    """
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    if not watch:
        return subprocess.run(cmd, capture_output=True, text=True, errors='replace', stdin=subprocess.DEVNULL,
                              creationflags=flags)

    cmd = cmd[:1] + ['-progress', 'pipe:1', '-nostats'] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, errors='replace', creationflags=flags)
    fraction, errors = None, []
    for line in proc.stdout:
        line = line.strip()
        m = re.match(r'out_time_us=(\d+)', line)
        if m and duration:
            fraction = min(1.0, int(m.group(1)) / 1e6 / duration)
        elif line == 'progress=continue' or line == 'progress=end':
            if watch(fraction):
                proc.kill()
                proc.wait()
                raise Cancelled()
        elif line and not FFMPEG_PROGRESS_LINE.match(line):  # anything else is an error message
            errors.append(line)
    proc.wait()
    return subprocess.CompletedProcess(cmd, proc.returncode, '', '\n'.join(errors))


def is_animated(path):
    try:
        with Image.open(path) as img:
            return getattr(img, 'is_animated', False)
    except OSError:
        return False


def collect_files(input_folder, media):
    if media in ('image', 'audio'):
        exts = IMAGE_EXTS if media == 'image' else AUDIO_EXTS
        return sorted(f for f in os.listdir(input_folder) if f.lower().endswith(exts))
    # Animated GIFs are short silent videos, so the video pipeline accepts them too
    return sorted(f for f in os.listdir(input_folder)
                  if f.lower().endswith(VIDEO_EXTS)
                  or (f.lower().endswith('.gif') and is_animated(os.path.join(input_folder, f))))


# ---------- images ----------

def center_crop(img, ratio):
    rw, rh = ratio
    w, h = img.size
    target = rw / rh
    if w / h > target:  # too wide
        new_w = round(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / target)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def fit(img, ratio, size):
    if ratio:
        img = center_crop(img, ratio)
    if size:
        img.thumbnail(size, Image.Resampling.LANCZOS)
    return img


def convert_animation(img, dst, pil_format, ratio, size, quality):
    """Crop/resize every frame of an animated image and save it as an animation."""
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        durations.append(frame.info.get('duration', 100))
        frames.append(fit(frame.convert('RGBA'), ratio, size))

    save_args = {'save_all': True, 'append_images': frames[1:], 'duration': durations,
                 'loop': img.info.get('loop', 0)}
    if pil_format == 'GIF':
        save_args.update(optimize=True, disposal=2)  # frames are already full composited images
    elif pil_format == 'WEBP':
        if quality == LOSSLESS:
            save_args.update(lossless=True, quality=100, method=6)
        else:
            save_args.update(quality=quality, method=6)
    frames[0].save(dst, pil_format, **save_args)


def convert_image(src, dst, fmt, ratio=None, size=None, quality=80, keep_metadata=False):
    """Returns True if an animation was flattened to its first frame."""
    pil_format = IMAGE_FORMATS[fmt]
    lossless = quality == LOSSLESS
    with Image.open(src) as img:
        animated = getattr(img, 'is_animated', False)
        if animated and pil_format in ANIMATED_FORMATS:
            convert_animation(img, dst, pil_format, ratio, size, quality)
            return False

        img = ImageOps.exif_transpose(img)  # respect camera rotation before cropping

        # Grab metadata before converting. The colour profile is always kept (dropping it
        # shifts colours) unless the source is CMYK, since that profile would be wrong on RGB.
        exif = img.getexif().tobytes() if keep_metadata else None
        icc = img.info.get('icc_profile') if img.mode != 'CMYK' else None

        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')

        img = fit(img, ratio, size)

        # Formats without alpha support: flatten onto white
        if pil_format in ('JPEG', 'BMP') and img.mode == 'RGBA':
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.getchannel('A'))
            img = background

        save_args = {}
        if pil_format == 'JPEG':
            if lossless:  # JPEG can't be lossless; use its best setting with full colour detail
                save_args = {'quality': 100, 'subsampling': 0}
            else:
                save_args = {'quality': quality}
            save_args.update(optimize=True, progressive=True)
        elif pil_format == 'WEBP':
            if lossless:  # in lossless mode 'quality' is compression effort, not fidelity
                save_args = {'lossless': True, 'quality': 100, 'method': 6}
            else:
                save_args = {'quality': quality, 'method': 6}
        elif pil_format == 'PNG':
            save_args = {'optimize': True}
        elif pil_format == 'TIFF':
            save_args = {'compression': 'tiff_adobe_deflate'}

        if pil_format in ('JPEG', 'WEBP', 'PNG', 'TIFF'):
            if exif:
                save_args['exif'] = exif
            if icc:
                save_args['icc_profile'] = icc

        img.save(dst, pil_format, **save_args)
    return animated


# ---------- videos ----------

def is_interlaced(ffmpeg, src, frames=300):
    """Run ffmpeg's idet filter on the first frames and report whether most are interlaced."""
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-i', src, '-map', '0:v:0',
           '-vf', 'idet', '-frames:v', str(frames), '-an', '-f', 'null', '-']
    result = run_ffmpeg(cmd)
    # ffmpeg may print several summaries (e.g. after a filter reinit); the last one is the real total
    matches = re.findall(r'Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)', result.stderr)
    if not matches:
        return False
    tff, bff, progressive = map(int, matches[-1])
    # Be conservative: detail-heavy progressive footage can produce some false TFF/BFF hits
    return (tff + bff) > 2 * progressive


def build_video_filter(ratio, size, interlaced, fmt):
    filters = []

    # De-interlace: always process frames flagged as interlaced; if detection
    # found interlacing (often unflagged), process every frame.
    filters.append(f"bwdif=mode=send_frame:deint={'all' if interlaced else 'interlaced'}")

    if ratio:
        rw, rh = ratio
        filters.append(
            f"crop='trunc(min(iw,ih*{rw}/{rh})/2)*2':'trunc(min(ih,iw*{rh}/{rw})/2)*2'"
        )
    if size:
        w, h = size
        # Fit inside the box, never upscale, keep dimensions even for the encoder
        filters.append(
            f"scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease"
            ":force_divisible_by=2:flags=lanczos"
        )
    elif fmt != 'gif':
        # yuv420p encoders need even dimensions; GIF sources are often odd-sized
        filters.append("scale='trunc(iw/2)*2':'trunc(ih/2)*2'")
    filters.append('setsar=1')

    if fmt == 'gif':
        filters.append('fps=15')
        return ','.join(filters) + ',split[a][b];[a]palettegen[p];[b][p]paletteuse'
    return ','.join(filters)


def probe(ffmpeg, src):
    """Return (duration in seconds or None, whether the file has an audio stream)."""
    stderr = run_ffmpeg([ffmpeg, '-hide_banner', '-i', src]).stderr
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', stderr)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    return duration or None, re.search(r'Stream #\d+:\d+.*?: Audio:', stderr) is not None


def convert_video(ffmpeg, src, dst, fmt, ratio=None, size=None, quality=80, keep_metadata=False,
                  strip_audio=False, duration=None, watch=None):
    lossless = quality == LOSSLESS
    interlaced = not src.lower().endswith('.gif') and is_interlaced(ffmpeg, src)
    vf = build_video_filter(ratio, size, interlaced, fmt)

    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', src]
    if fmt == 'gif':
        cmd += ['-filter_complex', vf, '-an']
    else:
        cmd += ['-vf', vf]

    if not keep_metadata:  # strips tags like GPS location and recording device
        cmd += ['-map_metadata', '-1']

    # Lossy modes map 0-100 quality onto encoder CRF (higher quality -> lower CRF).
    # Lossless modes keep the source colour format instead of forcing yuv420p.
    if fmt in ('mp4', 'mov', 'mkv'):
        if lossless:
            audio = 'flac' if fmt == 'mkv' else 'alac'
            cmd += ['-c:v', 'libx264', '-preset', 'slow', '-qp', '0', '-c:a', audio]
        else:
            crf = round(35 - quality * 0.17)  # 80 -> 21
            cmd += ['-c:v', 'libx264', '-preset', 'slow', '-crf', str(crf), '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '160k']
        if fmt in ('mp4', 'mov'):
            cmd += ['-movflags', '+faststart']
    elif fmt == 'webm':
        if lossless:  # WebM only allows lossy audio, so use a high Opus bitrate
            cmd += ['-c:v', 'libvpx-vp9', '-lossless', '1', '-row-mt', '1',
                    '-c:a', 'libopus', '-b:a', '256k']
        else:
            crf = round(50 - quality * 0.25)  # 80 -> 30
            cmd += ['-c:v', 'libvpx-vp9', '-crf', str(crf), '-b:v', '0', '-row-mt', '1',
                    '-pix_fmt', 'yuv420p', '-c:a', 'libopus', '-b:a', '128k']

    if strip_audio:  # overrides the audio codec settings above
        cmd.append('-an')

    cmd.append(dst)
    result = run_ffmpeg(cmd, duration, watch)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'ffmpeg failed')
    return interlaced


# ---------- audio ----------

def convert_audio(ffmpeg, src, dst, fmt, quality=80, keep_metadata=False, duration=None, watch=None):
    # Only the first audio track; drops embedded cover art, which can be larger than the audio
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-map', '0:a:0', '-vn']
    if not keep_metadata:
        cmd += ['-map_metadata', '-1']

    # Lossy formats can't be lossless, so 'lossless' means their highest setting
    q = 100 if quality == LOSSLESS else quality
    if fmt == 'mp3':
        if quality == LOSSLESS:
            cmd += ['-c:a', 'libmp3lame', '-b:a', '320k']
        else:  # LAME VBR: 0 best .. 9 smallest. 80 -> V2 (~190 kbps)
            cmd += ['-c:a', 'libmp3lame', '-q:a', str(min(9, max(0, round((100 - q) / 10))))]
    elif fmt == 'm4a':
        cmd += ['-c:a', 'aac', '-b:a', f"{min(256, 32 + q * 2)}k", '-movflags', '+faststart']  # 80 -> 192k
    elif fmt == 'opus':
        cmd += ['-c:a', 'libopus', '-b:a', f"{min(192, round(16 + q * 1.4))}k"]  # 80 -> 128k
    elif fmt == 'flac':
        cmd += ['-c:a', 'flac', '-compression_level', '8']

    cmd.append(dst)
    result = run_ffmpeg(cmd, duration, watch)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'ffmpeg failed')


# ---------- driver ----------

def assign_output_names(jobs):
    """Give each (filename, media, fmt) job a unique output name.

    Normally 'photo.jpg' -> 'photo.webp'. When several inputs would produce the
    same name (e.g. photo.jpg and photo.png), the source extension is added
    to each of them: 'photo-jpg.webp', 'photo-png.webp'.
    """
    def key(name):  # Windows file names are case-insensitive
        return name.lower()

    simple = [f"{os.path.splitext(f)[0]}.{fmt}" for f, _, fmt in jobs]
    counts = {}
    for name in simple:
        counts[key(name)] = counts.get(key(name), 0) + 1

    names, used = [], set()
    for (filename, _, fmt), name in zip(jobs, simple):
        base, ext = os.path.splitext(filename)
        if counts[key(name)] > 1:
            name = f"{base}-{ext.lstrip('.')}.{fmt}"
        # Last resort, e.g. an input literally named 'photo-png.jpg'
        n, candidate = 2, name
        while key(candidate) in used:
            candidate = f"{os.path.splitext(name)[0]}-{n}.{fmt}"
            n += 1
        used.add(key(candidate))
        names.append(candidate)
    return names


def process_folder(input_folder, output_folder, image_fmt=None, video_fmt=None, ratio=None, size=None,
                   quality=80, keep_metadata=False, audio_fmt=None, split_audio_fmt=None, audio_only=False,
                   log=tqdm.write, progress=None, cancel=None):
    """Convert every image, video and/or audio file in input_folder to the given formats.

    With split_audio_fmt, videos are written without sound and each video's audio
    is saved as a separate file in that format. Adding audio_only skips the video
    files entirely, so only the audio files are written (video_fmt is then ignored).

    log(message) receives status messages. progress(done, total, current, fraction), if given, replaces the console progress bar;
    current names the file in progress (None once it finishes) and fraction is how far through
    it ffmpeg is, 0-1 (None when unknown, e.g. for images).
    cancel(), if given, is polled between files and stops the run when it returns True.
    """
    audio_only = audio_only and bool(split_audio_fmt)
    if audio_only:
        video_fmt = None

    os.makedirs(output_folder, exist_ok=True)

    jobs = []
    video_files = collect_files(input_folder, 'video') if (video_fmt or audio_only) else []
    if image_fmt:
        # When converting both, animated GIFs go through the video pipeline only
        converted_videos = video_files if video_fmt else []
        jobs += [(f, 'image', image_fmt) for f in collect_files(input_folder, 'image') if f not in converted_videos]
    for f in video_files:
        if video_fmt:
            jobs.append((f, 'video', video_fmt))
        if split_audio_fmt and not f.lower().endswith('.gif'):  # GIFs never have sound
            jobs.append((f, 'extract', split_audio_fmt))
    if audio_fmt:
        jobs += [(f, 'audio', audio_fmt) for f in collect_files(input_folder, 'audio')]
    if not jobs:
        kinds = ' or '.join(k for k, f in (('image', image_fmt), ('video', video_fmt or audio_only),
                                           ('audio', audio_fmt)) if f)
        log(f"No {kinds} files found in '{input_folder}'.")
        return

    if quality == LOSSLESS:
        if 'jpg' == image_fmt:
            log("Note: JPG can't be lossless; using its highest quality setting (100, full colour detail).")
        if 'gif' in (image_fmt, video_fmt):
            log("Note: GIF is limited to 256 colours, so it can't be lossless for photos or video.")
        for fmt in dict.fromkeys(f for f in (audio_fmt, split_audio_fmt) if f in LOSSY_AUDIO_FORMATS):
            log(f"Note: {fmt.upper()} can't be lossless; using its highest bitrate. Choose FLAC for lossless audio.")

    ffmpeg = None
    if any(media in ('video', 'audio', 'extract') for _, media, _ in jobs):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found. Install it, or run: pip install imageio-ffmpeg")

    out_names = assign_output_names(jobs)
    work = list(zip(jobs, out_names))
    total = len(work)
    if progress:
        progress(0, total, None, None)
    else:
        work = tqdm(work, desc='Converting', unit='file')
    for done, ((filename, media, fmt), out_name) in enumerate(work):
        if cancel and cancel():
            log("Stopped.")
            break
        src = os.path.join(input_folder, filename)
        dst = os.path.join(output_folder, out_name)
        current = f"{filename} -> {out_name}"
        if out_name != f"{os.path.splitext(filename)[0]}.{fmt}":
            log(f"Name clash: {filename} -> {out_name}")
        if progress:
            progress(done, total, current, None)

        def watch(fraction):
            if progress:
                progress(done, total, current, fraction)
            return bool(cancel and cancel())

        try:
            if media == 'image':
                if convert_image(src, dst, fmt, ratio, size, quality, keep_metadata):
                    log(f"{filename} is animated; {fmt.upper()} can't animate, so only the first frame "
                        f"was kept. Use GIF/WEBP/PNG, or convert it as a video.")
            else:
                duration, audio_present = probe(ffmpeg, src)
                watcher = watch if (progress or cancel) else None
                if media == 'audio':
                    convert_audio(ffmpeg, src, dst, fmt, quality, keep_metadata, duration, watcher)
                elif media == 'extract':
                    if audio_present:
                        convert_audio(ffmpeg, src, dst, fmt, quality, keep_metadata, duration, watcher)
                    else:
                        log(f"{filename} has no audio track; no audio file was created")
                elif convert_video(ffmpeg, src, dst, fmt, ratio, size, quality, keep_metadata,
                                   bool(split_audio_fmt), duration, watcher):
                    log(f"De-interlaced {filename}")
        except Cancelled:
            if os.path.exists(dst):  # ffmpeg was killed mid-write, so the file is incomplete
                os.remove(dst)
            log(f"Stopped while converting {filename}.")
            break
        except Exception as e:
            log(f"Error processing {filename}: {e}")
        if progress:
            progress(done + 1, total, None, None)


CUSTOM = object()

RATIO_CHOICES = [
    ('Original (no crop)', None),
    ('1:1   Square', (1, 1)),
    ('16:9  Widescreen', (16, 9)),
    ('9:16  Vertical / Stories', (9, 16)),
    ('4:3   Standard', (4, 3)),
    ('3:2   Classic photo', (3, 2)),
    ('4:5   Portrait (Instagram)', (4, 5)),
    ('21:9  Ultrawide', (21, 9)),
    ('Custom...', CUSTOM),
]

SIZE_CHOICES = [
    ('Original (no resize)', None),
    ('3840x2160  4K', (3840, 2160)),
    ('2560x1440  QHD', (2560, 1440)),
    ('1920x1080  Full HD', (1920, 1080)),
    ('1280x720   HD', (1280, 720)),
    ('1080x1080  Square', (1080, 1080)),
    ('1080x1350  Portrait', (1080, 1350)),
    ('1080x1920  Vertical', (1080, 1920)),
    ('800x800    Thumbnail', (800, 800)),
    ('Custom...', CUSTOM),
]

QUALITY_CHOICES = [
    ('Lossless (no quality loss, larger files)', LOSSLESS),
    ('High    (95)', 95),
    ('Good    (80)', 80),
    ('Medium  (65)', 65),
    ('Low     (50)', 50),
    ('Custom...', CUSTOM),
]

AUDIO_CHOICES = [
    ('MP3   Plays everywhere', 'mp3'),
    ('M4A   AAC, smaller than MP3 at the same quality', 'm4a'),
    ('OPUS  Smallest, best for speech and podcasts', 'opus'),
    ('FLAC  Lossless (large, for archiving)', 'flac'),
]


def choose(title, options, default=1):
    """Show a numbered menu of (label, value) pairs and return the chosen value."""
    print(f"\n{title}")
    for n, (label, _) in enumerate(options, 1):
        marker = '  (default)' if n == default else ''
        print(f"  {n}. {label}{marker}")
    while True:
        answer = input(f"Enter 1-{len(options)} [{default}]: ").strip() or str(default)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        print(f"  Please enter a number from 1 to {len(options)}.")


def ask_custom(prompt, parser):
    while True:
        try:
            return parser(input(f"{prompt}: "))
        except ValueError as e:
            print(f"  {e}")


def parse_quality(text):
    if text.strip().lower() == LOSSLESS:
        return LOSSLESS
    if not text.strip().isdigit() or not 1 <= int(text) <= 100:
        raise ValueError("Enter a whole number from 1 to 100, or 'lossless'")
    return int(text)


def interactive():
    print("=== Web Photo / Video / Audio Optimizer ===")
    print("Reading from 'input_folder', writing to 'output_folder'.")

    media = choose("What do you want to convert?",
                   [('Images', 'image'), ('Videos', 'video'), ('Audio', 'audio'),
                    ('Images and videos', 'both'), ('Everything', 'all')])

    image_fmt = video_fmt = audio_fmt = split_audio_fmt = None
    audio_only = False
    if media in ('image', 'both', 'all'):
        image_fmt = choose("Output file type for images:", [(f.upper(), f) for f in IMAGE_FORMATS])
    if media in ('video', 'both', 'all'):
        sound = choose("Video sound:", [('Keep it in the video', 'keep'),
                                        ('Split it out: silent video + separate audio file', 'split'),
                                        ('Audio only: save just the sound, no video file', 'only')])
        audio_only = sound == 'only'
        if not audio_only:
            video_fmt = choose("Output file type for videos:", [(f.upper(), f) for f in VIDEO_FORMATS])
        if sound != 'keep':
            split_audio_fmt = choose("Output file type for the audio taken from videos:", AUDIO_CHOICES)
    if media in ('audio', 'all'):
        audio_fmt = choose("Output file type for audio:", AUDIO_CHOICES)

    ratio = size = None
    if image_fmt or video_fmt:  # cropping and resizing don't apply to audio
        ratio = choose("Crop to aspect ratio:", RATIO_CHOICES)
        if ratio is CUSTOM:
            ratio = ask_custom("Enter ratio, e.g. 5:4", parse_ratio)

        size = choose("Maximum output size:", SIZE_CHOICES, default=4)
        if size is CUSTOM:
            size = ask_custom("Enter size WxH, e.g. 1600x900", parse_size)

    # PNG/TIFF/BMP/FLAC are always lossless and GIF is always 256 colours, so only
    # ask about quality when a chosen format actually uses it
    if any(f and f not in FIXED_QUALITY_FORMATS for f in (image_fmt, video_fmt, audio_fmt, split_audio_fmt)):
        quality = choose("Quality:", QUALITY_CHOICES, default=3)
        if quality is CUSTOM:
            quality = ask_custom("Enter quality 1-100", parse_quality)
    else:
        quality = LOSSLESS

    keep_metadata = choose("Keep metadata (camera info, date, GPS location, audio tags)?",
                           [('No, strip it (smaller, more private)', False), ('Yes, keep it', True)])

    return dict(input_folder='input_folder', output_folder='output_folder', image_fmt=image_fmt,
                video_fmt=video_fmt, audio_fmt=audio_fmt, ratio=ratio, size=size, quality=quality,
                keep_metadata=keep_metadata, split_audio_fmt=split_audio_fmt, audio_only=audio_only)


def main():
    try:
        run()
    except RuntimeError as e:
        sys.exit(str(e))


def run():
    if len(sys.argv) == 1:
        process_folder(**interactive())
        return

    p = argparse.ArgumentParser(
        description="Crop, resize and convert images or videos, and compress audio. Videos are de-interlaced "
                    "automatically. Run with no arguments for interactive mode.")
    p.add_argument('-i', '--input', default='input_folder', help="input folder (default: input_folder)")
    p.add_argument('-o', '--output', default='output_folder', help="output folder (default: output_folder)")
    p.add_argument('-m', '--media', choices=['image', 'video', 'audio', 'both', 'all'], default='image',
                   help="type of files to convert; both = images and videos, all = everything (default: image)")
    p.add_argument('-f', '--format', help=f"output type with a single -m type. images: {', '.join(IMAGE_FORMATS)}; "
                                          f"videos: {', '.join(VIDEO_FORMATS)}; audio: {', '.join(AUDIO_FORMATS)}")
    p.add_argument('--image-format', help="output type for images with -m both/all (default: png)")
    p.add_argument('--video-format', help="output type for videos with -m both/all (default: mp4)")
    p.add_argument('--audio-format', help="output type for audio with -m all, and for audio split from videos "
                                          "with -a (default: mp3)")
    p.add_argument('-a', '--split-audio', action='store_true',
                   help="remove the sound from videos and save it as a separate audio file")
    p.add_argument('--audio-only', action='store_true',
                   help="save just the sound from videos as audio files, with no video output "
                        "(with -m video, -f then sets the audio type)")
    p.add_argument('-r', '--ratio', type=parse_ratio, help="center-crop to aspect ratio, e.g. 16:9, 1:1, 4:5")
    p.add_argument('-s', '--size', type=parse_size, default=(1920, 1080),
                   help="max output size WxH (default: 1920x1080, 'original' to keep)")
    p.add_argument('-q', '--quality', type=parse_quality, default=80,
                   help="quality 1-100 or 'lossless' (default: 80)")
    p.add_argument('-k', '--keep-metadata', action='store_true',
                   help="keep camera info, dates and GPS location (stripped by default)")
    args = p.parse_args()

    valid_formats = {'image': IMAGE_FORMATS, 'video': VIDEO_FORMATS, 'audio': AUDIO_FORMATS}
    aliases = {'jpeg': 'jpg', 'aac': 'm4a'}

    def check_format(fmt, kind, default):
        valid = valid_formats[kind]
        fmt = (fmt or default).lower().lstrip('.')
        fmt = aliases.get(fmt, fmt)
        if fmt not in valid:
            p.error(f"'{fmt}' is not a valid {kind} format. Choose from: {', '.join(valid)}")
        return fmt

    kinds = {'both': ('image', 'video'), 'all': ('image', 'video', 'audio')}.get(args.media, (args.media,))
    if len(kinds) > 1 and args.format:
        p.error(f"with -m {args.media}, use --image-format, --video-format and --audio-format instead of -f")
    if args.audio_only and 'video' not in kinds:
        p.error("--audio-only needs -m video, both or all")

    # Audio-only video runs write no video, so a lone -f names the audio type instead
    audio_format = args.audio_format
    if args.audio_only and kinds == ('video',):
        audio_format = args.format or audio_format
        kinds = ()
    per_kind = {'image': (args.image_format, 'png'), 'video': (args.video_format, 'mp4'),
                'audio': (args.audio_format, 'mp3')}
    fmts = {k: check_format(args.format or per_kind[k][0], k, per_kind[k][1]) for k in kinds}
    if args.audio_only:
        fmts.pop('video', None)

    split_audio_fmt = None
    if args.split_audio or args.audio_only:
        if 'video' not in kinds and not args.audio_only:
            p.error("-a/--split-audio needs -m video, both or all")
        split_audio_fmt = check_format(audio_format, 'audio', 'mp3')

    process_folder(args.input, args.output, fmts.get('image'), fmts.get('video'), args.ratio, args.size,
                   args.quality, args.keep_metadata, fmts.get('audio'), split_audio_fmt, args.audio_only)


if __name__ == '__main__':
    main()
