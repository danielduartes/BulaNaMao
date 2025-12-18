from time import timezone
from urllib.parse import quote
import os
import json
from dotenv import load_dotenv
import openai
from flask import request
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler import events

load_dotenv()
chave_api = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")  # Chave da API do Google
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")    # ID do Custom Search Engine

NODE_JS_URL = 'http://localhost:3000/enviar_lembrete'
scheduler = None

# Dicionário para armazenar confirmações pendentes de sobreposição
# Formato: {telefone_usuario: {'nome_remedio': str, 'horario': str, 'frequencia_horas': int, 'chat_id_completo': str}}
confirmacoes_pendentes = {}

# === Inicialização do agendador ===
def inicializar_scheduler():
    global scheduler
    # Se o scheduler já existe e está rodando, retorna ele
    if scheduler is not None:
        if scheduler.running:
            print(f"[SCHEDULER] ✅ Scheduler já está rodando. Jobs ativos: {len(scheduler.get_jobs())}")
            return scheduler
        else:
            # Se parou, reinicia
            print("[SCHEDULER] ⚠️ Scheduler parado, reiniciando...")
            scheduler.start()
            return scheduler
    
    # Cria um novo scheduler apenas se não existir
    print("[SCHEDULER] 🚀 Criando novo scheduler...")
    scheduler = BackgroundScheduler()
    
    # Adiciona listeners para debug
    def job_executado(event):
        print(f"[SCHEDULER] ⏰ Job executado: {event.job_id} às {datetime.now().strftime('%H:%M:%S')}")
    
    def job_erro(event):
        print(f"[SCHEDULER] ❌ Erro ao executar job {event.job_id}: {event.exception}")
        import traceback
        traceback.print_exc()
    
    scheduler.add_listener(job_executado, events.EVENT_JOB_EXECUTED)
    scheduler.add_listener(job_erro, events.EVENT_JOB_ERROR)
    
    scheduler.start()
    print("[SCHEDULER] ✅ Iniciado com sucesso.")
    return scheduler


# === Envio de lembrete para o Node.js ===
def eh_url_imagem_direta(imagem_url):
    """
    Verifica se a URL é uma imagem direta (não é crawler, widget, API, etc.)
    Retorna True se for uma URL de imagem válida, False caso contrário.
    """
    if not imagem_url:
        return False
    
    url_lower = imagem_url.lower()
    
    # Rejeita URLs de crawlers, widgets, APIs, etc.
    padroes_rejeitados = [
        'crawler',
        'widget',
        'seo/google_widget',
        'api/',
        '/api/',
        'lookaside',
        'proxy',
        'redirect',
        '?media_id=',
        'google_widget',
        'instagram.com/seo',
        'facebook.com/tr',
    ]
    
    for padrao in padroes_rejeitados:
        if padrao in url_lower:
            print(f"[VALIDACAO URL] ❌ URL rejeitada (contém '{padrao}'): {imagem_url[:80]}...")
            return False
    
    # Verifica se a URL termina com extensão de imagem ou contém extensão de imagem
    extensoes_imagem = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    url_sem_query = url_lower.split('?')[0]  # Remove query parameters
    
    # Verifica se termina com extensão de imagem
    if any(url_sem_query.endswith(ext) for ext in extensoes_imagem):
        return True
    
    # Verifica se contém extensão de imagem no caminho (não apenas no final)
    # Exemplo: https://example.com/images/photo.jpg?size=large
    if any(f'/{ext[1:]}' in url_sem_query or f'.{ext[1:]}' in url_sem_query for ext in extensoes_imagem):
        return True
    
    # Se não tem extensão clara, verifica o Content-Type via HEAD request
    try:
        response = requests.head(imagem_url, timeout=5, allow_redirects=True)
        content_type = response.headers.get('content-type', '').lower()
        if content_type.startswith('image/'):
            print(f"[VALIDACAO URL] ✅ URL aceita (Content-Type: {content_type})")
            return True
    except:
        pass
    
    print(f"[VALIDACAO URL] ⚠️ URL não identificada como imagem direta: {imagem_url[:80]}...")
    return False


def validar_imagem_com_vision_api(imagem_url, nome_remedio_esperado):
    """
    Valida se a imagem realmente corresponde ao remédio esperado usando Vision API.
    Retorna True se a imagem parece corresponder ao remédio, False caso contrário.
    """
    # Primeiro verifica se é uma URL de imagem direta
    if not eh_url_imagem_direta(imagem_url):
        print(f"[VALIDACAO IMG] ❌ URL não é uma imagem direta, rejeitando")
        return False
    
    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        
        
        # Faz download da imagem
        response_img = requests.get(imagem_url, timeout=10)
        if response_img.status_code != 200:
            print(f"[VALIDACAO IMG] ⚠️ Não foi possível baixar a imagem para validação (status: {response_img.status_code})")
            return False  # Rejeita se não conseguir baixar
        
        # Verifica o Content-Type da resposta
        content_type = response_img.headers.get('content-type', '').lower()
        if not content_type.startswith('image/'):
            print(f"[VALIDACAO IMG] ❌ Content-Type inválido: {content_type}")
            return False
        
        import base64
        imagem_base64 = base64.b64encode(response_img.content).decode('utf-8')
        mime_type = content_type
        
        prompt_validacao = (
            f"Analise esta imagem de uma embalagem de medicamento. "
            f"O nome do remédio esperado é: '{nome_remedio_esperado}'. "
            f"Responda APENAS com 'SIM' se a imagem mostra claramente o remédio '{nome_remedio_esperado}', "
            f"ou 'NÃO' se a imagem mostra um remédio diferente ou não é possível identificar claramente o nome '{nome_remedio_esperado}' na embalagem."
        )
        
        image_url_data = f"data:{mime_type};base64,{imagem_base64}"
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Você é um especialista em identificar medicamentos em embalagens. Seja rigoroso na validação."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt_validacao},
                    {"type": "image_url", "image_url": {"url": image_url_data}}
                ]}
            ],
            max_tokens=10,
            temperature=0
        )
        
        resposta = response.choices[0].message.content.strip().upper()
        valido = "SIM" in resposta
        
        print(f"[VALIDACAO IMG] {'✅' if valido else '❌'} Imagem {'válida' if valido else 'inválida'} para '{nome_remedio_esperado}'")
        return valido
        
    except Exception as e:
        print(f"[VALIDACAO IMG] ⚠️ Erro ao validar imagem (rejeitando): {e}")
        return False  # Rejeita se houver erro na validação


