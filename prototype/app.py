
import streamlit as st
import boto3
import tarfile
import json
import os

# AWS config
bucket = "faa-compass-poc-data"
textract_prefix = "processed-data/"
comprehend_key = "analytics-ready/655276615738-KP-52159175b457267b6763c4441f279dac/output/output.tar.gz"
region = "us-east-1"

s3 = boto3.client("s3", region_name=region)
bedrock = boto3.client("bedrock-runtime", region_name=region)

# Load Comprehend output
@st.cache_resource
def load_comprehend():
    tar_path = "output.tar.gz"
    extract_path = "comprehend_output"
    os.makedirs(extract_path, exist_ok=True)
    s3.download_file(bucket, comprehend_key, tar_path)

    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(extract_path)

    for root, _, files in os.walk(extract_path):
        for f in files:
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8") as file:
                return [json.loads(line) for line in file if line.strip()]
    return []

# Load Textract .txt files
@st.cache_resource
def load_textract():
    docs = {}
    response = s3.list_objects_v2(Bucket=bucket, Prefix=textract_prefix)
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".txt"):
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
            docs[key] = body
    return docs

# Prepare combined documents
@st.cache_resource
def prepare_documents():
    comprehend_data = load_comprehend()
    textract_data = load_textract()
    documents = []

    for idx, (filename, content) in enumerate(textract_data.items()):
        key_phrases = []
        if idx < len(comprehend_data):
            key_phrases = [kp["Text"] for kp in comprehend_data[idx].get("KeyPhrases", [])]

        key_text = ", ".join(key_phrases)
        combined = f"Filename: {filename}\n\nContent:\n{content}\n\nKey Phrases:\n{key_text}"
        documents.append({"filename": filename, "content": combined})
    return documents

# Split long content into chunks (max ~12,000 characters)
def chunk_text(text, max_len=12000):
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]

# Claude call per chunk
def ask_claude_chunk(chunk, question):
    prompt = f"""
You are a helpful assistant.

Below is a chunk of a technical document. Based only on this chunk, answer the question provided.

 If the answer cannot be determined from this chunk, respond exactly with:
Not found in this chunk.

Do not repeat the chunk or say anything else.

---

[Document Chunk]
{chunk}

---

Question: {question}
Answer:
"""

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500
        }),
        contentType="application/json",
        accept="application/json"
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()

def summarize_relevant_answers(answers, question):
    summary_prompt = f"""
You are an assistant summarizing extracted answers to a user question.

Below are multiple answers, each from a different chunk of source documents. Your task is to create a concise, clear summary that answers the user's question.

User Question:
{question}

Extracted Answers:
{chr(10).join(answers)}

Summary:
"""

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "messages": [{"role": "user", "content": summary_prompt}],
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 700
        }),
        contentType="application/json",
        accept="application/json"
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()

# Streamlit UI
st.set_page_config(page_title="FAA Chunked Chatbot", layout="wide")
st.title("🧠 FAA Smart Chatbot ")
st.markdown("Ask questions and get smart.")

query = st.text_input("💬 Ask your question:")

if query:
    docs = prepare_documents()
    relevant_answers = []

    with st.spinner("Processing chunks with Claude..."):
        for doc in docs:
            chunks = chunk_text(doc["content"])
            for i, chunk in enumerate(chunks):
                answer = ask_claude_chunk(chunk, query)
                if answer.lower().strip() != "not found in this chunk":
                    relevant_answers.append(answer)

    if relevant_answers:
        st.subheader(" Final Answer:")
        summary = summarize_relevant_answers(relevant_answers, query)
        st.success(summary)

        with st.expander("📄 View all individual relevant chunk responses"):
            for i, a in enumerate(relevant_answers, 1):
                st.markdown(f"**Chunk {i}:** {a}")
    else:
        st.warning("No relevant answers found in the document chunks.")
