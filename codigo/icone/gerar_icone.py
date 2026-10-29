"""
Desenha o ícone da Peninha (pena dourada com olhinhos) e grava:
  ../imagens/peninha.ico  -> ícone do .exe, da janela e da barra de tarefas
  ../imagens/peninha.png  -> a mesma arte em 256x256
  ../imagens/abertura_*.png e mascote_*.png -> quadros das animações

Usa só Python puro (sem bibliotecas extras): um pequeno desenhista de
polígonos com suavização. Para mexer no desenho, altere as cores/medidas em
`peninha()` e rode:  python gerar_icone.py
"""
import math
import os
import struct
import zlib

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(os.path.dirname(AQUI), "imagens")  # codigo/imagens

# cores
CORPO = ("#fff3a0", "#f09a1e")   # degradê da pena (cima -> baixo)
CONTORNO = "#6b3f0a"
HASTE = "#fff8dc"
BARBAS = "#b8741a"
OLHO_CONTORNO = "#1b2b2b"
BOCHECHA = "#ff7a8a"
BOCA = "#5a1e12"
LINGUA = "#ff6f7f"

# posição da pena no quadrado 0..1: cabo embaixo à esquerda, ponta em cima à direita
BASE, CTRL, PONTA, LARG = (0.20, 0.97), (0.30, 0.42), (0.86, 0.05), 0.20


