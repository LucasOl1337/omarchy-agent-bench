#!/usr/bin/env python3
"""Human-opened controls; no window is focused until the human presses Show."""
import json
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from bench_control import RUNTIME, views

root = tk.Tk(className='AgentBenchPanel')
root.title('Bancadas dos agentes')
root.geometry('750x390')
root.minsize(620, 340)
outer = ttk.Frame(root, padding=20)
outer.pack(fill='both', expand=True)
ttk.Label(outer, text='Bancadas dos agentes', font=('sans', 19, 'bold')).pack(anchor='w')
ttk.Label(outer, text='Mesmo computador. Cada bancada tem sua tela e entrada de teste.').pack(anchor='w', pady=(6,14))
table = ttk.Treeview(outer, columns=('workspace','mode'), show='tree headings', height=5, selectmode='browse')
table.heading('#0', text='Bancada'); table.column('#0', width=310)
table.heading('workspace', text='Workspace'); table.column('workspace', width=100, anchor='center')
table.heading('mode', text='Controle'); table.column('mode', width=130, anchor='center')
table.pack(fill='both', expand=True)
status = tk.StringVar(value='Super+6 visita sem pausar o agente. Super+Alt+A assume a janela focada. Super+1 devolve sozinho.')

def refresh():
    try:
        entries = json.loads((RUNTIME / 'views.json').read_text())
        for name in table.get_children():
            if name not in entries: table.delete(name)
        for name, entry in sorted(entries.items(), key=lambda pair: pair[1]['workspace']):
            mode = 'Você' if (RUNTIME / name / 'human-control').exists() else 'Agente'
            values = (entry['workspace'], mode)
            if table.exists(name): table.item(name, values=values)
            else: table.insert('', 'end', iid=name, text=name, values=values)
        if not table.selection() and table.get_children(): table.selection_set(table.get_children()[0])
    except (OSError, ValueError):
        status.set('O serviço de acompanhamento não está disponível.')
    root.after(2000, refresh)

def selected():
    current = table.selection()
    if not current: raise RuntimeError('Selecione uma bancada primeiro.')
    return current[0]

def show():
    try:
        name = selected()
        info = views('ensure', name)
        title = json.dumps(f'Bancada dos agentes — {name} - TigerVNC', ensure_ascii=False)
        # This is a direct human button action, never called from agent input.
        code = (f'for _,w in ipairs(hl.get_windows()) do '
                f'if w.pid=={info["viewer_pid"]} and w.class=="Vncviewer" and w.title=={title} then '
                'hl.dispatch(hl.dsp.focus({window=w})); break end end')
        subprocess.run(['hyprctl', 'eval', code], check=True, capture_output=True, text=True, timeout=5)
    except Exception as exc: messagebox.showerror('Bancadas', str(exc))

def change(action):
    try:
        name = selected()
        views(action, name)
        if action == 'collaborate':
            status.set('Controle com você. Super+1 (ou Devolver ao agente) devolve; o agente continua pausado só enquanto você estiver no workspace da bancada.')
        else:
            status.set('Controle devolvido. A janela retornou ao workspace reservado.')
        mode = 'Você' if action == 'collaborate' else 'Agente'
        values = list(table.item(name, 'values')); values[1] = mode; table.item(name, values=values)
    except Exception as exc: messagebox.showerror('Bancadas', str(exc))

buttons = ttk.Frame(outer)
buttons.pack(fill='x', pady=(14,10))
ttk.Button(buttons, text='Mostrar tela', command=show).pack(side='left')
ttk.Button(buttons, text='Assumir controle', command=lambda: change('collaborate')).pack(side='left', padx=8)
ttk.Button(buttons, text='Devolver ao agente', command=lambda: change('resume')).pack(side='left')
ttk.Label(outer, textvariable=status, wraplength=695).pack(anchor='w')
refresh()
root.mainloop()
