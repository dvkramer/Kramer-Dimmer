import tkinter as tk
from tkinter import ttk
import ctypes
from ctypes import windll, byref, Structure, c_long
from screeninfo import get_monitors
import threading
import pystray
from PIL import Image, ImageDraw, ImageTk
import sys
import os
import winreg
import atexit
import subprocess
import webbrowser
import keyboard

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

class RAMP(Structure):
    _fields_ = [("Red", ctypes.c_uint16 * 256),
                ("Green", ctypes.c_uint16 * 256),
                ("Blue", ctypes.c_uint16 * 256)]

class RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]

def get_real_monitor_names():
    names = []
    try:
        cmd = r"""
        Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID | ForEach-Object { 
            $name = [System.Text.Encoding]::ASCII.GetString($_.UserFriendlyName).Trim([char]0)
            Write-Output $name
        }
        """
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        process = subprocess.Popen(["powershell", "-Command", cmd], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   startupinfo=startupinfo,
                                   text=True)
        out, err = process.communicate()
        if out:
            lines = out.strip().split('\n')
            names = [line.strip() for line in lines if line.strip()]
    except Exception as e:
        print(f"Error fetching WMI names: {e}")
    return names

class GammaController:
    def __init__(self):
        self.monitor_dcs = [] 
        self.init_monitors()
        atexit.register(self.restore_all)

    def init_monitors(self):
        self.restore_all()
        try:
            monitors = get_monitors()
            real_names = get_real_monitor_names()
        except: return

        for i, m in enumerate(monitors):
            hdc = windll.gdi32.CreateDCW(None, m.name, None, None)
            if hdc:
                original = RAMP()
                if windll.gdi32.GetDeviceGammaRamp(hdc, byref(original)):
                    friendly_name = "Generic Monitor"
                    if i < len(real_names):
                        friendly_name = real_names[i]
                    
                    self.monitor_dcs.append({
                        'hdc': hdc,
                        'orig': original,
                        'name': m.name,
                        'friendly_name': friendly_name
                    })

    # This function sets brightness directly (0=Dark, 100=Bright)
    def set_brightness(self, monitor_index, brightness_percent):
        if brightness_percent < 0: brightness_percent = 0
        if brightness_percent > 100: brightness_percent = 100 
        
        # Gamma calculation (Multiplier 0.0 to 1.0)
        multiplier = brightness_percent / 100.0

        new_ramp = RAMP()
        for i in range(256):
            val = int(i * 256 * multiplier)
            if val > 65535: val = 65535
            new_ramp.Red[i] = val
            new_ramp.Green[i] = val
            new_ramp.Blue[i] = val

        if monitor_index == -1:
            for m in self.monitor_dcs:
                windll.gdi32.SetDeviceGammaRamp(m['hdc'], byref(new_ramp))
        else:
            if 0 <= monitor_index < len(self.monitor_dcs):
                hdc = self.monitor_dcs[monitor_index]['hdc']
                windll.gdi32.SetDeviceGammaRamp(hdc, byref(new_ramp))

    def restore_all(self):
        for m in self.monitor_dcs:
            try:
                windll.gdi32.SetDeviceGammaRamp(m['hdc'], byref(m['orig']))
                windll.gdi32.DeleteDC(m['hdc'])
            except: pass
        self.monitor_dcs.clear()

