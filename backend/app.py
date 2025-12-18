import sys
import os
import traceback
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI

# ===============================
# Configuração inicial
# ===============================
load_dotenv()

# Garante que o diretório pai está no path
caminho_pai = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if caminho_pai not in sys.path:
    sys.path.append(caminho_pai)

# Inicializa cliente OpenAI
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# Importações
from agendamentos import (
    inicializar_scheduler,
    detectar_nomes_remedios_ia,
    processar_agendamento_ia,
    criar_lembrete_diario_apscheduler,
    enviar_lembrete_whatsapp,
    listar_nomes_remedios,
    listar_remedios_com_horarios,
    remover_varios_remedios
)
from consultas import buscar_informacao
from vision_utils import extrair_informacao_imagem

# Flask setup
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

scheduler = inicializar_scheduler()

# ===============================
# Classificação da intenção
# ===============================
def classificar_intencao_com_openai(pergunta: str) -> str:
    system_prompt = (
        "Sua única tarefa é classificar a intenção da mensagem do usuário.\n\n"
        "IMPORTANTE: Se a mensagem contém um NOME DE REMÉDIO junto com HORÁRIO ou FREQUÊNCIA (ex: 'a cada X horas', 'às 10h', 'começando às', 'a partir de'), "
        "classifique como 'CRIAR AGENDAMENTO', mesmo que não tenha palavras explícitas como 'quero' ou 'criar'.\n\n"
        "Exemplos de 'CRIAR AGENDAMENTO':\n"
        "- 'tomar dipirona a cada 2 horas começando às 11h49'\n"
        "- 'dipirona às 10h'\n"
        "- 'tomar paracetamol a cada 6 horas a partir de 14h'\n"
        "- 'quero tomar remédio X às 9h'\n\n"
        "Use 'DÚVIDA CRIAR AGENDAMENTO' apenas se for uma PERGUNTA sobre COMO criar (ex: 'como criar lembrete?').\n"
        "Use 'DÚVIDA REMOVER AGENDAMENTO' se for uma PERGUNTA sobre COMO remover.\n"
        "Use 'DÚVIDA LISTAR AGENDAMENTOS' se for uma PERGUNTA sobre COMO listar.\n"
        "Use 'CRIAR AGENDAMENTO' se a mensagem contém: nome de remédio + horário/frequência (mesmo sem 'quero' ou 'criar').\n"
        "Use 'REMOVER AGENDAMENTO' se o usuário quiser REMOVER um lembrete existente.\n"
        "Use 'LISTAR AGENDAMENTOS' se o usuário quiser ver seus lembretes ativos.\n"
        "Use 'MEDICAMENTO' se for sobre bulas, remédios, dosagem, efeitos etc. (SEM horário/frequência).\n"
        "Use 'GERAL' se for qualquer outro tópico.\n"
        "Use 'IMAGEM' se o usuário enviar a foto de um remédio.\n\n"
        "Responda APENAS com um JSON: {\"intent\": \"[INTENÇÃO]\"}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ],
            temperature=0.0,
            max_tokens=50
        )
        json_string = response.choices[0].message.content.strip()
        intent_data = json.loads(json_string)
        intent = intent_data.get('intent', 'GERAL').strip().upper()

        print(f"[DEBUG CLASSIFICAÇÃO] JSON retornado: {json_string}")
        return intent
    except Exception as e:
        print(f"[ERRO CLASSIFICAÇÃO] {e}")
        return "GERAL"

