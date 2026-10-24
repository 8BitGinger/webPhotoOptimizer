# Web Photo / Video / Audio Optimizer

A Python script that batch-converts, crops, resizes and compresses images, **videos and audio** for the web. Drop files into `input_folder`, run the script, and collect the results from `output_folder`.

- **Images:** PNG, JPG, WebP, TIFF, BMP, GIF
- **Videos:** MP4, WebM, MOV, MKV, GIF (via ffmpeg). Animated GIFs can be used as video input
- **Audio:** MP3, M4A (AAC), Opus, FLAC (via ffmpeg)
- Center-crop to any aspect ratio, and downscale to a maximum size (never upscales)
- Lossy or lossless output with a single quality setting
- Metadata (EXIF, GPS, camera info) stripped by default for smaller, more private files
- Automatic de-interlacing for videos
- Split a video's sound into a separate audio file, leaving a silent video, or save **just the audio** with no video file
- Simple desktop **GUI**, an interactive menu, **or** command-line flags for scripting

## Requirements

- Python 3.9+
- [Pillow](https://pypi.org/project/pillow/) and [tqdm](https://pypi.org/project/tqdm/)
- ffmpeg (only needed for videos and audio)

```bash
pip install pillow tqdm
```

For ffmpeg, either [install it](https://ffmpeg.org/download.html) and make sure it is on your `PATH`, or let Python bundle one:

```bash
pip install imageio-ffmpeg
```

> `package.json` in this repo is a leftover and isn't used. Only the Python dependencies above are required.

## Quick start

Double-click **`launch_gui.bat`** to open the GUI (see [GUI](#gui)), or use the terminal:

1. Put your files in `input_folder/` (create it next to `script.py` if it doesn't exist).
2. Run the script:

   ```bash
   python script.py
   ```
3. Answer the prompts. Press Enter to accept the default shown in `[brackets]`.
4. Find the converted files in `output_folder/`.

## GUI

`launch_gui.bat` (or `python gui.py`) opens a window with the same options as the interactive menu:

- Pick the input and output folders (defaults are `input_folder` and `output_folder`); **Open** shows a folder in Explorer.
- Tick **Images**, **Videos** and/or **Audio** and choose an output type for each.
- **Video sound** (when Videos is ticked): keep it in the video, split it out into a silent video plus an audio file, or **audio only** to save just the sound with no video file. Audio taken from videos uses the Audio type.
- Set crop ratio, maximum size, quality and metadata. Choose **Custom...** to type your own value. Options that don't apply to your choices are greyed out.
- **Convert** runs in the background with a progress bar, a log, and a live percentage for the video or audio file being encoded. **Stop** cancels immediately, even mid-encode, and removes the unfinished file.

The GUI uses tkinter, which ships with Python, so it needs no extra packages.

## Interactive mode

Running with no arguments walks you through each choice:

| Prompt | Options |
| --- | --- |
| What to convert | Images, videos, audio, images and videos, or everything |
| Output file type | Image: PNG, JPG, WebP, TIFF, BMP, GIF. Video: MP4, WebM, MOV, MKV, GIF. Audio: MP3, M4A, Opus, FLAC |
| Video sound (videos only) | Keep it in the video (default), split it out into a silent video plus a separate audio file, or audio only (no video file). The audio format is the one you pick |
| Crop to aspect ratio (not asked for audio only) | Original, 1:1, 16:9, 9:16, 4:3, 3:2, 4:5, 21:9, or custom |
| Maximum output size (not asked for audio only) | Original, 4K, QHD, Full HD (default), HD, square/portrait/vertical presets, thumbnail, or custom |
| Quality | Lossless, High (95), Good (80, default), Medium (65), Low (50), or custom 1–100 |
| Keep metadata | No (default) or yes |

The quality prompt is skipped when the chosen formats ignore it (PNG, TIFF, BMP, GIF, FLAC).

## Command-line usage

Passing any argument switches to non-interactive mode.

```bash
python script.py [-i INPUT] [-o OUTPUT] [-m {image,video,audio,both,all}] [-f FORMAT]
                 [--image-format FMT] [--video-format FMT] [--audio-format FMT]
                 [-a] [--audio-only] [-r RATIO] [-s SIZE] [-q QUALITY] [-k]
```

| Flag | Description | Default |
| --- | --- | --- |
| `-i`, `--input` | Input folder | `input_folder` |
| `-o`, `--output` | Output folder (created if missing) | `output_folder` |
| `-m`, `--media` | `image`, `video`, `audio`, `both` (images and videos) or `all` | `image` |
| `-f`, `--format` | Output type when `-m` is `image`, `video` or `audio` | `png` for images, `mp4` for videos, `mp3` for audio |
| `--image-format` | Image output type when `-m both` or `all` | `png` |
| `--video-format` | Video output type when `-m both` or `all` | `mp4` |
| `--audio-format` | Audio output type when `-m all`, and for audio taken from videos with `-a` or `--audio-only` | `mp3` |
| `-a`, `--split-audio` | Remove the sound from videos and save it as a separate audio file | off |
| `--audio-only` | Save just the sound from videos as audio files and write no video. With `-m video`, `-f` sets the audio type | off |
| `-r`, `--ratio` | Center-crop to an aspect ratio, e.g. `16:9`, `1:1`, `4:5` | no crop |
| `-s`, `--size` | Maximum size as `WxH`, or `original` to keep | `1920x1080` |
| `-q`, `--quality` | `1`–`100` or `lossless` | `80` |
| `-k`, `--keep-metadata` | Keep camera info, dates and GPS location | stripped |

### Examples

Convert all images to WebP at quality 80, max 1920x1080:

```bash
python script.py -f webp
```

Square 1080px JPG thumbnails at high quality:

```bash
python script.py -f jpg -r 1:1 -s 1080x1080 -q 95
```

Lossless WebP at original size:

```bash
python script.py -f webp -s original -q lossless
```

Convert videos to web-friendly MP4:

```bash
python script.py -m video -f mp4 -s 1280x720
```

Convert images to WebP and videos to WebM in one pass, from a custom folder:

```bash
python script.py -m both --image-format webp --video-format webm -i ./photos -o ./web
```

Compress audio for the web (Opus at quality 80 is about 128 kbps):

```bash
python script.py -m audio -f opus
```

Convert animated GIFs to much smaller MP4s:

```bash
python script.py -m video -f mp4
```

Make silent MP4s and save each video's sound as an M4A next to it (`clip.mp4` + `clip.m4a`):

```bash
python script.py -m video -f mp4 -a --audio-format m4a
```

Save only the sound from videos (no video files), here as MP3:

```bash
python script.py -m video --audio-only -f mp3
```

Convert everything in one pass:

```bash
python script.py -m all --image-format webp --video-format mp4 --audio-format m4a
```

Turn a clip into an animated GIF:

```bash
python script.py -m video -f gif -s 640x360
```

## How it works

### Supported inputs

- **Images:** `.png .jpg .jpeg .jfif .tif .tiff .webp .bmp .gif`
- **Videos:** `.mp4 .mov .mkv .avi .webm .m4v .mpg .mpeg .ts .mts .m2ts .vob .wmv .flv`, plus animated `.gif`
- **Audio:** `.mp3 .wav .flac .m4a .aac .ogg .oga .opus .wma .aif .aiff .alac .amr .ape .wv`

Only files directly inside the input folder are processed (subfolders are ignored).

### Cropping and resizing

- **Crop** is always centered and applied before resizing.
- **Size** is a bounding box: the image keeps its aspect ratio and is scaled down to fit. Smaller images are never enlarged.
- Image rotation from EXIF orientation is applied before cropping, so phone photos come out the right way up.
- Video dimensions are kept even, as required by the encoders.

### Quality

| Format | Behavior |
| --- | --- |
| JPG, WebP, MP4/MOV/MKV (H.264), WebM (VP9) | Quality 1–100 controls compression. Higher is better quality and larger files. |
| `lossless` | WebP and video formats are encoded losslessly. JPG uses quality 100 with full colour detail (JPG can't be truly lossless). |
| PNG, TIFF, BMP | Always lossless, so quality has no effect. |
| GIF | Always limited to 256 colours, so quality has no effect. Video GIFs are 15 fps. |
| MP3 | Quality maps to LAME VBR presets: 95 → V0 (~245 kbps), 80 → V2 (~190 kbps), 50 → V5 (~130 kbps). `lossless` uses 320 kbps. |
| M4A (AAC) | Quality 80 → 192 kbps (max 256). `lossless` uses 256 kbps. |
| Opus | Quality 80 → 128 kbps (max 192). Best quality per byte, especially for speech. `lossless` uses 192 kbps. |
| FLAC | Always lossless, so quality has no effect. |

### Transparency

Transparent images keep their alpha channel in formats that support it (PNG, WebP, TIFF, GIF). For JPG and BMP, transparency is flattened onto a white background.

### Metadata

By default metadata such as GPS location and recording device is stripped. Use `-k` (or answer "Yes" in the menu) to keep it. Colour profiles are always preserved for images so colours don't shift (CMYK profiles are dropped, since they'd be wrong after conversion to RGB).

### Video details

- Videos are checked for interlacing and de-interlaced automatically (`bwdif`).
- MP4/MOV are encoded with `+faststart` so playback can begin before the full download finishes.
- GIF output is built with a generated palette for better colour and has no audio.
- With audio only, no video is written; each video produces just its audio file. Videos with no audio track are skipped with a note, and animated GIFs are ignored.
- With split audio, the first audio track is saved using the same settings as audio conversion (see the quality table). Videos with no audio track are still converted, with a note that no audio file was made.

### Audio details

- Only the first audio track is kept. Embedded cover art is dropped, since it's often larger than a short clip's audio.
- Sample rates the encoder doesn't support (e.g. 96 kHz into MP3) are resampled automatically.
- Browser support: MP3 and M4A play everywhere; Opus plays in Chrome, Firefox and Edge, but Safari support for `.opus` files is recent and patchy, so use M4A or MP3 if Apple devices matter.

### Animated GIFs

- **Image mode** keeps the animation when the output is GIF, WebP or PNG (APNG). JPG, BMP and TIFF keep only the first frame, with a warning.
- **Video mode** picks up animated GIFs as input. MP4/WebM is usually 5–10× smaller than the GIF; embed it with `<video autoplay loop muted playsinline>`. Transparent areas become black.

### Output naming

`photo.jpg` becomes `photo.webp` in the output folder. If several inputs would produce the same name (for example `photo.jpg` and `photo.png`), the source extension is added to each: `photo-jpg.webp` and `photo-png.webp`. Existing files in the output folder with the same name are overwritten.

## Troubleshooting

- **"ffmpeg not found"**: install ffmpeg and add it to your `PATH`, or run `pip install imageio-ffmpeg`.
- **"No image files found"**: check that your files are directly inside the input folder and have a supported extension.
- **A file fails to convert**: the error is printed and the script moves on to the next file.
- **Invalid ratio or size**: use the `W:H` form for ratios (`16:9`) and `WxH` for sizes (`1920x1080`).

## License

See [LICENSE](LICENSE).
