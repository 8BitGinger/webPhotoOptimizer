import argparse
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageOps
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

LOSSLESS = 'lossless'  # special quality value: no added compression loss
FIXED_QUALITY_FORMATS = ('png', 'tiff', 'bmp', 'gif')  # the quality setting has no effect


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


def collect_files(input_folder, media):
    exts = {'image': IMAGE_EXTS, 'video': VIDEO_EXTS}[media]
    return sorted(f for f in os.listdir(input_folder) if f.lower().endswith(exts))


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


def convert_image(src, dst, fmt, ratio=None, size=None, quality=80, keep_metadata=False):
    pil_format = IMAGE_FORMATS[fmt]
    lossless = quality == LOSSLESS
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)  # respect camera rotation before cropping

        # Grab metadata before converting. The colour profile is always kept (dropping it
        # shifts colours) unless the source is CMYK, since that profile would be wrong on RGB.
        exif = img.getexif().tobytes() if keep_metadata else None
        icc = img.info.get('icc_profile') if img.mode != 'CMYK' else None

        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')

        if ratio:
            img = center_crop(img, ratio)
        if size:
            img.thumbnail(size, Image.Resampling.LANCZOS)

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


# ---------- videos ----------

def is_interlaced(ffmpeg, src, frames=300):
    """Run ffmpeg's idet filter on the first frames and report whether most are interlaced."""
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-i', src, '-map', '0:v:0',
           '-vf', 'idet', '-frames:v', str(frames), '-an', '-f', 'null', '-']
    result = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
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
    filters.append('setsar=1')

    if fmt == 'gif':
        filters.append('fps=15')
        return ','.join(filters) + ',split[a][b];[a]palettegen[p];[b][p]paletteuse'
    return ','.join(filters)


def convert_video(ffmpeg, src, dst, fmt, ratio=None, size=None, quality=80, keep_metadata=False):
    lossless = quality == LOSSLESS
    interlaced = is_interlaced(ffmpeg, src)
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

    cmd.append(dst)
    result = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'ffmpeg failed')
    return interlaced


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
                   quality=80, keep_metadata=False):
    """Convert every image (to image_fmt) and/or every video (to video_fmt) in input_folder."""
    os.makedirs(output_folder, exist_ok=True)

    jobs = []
    if image_fmt:
        jobs += [(f, 'image', image_fmt) for f in collect_files(input_folder, 'image')]
    if video_fmt:
        jobs += [(f, 'video', video_fmt) for f in collect_files(input_folder, 'video')]
    if not jobs:
        kinds = ' or '.join(k for k, f in (('image', image_fmt), ('video', video_fmt)) if f)
        print(f"No {kinds} files found in '{input_folder}'.")
        return

    if quality == LOSSLESS:
        if 'jpg' == image_fmt:
            print("Note: JPG can't be lossless; using its highest quality setting (100, full colour detail).")
        if 'gif' in (image_fmt, video_fmt):
            print("Note: GIF is limited to 256 colours, so it can't be lossless for photos or video.")

    ffmpeg = None
    if any(media == 'video' for _, media, _ in jobs):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            sys.exit("ffmpeg not found. Install it, or run: pip install imageio-ffmpeg")

    out_names = assign_output_names(jobs)
    for (filename, media, fmt), out_name in tqdm(list(zip(jobs, out_names)), desc='Converting', unit='file'):
        src = os.path.join(input_folder, filename)
        dst = os.path.join(output_folder, out_name)
        if out_name != f"{os.path.splitext(filename)[0]}.{fmt}":
            tqdm.write(f"Name clash: {filename} -> {out_name}")
        try:
            if media == 'image':
                convert_image(src, dst, fmt, ratio, size, quality, keep_metadata)
            else:
                if convert_video(ffmpeg, src, dst, fmt, ratio, size, quality, keep_metadata):
                    tqdm.write(f"De-interlaced {filename}")
        except Exception as e:
            tqdm.write(f"Error processing {filename}: {e}")


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
    print("=== Web Photo / Video Optimizer ===")
    print("Reading from 'input_folder', writing to 'output_folder'.")

    media = choose("What do you want to convert?",
                   [('Images', 'image'), ('Videos', 'video'), ('Images and videos', 'both')])

    image_fmt = video_fmt = None
    if media in ('image', 'both'):
        image_fmt = choose("Output file type for images:", [(f.upper(), f) for f in IMAGE_FORMATS])
    if media in ('video', 'both'):
        video_fmt = choose("Output file type for videos:", [(f.upper(), f) for f in VIDEO_FORMATS])

    ratio = choose("Crop to aspect ratio:", RATIO_CHOICES)
    if ratio is CUSTOM:
        ratio = ask_custom("Enter ratio, e.g. 5:4", parse_ratio)

    size = choose("Maximum output size:", SIZE_CHOICES, default=4)
    if size is CUSTOM:
        size = ask_custom("Enter size WxH, e.g. 1600x900", parse_size)

    # PNG/TIFF/BMP are always lossless and GIF is always 256 colours, so only
    # ask about quality when a chosen format actually uses it
    if any(f and f not in FIXED_QUALITY_FORMATS for f in (image_fmt, video_fmt)):
        quality = choose("Quality:", QUALITY_CHOICES, default=3)
        if quality is CUSTOM:
            quality = ask_custom("Enter quality 1-100", parse_quality)
    else:
        quality = LOSSLESS

    keep_metadata = choose("Keep metadata (camera info, date, GPS location)?",
                           [('No, strip it (smaller, more private)', False), ('Yes, keep it', True)])

    return 'input_folder', 'output_folder', image_fmt, video_fmt, ratio, size, quality, keep_metadata


