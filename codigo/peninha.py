"""
Peninha — conversa ao vivo entre computadores, em qualquer rede.

- As mensagens passam por um servidor público de mensagens (MQTT), num canal
  secreto calculado a partir da SALA + SENHA. Tudo vai criptografado:
  quem não tem a senha não consegue ler nada.
- O outro lado vê o texto enquanto você digita.
- Cada computador guarda o próprio histórico (SQLite). Se um PC estava
  desligado, ao abrir ele pede ao outro as mensagens que perdeu.

Para gerar o executável, use o gerar_exe.bat.
"""
import base64
import hashlib
import json
import math
import os
import queue
import random
import sqlite3
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import uuid
from datetime import datetime
from tkinter import messagebox, simpledialog

import paho.mqtt.client as mqtt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ---------------------------------------------------------------- ajustes
NOME = "Peninha"
VERSAO = "1.0"

BROKER = os.environ.get("PENINHA_BROKER", "broker.hivemq.com")
PORTA = int(os.environ.get("PENINHA_PORTA", "1883"))
# Atenção: os textos "recados" dentro de derivar() e Conexao não mudam com o nome
# do programa. Eles definem o canal e a chave: trocar quebraria a conversa com
# quem ainda usa a versão antiga e o histórico já guardado.


def _pasta_dados():
    if os.environ.get("PENINHA_PASTA"):
        return os.environ["PENINHA_PASTA"]
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    nova, antiga = os.path.join(base, "Peninha"), os.path.join(base, "Recados")
    if not os.path.exists(nova) and os.path.exists(antiga):
        try:  # quem vinha do "Recados" traz junto a configuração e o histórico
            os.rename(antiga, nova)
        except OSError:  # pasta em uso (o Recados antigo está aberto?): continua usando a antiga
            return antiga
    return nova


PASTA = _pasta_dados()
os.makedirs(PASTA, exist_ok=True)
ARQ_CONFIG = os.path.join(PASTA, "config.json")
ARQ_BANCO = os.path.join(PASTA, "historico.db")

MAX_TEXTO = 5000

# tamanho da letra da conversa
FONTE_PADRAO = 10
FONTE_MIN = 8
FONTE_MAX = 28

# cores
ESCURO = "#2d3a3a"
VERDE = "#27ae60"
VERMELHO = "#c0392b"

TEMAS = {
    "claro": {
        "fundo": "#f3f1ec", "texto": "#222222", "topo": ESCURO, "botao": ESCURO, "botao_ativo": "#3f5252",
        "rodape": "#ffffff", "caixa": "#ffffff", "borda": "#9a9a9a", "borda_foco": ESCURO,
        "eu_nome": "#1e7d4a", "outro_nome": ESCURO, "hora": "#999999", "ao_vivo": "#555555",
        "selecao": "#cfe3d8",
    },
    "escuro": {
        "fundo": "#1d2223", "texto": "#e4e6e6", "topo": "#121617", "botao": "#2f6b4c", "botao_ativo": "#3a815d",
        "rodape": "#262c2d", "caixa": "#30383a", "borda": "#465153", "borda_foco": "#6fbf92",
        "eu_nome": "#6fcf97", "outro_nome": "#8ec5dc", "hora": "#7a8587", "ao_vivo": "#a3acae",
        "selecao": "#3d5a4b",
    },
}


# ---------------------------------------------------------------- tela (escala e arquivos)
ESCALA = 1.0  # 1.0 = tela em 100%; 1.25 = 125%... (calculada ao abrir)


def px(v):
    """Converte uma medida pensada para tela 100% para a escala real do monitor."""
    return int(round(v * ESCALA))


def recurso(nome):
    """Caminho de um arquivo que vai junto com o programa (ícone, imagens da abertura)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


def preparar_escala_windows():
    """Pede ao Windows para desenhar nítido (sem esticar a janela) e agrupa a barra de tarefas."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Peninha.Conversa")
    except Exception:
        pass


def tema_do_windows():
    """'escuro' se o Windows estiver no modo escuro, senão 'claro'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "claro" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "escuro"
    except Exception:
        return "claro"


# ---------------------------------------------------------------- segurança
def derivar(sala: str, senha: str):
    """Transforma sala+senha em uma chave de criptografia e um canal secreto."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=("recados:" + sala.strip().lower()).encode(),
        iterations=300_000,
    )
    chave = kdf.derive(senha.encode())
    topico = "recados/" + hashlib.sha256(b"topico" + chave).hexdigest()[:32]
    return base64.urlsafe_b64encode(chave).decode(), topico


# ---------------------------------------------------------------- configuração
def ler_config():
    try:
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def salvar_config(cfg):
    with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- histórico
class Historico:
    def __init__(self, arquivo=ARQ_BANCO):
        self.con = sqlite3.connect(arquivo, check_same_thread=False)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS mensagens ("
            "id TEXT PRIMARY KEY, topico TEXT, autor TEXT, nome TEXT, texto TEXT, ts REAL)"
        )
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_top_ts ON mensagens(topico, ts)")
        self.con.commit()

    def salvar(self, m, topico) -> bool:
        """Guarda a mensagem. Devolve True se ela ainda não existia."""
        cur = self.con.execute(
            "INSERT OR IGNORE INTO mensagens (id, topico, autor, nome, texto, ts) VALUES (?,?,?,?,?,?)",
            (m["id"], topico, m["autor"], m["nome"], m["texto"], m["ts"]),
        )
        self.con.commit()
        return cur.rowcount == 1

    def ultimas(self, topico, qtd=300):
        linhas = self.con.execute(
            "SELECT id, autor, nome, texto, ts FROM mensagens WHERE topico=? ORDER BY ts DESC LIMIT ?",
            (topico, qtd),
        ).fetchall()
        return [dict(zip(("id", "autor", "nome", "texto", "ts"), l)) for l in reversed(linhas)]

    def desde(self, topico, ts, qtd=200):
        linhas = self.con.execute(
            "SELECT id, autor, nome, texto, ts FROM mensagens WHERE topico=? AND ts>? ORDER BY ts LIMIT ?",
            (topico, ts, qtd),
        ).fetchall()
        return [dict(zip(("id", "autor", "nome", "texto", "ts"), l)) for l in linhas]

    def ultimo_ts(self, topico):
        r = self.con.execute("SELECT MAX(ts) FROM mensagens WHERE topico=?", (topico,)).fetchone()
        return r[0] or 0

    def apagar(self, topico):
        self.con.execute("DELETE FROM mensagens WHERE topico=?", (topico,))
        self.con.commit()


