"""Whole Launcher: a mod manager for South Park: The Fractured But Whole.

Add mods (.zip or folders), tick the ones you want, set the load order, press Install mods,
then Play. Uninstall puts the game back exactly as it was.
Plain Python + tkinter (both come with Python), so every line can be read before it runs.
Tip: drag mod .zip files onto "Whole Launcher.bat" to add them.
"""
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
for base in (HERE, os.path.dirname(HERE)):
    if os.path.isdir(os.path.join(base, 'tfbw')):
        sys.path.insert(0, base)
        break
HOME = os.path.dirname(HERE) if os.path.isdir(os.path.join(os.path.dirname(HERE), 'mods')) else HERE
MODS = os.path.join(HOME, 'mods')
SETTINGS = os.path.join(HOME, 'settings.json')
LEGACY_BACKUP = os.path.join(HOME, 'backup', 'sdf.sdftoc.original')   # the dev toolkit's old backup

import tkinter as tk  # noqa: E402
from tkinter import filedialog, font as tkfont, messagebox, ttk  # noqa: E402

from tfbw import engine, mods  # noqa: E402

TITLE = 'Whole Launcher'
AUTHOR = 'Coolman559654'
VERSION = '1.1.0'

# colours
BG, PANEL, PANEL2, LINE = '#1d2026', '#272b33', '#30353f', '#3a404b'
TEXT, MUTED, DIM = '#e8e8e8', '#a3abb6', '#6f7782'
GREEN, ORANGE, YELLOW, RED, BLUE = '#5fb83a', '#e8892c', '#e6b33a', '#e05a4f', '#4f8fd6'