def main():
    if len(sys.argv) == 1:
        process_folder(*interactive())
        return

    p = argparse.ArgumentParser(
        description="Crop, resize and convert images or videos. Videos are de-interlaced automatically. "
                    "Run with no arguments for interactive mode.")
    p.add_argument('-i', '--input', default='input_folder', help="input folder (default: input_folder)")
    p.add_argument('-o', '--output', default='output_folder', help="output folder (default: output_folder)")
    p.add_argument('-m', '--media', choices=['image', 'video', 'both'], default='image',
                   help="type of files to convert (default: image)")
    p.add_argument('-f', '--format', help=f"output type with -m image or video. images: {', '.join(IMAGE_FORMATS)}; "
                                          f"videos: {', '.join(VIDEO_FORMATS)}")
    p.add_argument('--image-format', help="output type for images with -m both (default: png)")
    p.add_argument('--video-format', help="output type for videos with -m both (default: mp4)")
    p.add_argument('-r', '--ratio', type=parse_ratio, help="center-crop to aspect ratio, e.g. 16:9, 1:1, 4:5")
    p.add_argument('-s', '--size', type=parse_size, default=(1920, 1080),
                   help="max output size WxH (default: 1920x1080, 'original' to keep)")
    p.add_argument('-q', '--quality', type=parse_quality, default=80,
                   help="quality 1-100 or 'lossless' (default: 80)")
    p.add_argument('-k', '--keep-metadata', action='store_true',
                   help="keep camera info, dates and GPS location (stripped by default)")
    args = p.parse_args()

    def check_format(fmt, kind, default):
        valid = IMAGE_FORMATS if kind == 'image' else VIDEO_FORMATS
        fmt = (fmt or default).lower().lstrip('.')
        if fmt == 'jpeg':
            fmt = 'jpg'
        if fmt not in valid:
            p.error(f"'{fmt}' is not a valid {kind} format. Choose from: {', '.join(valid)}")
        return fmt

    if args.media == 'both':
        if args.format:
            p.error("with -m both, use --image-format and --video-format instead of -f")
        image_fmt = check_format(args.image_format, 'image', 'png')
        video_fmt = check_format(args.video_format, 'video', 'mp4')
    elif args.media == 'image':
        image_fmt, video_fmt = check_format(args.format or args.image_format, 'image', 'png'), None
    else:
        image_fmt, video_fmt = None, check_format(args.format or args.video_format, 'video', 'mp4')

    process_folder(args.input, args.output, image_fmt, video_fmt, args.ratio, args.size,
                   args.quality, args.keep_metadata)


if __name__ == '__main__':
    main()
