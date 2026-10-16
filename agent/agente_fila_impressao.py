#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGENTE AUTOMÁTICO DE FILA E IMPRESSÃO BROTHER MFC-L8610CDW
E.E. Antônio Reginato - Marília/SP

Suporta conexão direta via USB (Spooler RAW do Windows) ou REDE (Socket TCP Porta 9100).
Como funciona:
1. Conecta na URL do Google Apps Script (WEBAPP_URL).
2. Busca solicitações que a Gestão clicou em "Liberar" (Status: "Liberado").
3. Baixa o PDF original do Google Drive (com suporte a arquivos grandes e cookies de confirmação).
4. Se o acabamento for "Formato Livreto", executa a imposição matemática (2-up dobrável).
5. Dispara para a Brother MFC-L8610CDW via USB (Windows Spooler RAW) ou REDE (porta 9100).
6. Atualiza a Planilha Google marcando como "Concluído" automaticamente!
"""

import os
import sys
import time
import socket
import urllib.request
import urllib.parse
import http.cookiejar
import json
import re
from pypdf import PdfReader, PdfWriter, PageObject, Transformation

# ==================== CONFIGURAÇÕES DA ESCOLA ====================
# 1. Cole aqui a mesma URL do Web App gerada no seu Apps Script (/exec):
WEBAPP_URL = "SUA_URL_DO_APPS_SCRIPT_AQUI"

# 2. Conexão com a impressora Brother MFC-L8610CDW:
# Opções: "USB" (cabo direto no computador) ou "REDE" (cabo de rede Ethernet / Wi-Fi)
TIPO_CONEXAO = "USB"

# [Se TIPO_CONEXAO = "USB"]:
# Use "AUTO" para detectar automaticamente qualquer impressora Brother instalada no Windows,
# ou informe o nome exato (ex: "Brother MFC-L8610CDW series")
PRINTER_USB_NAME = "AUTO"

# [Se TIPO_CONEXAO = "REDE"]:
PRINTER_IP = "192.168.1.100"
PRINTER_PORT = 9100

# 3. Intervalo de checagem da fila em segundos (quando rodando contínuo com --loop):
INTERVALO_SEGUNDOS = 15
# =================================================================


def criar_livreto_pdf(input_path, output_path):
    """
    Reorganiza as páginas do PDF para formação de livreto (dobra de caderno).
    Adiciona páginas em branco ao final caso o total não seja múltiplo de 4.
    """
    reader = PdfReader(input_path)
    paginas = list(reader.pages)
    total = len(paginas)

    resto = total % 4
    if resto != 0:
        for _ in range(4 - resto):
            paginas.append(None)

    total_ajustado = len(paginas)
    num_folhas = total_ajustado // 4
    writer = PdfWriter()

    # A4 Paisagem (841.89 x 595.28 pt)
    folha_l = 841.89
    folha_a = 595.28
    meia_l = folha_l / 2

    for i in range(num_folhas):
        f_esq = total_ajustado - 1 - 2 * i
        f_dir = 2 * i
        v_esq = 2 * i + 1
        v_dir = total_ajustado - 2 - 2 * i

        for lado_esq, lado_dir in [(f_esq, f_dir), (v_esq, v_dir)]:
            sheet = PageObject.create_blank_page(width=folha_l, height=folha_a)

            if lado_esq < len(paginas) and paginas[lado_esq]:
                p = paginas[lado_esq]
                scale = min(meia_l / float(p.mediabox.width), folha_a / float(p.mediabox.height)) * 0.95
                tx = (meia_l - float(p.mediabox.width) * scale) / 2
                ty = (folha_a - float(p.mediabox.height) * scale) / 2
                sheet.merge_transformed_page(p, Transformation().scale(scale, scale).translate(tx, ty))

            if lado_dir < len(paginas) and paginas[lado_dir]:
                p = paginas[lado_dir]
                scale = min(meia_l / float(p.mediabox.width), folha_a / float(p.mediabox.height)) * 0.95
                tx = meia_l + (meia_l - float(p.mediabox.width) * scale) / 2
                ty = (folha_a - float(p.mediabox.height) * scale) / 2
                sheet.merge_transformed_page(p, Transformation().scale(scale, scale).translate(tx, ty))

            writer.add_page(sheet)

    with open(output_path, "wb") as f:
        writer.write(f)
    print(f" [+] Livreto montado: {num_folhas} folhas A4 frente e verso.")


def gerar_stream_pjl_brother(pdf_bytes, copias=1, acabamento="Formato Livreto", cor="P&B"):
    """
    Gera o envelope binário com comandos PJL da Brother:
    - Duplex com borda curta para Livreto, ou borda longa para Frente e Verso comum.
    - Modo monocromático (P&B) ou colorido.
    - Número de cópias de hardware.
    - Ativação do interpretador PDF direto da impressora.
    """
    if "Livreto" in acabamento:
        pjl_duplex = b"@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = SHORTEDGE\r\n"
    elif "Frente e Verso" in acabamento or "Duplex" in acabamento:
        pjl_duplex = b"@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = LONGEDGE\r\n"
    else:
        pjl_duplex = b"@PJL SET DUPLEX = OFF\r\n"

    pjl_color = b"@PJL SET COLOR = OFF\r\n" if ("P&B" in cor.upper() or "MONO" in cor.upper()) else b"@PJL SET COLOR = ON\r\n"

    header = (
        b"\x1b%-12345X@PJL\r\n"
        b"@PJL JOB NAME = 'REPROGRAFIA_EE_REGINATO'\r\n"
        + pjl_duplex
        + pjl_color
        + f"@PJL SET COPIES = {copias}\r\n".encode("ascii")
        + b"@PJL ENTER LANGUAGE = PDF\r\n"
    )
    footer = b"\x1b%-12345X@PJL EOJ\r\n\x1b%-12345X"

    return header + pdf_bytes + footer


def enviar_usb_windows(payload_pjl, printer_name="AUTO"):
    """
    Envia o fluxo RAW com PJL diretamente para a porta USB da impressora
    através do subsistema de spooler do Windows (win32print).
    """

# Detecta automaticamente se está no Windows (cliente) ou no Ubuntu (desenvolvedor)
if sys.platform == "win32":
    try:
        import win32print
    except ImportError:
        raise RuntimeError(
            "A biblioteca 'pywin32' é obrigatória no Windows para conexão via USB.\n"
            "Instale executando: pip install pywin32"
        )
else:
    try:
        import simulador_brother as win32print
        print("[MODO DEV] Executando no Ubuntu com Simulador da Brother MFC-L8610CDW.")
    except ImportError:
        raise RuntimeError(
            "O arquivo 'simulador_brother.py' não foi encontrado no mesmo diretório do script."
        )

    nome_final = printer_name
    if not printer_name or printer_name.upper() == "AUTO":
        printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        candidatas = [p for p in printers if "brother" in p.lower() or "8610" in p]
        if candidatas:
            nome_final = candidatas[0]
            print(f" -> Impressora USB identificada: '{nome_final}'")
        else:
            nome_final = win32print.GetDefaultPrinter()
            print(f" -> Nenhuma Brother detectada nominalmente. Usando padrão do Windows: '{nome_final}'")

    print(f" -> Despejando stream RAW no Spooler do Windows para '{nome_final}'...")
    hprinter = win32print.OpenPrinter(nome_final)
    try:
        hjob = win32print.StartDocPrinter(hprinter, 1, ("REPROGRAFIA_EE_REGINATO", None, "RAW"))
        win32print.StartPagePrinter(hprinter)
        win32print.WritePrinter(hprinter, payload_pjl)
        win32print.EndPagePrinter(hprinter)
        win32print.EndDocPrinter(hprinter)
    finally:
        win32print.ClosePrinter(hprinter)


def enviar_socket_rede(payload_pjl, ip=PRINTER_IP, porta=PRINTER_PORT):
    """Envia o fluxo binário diretamente para a impressora via porta TCP 9100."""
    print(f" -> Conectando via Socket TCP em {ip}:{porta}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(20)
    s.connect((ip, porta))
    s.sendall(payload_pjl)
    s.close()


def despachar_impressao(pdf_bytes, copias=1, acabamento="Formato Livreto", cor="P&B"):
    """Orquestra a montagem PJL e decide o meio físico de transmissão (USB ou Rede)."""
    payload_pjl = gerar_stream_pjl_brother(pdf_bytes, copias=copias, acabamento=acabamento, cor=cor)

    if TIPO_CONEXAO.upper() == "USB":
        enviar_usb_windows(payload_pjl, printer_name=PRINTER_USB_NAME)
    elif TIPO_CONEXAO.upper() == "REDE":
        enviar_socket_rede(payload_pjl, ip=PRINTER_IP, porta=PRINTER_PORT)
    else:
        raise ValueError(f"TIPO_CONEXAO inválido: '{TIPO_CONEXAO}'. Use 'USB' ou 'REDE'.")


def baixar_pdf_drive(url_drive, destino_local):
    """
    Extrai o ID do arquivo do Google Drive e faz o download direto do PDF,
    tratando confirmação de antivírus para arquivos grandes (>5MB) e validando o cabeçalho PDF.
    """
    match = re.search(r"d/([a-zA-Z0-9_-]+)", url_drive) or re.search(r"id=([a-zA-Z0-9_-]+)", url_drive)
    if not match:
        raise ValueError(f"Não foi possível extrair ID do arquivo da URL: {url_drive}")

    file_id = match.group(1)

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    urls_tentativa = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&confirm=t&id={file_id}",
        f"https://drive.google.com/uc?export=download&id={file_id}"
    ]

    conteudo = None
    for u in urls_tentativa:
        try:
            req = urllib.request.Request(u, headers=headers)
            with opener.open(req, timeout=30) as resp:
                data = resp.read()
                if data.startswith(b"%PDF-"):
                    conteudo = data
                    break
                html_str = data.decode("utf-8", errors="ignore")
                match_confirm = re.search(r'href="(\/uc\?export=download[^"]+)"', html_str) or re.search(r'action="(https?:\/\/[^"]*drive\.google\.com[^"]*)"', html_str)
                if match_confirm:
                    confirm_link = match_confirm.group(1)
                    if confirm_link.startswith("/"):
                        confirm_link = "https://drive.google.com" + confirm_link
                    req2 = urllib.request.Request(confirm_link, headers=headers)
                    with opener.open(req2, timeout=30) as resp2:
                        data2 = resp2.read()
                        if data2.startswith(b"%PDF-"):
                            conteudo = data2
                            break
        except Exception:
            continue

    if not conteudo or not conteudo.startswith(b"%PDF-"):
        raise RuntimeError(
            f"\n[!] Falha no download do PDF (ID: {file_id}).\n"
            f"O Google Drive retornou uma página HTML em vez do arquivo PDF (Login ou Permissão Negada).\n"
            f"-> CAUSA: O arquivo está com acesso restrito no Google Drive.\n"
            f"-> SOLUÇÃO: No Google Drive, clique com botão direito no arquivo (ou na pasta 'online') > 'Compartilhar' > altere o Acesso Geral para 'Qualquer pessoa com o link' (como Leitor)."
        )

    with open(destino_local, "wb") as f:
        f.write(conteudo)
    print(f" -> Download concluído com sucesso ({len(conteudo) / (1024*1024):.2f} MB recebidos).")


def atualizar_status_nuvem(item_id, novo_status, copias_feitas):
    """Informa o Apps Script e a Planilha que a impressão foi concluída."""
    payload = json.dumps({
        "action": "updateStatus",
        "id": item_id,
        "status": novo_status,
        "copiasImpressas": copias_feitas
    }).encode("utf-8")

    req = urllib.request.Request(WEBAPP_URL, data=payload, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)


def processar_fila():
    if not WEBAPP_URL or "SUA_URL" in WEBAPP_URL:
        print("[!] Configure a WEBAPP_URL no script antes de iniciar.")
        return

    os.makedirs("./temp_impressoes", exist_ok=True)
    print(f"\n[*] Consultando fila no Google Apps Script...")

    try:
        req = urllib.request.urlopen(WEBAPP_URL)
        itens = json.loads(req.read().decode("utf-8"))
    except Exception as e:
        print(f"[x] Erro ao consultar a nuvem: {e}")
        return

    liberados = [it for it in itens if it.get("Status") == "Liberado"]
    if not liberados:
        print("[-] Nenhuma solicitação pendente com status 'Liberado'.")
        return

    print(f"[!] {len(liberados)} solicitação(ões) liberada(s) para impressão!")

    for item in liberados:
        sol_id = item.get("ID")
        prof = item.get("Professor")
        turma = item.get("Turma")
        disc = item.get("Disciplina")
        copias = int(item.get("Copias_Solicitadas", 35))
        acabamento = item.get("Acabamento", "Formato Livreto")
        cor = item.get("Cor", "P&B")
        link_drive = item.get("Link_Drive_PDF")

        print(f"\n==================================================")
        print(f" PROCESSANDO: {sol_id} | {prof} ({turma})")
        print(f" Matéria: {disc} | Cópias: {copias} | Modo: {acabamento} ({cor})")
        print(f" Conexão: {TIPO_CONEXAO}")
        print(f"==================================================")

        try:
            arquivo_original = f"./temp_impressoes/{sol_id}_original.pdf"
            print(" -> Baixando arquivo do Google Drive...")
            baixar_pdf_drive(link_drive, arquivo_original)

            arquivo_para_imprimir = arquivo_original
            if "Livreto" in acabamento:
                arquivo_livreto = f"./temp_impressoes/{sol_id}_livreto.pdf"
                print(" -> Aplicando imposição de páginas para Formato Livreto...")
                criar_livreto_pdf(arquivo_original, arquivo_livreto)
                arquivo_para_imprimir = arquivo_livreto

            print(f" -> Lendo dados para despacho de impressão...")
            with open(arquivo_para_imprimir, "rb") as f:
                pdf_data = f.read()

            despachar_impressao(pdf_data, copias=copias, acabamento=acabamento, cor=cor)
            print(" -> Impressão enviada com sucesso para a fila da máquina!")

            print(" -> Atualizando Planilha Google para 'Concluído'...")
            atualizar_status_nuvem(sol_id, "Concluído", copias)
            print(f" ✓ [OK] Solicitação {sol_id} 100% finalizada!")

        except Exception as err:
            print(f" [x] Falha ao processar {sol_id}: {err}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        print(f"[*] Agente iniciado em modo sentinela (verificando a cada {INTERVALO_SEGUNDOS}s)...")
        print(f"[*] Modo de conexão configurado: {TIPO_CONEXAO}")
        print("Pressione Ctrl+C para encerrar.")
        while True:
            processar_fila()
            time.sleep(INTERVALO_SEGUNDOS)
    else:
        print(f"[*] Executando verificação pontual da fila (Modo: {TIPO_CONEXAO})...")
        processar_fila()
        print("\nPara rodar contínuo em segundo plano, execute: python agente_fila_impressao.py --loop")
