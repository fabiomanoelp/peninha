# 🐦 Peninha

Conversa ao vivo entre dois computadores Windows, com criptografia e uma mascote animada.
Antes, o programa se chamava **Recados**.

Abra o `Peninha.exe` nos dois PCs, use o mesmo nome de sala e a mesma senha, e pronto: um vê o que o outro digita.
O computador de quem recebe **não precisa ter Python**.

## Funcionalidades

- **Criptografia:** as mensagens passam por um servidor MQTT público, mas vão cifradas com a senha da sala. Quem não tem a senha não consegue ler.
- **Digitação ao vivo:** o outro lado vê o texto enquanto você escreve.
- **Mensagens perdidas recuperadas:** se um PC estava desligado, ao abrir ele busca no outro o que perdeu (desde que o outro esteja com o Peninha aberto).
- **Histórico local** salvo no próprio computador.
- **Notificações:** quando chega mensagem com a janela minimizada, o programa apita e pisca na barra de tarefas.
- **Interface ajustável:** modo claro/escuro, tamanho da letra e janela redimensionável. O programa lembra como você deixou a janela.
- **Compatível com o antigo Recados:** o Peninha conversa com quem ainda usa o Recados, na mesma sala e com a mesma senha.

### A Peninha (a mascote)

Ela fica ao lado da caixa de texto. Pisca, olha em volta, acompanha o que você escreve e dá um pulinho quando você envia.
Leva um susto quando chega mensagem e cochila (zZ) depois de 2 minutos sem ninguém mexer.
Clique nela para cutucar.

## Como usar

1. Baixe o `Peninha.exe` na página de [Releases](../../releases) e copie para os dois computadores.
2. Abra o programa nos dois.
3. Na primeira vez, ele pede:
   - **Nome:** cada um coloca o seu
   - **Sala:** a **mesma** nos dois (ex.: `escritorio`)
   - **Senha:** a **mesma** nos dois (mínimo de 6 caracteres)
4. Pronto!

| Atalho | Ação |
| --- | --- |
| `Enter` | Envia a mensagem |
| `Shift + Enter` | Pula linha |
| `Ctrl +` / `Ctrl -` / `Ctrl 0` | Aumenta, diminui ou restaura o tamanho da letra |
| `Ctrl + rodinha do mouse` | Ajusta o tamanho da letra |

No menu **Opções** você troca o nome, a sala ou a senha e apaga o histórico.
No menu **Exibir** você liga ou desliga o modo escuro, a mascote e a animação de abertura.

### Dicas

- **"O Windows protegeu o computador":** clique em *Mais informações* → *Executar assim mesmo*. O aviso aparece com qualquer programa sem assinatura digital.
- **Abrir junto com o Windows:** aperte `Win + R`, digite `shell:startup` e coloque nessa pasta um atalho para o `Peninha.exe`.
- **Onde ficam os dados:** configurações e histórico ficam em `%APPDATA%\Peninha`. Se você usava o Recados, a pasta antiga `%APPDATA%\Recados` é migrada automaticamente na primeira abertura.

## Como funciona

- As mensagens são trocadas pelo broker MQTT público e gratuito `broker.hivemq.com`. Para usar outro broker, defina a variável de ambiente `PENINHA_BROKER`.
- O conteúdo é cifrado com **Fernet** (AES + HMAC). A chave é derivada da senha com **PBKDF2-HMAC-SHA256** (300.000 iterações).
- O histórico local é guardado em **SQLite**.
- A interface é feita em **Tkinter**.

## Gerar o executável

Pré-requisito: Windows. Se o computador não tiver Python, o próprio script baixa e instala.

1. Abra a pasta `codigo` e dê dois cliques em `gerar_exe.bat`.
2. O script instala as dependências (`paho-mqtt`, `cryptography` e `pyinstaller`) e gera o `Peninha.exe` na pasta raiz do projeto.

Para rodar direto pelo código:

```bash
pip install paho-mqtt cryptography
python codigo/peninha.py
```

### Mudar o desenho da Peninha

O ícone e os quadros das animações são gerados por código:

```bash
cd codigo/icone
python gerar_icone.py
```

Depois, gere o `.exe` de novo.

## Estrutura

```
Peninha/
├── LEIA-ME.txt           # instruções para o usuário final
└── codigo/
    ├── peninha.py        # o programa
    ├── gerar_exe.bat     # monta o Peninha.exe
    ├── imagens/          # ícone e quadros das animações (embutidos no .exe)
    └── icone/
        └── gerar_icone.py  # desenha o ícone e as imagens
```

## Tecnologias

Python · Tkinter · paho-mqtt · cryptography · SQLite · PyInstaller
