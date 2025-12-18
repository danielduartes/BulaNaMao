import base64
from openai import OpenAI

# Traduz a imagem para Base64.
def codificar_imagem_para_base64(caminho_imagem):
    """
    Codifica uma imagem local para uma string Base64.
    """
    try:
        with open(caminho_imagem, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        print(f"Erro: Arquivo não encontrado no caminho: {caminho_imagem}")
        raise
    except Exception as e:
        print(f"Erro ao codificar a imagem: {e}")
        raise

# Função para extrair as informações da imagem usando GPT-4o
def extrair_informacao_imagem(base64_imagem: str, mime_type: str, api_key: str) -> str:
    """
    Envia uma imagem Base64 para a API GPT-4o para extrair informações.
    """
    
    # 1. Configurando o URL da imagem para o modelo
    image_url_data = f"data:{mime_type};base64,{base64_imagem}"
    
    # 2. Definindo a instrução principal (prompt de tarefa)
    instrucao_tarefa = (
        "Analise a imagem de uma embalagem de medicamento. "
        "Primeiro, extraia APENAS o nome do medicamento. \n\n"
        "Em seguida, com base no nome extraído, forneça um resumo de bula, incluindo: \n"
        "1. Dosagem \n"
        "2. Função do remédio \n"
        "3. Possíveis efeitos colaterais \n"
        "4. Interações medicamentosas \n"
        "5. Forma de uso \n"
        "6. Contraindicações \n"
        "7. Forma de armazenamento \n\n"
        "Mantenha as informações separadas por quebras de linha. Responda apenas em português."
    )
    
    # 3. Simplificando a instrução de sistema (persona)
    instrucao_persona = "Você é um assistente especialista em bulas e embalagens de medicamentos. Seja conciso e informativo."

    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[ 
                {"role": "system", "content": instrucao_persona}, 
                {"role": "user", "content": [ 
                    {"type": "text", "text": instrucao_tarefa},
                    {"type": "image_url", "image_url": {
                        "url": image_url_data 
                    }}
                ]}
            ],
            max_tokens=2000
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"Erro ao processar a imagem pela API da OpenAI: {e}")
        return "Desculpe, não consegui processar a imagem com a API Vision. Tente novamente."