# ====================================================================== desenhista
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Tela:
    def __init__(self, n, ss=4):
        self.n, self.ss = n, ss
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(n * n)]  # r, g, b, a (pré-multiplicado, 0..1)

    def poligono(self, pts, cor, alfa=1.0):
        """Preenche um polígono (pontos em 0..1) com suavização por supersampling."""
        n, ss = self.n, self.ss
        cob = {}
        P = [(x * n, y * n) for x, y in pts]
        ys = [p[1] for p in P]
        y0, y1 = max(0, int(min(ys) * ss)), min(n * ss - 1, int(max(ys) * ss) + 1)
        m = len(P)
        for sy in range(y0, y1 + 1):
            y = (sy + 0.5) / ss
            xs = []  # regra "nonzero": sem buracos onde o contorno se cruza
            for i in range(m):
                ax, ay = P[i]
                bx, by = P[(i + 1) % m]
                if (ay <= y < by) or (by <= y < ay):
                    xs.append((ax + (y - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1))
            xs.sort()
            py, w, ini = sy // ss, 0, 0.0
            for x, sentido in xs:
                if w == 0:
                    ini = x
                w += sentido
                if w == 0:
                    sa = max(0, int(math.ceil(ini * ss - 0.5)))
                    sb = min(n * ss - 1, int(math.floor(x * ss - 0.5)))
                    for sx in range(sa, sb + 1):
                        k = py * n + sx // ss
                        cob[k] = cob.get(k, 0) + 1
        fixo = None if callable(cor) else tuple(c / 255 for c in hex2rgb(cor))
        for k, c in cob.items():
            a = min(1.0, c / (ss * ss)) * alfa
            if fixo:
                r, g, b = fixo
            else:  # degradê: cor(u, v) -> (r, g, b, a)
                r, g, b, ag = cor((k % n + 0.5) / n, (k // n + 0.5) / n)
                a *= ag
            p = self.px[k]
            p[0] = r * a + p[0] * (1 - a)
            p[1] = g * a + p[1] * (1 - a)
            p[2] = b * a + p[2] * (1 - a)
            p[3] = a + p[3] * (1 - a)

    def linha(self, a, b, larg, cor, alfa=1.0):
        (ax, ay), (bx, by) = a, b
        L = math.hypot(bx - ax, by - ay) or 1
        nx, ny = -(by - ay) / L * larg / 2, (bx - ax) / L * larg / 2
        self.poligono([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)], cor, alfa)

    def rgba(self):
        out = bytearray()
        for r, g, b, a in self.px:
            out += bytes((round(r / a * 255), round(g / a * 255), round(b / a * 255), round(a * 255))) if a else b"\0" * 4
        return bytes(out)


def degrade(c1, c2, a1=1.0, a2=1.0, ini=0.0, fim=1.0):
    """Degradê vertical entre duas cores (e opacidades)."""
    r1, g1, b1 = (c / 255 for c in hex2rgb(c1))
    r2, g2, b2 = (c / 255 for c in hex2rgb(c2))

    def f(_u, v):
        t = max(0.0, min(1.0, (v - ini) / (fim - ini)))
        return r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t, a1 + (a2 - a1) * t
    return f


def elipse(cx, cy, rx, ry, seg=72):
    return [(cx + rx * math.cos(2 * math.pi * i / seg), cy + ry * math.sin(2 * math.pi * i / seg)) for i in range(seg)]


def bezier(p0, p1, p2, t):
    u = 1 - t
    return u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]


# ====================================================================== a pena
INICIO = 0.24          # onde começam as barbas (antes disso é só o cabo)
ASSIM = (1.0, 0.72)    # um lado da pena é mais estreito que o outro
ENTALHES = (           # fendas nas bordas: (posição, lado, profundidade, abertura)
    (0.03, 1, 0.75, 0.025), (0.08, 1, 0.65, 0.025), (0.30, 1, 0.55, 0.03), (0.58, 1, 0.45, 0.03),
    (0.04, -1, 0.75, 0.025), (0.10, -1, 0.65, 0.025), (0.42, -1, 0.55, 0.03), (0.74, -1, 0.40, 0.03),
)


def ponto(t):
    """Ponto da haste em t (0 = ponta do cabo, 1 = ponta da pena) e a direção perpendicular."""
    t = min(t, 0.999)
    x, y = bezier(BASE, CTRL, PONTA, t)
    x2, y2 = bezier(BASE, CTRL, PONTA, t + 0.001)
    L = math.hypot(x2 - x, y2 - y) or 1
    return x, y, -(y2 - y) / L, (x2 - x) / L


def largura(s):
    """Largura relativa ao longo das barbas (0 = base, 1 = ponta)."""
    sobe = min(1.0, s / 0.10) ** 0.55
    desce = max(0.0, 1 - max(0.0, (s - 0.45) / 0.55) ** 2.0) ** 0.6
    return sobe * desce


def contorno(extra=0.0, N=140):
    """Contorno da pena com as fendas recortadas (ficam transparentes)."""
    lados = []
    for sinal in (1, -1):
        fator = ASSIM[0] if sinal > 0 else ASSIM[1]
        fendas = sorted((s, prof, abre) for s, lado, prof, abre in ENTALHES if lado == sinal)

        def borda(s, encolhe=0.0):
            x, y, nx, ny = ponto(INICIO + (1 - INICIO) * s)
            w = largura(s) * LARG * fator * (1 - encolhe) + extra
            return x + sinal * nx * w, y + sinal * ny * w

        pts, k, pular_ate = [], 0, -1.0
        for i in range(N + 1):
            s = i / N
            while k < len(fendas) and fendas[k][0] <= s:
                sn, prof, abre = fendas[k]
                pts += [borda(sn), borda(max(0.0, sn - 0.05), prof)]
                pular_ate = sn + abre
                k += 1
            if s > pular_ate:
                pts.append(borda(s))
        lados.append(pts)
    return lados[0] + lados[1][::-1]


def desenhar_pena(t, borda_grossa=False, trechos_haste=((0.0, 1.0),)):
    esp = 0.03 if borda_grossa else 0.012
    haste = 0.05 if borda_grossa else 0.026
    t.poligono(contorno(extra=esp), CONTORNO)
    t.poligono(contorno(), degrade(*CORPO, ini=0.1, fim=0.9))
    if not borda_grossa:  # riscos das barbas: só aparecem nos tamanhos grandes
        for sinal, fator in ((1, ASSIM[0]), (-1, ASSIM[1])):
            for j in range(3, 17):
                s0, s1 = j / 18, min(1.0, j / 18 + 0.07)
                x, y, _, _ = ponto(INICIO + (1 - INICIO) * s0)
                x1, y1, nx, ny = ponto(INICIO + (1 - INICIO) * s1)
                w = largura(s1) * LARG * fator * 0.85
                t.linha((x, y), (x1 + sinal * nx * w, y1 + sinal * ny * w), 0.006, BARBAS, 0.35)
    # cabo com ponta de caneta, e a haste afinando até a ponta
    for t_a, t_b in trechos_haste:
        for i in range(70):
            ta, tb = i / 70, (i + 1) / 70
            if tb < t_a or ta > t_b:
                continue
            if ta < 0.06:
                lw = haste * 1.25 * max(0.15, (ta / 0.06) ** 0.8)
            elif ta < INICIO:
                lw = haste * 1.25
            else:
                lw = haste * (1.0 - 0.7 * (ta - INICIO) / (1 - INICIO))
            p1, p2 = bezier(BASE, CTRL, PONTA, ta), bezier(BASE, CTRL, PONTA, tb)
            t.linha(p1, p2, lw + esp * 1.4, CONTORNO)
            t.linha(p1, p2, lw, HASTE)


def brilho(t):
    """Reflexo de vidro no lado de cima da pena (o toque anos 2000)."""
    ida, volta = [], []
    for i in range(30):
        x, y, nx, ny = ponto(0.40 + 0.5 * i / 29)
        ida.append((x - nx * LARG * 0.42, y - ny * LARG * 0.42))
        w = LARG * 0.42 + LARG * 0.14 * math.sin(math.pi * i / 29)
        volta.append((x - nx * w, y - ny * w))
    t.poligono(ida + volta[::-1], "#ffffff", 0.55)


def olho(t, cx, cy, r, olhar=(0.2, 0.05), pupila=0.52):
    t.poligono(elipse(cx, cy, r * 1.12, r * 1.32), OLHO_CONTORNO)
    t.poligono(elipse(cx, cy, r, r * 1.2), degrade("#ffffff", "#dfe6e6", ini=cy - r, fim=cy + r))
    px, py = cx + olhar[0] * r, cy + olhar[1] * r
    t.poligono(elipse(px, py, r * pupila, r * pupila, 40), OLHO_CONTORNO)
    t.poligono(elipse(px - r * 0.18, py - r * 0.2, r * 0.17, r * 0.17, 24), "#ffffff")


# expressões: (olhar para onde, olhos fechados?, susto?)
EXPRESSOES = {
    "normal": ((0.2, 0.05), False, False),
    "esquerda": ((-0.42, 0.05), False, False),
    "direita": ((0.45, 0.05), False, False),
    "cima": ((0.1, -0.42), False, False),
    "baixo": ((0.05, 0.42), False, False),
    "piscando": ((0, 0), True, False),
    "surpresa": ((0.0, 0.0), False, True),
}


def rosto(t, t_rosto, expressao="normal"):
    olhar, fechados, susto = EXPRESSOES[expressao]
    sx, sy, nx, ny = ponto(t_rosto)
    cx, cy = sx + nx * 0.03, sy + ny * 0.03
    r, d = (0.074, 0.076) if susto else (0.066, 0.072)
    olhos = ((cx - d, cy), (cx + d, cy))
    for ex, ey in olhos:
        sobe = 0.02 if susto else 0.0  # no susto as sobrancelhas sobem
        arco = [(ex - 0.042 + 0.084 * i / 12, ey - r * 1.65 - sobe - 0.016 * math.sin(math.pi * i / 12))
                for i in range(13)]
        for a, b in zip(arco, arco[1:]):
            t.linha(a, b, 0.018, OLHO_CONTORNO)
        if fechados:  # olhinhos fechados e felizes: dois arcos ^ ^
            arco = [(ex - r + 2 * r * i / 16, ey + 0.01 - r * 0.7 * math.sin(math.pi * i / 16)) for i in range(17)]
            for a, b in zip(arco, arco[1:]):
                t.linha(a, b, 0.026, OLHO_CONTORNO)
        else:
            olho(t, ex, ey, r, olhar, pupila=0.36 if susto else 0.52)
    mx, my = cx + 0.004, cy + 0.105
    for ex, _ in olhos:
        t.poligono(elipse(ex + (-0.02 if ex < cx else 0.02), my - 0.02, 0.032, 0.02), BOCHECHA, 0.45)
    if susto:  # boquinha de "Oh!"
        t.poligono(elipse(mx, my + 0.008, 0.026, 0.034), BOCA)
        t.poligono(elipse(mx, my + 0.024, 0.016, 0.012), LINGUA)
        return
    bw = 0.05
    arco = [(mx - bw + 2 * bw * i / 24, my - 0.012 + 0.045 * math.sin(math.pi * i / 24)) for i in range(25)]
    t.poligono([(mx - bw, my - 0.012), (mx + bw, my - 0.012)] + arco[::-1], BOCA)
    t.poligono(elipse(mx, my + 0.017, 0.024, 0.011), LINGUA)


def peninha(n, expressao="normal"):
    """A Peninha em n x n pixels. No tamanho minúsculo vai só a pena (o rosto viraria um borrão)."""
    t = Tela(n)
    if n < 24:
        desenhar_pena(t, borda_grossa=True)
        return t
    t_rosto = 0.57
    desenhar_pena(t, trechos_haste=((0.0, t_rosto - 0.2), (t_rosto + 0.1, 1.0)))
    brilho(t)
    rosto(t, t_rosto, expressao)
    return t


# ====================================================================== arquivos
def png(n, rgba):
    def bloco(tipo, dados):
        c = tipo + dados
        return struct.pack(">I", len(dados)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    linhas = b"".join(b"\0" + rgba[y * n * 4:(y + 1) * n * 4] for y in range(n))
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(linhas, 9)) + bloco(b"IEND", b""))