# ===============================
# Resposta geral com OpenAI
# ===============================
def gerar_resposta_geral_com_openai(pergunta: str) -> str:
    try:
        system_prompt = (
            "Você é o RemedIAr, um assistente virtual amigável especializado "
            "em medicamentos e lembretes de remédios. Responda de forma clara e útil."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[ERRO GERAL] {e}")
        print(traceback.format_exc())
        return "Desculpe, ocorreu um erro ao gerar a resposta."

# ===============================
# Endpoint principal
# ===============================
@app.route('/')
def home():
    return 'Servidor Flask RemedIAr está rodando!'

@app.route('/bula/consultar', methods=['POST'])
def consultar_bula():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"status": "error", "resposta": f"JSON inválido: {e}"}), 400

    mensagem = data.get('mensagem', '').strip()
    telefone_usuario = data.get('telefone_usuario', 'DESTINO_PADRAO')
    chat_id_completo = data.get('chat_id_completo', None)  # ID completo do chat (com @lid ou @c.us)
    is_media = data.get('isMedia', False)
    media_data = data.get('mediaData')
    mime_type = data.get('mimeType', 'image/jpeg')

    print(f"[DEBUG] Dados recebidos de {telefone_usuario}: {list(data.keys())}")
    if chat_id_completo:
        print(f"[DEBUG] Chat ID completo recebido: {chat_id_completo}")

    # === Caso imagem ===
    if is_media and media_data:
        try:
            resposta = extrair_informacao_imagem(media_data, mime_type, chave_api)
            return jsonify({'status': 'success', 'resposta': resposta})
        except Exception as e:
            print(f"[ERRO IMAGEM] {e}")
            return jsonify({'status': 'error', 'resposta': 'Falha ao processar imagem.'}), 500

    if not mensagem:
        return jsonify({'status': 'error', 'resposta': 'Mensagem vazia.'}), 400

    try:
        # Importa confirmacoes_pendentes para verificar se há confirmação pendente
        from agendamentos import confirmacoes_pendentes
        
        # Se há uma confirmação pendente para este usuário, processa como CRIAR AGENDAMENTO
        # para que a função processar_agendamento_ia possa lidar com a resposta
        if telefone_usuario in confirmacoes_pendentes:
            print(f"[DEBUG] Confirmação pendente detectada para {telefone_usuario}, processando como CRIAR AGENDAMENTO")
            intent = "CRIAR AGENDAMENTO"
        else:
            intent = classificar_intencao_com_openai(mensagem)
            print(f"[DEBUG] Intenção classificada: {intent}")

        # === ROTAS DE INTENÇÃO ===
        if intent == "MEDICAMENTO":
            resposta = buscar_informacao(mensagem)

        elif intent == "GERAL":
            resposta = gerar_resposta_geral_com_openai(mensagem)

        elif intent == "CRIAR AGENDAMENTO":
            resposta = processar_agendamento_ia(mensagem, telefone_usuario, chat_id_completo)

        elif intent == "REMOVER AGENDAMENTO":
            nomes = detectar_nomes_remedios_ia(mensagem)
            if nomes:
                resposta = remover_varios_remedios(nomes)
            else:
                resposta = "⚠️ Não identifiquei o remédio. Tente novamente especificando o nome do remédio."

        elif intent == "LISTAR AGENDAMENTOS":
            remedios_info = listar_remedios_com_horarios()
            if not remedios_info:
                resposta = "📭 Nenhum lembrete ativo."
            else:
                resposta = "📋 *Remédios com lembretes ativos:*\n\n"
                for i, remedio in enumerate(remedios_info, 1):
                    nome = remedio['nome']
                    proximo_horario = remedio['proximo_horario']
                    resposta += f"{i}. *{nome}*\n   ⏰ Próximo lembrete: {proximo_horario}\n\n"
                resposta = resposta.rstrip()  # Remove a última quebra de linha extra

        elif intent == "IMAGEM":
            resposta = "Envie uma imagem válida para que eu possa ler o nome do remédio."

        elif intent == "DÚVIDA CRIAR AGENDAMENTO":
            resposta = "Para criar um lembrete, diga: `Quero tomar Dipirona às 10h e repetir a cada 6 horas.`"

        elif intent == "DÚVIDA LISTAR AGENDAMENTOS":
            resposta = "Para listar lembretes, diga: `listar agendamentos`."

        elif intent == "DÚVIDA REMOVER AGENDAMENTO":
            resposta = "Para remover, diga: `remover agendamento Dipirona`."

        else:
            resposta = gerar_resposta_geral_com_openai(mensagem)

        return jsonify({'status': 'success', 'resposta': resposta})

    except Exception as e:
        print(f"[ERRO CONSULTA] {e}")
        return jsonify({'status': 'error', 'resposta': 'Erro interno no processamento.'}), 500


# ===============================
# Inicialização do servidor
# ===============================
if __name__ == '__main__':
    print("[INFO] Inicializando scheduler...")
    scheduler = inicializar_scheduler()  # precisa retornar o objeto
    if not scheduler:
        print("[AVISO] Scheduler não retornou instância válida!")
    else:
        print("[OK] Scheduler iniciado com sucesso.")
        for job in scheduler.get_jobs():
            print("[JOB ATIVO]", job)

    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