def buscar_google_custom_search(nome_remedio):
    """
    Busca imagem via Google Custom Search API (requer GOOGLE_API_KEY e GOOGLE_CSE_ID).
    Esta é a estratégia mais confiável para encontrar imagens reais de produtos.
    Valida as imagens encontradas usando Vision API para garantir que correspondem ao remédio correto.
    """
    if not os.getenv("GOOGLE_API_KEY") or not os.getenv("GOOGLE_CSE_ID"):
        print(f"[DEBUG IMG Google API] ⚠️ Chaves não configuradas")
        print(f"[DEBUG IMG Google API]   - GOOGLE_API_KEY: {'✅' if os.getenv("GOOGLE_API_KEY") else '❌'}")
        print(f"[DEBUG IMG Google API]   - GOOGLE_CSE_ID: {'✅' if os.getenv("GOOGLE_CSE_ID") else '❌'}")
        return None
    
    try:
        # Query mais específica usando aspas para buscar o nome exato do remédio
        # Isso ajuda a evitar resultados de remédios similares
        queries = [
            f'"{nome_remedio}" caixa remédio medicamento embalagem',
            f'"{nome_remedio}" medicamento farmácia',
            f'caixa "{nome_remedio}" remédio',
        ]
        
        for query in queries:
            print(f"[DEBUG IMG Google API] 🔍 Buscando com query: {query}")
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": os.getenv("GOOGLE_API_KEY"),
                "cx": os.getenv("GOOGLE_CSE_ID"),
                "q": query,
                "searchType": "image",
                "num": 10,  # Pega até 10 resultados para ter mais opções
                "safe": "active",
                "imgSize": "large",  # Prioriza imagens grandes
                "imgType": "photo",  # Prioriza fotos (não ilustrações)
            }
            
            res = requests.get(url, params=params, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                print(f"[DEBUG IMG Google API] 📊 Total de resultados encontrados: {data.get('searchInformation', {}).get('totalResults', 0)}")
                
                if "items" in data and len(data["items"]) > 0:
                    # Prioriza imagens que parecem ser de caixas de remédios e valida com Vision API
                    for idx, item in enumerate(data["items"]):
                        link = item.get("link", "")
                        # Verifica se a URL parece ser de uma imagem válida
                        if link and link.startswith(('http://', 'https://')):
                            # Primeiro verifica se é uma URL de imagem direta (filtra crawlers, widgets, etc.)
                            if not eh_url_imagem_direta(link):
                                print(f"[DEBUG IMG Google API] ⏭️ URL rejeitada (não é imagem direta), tentando próxima...")
                                continue
                            
                            # Prioriza URLs que não sejam thumbnails
                            if not any(x in link.lower() for x in ['thumb', 'thumbnail', 'w=120', 'h=120', 'w=100', 'h=100']):
                                print(f"[DEBUG IMG Google API] 🔍 Validando imagem (resultado {idx+1}): {link[:80]}...")
                                # Valida se a imagem realmente corresponde ao remédio
                                if validar_imagem_com_vision_api(link, nome_remedio):
                                    print(f"[DEBUG IMG Google API] ✅ URL validada e aceita (resultado {idx+1}): {link}")
                                    return link
                                else:
                                    print(f"[DEBUG IMG Google API] ❌ Imagem rejeitada na validação, tentando próxima...")
                    
                    # Se não encontrou uma validada, tenta encontrar uma URL válida sem validação Vision (fallback)
                    for idx, item in enumerate(data["items"]):
                        link = item.get("link", "")
                        if link and link.startswith(('http://', 'https://')):
                            if eh_url_imagem_direta(link):
                                print(f"[DEBUG IMG Google API] ⚠️ Usando primeira URL válida disponível sem validação Vision: {link[:80]}...")
                                return link
                    
                    # Último recurso: primeira URL mesmo que não seja validada
                    primeira_url = data["items"][0].get("link")
                    if primeira_url:
                        print(f"[DEBUG IMG Google API] ⚠️⚠️ Usando primeira URL disponível sem validação: {primeira_url[:80]}...")
                        return primeira_url
            elif res.status_code == 403:
                print(f"[ERRO IMG Google API] ❌ Acesso negado (403). A Custom Search API precisa ser habilitada.")
                print(f"[ERRO IMG Google API] 📝 Acesse: https://console.cloud.google.com/apis/library/customsearch.googleapis.com")
                print(f"[ERRO IMG Google API]   1. Selecione o projeto correto")
                print(f"[ERRO IMG Google API]   2. Clique em 'Enable' para habilitar a API")
                print(f"[ERRO IMG Google API]   3. Aguarde alguns segundos e tente novamente")
                print(f"[ERRO IMG Google API] ⚠️ Continuando com outras estratégias de busca...")
                break  # Não tenta outras queries se for erro de permissão
            else:
                print(f"[ERRO IMG Google API] Status {res.status_code}: {res.text[:200]}")
    except Exception as e:
        print(f"[ERRO IMG Google API] Exceção: {e}")
        import traceback
        traceback.print_exc()
    return None


def buscar_imagem_remedio(nome_remedio):
    """
    Busca imagem do remédio usando múltiplas fontes.
    Retorna None se não encontrar ou houver erro (não bloqueia o envio).
    """
    # Tenta diferentes estratégias de busca
    estrategias = [
        # Estratégia 1: Google Custom Search API (mais confiável, requer chave)
        lambda: buscar_google_custom_search(nome_remedio),
        # Estratégia 2: DuckDuckGo (busca no HTML)
        lambda: buscar_duckduckgo(nome_remedio),
        # Estratégia 3: Google Images (extração do HTML)
        lambda: buscar_google_images(nome_remedio),
        # Estratégia 4: Bing Image Search
        lambda: buscar_bing_images(nome_remedio),
    ]
    
    for i, estrategia in enumerate(estrategias, 1):
        try:
            print(f"[DEBUG IMG] Tentando estratégia {i} para {nome_remedio}...")
            imagem_url = estrategia()
            if imagem_url:
                print(f"[DEBUG IMG] ✅ Imagem encontrada com estratégia {i}: {imagem_url[:80]}...")
                return imagem_url
        except Exception as e:
            print(f"[ERRO IMG] Estratégia {i} falhou: {e}")
            continue
    
    print(f"[ERRO IMG] Nenhuma imagem encontrada para {nome_remedio} após tentar todas as estratégias")
    return None


def buscar_duckduckgo(nome_remedio):
    """Busca imagem via DuckDuckGo focando em caixa do remédio"""
    try:
        # Query mais específica usando aspas para buscar o nome exato
        query = quote(f'"{nome_remedio}" caixa remédio medicamento embalagem')
        url = f"https://duckduckgo.com/?q={query}&iax=images&ia=images"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            import re
            # Padrões para encontrar imagens maiores (não thumbnails)
            patterns = [
                r'data-src="(https://[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"',
                r'src="(https://[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"',
            ]
            urls_encontradas = []
            for pattern in patterns:
                matches = re.findall(pattern, res.text, re.IGNORECASE)
                urls_encontradas.extend(matches)
            
            # Filtra URLs: prioriza imagens maiores, evita thumbnails pequenos
            for url_img in urls_encontradas:
                if url_img.startswith(('http://', 'https://')):
                    # Evita thumbnails muito pequenos
                    if not any(x in url_img.lower() for x in ['thumb', 'thumbnail', 'w=120', 'h=120', 'w=100', 'h=100']):
                        return url_img
                    # Se só tiver thumbnails, pega o primeiro
                    elif len(urls_encontradas) == 1:
                        return url_img
    except Exception as e:
        print(f"[ERRO IMG DuckDuckGo] {e}")
    return None


def buscar_google_images(nome_remedio):
    """Busca imagem via Google Images focando em caixa do remédio"""
    try:
        # Query mais específica usando aspas para buscar o nome exato
        query = quote(f'"{nome_remedio}" caixa remédio medicamento embalagem')
        url = f"https://www.google.com/search?q={query}&tbm=isch&tbs=isz:m"  # isz:m = medium size
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            import re
            # Padrões do Google Images - prioriza URL original (ou)
            img_patterns = [
                r'"ou":"(https://[^"]*)"',  # URL original (melhor qualidade)
                r'"murl":"(https://[^"]*)"',  # Mobile URL
            ]
            urls_encontradas = []
            for pattern in img_patterns:
                matches = re.findall(pattern, res.text)
                urls_encontradas.extend(matches)
            
            # Filtra e prioriza URLs maiores
            for url_img in urls_encontradas:
                if url_img.startswith(('http://', 'https://')):
                    # Evita thumbnails pequenos
                    if not any(x in url_img.lower() for x in ['thumb', 'thumbnail', 'w=120', 'h=120', 'encrypted-tbn']):
                        return url_img
                    # Se for encrypted-tbn mas for grande, aceita
                    elif 'encrypted-tbn' in url_img and ('=s0' in url_img or '=s320' in url_img or '=s640' in url_img):
                        return url_img
    except Exception as e:
        print(f"[ERRO IMG Google] {e}")
    return None


def buscar_bing_images(nome_remedio):
    """Busca imagem via Bing Image Search focando em caixa do remédio"""
    try:
        # Query mais específica usando aspas para buscar o nome exato
        query = quote(f'"{nome_remedio}" caixa remédio medicamento embalagem')
        url = f"https://www.bing.com/images/search?q={query}&qft=+filterui:imagesize-large"  # Filtra imagens grandes
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            import re
            from html import unescape
            # Procura por URLs de imagens no HTML do Bing - prioriza murl (URL original)
            patterns = [
                r'murl":"(https://[^"]*)"',  # URL completa do Bing (melhor qualidade)
                r'data-src="(https://[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"',
            ]
            urls_encontradas = []
            for pattern in patterns:
                matches = re.findall(pattern, res.text, re.IGNORECASE)
                for match in matches:
                    # Remove entidades HTML e limpa a URL
                    url_limpa = unescape(match).replace('&amp;', '&')
                    if url_limpa.startswith(('http://', 'https://')):
                        urls_encontradas.append(url_limpa)
            
            # Prioriza URLs que não sejam thumbnails pequenos
            for url_img in urls_encontradas:
                # Evita thumbnails muito pequenos (w=120, h=120)
                if 'th?' not in url_img or ('w=120' not in url_img and 'h=120' not in url_img):
                    return url_img
                # Se for thumbnail mas for maior (w=300+), aceita
                elif 'th?' in url_img and ('w=300' in url_img or 'w=400' in url_img or 'w=500' in url_img):
                    return url_img
            
            # Se só encontrar thumbnails pequenos, retorna o primeiro (melhor que nada)
            if urls_encontradas:
                return urls_encontradas[0]
    except Exception as e:
        print(f"[ERRO IMG Bing] {e}")
    return None




def enviar_lembrete_whatsapp(telefone_destino, nome_remedio, horario_exibicao, chat_id_completo=None, frequencia_horas=24, imagem_url=None):
    """
    Envia lembrete de medicamento com imagem e próximo horário.
    A imagem_url deve ser fornecida (já buscada na criação do agendamento).
    """
    try:
        print(f"[LEMBRETE] Iniciando envio de lembrete para {nome_remedio}")
        
        # Calcula o próximo horário do lembrete
        agora = datetime.now()
        proximo_horario = agora + timedelta(hours=frequencia_horas)
        proximo_horario_str = proximo_horario.strftime("%H:%M")
        
        # Usa a imagem que foi salva na criação do agendamento
        if imagem_url:
            # Limpa a URL removendo entidades HTML
            from html import unescape
            imagem_url = unescape(imagem_url).replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            print(f"[LEMBRETE] 📷 Usando imagem salva (limpa): {imagem_url[:80]}...")
        else:
            print(f"[LEMBRETE] ⚠️ Nenhuma imagem salva para {nome_remedio}")
        
        # Monta a mensagem com o próximo horário
        mensagem_alerta = (
            f"💊 *Lembrete: Hora de tomar {nome_remedio}!*\n\n"
            f"⏰ Próximo lembrete: {proximo_horario_str}"
        )
        
        print(f"[LEMBRETE] Mensagem preparada: {mensagem_alerta[:50]}...")
        
        url = NODE_JS_URL

        # Se temos o chat_id_completo, usa ele diretamente (já tem o formato correto com @lid)
        if chat_id_completo:
            print(f"[DEBUG LEMBRETE] Usando chat_id_completo: {chat_id_completo}")
            payload = {
                "to": chat_id_completo,  # Usa o ID completo do chat
                "message": mensagem_alerta,
                "image_url": imagem_url if imagem_url else None,  # URL da imagem do remédio
            }
        else:
            # Fallback: Remove @c.us se já tiver, pois o Node.js vai formatar corretamente
            numero_limpo = telefone_destino.replace("@c.us", "").replace("@lid", "")
            
            # Garante que o número está limpo (apenas dígitos, sem @)
            numero_limpo = ''.join(filter(str.isdigit, numero_limpo))
            
            # Se não começar com 55, adiciona (código do Brasil)
            if not numero_limpo.startswith('55'):
                numero_limpo = '55' + numero_limpo
            
            # Limita a 13 dígitos
            if len(numero_limpo) > 13:
                numero_limpo = numero_limpo[:13]
            
            print(f"[DEBUG LEMBRETE] Enviando para número: {numero_limpo} (original: {telefone_destino})")
            payload = {
                "to": numero_limpo,  # Envia apenas o número, sem @c.us
                "message": mensagem_alerta,
                "image_url": imagem_url if imagem_url else None,  # URL da imagem do remédio
            }

        try:
            destino_log = payload.get('to', telefone_destino)
            print(f"[LEMBRETE] Enviando para Node.js: {destino_log}")
            print(f"[LEMBRETE] Payload: to={payload.get('to')}, message={payload.get('message')[:50]}...")
            print(f"[LEMBRETE] 📷 image_url: {payload.get('image_url') if payload.get('image_url') else 'NENHUMA'}")
            
            response = requests.post(NODE_JS_URL, json=payload, timeout=15)
            if response.status_code==200:
                print(f"[LEMBRETE] ✅ Enviado com sucesso para {destino_log} (imagem: {'sim' if imagem_url else 'não'})")
            else:
                print(f"[ERRO NODE] {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as e:
            destino_log = payload.get('to', telefone_destino)
            print(f"[ERRO CONEXÃO] Falha ao enviar lembrete para {destino_log}: {e}")
            import traceback
            traceback.print_exc()
    except Exception as e:
        print(f"[ERRO LEMBRETE] Erro geral ao processar lembrete: {e}")
        import traceback
        traceback.print_exc()


# === Criar lembrete com repetição (nova função) ===
def criar_lembrete_apscheduler(scheduler, telefone, nome_remedio, horario, frequencia_horas, chat_id_completo=None):
    """
    Cria um lembrete que se repete a cada X horas, começando no horário especificado.
    """
    if scheduler is None:
        print("[ERRO] Scheduler não inicializado!")
        return False

    try:
        # Normaliza o formato do horário (aceita "12:01" ou "12h01")
        horario_normalizado = horario.replace('h', ':').replace('H', ':')
        if ':' not in horario_normalizado:
            # Se não tem separador, assume formato "1201" -> "12:01"
            if len(horario_normalizado) == 4:
                horario_normalizado = horario_normalizado[:2] + ':' + horario_normalizado[2:]
        
        hora = int(horario_normalizado[:2])
        minuto = int(horario_normalizado[3:5]) if len(horario_normalizado) > 3 else 0
        
        # Cria um ID único incluindo frequência
        nome_limpo = nome_remedio.replace(' ', '_').lower()
        job_id = f"{telefone}-{nome_limpo}-{horario_normalizado.replace(':', 'h')}-{frequencia_horas}h"
        
        # Calcula o próximo horário de execução
        agora = datetime.now()
        horario_inicio = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
        
        print(f"[SCHEDULER] 📅 Agora: {agora.strftime('%H:%M:%S')} | Horário solicitado: {horario_inicio.strftime('%H:%M')}")
        
        # Se o horário já passou hoje, agenda para amanhã
        if horario_inicio < agora:
            horario_inicio += timedelta(days=1)
            print(f"[SCHEDULER] ⏭️ Horário já passou, agendando para amanhã: {horario_inicio.strftime('%H:%M')}")
        else:
            print(f"[SCHEDULER] ✅ Horário ainda não passou, agendando para hoje: {horario_inicio.strftime('%H:%M')}")
        
        # Busca a imagem do remédio AGORA (na criação do agendamento)
        print(f"[SCHEDULER] 🔍 Buscando imagem para {nome_remedio}...")
        print(f"[SCHEDULER] 🔑 Google API Key configurada: {'Sim' if os.getenv("GOOGLE_API_KEY") else 'Não'}")
        print(f"[SCHEDULER] 🔑 Google CSE ID configurado: {'Sim' if os.getenv("GOOGLE_CSE_ID") else 'Não'}")
        imagem_url_salva = None
        try:
            imagem_url_salva = buscar_imagem_remedio(nome_remedio)
            if imagem_url_salva:
                print(f"[SCHEDULER] ✅ Imagem encontrada e salva: {imagem_url_salva}")
                print(f"[SCHEDULER] 📏 Tamanho da URL: {len(imagem_url_salva)} caracteres")
            else:
                print(f"[SCHEDULER] ⚠️ Imagem não encontrada para {nome_remedio} (lembrete será enviado sem imagem)")
        except Exception as img_error:
            print(f"[SCHEDULER] ⚠️ Erro ao buscar imagem (continuando sem imagem): {img_error}")
            import traceback
            traceback.print_exc()
            imagem_url_salva = None
        
        # Cria uma função wrapper que envia o lembrete e agenda o próximo
        proximo_id = f"{job_id}-repeticao"
        
        def enviar_e_reagendar():
            """Envia o lembrete e agenda o próximo"""
            try:
                print(f"[SCHEDULER] 🚀 Executando job: {job_id}")
                # Envia o lembrete com a imagem salva
                enviar_lembrete_whatsapp(telefone, nome_remedio, horario, chat_id_completo, frequencia_horas, imagem_url_salva)
                
                # Remove o job atual se for o inicial
                try:
                    if scheduler.get_job(job_id):
                        scheduler.remove_job(job_id)
                        print(f"[SCHEDULER] 🗑️ Job inicial removido: {job_id}")
                except:
                    pass
                
                # Agenda o próximo lembrete (a cada X horas a partir de agora) apenas se não existir
                if not scheduler.get_job(proximo_id):
                    scheduler.add_job(
                        func=enviar_e_reagendar,
                        trigger=IntervalTrigger(hours=frequencia_horas),
                        id=proximo_id,
                        replace_existing=False
                    )
                    print(f"[SCHEDULER] ✅ Próximo lembrete agendado (a cada {frequencia_horas}h)")
            except Exception as e:
                print(f"[SCHEDULER] ❌ Erro ao executar/enviar lembrete: {e}")
                import traceback
                traceback.print_exc()
        
        # Cria o job inicial com DateTrigger para executar no horário específico
        scheduler.add_job(
            func=enviar_e_reagendar,
            trigger=DateTrigger(run_date=horario_inicio),
            id=job_id,
            replace_existing=True
        )
        
        # Verifica se o job foi criado
        job_criado = scheduler.get_job(job_id)
        if job_criado:
            print(f"[SCHEDULER] ✅ Lembrete criado: {job_id}")
            print(f"[SCHEDULER] 📋 Próxima execução: {job_criado.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job_criado.next_run_time else 'N/A'}")
            print(f"[SCHEDULER] 🔄 Frequência: a cada {frequencia_horas}h")
        else:
            print(f"[SCHEDULER] ❌ ERRO: Job não foi criado corretamente!")
        
        return True
    
    except Exception as e:
        print(f"[ERRO AO CRIAR LEMBRETE] {e}")
        import traceback
        traceback.print_exc()
        return False

# === Criar lembrete diário (mantida para compatibilidade) ===
def criar_lembrete_diario_apscheduler(scheduler, telefone, nome_remedio, horario, chat_id_completo=None):
    """Função legada - cria lembrete diário (uma vez por dia)"""
    return criar_lembrete_apscheduler(scheduler, telefone, nome_remedio, horario, 24, chat_id_completo)


# === Função IA detectar intenção ===
def detectar_intencao_ia(mensagem_usuario):
    system_prompt = (
        "Sua única tarefa é classificar a intenção da mensagem do usuário."
        "Use “DÚVIDA CRIAR AGENDAMENTO” se o usuário estiver pedindo ajuda, explicação ou instruções sobre como criar um lembrete, sem necessariamente querer criar um naquele momento."
        "Use 'DÚVIDA REMOVER AGENDAMENTO' se for uma PERGUNTA sobre como remover."
        "Use 'DÚVIDA LISTAR AGENDAMENTOS' se for uma PERGUNTA sobre como ver todos os lembretes."
        "Use “CRIAR AGENDAMENTO” se o usuário estiver claramente pedindo para criar, registrar ou marcar um lembrete. Todos os dados necessários para criar o lembrete devem estar na mensagem (nome do remédio, intervalo entre dosagens e horário de início)."
        "Use 'REMOVER AGENDAMENTO' se o usuário quiser deletar um lembrete."
        "Use 'LISTAR AGENDAMENTOS' se o usuário quiser ver todos os remédios com lembretes."
        "Use 'MEDICAMENTO' se for uma mensagem sobre bulas, dosagem ou remédios."
        "Use 'GERAL' se for sobre qualquer outro assunto."
        "Use 'IMAGEM' se o usuário pedir uma imagem de remédio."
        "Responda APENAS com um JSON no formato: {\"intent\": \"[INTENÇÃO]\"}"
    )

    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensagem_usuario}
            ],
            max_tokens=5,
            temperature=0
        )
        resposta = response.choices[0].message.content.strip()

        if resposta not in [
            "DÚVIDA CRIAR AGENDAMENTO",
            "DÚVIDA REMOVER AGENDAMENTO",
            "DÚVIDA LISTAR AGENDAMENTOS",
            "CRIAR AGENDAMENTO",
            "REMOVER AGENDAMENTO",
            "LISTAR AGENDAMENTOS",
            "MEDICAMENTO",
            "GERAL",
            "IMAGEM"
        ]:
            resposta = "outra_intencao"

        print(f"[DEBUG CLASSIFICAÇÃO] Intenção detectada: {resposta}")
        return resposta

    except Exception as e:
        print(f"[ERRO IA] {e}")
        return "outra_intencao"