# ---------------------------------------------------------------- conexão
class Conexao:
    """Fala com o servidor de mensagens. Tudo que chega vai para a `fila`."""

    def __init__(self, topico, chave, meu_id, fila):
        self.topico = topico
        self.fernet = Fernet(chave.encode())
        self.fila = fila
        self.cli = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"recados-{meu_id[:8]}-{uuid.uuid4().hex[:6]}",
        )
        self.cli.on_connect = self._conectou
        self.cli.on_disconnect = self._caiu
        self.cli.on_message = self._chegou
        self.cli.reconnect_delay_set(1, 20)

    def iniciar(self):
        self.cli.connect_async(BROKER, PORTA, keepalive=30)
        self.cli.loop_start()

    def parar(self):
        try:
            self.cli.disconnect()
            self.cli.loop_stop()
        except Exception:
            pass

    def _conectou(self, cli, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.fila.put(("status", False))
            return
        cli.subscribe(self.topico, qos=1)
        self.fila.put(("status", True))

    def _caiu(self, cli, userdata, flags, reason_code, properties):
        self.fila.put(("status", False))

    def _chegou(self, cli, userdata, msg):
        try:
            dados = json.loads(self.fernet.decrypt(msg.payload))
        except (InvalidToken, ValueError, TypeError):
            return  # não é nosso (ou senha diferente): ignora
        if isinstance(dados, dict):
            self.fila.put(("dados", dados))

    def enviar(self, dados, qos=1):
        token = self.fernet.encrypt(json.dumps(dados, ensure_ascii=False).encode())
        self.cli.publish(self.topico, token, qos=qos)


def msg_valida(m):
    try:
        return (
            isinstance(m.get("id"), str) and len(m["id"]) <= 64
            and isinstance(m.get("autor"), str) and len(m["autor"]) <= 64
            and isinstance(m.get("nome"), str)
            and isinstance(m.get("texto"), str) and m["texto"].strip()
            and isinstance(m.get("ts"), (int, float))
        )
    except Exception:
        return False


def limpar_msg(m):
    return {
        "id": m["id"], "autor": m["autor"], "nome": m["nome"][:40],
        "texto": m["texto"][:MAX_TEXTO], "ts": float(m["ts"]),
    }


# ---------------------------------------------------------------- tela de configuração
def pedir_config(root, atual=None):
    """Janela para nome, sala e senha. Devolve o config ou None se cancelar."""
    atual = atual or {}
    janela = tk.Toplevel(root)
    janela.title(f"{NOME} — configurar")
    janela.resizable(False, False)
    janela.configure(padx=px(20), pady=px(16))
    resultado = {}

    tk.Label(janela, text="Configurar conversa", font=("Segoe UI", 13, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    tk.Label(
        janela,
        text="Os dois computadores precisam usar a MESMA sala e a MESMA senha.\n"
             "A senha protege as mensagens: escolha uma que só vocês saibam.",
        justify="left", fg="#555", font=("Segoe UI", 9),
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(2), px(12)))

    campos = {}
    for i, (chave, rotulo, mostrar) in enumerate(
        [("nome", "Seu nome", True), ("sala", "Sala", True), ("senha", "Senha", False)], start=2
    ):
        tk.Label(janela, text=rotulo + ":", font=("Segoe UI", 10)).grid(row=i, column=0, sticky="w", pady=px(4))
        e = tk.Entry(janela, width=30, font=("Segoe UI", 10), show="" if mostrar else "•")
        e.grid(row=i, column=1, pady=px(4), padx=(px(8), 0))
        if chave in ("nome", "sala"):
            e.insert(0, atual.get(chave, ""))
        campos[chave] = e

    def confirmar(_=None):
        nome = campos["nome"].get().strip()
        sala = campos["sala"].get().strip()
        senha = campos["senha"].get()
        if not nome or not sala or len(senha) < 6:
            messagebox.showwarning(NOME, "Preencha nome e sala, e use uma senha com pelo menos 6 caracteres.",
                                   parent=janela)
            return
        janela.config(cursor="watch")
        janela.update()
        chave, topico = derivar(sala, senha)
        resultado.update(nome=nome[:40], sala=sala, chave=chave, topico=topico,
                         id=atual.get("id") or uuid.uuid4().hex)
        janela.destroy()

    tk.Button(janela, text="Entrar", command=confirmar, bg=ESCURO, fg="white",
              relief="flat", padx=px(18), pady=px(4), font=("Segoe UI", 10)).grid(
        row=5, column=0, columnspan=2, pady=(px(14), 0))
    janela.bind("<Return>", confirmar)
    (campos["nome"] if not atual.get("nome") else campos["senha"]).focus_set()

    janela.transient(root) if root.winfo_viewable() else None
    janela.grab_set()
    root.wait_window(janela)
    return resultado or None


# ---------------------------------------------------------------- animações
def tamanho_para_escala(opcoes, base):
    """Das imagens prontas (ex.: 64, 80, 96), escolhe a que fica mais perto do tamanho certo para esta tela."""
    return min(opcoes, key=lambda n: abs(n - base * ESCALA))


def quicar(p):
    """Movimento de bola quicando (0 -> 1)."""
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


def misturar(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def estrela(cx, cy, r):
    """Brilhinho de 4 pontas."""
    pts = []
    for k in range(8):
        ang = math.pi / 4 * k - math.pi / 2
        rr = r if k % 2 == 0 else r * 0.32
        pts += [cx + rr * math.cos(ang), cy + rr * math.sin(ang)]
    return pts


class Abertura:
    """Tela de abertura animada, no estilo dos programas dos anos 2000. Um clique pula."""
    DURACAO = 3.0

    def __init__(self, root, ao_terminar):
        self.ao_terminar = ao_terminar
        self.top = top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        self._alfa(0.0)
        self.W, self.H = W, H = px(460), px(270)
        top.geometry(f"{W}x{H}+{(root.winfo_screenwidth() - W) // 2}+{(root.winfo_screenheight() - H) // 2}")
        c = self.c = tk.Canvas(top, width=W, height=H, highlightthickness=0, bd=0)
        c.pack()

        # fundo "aqua": metade de cima brilhante, metade de baixo azul-céu
        meio = int(H * 0.46)
        for y in range(H):
            cor = (misturar("#ffffff", "#e2f2fc", y / meio) if y < meio
                   else misturar("#c9e7f8", "#8fcaee", (y - meio) / (H - meio)))
            c.create_line(0, y, W, y, fill=cor)
        c.create_rectangle(1, 1, W - 2, H - 2, outline="#3b7fb8", width=2)

        tam = tamanho_para_escala((140, 175, 210), 140)
        self.img = tk.PhotoImage(file=recurso(f"imagens/abertura_{tam}_normal.png"))
        self.img_pisc = tk.PhotoImage(file=recurso(f"imagens/abertura_{tam}_piscando.png"))
        self.px_pena, self.py_pena = px(118), px(122)
        self.pena = c.create_image(self.px_pena, -tam, image=self.img)
        self.brilhos = []
        for dx, dy, r, fase in ((-70, -62, 9, 0.0), (66, -70, 7, 0.35), (78, 20, 8, 0.7),
                                (-80, 30, 6, 0.15), (-30, -88, 5, 0.55), (40, 70, 6, 0.85)):
            item = c.create_polygon(estrela(0, 0, 1), fill="#ffe066", outline="#ffffff", state="hidden")
            self.brilhos.append((item, self.px_pena + px(dx), self.py_pena + px(dy), px(r), fase))

        x_tit, y_tit = px(214), px(100)
        fonte_tit = ("Trebuchet MS", 32, "bold italic")
        self.tit_sombra = c.create_text(x_tit + px(3), y_tit + px(3), anchor="w", font=fonte_tit, fill="#86bde3")
        self.tit = c.create_text(x_tit, y_tit, anchor="w", font=fonte_tit, fill="#1d4f91")
        self.sub = c.create_text(x_tit + px(4), y_tit + px(44), anchor="w", text="recados ao vivo, de um PC pro outro",
                                 font=("Trebuchet MS", 10, "italic"), fill="#3d6f9e", state="hidden")

        # barrinha de carregamento estilo XP: blocos verdes passando
        self.bx0, self.bx1 = px(40), W - px(40)
        by0, by1 = H - px(52), H - px(38)
        c.create_rectangle(self.bx0 - 1, by0 - 1, self.bx1 + 1, by1 + 1, outline="#6d8fb3", fill="#ffffff")
        self.bloco_l, self.bloco_gap = px(11), px(3)
        self.blocos = [c.create_rectangle(0, by0 + 2, 0, by1 - 1, fill="#43c443", outline="#2e9e2e")
                       for _ in range(4)]
        self.carregando = c.create_text(self.bx0, H - px(24), anchor="w", font=("Tahoma", 8), fill="#2d5f8e")
        c.create_text(self.bx1, H - px(24), anchor="e", font=("Tahoma", 8), fill="#2d5f8e", text=f"versão {VERSAO}")

        c.bind("<Button-1>", self.pular)
        self._cantos_redondos()
        self.t0 = time.monotonic()
        self._quadro()

    def _alfa(self, a):
        try:
            self.top.attributes("-alpha", max(0.0, min(1.0, a)))
        except tk.TclError:
            pass

    def _cantos_redondos(self):
        try:  # Windows 11: cantos arredondados também numa janela sem moldura
            import ctypes
            self.top.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id())
            v = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    def pular(self, _=None):
        agora = time.monotonic() - self.t0
        if agora < self.DURACAO - 0.35:
            self.t0 = time.monotonic() - (self.DURACAO - 0.35)

    def _quadro(self):
        t = time.monotonic() - self.t0
        if t >= self.DURACAO:
            self.top.destroy()
            self.ao_terminar()
            return
        c = self.c
        self._alfa(min(t / 0.3, (self.DURACAO - t) / 0.35))
        # a Peninha cai e quica
        p = min(1.0, t / 0.9)
        c.coords(self.pena, self.px_pena, -px(100) + (self.py_pena + px(100)) * quicar(p))
        piscando = 1.35 < t < 1.5 or 2.25 < t < 2.4
        c.itemconfigure(self.pena, image=self.img_pisc if piscando else self.img)
        # brilhinhos piscando em volta
        for item, x, y, r, fase in self.brilhos:
            if t > 0.8:
                k = 0.5 + 0.5 * math.sin(2 * math.pi * (t * 1.4 + fase))
                c.coords(item, *estrela(x, y, max(1.0, r * (0.3 + k))))
                c.itemconfigure(item, state="normal")
        # o nome sendo escrito letra por letra
        letras = max(0, min(len(NOME), int((t - 0.55) / 0.09)))
        c.itemconfigure(self.tit, text=NOME[:letras])
        c.itemconfigure(self.tit_sombra, text=NOME[:letras])
        if t > 1.25:
            c.itemconfigure(self.sub, state="normal")
        # barrinha
        larg_grupo = 4 * (self.bloco_l + self.bloco_gap)
        trilho = self.bx1 - self.bx0
        ini = self.bx0 - larg_grupo + (t * px(190)) % (trilho + larg_grupo)
        for i, b in enumerate(self.blocos):
            x0 = ini + i * (self.bloco_l + self.bloco_gap)
            x0c, x1c = max(self.bx0 + 2, x0), min(self.bx1 - 1, x0 + self.bloco_l)
            if x1c > x0c:
                c.coords(b, x0c, c.coords(b)[1], x1c, c.coords(b)[3])
                c.itemconfigure(b, state="normal")
            else:
                c.itemconfigure(b, state="hidden")
        c.itemconfigure(self.carregando, text="Carregando" + "." * (int(t * 3) % 4))
        self.top.after(16, self._quadro)


class Mascote:
    """A Peninha ao lado da caixa de texto: respira, pisca, olha em volta, cochila e reage à conversa."""
    QUADROS = ("normal", "esquerda", "direita", "cima", "baixo", "piscando", "surpresa")
    SONO = 120  # segundos sem nada acontecer até ela cochilar

    def __init__(self, pai):
        tam = tamanho_para_escala((48, 60, 72), 48)
        self.imgs = {q: tk.PhotoImage(file=recurso(f"imagens/mascote_{tam}_{q}.png")) for q in self.QUADROS}
        self.w, self.h = tam + px(10), tam + px(10)
        self.canvas = tk.Canvas(pai, width=self.w, height=self.h, highlightthickness=0, bd=0, cursor="hand2")
        self.base_y = self.h - px(2)
        self.item = self.canvas.create_image(self.w // 2, self.base_y, image=self.imgs["normal"], anchor="s")
        self.extra = self.canvas.create_text(self.w - px(6), px(8), text="", font=("Comic Sans MS", 11, "bold"))
        self.canvas.bind("<Button-1>", lambda e: (self.acordar(), self.pular()))  # cutucar a Peninha
        agora = time.monotonic()
        self.atividade = agora
        self.quadro_atual = "normal"
        self.olhar, self.olhar_ate = "normal", 0.0
        self.piscar_ate, self.prox_piscar = 0.0, agora + random.uniform(1.5, 4)
        self.prox_olhar = agora + random.uniform(3, 7)
        self.susto_ate = 0.0
        self.pulo_ini = None
        self._tick()

    # ---- o que a janela avisa
    def acordar(self):
        self.atividade = time.monotonic()

    def pular(self):
        self.pulo_ini = time.monotonic()

    def olhar_para(self, lado, segundos=1.2):
        self.acordar()
        if time.monotonic() >= self.susto_ate:
            self.olhar, self.olhar_ate = lado, time.monotonic() + segundos

    def susto(self):
        self.acordar()
        self.susto_ate = time.monotonic() + 1.4
        self.pular()

    def mudar_fundo(self, cor, cor_texto):
        self.canvas.configure(bg=cor)
        self.cor_texto = cor_texto

    # ---- animação
    def _tick(self):
        if not self.canvas.winfo_exists():
            return
        agora = time.monotonic()
        dormindo = agora - self.atividade > self.SONO
        if not dormindo:
            if agora >= self.prox_piscar:
                self.piscar_ate = agora + 0.15
                dupla = random.random() < 0.25  # às vezes pisca duas vezes
                self.prox_piscar = agora + (0.35 if dupla else random.uniform(2.5, 6))
            if agora >= self.prox_olhar:
                self.olhar = random.choice(("esquerda", "direita", "cima", "baixo", "esquerda", "direita"))
                self.olhar_ate = agora + random.uniform(0.8, 1.6)
                self.prox_olhar = agora + random.uniform(4, 9)
                if random.random() < 0.12:  # de vez em quando, um pulinho à toa
                    self.pular()

        if agora < self.susto_ate:
            q = "surpresa"
        elif dormindo or agora < self.piscar_ate:
            q = "piscando"
        elif agora < self.olhar_ate:
            q = self.olhar
        else:
            q = "normal"
        if q != self.quadro_atual:
            self.quadro_atual = q
            self.canvas.itemconfigure(self.item, image=self.imgs[q])

        # respirar (sobe e desce devagar) + pulinho
        dy = math.sin(agora * (1.2 if dormindo else 2.4)) * px(1.5)
        if self.pulo_ini is not None:
            p = (agora - self.pulo_ini) / 0.45
            if p >= 1:
                self.pulo_ini = None
            else:
                dy -= math.sin(math.pi * p) * px(8)
        self.canvas.coords(self.item, self.w // 2, self.base_y + dy)

        # "!" no susto, "zZ" flutuando quando cochila
        cor = getattr(self, "cor_texto", "#555555")
        if agora < self.susto_ate:
            self.canvas.itemconfigure(self.extra, text="!", fill="#e74c3c")
            self.canvas.coords(self.extra, self.w - px(8), px(10))
        elif dormindo:
            fase = (agora * 0.6) % 1.0
            self.canvas.itemconfigure(self.extra, text="z" if fase < 0.5 else "zZ", fill=cor)
            self.canvas.coords(self.extra, self.w - px(10) + px(4) * fase, px(18) - px(10) * fase)
        else:
            self.canvas.itemconfigure(self.extra, text="")
        self.canvas.after(100 if dormindo else 50, self._tick)


# ---------------------------------------------------------------- janela principal
class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.hist = Historico()
        self.fila = queue.Queue()
        self.con = None
        self.focado = True
        self.nao_lidas = 0
        self._dig_agendado = None
        self._ult_dig = ""
        self._limpar_preview = None
        self._ultimo_ts_mostrado = 0

        # fontes da conversa: mudar o tamanho delas atualiza a tela na hora
        self.f_texto = tkfont.Font(family="Segoe UI")
        self.f_nome = tkfont.Font(family="Segoe UI", weight="bold")
        self.f_hora = tkfont.Font(family="Segoe UI")
        self.f_aviso = tkfont.Font(family="Segoe UI", slant="italic")
        self.f_ao_vivo = tkfont.Font(family="Segoe UI", slant="italic")
        try:
            self.tam_fonte = min(FONTE_MAX, max(FONTE_MIN, int(cfg.get("fonte", FONTE_PADRAO))))
        except (TypeError, ValueError):
            self.tam_fonte = FONTE_PADRAO
        self._aplicar_fonte()

        root.title(NOME)
        root.minsize(px(320), px(360))
        self._posicionar_janela()

        # ---- topo (o status vai primeiro: com nome comprido, quem é cortado é o nome)
        self.topo = tk.Frame(root, padx=px(12), pady=px(8))
        self.topo.pack(fill="x")
        self.lbl_status = tk.Label(self.topo, text=" conectando… ", bg="#7f8c8d", fg="white", font=("Segoe UI", 8))
        self.lbl_status.pack(side="right")
        self.lbl_quem = tk.Label(self.topo, fg="white", font=("Segoe UI", 10, "bold"), anchor="w")
        self.lbl_quem.pack(side="left", fill="x", expand=True)

        menu = tk.Menu(root)
        opcoes = tk.Menu(menu, tearoff=0)
        opcoes.add_command(label="Trocar meu nome", command=self.trocar_nome)
        opcoes.add_command(label="Trocar sala / senha", command=self.trocar_sala)
        opcoes.add_separator()
        opcoes.add_command(label="Apagar histórico deste computador", command=self.apagar_historico)
        menu.add_cascade(label="Opções", menu=opcoes)
        exibir = tk.Menu(menu, tearoff=0)
        exibir.add_command(label="Aumentar letra", accelerator="Ctrl +", command=lambda: self.mudar_fonte(+1))
        exibir.add_command(label="Diminuir letra", accelerator="Ctrl -", command=lambda: self.mudar_fonte(-1))
        exibir.add_command(label="Tamanho normal", accelerator="Ctrl 0", command=lambda: self.mudar_fonte(None))
        exibir.add_separator()
        self.var_tema = tk.StringVar(value=cfg.get("tema") if cfg.get("tema") in TEMAS else tema_do_windows())
        exibir.add_radiobutton(label="Modo claro", value="claro", variable=self.var_tema,
                               command=lambda: self.mudar_tema(self.var_tema.get()))
        exibir.add_radiobutton(label="Modo escuro", value="escuro", variable=self.var_tema,
                               command=lambda: self.mudar_tema(self.var_tema.get()))
        exibir.add_separator()
        self.var_mascote = tk.BooleanVar(value=cfg.get("mascote", True))
        exibir.add_checkbutton(label="Mostrar a Peninha", variable=self.var_mascote, command=self._mudou_mascote)
        self.var_abertura = tk.BooleanVar(value=cfg.get("abertura", True))
        exibir.add_checkbutton(label="Animação de abertura", variable=self.var_abertura,
                               command=lambda: self._salvar_opcao("abertura", self.var_abertura.get()))
        menu.add_cascade(label="Exibir", menu=exibir)
        root.config(menu=menu)

        # ---- rodapé (empacotado antes da lista para não sumir ao encolher)
        self.rodape = tk.Frame(root, padx=px(8), pady=px(8))
        self.rodape.pack(side="bottom", fill="x")
        self.mascote = None
        try:
            self.mascote = Mascote(self.rodape)
        except tk.TclError:  # sem as imagens (rodando sem a pasta "imagens"): segue sem mascote
            pass
        # caixa + botão numa linha própria, alinhada embaixo: a altura dela é a do texto, não a da Peninha
        self.linha_envio = tk.Frame(self.rodape)
        self.linha_envio.pack(side="left", fill="x", expand=True, anchor="s")
        # o botão vai primeiro: senão a caixa ocupa toda a largura e ele some
        self.btn_enviar = tk.Button(self.linha_envio, text="Enviar", command=self.enviar, fg="white",
                                    relief="flat", font=("Segoe UI", 10), padx=px(14), cursor="hand2")
        self.btn_enviar.pack(side="right", fill="y", padx=(px(8), 0))
        self.caixa = tk.Text(self.linha_envio, height=1, width=1, wrap="word", font=self.f_texto, relief="flat",
                             bd=0, highlightthickness=1, padx=px(6), pady=px(6))
        self.caixa.pack(side="left", fill="both", expand=True)

        self.ao_vivo = tk.Label(root, text="", font=self.f_ao_vivo, anchor="w", justify="left", padx=px(12))
        self.ao_vivo.pack(side="bottom", fill="x")

        # ---- lista de mensagens
        self.meio = tk.Frame(root)
        self.meio.pack(fill="both", expand=True)
        # sem barra de rolagem visível: rola com a rodinha do mouse (ou arrastando a seleção)
        self.lista = tk.Text(self.meio, wrap="word", state="disabled", relief="flat", bd=0, highlightthickness=0,
                             padx=px(12), pady=px(10), font=self.f_texto, cursor="arrow")
        self.lista.pack(side="left", fill="both", expand=True)
        self.lista.tag_configure("eu", justify="right")
        self.lista.tag_configure("outro", justify="left")
        self.lista.tag_configure("eu_nome", font=self.f_nome)
        self.lista.tag_configure("outro_nome", font=self.f_nome)
        self.lista.tag_configure("hora", font=self.f_hora)
        self.lista.tag_configure("texto", spacing3=px(10))
        self.lista.tag_configure("aviso", justify="center", font=self.f_aviso, spacing3=px(10))
        self._margens = None
        self._aplicar_tema()

        # ---- eventos
        self.caixa.bind("<Return>", self._enter)
        self.caixa.bind("<KeyRelease>", self.ao_digitar)
        root.bind("<FocusIn>", self._ganhou_foco)
        root.bind("<FocusOut>", lambda e: setattr(self, "focado", False))
        root.bind("<Configure>", self._ao_redimensionar)
        self.lista.bind("<Configure>", self._ajustar_margens)
        self.caixa.bind("<FocusIn>", lambda e: self.mascote and self.mascote.olhar_para("direita", 0.8), add="+")
        root.protocol("WM_DELETE_WINDOW", self.fechar)
        for tecla in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            root.bind(tecla, lambda e: self.mudar_fonte(+1))
        for tecla in ("<Control-minus>", "<Control-KP_Subtract>"):
            root.bind(tecla, lambda e: self.mudar_fonte(-1))
        for tecla in ("<Control-0>", "<Control-KP_0>"):
            root.bind(tecla, lambda e: self.mudar_fonte(None))
        for w in (self.lista, self.caixa):  # Ctrl + rodinha do mouse (sem rolar a conversa junto)
            w.bind("<Control-MouseWheel>", lambda e: self.mudar_fonte(+1 if e.delta > 0 else -1) or "break")

        self.conectar()
        self.processar_fila()
        self.caixa.focus_set()

    # -------------------------------------------------- conexão
    def conectar(self):
        if self.con:
            self.con.parar()
        self.lbl_quem.config(text=f"{self.cfg['nome']}  ·  sala {self.cfg['sala']}")
        self.recarregar()
        self.con = Conexao(self.cfg["topico"], self.cfg["chave"], self.cfg["id"], self.fila)
        self.con.iniciar()

    def processar_fila(self):
        try:
            while True:
                tipo, dado = self.fila.get_nowait()
                if tipo == "status":
                    self.mudar_status(dado)
                elif tipo == "dados":
                    self.tratar(dado)
        except queue.Empty:
            pass
        self.root.after(100, self.processar_fila)

    def mudar_status(self, online):
        if online:
            self.lbl_status.config(text=" conectado ", bg=VERDE)
            # pede ao outro computador o que perdemos enquanto estávamos fora
            desde = max(0, self.hist.ultimo_ts(self.cfg["topico"]) - 300)
            self.con.enviar({"tipo": "sync", "autor": self.cfg["id"], "desde": desde})
        else:
            self.lbl_status.config(text=" sem conexão ", bg=VERMELHO)

    def tratar(self, d):
        tipo = d.get("tipo")
        autor = d.get("autor")
        meu = self.cfg["id"]

        if tipo == "msg" and msg_valida(d):
            m = limpar_msg(d)
            if self.hist.salvar(m, self.cfg["topico"]):
                self.mostrar(m)
                if m["autor"] != meu:
                    self.ao_vivo.config(text="")
                    if self.mascote:
                        self.mascote.susto()
                    self.avisar()

        elif tipo == "digitando" and autor != meu:
            texto = str(d.get("texto", ""))[-400:]
            nome = str(d.get("nome", "?"))[:40]
            self.ao_vivo.config(text=f"{nome} está escrevendo: {texto}" if texto.strip() else "")
            if texto.strip() and self.mascote:  # a Peninha espia a conversa lá em cima
                self.mascote.olhar_para("cima", 1.5)
            if self._limpar_preview:
                self.root.after_cancel(self._limpar_preview)
            self._limpar_preview = self.root.after(60_000, lambda: self.ao_vivo.config(text=""))

        elif tipo == "sync" and autor != meu:
            try:
                desde = float(d.get("desde", 0))
            except (TypeError, ValueError):
                return
            msgs = self.hist.desde(self.cfg["topico"], desde)
            for i in range(0, len(msgs), 20):
                self.con.enviar({"tipo": "lote", "para": autor, "msgs": msgs[i:i + 20]})

        elif tipo == "lote" and d.get("para") == meu and isinstance(d.get("msgs"), list):
            novas = 0
            for m in d["msgs"]:
                if isinstance(m, dict) and msg_valida(m):
                    novas += self.hist.salvar(limpar_msg(m), self.cfg["topico"])
            if novas:
                self.recarregar()
                if self.mascote:
                    self.mascote.susto()
                self.avisar()

    # -------------------------------------------------- enviar / digitar
    def _enter(self, e):
        if e.state & 0x0001:  # Shift+Enter = nova linha
            return None
        self.enviar()
        return "break"

    def enviar(self):
        texto = self.caixa.get("1.0", "end-1c").strip()
        if not texto:
            return
        m = {"tipo": "msg", "id": uuid.uuid4().hex, "autor": self.cfg["id"], "nome": self.cfg["nome"],
             "texto": texto[:MAX_TEXTO], "ts": time.time()}
        self.hist.salvar(limpar_msg(m), self.cfg["topico"])
        self.mostrar(limpar_msg(m))
        self.con.enviar(m)  # se estiver sem internet, sai assim que voltar
        self.caixa.delete("1.0", "end")
        self._ajustar_caixa()
        self._ult_dig = ""
        if self.mascote:
            self.mascote.acordar()
            self.mascote.pular()
        self.con.enviar({"tipo": "digitando", "autor": self.cfg["id"], "nome": self.cfg["nome"], "texto": ""}, qos=0)

    def ao_digitar(self, _=None):
        self._ajustar_caixa()
        if self.mascote:  # a Peninha fica de olho no que você escreve
            self.mascote.olhar_para("direita", 1.0)
        if not self._dig_agendado:  # manda no máximo ~3 atualizações por segundo
            self._dig_agendado = self.root.after(300, self._enviar_digitando)

    def _enviar_digitando(self):
        self._dig_agendado = None
        texto = self.caixa.get("1.0", "end-1c")
        if texto != self._ult_dig:
            self._ult_dig = texto
            self.con.enviar({"tipo": "digitando", "autor": self.cfg["id"], "nome": self.cfg["nome"],
                             "texto": texto[-400:]}, qos=0)

    # -------------------------------------------------- tela
    def mostrar(self, m):
        if m["ts"] < self._ultimo_ts_mostrado:  # chegou fora de ordem: redesenha tudo
            self.recarregar()
            return
        self._ultimo_ts_mostrado = m["ts"]
        lado = "eu" if m["autor"] == self.cfg["id"] else "outro"
        hora = datetime.fromtimestamp(m["ts"]).strftime("%d/%m %H:%M")
        self.lista.config(state="normal")
        self.lista.insert("end", m["nome"], (lado, lado + "_nome"))
        self.lista.insert("end", "  " + hora + "\n", (lado, "hora"))
        self.lista.insert("end", m["texto"] + "\n", (lado, "texto"))
        self.lista.config(state="disabled")
        self.lista.see("end")

    def recarregar(self):
        self.lista.config(state="normal")
        self.lista.delete("1.0", "end")
        self.lista.config(state="disabled")
        self._ultimo_ts_mostrado = 0
        msgs = self.hist.ultimas(self.cfg["topico"])
        if not msgs:
            self.lista.config(state="normal")
            self.lista.insert("end", "Nenhuma mensagem ainda. Diga oi!\n", "aviso")
            self.lista.config(state="disabled")
        for m in msgs:
            self.mostrar(m)

    def _aplicar_fonte(self):
        t = self.tam_fonte
        self.f_texto.configure(size=t)
        self.f_nome.configure(size=t - 1)
        self.f_hora.configure(size=t - 2)
        self.f_aviso.configure(size=t - 2)
        self.f_ao_vivo.configure(size=t - 1)

    def mudar_fonte(self, passo):
        """+1 aumenta, -1 diminui, None volta ao tamanho normal."""
        novo = FONTE_PADRAO if passo is None else min(FONTE_MAX, max(FONTE_MIN, self.tam_fonte + passo))
        if novo == self.tam_fonte:
            return
        no_fim = self.lista.yview()[1] >= 0.999
        self.tam_fonte = novo
        self._aplicar_fonte()
        self._ajustar_caixa()
        if no_fim:  # se estava vendo as últimas mensagens, continua vendo (depois de redesenhar)
            self.root.after(50, lambda: self.lista.see("end"))
        self.cfg["fonte"] = novo
        salvar_config(self.cfg)

    def _aplicar_tema(self):
        c = TEMAS[self.var_tema.get()]
        self.root.configure(bg=c["fundo"])
        self.topo.configure(bg=c["topo"])
        self.lbl_quem.configure(bg=c["topo"])
        self.rodape.configure(bg=c["rodape"])
        self.linha_envio.configure(bg=c["rodape"])
        self.btn_enviar.configure(bg=c["botao"], activebackground=c["botao_ativo"], activeforeground="white")
        self.caixa.configure(bg=c["caixa"], fg=c["texto"], insertbackground=c["texto"],
                             highlightbackground=c["borda"], highlightcolor=c["borda_foco"],
                             selectbackground=c["selecao"], selectforeground=c["texto"])
        self.ao_vivo.configure(bg=c["fundo"], fg=c["ao_vivo"])
        if self.mascote:
            self.mascote.mudar_fundo(c["rodape"], c["ao_vivo"])
        self.meio.configure(bg=c["fundo"])
        self.lista.configure(bg=c["fundo"], fg=c["texto"], selectbackground=c["selecao"],
                             selectforeground=c["texto"], inactiveselectbackground=c["selecao"])
        self.lista.tag_configure("eu_nome", foreground=c["eu_nome"])
        self.lista.tag_configure("outro_nome", foreground=c["outro_nome"])
        self.lista.tag_configure("hora", foreground=c["hora"])
        self.lista.tag_configure("aviso", foreground=c["hora"])
        self._barra_de_titulo(self.var_tema.get() == "escuro")

    def _barra_de_titulo(self, escura):
        """Deixa a barra de título do Windows escura ou clara junto com o tema."""
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            valor = ctypes.c_int(1 if escura else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(valor), ctypes.sizeof(valor))
            # força o Windows a redesenhar a moldura (SWP_FRAMECHANGED|NOMOVE|NOSIZE|NOZORDER|NOACTIVATE)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)
        except Exception:
            pass

    def mudar_tema(self, tema):
        self.var_tema.set(tema)
        self._aplicar_tema()
        self._salvar_opcao("tema", tema)

    def _salvar_opcao(self, chave, valor):
        self.cfg[chave] = valor
        salvar_config(self.cfg)

    # -------------------------------------------------- tamanho da janela (responsivo)
    def _posicionar_janela(self):
        """Abre no tamanho/posição da última vez; se não couber nesta tela, centraliza num tamanho que cabe."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        try:
            w, resto = self.cfg["janela"].split("x")
            h, x, y = (int(v) for v in resto.replace("+", " +").split())
            w = int(w)
            if w <= sw and h <= sh and 0 <= x <= sw - px(80) and 0 <= y <= sh - px(80):
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                return
        except (KeyError, ValueError, AttributeError):
            pass
        w, h = min(px(480), int(sw * 0.9)), min(px(640), int(sh * 0.85))
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - px(20))}")

    def _ao_redimensionar(self, e):
        if e.widget is not self.root:
            return
        self.ao_vivo.config(wraplength=max(px(150), e.width - px(30)))
        self._ajustar_caixa()
        self._atualizar_mascote()

    def _ajustar_margens(self, e=None):
        """Recuo das mensagens proporcional à largura: em janela estreita, sobra mais espaço para o texto."""
        largura = self.lista.winfo_width()
        m = max(px(16), min(px(160), int(largura * 0.16)))
        if m != self._margens:
            self._margens = m
            self.lista.tag_configure("eu", lmargin1=m, lmargin2=m)
            self.lista.tag_configure("outro", rmargin=m)

    def _ajustar_caixa(self):
        """A caixa começa com 1 linha e cresce com o texto, até ~1/4 da janela (no máximo 5 linhas)."""
        linha = max(1, self.f_texto.metrics("linespace"))
        maximo = max(1, min(5, int(self.root.winfo_height() * 0.25 / linha)))
        try:
            n = self.caixa.count("1.0", "end-1c", "displaylines")
            n = (n[0] if isinstance(n, tuple) else n or 0) + 1
        except tk.TclError:
            n = 1
        linhas = max(1, min(maximo, n))
        if int(self.caixa.cget("height")) != linhas:
            self.caixa.configure(height=linhas)
            self.caixa.see("insert")

    def _mudou_mascote(self):
        self._salvar_opcao("mascote", self.var_mascote.get())
        self._atualizar_mascote()

    def _atualizar_mascote(self):
        """Mostra a Peninha só se ela couber sem apertar a caixa de texto."""
        if not self.mascote:
            return
        cabe = self.root.winfo_width() >= px(400) and self.root.winfo_height() >= px(440)
        mostrar = self.var_mascote.get() and cabe
        visivel = self.mascote.canvas.winfo_manager() == "pack"
        if mostrar and not visivel:
            self.mascote.canvas.pack(side="left", anchor="s", padx=(0, px(6)), before=self.linha_envio)
        elif not mostrar and visivel:
            self.mascote.canvas.pack_forget()

    def avisar(self):
        if self.focado:
            return
        self.nao_lidas += 1
        self.root.title(f"({self.nao_lidas}) {NOME}")
        self.root.bell()
        try:  # pisca o ícone na barra de tarefas (Windows)
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def _ganhou_foco(self, _=None):
        self.focado = True
        if self.nao_lidas:
            self.nao_lidas = 0
            self.root.title(NOME)

    # -------------------------------------------------- menu
    def trocar_nome(self):
        n = simpledialog.askstring(NOME, "Seu nome:", initialvalue=self.cfg["nome"], parent=self.root)
        if n and n.strip():
            self.cfg["nome"] = n.strip()[:40]
            salvar_config(self.cfg)
            self.lbl_quem.config(text=f"{self.cfg['nome']}  ·  sala {self.cfg['sala']}")

    def trocar_sala(self):
        novo = pedir_config(self.root, self.cfg)
        if novo:
            for chave in ("fonte", "tema", "mascote", "abertura", "janela"):  # preferências continuam
                if chave in self.cfg:
                    novo[chave] = self.cfg[chave]
            self.cfg = novo
            salvar_config(self.cfg)
            self.conectar()

    def apagar_historico(self):
        if messagebox.askyesno(NOME, "Apagar as mensagens guardadas NESTE computador?\n"
                                          "(O outro computador continua com as dele.)", parent=self.root):
            self.hist.apagar(self.cfg["topico"])
            self.recarregar()

    def fechar(self):
        if self.root.state() == "normal":  # lembra tamanho e posição para a próxima vez
            self._salvar_opcao("janela", self.root.geometry())
        if self.con:
            self.con.parar()
        self.root.destroy()


def main():
    global ESCALA
    preparar_escala_windows()
    root = tk.Tk()
    root.withdraw()
    ESCALA = max(1.0, root.winfo_fpixels("1i") / 96)
    try:
        root.iconbitmap(default=recurso(os.path.join("imagens", "peninha.ico")))
    except tk.TclError:
        pass
    cfg = ler_config()

    def abrir_conversa():
        nonlocal cfg
        if not cfg or not all(k in cfg for k in ("nome", "sala", "chave", "topico", "id")):
            cfg = pedir_config(root)
            if not cfg:
                root.destroy()
                return
            salvar_config(cfg)
        App(root, cfg)
        root.deiconify()

    if (cfg or {}).get("abertura", True):
        try:
            Abertura(root, abrir_conversa)
        except tk.TclError:  # sem as imagens da abertura: abre direto
            abrir_conversa()
    else:
        abrir_conversa()
    root.mainloop()


if __name__ == "__main__":
    main()
