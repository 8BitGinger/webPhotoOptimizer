import os

try:
    from PIL import Image, ImageSequence
    from tqdm import tqdm
except ImportError as exc:
    raise SystemExit(
        "Missing Python dependency. Install it with: python -m pip install pillow tqdm"
    ) from exc


def _process_frame(frame, max_size):
    frame = frame.convert("RGBA") if frame.mode in ("RGBA", "P") else frame.convert("RGB")
    frame.thumbnail(max_size, Image.Resampling.LANCZOS)
    return frame


def optimize_images(input_folder, output_folder, max_size=(500, 500), quality=80):
    if not os.path.isdir(input_folder):
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    files = [
        f for f in os.listdir(input_folder)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'))
    ]

    if not files:
        print(f"No supported image files found in '{input_folder}'. Add PNG, JPG, JPEG, TIFF, BMP, or GIF files and run again.")
        return

    for filename in tqdm(files, desc="Optimizing Photos", unit="photo"):
        img_path = os.path.join(input_folder, filename)

        try:
            with Image.open(img_path) as img:
                base_name = os.path.splitext(filename)[0]
                output_path = os.path.join(output_folder, f"{base_name}.webp")

                if getattr(img, "is_animated", False):
                    # animated GIF -> animated WEBP, keeping per-frame timing and loop count
                    frames = [_process_frame(frame.copy(), max_size) for frame in ImageSequence.Iterator(img)]
                    frames[0].save(
                        output_path, "WEBP", save_all=True, append_images=frames[1:],
                        duration=img.info.get("duration", 100), loop=img.info.get("loop", 0),
                        quality=quality, method=6,
                    )
                else:
                    frame = _process_frame(img, max_size)
                    frame.save(output_path, "WEBP", quality=quality, method=6)
        except Exception as exc:
            print(f"Error processing {filename}: {exc}")


if __name__ == "__main__":
    optimize_images('input_folder', 'output_folder')