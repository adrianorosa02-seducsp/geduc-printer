#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIMULADOR DE IMPRESSORA BROTHER MFC-L8610CDW
E.E. Antônio Reginato - Ambiente de Desenvolvimento (Ubuntu / Linux)
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
- Conclusão limpa de lote com telemetria de cópias

Pode ser controlado interativamente pelo terminal ou via arquivo 'simulador_estado.json'.
"""

import os
import sys
import time
import json
import threading
from pathlib import Path

# ============================================================================
# CÓDIGOS DE STATUS OFICIAIS DO WINDOWS SPOOLER (win32print)
# ============================================================================
PRINTER_STATUS_IDLE             = 0x00000000  # Pronta / Ociosa
PRINTER_STATUS_PAUSED           = 0x00000001
PRINTER_STATUS_ERROR            = 0x00000002
PRINTER_STATUS_PAPER_JAM        = 0x00000008  # Papel Preso / Atolamento
PRINTER_STATUS_PAPER_OUT        = 0x00000020  # Sem Papel na bandeja
PRINTER_STATUS_OFFLINE          = 0x00000080  # Desconectada / Sem energia
PRINTER_STATUS_BUSY             = 0x00000200  # Ocupada processando
PRINTER_STATUS_PRINTING         = 0x00000400  # Motor ativo imprimindo
PRINTER_STATUS_WARMING_UP       = 0x00010000  # Aquecendo fusor
PRINTER_STATUS_TONER_LOW        = 0x00020000  # Toner baixo
PRINTER_STATUS_NO_TONER         = 0x00040000  # Sem toner
PRINTER_STATUS_DOOR_OPEN        = 0x00400000  # Tampa / Porta aberta
PRINTER_STATUS_USER_INTERVENTION= 0x00100000  # Requer intervenção manual

JOB_STATUS_PAUSED               = 0x00000001
JOB_STATUS_ERROR                = 0x00000002
JOB_STATUS_DELETING             = 0x00000004  # Cancelado
JOB_STATUS_SPOOLING             = 0x00000008
JOB_STATUS_PRINTING             = 0x00000010
JOB_STATUS_PAPEROUT             = 0x00000040
JOB_STATUS_PRINTED              = 0x00000080
JOB_STATUS_USER_INTERVENTION    = 0x00000400

ARQUIVO_ESTADO = Path("./simulador_estado.json")
DIR_OUTPUT_SIMULADO = Path("./simulador_saida")

ESTADO_PADRAO = {
    "online": True,
    "sem_papel": False,
    "papel_preso": False,
    "tampa_aberta": False,
    "sem_toner": False,
    "toner_baixo": False,
    "tempo_por_folha_segundos": 1.0,
    "falhar_apos_copias": 0,
    "tipo_falha_automatica": "SEM_PAPEL"
}


class ImpressoraSimulada:
    """Gerencia o ciclo de vida e a mecânica da impressora simulada."""

    def __init__(self):
        self.lock = threading.Lock()
        self.carregar_ou_criar_estado()
        DIR_OUTPUT_SIMULADO.mkdir(parents=True, exist_ok=True)
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
            self.next_job_id += 1
            job_id = self.next_job_id
            caminho_salvo = DIR_OUTPUT_SIMULADO / f"job_{job_id}_{Path(doc_name).name}"
            with open(caminho_salvo, "wb") as f:
                f.write(payload_bytes)

            novo_job = {
                "JobId": job_id,
                "pDocument": doc_name,
                "Status": JOB_STATUS_PRINTING,
                "TotalPages": total_copias,
                "PagesPrinted": 0,
                "caminho_arquivo": str(caminho_salvo),
                "iniciado_em": time.time()
            }
            self.fila_jobs.append(novo_job)
            print(f"[SIMULADOR] 📥 Trabalho recebido na USB: JobId {job_id} | '{doc_name}' ({total_copias} cópias)")
            return job_id

    def _ciclo_mecanico(self):
        """Thread interna que simula a passagem física do papel pelo rolo fusor."""
        while True:
            time.sleep(0.3)
            with self.lock:
                self.carregar_ou_criar_estado()
                if not self.fila_jobs:
                    self.motor_ativo = False
                    continue

                # Se a máquina estiver sem energia, atolada ou aberta, o motor para
                if (not self.estado.get("online", True) or 
                    self.estado.get("papel_preso", False) or 
                    self.estado.get("tampa_aberta", False)):
                    self.motor_ativo = False
                    continue

                job_atual = self.fila_jobs[0]
                self.motor_ativo = True

                # Pausa mecânica se faltar papel
                if self.estado.get("sem_papel", False):
                    job_atual["Status"] = JOB_STATUS_PAPEROUT | JOB_STATUS_USER_INTERVENTION
                    self.motor_ativo = False
                    continue

                tempo_folha = float(self.estado.get("tempo_por_folha_segundos", 1.0))

            time.sleep(tempo_folha)

            with self.lock:
                if not self.fila_jobs or self.fila_jobs[0] != job_atual:
                    continue

                job_atual["PagesPrinted"] += 1
                self.total_paginas_hardware += 1
                print(f"[SIMULADOR] 🖨️ Página física ejetada ({job_atual['PagesPrinted']}/{job_atual['TotalPages']}) | Total da máquina: {self.total_paginas_hardware}")

                # Quando todas as cópias do trabalho saem na bandeja
                if job_atual["PagesPrinted"] >= job_atual["TotalPages"]:
                    concluido = self.fila_jobs.pop(0)
                    print(f"[SIMULADOR] ✅ Trabalho {concluido['JobId']} finalizado com sucesso! Removido da fila USB.")


SIMULADOR = ImpressoraSimulada()


# ============================================================================
# CLASSE MOCKWIN32PRINT (SUBSTITUI WIN32PRINT NO UBUNTU SEM ERROS)
# ============================================================================
class MockWin32Print:
    """Emulador idêntico à API win32print do Windows para testes no Ubuntu."""

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

    @staticmethod
    def OpenPrinter(printer_name):
        return {"printer_name": printer_name}

    @staticmethod
    def ClosePrinter(hprinter):
        return True

    @staticmethod
    def GetPrinter(hprinter, level=2):
        st = SIMULADOR.get_printer_status_bitmask()
        return {
            "pPrinterName": hprinter.get("printer_name", "Brother MFC-L8610CDW (Simulada)"),
            "Status": st,
            "cJobs": len(SIMULADOR.fila_jobs)
        }

    @staticmethod
    def EnumJobs(hprinter, first_job, num_jobs, level=2):
        with SIMULADOR.lock:
            return [{
                "JobId": j["JobId"],
                "pDocument": j["pDocument"],
                "Status": j["Status"],
                "TotalPages": j["TotalPages"],
                "PagesPrinted": j["PagesPrinted"]
            } for j in SIMULADOR.fila_jobs]

    @staticmethod
    def StartDocPrinter(hprinter, level, doc_tuple):
        return 1

    @staticmethod
    def StartPagePrinter(hprinter):
        return True

    @staticmethod
    def WritePrinter(hprinter, data_bytes):
        SIMULADOR.adicionar_job("documento_spool.pdf", data_bytes)
        return len(data_bytes)

    @staticmethod
    def EndPagePrinter(hprinter):
        return True

    @staticmethod
    def EndDocPrinter(hprinter):
        return True


def menu_interativo():
    print("=" * 65)
    print(" 🖨️  SIMULADOR BROTHER MFC-L8610CDW INICIADO (Ubuntu)")
    print("=" * 65)
    print("Controle de Estados em Tempo Real:")
    print(" [1] PRONTA / OCIOSA (Limpa qualquer erro ativo)")
    print(" [2] Injetar: SEM PAPEL       (Status: 0x00000020)")
    print(" [3] Injetar: PAPEL PRESO     (Status: 0x00000008)")
    print(" [4] Injetar: TAMPA ABERTA    (Status: 0x00400000)")
    print(" [5] Injetar: OFFLINE/TOMADA  (Status: 0x00000080)")
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
                print("[✔] Status: PRONTA (0x00000000)")
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
            elif cmd == "s":
                st = SIMULADOR.get_printer_status_bitmask()
                print(f"Status Bitmask: 0x{st:08X} | Jobs na fila: {len(SIMULADOR.fila_jobs)} | Total folhas: {SIMULADOR.total_paginas_hardware}")
            elif cmd == "q":
                break
        except (KeyboardInterrupt, EOFError):
            break

if __name__ == "__main__":
    menu_interativo()
