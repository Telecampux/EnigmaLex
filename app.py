"""Interfaz local en español; los documentos nunca se suben a internet."""
import os
import sys
import json
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import tempfile
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

from anonimizador import AnonymizationError, MAX_BYTES, analyze, apply_review, read_recovery, restore
from restauracion_campos import restore_judgment
from revision import ReviewWindow
from estilo import configurar_estilos
from estilo_casillas import configurar_casillas
from procesamiento import ProcessingCancelled, cancellation_scope, check_cancelled
from actualizacion import compare_release, download_installer, installed_version


BASE_FOLDER = (Path.home() / 'Documents' / 'EnigmaLex' if getattr(sys, 'frozen', False)
               else Path(__file__).resolve().parent.parent)
ORIGINALS_FOLDER = BASE_FOLDER / 'ORIGINALES'
RESTORATION_FOLDER = BASE_FOLDER / 'ANONIMIZADOS'
PROVIDENCIAS_FOLDER = BASE_FOLDER / 'PROVIDENCIAS'
PROVIDENCIAS_RESTAURADAS_FOLDER = PROVIDENCIAS_FOLDER / 'RESTAURADAS'
SETTINGS_FILE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Anonimizador' / 'carpetas.json'


def cargar_carpetas():
    defaults = {'originales': ORIGINALS_FOLDER, 'anonimizados': RESTORATION_FOLDER,
                'providencias': PROVIDENCIAS_FOLDER}
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        if not isinstance(saved, dict):
            return defaults
        return {key: Path(saved[key]).expanduser().resolve() if isinstance(saved.get(key), str)
                and saved[key].strip() else value for key, value in defaults.items()}
    except (OSError, ValueError, TypeError):
        return defaults


def validar_carpetas(folders):
    for key, path in folders.items():
        if not path.is_dir():
            raise ValueError(f'La carpeta de {key} no existe o no está conectada: {path}')
        if key != 'originales':
            try:
                with tempfile.TemporaryFile(dir=path):
                    pass
            except OSError as error:
                raise ValueError(f'No se puede escribir en la carpeta de {key}: {path}') from error


