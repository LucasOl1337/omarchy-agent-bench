#!/usr/bin/env python3
"""Agent bench home: tools, local files, operating guide and input test."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from toolkit import catalog

name, state = sys.argv[1], Path(sys.argv[2])
# One home per display. Opening Home again leaves existing applications intact.
lock = (state / 'home.lock').open('a')
try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    # This process inherits only the bench display. Restore its own Home window.
    found=subprocess.run(['xdotool','search','--name','^Central da bancada .*'+name+'$'],capture_output=True,text=True)
    if found.returncode==0:
        wid=found.stdout.splitlines()[0]
        subprocess.run(['xdotool','windowmap',wid,'windowactivate',wid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    sys.exit(0)
bench = str(Path(__file__).resolve().parent.parent / 'bin' / 'agent-bench')
base = Path(__file__).resolve().parent
runtime = Path('/run/user') / str(os.getuid()) / 'agent-bench'
bg, panel, fg, muted, accent = '#101b29', '#1b2c40', '#e8f0f8', '#a6b8ca', '#67e8cf'
root = tk.Tk(); root.title('Central da bancada — ' + name); root.geometry('1100x880+30+30'); root.minsize(800, 700); root.configure(bg=bg)
root.option_add('*Font', 'sans 11')
style=ttk.Style(); style.theme_use('clam')
style.configure('TNotebook', background=bg, borderwidth=0)
style.configure('TNotebook.Tab', padding=(20,12), background=panel, foreground=fg)
style.map('TNotebook.Tab', background=[('selected',accent)], foreground=[('selected',bg)])
style.configure('TFrame',background=bg)
style.configure('Treeview',background=panel,fieldbackground=panel,foreground=fg,rowheight=30)
style.configure('Treeview.Heading',background=panel,foreground=fg)
header=tk.Frame(root,bg=bg);header.pack(fill='x',padx=28,pady=(24,8))
tk.Label(header,text='AMBIENTE DOS AGENTES',bg=bg,fg=accent,font=('sans',10,'bold')).pack(anchor='w')
tk.Label(header,text=name,bg=bg,fg=fg,font=('sans',26,'bold')).pack(anchor='w',pady=(6,8))
status=tk.StringVar();tk.Label(header,textvariable=status,bg=bg,fg=muted).pack(anchor='w')
def refresh_status():
    try:
        view=json.loads((runtime/'views.json').read_text()).get(name,{})
        control='Humano no controle · agente pausado' if (runtime/name/'human-control').exists() else 'Agente no controle'
        status.set(f"Workspace {view.get('workspace','em preparação')}  ·  {os.environ.get('DISPLAY','')}  ·  {control}  ·  Clipboard próprio")
    except (OSError,ValueError): status.set('Preparando identificação da bancada…')
    root.after(3000,refresh_status)
refresh_status()
notice=tk.StringVar(value='Toda interface desta tarefa fica aqui. Super+6 visita; Super+Alt+A assume; Super+1 devolve. Workspaces 1–5 pertencem ao humano.')
def run(*args):
    try:
        result=subprocess.run([bench,*args],capture_output=True,text=True,timeout=25)
        notice.set('Aberto nesta bancada.' if result.returncode==0 else result.stderr.strip()[:180])
    except (OSError,subprocess.TimeoutExpired) as exc: notice.set(str(exc))
actions=tk.Frame(root,bg=bg);actions.pack(fill='x',padx=28,pady=16)
def button(parent,label,command):
    b=tk.Button(parent,text=label,command=command,bg=panel,fg=fg,activebackground=accent,activeforeground=bg,relief='flat',padx=18,pady=12,cursor='hand2');b.pack(side='left',padx=(0,8));return b
button(actions,'Abrir navegador',lambda:run('browser',name))
button(actions,'Abrir terminal',lambda:run('terminal',name))
notebook=ttk.Notebook(root);notebook.pack(fill='both',expand=True,padx=28,pady=(0,12))
def tab(label):
    frame=ttk.Frame(notebook,padding=14);notebook.add(frame,text=label);return frame

def text_panel(parent,content):
    box=tk.Text(parent,height=12,wrap='word',bg=panel,fg=fg,relief='flat',padx=16,pady=14,insertbackground=accent)
    scroll=ttk.Scrollbar(parent,command=box.yview);scroll.pack(side='right',fill='y');box.configure(yscrollcommand=scroll.set);box.pack(fill='both',expand=True)
    box.insert('1.0',content);box.configure(state='disabled');return box

tools=tab('Ferramentas')
tree=ttk.Treeview(tools,columns=('tool','purpose','status'),show='headings',selectmode='browse')
for col,title,width in [('tool','Ferramenta',150),('purpose','Quando usar',620),('status','Disponibilidade',140)]:tree.heading(col,text=title);tree.column(col,width=width)
scroll=ttk.Scrollbar(tools,command=tree.yview);scroll.pack(side='right',fill='y');tree.configure(yscrollcommand=scroll.set);tree.pack(fill='both',expand=True)
for tool in catalog():tree.insert('','end',values=(tool['name'],tool['purpose'],'Instalada' if tool['available'] else 'Não instalada'))

files=tab('Arquivos')
path_var=tk.StringVar(value=str(state))
bar=tk.Frame(files,bg=bg);bar.pack(fill='x',pady=(0,10))
entry_path=tk.Entry(bar,textvariable=path_var,bg=panel,fg=fg,insertbackground=accent,relief='flat');entry_path.pack(side='left',fill='x',expand=True,ipady=10)
filelist=tk.Listbox(files,height=12,bg=panel,fg=fg,selectbackground=accent,selectforeground=bg,relief='flat');filelist.pack(fill='both',expand=True)
file_paths=[]
def list_files(path=None):
    try:
        folder=Path(path or path_var.get()).expanduser().resolve(); entries=sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower()))
        file_paths[:]=[folder.parent]+entries;filelist.delete(0,'end');filelist.insert('end','../')
        for p in entries:filelist.insert('end',('▸ ' if p.is_dir() else '  ')+p.name)
        path_var.set(str(folder))
    except OSError as exc:notice.set(str(exc))
def open_file(_=None):
    if not filelist.curselection():return
    p=file_paths[filelist.curselection()[0]]
    if p.is_dir():list_files(p);return
    try:
        if p.stat().st_size>500000:notice.set('Arquivo grande; use o terminal para consultar.');return
        content=p.read_text(); win=tk.Toplevel(root);win.title(p.name);win.geometry('900x600');text_panel(win,content)
    except (OSError,UnicodeError):notice.set('Arquivo binário ou indisponível; use a ferramenta adequada pelo terminal.')
button(bar,'Abrir pasta',list_files);entry_path.bind('<Return>',lambda _:list_files());filelist.bind('<Double-Button-1>',open_file);list_files()

guide=tab('Guia');text_panel(guide,(base/'USAGE.md').read_text())
diag=tab('Diagnóstico');diag_button=tk.Frame(diag,bg=bg);diag_button.pack(fill='x',pady=(0,10));diag_text=text_panel(diag,'Clique em Verificar para consultar esta bancada e seu navegador.')
def diagnose():
    try:
        result=subprocess.run([bench,'doctor',name],capture_output=True,text=True,timeout=8)
        diag_text.configure(state='normal');diag_text.delete('1.0','end');diag_text.insert('1.0',result.stdout or result.stderr);diag_text.configure(state='disabled')
    except (OSError,subprocess.TimeoutExpired) as exc:notice.set(str(exc))
button(diag_button,'Verificar',diagnose)

test=tab('Teste de entrada');entry=tk.Entry(test,name='test_input',bg=panel,fg=fg,insertbackground=accent,font=('sans',15));entry.pack(fill='x',pady=12);entry.insert(0,'Digite aqui para testar')
entry.bind('<Control-a>',lambda event: (entry.selection_range(0,'end'), 'break')[-1])
result=tk.StringVar(value='Teste inofensivo de clique, texto e arraste.');clicks=0
def record():
    global clicks
    clicks+=1;(state/'welcome-result.json').write_text(json.dumps({'bench':name,'kind':'click','text':entry.get(),'clicks':clicks},ensure_ascii=False));result.set(f'Teste {clicks}: {entry.get()}')
row=tk.Frame(test,bg=bg);row.pack(fill='x');button(row,'Confirmar teste',record);tk.Label(test,textvariable=result,bg=bg,fg=accent).pack(anchor='w',pady=18)
canvas=tk.Canvas(test,height=170,bg=panel,highlightthickness=0);canvas.pack(fill='x');item=canvas.create_rectangle(25,30,105,100,fill=accent,outline='');last=[0,0]
def press(e):last[:]=[e.x,e.y]
def drag(e):canvas.move(item,e.x-last[0],e.y-last[1]);last[:]=[e.x,e.y]
def release(e):(state/'drag-result.json').write_text(json.dumps({'bench':name,'coords':canvas.coords(item)}))
canvas.tag_bind(item,'<ButtonPress-1>',press);canvas.tag_bind(item,'<B1-Motion>',drag);canvas.tag_bind(item,'<ButtonRelease-1>',release)
tk.Label(root,textvariable=notice,bg=bg,fg=muted,wraplength=1040,anchor='w').pack(fill='x',padx=28,pady=(0,18))
root.mainloop()
