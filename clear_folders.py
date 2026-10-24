import os
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'output_folder')
INPUT_FOLDER = os.path.join(BASE_DIR, 'input_folder')
KEEP = ('.gitkeep',)  # placeholder files that keep the folder in git


def clear_folder(folder):
    """Delete everything inside folder (files and subfolders), leaving the folder itself."""
    if not os.path.isdir(folder):
        print(f"'{os.path.basename(folder)}' not found, skipping.")
        return
    removed = 0
    for name in os.listdir(folder):
        if name in KEEP:
            continue
        path = os.path.join(folder, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except OSError as e:
            print(f"  Could not remove '{name}': {e}")
    print(f"Cleared {removed} item(s) from '{os.path.basename(folder)}'.")


def main():
    print("=== Clear Folders ===")
    clear_folder(OUTPUT_FOLDER)

    answer = input("Also clear 'input_folder'? [y/N]: ").strip().lower()
    if answer in ('y', 'yes'):
        clear_folder(INPUT_FOLDER)
    else:
        print("Left 'input_folder' untouched.")


if __name__ == '__main__':
    main()