class HyperOverlay:
    def __init__(self, root):
        self.root = root
        self.windows = []
        self.active = False
        self.current_alpha = 0.0

    def get_work_area(self):
        rect = RECT()
        windll.user32.SystemParametersInfoW(48, 0, byref(rect), 0)
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def update(self, active, brightness_percent):
        self.active = active
        # Invert: Lower brightness = Higher Alpha (Darker Overlay)
        # 100% Bright = 0.0 Alpha
        # 0% Bright = 0.98 Alpha
        dim_percent = 100 - brightness_percent
        alpha = (dim_percent / 100.0) * 0.98 
        self.current_alpha = alpha

        if not active:
            self.destroy_overlays()
            return

        if not self.windows:
            self.create_overlays()
        
        for win in self.windows:
            win.attributes('-alpha', alpha)

    def create_overlays(self):
        monitors = get_monitors()
        work_x, work_y, work_w, work_h = self.get_work_area()

        for i, m in enumerate(monitors):
            top = tk.Toplevel(self.root)
            top.title("KramerOverlay")
            top.configure(bg='black')
            top.overrideredirect(True)
            top.update() 

            if m.x == 0 and m.y == 0: 
                top.geometry(f"{work_w}x{work_h}+{work_x}+{work_y}")
            else:
                top.geometry(f"{m.width}x{m.height}+{m.x}+{m.y}")
            
            top.attributes('-topmost', True)
            top.attributes('-alpha', self.current_alpha)

            try:
                hwnd = windll.user32.GetParent(top.winfo_id())
                if hwnd == 0: hwnd = top.winfo_id()
                old_style = windll.user32.GetWindowLongW(hwnd, -20)
                new_style = old_style | 0x80000 | 0x20
                windll.user32.SetWindowLongW(hwnd, -20, new_style)
            except Exception as e:
                print(f"Overlay Error: {e}")

            self.windows.append(top)

    def destroy_overlays(self):
        for win in self.windows:
            try: win.destroy()
            except: pass
        self.windows.clear()

