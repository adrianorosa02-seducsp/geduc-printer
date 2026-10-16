#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador de Livretos (Booklet Imposition) e Monitor de Impressão
para Impressora Brother MFC-L8610CDW
----------------------------------------------------------------------------
Este script realiza:
1. Imposição de páginas de qualquer arquivo PDF para formato de livreto
   (folheto dobrado ao meio, encadernação tipo canoa / saddle stitch).
2. Ajuste automático para múltiplos de 4 páginas e 2 páginas por lado da folha A4 Paisagem.
3. Injeção de metadados de impressão Duplex na Borda Curta (/DuplexFlipShortEdge) e
   tamanho real (/PrintScaling /None).
4. Disparo de impressão via driver oficial da Brother (via SumatraPDF) para evitar
   o travamento no visor 'Recebendo dados'.
5. Monitoramento bidirecional da fila USB sem necessidade de permissões de administrador:
   detecta papel preso, sem papel, tampa aberta e confirma se o trabalho concluiu com sucesso.

Uso:
    python gerador_livreto_brother.py -i documento.pdf
    python gerador_livreto_brother.py -i documento.pdf -o meu_livreto.pdf --paper A4
    python gerador_livreto_brother.py -i documento.pdf --print --monitor --printer-name "Brother MFC-L8610CDW"