# === Verificar se remédio já está cadastrado ===
def remedio_ja_cadastrado(nome_remedio, telefone_usuario=None):
    """
    Verifica se um remédio já está cadastrado nos lembretes ativos.
    Se telefone_usuario for fornecido, verifica apenas para aquele telefone.
    Caso contrário, verifica globalmente.
    """
    global scheduler
    if scheduler is None:
        print("[DEBUG VERIFICAÇÃO] ⚠️ Scheduler não inicializado!")
        return False

    jobs = scheduler.get_jobs()
    if len(jobs) == 0:
        return False
    
    # Normaliza o nome para comparação
    nome_busca = nome_remedio.lower().replace(' ', '_').strip()
    nome_busca_sem_underscore = nome_remedio.lower().replace(' ', '').strip()
    nome_busca_original = nome_remedio.lower().strip()
    
    for job in jobs:
        try:
            job_id = job.id
            job_id_lower = job_id.lower()
            
            # Se telefone foi fornecido, verifica se o job pertence a esse telefone
            if telefone_usuario:
                telefone_limpo = telefone_usuario.replace('@c.us', '').replace('@lid', '').replace('-', '')
                if telefone_limpo not in job_id_lower:
                    continue
            
            # Verifica se o nome do remédio está no job_id
            # Tenta diferentes variações do nome
            if (nome_busca in job_id_lower or 
                nome_busca_sem_underscore in job_id_lower.replace('_', '') or
                nome_busca_original in job_id_lower):
                
                # Extrai o nome do remédio do job_id para confirmar
                partes = job_id.split('-')
                for i, parte in enumerate(partes):
                    if i == 0:  # Ignora telefone
                        continue
                    # Se a parte contém letras e não é só números, provavelmente é o nome
                    if any(c.isalpha() for c in parte) and not parte.replace('_', '').isdigit():
                        nome_job = parte.replace('_', ' ').strip().lower()
                        # Compara de forma flexível (ignora diferenças de capitalização e espaços)
                        if (nome_job == nome_busca_original or 
                            nome_job.replace(' ', '') == nome_busca_sem_underscore or
                            nome_busca_original in nome_job or
                            nome_job in nome_busca_original):
                            print(f"[DEBUG VERIFICAÇÃO] ✅ Remédio '{nome_remedio}' já está cadastrado (job: {job_id})")
                            return True
                        break
        except Exception as e:
            print(f"[ERRO VERIFICAÇÃO] Erro ao verificar job {job.id}: {e}")
            continue
    
    print(f"[DEBUG VERIFICAÇÃO] ❌ Remédio '{nome_remedio}' não está cadastrado")
    return False