def guardar_carpetas(folders):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps({key: str(value) for key, value in folders.items()},
                                    ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(SETTINGS_FILE)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        configurar_estilos(self)
        configurar_casillas(self)
        self.title(f'Anonimizador · Revisión y autorización · {installed_version(Path(__file__).resolve().parent)}')
        icon = Path(__file__).resolve().parent / 'assets' / 'anonimizador.ico'
        if icon.is_file():
            self.iconbitmap(str(icon))
        self.geometry('860x760')
        self.minsize(720, 660)
        self.state('zoomed')
        self.files = []
        self.archive_workspace = None
        self.archive_files = {}
        self.events = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.progress_page = None
        self.active_filename = ''
        self.restored_file = ''
        self.folders = cargar_carpetas()
        self.selected_restored = tk.StringVar()
        self.output = tk.StringVar(value=str(self.folders['anonimizados']))
        self.auto = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value='anonimizar')
        self.restore_kind = tk.StringVar(value='original')
        self.recovery = tk.StringVar()
        self.judgment = tk.StringVar()
        self.judgment_name = tk.StringVar(value='Seleccione el archivo que desea restaurar')
        self.recovery_name = tk.StringVar(value='Ninguna tabla cargada')
        self.anonymized_source = tk.StringVar(value='Insumo anonimizado asociado: sin cargar')
        self.status = tk.StringVar(value='Seleccione archivos, analice las propuestas y autorice cuáles aplicar.')
        compact = self.winfo_screenheight() < 800
        frame = ttk.Frame(self, padding=12 if compact else 22)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='EnigmaLex - Anonimizador reversible', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=f'Versión instalada: {installed_version(Path(__file__).resolve().parent)}').pack(anchor='w')
        ttk.Label(frame, text='OCR local en español e inglés · Originales intactos · Restauración de archivos anonimizados').pack(anchor='w', pady=(3, 15))
        modes = ttk.Frame(frame)
        modes.pack(fill='x')
        for value, label in [('anonimizar', 'Anonimizar archivos'), ('fallo', 'Restaurar archivos anonimizados')]:
            ttk.Radiobutton(modes, text=label, variable=self.mode, value=value).pack(side='left', padx=(0, 24))
        self.version_button = ttk.Button(modes, text='Actualización pendiente',
                                         command=self.offer_update, state='disabled')
        self.version_button.pack(side='right')
        self.available_update = None
        self.installing_update = False
        self.after_idle(self.check_for_updates)
        ttk.Button(modes, text='Configurar carpetas…', command=self.configurar_carpetas).pack(side='right', padx=8)
        document_area = ttk.Frame(frame)
        document_area.pack(fill='x')
        self.input_frame = ttk.Frame(document_area)
        self.input_frame.pack(fill='x')
        bar = ttk.Frame(self.input_frame)
        bar.pack(fill='x', pady=(15, 5))
        ttk.Button(bar, text='Seleccionar archivos…', command=self.choose).pack(side='left')
        ttk.Button(bar, text='Limpiar selección', command=self.clear).pack(side='left', padx=8)
        self.listing = tk.Listbox(self.input_frame, height=2 if compact else 5)
        self.listing.pack(fill='x')
        self.judgment_frame = ttk.LabelFrame(document_area, text='Restaurar archivos anonimizados', padding=10)
        self.local_restore_notice = ttk.Label(
            self.judgment_frame,
            text='PRIVACIDAD: los datos personales se procesan solo en el sistema local.',
            foreground='#9a3412', font=('Segoe UI', 10, 'bold'), wraplength=760)
        self.local_restore_notice.pack(anchor='w', fill='x', pady=(0, 10))
        for value, label in (
            ('original', 'Restaurar una copia idéntica del archivo original'),
            ('elaborado', 'Restaurar sentencia, fallo, auto u otro documento elaborado por el sustanciador'),
        ):
            ttk.Radiobutton(self.judgment_frame, text=label, variable=self.restore_kind,
                            value=value, command=self.update_restoration_kind).pack(anchor='w', pady=3)
        judgment_buttons = ttk.Frame(self.judgment_frame)
        judgment_buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(judgment_buttons, text='Seleccionar archivo',
                   command=self.load_downloaded_judgment).pack(side='left')
        ttk.Label(self.judgment_frame, textvariable=self.judgment_name, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(5, 0))
        self.judgment_path = ttk.Entry(self.judgment_frame, textvariable=self.judgment, state='readonly')
        recovery_bar = self.recovery_bar = ttk.LabelFrame(document_area, text='Recuperación identificada automáticamente', padding=10)
        recovery_bar.pack(fill='x', pady=5)
        ttk.Label(recovery_bar, text='La aplicación identifica los datos originales al cargar el archivo anonimizado.', wraplength=700).pack(anchor='w')
        recovery_buttons = ttk.Frame(recovery_bar)
        recovery_buttons.pack(fill='x', pady=4)
        ttk.Label(recovery_bar, textvariable=self.recovery_name, font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.recovery_path = ttk.Entry(recovery_bar, textvariable=self.recovery, state='readonly')
        self.recovery_path.pack(fill='x', pady=3)
        ttk.Label(recovery_bar, textvariable=self.anonymized_source, wraplength=700).pack(anchor='w')
        self.recovery_info = tk.StringVar(value='Cargue la tabla .restauracion del caso para ver sus campos.')
        ttk.Label(recovery_bar, textvariable=self.recovery_info, wraplength=700).pack(anchor='w')
        fields_frame = ttk.Frame(recovery_bar)
        fields_frame.pack(fill='x', pady=(5, 0))
        self.recovery_fields = ttk.Treeview(fields_frame, columns=('code', 'value'), show='headings', height=4)
        self.recovery_fields.heading('code', text='Campo anonimizado')
        self.recovery_fields.heading('value', text='Dato original que se restaurará')
        self.recovery_fields.column('code', width=320)
        self.recovery_fields.column('value', width=360)
        fields_scroll = ttk.Scrollbar(fields_frame, orient='vertical', command=self.recovery_fields.yview)
        self.recovery_fields.configure(yscrollcommand=fields_scroll.set)
        fields_scroll.pack(side='right', fill='y')
        self.recovery_fields.pack(fill='x', expand=True)
        self.manual_frame = ttk.Frame(frame)
        self.manual_frame.pack(fill='x')
        ttk.Label(self.manual_frame, text='Otros datos o variables que desea anonimizar (uno por línea):').pack(anchor='w', pady=(14, 4))
        self.manual = tk.Text(self.manual_frame, height=2 if compact else 5, wrap='word', font=('Segoe UI', 10))
        self.manual.pack(fill='x')
        ttk.Label(self.manual_frame, text='Metadatos, Nombres, Identificaciones y correos electrónicos',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(8, 2))
        ttk.Checkbutton(self.manual_frame, text='Detectar automáticamente metadatos, nombres, identificaciones y correos electrónicos',
                        variable=self.auto, command=self.actualizar_aviso_deteccion).pack(anchor='w', pady=6)
        ttk.Label(self.manual_frame, text='Metadatos: puede seleccionar su limpieza en la ventana de revisión (autor, fechas internas y datos de cámara).',
                  wraplength=790).pack(anchor='w')
        self.aviso_deteccion = tk.StringVar()
        ttk.Label(self.manual_frame, textvariable=self.aviso_deteccion, foreground='#9a3412',
                  wraplength=790).pack(anchor='w')
        self.manual.bind('<KeyRelease>', self.actualizar_aviso_deteccion)
        self.actualizar_aviso_deteccion()
        grid = self.output_grid = ttk.Frame(frame)
        grid.pack(fill='x', pady=8)
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text='Carpeta de anonimizados').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=4)
        ttk.Label(grid, textvariable=self.output).grid(row=0, column=1, sticky='w', pady=4)
        self.mode_hint = tk.StringVar()
        ttk.Label(frame, textvariable=self.mode_hint, wraplength=790, foreground='#794500').pack(anchor='w', pady=8)
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=5)
        self.run_button = ttk.Button(actions, text='Anonimizar', command=self.run, style='Proceso.TButton')
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancelar anonimización', command=self.cancel_processing, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.folder_button = ttk.Button(actions, text='Abrir carpeta anonimizados', command=self.open_output)
        self.folder_button.pack(side='left', padx=8)
        self.activity = ttk.Progressbar(actions, mode='indeterminate', length=150,
                                        style='Proceso.Horizontal.TProgressbar')
        self.activity.pack(side='right', padx=8)
        ttk.Label(frame, textvariable=self.status, wraplength=790).pack(anchor='w', pady=6)
        results = self.results_frame = ttk.LabelFrame(frame, text='Archivo restaurado para revisión', padding=10)
        self.result_name = tk.StringVar(value='')
        ttk.Label(results, textvariable=self.result_name, wraplength=700).pack(anchor='w', pady=(0, 5))
        result_actions = ttk.Frame(results)
        result_actions.pack(fill='x')
        self.open_result_button = ttk.Button(result_actions, text='Abrir', command=self.open_restored, state='disabled')
        self.open_result_button.pack(side='left', padx=8)
        self.download_result_button = ttk.Button(result_actions, text='Guardar copia revisada…', command=self.download_restored, state='disabled')
        self.download_result_button.pack(side='left')
        self.selected_restored.trace_add('write', self.update_result_controls)
        self.log = tk.Text(frame, height=3 if compact else 6, state='disabled', wrap='word')
        self.log.tag_configure('pagina', font=('Segoe UI', 11, 'bold'))
        self.log.pack(fill='both', expand=True)
        self.mode.trace_add('write', self.update_mode)
        self.update_mode()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(150, self.poll)

    def actualizar_aviso_deteccion(self, event=None):
        if self.auto.get():
            self.aviso_deteccion.set('')
        elif self.manual.get('1.0', 'end').strip():
            self.aviso_deteccion.set('Al desactivar esta opción, los metadatos y los demás datos no quedan marcados automáticamente. Puede seleccionar metadatos en la revisión y se aplicarán los datos que haya incluido manualmente arriba.')
        else:
            self.aviso_deteccion.set('Al desactivar esta opción, los metadatos y los demás datos no quedan marcados automáticamente. Puede seleccionar metadatos en la revisión o incluir datos manualmente arriba.')

    def update_result_controls(self, *_):
        selected = self.selected_restored.get()
        self.result_name.set(Path(selected).name if selected else '')
        if selected and Path(selected).is_file() and self.mode.get() == 'fallo':
            self.results_frame.pack(fill='x', pady=5)
        else:
            self.results_frame.pack_forget()
        state = 'normal' if selected and Path(selected).is_file() else 'disabled'
        self.open_result_button.config(state=state)
        self.download_result_button.config(state=state)

    def restored_selection(self):
        path = Path(self.selected_restored.get()) if self.selected_restored.get() else None
        if path is None or not path.is_file():
            messagebox.showinfo('Archivo restaurado', 'Seleccione primero un archivo restaurado.')
            return None
        return path

    def open_restored(self):
        path = self.restored_selection()
        if path is not None:
            try:
                os.startfile(str(path.resolve()))
            except OSError as exc:
                messagebox.showerror('Abrir archivo', str(exc))

    def download_restored(self):
        path = self.restored_selection()
        if path is None:
            return
        if self.busy:
            return
        destination = filedialog.asksaveasfilename(title='Guardar copia revisada',
            initialdir=str(self.folders['providencias'] / 'RESTAURADAS' if self.restore_kind.get() != 'original' else self.folders['anonimizados']),
            initialfile=path.name, defaultextension=path.suffix,
            filetypes=[('Mismo formato del documento', '*' + path.suffix)])
        if destination:
            target = Path(destination)
            if target.suffix.lower() != path.suffix.lower():
                messagebox.showerror('Guardar copia', 'Conserve el formato del documento restaurado.')
                return
            try:
                if target.resolve() != path.resolve():
                    shutil.copy2(path, destination)
                self.status.set(f'Copia guardada: {destination}')
            except OSError as exc:
                messagebox.showerror('Guardar copia', str(exc))

    def close(self):
        if self.installing_update:
            messagebox.showinfo('Actualizaciones', 'Espere a que termine la descarga.', parent=self)
        elif self.busy:
            self.cancel_processing()
        else:
            self.clear()
            self.destroy()

    def check_for_updates(self):
        if self.installing_update:
            self.after(300_000, self.check_for_updates)
            return
        installed = installed_version(Path(__file__).resolve().parent)

        def show_result(release, error):
            self.available_update = None
            if release and release.newer and release.installer_url and getattr(sys, 'frozen', False):
                self.available_update = release
            enabled = self.available_update is not None and not self.installing_update
            self.version_button.configure(state='normal' if enabled else 'disabled')
            self.after(60_000 if error else 300_000, self.check_for_updates)

        self.run_update_task(lambda: compare_release(installed), show_result)

    def offer_update(self):
        release = self.available_update
        if release is None or self.installing_update:
            return
        installed = installed_version(Path(__file__).resolve().parent)
        details = f'Versión instalada: {installed}\nNueva versión: {release.version}\n\n'
        if self.busy:
            messagebox.showinfo('Actualización pendiente', details +
                                'Espere a que termine el proceso actual.', parent=self)
            return
        if messagebox.askyesno('Actualización pendiente', details +
                               '¿Desea instalar la nueva versión?\n'
                               'EnigmaLex se cerrará al abrir el instalador.', parent=self):
            self.install_update(release)

    def run_update_task(self, task, on_result):
        results = queue.Queue()

        def run():
            try:
                results.put((task(), None))
            except (OSError, ValueError) as error:
                results.put((None, error))

        def poll_result():
            try:
                result, error = results.get_nowait()
            except queue.Empty:
                self.after(100, poll_result)
                return
            on_result(result, error)

        threading.Thread(target=run, daemon=True).start()
        self.after(100, poll_result)

    def install_update(self, release):
        self.installing_update = True
        self.version_button.configure(state='disabled', text='Descargando actualización…')

        def fetch():
            folder = Path(tempfile.mkdtemp(prefix='EnigmaLex-'))
            try:
                return download_installer(release, folder)
            except (OSError, ValueError):
                shutil.rmtree(folder, ignore_errors=True)
                raise

        def open_installer(installer, error):
            if error is None:
                try:
                    if self.busy:
                        raise ValueError('Espere a que termine el proceso actual y vuelva a intentarlo.')
                    destination = Path(sys.executable).resolve().parent
                    subprocess.Popen([str(installer), f'/DIR={destination}'], cwd=installer.parent)
                except (OSError, ValueError) as failure:
                    shutil.rmtree(installer.parent, ignore_errors=True)
                    error = failure
            if error is None:
                self.destroy()
                return
            self.installing_update = False
            self.version_button.configure(
                state='normal' if self.available_update else 'disabled', text='Actualización pendiente')
            messagebox.showerror('Actualizaciones', f'No se pudo abrir el instalador.\n\n{error}', parent=self)

        self.run_update_task(fetch, open_installer)

    def cancel_processing(self):
        if self.busy:
            self.cancel_event.set()
            self.cancel_button.config(state='disabled')
            self.status.set('Cancelando…')

    def clear_progress(self):
        self.log.config(state='normal')
        self.log.delete('1.0', 'end')
        self.log.config(state='disabled')
        self.progress_page = None
        self.seen_progress_findings = set()

    def show_page_progress(self, message):
        activity = re.fullmatch(r'(?:Analizando|Anonimizando|Restaurando) página (\d+) de (\d+)', message)
        if activity:
            self.page_activity = (self.active_filename, activity.group(1), message)
            self.status.set(message)
            return
        finding = re.match(r'^(?:Detectado|Anonimizando) ([A-Z_]+): (.*?) — (.+)$', message, re.DOTALL)
        if not finding:
            page = re.search(r'página (\d+)', message, re.IGNORECASE)
            if page:
                activity = getattr(self, 'page_activity', None)
                self.status.set(activity[2] if activity and activity[:2] == (self.active_filename, page.group(1))
                                else 'Analizando página ' + page.group(1))
            return
        kind, value, location = finding.groups()
        page = re.search(r'página (\d+)', location, re.IGNORECASE)
        title = 'Página ' + page.group(1) if page else 'Documento'
        key = (self.active_filename, title)
        if not hasattr(self, 'seen_progress_findings'):
            self.seen_progress_findings = set()
        item_key = (key, kind, ' '.join(value.split()).casefold())
        if item_key in self.seen_progress_findings:
            return
        self.seen_progress_findings.add(item_key)
        self.log.config(state='normal')
        if key != self.progress_page:
            self.log.insert('end', ('\n' if self.progress_page else '') + title + '\n', 'pagina')
            self.progress_page = key
        label = 'Identificación' if kind in {'DOCUMENTO', 'IDENTIFICACION'} else 'Nombre' if kind in {'PERSONA', 'NOMBRE_FORMULARIO', 'PROFESIONAL'} else 'Correo electrónico' if kind == 'CORREO' else 'Dato'
        self.log.insert('end', f"{label}: {' '.join(value.split())}\n")
        self.log.see('end')
        self.log.config(state='disabled')
        activity = getattr(self, 'page_activity', None)
        self.status.set(activity[2] if page and activity and activity[:2] == (self.active_filename, page.group(1)) else title)

    def update_mode(self, *_):
        mode = self.mode.get()
        self.input_frame.pack_forget()
        self.judgment_frame.pack_forget()
        self.recovery_bar.pack_forget()
        self.manual_frame.pack_forget()
        if mode == 'fallo':
            self.output_grid.pack_forget()
            self.folder_button.pack_forget()
            self.log.pack_forget()
            self.update_result_controls()
            self.judgment_frame.pack(fill='x', pady=(12, 5))
            self.update_restoration_kind(clear=False)
        else:
            self.results_frame.pack_forget()
            self.folder_button.pack(side='left', padx=8)
            self.log.pack(fill='both', expand=True)
            self.input_frame.pack(fill='x')
            self.manual_frame.pack(fill='x')
            self.output_grid.pack(fill='x', pady=8, after=self.manual_frame)
            self.run_button.config(text='Anonimizar', state='normal')
            self.mode_hint.set('')

    def update_restoration_kind(self, clear=True):
        if clear:
            self.judgment.set('')
            self.judgment_name.set('Seleccione el archivo que desea restaurar')
            self.selected_restored.set('')
            self.reset_recovery()
        original = self.restore_kind.get() == 'original'
        label = 'Restaurar copia exacta del original' if original else 'Restaurar documento del sustanciador en local'
        self.run_button.config(text=label,
                               state='normal' if self.judgment.get() and self.recovery.get() else 'disabled')
        self.mode_hint.set('Seleccione el archivo anonimizado y su recuperación. Todo se procesa en este equipo.' if original else
                           'Seleccione la providencia PDF, Word o de texto con sus códigos intactos. Se restaurará localmente en PROVIDENCIAS\\RESTAURADAS.')

    def choose(self):
        if self.busy:
            return
        if self.mode.get() == 'fallo':
            self.load_downloaded_judgment()
            return
        originals = self.folders['originales']
        files = filedialog.askopenfilenames(
            parent=self,
            title='Abra la carpeta y seleccione los archivos originales para anonimizar',
            initialdir=str(originals if originals.is_dir() else Path.home()),
            filetypes=[('Todos los archivos', '*')])
        for file in files:
            if Path(file).suffix.lower() == '.zip':
                self.choose_archive_members(Path(file))
            else:
                self.add_original(file)

    def add_original(self, file, label=None):
        if file not in self.files:
            self.files.append(file)
            self.listing.insert('end', label or file)

    def choose_archive_members(self, path):
        try:
            with zipfile.ZipFile(path) as archive:
                members = [entry for entry in archive.infolist() if not entry.is_dir()]
                if not members:
                    messagebox.showinfo('Archivo ZIP', 'El archivo comprimido está vacío.', parent=self)
                    return
                selection = self.show_archive_members(path, members)
                if selection is None:
                    self.add_original(str(path))
                    return
                pending = [index for index in selection if (path, index) not in self.archive_files]
                if any(members[index].file_size > MAX_BYTES for index in pending):
                    raise AnonymizationError('El límite por documento es 100 MB.')
                if sum(members[index].file_size for index in pending) > 3 * MAX_BYTES:
                    raise AnonymizationError('Seleccione hasta 300 MB de documentos por vez.')
                if pending and self.archive_workspace is None:
                    self.archive_workspace = tempfile.TemporaryDirectory(prefix='EnigmaLex-')
                for index in pending:
                    entry = members[index]
                    # Keep only the basename and write into our own temporary directory.
                    name = entry.filename.replace(chr(92), '/').rsplit('/', 1)[-1]
                    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip().rstrip('. ')
                    if not name:
                        name = f'documento_{index + 1}'
                    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])', name.split('.')[0]):
                        name = '_' + name
                    with archive.open(entry) as source:
                        data = source.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise AnonymizationError('El límite por documento es 100 MB.')
                    folder = Path(tempfile.mkdtemp(dir=self.archive_workspace.name))
                    target = folder / name
                    target.write_bytes(data)
                    self.archive_files[(path, index)] = target
                    self.add_original(str(target), f'{path.name}  ›  {entry.filename}')
        except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, AnonymizationError) as error:
            messagebox.showerror('Archivo ZIP', f'No se pudo cargar el documento del ZIP.\n{error}', parent=self)

    def show_archive_members(self, path, members):
        window = tk.Toplevel(self)
        window.title(path.name)
        window.transient(self)
        panel = ttk.Frame(window, padding=14)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Seleccione los documentos que desea cargar.').pack(anchor='w', pady=(0, 8))
        contents = ttk.Frame(panel)
        contents.pack(fill='both', expand=True)
        contents.rowconfigure(0, weight=1)
        contents.columnconfigure(0, weight=1)
        listing = tk.Listbox(contents, selectmode='extended', width=70, height=14)
        listing.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(contents, command=listing.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(contents, orient='horizontal', command=listing.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        listing.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        for entry in members:
            listing.insert('end', entry.filename)
        buttons = ttk.Frame(panel)
        buttons.pack(fill='x', pady=(10, 0))
        selection = ()

        def finish(selected):
            nonlocal selection
            selection = selected
            window.destroy()

        def load_selected(event=None):
            if listing.curselection():
                finish(listing.curselection())

        ttk.Button(buttons, text='Cancelar', command=window.destroy).pack(side='right')
        ttk.Button(buttons, text='Cargar selección', command=load_selected).pack(side='right', padx=6)
        ttk.Button(buttons, text='Cargar ZIP completo', command=lambda: finish(None)).pack(side='left')
        listing.bind('<Double-Button-1>', load_selected)
        listing.bind('<Return>', load_selected)
        window.bind('<Escape>', lambda event: window.destroy())
        window.wait_visibility()
        window.grab_set()
        listing.focus_set()
        self.wait_window(window)
        return selection

    def load_downloaded_judgment(self):
        """Selecciona el archivo de una de las dos formas de restauración."""
        if self.busy:
            return
        original = self.restore_kind.get() == 'original'
        path = filedialog.askopenfilename(
            title='Seleccione el archivo anonimizado original' if original else
                  'Seleccione el documento anonimizado elaborado por el sustanciador',
            initialdir=str(self.folders['anonimizados'] if original else
                           (Path.home() / 'Downloads' if (Path.home() / 'Downloads').is_dir() else self.folders['providencias'])),
            filetypes=[('Todos los archivos', '*')] if original else
                      [('Providencias PDF, Word y texto', '*.pdf *.docx *.txt *.md *.log *.rst'),
                       ('Todos los archivos', '*')])
        if not path:
            return
        if not original and Path(path).suffix.lower() not in {'.pdf', '.docx', '.txt', '.md', '.log', '.rst'}:
            messagebox.showerror('Providencia', 'Seleccione una providencia PDF, Word o de texto.')
            return
        self.selected_restored.set('')
        self.judgment.set(path)
        self.judgment_name.set(Path(path).name)
        if original and Path(path).suffix.lower() == '.restauracion':
            self.load_recovery(path)
        else:
            self.detect_recovery(path)
        if not self.recovery.get():
            self.choose_recovery()

    def detect_recovery(self, path):
        document = Path(path)
        self.reset_recovery()
        candidates = [Path(str(document) + '.restauracion')]
        for folder in (self.folders['anonimizados'], Path(__file__).resolve().parent / 'salida'):
            candidate = folder / (document.name + '.restauracion')
            if candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            if candidate.is_file():
                self.load_recovery(candidate)
                return
        self.status.set('No se encontró la recuperación automática de este archivo. Conserve el archivo .restauracion generado al anonimizar junto al documento.')

    def reset_recovery(self):
        self.recovery.set('')
        if self.mode.get() == 'fallo':
            self.run_button.config(state='disabled')
        self.recovery_name.set('Ninguna tabla cargada')
        self.recovery_info.set('La recuperación se identifica automáticamente al cargar un archivo.')
        self.anonymized_source.set('Insumo anonimizado asociado: sin cargar')
        self.recovery_fields.delete(*self.recovery_fields.get_children())

    def clear(self):
        if not self.busy:
            self.files.clear()
            self.listing.delete(0, 'end')
            self.archive_files.clear()
            if self.archive_workspace is not None:
                self.archive_workspace.cleanup()
                self.archive_workspace = None

    def choose_recovery(self):
        if self.busy:
            return
        if not self.judgment.get():
            return
        path = filedialog.askopenfilename(title='Cargue la tabla .restauracion o el insumo anonimizado que la acompaña',
            initialdir=str(self.folders['anonimizados'] if self.restore_kind.get() != 'original' else Path(self.judgment.get()).parent),
            filetypes=[('Todos los archivos del caso', '*'),
                       ('Tabla de correspondencias', '*.restauracion'),
                       ('Tabla e insumos anonimizados', ('*.restauracion', '*.pdf', '*.docx', '*.txt', '*.md', '*.log', '*.rst'))])
        if path:
            self.load_recovery(path)

    def load_recovery(self, path):
        self.reset_recovery()
        path = Path(path)
        recovery_path = path if path.suffix.lower() == '.restauracion' else Path(str(path) + '.restauracion')
        try:
            body, _ = read_recovery(recovery_path)
        except Exception as exc:
            messagebox.showerror('Recuperación no válida', str(exc))
            return
        self.recovery.set(str(recovery_path))
        if self.mode.get() == 'fallo':
            self.update_restoration_kind(clear=False)
        self.recovery_name.set(recovery_path.name)
        associated = Path(str(recovery_path)[:-len('.restauracion')])
        self.anonymized_source.set(f'Insumo anonimizado asociado: {associated}' + ('' if associated.is_file() else ' (no está en esta carpeta)'))
        self.recovery_fields.delete(*self.recovery_fields.get_children())
        for code, value in sorted(body['fields'].items()):
            self.recovery_fields.insert('', 'end', values=(code, value))
        self.recovery_info.set(f"Insumo de origen: {body['name']} · {len(body['fields'])} campos guardados.")
        self.status.set('Archivo listo para restaurar.')

    def configurar_carpetas(self):
        if self.busy:
            messagebox.showinfo('Carpetas', 'Espere a que termine el proceso actual.')
            return
        dialog = tk.Toplevel(self)
        dialog.title('Configurar carpetas locales')
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill='both', expand=True)
        panel.columnconfigure(1, weight=1)
        labels = {'originales': 'Archivos originales', 'anonimizados': 'Archivos anonimizados',
                  'providencias': 'Proyectos y providencias restauradas'}
        values = {key: tk.StringVar(value=str(path)) for key, path in self.folders.items()}
        for row, (key, label) in enumerate(labels.items()):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=5)
            ttk.Entry(panel, textvariable=values[key], width=65).grid(row=row, column=1, sticky='ew', pady=5)
            def browse(kind=key):
                selected = filedialog.askdirectory(parent=dialog, initialdir=values[kind].get() or str(BASE_FOLDER))
                if selected:
                    values[kind].set(selected)
            ttk.Button(panel, text='Elegir…', command=browse).grid(row=row, column=2, padx=(8, 0), pady=5)
        def save():
            folders = {key: Path(value.get().strip()).expanduser().resolve()
                       for key, value in values.items() if value.get().strip()}
            if len(folders) != len(values):
                messagebox.showerror('Carpetas', 'Seleccione las tres carpetas.', parent=dialog)
                return
            try:
                validar_carpetas(folders)
                guardar_carpetas(folders)
            except (OSError, ValueError) as error:
                messagebox.showerror('Carpetas', str(error), parent=dialog)
                return
            self.folders = folders
            self.output.set(str(folders['anonimizados']))
            dialog.destroy()
        ttk.Button(panel, text='Guardar', command=save).grid(row=3, column=2, sticky='e', pady=(12, 0))

    def open_output(self):
        path = self.folders['anonimizados']
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo('Salida', 'La carpeta se creará al procesar archivos.')

    def run(self):
        if self.busy:
            return
        mode = self.mode.get()
        try:
            validar_carpetas({key: path for key, path in self.folders.items() if key != 'originales'})
        except ValueError as error:
            messagebox.showerror('Carpetas', str(error))
            return
        files = [self.judgment.get()] if mode == 'fallo' and self.judgment.get() else ([] if mode == 'fallo' else list(self.files))
        if not files:
            messagebox.showerror('Falta el documento' if mode == 'fallo' else 'Falta selección',
                                 'Seleccione primero el archivo que desea restaurar.' if mode == 'fallo' else 'Seleccione al menos un archivo.')
            return
        if mode == 'fallo' and not self.recovery.get():
            self.detect_recovery(self.judgment.get())
            if self.recovery.get():
                return self.run()
            messagebox.showerror('No se encontró la recuperación', f'No se encontró el archivo .restauracion asociado. Debe conservarse junto al archivo anonimizado o en {self.folders["anonimizados"]}.')
            return
        output = str(self.folders['providencias'] / 'RESTAURADAS' if mode == 'fallo' and self.restore_kind.get() != 'original' else self.folders['anonimizados'])
        manual, automatic = self.manual.get('1.0', 'end').splitlines(), self.auto.get()
        self.busy = True
        self.restored_file = ''
        self.cancel_event.clear()
        self.clear_progress()
        self.cancel_button.config(state='normal', text='Cancelar anonimización' if mode == 'anonimizar' else 'Cancelar restauración')
        self.run_button.config(state='disabled', text='Procesando…')
        self.activity.start(12)
        work_mode = ('original' if self.restore_kind.get() == 'original' else 'fallo') if mode == 'fallo' else mode
        self.status.set('Analizando… No se guardará nada hasta que autorice las propuestas.' if mode == 'anonimizar' else
                        'Recuperando el archivo original localmente…' if work_mode == 'original' else
                        'Restaurando campos del documento localmente…')
        threading.Thread(target=self.work, args=(work_mode, files, output, manual, automatic, self.recovery.get()), daemon=True).start()

    def report_progress(self, message):
        check_cancelled()
        self.events.put(('progress', message))

    def work(self, mode, files, output, manual, automatic, recovery=''):
        try:
            with cancellation_scope(getattr(self, 'cancel_event', None)):
                App._work(self, mode, files, output, manual, automatic, recovery)
        except ProcessingCancelled:
            self.events.put(('done', 'Proceso cancelado.'))
        except Exception as exc:
            self.events.put(('error', str(exc)))
            self.events.put(('done', 'No se pudo completar el proceso.'))

    def _work(self, mode, files, output, manual, automatic, recovery=''):
        success, plans = 0, []
        for index, file in enumerate(files, 1):
            check_cancelled()
            self.events.put(('file', Path(file).name))
            self.report_progress(f'Archivo {index}/{len(files)}: {Path(file).name}')
            try:
                if mode == 'anonimizar':
                    plan = analyze(file, manual, automatic, self.report_progress)
                    plans.append(plan)
                    message = f'{Path(file).name}: {len(plan.findings)} propuestas; {plan.ocr_pages} imágenes/páginas con OCR. Pendiente de su autorización.'
                elif mode == 'fallo':
                    result = restore_judgment(file, recovery, output, self.report_progress)
                    self.events.put(('restored', result['output']))
                    message = (f'{Path(file).name}: documento restaurado localmente; '
                               f'{result["replacements"]} apariciones de {result["fields"]} campos.\n'
                               f'Revise el archivo antes de entregarlo: {result["output"]}')
                elif mode == 'original':
                    restored = restore(recovery, output)
                    self.events.put(('restored', restored))
                    message = f'{Path(file).name}: copia exacta del archivo original recuperada localmente.\nArchivo: {restored}'
                else:
                    raise ValueError('Seleccione «Anonimizar archivos» o «Restaurar archivos anonimizados».')
                success += 1
            except ProcessingCancelled:
                raise
            except Exception as exc:
                message = f'{Path(file).name}: ERROR — {exc}'
                self.events.put(('error', message))
            if mode != 'anonimizar' or 'ERROR —' in message:
                self.events.put(('log', message))
        check_cancelled()
        if plans:
            self.events.put(('review', (plans, output)))
        else:
            self.events.put(('done', 'Proceso terminado' if success == len(files) else 'Revise los errores.'))

    def save_approved(self, selections, output):
        self.busy = True
        self.restored_file = ''
        self.cancel_event.clear()
        self.clear_progress()
        self.cancel_button.config(state='normal', text='Cancelar anonimización')
        self.run_button.config(state='disabled', text='Procesando…')
        self.activity.start(12)
        self.status.set('Aplicando únicamente las propuestas autorizadas…')
        threading.Thread(target=self.save_work, args=(selections, output), daemon=True).start()

    def save_work(self, selections, output):
        try:
            with cancellation_scope(self.cancel_event):
                App._save_work(self, selections, output)
        except ProcessingCancelled:
            self.events.put(('done', 'Anonimización cancelada.'))

    def _save_work(self, selections, output):
        success = 0
        for plan, ids in selections:
            check_cancelled()
            self.events.put(('file', plan.path.name))
            try:
                result = apply_review(plan, ids, output, self.report_progress)
                zones = sum(plan.findings[identity]['kind'] == 'ZONA' for identity in ids if identity in plan.findings)
                self.events.put(('log', f'Guardado: {result["output"]}\n'
                                         f'Zonas de firma aplicadas: {zones}'))
                message = None
                success += 1
            except ProcessingCancelled:
                raise
            except Exception as exc:
                message = f'{plan.path.name}: ERROR — {exc}'
            if message:
                self.events.put(('log', message))
        self.events.put(('done', 'Proceso terminado' if success == len(selections) else 'Revise los errores.'))

    def poll(self):
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == 'file':
                    self.active_filename = message
                    self.progress_page = None
                elif kind == 'log':
                    self.log.config(state='normal')
                    self.log.insert('end', message + '\n\n')
                    self.log.see('end')
                    self.log.config(state='disabled')
                elif kind == 'progress':
                    if not self.cancel_event.is_set():
                        self.show_page_progress(message)
                elif kind == 'error':
                    self.busy = False
                    self.activity.stop()
                    self.cancel_button.config(state='disabled')
                    self.run_button.config(state='normal', text=('Restaurar copia exacta del original' if self.restore_kind.get() == 'original' else 'Restaurar documento del sustanciador en local') if self.mode.get() == 'fallo' else 'Anonimizar')
                    self.status.set('No se pudo completar el proceso.')
                    messagebox.showerror('Error al procesar el archivo', message)
                elif kind == 'restored':
                    self.restored_file = message
                    self.selected_restored.set(message)
                elif kind == 'review':
                    self.busy = False
                    self.activity.stop()
                    self.run_button.config(state='normal', text='Anonimizar')
                    self.cancel_button.config(state='disabled')
                    if self.cancel_event.is_set():
                        self.status.set('Anonimización cancelada.')
                        continue
                    plans, output = message
                    self.status.set('Revise las propuestas y autorice las que desea aplicar. No se han generado archivos.')
                    ReviewWindow(self, plans, lambda selections: self.save_approved(selections, output))
                else:
                    self.busy = False
                    self.activity.stop()
                    label = ('Restaurar copia exacta del original' if self.restore_kind.get() == 'original' else 'Restaurar documento del sustanciador en local') if self.mode.get() == 'fallo' else 'Anonimizar'
                    self.run_button.config(state='normal', text='Proceso terminado' if message == 'Proceso terminado' else label)
                    self.cancel_button.config(state='disabled')
                    self.status.set(message)
                    if self.restored_file:
                        restored = self.restored_file
                        self.restored_file = ''
                        self.status.set('Documento restaurado. Revíselo antes de guardarlo o entregarlo.')
                        self.update_idletasks()
                        try:
                            os.startfile(str(Path(restored).resolve()))
                        except OSError as exc:
                            messagebox.showinfo('Archivo restaurado', f'Archivo guardado en: {restored}\nNo se pudo abrir automáticamente: {exc}')
        except queue.Empty:
            pass
        self.after(150, self.poll)


if __name__ == '__main__':
    App().mainloop()
