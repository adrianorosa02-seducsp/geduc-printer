/**
 * BACKEND GOOGLE APPS SCRIPT - GESTÃO DE IMPRESSÕES ESCOLARES
 * Author: Adriano Justino Rosa
 * E.E. Antônio Reginato - Marília/SP
 *
 * Instruções de Implantação:
 * 1. Abra o Google Drive, crie uma Planilha chamada "Gestão de Impressões".
 * 2. Em Extensões > Apps Script, cole este código.
 * 3. Configure o ID da Pasta onde os PDFs serão salvos (ID_PASTA_UPLOADS).
 * 4. Clique em Implantar > Nova Implantação > Tipo: "App da Web" (Executar como: Eu; Quem pode acessar: Qualquer pessoa).
 */

const ID_PLANILHA = "1AwxeYKvK5icNZF745VxwLC1oh_7_lETZimxznyKWjm8";
const ID_PASTA_UPLOADS = "1fyOqw8dL6hkPZ48TDDrd-kLzTiVjsOS7";

function getSheetInstance() {
  let ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    ss = SpreadsheetApp.openById(ID_PLANILHA);
  }
  let sheet = ss.getSheetByName("Solicitações");
  if (!sheet) {
    sheet = ss.insertSheet("Solicitações");
    sheet.appendRow([
      "ID", "Data_Pedido", "Data_Necessidade", "Professor", "Turma", 
      "Disciplina", "Copias_Solicitadas", "Acabamento", "Cor", 
      "Status", "Copias_Impressas", "Nome_Arquivo", "Link_Drive_PDF", "Observacoes"
    ]);
  }
  return sheet;
}

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const sheet = getSheetInstance();

    // AÇÃO 1: ATUALIZAÇÃO DE STATUS (LIBERAR / CONCLUIR PELA GESTÃO OU AGENTE PYTHON)
    if (data.action === "updateStatus") {
      const rows = sheet.getDataRange().getValues();
      const targetId = data.id;
      let foundRow = -1;

      for (let i = 1; i < rows.length; i++) {
        if (rows[i][0] === targetId) {
          foundRow = i + 1; // 1-indexed no Sheet
          break;
        }
      }

      if (foundRow > -1) {
        if (data.status) {
          sheet.getRange(foundRow, 10).setValue(data.status); // Coluna J = Status
        }
        if (data.copiasImpressas !== undefined) {
          sheet.getRange(foundRow, 11).setValue(data.copiasImpressas); // Coluna K = Copias_Impressas
        }
        return ContentService.createTextOutput(JSON.stringify({
          status: "success",
          message: `Item ${targetId} atualizado para ${data.status}`
        })).setMimeType(ContentService.MimeType.JSON);
      } else {
        return ContentService.createTextOutput(JSON.stringify({
          status: "error",
          message: `ID ${targetId} não encontrado`
        })).setMimeType(ContentService.MimeType.JSON);
      }
    }

    // AÇÃO 2: NOVA SOLICITAÇÃO VINDA DO PROFESSOR (PADRÃO)
    const nextId = "SOL-" + (sheet.getLastRow() + 100);
    const timestamp = new Date().toISOString().split("T")[0];
    
    // Upload do PDF para a pasta do Google Drive
    let fileUrl = "";
    if (data.fileBase64 && data.fileName) {
      const folder = DriveApp.getFolderById(ID_PASTA_UPLOADS);
      const cleanBase64 = data.fileBase64.indexOf(",") > -1 ? data.fileBase64.split(",")[1] : data.fileBase64;
      const decodedBytes = Utilities.base64Decode(cleanBase64);
      const blob = Utilities.newBlob(decodedBytes, "application/pdf", `${nextId}_${data.fileName}`);
      const file = folder.createFile(blob);
      fileUrl = file.getUrl();
    }

    sheet.appendRow([
      nextId,
      timestamp,
      data.dataNecessidade || "",
      data.professor || "",
      data.turma || "",
      data.disciplina || "",
      data.copias || 35,
      data.acabamento || "Formato Livreto",
      data.cor || "P&B",
      "Pendente",
      0,
      data.fileName || "",
      fileUrl,
      data.observacoes || ""
    ]);

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      id: nextId,
      url: fileUrl,
      message: "Solicitação gravada com sucesso!"
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      message: error.toString()
    })).setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  try {
    const sheet = getSheetInstance();
    const rows = sheet.getDataRange().getValues();
    if (rows.length <= 1) {
      return ContentService.createTextOutput(JSON.stringify([])).setMimeType(ContentService.MimeType.JSON);
    }

    const headers = rows[0];
    const items = [];
    
    for (let i = 1; i < rows.length; i++) {
      let item = {};
      for (let j = 0; j < headers.length; j++) {
        item[headers[j]] = rows[i][j];
      }
      items.push(item);
    }
    
    return ContentService.createTextOutput(JSON.stringify(items)).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: err.toString() })).setMimeType(ContentService.MimeType.JSON);
  }
}
