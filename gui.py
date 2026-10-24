import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import script

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOUND_CHOICES = [
    ('Keep it in the video', 'keep'),
    ('Split out: silent video + separate audio file', 'split'),
    ('Audio only: just the sound, no video file', 'only'),
]


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=12)
        self.root = root
        self.messages = queue.Queue()
        self.cancel_requested = threading.Event()
        self.worker = None
        self.current_file = None

        self.input_var = tk.StringVar(value=os.path.join(BASE_DIR, 'input_folder'))
        self.output_var = tk.StringVar(value=os.path.join(BASE_DIR, 'output_folder'))
        self.do_image = tk.BooleanVar(value=True)
        self.do_video = tk.BooleanVar(value=False)
        self.do_audio = tk.BooleanVar(value=False)
        self.image_fmt = tk.StringVar(value='webp')
        self.video_fmt = tk.StringVar(value='mp4')
        self.audio_fmt = tk.StringVar(value='mp3')
        self.sound = tk.StringVar(value='keep')
        self.keep_metadata = tk.BooleanVar(value=False)

        self.ratio_var = tk.StringVar(value=script.RATIO_CHOICES[0][0])
        self.size_var = tk.StringVar(value=script.SIZE_CHOICES[3][0])
        self.quality_var = tk.StringVar(value=script.QUALITY_CHOICES[2][0])
        self.ratio_custom = tk.StringVar()
        self.size_custom = tk.StringVar()
        self.quality_custom = tk.StringVar()

        self.build()
        self.update_state()
        self.grid(sticky='nsew')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.poll()

    # ---------- layout ----------

    def build(self):
        self.columnconfigure(1, weight=1)
        row = 0

        for label, var in (('Input folder', self.input_var), ('Output folder', self.output_var)):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky='w', pady=2)
            ttk.Entry(self, textvariable=var).grid(row=row, column=1, sticky='ew', padx=6, pady=2)
            ttk.Button(self, text='Browse...', command=lambda v=var: self.browse(v)).grid(row=row, column=2)
            ttk.Button(self, text='Open', command=lambda v=var: self.open_folder(v)).grid(row=row, column=3, padx=(4, 0))
            row += 1

        # What to convert
        box = ttk.LabelFrame(self, text='What to convert', padding=8)
        box.grid(row=row, column=0, columnspan=4, sticky='ew', pady=(10, 0))
        box.columnconfigure(2, weight=1)
        row += 1

        def media_row(r, text, flag, fmt_var, formats):
            ttk.Checkbutton(box, text=text, variable=flag, command=self.update_state).grid(row=r, column=0, sticky='w')
            ttk.Label(box, text='to').grid(row=r, column=1, padx=6)
            combo = ttk.Combobox(box, textvariable=fmt_var, values=[f.upper() for f in formats],
                                 state='readonly', width=8)
            combo.grid(row=r, column=2, sticky='w')
            combo.bind('<<ComboboxSelected>>', lambda e: self.update_state())
            return combo

        self.image_combo = media_row(0, 'Images', self.do_image, self.image_fmt, script.IMAGE_FORMATS)
        self.video_combo = media_row(1, 'Videos', self.do_video, self.video_fmt, script.VIDEO_FORMATS)
        self.audio_combo = media_row(2, 'Audio', self.do_audio, self.audio_fmt, script.AUDIO_FORMATS)
        for var, combo in ((self.image_fmt, self.image_combo), (self.video_fmt, self.video_combo),
                           (self.audio_fmt, self.audio_combo)):
            combo.set(var.get().upper())

        ttk.Label(box, text='Video sound').grid(row=3, column=0, sticky='w', pady=(8, 0))
        self.sound_buttons = []
        for n, (text, value) in enumerate(SOUND_CHOICES):
            rb = ttk.Radiobutton(box, text=text, value=value, variable=self.sound, command=self.update_state)
            rb.grid(row=3 + n, column=1, columnspan=3, sticky='w', pady=(8 if n == 0 else 0, 0))
            self.sound_buttons.append(rb)
        ttk.Label(box, text='Audio from videos uses the Audio type above.', foreground='gray').grid(
            row=6, column=1, columnspan=3, sticky='w')

        # Options
        opts = ttk.LabelFrame(self, text='Options', padding=8)
        opts.grid(row=row, column=0, columnspan=4, sticky='ew', pady=(10, 0))
        opts.columnconfigure(1, weight=1)
        row += 1

        self.ratio_combo, self.ratio_entry = self.option_row(
            opts, 0, 'Crop to ratio', self.ratio_var, [l for l, _ in script.RATIO_CHOICES], self.ratio_custom, 'e.g. 5:4')
        self.size_combo, self.size_entry = self.option_row(
            opts, 1, 'Maximum size', self.size_var, [l for l, _ in script.SIZE_CHOICES], self.size_custom, 'e.g. 1600x900')
        self.quality_combo, self.quality_entry = self.option_row(
            opts, 2, 'Quality', self.quality_var, [l for l, _ in script.QUALITY_CHOICES], self.quality_custom, '1-100')
        ttk.Checkbutton(opts, text='Keep metadata (camera info, date, GPS location, audio tags)',
                        variable=self.keep_metadata).grid(row=3, column=0, columnspan=3, sticky='w', pady=(6, 0))

        # Run controls
        controls = ttk.Frame(self)
        controls.grid(row=row, column=0, columnspan=4, sticky='ew', pady=(12, 0))
        controls.columnconfigure(2, weight=1)
        row += 1
        self.start_btn = ttk.Button(controls, text='Convert', command=self.start)
        self.start_btn.grid(row=0, column=0)
        self.stop_btn = ttk.Button(controls, text='Stop', command=self.cancel_requested.set, state='disabled')
        self.stop_btn.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(controls, mode='determinate')
        self.progress.grid(row=0, column=2, sticky='ew', padx=(6, 0))
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status).grid(row=row, column=0, columnspan=4, sticky='w', pady=(4, 0))
        row += 1

        log_frame = ttk.Frame(self)
        log_frame.grid(row=row, column=0, columnspan=4, sticky='nsew', pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.rowconfigure(row, weight=1)
        self.log_text = tk.Text(log_frame, height=10, state='disabled', wrap='word')
        self.log_text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.log_text['yscrollcommand'] = scroll.set

    def option_row(self, parent, r, label, var, values, custom_var, hint):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=2)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state='readonly', width=40)
        combo.grid(row=r, column=1, sticky='w', padx=6, pady=2)
        combo.bind('<<ComboboxSelected>>', lambda e: self.update_state())
        entry = ttk.Entry(parent, textvariable=custom_var, width=14)
        entry.grid(row=r, column=2, pady=2)
        ttk.Label(parent, text=hint, foreground='gray').grid(row=r, column=3, padx=(6, 0), sticky='w')
        return combo, entry

    # ---------- state ----------

    def formats(self):
        """Resolve the form into the output type for each media kind (None = not converted)."""
        video_on = self.do_video.get()
        sound = self.sound.get() if video_on else 'keep'
        image_fmt = self.image_combo.get().lower() if self.do_image.get() else None
        video_fmt = self.video_combo.get().lower() if video_on and sound != 'only' else None
        audio_fmt = self.audio_combo.get().lower() if self.do_audio.get() else None
        split_fmt = self.audio_combo.get().lower() if video_on and sound != 'keep' else None
        return image_fmt, video_fmt, audio_fmt, split_fmt, sound == 'only'

    def update_state(self):
        image_fmt, video_fmt, audio_fmt, split_fmt, audio_only = self.formats()

        def enable(widget, on, readonly=False):
            widget.configure(state=('readonly' if readonly else 'normal') if on else 'disabled')

        enable(self.image_combo, self.do_image.get(), readonly=True)
        enable(self.video_combo, self.do_video.get() and not audio_only, readonly=True)
        enable(self.audio_combo, self.do_audio.get() or split_fmt is not None, readonly=True)
        for rb in self.sound_buttons:
            rb.configure(state='normal' if self.do_video.get() else 'disabled')

        # Cropping and resizing only apply when images or video are written
        visual = bool(image_fmt or video_fmt)
        for combo, entry in ((self.ratio_combo, self.ratio_entry), (self.size_combo, self.size_entry)):
            enable(combo, visual, readonly=True)
            enable(entry, visual and combo.get() == 'Custom...')

        # Quality is ignored by always-lossless formats
        uses_quality = any(f and f not in script.FIXED_QUALITY_FORMATS
                           for f in (image_fmt, video_fmt, audio_fmt, split_fmt))
        enable(self.quality_combo, uses_quality, readonly=True)
        enable(self.quality_entry, uses_quality and self.quality_combo.get() == 'Custom...')

    def read_choice(self, combo, choices, custom_var, parser):
        label = combo.get()
        value = dict(choices)[label]
        if value is script.CUSTOM:
            value = parser(custom_var.get())
        return value

    def collect_settings(self):
        image_fmt, video_fmt, audio_fmt, split_fmt, audio_only = self.formats()
        if not (image_fmt or video_fmt or audio_fmt or audio_only):
            raise ValueError("Tick at least one of Images, Videos or Audio.")

        ratio = size = None
        quality = script.LOSSLESS
        if str(self.ratio_combo.cget('state')) != 'disabled':
            ratio = self.read_choice(self.ratio_combo, script.RATIO_CHOICES, self.ratio_custom, script.parse_ratio)
            size = self.read_choice(self.size_combo, script.SIZE_CHOICES, self.size_custom, script.parse_size)
        if str(self.quality_combo.cget('state')) != 'disabled':
            quality = self.read_choice(self.quality_combo, script.QUALITY_CHOICES, self.quality_custom,
                                       script.parse_quality)

        return dict(input_folder=self.input_var.get(), output_folder=self.output_var.get(),
                    image_fmt=image_fmt, video_fmt=video_fmt, audio_fmt=audio_fmt, ratio=ratio, size=size,
                    quality=quality, keep_metadata=self.keep_metadata.get(), split_audio_fmt=split_fmt,
                    audio_only=audio_only)

    # ---------- actions ----------

    def browse(self, var):
        folder = filedialog.askdirectory(initialdir=var.get() if os.path.isdir(var.get()) else BASE_DIR)
        if folder:
            var.set(os.path.normpath(folder))

    def open_folder(self, var):
        folder = var.get()
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def log(self, text):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def start(self):
        try:
            settings = self.collect_settings()
        except ValueError as e:
            messagebox.showerror('Check your settings', str(e))
            return
        if not os.path.isdir(settings['input_folder']):
            messagebox.showerror('Input folder not found', f"'{settings['input_folder']}' does not exist.")
            return

        self.cancel_requested.clear()
        self.current_file = None
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress.configure(value=0)
        self.log('Starting...')

        def run():
            try:
                script.process_folder(
                    **settings,
                    log=lambda m: self.messages.put(('log', m)),
                    progress=lambda *args: self.messages.put(('progress', *args)),
                    cancel=self.cancel_requested.is_set)
                self.messages.put(('done',))
            except Exception as e:
                self.messages.put(('error', str(e)))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def poll(self):
        """Drain messages from the worker thread; Tk widgets must only be touched from this thread."""
        try:
            while True:
                kind, *data = self.messages.get_nowait()
                if kind == 'log':
                    self.log(data[0])
                elif kind == 'progress':
                    done, total, current, fraction = data
                    self.progress.configure(maximum=max(total, 1), value=done + (fraction or 0))
                    if current:  # large videos can take minutes, so show what is being worked on
                        percent = f" - {fraction:.0%}" if fraction is not None else ''
                        self.status.set(f"Converting {done + 1} of {total}: {current}{percent}")
                        if current != self.current_file:
                            self.log(f"Converting {current}")
                            self.current_file = current
                else:
                    self.status.set('')
                    if kind == 'error':
                        self.log(f'Error: {data[0]}')
                    elif not self.cancel_requested.is_set():  # a stop already logged that it stopped
                        self.log('Finished.')
                    self.start_btn.configure(state='normal')
                    self.stop_btn.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    root.title('Web Photo / Video / Audio Optimizer')
    root.minsize(640, 600)
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