# === Listar nomes dos remédios ===
def listar_nomes_remedios():
    """
    Retorna apenas os nomes dos remédios (mantida para compatibilidade).
    Use listar_remedios_com_horarios() para obter informações completas.
    """
    remedios_info = listar_remedios_com_horarios()
    return [remedio['nome'] for remedio in remedios_info]


def listar_remedios_com_horarios():
    """
    Lista remédios com seus próximos horários de lembrete.
    Retorna uma lista de dicionários com 'nome' e 'proximo_horario'.
    """
    global scheduler
    if scheduler is None:
        print("[DEBUG LISTAGEM] ⚠️ Scheduler não inicializado!")
        return []

    jobs = scheduler.get_jobs()
    print(f"[DEBUG LISTAGEM] 📋 Total de jobs no scheduler: {len(jobs)}")
    
    if len(jobs) == 0:
        print("[DEBUG LISTAGEM] ⚠️ Nenhum job encontrado no scheduler!")
        return []
    
    # Lista todos os job_ids para debug
    print(f"[DEBUG LISTAGEM] Job IDs encontrados: {[job.id for job in jobs]}")
    
    remedios_info = []
    nomes_vistos = set()  # Para evitar duplicatas
    
    for job in jobs:
        try:
            job_id = job.id
            print(f"[DEBUG LISTAGEM] Processando job: {job_id}")
            
            # Obtém o próximo horário de execução
            proximo_horario = None
            if job.next_run_time:
                proximo_horario = job.next_run_time.strftime("%H:%M")
            else:
                proximo_horario = "N/A"
            
            # O job_id tem formato: {telefone}-{nome_remedio}-{horario}-{frequencia}h
            # ou: {telefone}-{nome_remedio}-{horario} (formato antigo)
            # Pode ter múltiplos hífens no telefone, então precisamos ser mais cuidadosos
            
            # Tenta encontrar o nome do remédio (geralmente é a segunda parte após o telefone)
            partes = job_id.split('-')
            print(f"[DEBUG LISTAGEM] Partes do job_id: {partes}")
            
            if len(partes) >= 2:
                # O nome do remédio geralmente está na segunda posição
                # Mas pode variar se o telefone tiver hífens
                # Vamos tentar encontrar a parte que não parece ser número ou horário
                nome_candidato = None
                
                # Procura pela parte que contém letras (nome do remédio)
                for i, parte in enumerate(partes):
                    # Ignora a primeira parte (telefone) e partes que são claramente números/horários
                    if i == 0:
                        continue
                    # Se a parte contém letras e não é só números, provavelmente é o nome
                    if any(c.isalpha() for c in parte) and not parte.replace('_', '').isdigit():
                        nome_candidato = parte
                        break
                
                # Se não encontrou, usa a segunda parte como fallback
                if nome_candidato is None and len(partes) >= 2:
                    nome_candidato = partes[1]
                
                if nome_candidato:
                    nome = nome_candidato.replace('_', ' ').strip()
                    # Capitaliza cada palavra
                    nome_formatado = ' '.join([p.capitalize() for p in nome.split() if p])
                    
                    if nome_formatado:
                        # Evita duplicatas mas mantém a ordem
                        if nome_formatado.lower() not in nomes_vistos:
                            remedios_info.append({
                                'nome': nome_formatado,
                                'proximo_horario': proximo_horario
                            })
                            nomes_vistos.add(nome_formatado.lower())
                            print(f"[DEBUG LISTAGEM] ✅ Adicionado: {nome_formatado} - Próximo: {proximo_horario}")
                        else:
                            # Se já existe, atualiza o próximo horário se for mais próximo
                            for remedio in remedios_info:
                                if remedio['nome'].lower() == nome_formatado.lower():
                                    # Mantém o horário mais próximo (menor)
                                    if proximo_horario != "N/A" and (remedio['proximo_horario'] == "N/A" or proximo_horario < remedio['proximo_horario']):
                                        remedio['proximo_horario'] = proximo_horario
                                    print(f"[DEBUG LISTAGEM] ⏭️ Duplicata atualizada: {nome_formatado} - Próximo: {proximo_horario}")
                                    break
                    else:
                        print(f"[DEBUG LISTAGEM] ⚠️ Nome vazio após processamento")
                else:
                    print(f"[DEBUG LISTAGEM] ⚠️ Não foi possível identificar o nome do remédio")
            else:
                print(f"[DEBUG LISTAGEM] ⚠️ Job ID com formato inválido: {job_id}")
        except Exception as e:
            print(f"[ERRO LISTAGEM] Erro ao processar job {job.id}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"[DEBUG LISTAGEM] ✅ Resultado final: {len(remedios_info)} remédios únicos encontrados")
    return remedios_info


# === Detectar nomes de remédios (IA) ===
def detectar_nomes_remedios_ia(mensagem_usuario):
    system_prompt = (
        "Sua tarefa é identificar todos os nomes de medicamentos que o usuário quer remover."
        "Responda apenas com uma lista de nomes em minúsculas, separados por vírgula."
        "Exemplo: 'dipirona, paracetamol'"
    )

    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensagem_usuario}
            ],
            max_tokens=50,
            temperature=0
        )
        nomes = response.choices[0].message.content.strip().lower()
        nomes_lista = [n.strip() for n in nomes.split(',') if n.strip()]
        return nomes_lista
    except Exception as e:
        print(f"[ERRO IA NOMES] {e}")
        return []