"""

import sys
import os
import time
import argparse
import subprocess
from pathlib import Path

# Biblioteca PDF
try:
    import pypdf
    from pypdf import PdfReader, PdfWriter, PageObject, Transformation
    from pypdf.generic import NameObject, DictionaryObject
except ImportError:
    print("Erro: A biblioteca 'pypdf' é necessária. Instale com: pip install pypdf")
    sys.exit(1)

# Biblioteca de monitoramento Windows Spooler (opcional para quem roda fora do Windows)
try:
    import win32print
except ImportError:
    win32print = None

# Dimensões padrão em pontos (72 pontos = 1 polegada)
PAGE_SIZES = {
    "A4": (595.276, 841.890),        # 210 x 297 mm
    "LETTER": (612.0, 792.0),         # 8.5 x 11 polegadas
    "LEGAL": (612.0, 1008.0)          # 8.5 x 14 polegadas
}

def calculate_booklet_order(total_pages: int):
    """
    Calcula a ordem das páginas para imposição em livreto (dobra ao meio).
    total_pages deve ser um múltiplo de 4.
    Retorna uma lista de tuplas (pagina_esquerda, pagina_direita) em índice 0-based.
    """
    sheets = total_pages // 4
    pairs = []
    for s in range(sheets):
        # Frente da folha s
        front_left = total_pages - 2 * s - 1
        front_right = 2 * s
        pairs.append((front_left, front_right))

        # Verso da folha s
        back_left = 2 * s + 1
        back_right = total_pages - 2 * s - 2
        pairs.append((back_left, back_right))

    return pairs

def create_booklet(input_pdf_path: str, output_pdf_path: str, paper_size: str = "A4"):
    """
    Gera o PDF imposto em livreto a partir do PDF de entrada.
    """
    input_path = Path(input_pdf_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {input_pdf_path}")

    reader = PdfReader(str(input_path))
    num_orig_pages = len(reader.pages)
    if num_orig_pages == 0:
        raise ValueError("O arquivo PDF está vazio.")

    target_paper = paper_size.upper()
    if target_paper not in PAGE_SIZES:
        target_paper = "A4"
    
    portrait_w, portrait_h = PAGE_SIZES[target_paper]
    # Folha de impressão em paisagem (Landscape)
    sheet_w, sheet_h = portrait_h, portrait_w

    # Múltiplo de 4 para encadernação em livreto
    remainder = num_orig_pages % 4
    padding_needed = (4 - remainder) % 4
    total_booklet_pages = num_orig_pages + padding_needed

    print(f"[*] Processando: {input_path.name}")
    print(f"[*] Páginas originais: {num_orig_pages}")
    print(f"[*] Páginas adicionadas para completar múltiplo de 4: {padding_needed}")
    print(f"[*] Total de páginas no livreto: {total_booklet_pages} ({total_booklet_pages // 4} folhas físicas)")
    print(f"[*] Formato de folha: {target_paper} Paisagem ({sheet_w:.1f} x {sheet_h:.1f} pt)")

    page_pairs = calculate_booklet_order(total_booklet_pages)
    writer = PdfWriter()
    half_width = sheet_w / 2.0

    def get_source_page(idx):
        if idx < num_orig_pages:
            return reader.pages[idx]
        return None

    for sheet_idx, (left_idx, right_idx) in enumerate(page_pairs):
        new_sheet = PageObject.create_blank_page(width=sheet_w, height=sheet_h)

        # 1. Página Esquerda
        page_left = get_source_page(left_idx)
        if page_left is not None:
            orig_w = float(page_left.mediabox.width)
            orig_h = float(page_left.mediabox.height)
            scale = min(half_width / orig_w, sheet_h / orig_h)
            scaled_w = orig_w * scale
            scaled_h = orig_h * scale
            tx = (half_width - scaled_w) / 2.0
            ty = (sheet_h - scaled_h) / 2.0

            temp_page = PageObject.create_blank_page(width=sheet_w, height=sheet_h)
            temp_page.merge_transformed_page(
                page_left,
                Transformation().scale(scale).translate(tx, ty)
            )
            new_sheet.merge_page(temp_page)

        # 2. Página Direita
        page_right = get_source_page(right_idx)
        if page_right is not None:
            orig_w = float(page_right.mediabox.width)
            orig_h = float(page_right.mediabox.height)
            scale = min(half_width / orig_w, sheet_h / orig_h)
            scaled_w = orig_w * scale
            scaled_h = orig_h * scale
            tx = half_width + (half_width - scaled_w) / 2.0
            ty = (sheet_h - scaled_h) / 2.0

            temp_page = PageObject.create_blank_page(width=sheet_w, height=sheet_h)
            temp_page.merge_transformed_page(
                page_right,
                Transformation().scale(scale).translate(tx, ty)
            )
            new_sheet.merge_page(temp_page)

        writer.add_page(new_sheet)

    # Injeta preferências de visualização e impressão automática para a Brother MFC-L8610CDW:
    # DuplexFlipShortEdge = Frente e Verso na borda curta (essencial para paisagem)
    # PrintScaling = None (Tamanho real)
    viewer_prefs = DictionaryObject()
    viewer_prefs[NameObject('/Duplex')] = NameObject('/DuplexFlipShortEdge')
    viewer_prefs[NameObject('/PrintScaling')] = NameObject('/None')
    writer._root_object[NameObject('/ViewerPreferences')] = viewer_prefs

    output_path = Path(output_pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f_out:
        writer.write(f_out)

    print(f"[✔] Livreto gerado com sucesso: {output_path.resolve()}")
    return str(output_path.resolve())

def print_with_sumatra(pdf_path: str, printer_name: str = "Brother MFC-L8610CDW", sumatra_path: str = None):
    """
    Envia o PDF para a fila da impressora Brother usando o SumatraPDF CLI no Windows.
    """
    if sumatra_path is None:
        possible_paths = [
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
            r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
            r"C:\Users\%USERNAME%\AppData\Local\SumatraPDF\SumatraPDF.exe",
            "SumatraPDF.exe"
        ]
        for p in possible_paths:
            expanded = os.path.expandvars(p)
            if os.path.exists(expanded):
                sumatra_path = expanded
                break

    if not sumatra_path or not os.path.exists(os.path.expandvars(sumatra_path)):
        print(f"[!] SumatraPDF não encontrado no caminho padrão. Instale ou configure o executável.")
        print(f"[i] Comando manual:")
        print(f'    SumatraPDF.exe -print-to "{printer_name}" -print-settings "duplexshort,noscale" "{pdf_path}"')
        return False

    cmd = [
        sumatra_path,
        "-print-to", printer_name,
        "-print-settings", "duplexshort,noscale",
        "-silent",
        pdf_path
    ]
    print(f"[*] Enviando trabalho para a impressora '{printer_name}' via Spooler...")
    try:
        subprocess.run(cmd, check=True)
        print(f"[✔] Arquivo transferido com sucesso para a fila de impressão.")
        return True
    except Exception as e:
        print(f"[!] Erro ao chamar SumatraPDF: {e}")
        return False

def obter_status_hardware(hprinter):
    """Lê o status da impressora repassado pelo cabo USB (sem precisar de admin)."""
    if not win32print:
        return [], False
    info = win32print.GetPrinter(hprinter, 2)
    st = info.get('Status', 0)
    erros = []
    if st & win32print.PRINTER_STATUS_PAPER_JAM:
        erros.append("Papel Preso")
    if st & win32print.PRINTER_STATUS_PAPER_OUT:
        erros.append("Sem Papel")
    if st & win32print.PRINTER_STATUS_DOOR_OPEN:
        erros.append("Tampa Aberta")
    if st & win32print.PRINTER_STATUS_OFFLINE:
        erros.append("Desconectada / Offline")
    is_printing = (st & win32print.PRINTER_STATUS_PRINTING) != 0
    return erros, is_printing

def monitorar_trabalho_usb(nome_impressora: str, nome_arquivo_pdf: str, timeout: int = 180):
    """
    Monitora a fila USB do Windows como usuário comum (sem permissão de administrador).
    Retorna True se impresso com sucesso, ou False caso ocorra erro.
    """
    if not win32print:
        print("[!] pywin32 não está instalado. Para monitorar: pip install pywin32")
        return False

    try:
        hprinter = win32print.OpenPrinter(nome_impressora)
    except Exception as e:
        print(f"[!] Não foi possível acessar a impressora '{nome_impressora}': {e}")
        return False

    nome_alvo = Path(nome_arquivo_pdf).name.lower()
    print(f"[*] Monitorando fila USB da impressora '{nome_impressora}'...")
    inicio = time.time()
    job_id = None

    try:
        # 1. Localiza o trabalho recém-adicionado
        while time.time() - inicio < 10:
            jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
            for j in jobs:
                doc = str(j.get('pDocument', '')).lower()
                if nome_alvo in doc:
                    job_id = j['JobId']
                    break
            if job_id:
                break
            time.sleep(0.5)

        if not job_id:
            print("[i] O trabalho já foi absorvido pelo buffer da impressora.")
            return True

        print(f"[*] Trabalho localizado na fila (ID: {job_id}). Aguardando processamento...")

        # 2. Acompanha o trabalho até a finalização limpa ou erro
        while time.time() - inicio < timeout:
            erros_hw, imprimindo = obter_status_hardware(hprinter)
            if erros_hw:
                print(f"[ALERTA HARDWARE] Problema na impressora: {', '.join(erros_hw)}")
                return False

            jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
            job = next((j for j in jobs if j['JobId'] == job_id), None)

            # Se o trabalho não está mais na fila e não houve erro, a máquina concluiu
            if not job:
                print(f"[✔] SUCESSO: O trabalho {job_id} foi processado e impresso pela Brother.")
                return True

            st_job = job.get('Status', 0)
            if st_job & (win32print.JOB_STATUS_ERROR | win32print.JOB_STATUS_USER_INTERVENTION):
                print(f"[!] Erro no processamento do trabalho {job_id} na fila.")
                return False
            if st_job & win32print.JOB_STATUS_DELETING:
                print(f"[!] O trabalho {job_id} foi cancelado/excluído.")
                return False

            time.sleep(1.5)

        print("[!] Tempo limite de monitoramento atingido.")
        return False

    finally:
        win32print.ClosePrinter(hprinter)

def main():
    parser = argparse.ArgumentParser(
        description="Gera PDF em formato de livreto (imposição 2-up) e monitora a impressão na Brother MFC-L8610CDW."
    )
    parser.add_argument("-i", "--input", required=True, help="Caminho do arquivo PDF original.")
    parser.add_argument("-o", "--output", help="Caminho do arquivo PDF de saída (padrão: <nome>_booklet.pdf).")
    parser.add_argument("--paper", default="A4", choices=["A4", "LETTER", "LEGAL"], help="Tamanho do papel (padrão: A4).")
    parser.add_argument("--print", dest="do_print", action="store_true", help="Enviar para impressão via SumatraPDF após gerar.")
    parser.add_argument("--monitor", dest="do_monitor", action="store_true", help="Monitorar o status da impressão na fila USB.")
    parser.add_argument("--printer-name", default="Brother MFC-L8610CDW", help="Nome da impressora no Windows.")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not args.output:
        output_path = input_path.with_name(f"{input_path.stem}_booklet.pdf")
    else:
        output_path = Path(args.output)

    try:
        result_file = create_booklet(str(input_path), str(output_path), paper_size=args.paper)

        if args.do_print:
            ok = print_with_sumatra(result_file, printer_name=args.printer_name)
            if ok and args.do_monitor:
                monitorar_trabalho_usb(args.printer_name, result_file)

    except Exception as e:
        print(f"[!] Erro durante o processamento: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