def bmp_icone(n, rgba):
    """Imagem no formato BMP de ícone: 32 bits BGRA, de baixo para cima, mais a máscara."""
    cab = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    px = bytearray()
    for y in range(n - 1, -1, -1):
        for x in range(n):
            r, g, b, a = rgba[(y * n + x) * 4:(y * n + x) * 4 + 4]
            px += bytes((b, g, r, a))
    return cab + bytes(px) + b"\0" * (((n + 31) // 32) * 4 * n)


def ico(imagens):
    """imagens: [(n, rgba)]. O de 256 vai como PNG; os menores como BMP (funciona em tudo)."""
    blocos = [png(n, d) if n >= 256 else bmp_icone(n, d) for n, d in imagens]
    cab = struct.pack("<HHH", 0, 1, len(imagens))
    pos = 6 + 16 * len(imagens)
    indice = b""
    for (n, _), bl in zip(imagens, blocos):
        indice += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(bl), pos)
        pos += len(bl)
    return cab + indice + b"".join(blocos)


def gravar(nome, dados):
    with open(os.path.join(DESTINO, nome), "wb") as f:
        f.write(dados)


if __name__ == "__main__":
    os.makedirs(DESTINO, exist_ok=True)
    imagens = [(n, peninha(n).rgba()) for n in (16, 24, 32, 48, 64, 128, 256)]
    gravar("peninha.ico", ico(imagens))
    gravar("peninha.png", png(256, imagens[-1][1]))
    # cada imagem animada vai em 3 tamanhos: telas em 100%, 125% e 150%
    for n in (140, 175, 210):  # abertura
        for expr in ("normal", "piscando"):
            gravar(f"abertura_{n}_{expr}.png", png(n, peninha(n, expr).rgba()))
    for n in (48, 60, 72):  # mascote ao lado da caixa de texto
        for expr in EXPRESSOES:
            gravar(f"mascote_{n}_{expr}.png", png(n, peninha(n, expr).rgba()))
    print("Pronto: ícone e quadros das animações na pasta", DESTINO)