# === Remoção de lembretes ===
def remover_por_nome(nome_remedio, telefone_usuario=None):
    """
    Remove lembretes por nome do remédio.
    Se telefone_usuario for fornecido, remove apenas os lembretes daquele usuário.
    """
    global scheduler
    if scheduler is None:
        return False, "Scheduler não inicializado."

    jobs = scheduler.get_jobs()
    # O job_id pode ter formato: "{telefone}-{nome_remedio}-{horario}-{frequencia}h" (novo)
    # ou: "{telefone}-{nome_remedio}-{horario}" (antigo)
    # Normaliza o nome para busca: remove espaços, converte para minúsculas
    nome_busca = nome_remedio.lower().replace(' ', '_').strip()
    nome_busca_sem_underscore = nome_remedio.lower().replace(' ', '').strip()
    
    # Normaliza telefone para busca
    telefone_busca = None
    if telefone_usuario:
        telefone_busca = telefone_usuario.replace('@c.us', '').replace('@lid', '').replace('-', '')
    
    encontrados = []
    for job in jobs:
        job_id_lower = job.id.lower()
        
        # Se telefone foi fornecido, verifica se o job pertence a esse telefone
        if telefone_busca:
            if telefone_busca not in job_id_lower:
                continue  # Pula jobs de outros usuários
        
        # Busca flexível: verifica se o nome do remédio está no job_id
        # Tenta com underscore e sem underscore
        if (nome_busca in job_id_lower or 
            nome_busca_sem_underscore in job_id_lower.replace('_', '') or
            nome_remedio.lower() in job_id_lower):
            encontrados.append(job)

    if not encontrados:
        # Lista todos os jobs para debug
        print(f"[DEBUG REMOÇÃO] Jobs disponíveis: {[job.id for job in jobs]}")
        print(f"[DEBUG REMOÇÃO] Buscando por: '{nome_busca}' ou '{nome_remedio.lower()}'")
        if telefone_usuario:
            print(f"[DEBUG REMOÇÃO] Filtrando por telefone: {telefone_busca}")
        return False, f"Nenhum agendamento encontrado para '{nome_remedio}'."

    removidos = []
    for job in encontrados:
        try:
            scheduler.remove_job(job.id)
            removidos.append(job.id)
            print(f"[SCHEDULER] ✅ Job removido: {job.id}")
        except Exception as e:
            print(f"[ERRO AO REMOVER] Falha ao remover job {job.id}: {e}")

    if removidos:
        return True, f"✅ Agendamento(s) de '{nome_remedio}' removido(s) com sucesso!"
    else:
        return False, f"❌ Não foi possível remover o agendamento de '{nome_remedio}'."


