"""Visual crop editor: a fixed crop frame floats over the picture, and you move, zoom, flip and tilt
the picture underneath it until the frame holds exactly what you want."""
import math
import os
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

import script

PROXY_SIDE = 2400  # largest preview kept in memory; the real crop always uses the full image
MARGIN = 40        # gap around the crop frame, so the picture outside it stays visible
MAX_ZOOM = 10
CHECKER = 12       # size of the squares shown behind transparent areas


def hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def checkerboard(size):
    board = Image.new('RGB', size, (200, 200, 200))
    dark = Image.new('RGB', (CHECKER, CHECKER), (150, 150, 150))
    for y in range(0, size[1], CHECKER):
        for x in range((y // CHECKER) % 2 * CHECKER, size[0], CHECKER * 2):
            board.paste(dark, (x, y))
    return board


class CropEditor(tk.Toplevel):
    def __init__(self, master, folder, files, crops, ratio_label, ratio_custom, theme, on_change):
        super().__init__(master, bg=theme['bg'])
        self.title('Visual crop')
        self.geometry('1100x760')
        self.minsize(760, 520)
        self.folder, self.files, self.crops = folder, files, crops
        self.theme, self.on_change = theme, on_change
        self.bg_rgb = hex_rgb(theme['bg'])
        self.ratio_var = tk.StringVar(value=ratio_label)
        self.custom_var = tk.StringVar(value=ratio_custom)
        self.tilt_var = tk.DoubleVar(value=0)
        self.zoom_var = tk.DoubleVar(value=0)
        self.info = tk.StringVar()
        self.photo = None
        self.levels = []
        self.drag = None
        self.redraw_pending = False
        self.index = None

        self.build()
        self.bind('<Escape>', lambda e: self.destroy())
        self.after_idle(lambda: self.select(0))

    # ---------- layout ----------

    def build(self):
        t = self.theme
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side = ttk.Frame(self, padding=(10, 10, 0, 10))
        side.grid(row=0, column=0, rowspan=2, sticky='ns')
        side.rowconfigure(1, weight=1)
        ttk.Label(side, text='Images  (✓ = adjusted)').grid(row=0, column=0, sticky='w', pady=(0, 4))
        self.listbox = tk.Listbox(side, width=28, activestyle='none', exportselection=False, bg=t['surface'],
                                  fg=t['fg'], selectbackground=t['accent'], selectforeground='#ffffff',
                                  relief='flat', highlightthickness=1, highlightbackground=t['border'],
                                  highlightcolor=t['border'])
        self.listbox.grid(row=1, column=0, sticky='ns')
        self.listbox.bind('<<ListboxSelect>>', lambda e: self.listbox.curselection()
                          and self.select(self.listbox.curselection()[0]))
        nav = ttk.Frame(side)
        nav.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        ttk.Button(nav, text='< Prev', command=lambda: self.step(-1)).pack(side='left')
        ttk.Button(nav, text='Next >', command=lambda: self.step(1)).pack(side='right')
        self.refresh_list()

        self.canvas = tk.Canvas(self, bg=t['bg'], highlightthickness=0, cursor='fleur')
        self.canvas.grid(row=0, column=1, sticky='nsew', padx=10, pady=(10, 0))
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', lambda e: setattr(self, 'drag', None))
        self.canvas.bind('<MouseWheel>', self.wheel)  # touchpad pinch arrives as Ctrl+wheel, handled the same
        self.canvas.bind('<Double-Button-1>', lambda e: self.reset())

        bar = ttk.Frame(self, padding=(10, 8, 10, 10))
        bar.grid(row=1, column=1, sticky='ew')

        row1 = ttk.Frame(bar)
        row1.pack(fill='x')
        ttk.Label(row1, text='Ratio').pack(side='left')
        combo = ttk.Combobox(row1, textvariable=self.ratio_var, state='readonly', width=26,
                             values=[label for label, _ in script.RATIO_CHOICES])
        combo.pack(side='left', padx=6)
        combo.bind('<<ComboboxSelected>>', lambda e: self.ratio_changed())
        self.custom_entry = ttk.Entry(row1, textvariable=self.custom_var, width=8)
        self.custom_entry.pack(side='left')
        self.custom_entry.bind('<Return>', lambda e: self.ratio_changed())
        self.custom_entry.bind('<FocusOut>', lambda e: self.ratio_changed())
        ttk.Label(row1, text='e.g. 5:4', style='Hint.TLabel').pack(side='left', padx=(4, 16))

        for text, op in (('Flip ↔', 'flip_h'), ('Flip ↕', 'flip_v'), ('Rotate ⟲', 'rotate_ccw'),
                         ('Rotate ⟳', 'rotate_cw')):
            ttk.Button(row1, text=text, command=lambda o=op: self.orient(o)).pack(side='left', padx=(0, 4))
        ttk.Button(row1, text='Done', command=self.destroy).pack(side='right')
        ttk.Button(row1, text='Reset', command=self.reset).pack(side='right', padx=6)

        row2 = ttk.Frame(bar)
        row2.pack(fill='x', pady=(8, 0))
        ttk.Label(row2, text='Zoom').pack(side='left')
        ttk.Scale(row2, from_=0, to=1, variable=self.zoom_var, length=200,
                  command=lambda v: self.zoom_to(MAX_ZOOM ** float(v))).pack(side='left', padx=6)
        ttk.Label(row2, text='Tilt').pack(side='left', padx=(16, 0))
        tilt = ttk.Scale(row2, from_=-45, to=45, variable=self.tilt_var, length=260,
                         command=lambda v: self.tilt_to(round(float(v) * 2) / 2))
        tilt.pack(side='left', padx=6)
        tilt.bind('<Double-Button-1>', lambda e: self.after_idle(lambda: self.tilt_to(0)))
        for text, step in (('−', -0.5), ('+', 0.5)):
            ttk.Button(row2, text=text, width=2,
                       command=lambda s=step: self.tilt_to(self.angle + s)).pack(side='left', padx=(0, 2))

        row3 = ttk.Frame(bar)
        row3.pack(fill='x', pady=(6, 0))
        ttk.Label(row3, textvariable=self.info).pack(side='right')
        ttk.Label(row3, style='Hint.TLabel',
                  text='Drag the picture to move it  ·  scroll to zoom at the pointer  ·  '
                       'double-click the tilt slider to straighten  ·  double-click the picture to reset'
                  ).pack(side='left')

    def refresh_list(self):
        selected = self.listbox.curselection()
        self.listbox.delete(0, 'end')
        for name in self.files:
            self.listbox.insert('end', ('✓ ' if name in self.crops else '    ') + name)
        for i in selected:
            self.listbox.selection_set(i)

    # ---------- loading ----------

    def step(self, delta):
        if self.index is not None:
            self.select((self.index + delta) % len(self.files))

    def select(self, index):
        self.index = index
        self.listbox.selection_clear(0, 'end')
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self.name = self.files[index]
        try:
            img, self.full = script.load_preview(os.path.join(self.folder, self.name), PROXY_SIDE)
        except Exception as e:
            self.levels = []
            self.canvas.delete('all')
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text=f"Can't preview {self.name}:\n{e}", fill=self.theme['fg'])
            return
        if img.getchannel('A').getextrema()[0] < 255:  # show transparency as a checkerboard
            board = checkerboard(img.size)
            board.paste(img, mask=img.getchannel('A'))
            img = board
        self.base = img.convert('RGB')

        saved = self.crops.get(self.name)
        if saved:
            self.ratio_var.set(saved['ratio_label'])
            self.custom_var.set(saved['ratio_custom'])
        self.ops = []
        self.build_levels()
        for op in (saved or {}).get('ops', ()):
            self.apply_op(op)
        self.angle = saved['angle'] if saved else 0
        self.zoom = saved['zoom'] if saved else 1
        self.cx = saved['cx'] * self.W if saved else self.W / 2
        self.cy = saved['cy'] * self.H if saved else self.H / 2
        self.sync_controls()
        self.redraw()

    def build_levels(self):
        """Half-size copies of the preview, so drawing never shrinks a picture more than 2x at once."""
        self.levels = [self.base]
        while max(self.levels[-1].size) > 300:
            self.levels.append(self.levels[-1].reduce(2))
        self.W, self.H = self.full

    # ---------- geometry (all in full-resolution pixels of the oriented image) ----------

    def ratio(self):
        value = dict(script.RATIO_CHOICES).get(self.ratio_var.get())
        if value is script.CUSTOM:
            try:
                value = script.parse_ratio(self.custom_var.get())
            except ValueError:
                value = None
        return value[0] / value[1] if value else self.W / self.H

    def crop_size(self):
        """Crop size for the current ratio, zoom and tilt. A tilted frame is shrunk until it fits
        inside the picture, so the output never has empty corners."""
        r = self.ratio()
        cw = min(self.W, self.H * r) / self.zoom
        ch = cw / r
        ex, ey = self.extent(cw, ch)
        k = min(1, self.W / (2 * ex), self.H / (2 * ey))
        return cw * k, ch * k

    def extent(self, cw, ch):
        """Half width and height of the tilted crop's bounding box."""
        cos, sin = (abs(v) for v in script.crop_rotation(self.angle))
        return (cw * cos + ch * sin) / 2, (cw * sin + ch * cos) / 2

    def clamp(self):
        """Keep the crop inside the picture. Its corners are inside exactly when its bounding box is."""
        ex, ey = self.extent(*self.crop_size())
        self.cx = min(max(self.cx, ex), self.W - ex)
        self.cy = min(max(self.cy, ey), self.H - ey)

    def view(self):
        """(canvas width, height, frame width, height, display pixels per image pixel)."""
        wc, hc = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        cw, ch = self.crop_size()
        fw = min(wc - 2 * MARGIN, (hc - 2 * MARGIN) * cw / ch)
        fw = max(fw, 10)
        return wc, hc, fw, fw * ch / cw, fw / cw

    def to_image(self, x, y):
        """Canvas point -> image point."""
        wc, hc, _, _, s = self.view()
        cos, sin = script.crop_rotation(self.angle)
        dx, dy = (x - wc / 2) / s, (y - hc / 2) / s
        return self.cx + dx * cos - dy * sin, self.cy + dx * sin + dy * cos

    # ---------- edits ----------

    def edited(self):
        self.clamp()
        cw, ch = self.crop_size()
        self.crops[self.name] = dict(ops=list(self.ops), angle=self.angle, zoom=self.zoom,
                                     cx=self.cx / self.W, cy=self.cy / self.H, cw=cw / self.W, ch=ch / self.H,
                                     ratio_label=self.ratio_var.get(), ratio_custom=self.custom_var.get())
        if self.listbox.get(self.index)[0] != '✓':
            self.refresh_list()
            self.on_change()
        self.redraw()

    def press(self, event):
        self.drag = (event.x, event.y)

    def move(self, event):
        if not self.drag or not self.levels:
            return
        # The picture follows the pointer, so the crop centre moves the opposite way
        _, _, _, _, s = self.view()
        cos, sin = script.crop_rotation(self.angle)
        dx, dy = (event.x - self.drag[0]) / s, (event.y - self.drag[1]) / s
        self.cx -= dx * cos - dy * sin
        self.cy -= dx * sin + dy * cos
        self.drag = (event.x, event.y)
        self.edited()

    def wheel(self, event):
        if self.levels:
            self.zoom_to(self.zoom * 1.15 ** (event.delta / 120), (event.x, event.y))

    def zoom_to(self, zoom, anchor=None):
        """Zoom, keeping the picture under `anchor` (a canvas point, default the frame centre) still."""
        if not self.levels:
            return
        zoom = min(max(zoom, 1), MAX_ZOOM)
        if anchor:
            px, py = self.to_image(*anchor)
        self.zoom = zoom
        if anchor:
            wc, hc, _, _, s = self.view()
            cos, sin = script.crop_rotation(self.angle)
            dx, dy = (anchor[0] - wc / 2) / s, (anchor[1] - hc / 2) / s
            self.cx, self.cy = px - (dx * cos - dy * sin), py - (dx * sin + dy * cos)
        self.sync_controls()
        self.edited()

    def tilt_to(self, angle):
        if not self.levels:
            return
        self.angle = min(max(angle, -45), 45)
        self.sync_controls()
        self.edited()

    def ratio_changed(self):
        self.custom_entry.configure(state='normal' if self.ratio_var.get() == 'Custom...' else 'disabled')
        if self.levels:
            self.edited()

    def apply_op(self, op):
        """Flip or turn the preview, and move the crop centre with the picture."""
        w, h = self.W, self.H
        transpose = script.ORIENT_OPS[op]
        self.levels = [level.transpose(transpose) for level in self.levels]
        if hasattr(self, 'cx'):
            if op == 'flip_h':
                self.cx, self.angle = w - self.cx, -self.angle
            elif op == 'flip_v':
                self.cy, self.angle = h - self.cy, -self.angle
            elif op == 'rotate_cw':
                self.cx, self.cy = h - self.cy, self.cx
            else:
                self.cx, self.cy = self.cy, w - self.cx
        if op.startswith('rotate'):
            self.W, self.H = h, w
        inverse = {'flip_h': 'flip_h', 'flip_v': 'flip_v', 'rotate_cw': 'rotate_ccw', 'rotate_ccw': 'rotate_cw'}
        if self.ops and self.ops[-1] == inverse[op]:
            self.ops.pop()
        else:
            self.ops.append(op)

    def orient(self, op):
        if self.levels:
            self.apply_op(op)
            self.sync_controls()
            self.edited()

    def reset(self):
        if self.index is None:
            return
        if self.crops.pop(self.name, None):
            self.refresh_list()
            self.on_change()
        self.select(self.index)

    def sync_controls(self):
        self.tilt_var.set(self.angle)
        self.zoom_var.set(math.log(self.zoom, MAX_ZOOM))
        self.custom_entry.configure(state='normal' if self.ratio_var.get() == 'Custom...' else 'disabled')

    # ---------- drawing ----------

    def redraw(self):
        if not self.redraw_pending:  # coalesce bursts of drag/wheel events into one frame
            self.redraw_pending = True
            self.after_idle(self.draw)

    def draw(self):
        self.redraw_pending = False
        if not self.levels or not self.winfo_exists():
            return
        self.clamp()
        wc, hc, fw, fh, s = self.view()
        # The smallest preview that still has at least one pixel per screen pixel
        level = next((lv for lv in reversed(self.levels) if lv.width / self.W >= s), self.levels[0])
        p = level.width / self.W
        cos, sin = script.crop_rotation(self.angle)
        a, b, d, e = p * cos / s, -p * sin / s, p * sin / s, p * cos / s
        data = (a, b, p * self.cx - a * wc / 2 - b * hc / 2, d, e, p * self.cy - d * wc / 2 - e * hc / 2)
        frame = Image.new('RGB', (wc, hc), self.bg_rgb)
        picture = level.transform((wc, hc), Image.Transform.AFFINE, data, Image.Resampling.BILINEAR,
                                  fillcolor=self.bg_rgb)
        box = (round((wc - fw) / 2), round((hc - fh) / 2), round((wc + fw) / 2), round((hc + fh) / 2))
        inside = picture.crop(box)
        picture = Image.blend(picture, frame, 0.6)  # dim everything outside the crop
        picture.paste(inside, box[:2])
        self.photo = ImageTk.PhotoImage(picture)

        c = self.canvas
        c.delete('all')
        c.create_image(0, 0, anchor='nw', image=self.photo)
        x0, y0, x1, y1 = box
        for i in (1, 2):  # rule-of-thirds guides
            x, y = x0 + (x1 - x0) * i / 3, y0 + (y1 - y0) * i / 3
            c.create_line(x, y0, x, y1, fill='#ffffff', stipple='gray50')
            c.create_line(x0, y, x1, y, fill='#ffffff', stipple='gray50')
        c.create_rectangle(x0, y0, x1, y1, outline='#ffffff', width=2)
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            c.create_line(cx, cy + 18 * sy, cx, cy, cx + 18 * sx, cy, fill=self.theme['accent'], width=4)

        cw, ch = self.crop_size()
        self.info.set(f"crop {round(cw)} × {round(ch)} px   zoom {self.zoom:.1f}×   "
                      f"tilt {self.angle:+.1f}°")
