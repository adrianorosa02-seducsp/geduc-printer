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
WEBAPP_URL = "https://script.google.com/macros/s/AKfycbyfIsn7cLoMNYUGIdVoGswTADhy-w3QWsbnZjRYhTvsvjFYd1fktniwrTEBvzO-tncB/exec"

# 2. Conexão com a impressora Brother MFC-L8610CDW:
# Opções: "USB" (cabo direto no computador), "REDE" (cabo de rede Ethernet / Wi-Fi) ou "SIMULADOR" (testes locais)
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

# 4. Modo de Simulação / Testes:
# Se True, utiliza o simulador_brother.py mesmo no Windows (sem precisar de impressora física).
# Pode também ser ativado por linha de comando: python agente_fila_impressao.py --simulador
MODO_SIMULADOR = False
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


def enviar_usb_windows(payload_pjl, printer_name="AUTO", doc_name="REPROGRAFIA_EE_REGINATO"):
    """
    Envia o fluxo RAW com PJL diretamente para a porta USB da impressora
    através do subsistema de spooler do Windows (win32print) ou do simulador.
    Retorna (nome_impressora, job_id).
    """

    # Seleciona o simulador (em modo teste ou fora do Windows) ou win32print nativo do Windows
    if MODO_SIMULADOR or sys.platform != "win32":
        try:
            import simulador_brother as win32print
            print(" [SIMULADOR] Utilizando Simulador da Brother MFC-L8610CDW.")
        except ImportError:
            try:
                from agent import simulador_brother as win32print
                print(" [SIMULADOR] Utilizando Simulador da Brother MFC-L8610CDW.")
            except ImportError:
                raise RuntimeError(
                    "O arquivo 'simulador_brother.py' não foi encontrado."
                )
    else:
        try:
            import win32print
        except ImportError:
            raise RuntimeError(
                "A biblioteca 'pywin32' é obrigatória no Windows para conexão via USB.\n"
                "Instale executando: pip install pywin32"
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

    print(f" -> Despejando stream RAW no Spooler para '{nome_final}'...")
    hprinter = win32print.OpenPrinter(nome_final)
    try:
        hjob = win32print.StartDocPrinter(hprinter, 1, (doc_name, None, "RAW"))
        win32print.StartPagePrinter(hprinter)
        win32print.WritePrinter(hprinter, payload_pjl)
        win32print.EndPagePrinter(hprinter)
        win32print.EndDocPrinter(hprinter)
        return nome_final, hjob
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


def despachar_impressao(pdf_bytes, copias=1, acabamento="Formato Livreto", cor="P&B", doc_id="REPROGRAFIA"):
    """Orquestra a montagem PJL e decide o meio físico de transmissão (USB, Rede ou Simulador)."""
    payload_pjl = gerar_stream_pjl_brother(pdf_bytes, copias=copias, acabamento=acabamento, cor=cor)

    if MODO_SIMULADOR or TIPO_CONEXAO.upper() == "SIMULADOR":
        return enviar_usb_windows(payload_pjl, printer_name="Brother MFC-L8610CDW series", doc_name=doc_id)
    elif TIPO_CONEXAO.upper() == "USB":
        return enviar_usb_windows(payload_pjl, printer_name=PRINTER_USB_NAME, doc_name=doc_id)
    elif TIPO_CONEXAO.upper() == "REDE":
        enviar_socket_rede(payload_pjl, ip=PRINTER_IP, porta=PRINTER_PORT)
        return "REDE", None
    else:
        raise ValueError(f"TIPO_CONEXAO inválido: '{TIPO_CONEXAO}'. Use 'USB', 'REDE' ou 'SIMULADOR'.")


def monitorar_impressao(nome_printer, job_id, copias_solicitadas, sol_id, timeout_segundos=600):
    """
    Monitora a fila da impressora até a conclusão física do trabalho.
    - Se houver erro de hardware (Sem Papel, Papel Preso, Tampa Aberta, Offline):
      -> Notifica IMEDIATAMENTE a Planilha Google/Frontend com o status de erro e cópias impressas parciais.
      -> Fica em prontidão aguardando o operador sanar a falha.
    - Quando o operador repuser o papel / destravar a máquina:
      -> Notifica a nuvem que a impressão foi retomada.
      -> Ao terminar 100%, conclui o lote com sucesso.
    """
    if MODO_SIMULADOR or sys.platform != "win32":
        try:
            import simulador_brother as win32print
        except ImportError:
            from agent import simulador_brother as win32print
    else:
        try:
            import win32print
        except ImportError:
            return True, copias_solicitadas

    if not nome_printer or nome_printer == "REDE" or not job_id:
        return True, copias_solicitadas

    print(f"[*] Monitorando execução física na impressora '{nome_printer}' (Trabalho ID: {job_id})...")
    hprinter = win32print.OpenPrinter(nome_printer)

    ultimo_status_notificado = None
    ultimo_copias_notificadas = -1
    copias_feitas = 0
    inicio = time.time()
    tempo_sem_job = 0

    try:
        while time.time() - inicio < timeout_segundos:
            # 1. Consulta status de hardware da impressora
            pinfo = win32print.GetPrinter(hprinter, 2)
            st_printer = pinfo.get("Status", 0) if isinstance(pinfo, dict) else 0

            erro_hardware = None
            if st_printer & win32print.PRINTER_STATUS_PAPER_OUT:
                erro_hardware = "Sem Papel"
            elif st_printer & win32print.PRINTER_STATUS_PAPER_JAM:
                erro_hardware = "Papel Preso"
            elif st_printer & win32print.PRINTER_STATUS_DOOR_OPEN:
                erro_hardware = "Tampa Aberta"
            elif st_printer & win32print.PRINTER_STATUS_OFFLINE:
                erro_hardware = "Offline"
            elif st_printer & win32print.PRINTER_STATUS_ERROR:
                erro_hardware = "Erro de Hardware"

            # 2. Consulta jobs na fila
            jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
            job = next((j for j in jobs if j.get("JobId") == job_id), None)

            if job:
                tempo_sem_job = 0
                copias_feitas = job.get("PagesPrinted", copias_feitas)
                st_job = job.get("Status", 0)

                if not erro_hardware:
                    if st_job & win32print.JOB_STATUS_PAPEROUT:
                        erro_hardware = "Sem Papel"
                    elif st_job & win32print.JOB_STATUS_ERROR:
                        erro_hardware = "Erro no Trabalho"

            if erro_hardware:
                status_erro_texto = f"Erro: {erro_hardware}"
                if ultimo_status_notificado != status_erro_texto or ultimo_copias_notificadas != copias_feitas:
                    print(f"\n[ALERTA] ⚠️ Impressora em estado de falha: {erro_hardware.upper()}!")
                    print(f" -> Cópias produzidas até a falha: {copias_feitas}/{copias_solicitadas}")
                    print(f" -> Atualizando frontend na nuvem: Status = '{status_erro_texto}' | Copias = {copias_feitas}...")
                    atualizar_status_nuvem(sol_id, status_erro_texto, copias_feitas)
                    ultimo_status_notificado = status_erro_texto
                    ultimo_copias_notificadas = copias_feitas
                    print(f" [Aguardando operador sanar '{erro_hardware}' na impressora ou simulador...]")

                time.sleep(2)
                continue

            # Se não está em erro, mas estava antes, operador acabou de consertar!
            if ultimo_status_notificado and "Erro:" in ultimo_status_notificado:
                print(f"\n[INFO] ✔ Falha resolvida! Retomando impressão...")
                atualizar_status_nuvem(sol_id, "Imprimindo", copias_feitas)
                ultimo_status_notificado = "Imprimindo"

            # Se o trabalho sumiu da fila e não há erro de hardware, foi concluído!
            if not job:
                tempo_sem_job += 1
                if tempo_sem_job >= 2:
                    print(f"\n[✔] SUCESSO: Trabalho {job_id} concluído pela impressora! ({copias_solicitadas}/{copias_solicitadas} cópias)")
                    return True, copias_solicitadas
            else:
                print(f" -> Imprimindo... ({copias_feitas}/{copias_solicitadas} folhas)", end="\r")

            time.sleep(1)

        print(f"\n[!] Timeout de monitoramento ({timeout_segundos}s) atingido.")
        return False, copias_feitas

    finally:
        win32print.ClosePrinter(hprinter)


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
    """Informa o Apps Script e a Planilha que a impressão foi atualizada."""
    payload = json.dumps({
        "action": "updateStatus",
        "id": item_id,
        "status": novo_status,
        "copiasImpressas": copias_feitas
    }).encode("utf-8")

    try:
        req = urllib.request.Request(WEBAPP_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print(f" -> Nuvem sincronizada: {item_id} | Status: '{novo_status}' | Cópias: {copias_feitas}")
        return True
    except Exception as e:
        print(f" [x] Falha ao atualizar status na nuvem ({item_id}): {e}")
        return False


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

    liberados = [
        it for it in itens 
        if str(it.get("Status", "")).strip().lower() in ("liberado", "liberada")
    ]
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

        modo_ativo = "SIMULADOR" if MODO_SIMULADOR else TIPO_CONEXAO
        print(f"\n==================================================")
        print(f" PROCESSANDO: {sol_id} | {prof} ({turma})")
        print(f" Matéria: {disc} | Cópias: {copias} | Modo: {acabamento} ({cor})")
        print(f" Conexão: {modo_ativo}")
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

            print(" -> Disparando stream de impressão...")
            nome_printer, hjob = despachar_impressao(pdf_data, copias=copias, acabamento=acabamento, cor=cor, doc_id=sol_id)
            print(" -> Impressão aceita na fila!")

            # Monitora em tempo real a ejeção física das folhas e detecção de erros
            sucesso, copias_feitas = monitorar_impressao(nome_printer, hjob, copias, sol_id)

            if sucesso:
                print(" -> Atualizando Planilha Google para 'Concluído'...")
                atualizar_status_nuvem(sol_id, "Concluído", copias_feitas)
                print(f" ✓ [OK] Solicitação {sol_id} 100% finalizada com {copias_feitas} cópias!")
            else:
                print(f" ⚠️ Solicitação {sol_id} interrompida com {copias_feitas}/{copias} cópias registradas na nuvem.")

        except Exception as err:
            print(f" [x] Falha ao processar {sol_id}: {err}")



if __name__ == "__main__":
    if "--simulador" in sys.argv or "-s" in sys.argv:
        MODO_SIMULADOR = True
        print("[!] Modo Simulador ativado via parâmetro de inicialização.")

    modo_ativo = "SIMULADOR" if MODO_SIMULADOR else TIPO_CONEXAO
    if len(sys.argv) > 1 and ("--loop" in sys.argv or "-l" in sys.argv):
        print(f"[*] Agente iniciado em modo sentinela (verificando a cada {INTERVALO_SEGUNDOS}s)...")
        print(f"[*] Modo de conexão configurado: {modo_ativo}")
        print("Pressione Ctrl+C para encerrar.")
        while True:
            processar_fila()
            time.sleep(INTERVALO_SEGUNDOS)
    else:
        print(f"[*] Executando verificação pontual da fila (Modo: {modo_ativo})...")
        processar_fila()
        print("\nPara rodar contínuo em segundo plano, execute: python agente_fila_impressao.py --loop")