def remover_varios_remedios(nomes_remedios):
    resultados = []
    for nome in nomes_remedios:
        ok, msg = remover_por_nome(nome)
        resultados.append(msg)
    return "\n".join(resultados)


# === Gerenciador geral com IA ===
def gerenciar_agendamentos_ia(mensagem_usuario):
    intencao = detectar_intencao_ia(mensagem_usuario)

    if intencao == "REMOVER AGENDAMENTO":
        nomes_remedios = detectar_nomes_remedios_ia(mensagem_usuario)
        if not nomes_remedios:
            return "⚠️ Não consegui identificar os remédios. Pode tentar de novo?"
        return remover_varios_remedios(nomes_remedios)

    elif intencao == "LISTAR AGENDAMENTOS":
        nomes = listar_nomes_remedios()
        if not nomes:
            return "📭 Nenhum lembrete ativo."
        resposta = "📋 *Remédios com lembretes ativos:*\n\n"
        for i, nome in enumerate(nomes, start=1):
            resposta += f"{i}. {nome}\n"
        return resposta


def detectar_resposta_confirmacao(mensagem_usuario):
    """
    Detecta se a mensagem do usuário é uma resposta de confirmação (sim/não).
    Retorna 'sim', 'nao' ou None.
    """
    mensagem_lower = mensagem_usuario.lower().strip()
    
    # Palavras que indicam "sim"
    palavras_sim = ['sim', 's', 'yes', 'y', 'ok', 'okay', 'confirmo', 'confirmar', 
                    'quero', 'pode', 'pode sim', 'claro', 'tudo bem', 'beleza', 
                    'sobrepor', 'substituir', 'trocar']
    
    # Palavras que indicam "não"
    palavras_nao = ['não', 'nao', 'n', 'no', 'não quero', 'nao quero', 'não quero sobrepor',
                    'nao quero sobrepor', 'cancelar', 'cancel', 'manter', 'deixar como está']
    
    # Verifica se contém palavras de confirmação positiva
    for palavra in palavras_sim:
        if palavra in mensagem_lower:
            return 'sim'
    
    # Verifica se contém palavras de confirmação negativa
    for palavra in palavras_nao:
        if palavra in mensagem_lower:
            return 'nao'
    
    return None


