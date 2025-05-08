from fastapi import FastAPI, File, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import docx  # pip install python-docx
import os
import smtplib
import re  # 👈 usamos para buscar o CNPJ no texto 
import httpx  # para consultar APIs externas
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel
from typing import List
from api.utils_protegidas import consultar_todos_os_orgaos
from dotenv import load_dotenv
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")



def extract_cnpj(text):
    match = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', text)
    return match.group() if match else None

async def validate_cnpj(cnpj):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://www.receitaws.com.br/v1/cnpj/{cnpj}")
            if response.status_code == 200:
                data = response.json()
                return {
                    "status": data.get("situacao"),
                    "razao_social": data.get("nome"),
                    "abertura": data.get("abertura"),
                    "uf": data.get("uf")
                }
    except Exception:
        pass
    return {"status": "inválido ou não encontrado"}


app = FastAPI()

# Middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "API ESG backend funcionando 🎉"}

# Função para cálculo de score ESG
def calcular_score(texto: str) -> int:
    texto = texto.lower()
    palavras_chave = [
        "esg", "sustentabilidade", "governança", "risco climático", "ifrs",
        "emissões", "escopo 1", "escopo 2", "escopo 3", "transição ecológica",
        "política", "metas", "indicadores", "relatório", "conformidade"
    ]
    pontos = sum(1 for palavra in palavras_chave if palavra in texto)
    return min(100, pontos * 10)  # Score de 0 a 100

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    filename = file.filename.lower()
    contents = await file.read()

    temp_path = f"temp_{filename}"
    with open(temp_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            doc = fitz.open(temp_path)
            text = "".join(page.get_text("text") for page in doc)
            doc.close()
            print("Texto extraído:", text[:1000])
        elif filename.endswith(".docx"):
            doc = docx.Document(temp_path)
            text = "\n".join([p.text for p in doc.paragraphs])
        elif filename.endswith(".txt"):
            with open(temp_path, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            return {"error": "Formato de arquivo não suportado"}
        if not text:
            return {"error": "Arquivo enviado está vazio ou não pôde ser lido."}

        linhas = text.splitlines()
        empresa = next((linha for linha in linhas if "empresa" in linha.lower()), "Empresa Auditada")

        def validar_empresa_publica(nome):
            if "fake" in nome.lower() or "jurandir" in nome.lower():
                return False
            return True

        validacao_publica = validar_empresa_publica(empresa)
        score = calcular_score(text)
        cnpj = extract_cnpj(text)
        dados_orgaos = await consultar_todos_os_orgaos(cnpj)

        return {
            "empresa": empresa.strip(),
            "score": score,
            "validacao_publica": validacao_publica,
            "texto": text,
            "cnpj": cnpj,
            "orgaos_publicos": dados_orgaos
        }

    except Exception as e:
        return {"error": f"Erro ao processar arquivo: {str(e)}"}


@app.post("/send-email")
async def send_email(request: Request):
    data = await request.json()
    file_name = data.get("fileName", "")
    score = data.get("score", "")
    content = data.get("content", "")
    emails = data.get("email", "")  # Aqui usamos "email", como vem do frontend

    if not emails:
        return JSONResponse(status_code=400, content={"message": "Endereço de e-mail não fornecido."})

    recipients = [e.strip() for e in emails.split(",") if e.strip()]
    subject = "Resultado da Análise ESG"
    body = f"Arquivo: {file_name}\nPontuação: {score}\n\nConteúdo da Análise:\n{content}"

    try:
        for receiver in recipients:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_SENDER
            msg['To'] = receiver
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
            server.quit()

        return JSONResponse(content={"message": "📤 E-mail enviado com sucesso!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": f"Erro ao enviar e-mail: {str(e)}"})


@app.get("/orgaos-publicos/{cnpj}")
async def consultar_orgaos_publicos(cnpj: str):
    dados = await consultar_todos_os_orgaos(cnpj)
    return dados