class ModernSlider(tk.Canvas):
    def __init__(self, master, from_=0, to=100, command=None, 
                 track_active_col="#60cdff", track_rem_col="#000000", 
                 thumb_fill_col="#2d2d2d", thumb_border_col="#60cdff", 
                 **kwargs):
        super().__init__(master, height=35, highlightthickness=0, **kwargs)
        self.from_ = from_
        self.to = to
        self.command = command
        self.value = to # Default to max (100% Brightness)
        
        # Logic inverted visually: "Active" color should be on the left (0 to Value)
        self.col_track_active = track_active_col  
        self.col_track_rem = track_rem_col     
        self.col_thumb_fill = thumb_fill_col
        self.col_thumb_border = thumb_border_col

        self.padding = 15
        self.track_height = 6
        self.thumb_radius = 10
        self.thumb_img = self._create_smooth_thumb()

        self.bind("<Configure>", self.draw)
        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)

    def _create_smooth_thumb(self):
        scale = 4
        r = self.thumb_radius
        size = r * 2 * scale
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        border_w = 2 * scale
        draw.ellipse((0, 0, size, size), fill=self.col_thumb_fill, outline=self.col_thumb_border, width=border_w)
        img = img.resize((r * 2, r * 2), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    
    def set_accent_color(self, color):
        self.col_track_active = color # Brightness bar is the colored part
        self.col_thumb_border = color
        self.thumb_img = self._create_smooth_thumb()
        self.draw()

    def val_to_x(self, val):
        w = self.winfo_width()
        range_val = self.to - self.from_
        percent = (val - self.from_) / range_val
        return self.padding + percent * (w - 2 * self.padding)

    def x_to_val(self, x):
        w = self.winfo_width()
        usable_w = w - 2 * self.padding
        if usable_w <= 0: return 0
        rel_x = x - self.padding
        percent = rel_x / usable_w
        val = self.from_ + percent * (self.to - self.from_)
        if val < self.from_: val = self.from_
        if val > self.to: val = self.to
        return val

    def set(self, val):
        self.value = val
        self.draw()

    def draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        cy = h / 2
        x_val = self.val_to_x(self.value)
        
        # Track Background (The "Empty" part on the right)
        self.create_line(self.padding, cy, w - self.padding, cy, 
                         fill=self.col_track_rem, width=self.track_height, capstyle=tk.ROUND)
        
        # Active Track (The "Bright" part on the left)
        if x_val > self.padding:
            self.create_line(self.padding, cy, x_val, cy, 
                             fill=self.col_track_active, width=self.track_height, capstyle=tk.ROUND)
                             
        self.create_image(x_val, cy, image=self.thumb_img, anchor='center')

    def on_click(self, event):
        val = self.x_to_val(event.x)
        self.set(val)
        if self.command: self.command(val)

    def on_drag(self, event):
        val = self.x_to_val(event.x)
        self.set(val)
        if self.command: self.command(val)

class BrightnessOSD:
    def __init__(self, master):
        self.master = master
        self.window = None
        self.hide_job = None
        self.bar_width = 300
        self.bar_height = 15

    def show(self, level):
        if self.window is None or not self.window.winfo_exists():
            self.create_window()
        
        self.window.deiconify()
        self.draw_bar(level)
        self.window.lift()

        if self.hide_job:
            self.master.after_cancel(self.hide_job)
        self.hide_job = self.master.after(3000, self.hide)

    def create_window(self):
        self.window = tk.Toplevel(self.master)
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.attributes('-alpha', 0.9)
        self.window.config(bg="#202020")
        
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        w, h = 320, 50
        x = (sw - w) // 2
        y = sh - 150
        self.window.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(self.window, width=w, height=h, bg="#202020", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

    def draw_bar(self, level):
        self.canvas.delete("all")
        cx = 160 
        cy = 25  
        
        self.canvas.create_text(cx, cy - 15, text=f"Brightness: {int(level)}%", 
                                fill="white", font=("Montserrat", 10, "bold"))
        
        bg_x1 = cx - (self.bar_width // 2)
        bg_y1 = cy + 5
        bg_x2 = bg_x1 + self.bar_width
        bg_y2 = bg_y1 + self.bar_height
        self.canvas.create_rectangle(bg_x1, bg_y1, bg_x2, bg_y2, fill="#404040", outline="")

        fill_width = (level / 100) * self.bar_width
        if fill_width > 0:
            self.canvas.create_rectangle(bg_x1, bg_y1, bg_x1 + fill_width, bg_y2, 
                                         fill="#60cdff", outline="")

    def hide(self):
        if self.window:
            self.window.withdraw()

class DimmerApp:
    def __init__(self, root):
        self.root = root
        self.gamma = GammaController()
        self.overlay = HyperOverlay(root)
        
        self.MAX_BRIGHT = 100
        self.DEFAULT_BRIGHT = 100 # Default to Full Brightness 
        self.is_updating = False
        
        self.colors = {
            "bg": "#202020",
            "surface": "#2d2d2d",
            "accent": "#60cdff", 
            "hyper": "#ff4d4d",
            "text": "#ffffff",
            "text_dim": "#a0a0a0",
            "disabled": "#404040",
        }
        
        self.setup_fonts()
        self.setup_window()
        self.setup_styles()
        self.setup_tray()
        self.setup_ui()
        
        self.osd = BrightnessOSD(root)

        self.root.after(100, self.apply_default_brightness)
        
        self.root.bind("<FocusOut>", self.on_focus_out)
        self.root.bind('<Control-q>', lambda e: self.quit_app())

    def setup_fonts(self):
        self.font_main = ("Montserrat", 10)
        self.font_header = ("Montserrat", 14, "bold")
        self.font_title = ("Montserrat", 13, "bold")
        self.font_small = ("Montserrat", 9)
        self.font_italic = ("Montserrat", 8, "italic")

    def setup_window(self):
        self.root.title("Kramer Dimmer")
        self.root.configure(bg=self.colors["bg"])
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.withdraw()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('default')
        style.configure("Win.TFrame", background=self.colors["bg"])
        style.configure("Sub.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=self.font_main)
        style.configure("Dim.TLabel", background=self.colors["bg"], foreground=self.colors["text_dim"], font=self.font_small)
        style.configure("Disabled.TLabel", background=self.colors["bg"], foreground=self.colors["text_dim"], font=self.font_main)

    def setup_ui(self):
        title_bar = tk.Frame(self.root, bg=self.colors["bg"], height=40)
        title_bar.pack(fill='x', pady=5)
        
        self.title_lbl = tk.Label(title_bar, text="Kramer", bg=self.colors["bg"], fg=self.colors["text"], 
                 font=self.font_title)
        self.title_lbl.pack(side='left', padx=15)
        
        close_btn = tk.Button(title_bar, text="✕", bg=self.colors["bg"], fg=self.colors["text"], 
                              bd=0, activebackground="#c42b1c", activeforeground="white", 
                              command=self.quit_app, font=("Arial", 11)) 
        close_btn.pack(side='right', padx=(5, 10))
        
        min_btn = tk.Button(title_bar, text="—", bg=self.colors["bg"], fg=self.colors["text"], 
                              bd=0, activebackground=self.colors["surface"], activeforeground="white", 
                              command=self.hide_to_tray, font=("Arial", 11, "bold")) 
        min_btn.pack(side='right', padx=0)

        min_btn.bind("<Enter>", lambda e: min_btn.config(bg=self.colors["surface"]))
        min_btn.bind("<Leave>", lambda e: min_btn.config(bg=self.colors["bg"]))
        close_btn.bind("<Enter>", lambda e: close_btn.config(bg="#c42b1c", fg="white"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(bg=self.colors["bg"], fg=self.colors["text"]))
        
        title_bar.bind('<Button-1>', self.start_move)
        title_bar.bind('<B1-Motion>', self.do_move)

        self.container = ttk.Frame(self.root, style="Win.TFrame")
        self.container.pack(fill='both', expand=True, padx=15, pady=5)

        mon_count = len(self.gamma.monitor_dcs)
        self.monitor_controls = [] 
        
        self.create_master_control(enabled=(mon_count > 1))
        ttk.Separator(self.container, orient='horizontal').pack(fill='x', pady=15)
        self.create_monitor_list()
        self.create_footer()
        
        req_height = 420 + (mon_count * 65) + 120
        if req_height > 1200: req_height = 1200
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"800x{req_height}+{sw-820}+{sh-req_height-60}")

    def create_master_control(self, enabled=True):
        frame = ttk.Frame(self.container, style="Win.TFrame")
        frame.pack(fill='x')
        header = ttk.Frame(frame, style="Win.TFrame")
        header.pack(fill='x', pady=(0, 5))
        
        lbl_style = "Sub.TLabel" if enabled else "Disabled.TLabel"
        ttk.Label(header, text="Master Brightness", style=lbl_style).pack(side='left')
        
        self.lbl_master_val = ttk.Label(header, text="100%", style="Dim.TLabel", cursor="xterm")
        self.lbl_master_val.pack(side='right')
        
        if enabled:
            self.lbl_master_val.bind("<Double-Button-1>", lambda e: self.start_edit(e, -1, self.lbl_master_val))

        if enabled:
            self.master_slider = ModernSlider(frame, from_=0, to=self.MAX_BRIGHT, 
                                              bg=self.colors["bg"], command=self.on_master_slide)
            self.master_slider.pack(fill='x')
        else:
            dummy = ModernSlider(frame, bg=self.colors["bg"],
                                 track_active_col=self.colors["disabled"],
                                 track_rem_col=self.colors["disabled"],
                                 thumb_fill_col=self.colors["bg"],
                                 thumb_border_col=self.colors["disabled"])
            dummy.set(100)
            dummy.unbind("<Button-1>")
            dummy.unbind("<B1-Motion>")
            dummy.pack(fill='x')
            self.master_slider = dummy

    def create_monitor_list(self):
        for i, mon in enumerate(self.gamma.monitor_dcs):
            frame = ttk.Frame(self.container, style="Win.TFrame")
            frame.pack(fill='x', pady=8)
            
            header = ttk.Frame(frame, style="Win.TFrame")
            header.pack(fill='x', pady=(0, 5))
            
            full_name = f"Display {i+1} • {mon['friendly_name']}"
            ttk.Label(header, text=full_name, style="Sub.TLabel").pack(side='left')
            
            lbl_val = ttk.Label(header, text="100%", style="Dim.TLabel", cursor="xterm")
            lbl_val.pack(side='right')
            
            lbl_val.bind("<Double-Button-1>", lambda e, idx=i, lbl=lbl_val: self.start_edit(e, idx, lbl))
            
            slider = ModernSlider(frame, from_=0, to=self.MAX_BRIGHT, 
                                  bg=self.colors["bg"], 
                                  command=lambda v, idx=i, l=lbl_val: self.on_indiv_slide(v, idx, l))
            slider.pack(fill='x')
            
            self.monitor_controls.append({'slider': slider, 'label': lbl_val, 'index': i})

    def change_brightness_safe(self, amount):
        self.root.after(0, lambda: self._apply_brightness_change(amount))

    def _apply_brightness_change(self, amount):
        current = self.master_slider.value
        new_val = current + amount
        
        if new_val < 0: new_val = 0
        if new_val > 100: new_val = 100
        
        self.master_slider.set(new_val)
        self.on_master_slide(new_val)
        self.osd.show(new_val)

    def toggle_hotkeys(self):
        if self.hotkey_var.get():
            try:
                # Right Shift + [ = DECREASE Brightness (-5)
                keyboard.add_hotkey('right shift+[', lambda: self.change_brightness_safe(-5))
                # Right Shift + ] = INCREASE Brightness (+5)
                keyboard.add_hotkey('right shift+]', lambda: self.change_brightness_safe(5))
            except Exception as e:
                print(f"Hotkey Error: {e}")
        else:
            try:
                keyboard.unhook_all()
            except: pass

    def create_footer(self):
        frame = ttk.Frame(self.root, style="Win.TFrame")
        frame.pack(side='bottom', fill='x', padx=15, pady=15)

        self.hyper_var = tk.BooleanVar(value=False)
        self.chk_hyper = tk.Checkbutton(frame, text="Hyper Mode (Taskbar Visible)", variable=self.hyper_var,
                           bg=self.colors["bg"], fg=self.colors["hyper"], 
                           selectcolor=self.colors["bg"], activebackground=self.colors["bg"],
                           activeforeground=self.colors["hyper"], command=self.toggle_hyper_mode,
                           font=("Montserrat", 9, "bold"))
        self.chk_hyper.pack(side='top', anchor='w', pady=0)
        
        self.hotkey_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame, text="Enable Hotkeys (R-Shift + [ / ])", variable=self.hotkey_var,
                       bg=self.colors["bg"], fg=self.colors["text"], 
                       selectcolor=self.colors["bg"], activebackground=self.colors["bg"],
                       activeforeground="white", command=self.toggle_hotkeys,
                       font=("Montserrat", 9)).pack(side='top', anchor='w', pady=(2, 5))
        self.toggle_hotkeys()

        row = ttk.Frame(frame, style="Win.TFrame")
        row.pack(fill='x')

        link = tk.Label(row, text="Made with <3 - Yashvardhan Gupta", 
                        bg=self.colors["bg"], fg=self.colors["text_dim"],
                        font=self.font_italic, cursor="hand2")
        link.pack(side='right', anchor='e')
        
        user_link = tk.Label(frame, text="Gratefully Stolen and Repackaged by Daniel Vincent Kramer", 
                 bg=self.colors["bg"], fg=self.colors["text_dim"],
                 font=self.font_italic, cursor="hand2")
        user_link.pack(side='bottom', anchor='e', pady=(5, 0))
        
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/YashvardhanG/"))
        link.bind("<Enter>", lambda e: link.config(fg=self.colors["accent"]))
        link.bind("<Leave>", lambda e: link.config(fg=self.colors["text_dim"]))

        user_link.bind("<Button-1>", lambda e: webbrowser.open("https://dvkramer.github.io"))
        user_link.bind("<Enter>", lambda e: user_link.config(fg=self.colors["accent"]))
        user_link.bind("<Leave>", lambda e: user_link.config(fg=self.colors["text_dim"]))

    def toggle_hyper_mode(self):
        is_hyper = self.hyper_var.get()
        current_val = self.master_slider.value
        
        active_color = self.colors["hyper"] if is_hyper else self.colors["accent"]
        
        if len(self.gamma.monitor_dcs) > 1:
            self.master_slider.set_accent_color(active_color)
        
        for ctrl in self.monitor_controls:
            ctrl['slider'].set_accent_color(active_color)

        if is_hyper:
            self.overlay.update(True, current_val)
            self.title_lbl.config(fg=self.colors["hyper"])
        else:
            self.overlay.update(False, 100) # Reset overlay to clean
            self.title_lbl.config(fg=self.colors["text"])
        
        self.gamma.set_brightness(-1, int(current_val))
        self.root.lift()

    def start_edit(self, event, idx, label_widget):
        initial_val = label_widget.cget("text").replace("%", "")
        entry = tk.Entry(label_widget.master, width=4, bg=self.colors["surface"], 
                         fg=self.colors["text"], insertbackground="white", bd=0, 
                         justify='right', font=self.font_main)
        entry.insert(0, initial_val)
        entry.select_range(0, tk.END)
        entry.pack(side='right')
        
        label_widget.pack_forget()
        entry.focus_set()
        
        entry.bind("<Return>", lambda e: self.finish_edit(entry, idx, label_widget))
        entry.bind("<FocusOut>", lambda e: self.finish_edit(entry, idx, label_widget))

    def finish_edit(self, entry, idx, label_widget):
        val_str = entry.get()
        try:
            val = int(val_str)
            if val < 0: val = 0
            if val > self.MAX_BRIGHT: val = self.MAX_BRIGHT
        except ValueError:
            val = None 
            
        entry.destroy()
        label_widget.pack(side='right')
        
        if val is not None:
            if idx == -1: 
                self.master_slider.set(val)
                self.on_master_slide(val)
            else: 
                for ctrl in self.monitor_controls:
                    if ctrl['index'] == idx:
                        ctrl['slider'].set(val)
                        self.on_indiv_slide(val, idx, label_widget)
                        break

    def apply_default_brightness(self):
        self.master_slider.set(self.DEFAULT_BRIGHT)
        self.on_master_slide(self.DEFAULT_BRIGHT)

    def on_master_slide(self, val):
        if self.is_updating: return
        self.is_updating = True
        
        try:
            value = float(val)
            if value > self.MAX_BRIGHT: value = self.MAX_BRIGHT
            
            self.lbl_master_val.config(text=f"{int(value)}%", foreground=self.colors["text_dim"])

            self.gamma.set_brightness(-1, int(value))

            if self.hyper_var.get():
                self.overlay.update(True, value)
            else:
                self.overlay.update(False, 100)

            for ctrl in self.monitor_controls:
                ctrl['slider'].set(value)
                ctrl['label'].config(text=f"{int(value)}%")
                
        finally:
            self.is_updating = False

    def on_indiv_slide(self, val, idx, lbl_widget):
        if self.is_updating: return
        self.is_updating = True
        
        try:
            value = float(val)
            if value > self.MAX_BRIGHT: value = self.MAX_BRIGHT
            
            lbl_widget.config(text=f"{int(value)}%", foreground=self.colors["text_dim"])
            
            self.gamma.set_brightness(idx, int(value))

            if self.hyper_var.get():
                 self.overlay.update(True, value)
            
            if len(self.monitor_controls) == 1:
                self.master_slider.set(value) 
                self.lbl_master_val.config(text=f"{int(value)}%")
        finally:
            self.is_updating = False

    def check_registry(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "Kramer Dimmer")
            key.Close()
            return True
        except: return False

    def toggle_autostart(self):
        path = sys.executable 
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            if self.autostart_var.get(): winreg.SetValueEx(key, "Kramer Dimmer", 0, winreg.REG_SZ, path)
            else: winreg.DeleteValue(key, "Kramer Dimmer")
            key.Close()
        except: pass

    def on_focus_out(self, event):
        if self.root.focus_displayof() is None:
             self.root.after(100, lambda: self.hide_to_tray() if not self.root.focus_displayof() else None)
    
    def hide_to_tray(self): self.root.withdraw()
    def show_window(self): 
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def start_move(self, e): self.x, self.y = e.x, e.y
    def do_move(self, e): self.root.geometry(f"+{self.root.winfo_x()+(e.x-self.x)}+{self.root.winfo_y()+(e.y-self.y)}")
    
    def quit_app(self):
        self.gamma.restore_all()
        self.overlay.destroy_overlays()
        self.icon.stop()
        self.root.quit()
        sys.exit()

    def setup_tray(self):
        img = Image.new('RGB', (64, 64), (32, 32, 32)) 
        d = ImageDraw.Draw(img)
        d.ellipse([16, 16, 48, 48], fill="#60cdff") 
        
        menu = pystray.Menu(pystray.MenuItem("Show", lambda i, item: self.show_window(), default=True),
                            pystray.MenuItem("Quit", lambda i, item: self.quit_app()))
        self.icon = pystray.Icon("Kramer Dimmer", img, "Kramer Dimmer", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

if __name__ == "__main__":
        root = tk.Tk()
        app = DimmerApp(root)
        root.mainloop()