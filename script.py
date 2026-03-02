import os
from PIL import Image
from tqdm import tqdm

def optimize_images(input_folder, output_folder, max_size=(1920, 540), quality=80):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Get a list of all valid image files first
    files = [f for f in os.listdir(input_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff'))]

    # Wrap the loop in tqdm for the progress bar
    # 'desc' sets the text prefix, 'unit' labels the items
    for filename in tqdm(files, desc="Optimizing Photos", unit="photo"):
        img_path = os.path.join(input_folder, filename)
        
        try:
            with Image.open(img_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")

                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                base_name = os.path.splitext(filename)[0]
                output_path = os.path.join(output_folder, f"{base_name}.webp")
                
                img.save(output_path, "WEBP", quality=quality, method=6)
        except Exception as e:
            print(f"Error processing {filename}: {e}")

# Usage
optimize_images('input_folder', 'output_folder')