def load_settings():
    try:
        return json.load(open(SETTINGS, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=1)
    except OSError:
        pass


def safe_extract(zpath, dest_root):
    """Unzip a mod into mods/. Refuses paths that would land outside mods/."""
    with zipfile.ZipFile(zpath) as z:
        names = [n.replace('\\', '/') for n in z.namelist()]
        tops = {n.split('/')[0] for n in names}
        if len(tops) == 1 and any('/' in n for n in names):
            # everything sits in one folder already: mods/<that folder>
            target, result = dest_root, os.path.join(dest_root, tops.pop())
        else:
            # loose files: mods/<zip name>
            target = result = os.path.join(dest_root, os.path.splitext(os.path.basename(zpath))[0])
        if os.path.exists(result):
            raise ValueError('mods/%s already exists. Remove the old version first.' % os.path.basename(result))
        root_real = os.path.realpath(dest_root)
        for n in names:
            out = os.path.realpath(os.path.join(target, n))
            if not out.startswith(root_real + os.sep):
                raise ValueError('unsafe path in zip: ' + n)
        z.extractall(target)
        return result


def overhaul_name(coll):
    """warhammer-1 -> Warhammer I"""
    if not coll:
        return ''
    roman = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V'}
    words = [roman.get(w, w.capitalize()) for w in re.split(r'[-_ ]+', coll)]
    return ' '.join(words)


def plain_contents(m):
    """what a mod changes, in player words"""
    parts = []
    if m.tweaks:
        parts.append('game settings')
    if sum(len(p) for p in m.loc.values()):
        parts.append('names & descriptions')
    if m.generators:
        parts.append('combat scripts')
    if m.files:
        parts.append('%d replaced file%s' % (len(m.files), '' if len(m.files) == 1 else 's'))
    return ', '.join(parts) or 'nothing (empty mod)'


def checkbox_image(on, size=18):
    """a filled green tick box or an empty grey box, drawn pixel by pixel (no image files to ship)"""
    img = tk.PhotoImage(width=size, height=size)
    edge = GREEN if on else '#59616c'
    fill = GREEN if on else PANEL
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            outside = x in (0, size - 1) or y in (0, size - 1)
            border = x in (1, size - 2) or y in (1, size - 2)
            row.append(PANEL if outside else edge if border else fill)
        rows.append(row)
    if on:   # white tick
        for x, y in ((4, 9), (5, 10), (6, 11), (7, 12), (8, 11), (9, 10), (10, 9), (11, 8), (12, 7), (13, 6)):
            for dy in (0, 1):
                rows[y + dy][x] = '#ffffff'
    img.put(' '.join('{' + ' '.join(r) + '}' for r in rows))
    return img


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, _):
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry('+%d+%d' % (x, y))
        tk.Label(self.tip, text=self.text, background='#111318', foreground=TEXT, relief='solid', borderwidth=1,
                 padx=8, pady=4, font=('Segoe UI', 9), wraplength=320, justify='left').pack()

    def hide(self, _):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App:
    def __init__(self, root, add_zips=()):
        self.root = root
        root.title('%s %s' % (TITLE, VERSION))
        self.settings = load_settings()
        root.geometry(self.settings.get('window', '1060x700'))
        root.minsize(900, 600)
        root.configure(background=BG)
        self.game = None
        self.order = []
        self.log_q = queue.Queue()
        self.busy = False
        os.makedirs(MODS, exist_ok=True)
        self._style()
        self._ui()
        self.find_game(self.settings.get('game'))
        for z in add_zips:
            self._add_zip_path(z)
        self.reload()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self._drain_log)

    # ---- look ------------------------------------------------------------------------
    def _style(self):
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            try:
                tkfont.nametofont(name).configure(family='Segoe UI', size=10)
            except tk.TclError:
                pass
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('.', background=BG, foreground=TEXT, fieldbackground=PANEL, bordercolor=LINE,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, focuscolor=BG)
        s.configure('TFrame', background=BG)
        s.configure('Panel.TFrame', background=PANEL)
        s.configure('TLabel', background=BG, foreground=TEXT)
        s.configure('Panel.TLabel', background=PANEL, foreground=TEXT)
        s.configure('Muted.TLabel', background=BG, foreground=MUTED)
        s.configure('PanelMuted.TLabel', background=PANEL, foreground=MUTED)
        s.configure('Title.TLabel', background=BG, foreground=TEXT, font=('Segoe UI', 20, 'bold'))
        s.configure('CardTitle.TLabel', background=PANEL, foreground=TEXT, font=('Segoe UI', 13, 'bold'))
        s.configure('Tool.TButton', background=PANEL2, foreground=TEXT, borderwidth=0, padding=(10, 6))
        s.map('Tool.TButton', background=[('active', LINE), ('disabled', PANEL)], foreground=[('disabled', DIM)])
        for name, color, hover in (('Install', GREEN, '#6fcb47'), ('Play', ORANGE, '#f39a40'),
                                   ('Plain', PANEL2, LINE)):
            s.configure(name + '.TButton', background=color, foreground='white' if name != 'Plain' else TEXT,
                        borderwidth=0, padding=(18, 10), font=('Segoe UI', 11, 'bold' if name != 'Plain' else 'normal'))
            s.map(name + '.TButton', background=[('disabled', PANEL), ('active', hover)],
                  foreground=[('disabled', DIM)])
        s.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=32,
                    borderwidth=0, font=('Segoe UI', 10))
        s.map('Treeview', background=[('selected', '#36506b')], foreground=[('selected', 'white')])
        s.configure('Treeview.Heading', background=PANEL2, foreground=MUTED, borderwidth=0, padding=(8, 6),
                    font=('Segoe UI', 9, 'bold'))
        s.map('Treeview.Heading', background=[('active', LINE)])
        s.configure('TCombobox', fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=TEXT,
                    borderwidth=0, padding=4)
        s.map('TCombobox', fieldbackground=[('readonly', PANEL2)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', PANEL2)], selectforeground=[('readonly', TEXT)])
        self.root.option_add('*TCombobox*Listbox.background', PANEL2)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        s.configure('Vertical.TScrollbar', background=LINE, troughcolor=PANEL, borderwidth=0, arrowcolor=MUTED,
                    bordercolor=PANEL, lightcolor=LINE, darkcolor=LINE, gripcount=0)
        s.map('Vertical.TScrollbar', background=[('active', '#4a515d')])
        s.configure('Horizontal.TProgressbar', background=GREEN, troughcolor=PANEL, borderwidth=0)

    def _btn(self, parent, text, cmd, style='Tool.TButton', tip=None, width=None, **pack):
        b = ttk.Button(parent, text=text, command=cmd, style=style, width=width)
        b.pack(**pack)
        if tip:
            Tooltip(b, tip)
        return b

    # ---- layout ----------------------------------------------------------------------
    def _ui(self):
        r = self.root
        # header
        head = ttk.Frame(r, padding=(20, 14, 20, 6))
        head.pack(fill='x')
        ttk.Label(head, text='WHOLE LAUNCHER', style='Title.TLabel').pack(side='left')
        ttk.Label(head, text='   mods for South Park: The Fractured But Whole', style='Muted.TLabel').pack(
            side='left', pady=(10, 0))
        ttk.Label(head, text='by ' + AUTHOR, style='Muted.TLabel').pack(side='right', pady=(10, 0))

        # status banner
        self.banner = tk.Frame(r, background=PANEL, highlightthickness=0)
        self.banner.pack(fill='x', padx=20, pady=(4, 10))
        self.banner_bar = tk.Frame(self.banner, background=DIM, width=6)
        self.banner_bar.pack(side='left', fill='y')
        inner = tk.Frame(self.banner, background=PANEL)
        inner.pack(side='left', fill='x', expand=True, padx=14, pady=10)
        self.status_var = tk.StringVar(value='Looking for the game...')
        self.sub_var = tk.StringVar()
        tk.Label(inner, textvariable=self.status_var, background=PANEL, foreground=TEXT,
                 font=('Segoe UI', 12, 'bold'), anchor='w').pack(fill='x')
        tk.Label(inner, textvariable=self.sub_var, background=PANEL, foreground=MUTED, anchor='w',
                 font=('Segoe UI', 9)).pack(fill='x')
        self._btn(self.banner, 'Change game folder', self.pick_game, side='right', padx=14,
                  tip='Pick the folder that holds SouthPark_TFBW.exe')

        # body: list (left) + details card (right)
        body = ttk.Frame(r, padding=(20, 0))
        body.pack(fill='both', expand=True)
        card = ttk.Frame(body, style='Panel.TFrame', padding=16, width=300)
        card.pack(side='right', fill='y', padx=(14, 0))
        card.pack_propagate(False)
        left = ttk.Frame(body)
        left.pack(side='left', fill='both', expand=True)

        bar = ttk.Frame(left)
        bar.pack(fill='x', pady=(0, 8))
        self._btn(bar, '+  Add mod', self.add_zip, side='left',
                  tip='Add a mod from a downloaded .zip file.\nTip: you can also drag .zip files onto '
                      'Whole Launcher.bat.')
        self._btn(bar, 'Add folder', self.add_folder, side='left', padx=(6, 0), tip='Add a mod that is a folder')
        self._btn(bar, '\u25b2', lambda: self.move(-1), side='left', padx=(16, 0), width=3,
                  tip='Move up (loads earlier). Lower mods win when two mods change the same thing.  [Ctrl+Up]')
        self._btn(bar, '\u25bc', lambda: self.move(1), side='left', padx=(4, 0), width=3, tip='Move down (wins more)  [Ctrl+Down]')
        self._btn(bar, 'Remove', self.remove, side='left', padx=(16, 0),
                  tip='Move the mod to mods/_removed (not deleted)  [Delete]')
        self._btn(bar, 'Mods folder', lambda: os.startfile(MODS), side='left', padx=(6, 0),
                  tip='Open the mods folder in Explorer')
        self.coll_var = tk.StringVar()
        self._btn(bar, 'Use', self.use_collection, side='right', width=5,
                  tip='Turn on every mod of this overhaul, and turn off other mods that take over the same classes')
        self.coll_box = ttk.Combobox(bar, textvariable=self.coll_var, state='readonly', width=14)
        self.coll_box.pack(side='right', padx=6)
        ttk.Label(bar, text='Overhaul:', style='Muted.TLabel').pack(side='right')

        listbox = ttk.Frame(left, style='Panel.TFrame')
        listbox.pack(fill='both', expand=True)
        self.img_on, self.img_off = checkbox_image(True), checkbox_image(False)
        cols = ('name', 'overhaul', 'version')
        self.tree = ttk.Treeview(listbox, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='ON', anchor='center')
        self.tree.column('#0', width=56, minwidth=56, stretch=False, anchor='center')
        for c, w, label, anchor, stretch in (('name', 330, 'MOD', 'w', True),
                                             ('overhaul', 130, 'OVERHAUL', 'w', False),
                                             ('version', 80, 'VERSION', 'center', False)):
            self.tree.heading(c, text=label, anchor=anchor)
            self.tree.column(c, width=w, anchor=anchor, stretch=stretch)
        self.tree.tag_configure('off', foreground=DIM)
        self.tree.tag_configure('odd', background='#2b3038')
        self.tree.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(listbox, orient='vertical', command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind('<Button-1>', self._click)
        self.tree.bind('<Double-1>', lambda e: self.toggle())
        self.tree.bind('<space>', lambda e: self.toggle())
        self.tree.bind('<Delete>', lambda e: self.remove())
        self.tree.bind('<Control-Up>', lambda e: (self.move(-1), 'break')[1])
        self.tree.bind('<Control-Down>', lambda e: (self.move(1), 'break')[1])
        self.tree.bind('<Button-3>', self._menu)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_details())
        self.empty = tk.Label(listbox, text='No mods yet.\n\nClick  "+  Add mod"  and pick a mod\'s .zip file.',
                              background=PANEL, foreground=MUTED, font=('Segoe UI', 11), justify='center')
        ttk.Label(left, text='Tick the mods you want.  Lower in the list wins when two mods change the same thing.',
                  style='Muted.TLabel').pack(anchor='w', pady=(6, 0))

        # details card (created above so the list can't squeeze it)
        self.d_title = ttk.Label(card, text='', style='CardTitle.TLabel', wraplength=268)
        self.d_title.pack(anchor='w')
        self.d_meta = ttk.Label(card, text='', style='PanelMuted.TLabel', wraplength=268)
        self.d_meta.pack(anchor='w', pady=(2, 10))
        self.d_desc = ttk.Label(card, text='Select a mod to see what it does.', style='Panel.TLabel',
                                wraplength=268, justify='left')
        self.d_desc.pack(anchor='w', fill='x')
        self.d_info = ttk.Label(card, text='', style='PanelMuted.TLabel', wraplength=268, justify='left')
        self.d_info.pack(anchor='w', pady=(12, 0), fill='x')
        self.d_toggle = ttk.Button(card, text='', command=self.toggle, style='Tool.TButton')

        # action bar
        act = ttk.Frame(r, padding=(20, 14, 20, 8))
        act.pack(fill='x')
        self.buttons = [
            self._btn(act, 'Install mods', self.install, style='Install.TButton', side='left',
                      tip='Build the ticked mods into the game. Close the game first.'),
            self._btn(act, '\u25b6  Play', self.play, style='Play.TButton', side='left', padx=(10, 0),
                      tip='Start the game'),
            self._btn(act, 'Uninstall all', self.uninstall, style='Plain.TButton', side='left', padx=(10, 0),
                      tip='Put the game back exactly as it was'),
            self._btn(act, 'Check conflicts', self.check, style='Plain.TButton', side='left', padx=(10, 0),
                      tip='See what the ticked mods would change and whether any fight each other'),
        ]
        self.log_btn = self._btn(act, 'Show log', self.toggle_log, side='right')
        self.progress = ttk.Progressbar(act, mode='indeterminate', length=140)

        self.log = tk.Text(r, height=8, wrap='word', background='#121418', foreground='#cfd3d8', relief='flat',
                           insertbackground=TEXT, padx=10, pady=8, font=('Consolas', 9), borderwidth=0)
        self.log.configure(state='disabled')
        self.log_shown = False

    # ---- small ui helpers ------------------------------------------------------------
    def set_banner(self, color, text, sub=''):
        self.banner_bar.configure(background=color)
        self.status_var.set(text)
        self.sub_var.set(sub)

    def toggle_log(self, show=None):
        show = (not self.log_shown) if show is None else show
        if show and not self.log_shown:
            self.log.pack(fill='both', padx=20, pady=(0, 16))
        elif not show and self.log_shown:
            self.log.pack_forget()
        self.log_shown = show
        self.log_btn.configure(text='Hide log' if show else 'Show log')

    def _click(self, e):
        if self.tree.identify_region(e.x, e.y) in ('tree', 'cell') and self.tree.identify_column(e.x) == '#0':
            row = self.tree.identify_row(e.y)
            if row:
                self.tree.selection_set(row)
                self.toggle()
                return 'break'

    def _menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        self.tree.selection_set(row)
        m = self.selected()
        menu = tk.Menu(self.root, tearoff=0, background=PANEL2, foreground=TEXT, activebackground=LINE,
                       activeforeground='white', borderwidth=0)
        menu.add_command(label='Disable' if m.enabled else 'Enable', command=self.toggle)
        menu.add_command(label='Move up', command=lambda: self.move(-1))
        menu.add_command(label='Move down', command=lambda: self.move(1))
        menu.add_separator()
        menu.add_command(label='Open mod folder', command=lambda: os.startfile(m.root))
        menu.add_command(label='Remove', command=self.remove)
        menu.tk_popup(e.x_root, e.y_root)

    # ---- game ------------------------------------------------------------------------
    def find_game(self, given=None):
        try:
            self.game = engine.Game.find(given, LEGACY_BACKUP)
        except engine.GameError as ex:
            self.game = None
            self.say(str(ex))
        if self.game and self.settings.get('game') != self.game.path:
            self.settings['game'] = self.game.path
            save_settings(self.settings)
        self.refresh_status()

    def pick_game(self):
        d = filedialog.askdirectory(title='Pick the South Park: The Fractured But Whole folder')
        if d:
            self.find_game(d)
            if not self.game:
                messagebox.showerror(TITLE, 'That folder does not contain the game.\n\n'
                                            'Pick the folder that holds SouthPark_TFBW.exe.')

    def refresh_status(self):
        if not self.game:
            self.set_banner(RED, 'Game not found',
                            'Press "Change game folder" and pick the folder that holds SouthPark_TFBW.exe.')
            return
        path = self.game.path
        if self.busy:
            return
        if self.game.running():
            self.set_banner(YELLOW, 'The game is running', 'Close it before installing or uninstalling mods.  ' + path)
            return
        inst = self.game.installed()
        want = [m.id for m in self.order if m.enabled]
        if inst is not None:
            if inst[1] != want:
                self.set_banner(YELLOW, 'You have changes that are not installed yet',
                                'Press "Install mods" to apply them.  ' + path)
            else:
                self.set_banner(GREEN, 'Ready to play: %d mod%s installed' % (len(inst[1]), '' if len(inst[1]) == 1 else 's'),
                                path)
        elif self.game.modded():
            self.set_banner(BLUE, 'Mods are installed (by the command-line toolkit)',
                            'Press "Install mods" to manage them from here.  ' + path)
        else:
            self.set_banner(DIM, 'No mods installed',
                            ('Tick some mods and press "Install mods".  ' if self.order else
                             'Add a mod with "+ Add mod" to get started.  ') + path)

    # ---- mod list --------------------------------------------------------------------
    def reload(self, keep=None):
        try:
            self.order = mods.ordered(MODS)
        except OSError as ex:
            self.say('could not read mods: %s' % ex)
            self.order = []
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.order):
            tags = (['off'] if not m.enabled else []) + (['odd'] if i % 2 else [])
            self.tree.insert('', 'end', iid=m.id, tags=tags, image=self.img_on if m.enabled else self.img_off,
                             values=(m.name, overhaul_name(m.collection), m.meta.get('version', '')))
        if self.order:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor='center')
        colls = sorted({m.collection for m in self.order if m.collection})
        self.coll_box['values'] = [overhaul_name(c) for c in colls]
        self._colls = dict(zip([overhaul_name(c) for c in colls], colls))
        if colls and self.coll_var.get() not in self._colls:
            self.coll_var.set(overhaul_name(colls[0]))
        target = keep if keep in self.tree.get_children() else (self.tree.get_children() or [None])[0]
        if target:
            self.tree.selection_set(target)
            self.tree.see(target)
        for c in mods.class_clashes([m for m in self.order if m.enabled]):
            self.say('CONFLICT ' + c)
        self.show_details()
        self.refresh_status()

    def selected(self):
        sel = self.tree.selection()
        return next((m for m in self.order if m.id == sel[0]), None) if sel else None

    def show_details(self):
        m = self.selected()
        if not m:
            self.d_title.configure(text='')
            self.d_meta.configure(text='')
            self.d_desc.configure(text='Select a mod to see what it does.' if self.order else '')
            self.d_info.configure(text='')
            self.d_toggle.pack_forget()
            return
        self.d_title.configure(text=m.name)
        meta = ['version %s' % m.meta['version'] if m.meta.get('version') else '',
                'by %s' % m.meta['author'] if m.meta.get('author') else '']
        self.d_meta.configure(text='   '.join(x for x in meta if x))
        self.d_desc.configure(text=m.meta.get('description', 'No description.'))
        info = ['Changes: ' + plain_contents(m)]
        if m.collection:
            info.append('Part of the %s overhaul' % overhaul_name(m.collection))
        if m.classes:
            info.append('Takes over the %s class' % ', '.join(c.capitalize() for c in m.classes))
        if m.generators:
            info.append('Runs %d small script%s at install time to build its changes from your own game files.'
                        % (len(m.generators), '' if len(m.generators) == 1 else 's'))
        self.d_info.configure(text='\n\n'.join(info))
        self.d_toggle.configure(text='Turn off' if m.enabled else 'Turn on')
        self.d_toggle.pack(anchor='w', pady=(16, 0))

    def toggle(self):
        m = self.selected()
        if m:
            m.enabled = not m.enabled
            mods.save_order(MODS, self.order)
            self.reload(m.id)

    def move(self, step):
        m = self.selected()
        if not m:
            return
        i = self.order.index(m)
        j = max(0, min(len(self.order) - 1, i + step))
        self.order.insert(j, self.order.pop(i))
        mods.save_order(MODS, self.order)
        self.reload(m.id)

    def remove(self):
        m = self.selected()
        if not m or not messagebox.askyesno(TITLE, 'Remove "%s"?\n\nIt is moved to mods/_removed, not deleted.'
                                            % m.name):
            return
        trash = os.path.join(MODS, '_removed')
        os.makedirs(trash, exist_ok=True)
        top = m.id.split('/')[0]
        dst = os.path.join(trash, top)
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(trash, '%s (%d)' % (top, n))
        shutil.move(os.path.join(MODS, top), dst)
        self.say('Moved %s to mods/_removed.' % top)
        self.reload()

    def _add_zip_path(self, p):
        try:
            where = safe_extract(p, MODS)
            self.say('Added %s' % os.path.relpath(where, MODS))
            return True
        except (zipfile.BadZipFile, ValueError, OSError) as ex:
            messagebox.showerror(TITLE, '%s:\n%s' % (os.path.basename(p), ex))
            return False

    def add_zip(self):
        paths = filedialog.askopenfilenames(title='Add mods', filetypes=[('Mod archives', '*.zip')])
        added = [p for p in paths if self._add_zip_path(p)]
        self.reload()
        if added:
            messagebox.showinfo(TITLE, 'Added %d mod package%s. Tick what you want, then press "Install mods".'
                                % (len(added), '' if len(added) == 1 else 's'))

    def add_folder(self):
        d = filedialog.askdirectory(title='Add a mod folder')
        if not d:
            return
        dst = os.path.join(MODS, os.path.basename(d.rstrip('/\\')))
        if os.path.exists(dst):
            messagebox.showerror(TITLE, 'mods/%s already exists.' % os.path.basename(dst))
            return
        shutil.copytree(d, dst)
        self.say('Added %s' % os.path.basename(dst))
        self.reload()

    def use_collection(self):
        coll = self._colls.get(self.coll_var.get()) if hasattr(self, '_colls') else None
        if not coll:
            return
        members = [m for m in self.order if m.collection == coll]
        taken = {c for m in members for c in m.classes}
        off = []
        for m in self.order:
            if m.collection == coll:
                m.enabled = True
            elif m.enabled and taken & set(m.classes):
                m.enabled = False
                off.append(m.name)
        mods.save_order(MODS, self.order)
        self.say('Using the %s overhaul.%s' % (overhaul_name(coll), (' Turned off: ' + ', '.join(off)) if off else ''))
        self.reload()

    # ---- work (runs in a thread, talks back through a queue) -------------------------------
    def say(self, msg):
        self.log_q.put(str(msg))

    def _drain_log(self):
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            if isinstance(msg, tuple):
                kind, payload = msg
                if kind == 'done':
                    self._finish(payload)
                continue
            self.log.configure(state='normal')
            self.log.insert('end', msg + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
            if msg.startswith(('ERROR', 'CONFLICT')):
                self.toggle_log(True)
        self.root.after(100, self._drain_log)

    def _finish(self, outcome):
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        for b in self.buttons:
            b.state(['!disabled'])
        self.refresh_status()
        kind, text = outcome
        if kind == 'installed':
            if messagebox.askyesno(TITLE, 'Mods installed!\n\nPlay now?'):
                self.play()
        elif kind == 'uninstalled':
            messagebox.showinfo(TITLE, 'All mods removed. Your game is back to normal.')
        elif kind == 'error':
            self.set_banner(RED, 'Something went wrong', text.splitlines()[0][:140] + '  (details in the log)')

    def run(self, title, fn, done_kind):
        if self.busy:
            return
        if not self.game:
            messagebox.showerror(TITLE, 'Pick the game folder first ("Change game folder").')
            return
        self.busy = True
        for b in self.buttons:
            b.state(['disabled'])
        self.progress.pack(side='right', padx=12)
        self.progress.start(12)
        self.set_banner(BLUE, title + '...', 'This can take a minute. Please don\'t start the game.')
        self.say('--- %s ---' % title)

        def work():
            outcome = (done_kind, '')
            try:
                fn()
            except engine.GameError as ex:
                self.say('ERROR: %s' % ex)
                outcome = ('error', str(ex))
            except Exception:
                tb = traceback.format_exc()
                self.say('ERROR (unexpected):\n' + tb)
                outcome = ('error', 'unexpected error')
            self.log_q.put(('done', outcome))
        threading.Thread(target=work, daemon=True).start()

    def install(self):
        order = list(self.order)
        if not any(m.enabled for m in order):
            if messagebox.askyesno(TITLE, 'No mods are ticked.\n\nUninstall everything instead?'):
                self.uninstall(confirm=False)
            return
        self.run('Installing mods', lambda: engine.install(self.game, order, log=self.say, label=TITLE), 'installed')

    def uninstall(self, confirm=True):
        if confirm and not messagebox.askyesno(TITLE, 'Remove all mods and put the game back to normal?'):
            return
        self.run('Uninstalling', lambda: engine.uninstall(self.game, log=self.say), 'uninstalled')

    def check(self):
        order = list(self.order)

        def go():
            with tempfile.TemporaryDirectory(prefix='wholelauncher-') as work:
                toc = self.game.toc()
                result = mods.merge(toc, engine.prepare(toc, order, work, log=self.say))
            for line in ([('ERROR', e) for e in result.errors] + [('CONFLICT', c) for c in result.conflicts]
                         + [('NOTE', w) for w in result.warnings]):
                self.say('%-8s %s' % line)
            if not (result.errors or result.conflicts):
                self.say('No conflicts: the ticked mods change %d game file(s) and work together.' % len(result.files))
            self.root.after(0, lambda: self.toggle_log(True))
        self.run('Checking for conflicts', go, 'checked')

    def play(self):
        if not self.game:
            return
        inst = self.game.installed()
        want = [m.id for m in self.order if m.enabled]
        if inst is not None and inst[1] != want and not messagebox.askyesno(
                TITLE, 'Your mod changes are not installed yet.\n\nPlay anyway?'):
            return
        self.game.play()
        self.say('Starting the game...')

    def close(self):
        self.settings['window'] = self.root.geometry()
        save_settings(self.settings)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root, add_zips=[a for a in sys.argv[1:] if a.lower().endswith('.zip') and os.path.isfile(a)])
    root.mainloop()


if __name__ == '__main__':
    main()
