#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIMULADOR DE IMPRESSORA BROTHER MFC-L8610CDW
E.E. Antônio Reginato - Ambiente de Teste e Desenvolvimento (Windows / Linux)
----------------------------------------------------------------------------
Este módulo simula o comportamento físico e os códigos de status de hardware
da impressora Brother MFC-L8610CDW conectada via USB (emulando a API win32print).

Permite testar todos os cenários de erro e sucesso sem impressora física:
- Máquina Pronta / Ociosa (0x00000000)
- Máquina Imprimindo / Ocupada (0x00000400 | 0x00000200)
- Falta de Papel (PRINTER_STATUS_PAPER_OUT: 0x00000020)
- Papel Atolado (PRINTER_STATUS_PAPER_JAM: 0x00000008)
- Tampa Aberta (PRINTER_STATUS_DOOR_OPEN: 0x00400000)
- Máquina Desligada / Cabo Puxado (PRINTER_STATUS_OFFLINE: 0x00000080)
- Simulação de falha programada após X cópias (falhar_apos_copias)
- Extração automática do PDF recebido para inspeção visual imediata!

Pode ser controlado interativamente pelo terminal ou via arquivo 'simulador_estado.json'.
"""

import os
import sys
import time
import json
import re
import threading
from pathlib import Path

# Garante suporte a UTF-8 no terminal Windows sem erros de encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================================
# CÓDIGOS DE STATUS OFICIAIS DO WINDOWS SPOOLER (win32print)
# ============================================================================
PRINTER_STATUS_IDLE              = 0x00000000  # Pronta / Ociosa
PRINTER_STATUS_PAUSED            = 0x00000001
PRINTER_STATUS_ERROR             = 0x00000002
PRINTER_STATUS_PAPER_JAM         = 0x00000008  # Papel Preso / Atolamento
PRINTER_STATUS_PAPER_OUT         = 0x00000020  # Sem Papel na bandeja
PRINTER_STATUS_OFFLINE           = 0x00000080  # Desconectada / Sem energia
PRINTER_STATUS_BUSY              = 0x00000200  # Ocupada processando
PRINTER_STATUS_PRINTING          = 0x00000400  # Motor ativo imprimindo
PRINTER_STATUS_WARMING_UP        = 0x00010000  # Aquecendo fusor
PRINTER_STATUS_TONER_LOW         = 0x00020000  # Toner baixo
PRINTER_STATUS_NO_TONER          = 0x00040000  # Sem toner
PRINTER_STATUS_DOOR_OPEN         = 0x00400000  # Tampa / Porta aberta
PRINTER_STATUS_USER_INTERVENTION = 0x00100000  # Requer intervenção manual

JOB_STATUS_PAUSED                = 0x00000001
JOB_STATUS_ERROR                 = 0x00000002
JOB_STATUS_DELETING              = 0x00000004  # Cancelado
JOB_STATUS_SPOOLING              = 0x00000008
JOB_STATUS_PRINTING              = 0x00000010
JOB_STATUS_PAPEROUT              = 0x00000040
JOB_STATUS_PRINTED               = 0x00000080
JOB_STATUS_USER_INTERVENTION     = 0x00000400

PRINTER_ENUM_DEFAULT             = 0x00000001
PRINTER_ENUM_LOCAL               = 0x00000002
PRINTER_ENUM_CONNECTIONS         = 0x00000004
PRINTER_ENUM_FAVORITE            = 0x00000004
PRINTER_ENUM_NAME                = 0x00000008
PRINTER_ENUM_REMOTE              = 0x00000010
PRINTER_ENUM_SHARED              = 0x00000020
PRINTER_ENUM_NETWORK             = 0x00000040

BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_ESTADO = BASE_DIR / "simulador_estado.json"
DIR_OUTPUT_SIMULADO = BASE_DIR / "simulador_saida"

ESTADO_PADRAO = {
    "online": True,
    "sem_papel": False,
    "papel_preso": False,
    "tampa_aberta": False,
    "sem_toner": False,
    "toner_baixo": False,
    "tempo_por_folha_segundos": 0.5,
    "falhar_apos_copias": 0,
    "tipo_falha_automatica": "SEM_PAPEL"
}


class ImpressoraSimulada:
    """Gerencia o ciclo de vida e a mecânica da impressora simulada."""

    def __init__(self):
        self.lock = threading.Lock()
        DIR_OUTPUT_SIMULADO.mkdir(parents=True, exist_ok=True)
        self.carregar_ou_criar_estado()
        self.fila_jobs = []
        self.next_job_id = 100
        self.motor_ativo = False
        self.total_paginas_hardware = 15420

        self.thread_mecanismo = threading.Thread(target=self._ciclo_mecanico, daemon=True)
        self.thread_mecanismo.start()

    def carregar_ou_criar_estado(self):
        if not ARQUIVO_ESTADO.exists():
            with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
                json.dump(ESTADO_PADRAO, f, indent=2, ensure_ascii=False)
            self.estado = dict(ESTADO_PADRAO)
        else:
            try:
                with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
                    self.estado = json.load(f)
            except Exception:
                self.estado = dict(ESTADO_PADRAO)

    def salvar_estado(self):
        with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(self.estado, f, indent=2, ensure_ascii=False)

    def atualizar_estado_manual(self, chave: str, valor):
        with self.lock:
            self.estado[chave] = valor
            self.salvar_estado()

    def get_printer_status_bitmask(self):
        """Retorna o bitmask numérico exato que win32print.GetPrinter() entregaria."""
        with self.lock:
            self.carregar_ou_criar_estado()
            st = 0
            if not self.estado.get("online", True):
                return PRINTER_STATUS_OFFLINE

            if self.estado.get("sem_papel", False):
                st |= PRINTER_STATUS_PAPER_OUT | PRINTER_STATUS_USER_INTERVENTION
            if self.estado.get("papel_preso", False):
                st |= PRINTER_STATUS_PAPER_JAM | PRINTER_STATUS_ERROR
            if self.estado.get("tampa_aberta", False):
                st |= PRINTER_STATUS_DOOR_OPEN | PRINTER_STATUS_USER_INTERVENTION
            if self.estado.get("sem_toner", False):
                st |= PRINTER_STATUS_NO_TONER | PRINTER_STATUS_USER_INTERVENTION
            if self.estado.get("toner_baixo", False):
                st |= PRINTER_STATUS_TONER_LOW

            if self.motor_ativo:
                st |= PRINTER_STATUS_PRINTING | PRINTER_STATUS_BUSY

            return st

    def adicionar_job(self, doc_name: str, payload_bytes: bytes, total_copias: int = 1):
        """Recebe o stream binário de impressão do agente e enfileira na máquina."""
        with self.lock:
            self.carregar_ou_criar_estado()
            self.next_job_id += 1
            job_id = self.next_job_id
            safe_name = "".join(c for c in Path(doc_name).name if c.isalnum() or c in ("-", "_", ".")).strip()
            if not safe_name:
                safe_name = "impressao"

            # Salva o arquivo binário PJL bruto
            caminho_prn = DIR_OUTPUT_SIMULADO / f"job_{job_id}_{safe_name}.prn"
            with open(caminho_prn, "wb") as f:
                f.write(payload_bytes)

            # Detecta cópias solicitadas no PJL e extrai o PDF limpo para visualização
            copias_detectadas = total_copias
            try:
                match_copias = re.search(rb"@PJL SET COPIES\s*=\s*(\d+)", payload_bytes)
                if match_copias:
                    copias_detectadas = int(match_copias.group(1))

                # Extrai o fluxo PDF puro embutido no PJL
                idx_inicio = payload_bytes.find(b"%PDF-")
                idx_fim = payload_bytes.rfind(b"%%EOF")
                if idx_inicio != -1 and idx_fim != -1:
                    pdf_data = payload_bytes[idx_inicio:idx_fim + 5]
                    caminho_pdf = DIR_OUTPUT_SIMULADO / f"job_{job_id}_{safe_name}.pdf"
                    with open(caminho_pdf, "wb") as f:
                        f.write(pdf_data)
                    print(f"[SIMULADOR] 📄 PDF extraído com sucesso: {caminho_pdf.name}")
            except Exception:
                pass

            # Se há falha programada (ex: falhar na 10ª folha), limpa erro prévio para permitir início do lote
            falha_apos = int(self.estado.get("falhar_apos_copias", 0))
            if falha_apos > 0:
                self.estado["sem_papel"] = False
                self.estado["papel_preso"] = False
                self.estado["tampa_aberta"] = False
                tipo_f = self.estado.get("tipo_falha_automatica", "SEM_PAPEL")
                print(f"[SIMULADOR] 🧪 TESTE PROGRAMADO ATIVO: A máquina irá falhar com '{tipo_f}' na página {falha_apos}!")

            novo_job = {
                "JobId": job_id,
                "pDocument": doc_name,
                "Status": JOB_STATUS_PRINTING,
                "TotalPages": copias_detectadas,
                "PagesPrinted": 0,
                "caminho_arquivo": str(caminho_prn),
                "iniciado_em": time.time()
            }
            self.fila_jobs.append(novo_job)
            self.estado["job_ativo"] = {
                "JobId": job_id,
                "pDocument": doc_name,
                "TotalPages": copias_detectadas,
                "PagesPrinted": 0,
                "Status": "PRINTING"
            }
            self.salvar_estado()
            print(f"[SIMULADOR] 📥 Trabalho recebido na USB: JobId {job_id} | '{doc_name}' ({copias_detectadas} cópias)")
            return job_id

    def _ciclo_mecanico(self):
        """Thread interna que simula a passagem física do papel pelo rolo fusor."""
        while True:
            time.sleep(0.2)
            with self.lock:
                self.carregar_ou_criar_estado()
                if not self.fila_jobs:
                    self.motor_ativo = False
                    if "job_ativo" in self.estado:
                        del self.estado["job_ativo"]
                        self.salvar_estado()
                    continue

                # Se a máquina estiver sem energia, atolada, aberta ou sem papel, o motor para
                tem_erro = (
                    not self.estado.get("online", True) or 
                    self.estado.get("papel_preso", False) or 
                    self.estado.get("tampa_aberta", False) or
                    self.estado.get("sem_papel", False)
                )

                job_atual = self.fila_jobs[0]

                if tem_erro:
                    self.motor_ativo = False
                    if self.estado.get("sem_papel", False):
                        job_atual["Status"] = JOB_STATUS_PAPEROUT | JOB_STATUS_USER_INTERVENTION
                    elif self.estado.get("papel_preso", False):
                        job_atual["Status"] = JOB_STATUS_ERROR | JOB_STATUS_USER_INTERVENTION
                    elif self.estado.get("tampa_aberta", False):
                        job_atual["Status"] = JOB_STATUS_USER_INTERVENTION
                    elif not self.estado.get("online", True):
                        job_atual["Status"] = JOB_STATUS_ERROR

                    self.estado["job_ativo"] = {
                        "JobId": job_atual["JobId"],
                        "pDocument": job_atual["pDocument"],
                        "TotalPages": job_atual["TotalPages"],
                        "PagesPrinted": job_atual["PagesPrinted"],
                        "Status": "PAUSED_ERROR"
                    }
                    self.salvar_estado()
                    continue

                # Sem erros: motor ativo e rodando
                self.motor_ativo = True
                job_atual["Status"] = JOB_STATUS_PRINTING
                tempo_folha = float(self.estado.get("tempo_por_folha_segundos", 0.5))

            time.sleep(tempo_folha)

            with self.lock:
                if not self.fila_jobs or self.fila_jobs[0] != job_atual:
                    continue

                job_atual["PagesPrinted"] += 1
                self.total_paginas_hardware += 1
                print(f"[SIMULADOR] 🖨️ Página física ejetada ({job_atual['PagesPrinted']}/{job_atual['TotalPages']}) | Total da máquina: {self.total_paginas_hardware}")

                self.estado["job_ativo"] = {
                    "JobId": job_atual["JobId"],
                    "pDocument": job_atual["pDocument"],
                    "TotalPages": job_atual["TotalPages"],
                    "PagesPrinted": job_atual["PagesPrinted"],
                    "Status": "PRINTING"
                }
                self.salvar_estado()

                # Checagem de falha automática programada
                falha_apos = int(self.estado.get("falhar_apos_copias", 0))
                if falha_apos > 0 and job_atual["PagesPrinted"] >= falha_apos:
                    tipo = str(self.estado.get("tipo_falha_automatica", "SEM_PAPEL")).upper()
                    if "PAPEL" in tipo and "PRESO" not in tipo and "JAM" not in tipo:
                        self.estado["sem_papel"] = True
                        job_atual["Status"] = JOB_STATUS_PAPEROUT | JOB_STATUS_USER_INTERVENTION
                        print(f"\n[SIMULADOR] ⚠️⚠️⚠️ FALHA DISPARADA: Fim de papel na folha {job_atual['PagesPrinted']}! Impressora pausada. ⚠️⚠️⚠️\n")
                    elif "PRESO" in tipo or "JAM" in tipo:
                        self.estado["papel_preso"] = True
                        job_atual["Status"] = JOB_STATUS_ERROR | JOB_STATUS_USER_INTERVENTION
                        print(f"\n[SIMULADOR] ⚠️⚠️⚠️ FALHA DISPARADA: Papel preso / Atolamento na folha {job_atual['PagesPrinted']}! ⚠️⚠️⚠️\n")
                    elif "TAMPA" in tipo or "DOOR" in tipo:
                        self.estado["tampa_aberta"] = True
                        job_atual["Status"] = JOB_STATUS_USER_INTERVENTION
                        print(f"\n[SIMULADOR] ⚠️⚠️⚠️ FALHA DISPARADA: Tampa aberta na folha {job_atual['PagesPrinted']}! ⚠️⚠️⚠️\n")
                    elif "OFFLINE" in tipo or "TOMADA" in tipo:
                        self.estado["online"] = False
                        print(f"\n[SIMULADOR] ⚠️⚠️⚠️ FALHA DISPARADA: Impressora offline na folha {job_atual['PagesPrinted']}! ⚠️⚠️⚠️\n")

                    self.motor_ativo = False
                    self.estado["job_ativo"]["Status"] = "PAUSED_ERROR"
                    self.salvar_estado()
                    continue

                # Quando todas as cópias do trabalho saem na bandeja
                if job_atual["PagesPrinted"] >= job_atual["TotalPages"]:
                    concluido = self.fila_jobs.pop(0)
                    if "job_ativo" in self.estado:
                        del self.estado["job_ativo"]
                    self.salvar_estado()
                    print(f"[SIMULADOR] ✅ Trabalho {concluido['JobId']} finalizado com sucesso! Removido da fila USB.")


SIMULADOR = ImpressoraSimulada()


# ============================================================================
# CLASSE MOCKWIN32PRINT (EMULADOR COMPLETO DA API WIN32PRINT)
# ============================================================================
class MockWin32Print:
    """Emulador idêntico à API win32print do Windows para testes locais (Windows / Linux)."""

    PRINTER_STATUS_IDLE = PRINTER_STATUS_IDLE
    PRINTER_STATUS_PRINTING = PRINTER_STATUS_PRINTING
    PRINTER_STATUS_BUSY = PRINTER_STATUS_BUSY
    PRINTER_STATUS_PAPER_JAM = PRINTER_STATUS_PAPER_JAM
    PRINTER_STATUS_PAPER_OUT = PRINTER_STATUS_PAPER_OUT
    PRINTER_STATUS_DOOR_OPEN = PRINTER_STATUS_DOOR_OPEN
    PRINTER_STATUS_OFFLINE = PRINTER_STATUS_OFFLINE
    PRINTER_STATUS_WARMING_UP = PRINTER_STATUS_WARMING_UP
    PRINTER_STATUS_USER_INTERVENTION = PRINTER_STATUS_USER_INTERVENTION

    JOB_STATUS_PRINTING = JOB_STATUS_PRINTING
    JOB_STATUS_SPOOLING = JOB_STATUS_SPOOLING
    JOB_STATUS_PAPEROUT = JOB_STATUS_PAPEROUT
    JOB_STATUS_ERROR = JOB_STATUS_ERROR
    JOB_STATUS_DELETING = JOB_STATUS_DELETING
    JOB_STATUS_USER_INTERVENTION = JOB_STATUS_USER_INTERVENTION

    PRINTER_ENUM_DEFAULT = PRINTER_ENUM_DEFAULT
    PRINTER_ENUM_LOCAL = PRINTER_ENUM_LOCAL
    PRINTER_ENUM_CONNECTIONS = PRINTER_ENUM_CONNECTIONS

    @staticmethod
    def EnumPrinters(flags=PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS, name=None, level=1):
        """Emula EnumPrinters retornando a Brother simulada."""
        nome_impressora = "Brother MFC-L8610CDW series"
        if level == 1:
            return [
                (flags, f"{nome_impressora} (Simulada)", nome_impressora, "Impressora Virtual de Testes")
            ]
        elif level == 2:
            return [{
                "pServerName": "",
                "pPrinterName": nome_impressora,
                "pShareName": "",
                "pPortName": "USB001",
                "pDriverName": nome_impressora,
                "pComment": "Simulador Brother para testes no Windows/Linux",
                "pLocation": "Reprografia Escolar",
                "Status": SIMULADOR.get_printer_status_bitmask(),
                "cJobs": len(SIMULADOR.fila_jobs),
                "Attributes": 0
            }]
        return []

    @staticmethod
    def GetDefaultPrinter():
        """Retorna o nome da impressora padrão simulada."""
        return "Brother MFC-L8610CDW series"

    @staticmethod
    def OpenPrinter(printer_name):
        return {"printer_name": printer_name, "doc_name": "documento_spool"}

    @staticmethod
    def ClosePrinter(hprinter):
        return True

    @staticmethod
    def GetPrinter(hprinter, level=2):
        st = SIMULADOR.get_printer_status_bitmask()
        nome = hprinter.get("printer_name", "Brother MFC-L8610CDW series") if isinstance(hprinter, dict) else "Brother MFC-L8610CDW series"
        return {
            "pPrinterName": nome,
            "Status": st,
            "cJobs": len(SIMULADOR.fila_jobs)
        }

    @staticmethod
    def EnumJobs(hprinter, first_job=0, num_jobs=10, level=2):
        with SIMULADOR.lock:
            SIMULADOR.carregar_ou_criar_estado()
            if SIMULADOR.fila_jobs:
                return [{
                    "JobId": j["JobId"],
                    "pDocument": j["pDocument"],
                    "Status": j["Status"],
                    "TotalPages": j["TotalPages"],
                    "PagesPrinted": j["PagesPrinted"]
                } for j in SIMULADOR.fila_jobs]

            # Suporte cross-process se o agente rodar em processo separado
            job_ativo = SIMULADOR.estado.get("job_ativo")
            if job_ativo:
                st = JOB_STATUS_PRINTING
                if SIMULADOR.estado.get("sem_papel"):
                    st = JOB_STATUS_PAPEROUT | JOB_STATUS_USER_INTERVENTION
                elif SIMULADOR.estado.get("papel_preso"):
                    st = JOB_STATUS_ERROR | JOB_STATUS_USER_INTERVENTION
                elif SIMULADOR.estado.get("tampa_aberta"):
                    st = JOB_STATUS_USER_INTERVENTION

                return [{
                    "JobId": job_ativo.get("JobId", 101),
                    "pDocument": job_ativo.get("pDocument", "Documento"),
                    "Status": st,
                    "TotalPages": job_ativo.get("TotalPages", 35),
                    "PagesPrinted": job_ativo.get("PagesPrinted", 0)
                }]
            return []

    @staticmethod
    def StartDocPrinter(hprinter, level, doc_tuple):
        if isinstance(hprinter, dict) and doc_tuple and len(doc_tuple) > 0:
            hprinter["doc_name"] = str(doc_tuple[0])
        return 101

    @staticmethod
    def StartPagePrinter(hprinter):
        return True

    @staticmethod
    def WritePrinter(hprinter, data_bytes):
        doc_name = hprinter.get("doc_name", "documento_spool") if isinstance(hprinter, dict) else "documento_spool"
        return SIMULADOR.adicionar_job(doc_name, data_bytes)

    @staticmethod
    def EndPagePrinter(hprinter):
        return True

    @staticmethod
    def EndDocPrinter(hprinter):
        return True


# ============================================================================
# EXPORTAÇÕES DE NÍVEL DE MÓDULO (Compatibilidade total com win32print)
# ============================================================================
EnumPrinters = MockWin32Print.EnumPrinters
GetDefaultPrinter = MockWin32Print.GetDefaultPrinter
OpenPrinter = MockWin32Print.OpenPrinter
ClosePrinter = MockWin32Print.ClosePrinter
GetPrinter = MockWin32Print.GetPrinter
EnumJobs = MockWin32Print.EnumJobs
StartDocPrinter = MockWin32Print.StartDocPrinter
StartPagePrinter = MockWin32Print.StartPagePrinter
WritePrinter = MockWin32Print.WritePrinter
EndPagePrinter = MockWin32Print.EndPagePrinter
EndDocPrinter = MockWin32Print.EndDocPrinter


def menu_interativo():
    print("=" * 65)
    print(" 🖨️  SIMULADOR BROTHER MFC-L8610CDW (Windows / Linux)")
    print(f" Pasta de saída: {DIR_OUTPUT_SIMULADO}")
    print("=" * 65)
    print("Controle de Estados em Tempo Real:")
    print(" [1] PRONTA / OCIOSA (Limpa qualquer erro ativo)")
    print(" [2] Injetar: SEM PAPEL       (Status: 0x00000020)")
    print(" [3] Injetar: PAPEL PRESO     (Status: 0x00000008)")
    print(" [4] Injetar: TAMPA ABERTA    (Status: 0x00400000)")
    print(" [5] Injetar: OFFLINE/TOMADA  (Status: 0x00000080)")
    print(" [6] Abrir pasta de saída com PDFs gerados")
    print(" [s] Exibir Status Atual")
    print(" [q] Sair")
    print("=" * 65)

    while True:
        try:
            cmd = input("Simulador > ").strip().lower()
            if cmd == "1":
                SIMULADOR.atualizar_estado_manual("online", True)
                SIMULADOR.atualizar_estado_manual("sem_papel", False)
                SIMULADOR.atualizar_estado_manual("papel_preso", False)
                SIMULADOR.atualizar_estado_manual("tampa_aberta", False)
                print("[✔] Status: PRONTA (0x00000000) - Falhas limpas, motor liberado!")
            elif cmd == "2":
                SIMULADOR.atualizar_estado_manual("sem_papel", True)
                print("[!] Injetado: SEM PAPEL (0x00000020)")
            elif cmd == "3":
                SIMULADOR.atualizar_estado_manual("papel_preso", True)
                print("[!] Injetado: PAPEL PRESO (0x00000008)")
            elif cmd == "4":
                SIMULADOR.atualizar_estado_manual("tampa_aberta", True)
                print("[!] Injetado: TAMPA ABERTA (0x00400000)")
            elif cmd == "5":
                SIMULADOR.atualizar_estado_manual("online", False)
                print("[!] Injetado: OFFLINE / TOMADA PUXADA (0x00000080)")
            elif cmd == "6":
                if sys.platform == "win32":
                    os.startfile(DIR_OUTPUT_SIMULADO)
                else:
                    os.system(f"xdg-open '{DIR_OUTPUT_SIMULADO}' 2>/dev/null")
                print(f"[📂] Pasta aberta: {DIR_OUTPUT_SIMULADO}")
            elif cmd == "s":
                SIMULADOR.carregar_ou_criar_estado()
                st = SIMULADOR.get_printer_status_bitmask()
                job_info = SIMULADOR.estado.get("job_ativo")
                if job_info:
                    print(f"Status Bitmask: 0x{st:08X} | Trabalho Ativo: {job_info.get('pDocument')} | Folhas: {job_info.get('PagesPrinted')}/{job_info.get('TotalPages')} | Estado: {job_info.get('Status')}")
                else:
                    print(f"Status Bitmask: 0x{st:08X} | Jobs na fila: {len(SIMULADOR.fila_jobs)} | Total folhas impressora: {SIMULADOR.total_paginas_hardware}")
            elif cmd == "q":
                break
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    menu_interativo()