def processar_agendamento_ia(mensagem_usuario, telefone_usuario, chat_id_completo=None):
    """
    Interpreta a mensagem do usuário com IA e cria o lembrete no horário certo.
    Exemplo: 'Quero tomar Dipirona a cada 6 horas começando às 10:30'
    """
    global confirmacoes_pendentes
    
    # Verifica se há uma confirmação pendente para este usuário
    if telefone_usuario in confirmacoes_pendentes:
        resposta = detectar_resposta_confirmacao(mensagem_usuario)
        
        if resposta == 'sim':
            # Usuário confirmou sobreposição
            dados_pendentes = confirmacoes_pendentes[telefone_usuario]
            nome_remedio = dados_pendentes['nome_remedio']
            horario = dados_pendentes['horario']
            frequencia_horas = dados_pendentes['frequencia_horas']
            chat_id = dados_pendentes.get('chat_id_completo')
            
            # Remove o remédio antigo (apenas do usuário atual)
            remover_por_nome(nome_remedio, telefone_usuario)
            
            # Remove a confirmação pendente
            del confirmacoes_pendentes[telefone_usuario]
            
            # Cria o novo lembrete
            ok = criar_lembrete_apscheduler(inicializar_scheduler(), telefone_usuario, nome_remedio, horario, frequencia_horas, chat_id)
            if ok:
                if frequencia_horas == 24:
                    return f"✅ Lembrete para *{nome_remedio}* sobreposto com sucesso às {horario} (diário)!"
                else:
                    return f"✅ Lembrete para *{nome_remedio}* sobreposto com sucesso às {horario} (a cada {frequencia_horas} horas)!"
            else:
                return "❌ Não consegui sobrepor o lembrete. Tente novamente."
        
        elif resposta == 'nao':
            # Usuário não quer sobrepor
            dados_pendentes = confirmacoes_pendentes[telefone_usuario]
            nome_remedio = dados_pendentes['nome_remedio']
            
            # Remove a confirmação pendente
            del confirmacoes_pendentes[telefone_usuario]
            
            return f"✅ Entendido! O lembrete de *{nome_remedio}* permanece como está."
        
        else:
            # Resposta não reconhecida, mantém a confirmação pendente e pede resposta clara
            return "⚠️ Não entendi sua resposta. Por favor, responda *sim* para sobrepor ou *não* para manter o lembrete atual."
    
    # Processamento normal de criação de agendamento
    system_prompt = (
        "Extraia da mensagem o nome do remédio, o horário inicial e a frequência (intervalo entre doses).\n"
        "Responda em JSON no formato: {\"nome_remedio\": \"dipirona\", \"horario\": \"10:30\", \"frequencia_horas\": 6}\n"
        "A frequência_horas deve ser o número de horas entre cada dose (ex: 'a cada 2 horas' = 2, 'de 4 em 4 horas' = 4).\n"
        "Se não encontrar horário, coloque null. Se não encontrar frequência, use 24 (uma vez por dia)."
    )

    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensagem_usuario}
            ],
            temperature=0,
            max_tokens=100
        )

        dados = json.loads(response.choices[0].message.content.strip())
        nome_remedio = dados.get("nome_remedio")
        horario = dados.get("horario")
        frequencia_horas = dados.get("frequencia_horas", 24)  # Default: uma vez por dia

        if not nome_remedio or not horario:
            return "⚠️ Não consegui identificar o remédio ou o horário. Pode tentar de novo?"

        # Valida frequência
        try:
            frequencia_horas = int(frequencia_horas)
            if frequencia_horas < 1:
                frequencia_horas = 24
        except (ValueError, TypeError):
            frequencia_horas = 24

        # Verifica se o remédio já está cadastrado
        if remedio_ja_cadastrado(nome_remedio, telefone_usuario):
            # Armazena os dados do agendamento pendente
            confirmacoes_pendentes[telefone_usuario] = {
                'nome_remedio': nome_remedio,
                'horario': horario,
                'frequencia_horas': frequencia_horas,
                'chat_id_completo': chat_id_completo
            }
            return (
                f"⚠️ O remédio *{nome_remedio}* já está cadastrado.\n\n"
                f"Você quer sobrepor com o novo lembrete (às {horario}, a cada {frequencia_horas}h)?\n\n"
                f"Responda *sim* para sobrepor ou *não* para manter o lembrete atual."
            )

        ok = criar_lembrete_apscheduler(inicializar_scheduler(), telefone_usuario, nome_remedio, horario, frequencia_horas, chat_id_completo)
        if ok:
            if frequencia_horas == 24:
                return f"✅ Lembrete para *{nome_remedio}* criado com sucesso às {horario} (diário)!"
            else:
                return f"✅ Lembrete para *{nome_remedio}* criado com sucesso às {horario} (a cada {frequencia_horas} horas)!"
        else:
            return "❌ Não consegui criar o lembrete. Tente novamente."

    except Exception as e:
        print(f"[ERRO AGENDAMENTO IA] {e}")
        import traceback
        traceback.print_exc()
        return "⚠️ Ocorreu um erro ao tentar criar o lembrete."