"""Revisión local de propuestas antes de autorizar la escritura de resultados."""
import io
import secrets
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageTk
from estilo import configurar_estilos

DATOS_PERSONALES_AUTOMATICOS = frozenset({
    'PERSONA', 'NOMBRE_FORMULARIO', 'PROFESIONAL', 'DOCUMENTO', 'IDENTIFICACION', 'CORREO', 'DATO',
})


def draw_redaction(draw, box):
    """Preview the same field background used in the exported file."""
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return
    draw.rectangle(box, fill='#fff2a8')


class ReviewWindow(tk.Toplevel):
    def __init__(self, parent, plans, on_approve):
        super().__init__(parent)
        configurar_estilos(self)
        self.restoring = all(getattr(plan, 'purpose', '') == 'restore_fields' for plan in plans)
        from actualizacion import installed_version
        self.title('Revisar campos para restaurar en el fallo' if self.restoring else
                   f'Revisar y autorizar anonimización · {installed_version(Path(__file__).resolve().parent)}')
        width, height = min(1200, self.winfo_screenwidth()-80), min(820, self.winfo_screenheight()-90)
        self.geometry(f'{width}x{height}+25+25')
        self.minsize(min(900, width), min(650, height))
        self.plans, self.on_approve = plans, on_approve
        self.rows, self.chosen = {}, set()
        self.metadata_rows = set()
        self.clean_metadata = tk.BooleanVar(value=all(getattr(plan, 'automatic', True) for plan in plans))
        self.row_locations = {}
        self.photo = None
        self.current = None
        self.image_geometry = None
        self.drag_start = None
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        frame = ttk.Frame(self, padding=15)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Revisa los datos que se cargarán al fallo' if self.restoring else 'Metadatos, Nombres, Identificaciones y correos electrónicos', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        instruction = ('Todavía no se ha guardado ningún resultado. Marca cada dato que autorizas restaurar.' if self.restoring else
                       'Se muestran primero los metadatos encontrados; puede elegir si los anonimiza. Después se proponen nombres, identificaciones y correos electrónicos, que puede desmarcar antes de generar.')
        ttk.Label(frame, text=instruction, wraplength=1050).pack(anchor='w', pady=5)
        if not self.restoring:
            ttk.Checkbutton(frame, text='Anonimizar metadatos', variable=self.clean_metadata).pack(anchor='w', pady=(5, 2))
            ttk.Label(frame, text='Metadatos encontrados en el original:').pack(anchor='w', pady=(2, 2))
            metadata_frame = ttk.Frame(frame)
            metadata_frame.pack(fill='x')
            metadata_table = ttk.Treeview(metadata_frame, columns=('file', 'field', 'value'), show='headings', height=4)
            for key, title, width in [('file', 'Archivo', 200), ('field', 'Campo', 260), ('value', 'Valor encontrado', 540)]:
                metadata_table.heading(key, text=title)
                metadata_table.column(key, width=width, stretch=True)
            metadata_scroll = ttk.Scrollbar(metadata_frame, orient='vertical', command=metadata_table.yview)
            metadata_table.configure(yscrollcommand=metadata_scroll.set)
            metadata_table.pack(side='left', fill='x', expand=True)
            metadata_scroll.pack(side='right', fill='y')
            for plan in plans:
                for location, key, value in (getattr(plan, 'metadata', None) or []):
                    metadata_table.insert('', 'end', values=(plan.path.name, f'{location} / {key}', value))
            if not metadata_table.get_children():
                metadata_table.insert('', 'end', values=('', 'Sin metadatos legibles', ''))
            ttk.Label(frame, text='Los nombres, identificaciones y correos se revisan por aparición en la lista siguiente.',
                      wraplength=1050).pack(anchor='w', pady=(4, 2))
        tools = ttk.Frame(frame)
        tools.pack(fill='x', pady=5)
        ttk.Button(tools, text='Marcar todos', command=lambda: self.mark_all(True)).pack(side='left')
        ttk.Button(tools, text='Desmarcar todos', command=lambda: self.mark_all(False)).pack(side='left', padx=6)
        self.counter = tk.StringVar()
        ttk.Label(tools, textvariable=self.counter).pack(side='left', padx=10)
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_frame, columns=('allow', 'kind', 'value', 'where'), show='headings', height=6)
        self.table.tag_configure('autorizado', background='#e4f2e8', foreground='#164526')
        self.table.tag_configure('metadato', background='#dbeafe', foreground='#1e3a8a')
        for key, text, width in [('allow', 'Autorizar', 70), ('kind', 'Tipo', 110), ('value', 'Dato que se ocultará', 300), ('where', 'Archivo / ubicación', 450)]:
            if key == 'value' and self.restoring:
                text = 'Código que se restaurará'
            self.table.heading(key, text=text)
            self.table.column(key, width=width, stretch=key != 'allow')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(xscrollcommand=horizontal.set)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.bind('<Button-1>', self.click)
        self.table.bind('<space>', self.toggle_current)
        self.table.bind('<<TreeviewSelect>>', self.show_selected)
        for index, plan in enumerate(plans):
            if not self.restoring:
                metadata = getattr(plan, 'metadata', None) or []
                for number, (location, key, value) in enumerate(metadata[:50]):
                    self.add_row(index, f'__METADATA__:{number}', 'METADATO', f'{key}: {value}', location)
                if len(metadata) > 50:
                    self.add_row(index, '__METADATA__:resto', 'METADATO',
                                 f'{len(metadata) - 50} campos adicionales; consulte la pestaña Metadatos', 'Archivo completo')
            for finding in plan.findings.values():
                self.add_row(index, finding['id'], finding['kind'], finding['value'], finding['location'])
            if not plan.findings:
                action = 'PDF sustituto: omitir TODO el contenido' if any('PDF sustituto' in w for w in plan.warnings) else 'Sin datos detectados; revise el documento'
                self.add_row(index, None, 'SIN HALLAZGOS', action, 'Archivo completo')
            for location, evidence in plan.evidence.items():
                if evidence.get('image'):
                    self.add_row(index, None, 'REVISAR PÁGINA',
                                 'Revise nombre, cédula, radicado, TD, NUI, patio y huella', location)
        if not self.restoring:
            for row, (index, identity) in self.rows.items():
                finding = plans[index].findings.get(identity, {})
                if finding.get('kind') in DATOS_PERSONALES_AUTOMATICOS or finding.get('kind') == 'ZONA':
                    self.toggle(row, redraw=False)
            self.clean_metadata.trace_add('write', self.sync_metadata_rows)
            self.sync_metadata_rows()
        self.update_counter()
        notebook = ttk.Notebook(frame)
        notebook.pack(fill='both', expand=True, pady=8)
        text_frame, image_frame = ttk.Frame(notebook), ttk.Frame(notebook)
        notebook.add(text_frame, text='Contexto y texto reconocido')
        self.notebook, self.image_frame = notebook, image_frame
        if not self.restoring:
            notebook.add(image_frame, text='Imagen y zonas manuales')
        if not self.restoring:
            metadata_tab = ttk.Frame(notebook)
            notebook.add(metadata_tab, text='✓ Metadatos')
            ttk.Checkbutton(metadata_tab, text='Anonimizar metadatos', variable=self.clean_metadata).pack(anchor='w', padx=8, pady=8)
            ttk.Label(metadata_tab, text='La recuperación conserva el original íntegro.',
                      wraplength=950).pack(anchor='w', padx=8, pady=8)
            metadata_text = tk.Text(metadata_tab, wrap='word', font=('Segoe UI', 10))
            metadata_scroll = ttk.Scrollbar(metadata_tab, orient='vertical', command=metadata_text.yview)
            metadata_text.configure(yscrollcommand=metadata_scroll.set)
            metadata_scroll.pack(side='right', fill='y')
            metadata_text.pack(fill='both', expand=True)
            for plan in plans:
                metadata_text.insert('end', f'{plan.path.name}\n')
                for location, key, value in (getattr(plan, 'metadata', None) or []):
                    metadata_text.insert('end', f'  {location} / {key}: {value}\n')
                metadata_text.insert('end', '\n')
            metadata_text.configure(state='disabled')
        self.approve_button = ttk.Button(notebook, text='Generar anonimizado',
                                         command=self.approve, style='Principal.TButton')
        self.approve_button.place(relx=1, x=-4, y=1, anchor='ne')
        self.context = tk.Text(text_frame, height=6, wrap='word', font=('Segoe UI', 10))
        text_scroll = ttk.Scrollbar(text_frame, orient='vertical', command=self.context.yview)
        self.context.configure(yscrollcommand=text_scroll.set)
        text_scroll.pack(side='right', fill='y')
        self.context.pack(side='left', fill='both', expand=True)
        self.context.tag_configure('match', background='#ffe180', foreground='#111111')
        zoom_bar = ttk.Frame(image_frame)
        zoom_bar.pack(fill='x')
        ttk.Label(zoom_bar, text='Vista ampliada del documento').pack(side='left')
        self.zoom = tk.DoubleVar(value=1)
        ttk.Scale(zoom_bar, from_=1, to=6, variable=self.zoom,
                  command=lambda value: self.draw_image()).pack(side='left', fill='x', expand=True)
        image_view = ttk.Frame(image_frame)
        image_view.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(image_view, background='#dbe2e8', height=190)
        vscroll = ttk.Scrollbar(image_view, orient='vertical', command=self.canvas.yview)
        hscroll = ttk.Scrollbar(image_view, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        vscroll.pack(side='right', fill='y')
        hscroll.pack(side='bottom', fill='x')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self.draw_image())
        if not self.restoring:
            self.canvas.bind('<ButtonPress-1>', self.start_region)
            self.canvas.bind('<B1-Motion>', self.drag_region)
            self.canvas.bind('<ButtonRelease-1>', self.finish_region)
            ttk.Label(zoom_bar, text='Arrastre sobre cada dato manuscrito para ocultarlo.').pack(side='left', padx=8)
        ttk.Label(frame, text='Seleccione cada aparición que autoriza restaurar. Las demás conservarán su código.' if self.restoring else 'Revise los datos y marque los que autoriza anonimizar.', wraplength=1050).pack(anchor='w')
        bottom = ttk.Frame(frame)
        bottom.pack(fill='x', pady=(10, 0))
        ttk.Button(bottom, text='Cancelar sin generar archivos', command=self.cancel).pack(side='left')
        if self.rows:
            first = next(iter(self.rows))
            self.table.selection_set(first)
            self.show_selected()
        self.transient(parent)
        self.grab_set()

    def add_row(self, plan_index, finding_id, kind, value, location):
        row = self.table.insert('', 'end', values=('☐' if finding_id else '', kind, value, self.plans[plan_index].path.name + ' / ' + location))
        self.rows[row] = (plan_index, finding_id)
        self.row_locations[row] = location
        if kind == 'METADATO':
            self.metadata_rows.add(row)
        return row

    def sync_metadata_rows(self, *_):
        selected = self.clean_metadata.get()
        for row in self.metadata_rows:
            if selected:
                self.chosen.add(row)
            else:
                self.chosen.discard(row)
            self.table.set(row, 'allow', '☑' if selected else '☐')
            self.table.item(row, tags=('metadato',))
        self.update_counter()

    def update_counter(self):
        self.counter.set(f'{len(self.chosen)} de {len(self.rows)} propuestas autorizadas')

    def toggle(self, row, redraw=True):
        if row in self.metadata_rows:
            self.clean_metadata.set(not self.clean_metadata.get())
            return
        if self.rows[row][1] is None:
            return
        if row in self.chosen:
            self.chosen.remove(row)
        else:
            self.chosen.add(row)
        self.table.set(row, 'allow', '☑' if row in self.chosen else '☐')
        self.table.item(row, tags=('autorizado',) if row in self.chosen else ())
        self.update_counter()
        if redraw:
            self.draw_image()

    def click(self, event):
        row = self.table.identify_row(event.y)
        if row and self.table.identify_column(event.x) == '#1':
            self.toggle(row)

    def toggle_current(self, event=None):
        for row in self.table.selection():
            self.toggle(row)
        return 'break'

    def mark_all(self, selected):
        if not self.restoring:
            self.clean_metadata.set(selected)
        for row in self.rows:
            if row in self.metadata_rows:
                continue
            if (row in self.chosen) != selected:
                self.toggle(row, redraw=False)
        self.draw_image()

    def show_selected(self, event=None):
        self.drag_start = None
        selection = self.table.selection()
        if not selection:
            return
        index, finding_id = self.rows[selection[0]]
        plan = self.plans[index]
        if selection[0] in self.metadata_rows:
            self.current = (None, None)
            self.context.configure(state='normal')
            self.context.delete('1.0', 'end')
            self.context.insert('end', 'Metadatos encontrados en el original. La casilla de esta fila controla la limpieza de los metadatos del archivo. Consulte la pestaña Metadatos para ver la lista completa.')
            self.context.configure(state='disabled')
            self.draw_image()
            return
        finding = plan.findings.get(finding_id)
        evidence = plan.evidence.get(finding['location']) if finding else None
        if not finding:
            evidence = plan.evidence.get(self.row_locations[selection[0]])
            if evidence is None:
                evidence = next((item for item in plan.evidence.values() if item.get('image')), None)
        self.current = (evidence, finding)
        if not self.restoring and evidence and evidence.get('image') and (not finding or finding.get('caption')):
            self.notebook.select(self.image_frame)
        self.context.configure(state='normal')
        self.context.delete('1.0', 'end')
        if finding:
            if self.restoring:
                proposal = f"Restaurar dato original: {finding['token']}"
            elif finding.get('caption'):
                proposal = 'Ocultar la imagen completa.'
            elif finding.get('token'):
                proposal = f"Ocultar en imagen/PDF o sustituir por {finding['token']} en texto."
            else:
                proposal = 'Ocultar la zona marcada.'
            self.context.insert('end', f"Archivo: {plan.path.name}\nUbicación: {finding['location']}\nDato: {finding['value']}\nPropuesta: {proposal}\n\nCONTEXTO:\n{finding['context']}\n\n")
        else:
            self.context.insert('end', 'No se encontraron coincidencias. Revise el texto reconocido y añada los datos que desea ocultar.\n\n')
        texts = [evidence] if evidence else list(plan.evidence.values())
        for item in texts:
            self.context.insert('end', 'TEXTO LEÍDO:\n' + item['text'] + '\n\n')
        if finding:
            start = '1.0'
            while True:
                position = self.context.search(finding['value'], start, stopindex='end', nocase=True)
                if not position:
                    break
                end = f'{position}+{len(finding["value"])}c'
                self.context.tag_add('match', position, end)
                start = end
        self.context.configure(state='disabled')
        self.draw_image()

    def get_preview_image(self):
        """Preview selected fields with their visible codes."""
        from etiquetas import image_label
        evidence, finding = self.current
        image = Image.open(io.BytesIO(evidence['image'])).convert('RGB')
        if self.restoring:
            return image
        selection = self.table.selection()
        if not selection:
            return image
        index, _ = self.rows[selection[0]]
        plan = self.plans[index]
        location = next(key for key, item in plan.evidence.items() if item is evidence)
        drawing = ImageDraw.Draw(image)
        for row in self.chosen:
            plan_index, identity = self.rows[row]
            selected = plan.findings.get(identity)
            if plan_index != index or not selected or selected['location'] != location:
                continue
            boxes = [selected['box']] if selected.get('box') else [
                box for a, b, box, _ in evidence['words']
                if selected['start'] < b and selected['stop'] > a]
            if boxes:
                box = (min(item[0] for item in boxes), min(item[1] for item in boxes),
                       max(item[2] for item in boxes), max(item[3] for item in boxes))
                label = selected.get('token') or selected.get('caption')
                if label:
                    image_label(image, box, label)
                else:
                    draw_redaction(drawing, box)
        return image

    def draw_image(self):
        self.canvas.delete('all')
        self.image_geometry = None
        if not self.current or not self.current[0] or not self.current[0].get('image'):
            self.canvas.create_text(20, 20, anchor='nw', text='La ubicación y el contexto de este dato se muestran en la pestaña de texto.')
            return
        evidence, finding = self.current
        image = self.get_preview_image()
        original_size = image.size
        if finding:
            draw = ImageDraw.Draw(image)
            if finding.get('box'):
                draw.rounded_rectangle(finding['box'], radius=4, outline='#147d74', width=max(2, image.width // 500))
            for a, b, box, _ in evidence['words']:
                if finding['start'] < b and finding['stop'] > a:
                    draw.rounded_rectangle(box, radius=3, outline='#147d74', width=max(2, image.width // 500))
        zoom = self.zoom.get()
        image.thumbnail((max(100, int((self.canvas.winfo_width()-20)*zoom)), max(100, int((self.canvas.winfo_height()-20)*zoom))))
        self.image_geometry = (original_size, image.size)
        self.canvas.configure(scrollregion=(0, 0, image.width+20, image.height+20))
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(10, 10, image=self.photo, anchor='nw')

    def start_region(self, event):
        if self.image_geometry and not self.restoring:
            self.drag_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def drag_region(self, event):
        if self.drag_start:
            self.canvas.delete('selection')
            self.canvas.create_rectangle(*self.drag_start, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y),
                                         outline='red', width=2, tags='selection')

    def finish_region(self, event):
        if not self.drag_start or not self.image_geometry:
            return
        x, y = self.drag_start
        self.drag_start = None
        (width, height), (shown_width, shown_height) = self.image_geometry
        x0, x1 = sorted((max(0, min(shown_width, x-10)), max(0, min(shown_width, self.canvas.canvasx(event.x)-10))))
        y0, y1 = sorted((max(0, min(shown_height, y-10)), max(0, min(shown_height, self.canvas.canvasy(event.y)-10))))
        if x1-x0 < 4 or y1-y0 < 4:
            self.canvas.delete('selection')
            return
        selected = self.table.selection()
        if not selected:
            return
        index, _ = self.rows[selected[0]]
        plan = self.plans[index]
        evidence = self.current[0]
        location = next(key for key, item in plan.evidence.items() if item is evidence)
        box = (int(x0*width/shown_width), int(y0*height/shown_height),
               min(width-1, int(x1*width/shown_width)+1), min(height-1, int(y1*height/shown_height)+1))
        if box[2]-box[0] < 8 or box[3]-box[1] < 8:
            self.canvas.delete('selection')
            messagebox.showinfo('Zona demasiado pequeña', 'Marque toda la zona que desea ocultar.', parent=self)
            return
        identity = 'zona_' + secrets.token_hex(12)
        plan.findings[identity] = {'id': identity, 'kind': 'ZONA', 'value': 'Zona marcada a mano',
            'location': location, 'box': box, 'start': -1, 'stop': -1,
            'context': 'Se ocultará completamente el rectángulo dibujado al autorizar esta propuesta.',
            'token': ''}
        row = self.add_row(index, identity, 'ZONA', 'Zona marcada a mano', location)
        self.toggle(row, redraw=False)
        self.table.selection_set(row)
        self.table.see(row)
        self.update_counter()
        self.show_selected()

    def approve(self):
        if not self.chosen and (self.restoring or not self.clean_metadata.get()):
            messagebox.showinfo('Sin autorización', 'Marque las propuestas que desea autorizar.', parent=self)
            return
        approved = {}
        for row in self.chosen:
            index, finding_id = self.rows[row]
            approved.setdefault(index, set())
            if finding_id and not str(finding_id).startswith('__METADATA__:'):
                approved[index].add(finding_id)
        self.grab_release()
        self.destroy()
        if not self.restoring and self.clean_metadata.get():
            for index in range(len(self.plans)):
                approved.setdefault(index, set())
        elif not self.restoring:
            for index in approved:
                approved[index].add('__KEEP_METADATA__')
        self.on_approve([(self.plans[index], ids) for index, ids in sorted(approved.items())])

    def cancel(self):
        self.master.status.set('Revisión cancelada. No se generaron archivos.')
        self.grab_release()
        self.destroy()
