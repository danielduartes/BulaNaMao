import os
import chromadb
from langchain_community.embeddings import OpenAIEmbeddings
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Nome da pasta em que o banco está
db_path = os.path.join(os.getcwd(), "vector_db")

# Nome da coleção no arquivo "extracting_pdf.py"
collection_name = "bula_farmaceutica"

# Pegando os embeddings na API da OpenAI
print("Carregando o modelo de embeddings (necessário para vetorizar a pergunta)...")
embeddings = OpenAIEmbeddings(
    api_key=os.getenv("OPENAI_API_KEY"), # key da API da OpenAI
    model="text-embedding-3-small" # tamanho dos chunks
)

# Conexão com o banco de dados usando path dele
client = chromadb.PersistentClient(path=db_path) 

# Pegando a coleção do banco
collection = client.get_collection(
    name=collection_name # nome da coleção
)

# Mensagem de caso a conexão com o banco seja bem-sucedida
print(f"Conexão bem-sucedida. Coleção '{collection_name}' carregada.")

# Função da busca da informação no banco 
def buscar_informacao(pergunta: str, num_resultados: int=3):
    """Realiza a busca no banco de dados vetorial."""

    query_vector = embeddings.embed_query(pergunta) # transforma a pergunta em um vetor numérico, o vetor representa o significado semântico da pergunta

    results = collection.query( # método de consulta no banco 
        query_embeddings=[query_vector], # o ChromaDB compara o vetor com todos os vetores armazenados na coleção
        n_results=num_resultados, # retorna o número especificado de vetores que são os mais próximos do "query_vector"
        include=["documents", "distances"] # define quais informações são incluídas nos resultados (results)
    )

    print("\n--- Resultado da Busca ---")
    print(f"Pergunta: {pergunta}") # mensagem da pergunta digitada

    resposta_final = ""

    if not (results and results['documents'] and results['documents'][0]):
        return "Desculpe, não encontrei nenhuma informação relevante sobre isso nas bulas cadastradas"

    contextos = []

    # Distâncias: medida numérica que quantifica a similaridade semântica entre o "query_vector" e o vetor de cada documento armazenado na coleção
    for i, (document, distance) in enumerate(zip(results['documents'][0], results['distances'][0])): # unindo os textos dos documentos com as suas distâncias  
        print(f"\n Resultado {i+1} (Distância: {distance:.4f})") # i=número de resultados 
        print(document) 
        contextos.append(document)
    
    if not contextos:
        return "Desculpe, a busca encontrou alguns trechos, mas eles não são relevantes"

    contexto_completo = "\n---\n".join(contextos)

    try:
        prompt_sistema = (
            "Você é o RemedIAr, um assistente de saúde prestativo. Sua tarefa é responder "
            "perguntas relacionadas à informações de remédios e agendar lembretes de quando o usuário deve tomar determinad remédio. "
            "Resuma a informação de forma clara e objetiva. Se o contexto não tiver a "
            "resposta, diga de forma educada que a informação não foi encontrada na bula."
            "Quando a mensagem não for relacionada à remédios, mas sim sobre o agendamento deles converse normalmente com o usuário."
        )

        prompt_usuario = (
            f"PERGUNTA: {pergunta}\n\n"
            f"CONTEXTO DA BULA: \n{contexto_completo}"
        )

        response = client_openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario}
            ],
            temperature=0.1
        )

        resposta_final = response.choices[0].message.content
        return resposta_final

    except Exception as e:
        print(f"Erro ao chamar a API da OpenAI para resumir: {e}")
        return "Desculpe, a IA falhou ao tentar resumir a informação da bula. Tente novamente."